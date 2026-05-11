#!/usr/bin/env python3
"""
Integration test for SystemdMonitor config-driven service registration.

Requires root (sudo).

Usage
-----
# Full automated run (default — no arguments needed):
    sudo python3 integration/test_systemd_monitor.py

# Step-by-step manual control:
    sudo python3 integration/test_systemd_monitor.py --initialize
    sudo systemctl restart healthagent
    sudo python3 integration/test_systemd_monitor.py --run
    sudo python3 integration/test_systemd_monitor.py --teardown
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import textwrap

# ── Constants ───────────────────────────────────────────────────────────────

SOCKET_PATH = "/opt/healthagent/run/health.sock"
TEST_SERVICE_NAME = "healthagent-test.service"
TEST_MODE_FILE = "/run/healthagent-test-mode"
UNIT_FILE_PATH = f"/etc/systemd/system/{TEST_SERVICE_NAME}"
HEALTHAGENT_CONFIG_PATH = "/etc/healthagent/config.yaml"

UNIT_FILE_CONTENT = textwrap.dedent("""\
    [Unit]
    Description=Healthagent integration test service

    [Service]
    Type=simple
    ExecStart=/bin/bash -c 'if [ "$(cat {mode_file} 2>/dev/null)" = "fail" ]; then exit 1; fi; exec sleep infinity'

    [Install]
    WantedBy=multi-user.target
""").format(mode_file=TEST_MODE_FILE)

# Minimal config: monitor only the test service so the test isn't affected
# by unrelated services (slurmd, munge, etc.) that may not be running.
CONFIG_YAML_CONTENT = textwrap.dedent("""\
    systemd:
      services:
        - {service}
""").format(service=TEST_SERVICE_NAME)

POLL_INTERVAL = 1    # seconds between status polls
POLL_TIMEOUT  = 15   # seconds to wait for healthagent to reflect a state change
STARTUP_TIMEOUT = 20 # seconds to wait for healthagent to come up after restart


# ── Socket helpers ───────────────────────────────────────────────────────────

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
        print("ERROR: Cannot connect to healthagent. Is it running?", file=sys.stderr)
        return None
    except socket.timeout:
        print("ERROR: Socket timed out.", file=sys.stderr)
        return None


def get_systemd_status() -> dict | None:
    """Return the systemd module's report dict, or None if unavailable."""
    response = get_response({"command": "status"})
    if response is None:
        return None
    return response.get("systemd")


# ── Service control helpers ──────────────────────────────────────────────────

def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def set_service_mode(mode: str):
    """Write 'fail' or 'ok' to the mode file and restart the test service."""
    with open(TEST_MODE_FILE, "w") as f:
        f.write(mode)
    _run(["systemctl", "restart", TEST_SERVICE_NAME])


def poll_for_service_status(expected_status: str) -> bool:
    """
    Poll the healthagent status socket until the systemd module reports
    the expected status for TEST_SERVICE_NAME, or until POLL_TIMEOUT.

    expected_status: "Error" or "OK"
    """
    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        module = get_systemd_status()
        if module:
            check = module.get("SystemdServiceCheck", {})
            current = check.get("status")
            svc_status = check.get(TEST_SERVICE_NAME, {}).get("status")
            print(f"  [poll] module={current!r}  service={svc_status!r}")
            if current == expected_status:
                return True
        time.sleep(POLL_INTERVAL)
    return False


# ── Healthagent control helpers ──────────────────────────────────────────────

def restart_healthagent():
    """Restart healthagent and wait until its socket is available."""
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
    raise RuntimeError(f"healthagent did not come up within {STARTUP_TIMEOUT}s after restart.")


def verify_config():
    """
    Query 'health -C' (show_config socket command) and assert that the
    test service appears in systemd.services.
    """
    print("Verifying effective config via 'health -C'...")
    config = get_response({"command": "show_config"})
    if config is None:
        raise RuntimeError("show_config returned no response.")
    services = config.get("systemd", {}).get("services", [])
    if TEST_SERVICE_NAME not in services:
        raise RuntimeError(
            f"Expected {TEST_SERVICE_NAME!r} in systemd.services, got: {services}"
        )
    print(f"  PASS: {TEST_SERVICE_NAME!r} present in loaded config (systemd.services: {services}).")


# ── Initialize ───────────────────────────────────────────────────────────────

def cmd_initialize():
    """Install unit file, write config.yaml, enable and start the test service."""
    print(f"Writing unit file → {UNIT_FILE_PATH}")
    with open(UNIT_FILE_PATH, "w") as f:
        f.write(UNIT_FILE_CONTENT)

    print("Reloading systemd daemon...")
    _run(["systemctl", "daemon-reload"])

    print(f"Enabling and starting {TEST_SERVICE_NAME}...")
    # Start in 'ok' mode
    with open(TEST_MODE_FILE, "w") as f:
        f.write("ok")
    _run(["systemctl", "enable", TEST_SERVICE_NAME])
    _run(["systemctl", "restart", TEST_SERVICE_NAME])

    print(f"Writing healthagent config → {HEALTHAGENT_CONFIG_PATH}")
    os.makedirs(os.path.dirname(HEALTHAGENT_CONFIG_PATH), exist_ok=True)
    with open(HEALTHAGENT_CONFIG_PATH, "w") as f:
        f.write(CONFIG_YAML_CONTENT)

    print()
    print("Initialization complete.")
    print("Restart healthagent and then run the tests:")
    print()
    print("    sudo systemctl restart healthagent")
    print(f"    sudo python3 {sys.argv[0]} --run")


