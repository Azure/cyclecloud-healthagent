#!/usr/bin/env python3
"""
Integration test for the kmsg module (reserved severe levels + regex patterns).

Writes a self-test regex rule into /etc/healthagent/config.yaml, then injects
messages into /dev/kmsg and verifies the KernelLogCheck report. This exercises,
end-to-end:

  * config loading of a user-defined kmsg.patterns rule,
  * the three reserved severe-level buckets (KERNEL_EMERGENCY / KERNEL_ALERT /
    KERNEL_CRITICAL) from kernel levels 0 / 1 / 2, and
  * a configured regex pattern that matches several different message types.

Run inside healthagent's virtualenv (so PyYAML is available) and as root
(needs /dev/kmsg writes and /etc/healthagent/config.yaml edits).

IMPORTANT — KMSG_TEST_MODE
--------------------------
The reserved-level buckets (KERNEL_EMERGENCY/ALERT/CRITICAL) only act on
kernel-facility messages. Lines written to /dev/kmsg from userspace carry the
LOG_USER facility, so healthagent ignores them for level detection *unless* the
daemon is started with KMSG_TEST_MODE=true. Set it via a systemd drop-in and
restart before injecting:

    sudo systemctl edit healthagent      # add under [Service]:
        Environment="KMSG_TEST_MODE=True"
    sudo systemctl restart healthagent

(The configured regex pattern matches regardless of facility, so the pattern
checks work with or without KMSG_TEST_MODE — it is only required to exercise the
reserved levels via userspace injection.)

Usage
-----
# Full run (initialize config -> restart healthagent -> inject -> verify):
    sudo .../python integration/test_kmsg_inject.py

# Step-by-step manual control (matches: edit config, YOU restart, then inject):
    sudo .../python integration/test_kmsg_inject.py --initialize
    sudo systemctl restart healthagent
    sudo .../python integration/test_kmsg_inject.py --inject
    sudo .../python integration/test_kmsg_inject.py --verify
    sudo .../python integration/test_kmsg_inject.py --teardown
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time

try:
    import yaml
except ImportError:
    sys.exit(
        "ERROR: PyYAML not found. Run this script inside healthagent's "
        "virtualenv (which ships PyYAML), e.g.\n"
        "  sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_inject.py"
    )

# ── Constants ───────────────────────────────────────────────────────────────

HEALTHAGENT_DIR = os.getenv("HEALTHAGENT_DIR", "/opt/healthagent")
SOCKET_PATH = f"{HEALTHAGENT_DIR}/run/health.sock"
CONFIG_PATH = "/etc/healthagent/config.yaml"
BACKUP_PATH = f"{CONFIG_PATH}.selftest.bak"
NOFILE_MARKER = f"{CONFIG_PATH}.selftest.nofile"

REPORT_NAME = "KernelLogCheck"
RULE_NAME = "healthagent_selftest"

# Regex intentionally matches several distinct message types
# (5 subsystems x 3 fault words) so one rule covers many lines.
RULE = {
    "pattern": r"healthagent-selftest: (disk|nvme|pcie|memory|thermal) (error|timeout|fault) [0-9]+",
    "eval": "ge",
    "warning": 3,
    "error": 10,
    "category": "SelfTest",
    "msg": "healthagent kmsg self-test pattern matched",
}

SUBSYSTEMS = ["disk", "nvme", "pcie", "memory", "thermal"]
FAULTS = ["error", "timeout", "fault"]
PATTERN_MSG_COUNT = 12          # >= 10 as requested; with error:10 -> Error
PATTERN_LEVEL = 6               # KERN_INFO: above the reserved (<=2) threshold

# level -> reserved bucket name (must match healthagent.kmsg.get_level)
RESERVED = {0: "KERNEL_EMERGENCY", 1: "KERNEL_ALERT", 2: "KERNEL_CRITICAL"}

STARTUP_TIMEOUT = 20            # seconds to wait for the socket after restart
VERIFY_TIMEOUT = 15            # seconds to wait for the report to reflect injects
VERIFY_INTERVAL = 1


# ── Helpers ─────────────────────────────────────────────────────────────────

def require_root():
    if os.geteuid() != 0:
        sys.exit("ERROR: must run as root (needs /dev/kmsg and config write access).")


def _run(cmd, check=True):
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def get_response(command: dict, timeout: int = 10):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(SOCKET_PATH)
            s.sendall(json.dumps(command).encode())
            s.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode())
    except (ConnectionRefusedError, FileNotFoundError):
        print("ERROR: cannot connect to healthagent. Is it running?", file=sys.stderr)
        return None
    except socket.timeout:
        print("ERROR: socket timed out.", file=sys.stderr)
        return None


# ── Config phase ────────────────────────────────────────────────────────────

def initialize_config():
    """Back up the current config and merge in the self-test rule."""
    if os.path.exists(CONFIG_PATH):
        if not os.path.exists(BACKUP_PATH):
            shutil.copy2(CONFIG_PATH, BACKUP_PATH)
            print(f"Backed up {CONFIG_PATH} -> {BACKUP_PATH}")
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}
    else:
        # Record that there was no original file, so teardown can remove it.
        open(NOFILE_MARKER, "w").close()
        config = {}

    if not isinstance(config, dict):
        sys.exit(f"ERROR: {CONFIG_PATH} is not a YAML mapping.")

    kmsg = config.setdefault("kmsg", {})
    patterns = kmsg.setdefault("patterns", {})
    patterns[RULE_NAME] = RULE

    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"Added kmsg pattern {RULE_NAME!r} to {CONFIG_PATH}:")
    print(f"    pattern: {RULE['pattern']}")
    print(f"    warning: {RULE['warning']}   error: {RULE['error']}")
    print_test_mode_reminder()


def print_test_mode_reminder():
    print(
        "\nNOTE: reserved-level checks need the daemon started with "
        "KMSG_TEST_MODE=true (userspace /dev/kmsg lines carry the LOG_USER "
        "facility). Set it and restart before injecting:\n"
        "    sudo systemctl edit healthagent   # [Service]\n"
        '        Environment="KMSG_TEST_MODE=True"\n'
        "    sudo systemctl restart healthagent\n"
        "The regex pattern checks work regardless of this setting."
    )


def teardown_config():
    """Restore the original config (or remove it if there was none)."""
    if os.path.exists(NOFILE_MARKER):
        if os.path.exists(CONFIG_PATH):
            os.remove(CONFIG_PATH)
        os.remove(NOFILE_MARKER)
        print(f"Removed {CONFIG_PATH} (no original existed).")
    elif os.path.exists(BACKUP_PATH):
        shutil.move(BACKUP_PATH, CONFIG_PATH)
        print(f"Restored {CONFIG_PATH} from backup.")
    else:
        print("Nothing to restore (no backup found).")


# ── Inject phase ────────────────────────────────────────────────────────────

def _kmsg_write(level: int, text: str):
    # A leading '<N>' sets the syslog level; userspace writes get the LOG_USER
    # facility, so healthagent only honors these levels under KMSG_TEST_MODE.
    with open("/dev/kmsg", "w") as f:
        f.write(f"<{level}>{text}\n")


def inject():
    """Write reserved-level lines and a burst of pattern-matching lines."""
    print_test_mode_reminder()
    print("\nInjecting reserved severe-level messages:")
    for level, name in RESERVED.items():
        text = f"healthagent-selftest reserved level {level} -> {name}"
        _kmsg_write(level, text)
        print(f"  <{level}> {name}: {text}")

    print(f"Injecting {PATTERN_MSG_COUNT} pattern messages (level {PATTERN_LEVEL}):")
    for i in range(PATTERN_MSG_COUNT):
        subsys = SUBSYSTEMS[i % len(SUBSYSTEMS)]
        fault = FAULTS[i % len(FAULTS)]
        text = f"healthagent-selftest: {subsys} {fault} {i}"
        _kmsg_write(PATTERN_LEVEL, text)
        print(f"  <{PATTERN_LEVEL}> {text}")


# ── Restart / verify ────────────────────────────────────────────────────────

def restart_healthagent():
    print("Restarting healthagent...")
    _run(["systemctl", "restart", "healthagent"])
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect(SOCKET_PATH)
            print("  healthagent is up.")
            return
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            time.sleep(1)
    raise RuntimeError(f"healthagent socket not available within {STARTUP_TIMEOUT}s.")


def verify() -> bool:
    """Poll the status socket and assert the expected buckets/status appear."""
    expected = set(RESERVED.values()) | {RULE_NAME}
    deadline = time.monotonic() + VERIFY_TIMEOUT
    report = {}
    while time.monotonic() < deadline:
        response = get_response({"command": "status"})
        report = (response or {}).get("kmsg", {}).get(REPORT_NAME, {})
        present = expected & set(report.keys())
        if present == expected:
            break
        time.sleep(VERIFY_INTERVAL)

    print("\n=== KernelLogCheck report ===")
    print(json.dumps(report, indent=2))

    ok = True
    for name in sorted(expected):
        hit = name in report
        ok = ok and hit
        print(f"  [{'PASS' if hit else 'FAIL'}] bucket {name} present")

    status = report.get("status")
    status_ok = status == "Error"
    ok = ok and status_ok
    print(f"  [{'PASS' if status_ok else 'FAIL'}] overall status is Error (got {status!r})")

    selftest = report.get(RULE_NAME, {})
    count_ok = selftest.get("count") == ">=10"
    ok = ok and count_ok
    print(f"  [{'PASS' if count_ok else 'FAIL'}] {RULE_NAME} count == '>=10' "
          f"(got {selftest.get('count')!r})")

    print(f"\n{'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}")
    reserved_missing = [n for n in RESERVED.values() if n not in report]
    if reserved_missing:
        print(f"\nMissing reserved buckets: {reserved_missing}")
        print_test_mode_reminder()
    return ok


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="kmsg module integration test.")
    parser.add_argument("--initialize", action="store_true", help="add the self-test rule to the config")
    parser.add_argument("--inject", action="store_true", help="write reserved-level and pattern messages to /dev/kmsg")
    parser.add_argument("--verify", action="store_true", help="query the socket and assert the report")
    parser.add_argument("--teardown", action="store_true", help="restore the original config")
    args = parser.parse_args()

    require_root()

    manual = args.initialize or args.inject or args.verify or args.teardown
    if manual:
        if args.initialize:
            initialize_config()
            print("\nNow run: systemctl restart healthagent   (then: --inject, --verify)")
        if args.inject:
            inject()
        if args.verify:
            sys.exit(0 if verify() else 1)
        if args.teardown:
            teardown_config()
            print("Run: systemctl restart healthagent   to drop the self-test rule.")
        return

    # Full automated run.
    initialize_config()
    restart_healthagent()
    inject()
    passed = verify()
    print("\nConfig left in place. To clean up:")
    print(f"  sudo {sys.argv[0]} --teardown && sudo systemctl restart healthagent")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
