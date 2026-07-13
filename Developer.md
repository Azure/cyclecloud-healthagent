## Developer Guide

- [Development Setup](#development-setup)
- [Building with package.sh](#building-with-packagesh)
  - [Non-Production (Development) Mode](#non-production-development-mode)
  - [Production Mode](#production-mode)
- [Running Tests](#running-tests)
  - [Unit Tests](#unit-tests)
  - [Integration Tests](#integration-tests)
- [DCGM Bindings for VS Code](#dcgm-bindings-for-vs-code)
- [DCGM Test Mode](#dcgm-test-mode)

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

Requires the **dev environment** to be active:
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

Integration tests live in `integration/` and require a running healthagent instance, root access, and (for GPU tests) NVIDIA GPUs with DCGM.

**Systemd monitor integration test:**

Tests config-driven service registration by creating a dummy systemd service and verifying healthagent detects its failure and recovery.

```bash
# Full automated run (requires root)
sudo python3 integration/test_systemd_monitor.py

# Or step-by-step:
sudo python3 integration/test_systemd_monitor.py --initialize
sudo systemctl restart healthagent
sudo python3 integration/test_systemd_monitor.py --run
sudo python3 integration/test_systemd_monitor.py --teardown
```

**DCGM field injection test:**

Injects DCGM field values to simulate GPU errors. Requires `nvidia-dcgm.service` running and healthagent started with `DCGM_TEST_MODE=true`. [DCGM Test Mode](#dcgm-test-mode) is defined in detail below.

```bash
# Run all injection tests on GPU 0, keep injecting for 2 minutes
python3 integration/test_inject.py --test all --duration 120

# Inject high temperature on GPU 1 for 60 seconds
python3 integration/test_inject.py --gpu 1 --test temp --duration 60

# Clear injected values (inject healthy values)
python3 integration/test_inject.py --test clear
```

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
systemctl daemon-reload
systemctl restart healthagent
```

#### Injecting Errors

With `DCGM_TEST_MODE` enabled and `nvidia-dcgm.service` running, inject values from another terminal using `dcgmi test --inject`.

For example, injecting a XID 63 error on GPU 0:

```bash
dcgmi test --inject --gpuid 0 -f 230 -v 63
# Successfully injected field info.
```

Injected values expire from the DCGM cache quickly. Use the `integration/test_inject.py` script with `--duration` to re-inject continuously:

```bash
# Inject high temperature on GPU 0 for 2 minutes
python3 integration/test_inject.py --gpu 0 --test temp --duration 120

# Clear all injected values (restore healthy state)
python3 integration/test_inject.py --test clear
```

**References:**
- [DCGM Modes of Operation](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/getting-started.html#modes-of-operation)
- [DCGM Error Injection Framework](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-error-injection.html#error-injection-workflow)