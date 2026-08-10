#!/usr/bin/env python3
"""Exercise the kmsg watcher with a real ext4 remount-on-error event.

Run without arguments for initialize -> trigger -> verify. Resources and the
config override are intentionally retained until --restore is requested.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time

try:
    import yaml
except ImportError:
    sys.exit(
        "ERROR: PyYAML not found. Run inside healthagent's virtualenv, e.g.\n"
        "  sudo /opt/healthagent/.venv/bin/python "
        "integration/test_kmsg_ext4_remount.py"
    )


WORK_DIR = Path("/var/tmp/healthagent-kmsg-ext4-remount")
STATE_PATH = WORK_DIR / "state.json"
IMAGE_PATH = WORK_DIR / "filesystem.img"
MOUNT_PATH = WORK_DIR / "mnt"
CONFIG_PATH = Path("/etc/healthagent/config.yaml")
SOCKET_PATH = "/opt/healthagent/run/health.sock"
DM_NAME = "healthagent-kmsg-ext4"
RULE_NAME = "ext4_remount_ro"
RULE = {
    "pattern": r"EXT4-fs \([^)]*\): Remounting filesystem read-only",
    "eval": "ge",
    "error": 1,
    "category": "Storage",
    "msg": "ext4 filesystem remounted read-only due to error",
}
REMOUNT_RE = re.compile(RULE["pattern"])
VERIFY_TIMEOUT = 20


def run(command: list[str], *, check: bool = True, timeout: int | None = None):
    print(f"\n$ {shlex.join(command)}", flush=True)
    try:
        result = subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        if error.stdout:
            print(f"stdout:\n{error.stdout.rstrip()}")
        if error.stderr:
            print(f"stderr:\n{error.stderr.rstrip()}")
        print(f"[timed out after {timeout} seconds]", flush=True)
        raise
    except subprocess.CalledProcessError as error:
        if error.stdout:
            print(f"stdout:\n{error.stdout.rstrip()}")
        if error.stderr:
            print(f"stderr:\n{error.stderr.rstrip()}")
        print(f"[exit code: {error.returncode}]", flush=True)
        raise

    if result.stdout:
        print(f"stdout:\n{result.stdout.rstrip()}")
    if result.stderr:
        print(f"stderr:\n{result.stderr.rstrip()}")
    print(f"[exit code: {result.returncode}]", flush=True)
    return result


def require_root():
    if os.geteuid() != 0:
        sys.exit("ERROR: run this integration test as root.")


def require_commands():
    required = (
        "blockdev",
        "dmsetup",
        "findmnt",
        "journalctl",
        "losetup",
        "mkfs.ext4",
        "mount",
        "sync",
        "systemctl",
        "truncate",
        "umount",
    )
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        sys.exit(f"ERROR: required commands not found: {', '.join(missing)}")


def save_state(state: dict):
    WORK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = STATE_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(state, indent=2) + "\n")
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(STATE_PATH)


def load_state() -> dict:
    if not STATE_PATH.exists():
        sys.exit(f"ERROR: no initialized test found at {STATE_PATH}; run --initialize first.")
    return json.loads(STATE_PATH.read_text())


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    config = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    if not isinstance(config, dict):
        sys.exit(f"ERROR: {CONFIG_PATH} is not a YAML mapping.")
    return config


def write_config(config: dict):
    CONFIG_PATH.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.safe_dump(config, default_flow_style=False, sort_keys=False)
    )


def install_rule(state: dict):
    config = load_config()
    patterns = config.setdefault("kmsg", {}).setdefault("patterns", {})
    state["config_existed"] = CONFIG_PATH.exists()
    state["rule_existed"] = RULE_NAME in patterns
    state["previous_rule"] = patterns.get(RULE_NAME)
    save_state(state)
    patterns[RULE_NAME] = RULE
    write_config(config)


def restore_rule(state: dict):
    config = load_config()
    kmsg = config.get("kmsg", {})
    patterns = kmsg.get("patterns", {})
    if state.get("rule_existed"):
        patterns[RULE_NAME] = state.get("previous_rule")
    else:
        patterns.pop(RULE_NAME, None)

    if not patterns:
        kmsg.pop("patterns", None)
    if not kmsg:
        config.pop("kmsg", None)

    if not state.get("config_existed") and not config:
        CONFIG_PATH.unlink(missing_ok=True)
    else:
        write_config(config)


def restart_healthagent():
    run(["systemctl", "restart", "healthagent"])
    deadline = time.monotonic() + VERIFY_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1)
                client.connect(SOCKET_PATH)
            return
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            time.sleep(0.5)
    raise RuntimeError("healthagent did not become available after restart")


def journal_cursor() -> str | None:
    result = run(
        ["journalctl", "-k", "-n", "0", "--show-cursor", "--no-pager"],
        check=False,
    )
    match = re.search(r"^-- cursor: (.+)$", result.stdout, re.MULTILINE)
    return match.group(1) if match else None


def journal_lines(state: dict) -> str:
    command = ["journalctl", "-k", "--no-pager", "-o", "short-iso"]
    cursor = state.get("journal_cursor")
    if cursor:
        command.append(f"--after-cursor={cursor}")
    else:
        command.extend(["--since", f"@{state['journal_epoch']}"])
    result = run(command, check=False)
    return result.stdout


def get_health_status() -> dict:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(SOCKET_PATH)
            client.sendall(json.dumps({"command": "status"}).encode())
            client.shutdown(socket.SHUT_WR)
            response = b""
            while chunk := client.recv(4096):
                response += chunk
        return json.loads(response.decode())
    except (OSError, json.JSONDecodeError):
        return {}


def mount_is_read_only(state: dict) -> bool:
    result = run(
        ["findmnt", "--noheadings", "--output", "OPTIONS", "--target", state["mount_path"]],
        check=False,
    )
    options = result.stdout.strip().split(",")
    return result.returncode == 0 and "ro" in options


def initialize():
    if STATE_PATH.exists():
        sys.exit(f"ERROR: a test is already initialized; run {sys.argv[0]} --restore first.")

    WORK_DIR.mkdir(mode=0o700, parents=True)
    MOUNT_PATH.mkdir(mode=0o700)
    state = {
        "image_path": str(IMAGE_PATH),
        "mount_path": str(MOUNT_PATH),
        "dm_name": DM_NAME,
        "mapping_mode": "none",
    }
    save_state(state)

    install_rule(state)
    restart_healthagent()

    run(["truncate", "--size", "128M", str(IMAGE_PATH)])
    loop_device = run(["losetup", "--find", "--show", str(IMAGE_PATH)]).stdout.strip()
    state["loop_device"] = loop_device
    save_state(state)

    run(["mkfs.ext4", "-F", "-q", loop_device])
    sectors = int(run(["blockdev", "--getsz", loop_device]).stdout.strip())
    state["sectors"] = sectors
    run(
        [
            "dmsetup",
            "create",
            DM_NAME,
            "--table",
            f"0 {sectors} linear {loop_device} 0",
        ]
    )
    state["mapping_mode"] = "linear"
    save_state(state)

    run(
        [
            "mount",
            "-t",
            "ext4",
            "-o",
            "errors=remount-ro,commit=1",
            f"/dev/mapper/{DM_NAME}",
            str(MOUNT_PATH),
        ]
    )
    trigger_path = MOUNT_PATH / "trigger"
    with trigger_path.open("wb") as trigger_file:
        trigger_file.write(b"0" * 1024 * 1024)
        trigger_file.flush()
        os.fsync(trigger_file.fileno())
    os.sync()

    state["journal_cursor"] = journal_cursor()
    state["journal_epoch"] = int(time.time())
    save_state(state)
    print("Initialized ext4 kmsg test.")
    print(f"  loop device: {loop_device}")
    print(f"  mapped device: /dev/mapper/{DM_NAME}")
    print(f"  mount point: {MOUNT_PATH}")
    print(f"  config rule: {RULE_NAME}")
    print(f"Next: sudo {sys.argv[0]} --trigger")


def switch_mapping(state: dict, target: str):
    run(["dmsetup", "suspend", state["dm_name"]])
    try:
        if target == "error":
            table = f"0 {state['sectors']} error"
        else:
            table = (
                f"0 {state['sectors']} linear "
                f"{state['loop_device']} 0"
            )
        run(["dmsetup", "load", state["dm_name"], "--table", table])
    finally:
        run(["dmsetup", "resume", state["dm_name"]], check=False)
    state["mapping_mode"] = target
    save_state(state)


def trigger():
    state = load_state()
    if state.get("mapping_mode") == "error":
        print("Error mapping is already active; verifying the existing failure.")
        return verify()

    switch_mapping(state, "error")
    trigger_path = Path(state["mount_path"]) / "trigger"
    try:
        with trigger_path.open("ab", buffering=0) as trigger_file:
            trigger_file.write(b"failure\n")
            os.fsync(trigger_file.fileno())
    except OSError as error:
        print(f"Expected writeback error: {error}")

    try:
        run(["sync", "-f", state["mount_path"]], check=False, timeout=10)
    except subprocess.TimeoutExpired:
        print("Forced sync timed out after the device began returning I/O errors.")
    return verify()


def verify() -> bool:
    state = load_state()
    deadline = time.monotonic() + VERIFY_TIMEOUT
    logs = ""
    report = {}
    while time.monotonic() < deadline:
        logs = journal_lines(state)
        report = (
            get_health_status()
            .get("kmsg", {})
            .get("KernelLogCheck", {})
        )
        kernel_line_found = REMOUNT_RE.search(logs) is not None
        read_only = mount_is_read_only(state)
        rule_error = report.get(RULE_NAME, {}).get("status") == "Error"
        critical_error = report.get("KERNEL_CRITICAL", {}).get("status") == "Error"
        if kernel_line_found and read_only and rule_error and critical_error:
            break
        time.sleep(0.5)

    checks = {
        "ext4 remount message found in kernel journal": REMOUNT_RE.search(logs) is not None,
        "test filesystem is mounted read-only": mount_is_read_only(state),
        f"healthagent {RULE_NAME} status is Error": (
            report.get(RULE_NAME, {}).get("status") == "Error"
        ),
        "healthagent KERNEL_CRITICAL status is Error": (
            report.get("KERNEL_CRITICAL", {}).get("status") == "Error"
        ),
    }
    print("\n=== Verification ===")
    for description, passed in checks.items():
        print(f"[{'PASS' if passed else 'FAIL'}] {description}")

    matching_lines = [line for line in logs.splitlines() if REMOUNT_RE.search(line)]
    if matching_lines:
        print("\nKernel remount message:")
        for line in matching_lines:
            print(f"  {line}")
    else:
        print("\nRecent kernel messages:")
        print(logs.rstrip() or "  (none available)")

    passed = all(checks.values())
    print(f"\n{'EXT4 KMSG TEST PASSED' if passed else 'EXT4 KMSG TEST FAILED'}")
    print(f"Restore with: sudo {sys.argv[0]} --restore")
    return passed


def restore():
    state = load_state()
    errors = []

    if state.get("mapping_mode") == "error":
        try:
            switch_mapping(state, "linear")
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(f"restore linear mapping: {error}")

    mount_path = state.get("mount_path")
    if mount_path:
        mounted = run(["findmnt", "--mountpoint", mount_path], check=False).returncode == 0
        if mounted:
            result = run(["umount", mount_path], check=False)
            if result.returncode != 0:
                errors.append(f"unmount {mount_path}: {result.stderr.strip()}")

    if run(["dmsetup", "info", state["dm_name"]], check=False).returncode == 0:
        result = run(["dmsetup", "remove", state["dm_name"]], check=False)
        if result.returncode != 0:
            errors.append(f"remove device-mapper target: {result.stderr.strip()}")

    loop_device = state.get("loop_device")
    if loop_device and run(["losetup", loop_device], check=False).returncode == 0:
        result = run(["losetup", "--detach", loop_device], check=False)
        if result.returncode != 0:
            errors.append(f"detach {loop_device}: {result.stderr.strip()}")

    try:
        restore_rule(state)
        restart_healthagent()
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        errors.append(f"restore config/restart healthagent: {error}")

    if errors:
        print("Restoration was incomplete:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(f"State retained at {STATE_PATH} for another --restore attempt.", file=sys.stderr)
        return False

    shutil.rmtree(WORK_DIR)
    print("Restored the config and removed the test filesystem, mapping, and loop device.")
    print(
        "NOTE: the real KERN_CRIT message remains in the kernel ring buffer. "
        "Healthagent can replay it for up to one hour after the event."
    )
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Trigger and verify a real ext4 remount-on-error kmsg event."
    )
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument("--initialize", action="store_true", help="install the rule and create the test filesystem")
    phase.add_argument("--trigger", action="store_true", help="force I/O failure and verify the resulting alert")
    phase.add_argument("--verify", action="store_true", help="verify an already-triggered failure")
    phase.add_argument("--restore", action="store_true", help="restore config and remove all test resources")
    args = parser.parse_args()

    require_root()
    require_commands()

    if args.initialize:
        initialize()
        return
    if args.trigger:
        sys.exit(0 if trigger() else 1)
    if args.verify:
        sys.exit(0 if verify() else 1)
    if args.restore:
        sys.exit(0 if restore() else 1)

    initialize()
    passed = trigger()
    print("\nResources intentionally left in place for inspection.")
    print(f"Restore with: sudo {sys.argv[0]} --restore")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()