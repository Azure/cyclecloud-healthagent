## Developer Guide

- [Development Setup](#development-setup)
- [Building with package.sh](#building-with-packagesh)
  - [Non-Production (Development) Mode](#non-production-development-mode)
  - [Production Mode](#production-mode)
- [Running Tests](#running-tests)
  - [Unit Tests](#unit-tests)
  - [Integration Tests](#integration-tests)
    - [Systemd Monitor](#systemd-monitor)
    - [DCGM Field Injection](#dcgm-field-injection)
    - [Kmsg Message Injection](#kmsg-message-injection)
    - [Ext4 Remount-on-Error](#ext4-remount-on-error)
    - [Process Monitor](#process-monitor)
- [DCGM Bindings for VS Code](#dcgm-bindings-for-vs-code)
- [DCGM Test Mode](#dcgm-test-mode)
- [Kmsg Test Mode](#kmsg-test-mode)

---

### Development Setup

Use `dev-setup.sh` to create a local development environment. This sets up a `.dev_venv` virtual environment in the root of the repo and installs healthagent in editable mode with dev and test dependencies.

```bash
./dev-setup.sh
```

This will:
1. Find the highest available Python 3.11+ on your system
2. Create a virtual environment at `.dev_venv/`
3. Install healthagent in editable mode (`pip install -e ".[dev]"`)
4. Install dev dependencies: `build`, `pytest`, `pytest-asyncio`

Activate the environment:
```bash
source .dev_venv/bin/activate
```

To recreate the virtual environment from scratch:
```bash
./dev-setup.sh --recreate
```

You can also override the Python binary:
```bash
PYTHON=/usr/bin/python3.12 ./dev-setup.sh
```

---

### Building with package.sh

`package.sh` builds the healthagent source distribution and places it in the `blobs/` directory. It supports two modes.

#### Non-Production (Development) Mode

```bash
./package.sh
```

- Reuses the existing `.dev_venv/` virtual environment (creates one if it doesn't exist)
- Does **not** check for uncommitted changes
- Suitable for local iteration and testing

#### Production Mode

```bash
./package.sh --prod
```

- **Requires a clean git tree** — fails if there are uncommitted changes
- Creates a **fresh** `.prod_venv/` virtual environment every time (deletes any existing one). Does not touch .dev_venv.
- Ensures a reproducible build from a known state

**Only use production mode for releases**.

Both modes:
1. Delete existing blobs listed in `project.ini`
2. Build a source distribution via `python -m build`
3. Move the `.tar.gz` artifact into `blobs/`
4. Verify that all expected blob files from `project.ini` are present
5. Print the current branch, last commit, and project version

Build logs are written to `.build.log`. If the build fails, check this file for details.

---

### Running Tests

Unit tests require the **dev environment** to be active:
```bash
source .dev_venv/bin/activate
```

#### Unit Tests

Unit tests live in `tests/` and can run on any development machine without GPUs or root access.

```bash
# Run all unit tests
pytest tests/

# Run a specific test file
pytest tests/test_config.py

# Run with verbose output
pytest tests/ -v

# Run a specific test by name
pytest tests/test_healthmodule.py -k "test_execute"
```

Available test modules:
| Test File | Covers |
|-----------|--------|
| `test_config.py` | Config loading, deep merge, Pydantic validation |
| `test_healthmodule.py` | Healthcheck decorator, check registry, execute logic |
| `test_reporter.py` | HealthReport, HealthStatus, CLI exclude behavior |
| `test_async_systemd.py` | Systemd monitor state transitions, D-Bus callbacks |
| `test_network.py` | Network interface checks, threshold evaluation |
| `test_scheduler.py` | Task scheduling, periodic tasks, locks |
| `test_util.py` | Evaluate functions, TimeSeries, read_kernel_attrs |

#### Integration Tests

Integration tests live in [integration/](integration/). **Run every integration test as root on a Linux test node with healthagent installed and running as root.** Run the commands below from the repository root on that node, not from a workstation. These are standalone scripts, not a pytest suite.

Use a dedicated test node without active workloads: tests restart healthagent, change configuration, create services, or inject faults that can trigger health alerts and external automation. Run one test at a time, back up `/etc/healthagent/config.yaml`, and complete cleanup before starting another test. GPU injection additionally requires NVIDIA GPUs and DCGM.

Examples use the installed healthagent interpreter at `/opt/healthagent/.venv/bin/python` (Python 3.11+). Adjust this path for your installation. The kmsg scripts require PyYAML from that environment; activating the development environment alone does not select the interpreter used by `sudo`.

```bash
# Confirm the service is active and its process runs as root
sudo systemctl is-active healthagent
sudo systemctl show healthagent --property=MainPID --value
# Use the PID printed above
sudo ps -o user,pid,args -p <MainPID>

# Confirm the local health interface responds
sudo /opt/healthagent/.venv/bin/health -s
```

The socket-based tests use `/opt/healthagent/run/health.sock` and configuration at `/etc/healthagent/config.yaml`. Ensure the relevant health module is enabled. Test-mode environment variables must be set on the **running daemon**, not just on the test script. After testing, remove any test-mode settings you added, run `sudo systemctl daemon-reload`, and restart healthagent.

#### Systemd Monitor

[integration/test_systemd_monitor.py](integration/test_systemd_monitor.py) verifies config-driven registration and failure/recovery detection through `SystemdServiceCheck`. It requires systemd and access to the healthagent socket; no GPU or test-mode flag is needed.

**Configuration warning:** initialization overwrites `/etc/healthagent/config.yaml` with a minimal configuration monitoring only `healthagent-test.service`. Teardown deletes that configuration; it does **not** back up or restore your original file. Preserve your original configuration separately before running this test.

```bash
# Full run: initialize, restart, verify config, test, teardown, restart
sudo /opt/healthagent/.venv/bin/python integration/test_systemd_monitor.py

# Alternatively, run each phase separately
sudo /opt/healthagent/.venv/bin/python integration/test_systemd_monitor.py --initialize
sudo systemctl restart healthagent
sudo /opt/healthagent/.venv/bin/python integration/test_systemd_monitor.py --run
sudo /opt/healthagent/.venv/bin/python integration/test_systemd_monitor.py --teardown
sudo systemctl restart healthagent
```

The test creates `/etc/systemd/system/healthagent-test.service` and `/run/healthagent-test-mode`, fails the service, and polls for `Error`, then recovers it and polls for `OK` (up to 15 seconds per transition). Success prints `All tests passed.` Use `--run --pause` to pause for 20 seconds after failure detection for manual inspection.

Teardown stops/disables the test service, removes its unit and mode file, reloads systemd, and deletes the test configuration. A failed full run may exit before teardown: run `--teardown` explicitly in that case. After either run, restore your saved original configuration, if any, and restart healthagent again. Without an original configuration, healthagent returns to defaults.

#### DCGM Field Injection

[integration/test_inject.py](integration/test_inject.py) injects DCGM field values to exercise GPU health reporting. In addition to the common root/node requirements, it needs a supported NVIDIA GPU, `dcgmi` on root's `PATH`, `nvidia-dcgm.service` running in standalone mode, and healthagent running with [DCGM Test Mode](#dcgm-test-mode) enabled.

```bash
sudo systemctl is-active nvidia-dcgm

# Run all scenarios on GPU 0; duration applies to EACH injected value
sudo /opt/healthagent/.venv/bin/python integration/test_inject.py --gpu 0 --test all --duration 120

# Inject high temperature on GPU 1 for 60 seconds
sudo /opt/healthagent/.venv/bin/python integration/test_inject.py --gpu 1 --test temp --duration 60

# Inspect healthagent from another terminal while injection is running
sudo /opt/healthagent/.venv/bin/health -s

# Inject healthy values on the SAME GPU after testing
sudo /opt/healthagent/.venv/bin/python integration/test_inject.py --gpu 1 --test clear
```

`--gpu` defaults to `0`, `--test` to `all`, and `--duration` to `120` seconds. Values are re-injected every second because injected cache entries expire quickly. `all` runs scenarios sequentially and excludes `clear`; it is not a two-minute total run. The `xid` scenario injects two values, each for the requested duration.

| `--test` | Simulated condition |
|----------|---------------------|
| `temp` | High GPU temperature |
| `clocks` | Clock throttling reasons |
| `persist` | Persistence mode disabled |
| `dbe` | Volatile double-bit ECC error |
| `remap` | Row remap failure |
| `pending` | Pending retired pages |
| `ber` | High effective bit error rate |
| `fabric` | Fabric manager failure |
| `fabric_health` | Unhealthy fabric route |
| `recovery` | GPU reset recovery action |
| `xid` | XID 48 (error), then XID 63 (warning) |
| `clear` | Healthy values for the injected fields |

The script prints injection `OK`/`FAIL` results but does **not** assert healthagent reports. Inspect `health -s` while injecting and confirm the corresponding GPU warnings/errors under the node's configured checks and thresholds. Unsupported fields or hardware can prevent a scenario from working; injection success alone is not an end-to-end pass.

Run `--test clear` for every GPU you targeted, including GPU 0 after an `all` run. This injects healthy cache values; it does not repair real hardware errors or guarantee immediate removal of historical alerts. The script does not modify healthagent configuration or automatically disable `DCGM_TEST_MODE`.

#### Kmsg Message Injection

[integration/test_kmsg_inject.py](integration/test_kmsg_inject.py) tests all three reserved severe-level buckets plus a configured regex rule in `KernelLogCheck`. It requires writable `/dev/kmsg`, PyYAML, systemd, and [Kmsg Test Mode](#kmsg-test-mode) enabled on the running daemon. Except for a standalone `--teardown`, the script prints `SKIP` and returns without testing if it cannot confirm `KMSG_TEST_MODE=true` in the daemon's environment. A skip is not a pass.

```bash
# Full run: add config, restart healthagent, inject, verify
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_inject.py

# Always clean up after the full run, including a failed verification
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_inject.py --teardown
sudo systemctl restart healthagent

# Alternatively, run each phase separately
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_inject.py --initialize
sudo systemctl restart healthagent
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_inject.py --inject
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_inject.py --verify
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_inject.py --teardown
sudo systemctl restart healthagent
```

Initialization backs up an existing config to `/etc/healthagent/config.yaml.selftest.bak` (or records its absence with a `.selftest.nofile` marker) and adds `kmsg.patterns.healthagent_selftest`. Injection writes levels 0, 1, and 2 plus 12 pattern-matching informational messages. Verification polls for up to 15 seconds, expects `KERNEL_EMERGENCY`, `KERNEL_ALERT`, `KERNEL_CRITICAL`, and `healthagent_selftest`, an overall `Error`, and a pattern count of `>=10`. Success prints `ALL CHECKS PASSED`.

Both full and staged runs leave the test config in place until `--teardown`; teardown restores the saved config or removes it if none originally existed. Restart afterward to drop the rule. Do not make unrelated config edits during the test, since restoring the backup replaces the file. Teardown does not erase messages already written to the kernel log.

#### Ext4 Remount-on-Error

[integration/test_kmsg_ext4_remount.py](integration/test_kmsg_ext4_remount.py) verifies a **real kernel** ext4 remount-on-error event, not a userspace log injection. It requires PyYAML, ext4/loop/device-mapper kernel support, and these commands on root's `PATH`: `blockdev`, `dmsetup`, `findmnt`, `journalctl`, `losetup`, `mkfs.ext4`, `mount`, `sync`, `systemctl`, `truncate`, and `umount`. No `KMSG_TEST_MODE` is required; leave it unset to exercise genuine kernel-facility detection.

**Use only a dedicated test node.** The script creates a disposable 128 MiB image, loop device, and device-mapper mapping, then deliberately forces I/O failures on that test filesystem. Do not substitute a real device or place workloads on the test mount.

```bash
# Full run: initialize, trigger I/O errors, verify; resources remain mounted
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_ext4_remount.py

# Mandatory cleanup after inspection, including after a failed run
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_ext4_remount.py --restore

# Alternatively, run each phase separately
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_ext4_remount.py --initialize
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_ext4_remount.py --trigger
# --trigger already verifies; use --verify to repeat inspection
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_ext4_remount.py --verify
sudo /opt/healthagent/.venv/bin/python integration/test_kmsg_ext4_remount.py --restore
```

Initialization adds `kmsg.patterns.ext4_remount_ro`, restarts healthagent, and mounts the image at `/var/tmp/healthagent-kmsg-ext4-remount/mnt` via `/dev/mapper/healthagent-kmsg-ext4`. Triggering switches that mapping to an error target and forces writeback. Verification waits up to 20 seconds for the kernel remount message, a read-only test mount, and `Error` status for both `ext4_remount_ro` and `KERNEL_CRITICAL`. Success prints `EXT4 KMSG TEST PASSED`.

The full run deliberately leaves resources and the rule in place for inspection. `--restore` restores the linear mapping, unmounts, removes the mapping, detaches the loop device, restores the previous rule (or removes the added rule), restarts healthagent, and deletes the work directory. It preserves unrelated configuration entries rather than restoring an entire file backup. If restoration fails, state remains in `/var/tmp/healthagent-kmsg-ext4-remount/state.json`; resolve the reported problem and retry `--restore`. Do not delete that directory while resources are still mounted or needed for recovery.

The genuine `KERN_CRIT` message remains in the kernel ring buffer after cleanup. Healthagent can replay it for up to one hour, so cleanup does not imply an immediately healthy report.

#### Process Monitor

[integration/test_process_monitor.py](integration/test_process_monitor.py) verifies that `ProcessStateCheck` detects a D-state process with a pending `SIGTERM`. It requires ext4/loop filesystem support and `fallocate`, `fsfreeze`, `health`, `mkfs.ext4`, `mount`, and `umount` on root's `PATH`. No GPU or test-mode flag is needed.

```bash
# Make the installed health CLI available to the script under sudo
sudo env PATH="/opt/healthagent/.venv/bin:$PATH" /opt/healthagent/.venv/bin/python integration/test_process_monitor.py
```

There are no phase flags: the script runs setup, verification, and cleanup in one invocation. It creates an 8 MiB ext4 image under a temporary `healthagent-proc-*` directory, mounts and freezes that disposable filesystem, starts a blocked `mkdir` process, sends `SIGTERM`, and confirms D state plus the pending signal through `/proc/<pid>/status`.

The script runs `health -e -c ProcessStateCheck` to execute the check and `health -s` to inspect it. It requires `proc.ProcessStateCheck` to report `Error`, description `Unkillable hung processes found`, and the test PID in `HungProcesses`. Success prints `PASS: ProcessStateCheck reported unkillable process` with the report. Process-state waits allow 15 seconds; health CLI commands allow 30 seconds.

Cleanup runs even if verification fails: it thaws the filesystem, waits for or terminates the child, unmounts, and removes the temporary directory. It does not edit healthagent configuration. If unmount cannot be confirmed, the script retains the directory and prints the exact mount path and PID. As root, thaw that mount with `fsfreeze -u`, terminate/reap the child, then unmount it. Remove the directory only after confirming it is unmounted. Do not freeze any production filesystem or access the frozen test mount from other workloads.

---

### DCGM Bindings for VS Code

Healthagent relies on DCGM bindings for GPU checks. The VS Code workspace settings file adds import paths for these bindings. To get IntelliSense working:

1. Copy the DCGM bindings from a running node
2. Place them in `${workspaceFolder}/.bindings/dcgm-4.5.0/`

---

### DCGM Test Mode

DCGM supports two modes of operation — [Standalone mode and Embedded mode](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/getting-started.html#modes-of-operation).

Healthagent by default loads DCGM in **embedded** mode, loading the DCGM library directly as a shared library. This avoids reliance on the `nvidia-dcgm` service (`nv-hostengine`) in production, since a service crash would disable GPU tests.

However, DCGM's [error injection framework](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-error-injection.html#error-injection-workflow) only works in **standalone** mode because injected values are stored in `nv-hostengine`'s cache. To test healthagent behavior by simulating DCGM errors, use `DCGM_TEST_MODE`.

#### Enabling DCGM Test Mode

Set the environment variable in the healthagent systemd unit file (`/etc/systemd/system/healthagent.service`):

```ini
[Service]
Environment="DCGM_TEST_MODE=True"
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart healthagent
```

#### Injecting Errors

With `DCGM_TEST_MODE` enabled and `nvidia-dcgm.service` running, inject values from another terminal using `dcgmi test --inject`.

For example, injecting a XID 63 error on GPU 0:

```bash
sudo dcgmi test --inject --gpuid 0 -f 230 -v 63
# Successfully injected field info.
```

Injected values expire from the DCGM cache quickly. See [DCGM Field Injection](#dcgm-field-injection) for continuous injection commands, supported scenarios, report verification, and cleanup. Remove the `DCGM_TEST_MODE` setting after testing, reload systemd, and restart healthagent to return to embedded mode.

**References:**
- [DCGM Modes of Operation](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/getting-started.html#modes-of-operation)
- [DCGM Error Injection Framework](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-error-injection.html#error-injection-workflow)

---

### Kmsg Test Mode

The kmsg module's reserved severe-level buckets (`KERNEL_EMERGENCY` / `KERNEL_ALERT` /
`KERNEL_CRITICAL`) only react to **genuine kernel messages** (syslog facility `LOG_KERN`).
Lines written to `/dev/kmsg` from userspace carry the `LOG_USER` facility, so healthagent
ignores them for severe-level detection — which means you cannot exercise that path by
injecting test messages unless you opt in.

Setting `KMSG_TEST_MODE=true` relaxes this: userspace-injected lines are also honored for
the reserved buckets. (Configured regex patterns match regardless of facility, so the
pattern path works with or without this flag.) Leave it unset in production.

#### Enabling Kmsg Test Mode

Set the environment variable in the healthagent systemd unit file
(`/etc/systemd/system/healthagent.service`):

```ini
[Service]
Environment="KMSG_TEST_MODE=True"
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart healthagent
```

#### Injecting Test Messages

See [Kmsg Message Injection](#kmsg-message-injection) for the full and staged test commands, expected reports, and required teardown. The script skips testing unless it confirms `KMSG_TEST_MODE=true` on the running daemon, even though regex matching itself does not require the flag.

For a real kernel-generated event without test mode, see [Ext4 Remount-on-Error](#ext4-remount-on-error). After userspace injection testing, remove the `KMSG_TEST_MODE` setting, reload systemd, and restart healthagent to restore kernel-only severe-level detection.