# ── Full automated run ───────────────────────────────────────────────────────

def cmd_full_run():
    """Initialize → restart healthagent → verify config → run tests → teardown → restart healthagent."""
    print("=== Full automated run ===")
    print()

    cmd_initialize()
    print()

    restart_healthagent()
    print()

    verify_config()
    print()

    cmd_run(pause=False)
    print()

    cmd_teardown()
    print()

    restart_healthagent()
    print()
    print("Done. healthagent restored to default config.")


# ── Run ──────────────────────────────────────────────────────────────────────

def cmd_run(pause: bool = False):
    failures = []

    # ── Precondition: healthagent sees the service ───────────────────────────
    print("=== Precondition: service registered with healthagent ===")
    module = get_systemd_status()
    if module is None:
        print("FAIL: Could not reach healthagent.", file=sys.stderr)
        sys.exit(1)

    check = module.get("SystemdServiceCheck", {})
    if TEST_SERVICE_NAME not in check:
        loaded = [k for k in check if k.endswith(".service")]
        print(f"FAIL: {TEST_SERVICE_NAME!r} not found in systemd module status.")
        print(f"      Loaded services: {loaded}")
        print("      Did you run --initialize and restart healthagent?")
        sys.exit(1)
    print(f"  PASS: {TEST_SERVICE_NAME!r} is registered.")

    # ── Test 1: failure detection ────────────────────────────────────────────
    print()
    print("=== Test 1: healthagent detects service failure ===")
    print(f"  Failing {TEST_SERVICE_NAME}...")
    set_service_mode("fail")

    if poll_for_service_status("Error"):
        print("  PASS: healthagent reported Error after service failure.")
        if pause:
            print("  Sleeping 20s for manual verification...")
            time.sleep(20)
    else:
        msg = f"FAIL: healthagent did not report Error within {POLL_TIMEOUT}s."
        print(f"  {msg}")
        failures.append(msg)

    # ── Test 2: recovery detection ───────────────────────────────────────────
    print()
    print("=== Test 2: healthagent detects service recovery ===")
    print(f"  Recovering {TEST_SERVICE_NAME}...")
    set_service_mode("ok")

    if poll_for_service_status("OK"):
        print("  PASS: healthagent reported OK after service recovery.")
    else:
        msg = f"FAIL: healthagent did not report OK within {POLL_TIMEOUT}s."
        print(f"  {msg}")
        failures.append(msg)

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    if failures:
        print(f"FAILED ({len(failures)} failure(s)):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All tests passed.")


# ── Teardown ─────────────────────────────────────────────────────────────────

def cmd_teardown():
    """Remove the test service and config. Restart healthagent afterwards."""
    print(f"Stopping and disabling {TEST_SERVICE_NAME}...")
    _run(["systemctl", "disable", "--now", TEST_SERVICE_NAME], check=False)

    for path in [UNIT_FILE_PATH, TEST_MODE_FILE]:
        if os.path.exists(path):
            os.remove(path)
            print(f"Removed {path}")

    print("Reloading systemd daemon...")
    _run(["systemctl", "daemon-reload"])

    if os.path.exists(HEALTHAGENT_CONFIG_PATH):
        os.remove(HEALTHAGENT_CONFIG_PATH)
        print(f"Removed {HEALTHAGENT_CONFIG_PATH}")

    print()
    print("Teardown complete. Restart healthagent to restore defaults:")
    print("    sudo systemctl restart healthagent")


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Systemd monitor integration test for healthagent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Default (no arguments): full automated run
              sudo python3 integration/test_systemd_monitor.py

            Step-by-step manual control:
              sudo python3 integration/test_systemd_monitor.py --initialize
              sudo systemctl restart healthagent
              sudo python3 integration/test_systemd_monitor.py --run
              sudo python3 integration/test_systemd_monitor.py --teardown
              sudo systemctl restart healthagent
        """)
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--initialize", action="store_true",
                       help="Install test service and config.yaml")
    group.add_argument("--run",        action="store_true",
                       help="Execute test scenarios against running healthagent")
    group.add_argument("--teardown",   action="store_true",
                       help="Remove test service and config.yaml")
    parser.add_argument("--pause",     action="store_true",
                        help="Pause 20s after failure detection for manual verification (only with --run)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("This script must be run as root (sudo).", file=sys.stderr)
        sys.exit(1)

    if args.initialize:
        cmd_initialize()
    elif args.run:
        cmd_run(pause=args.pause)
    elif args.teardown:
        cmd_teardown()
    else:
        cmd_full_run()


if __name__ == "__main__":
    main()
