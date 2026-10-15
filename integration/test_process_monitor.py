#!/usr/bin/env python3
"""Verify healthagent detects a D-state process with pending SIGTERM.

Run from the repository root:
    sudo python3 integration/test_process_monitor.py
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time


REPORT_NAME = "ProcessStateCheck"
STATE_TIMEOUT = 15
HEALTH_TIMEOUT = 30
SIGTERM_MASK = 1 << (signal.SIGTERM - 1)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(command)}", flush=True)
    return subprocess.run(command, check=check, text=True, timeout=STATE_TIMEOUT)


def require_root_commands_and_healthagent():
    if os.geteuid() != 0:
        sys.exit("ERROR: run this integration test as root.")

    required = ("fallocate", "fsfreeze", "health", "mkfs.ext4", "mount", "umount")
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        sys.exit(f"ERROR: required commands not found: {', '.join(missing)}")


def read_process_status(pid: int) -> dict[str, str]:
    status = {}
    with open(f"/proc/{pid}/status", encoding="utf-8") as status_file:
        for line in status_file:
            key, separator, value = line.partition(":")
            if separator:
                status[key] = value.strip()
    return status


def wait_for_process_state(
    process: subprocess.Popen,
    predicate,
    description: str,
) -> dict[str, str]:
    deadline = time.monotonic() + STATE_TIMEOUT
    last_status = {}
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"process {process.pid} exited with {process.returncode} before {description}"
            )
        last_status = read_process_status(process.pid)
        if predicate(last_status):
            return last_status
        time.sleep(0.05)
    raise RuntimeError(
        f"process {process.pid} did not reach {description}; last status: {last_status}"
    )


@contextmanager
def unkillable_process():
    work_path = Path(tempfile.mkdtemp(prefix="healthagent-proc-"))
    image_path = work_path / "filesystem.img"
    mount_path = work_path / "mnt"

    process = None
    mounted = False
    frozen = False
    try:
        mount_path.mkdir()
        run(["fallocate", "-l", "8M", str(image_path)])
        run(["mkfs.ext4", "-F", "-q", str(image_path)])
        mounted = True
        run(["mount", "-o", "loop", str(image_path), str(mount_path)])
        frozen = True
        run(["fsfreeze", "-f", str(mount_path)])

        process = subprocess.Popen(["mkdir", str(mount_path / "blocked")])
        wait_for_process_state(
            process,
            lambda status: status.get("State", "").startswith("D"),
            "D state",
        )
        print(f"PID {process.pid} entered D state")

        os.kill(process.pid, signal.SIGTERM)
        status = wait_for_process_state(
            process,
            lambda current: (
                current.get("State", "").startswith("D")
                and int(current.get("ShdPnd", "0"), 16) & SIGTERM_MASK
            ),
            "D state with process-level SIGTERM pending",
        )
        print(
            f"PID {process.pid} has pending SIGTERM "
            f"(ShdPnd={status['ShdPnd']})"
        )
        yield process.pid
    finally:
        cleanup_errors = []
        if frozen:
            try:
                run(["fsfreeze", "-u", str(mount_path)])
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append(f"Thaw failed: {error}")
        if process is not None:
            try:
                try:
                    process.wait(timeout=STATE_TIMEOUT)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=STATE_TIMEOUT)
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append(f"Child cleanup failed: {error}")
        if mounted:
            try:
                run(["umount", str(mount_path)])
                mounted = False
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append(f"Unmount failed: {error}")
        if mounted:
            print(
                f"Retained {work_path}: unmount was not confirmed. "
                f"Recover as root: thaw {mount_path} with fsfreeze -u, "
                f"terminate/reap child PID {process.pid if process is not None else 'N/A'}, "
                f"then umount {mount_path}. Remove {work_path} only after unmounting.",
                file=sys.stderr,
            )
        else:
            shutil.rmtree(work_path)
        if cleanup_errors:
            raise RuntimeError("; ".join(cleanup_errors))


def run_health(*arguments: str) -> dict:
    command = ["health", *arguments]
    print(f"$ {' '.join(command)}", flush=True)
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=HEALTH_TIMEOUT,
    )
    return json.loads(result.stdout)


def verify_process_monitor(pid: int):
    run_health("-e", "-c", REPORT_NAME)
    response = run_health("-s")
    report = response.get("proc", {}).get(REPORT_NAME)
    if not report:
        raise AssertionError(
            f"{REPORT_NAME} missing from healthagent response: {response}"
        )
    if report.get("status") != "Error":
        raise AssertionError(
            f"expected {REPORT_NAME} status Error, got {report.get('status')!r}"
        )
    expected_description = "Unkillable hung processes found"
    if report.get("description") != expected_description:
        raise AssertionError(
            f"expected description {expected_description!r}, "
            f"got {report.get('description')!r}"
        )
    hung_processes = report.get("HungProcesses", [])
    detected_process = next(
        (process for process in hung_processes if process.get("pid") == pid),
        None,
    )
    if detected_process is None:
        raise AssertionError(
            f"{REPORT_NAME} did not include PID {pid} in HungProcesses: "
            f"{hung_processes}"
        )

    print(f"PASS: {REPORT_NAME} reported unkillable process while PID {pid} was blocked")
    print(json.dumps(report, indent=2))


def main():
    require_root_commands_and_healthagent()
    with unkillable_process() as pid:
        verify_process_monitor(pid)


if __name__ == "__main__":
    main()