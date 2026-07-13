# Healthagent

Healthagent is a node health checking service for datacenter-class Azure HPC VMs. Deployed on every node, it provides continuous background health monitoring and on-demand active health checks.

---

## Table of Contents

- [Core Concepts](#core-concepts)
- [Installation](#installation)
  - [Requirements](#requirements)
- [Configuration](#configuration)
  - [Config File Location](#config-file-location)
  - [Default Configuration](#default-configuration)
  - [Evaluation Operators](#evaluation-operators)
  - [Override Mechanism](#override-mechanism)
- [CLI Reference](#cli-reference)
  - [health -s (Status)](#health--s-status)
  - [health -e (Epilog)](#health--e-epilog)
  - [health -p (Prolog)](#health--p-prolog)
  - [health -l (List Checks)](#health--l-list-checks)
  - [health -c (Run Specific Checks)](#health--c-run-specific-checks)
  - [health -C (Show Config)](#health--c-show-config)
  - [health -b (Bash Output)](#health--b-bash-output)
  - [health -v (Version)](#health--v-version)
- [Modules](#modules)
  - [GPU Module](#gpu-module)
  - [Systemd Module](#systemd-module)
  - [Network Module](#network-module)
  - [Kernel Message Module](#kernel-message-module)
  - [Process Module](#process-module)
- [Scheduler Integration](#scheduler-integration)
- [CycleCloud Integration](#cyclecloud-integration)
- [Environment Variables](#environment-variables)
- [Developer Guide](#developer-guide)

---

## Core Concepts

Healthagent is a lightweight systemd service that provides continuous node health checking for HPC/AI workloads running on datacenter-class GPUs.

Healthagent classifies health checks into two categories:

1. **Active Health Checks** — Checks performed when a user application or job is *not* running on the node. These tests are intrusive and attempt to fully utilize the node. They must be explicitly invoked via the `health` CLI described below.

    Active health checks are further classified into:
    - **Prolog** (pre-workload validation) — Ideal for asserting system readiness before the current workload.
    - **Epilog** (post-workload stress test) — Ideal for stress testing a system (e.g., power/thermal stress tests).

    Active health checks are blocking calls. While active checks are being performed, background checks continue running.

2. **Background Health Checks** — Checks that are safe to run alongside workloads. They are designed to be lightweight and minimally disruptive to user jobs. These are always running.

    - `health -s` returns the result of background health checks. Since background checks are always running, this command does not launch any checks — it returns the last known status.
    - In job schedulers like Slurm, background health checks can be integrated with `HealthCheckProgram` which calls `health -s` to down/drain nodes. This allows `health -s` to always return quickly since it does not launch any background operations. An example script ships with healthagent: [Slurm Health Check Example](healthagent/etc/health.sh.example).

These concepts translate well to almost every scheduler/workload orchestrator. Healthagent by default only runs background health checks. When a scheduler (for example Slurm) deems a node ready for active health checks, the active checks can be explicitly performed via scheduler integration or invoked manually.

## Installation

Healthagent is deployed as a CycleCloud default cluster-init project. Installation is handled automatically by the [cluster-init](specs/default/cluster-init/scripts/00-install.sh) script during node provisioning.

### Requirements

**Supported Operating Systems:** Ubuntu (22.04, 24.04), AlmaLinux, RHEL

**Minimum Python Version**: >=Python3.11

(Only if GPUs are present)
**DCGM Version**: >= 4.2.3

Healthagent installation script does attempt to install a suitable python version (from upstream repos) and a DCGM version. But if nvidia-repos are not set and/or if upstream access is blocked-- then healthagent setup script will not succeed.

**Runtime paths:**
| Path | Purpose |
|------|---------|
| `/opt/healthagent/` | Installation directory |
| `/opt/healthagent/.venv/` | Python virtual environment |
| `/opt/healthagent/healthagent.log` | Service log file |
| `/opt/healthagent/healthagent_install.log` | Installation log |
| `/opt/healthagent/run/` | Runtime state (sockets, XID history) |
| `/etc/healthagent/` | Configuration directory |
| `/etc/systemd/system/healthagent.service` | Systemd unit file |
| `/usr/bin/health` | CLI client binary |

**Disabling Healthagent:**

Set the CycleCloud jetpack config to disable healthagent at install time:

```
cyclecloud.healthagent.disable = True
```

**Running Healthagent**

Healthagent is a systemd service. Cluster-init script installs and launches the service. It can be managed by the operator using regular systemd rules.
```bash
# view the status of healthagent systemd service
systemctl status healthagent

# Restart healthagent
systemctl restart healthagent
```
---

## Configuration

#### Config File Location

Healthagent uses a layered configuration system:

1. **Package defaults** — bundled `defaults.yaml` (shipped with the package)
2. **User overrides** — `/etc/healthagent/config.yaml` (operator-managed)

The effective config is the deep merge of these two layers. User overrides take precedence. Setting a key to `null` in the override file removes that key from the effective config.

#### Default Configuration

The full default configuration is available in [defaults.yaml](healthagent/defaults.yaml). On a running node, use `health -C` to view the effective merged configuration. A reference copy is also installed to `/etc/healthagent/defaults.yaml` during installation.

**Config validation:** The configuration is validated at startup using Pydantic schemas. Invalid configuration values will cause healthagent to fail at startup with a clear validation error.

#### Evaluation Operators

Threshold checks in the configuration (GPU field watches, network checks) use an `eval` field to specify the comparison operator. The following operators are available:

| Operator | Description |
|----------|-------------|
| `gt` | Greater than |
| `lt` | Less than |
| `ge` | Greater than or equal |
| `le` | Less than or equal |
| `eq` | Equal to |
| `ne` | Not equal to |
| `in` | Value is in the threshold list |
| `bitmask` | Bitwise AND with threshold is non-zero |
| `window_gt` | Delta within a sliding time window exceeds threshold (see below) |

**`window_gt` operator:**

Unlike simple comparisons, `window_gt` tracks the *change* in a counter value over a sliding time window. It compares the delta (newest value minus the oldest value within the window) against the threshold. This is useful for detecting flapping or accumulating errors without alerting on absolute counter values. For alerting on absolute counter values use `gt`, `lt` or other basic operators.

Required fields when using `window_gt`:
- `window`: Time window in seconds over which to measure the delta (e.g., `10800` for 3 hours)
- `error` or `warning`: The delta threshold that triggers an alert

The check will not fire until enough samples have been collected to cover the full window duration.

**`strikes` parameter:**

The `strikes` field controls how many times a check is allowed to recover (transition from error back to OK) before the node is permanently degraded. This prevents flapping interfaces from being repeatedly marked healthy.

- `strikes: 0` (default) — Unlimited recovery. The check can return to OK any number of times.
- `strikes: 1` — The first error is permanent. Once triggered, the check stays in error even if the underlying value recovers. A node reboot or healthagent restart is required to clear it.
- `strikes: N` — After N OK→ERROR transitions, the check is permanently locked in error state.

`strikes` is useful when we have to treat a counter reaching a window_gt threshold as an "event". So the threshold `strikes` implements is equivalent to asking "How many times over the lifetime of operation has a node exceeded the `window_gt` threshold". This is particularly useful for link flapping events described in the network section below. A link flapping an absolute number of times during the lifetime of operation may not be a health issue since links can recover on their own and the flapping may happen during maintenance and/or could be distributed sparsely over the time window. But if the link flaps 3+ times within any 3-hour window, that counts as one strike. With `strikes: 1` set in the config, a single occurrence permanently degrades the node — meeting Azure's RMA criteria.

---

#### Override Mechanism

To override default config, place the overrides in `/etc/healthagent/config.yaml`. The final config is created by a deep merge viewable by `health -C` (when healthagent is running). While you can modify the `/etc/healthagent/defaults.yaml` directly-- your changes might get lost on re-install as this file is shipped with the package. After modifying config, healthagent needs to be restarted.

```yaml
# /etc/healthagent/config.yaml
# Only include keys you want to override.
# Keys set to null are removed from the effective config.

# Ignore the network module. Only load the modules listed.
modules:
  - gpu
  - systemd
  - kmsg
  - proc

gpu:
  xid:
    warning: [43, 63]    # Override the warning XID list
    ignore: [13]         # Ignore XID 13

network:
  infiniband:
    link_downed:
      error: 5           # Change link_downed threshold from 3 to 5. This is merged with the strikes field and window_gt evaluator.
                         # Making 5 link downs over 3 hrs as the new threshold.
    operstate:
      eval: ne
      error: up         # Promote IPoIB down from warning to error
      warning: null     # Don't treat operstate down as a warning.
      msg: "IPoIB not in state up"
```

Deep merge rules:
- **Dicts**: recursively merged
- **Lists/scalars**: override replaces the default value entirely
- **null**: removes the key from the result

---

## CLI Reference

The `health` CLI communicates with the running healthagent daemon over a Unix socket at `/opt/healthagent/run/health.sock`.

```
health [-h] [-e | -p | -s | -v | -l [TYPE] | -C] [-c NAME [key=value ...]] [-b]
```

#### health -s (Status)

Returns the current background health status. This is a **non-blocking** call. This is the default command when no flags are provided. This does not run any checks — it returns the last known status.

```bash
health -s
health        # equivalent, -s is the default
```

#### health -e (Epilog)

Runs post-job active health checks. This is a **blocking** call that may take several minutes.

```bash
health -e
```

#### health -p (Prolog)

Runs pre-job active health checks. This is a **blocking** call.

```bash
health -p
```

#### health -l (List Checks)

Lists all currently registered health checks with their module, category, interval, accepted arguments, and description. If any healthcheck or module is disabled, it is omitted from this output.

```bash
health -l            # List all checks
health -l epilog     # List only epilog checks
health -l prolog     # List only prolog checks
```

Example output:

```
root@ccw-gpu-16:~# health -l
Module   Check                  Category            Interval  Args                   Description
-------  ---------------------  ------------------  --------  ---------------------  --------------------------------------------------------------
gpu      GpuCountCheck          background          60s                              Check OS vs PCI vs NVML GPU count
gpu      GpuMemoryCheck         epilog, prolog      -1        gpu_id                 Run GPU memory allocation test. Args: gpu_id=0,1
gpu      GpuDiagnosticCheck     epilog, prolog      -1        gpu_id, tests, params  Run DCGM diagnostics checks. Eg. Args: gpu_id=0,1 tests=memory
gpu      GpuHealthCheck         background          60s                              Periodic GPU health monitoring
systemd  SystemdServiceCheck    background          async                            Track systemd service health
kmsg     KernelLogCheck         background          async                            Monitor kernel log for critical messages
network  NetworkInterfaceCheck  background          60s                              Monitor network interface health
proc     ProcessStateCheck      epilog, background  60s                              Detect zombie and unkillable processes
```

#### health -c (Run Specific Checks)

Run specific epilog/prolog checks by name. Must be combined with `-e` or `-p`. Supports passing key=value arguments to checks. Repeatable.

```bash
# Run only GpuMemoryCheck during epilog
health -e -c GpuMemoryCheck

# Run GpuMemoryCheck on specific GPUs
health -e -c GpuMemoryCheck gpu_id=0,1

# Run multiple specific checks
health -e -c GpuMemoryCheck gpu_id=0,1 -c GpuDiagnosticCheck

# Run diagnostics with custom test level
health -e -c GpuDiagnosticCheck tests=memory gpu_id=0,1
```

Check names are case-insensitive.

#### health -C (Show Config)

Displays the effective merged configuration as loaded by the running daemon.

```bash
health -C
```

#### health -b (Bash Output)

Exports results in a bash-friendly format (module,error_count per line). Useful for scripting.

```bash
health -s -b
# Output example:
# gpu,2
# systemd,0
# network,0
```

#### health -v (Version)

Returns the healthagent version.

```bash
health -v
```

---

## Modules

Healthagent is organized into modules. Each module is responsible for a domain of health checks. Modules can be enabled/disabled via the `modules` list in the config.

### GPU Module

Monitors NVIDIA GPUs using DCGM (Data Center GPU Manager). Requires GPUs to be present on the node.

MIG mode currently is not supported.

| Check | Type | Description | Frequency | Args |
|-------|------|-------------|-----------|------|
| `GpuHealthCheck` | Background | Periodic GPU health monitoring via DCGM health watches and field evaluation | 60s | — |
| `GpuCountCheck` | Background | Verifies OS, PCI, and NVML GPU counts match | 60s | — |
| `GpuMemoryCheck` | Active (epilog/prolog) | Allocates ~95% GPU memory per GPU to verify memory health | On demand | `gpu_id` |
| `GpuDiagnosticCheck` | Active (epilog/prolog) | Runs DCGM diagnostic tests (short/medium/long) | On demand | `gpu_id`, `tests`, `params` |

When `gpu_id` is specified (e.g., `gpu_id=0,1`), checks run only on the listed GPUs. If omitted, all GPUs on the node are tested. This is useful for scheduler integration where only job-allocated GPUs should be validated (e.g., `gpu_id=$SLURM_JOB_GPUS`) such as jobs that share a GPU node.

**XID Classification:**

XIDs (GPU error codes) are classified into three categories:
- **error** (default): All XIDs not in warning or ignore lists are treated as errors
- **warning**: XIDs downgraded to warning severity (default: 43, 63, 13, 31, 66, 94, 154)
- **ignore**: XIDs that are silently discarded

If the `error` list is explicitly populated, only those XIDs are treated as errors — all others become warnings.

**XID Persistence:**

XID history is persisted to `/opt/healthagent/run/xid_history.json` across healthagent restarts. XIDs are only discarded if they are older than the last boot time.

**Field Watches:**

GPU metrics are continuously tracked and evaluated against configurable thresholds. Each field watch specifies an evaluation type, threshold levels, and a human-readable message template. See the [default configuration](#default-configuration) for the full list of monitored fields.

Field watches allow users to implement any threshold to take nodes out of rotation (by draining them) if their workload is sensitive to a specific GPU degradation.
Healthagent already implements default field watches specified in [defaults.yaml](healthagent/defaults.yaml).

```
DCGM_FI_DEV_GPU_TEMP:
  eval: gt
  warning: 93
  category: Thermal
  msg: "GPU {gpu} temperature {value}°C exceeds {threshold}°C"
```

This tells healthagent to warn on a node when temperature exceeds 93. Units are the same as implemented by DCGM. This check can easily be bumped to error by override:

```
DCGM_FI_DEV_GPU_TEMP:
  eval: gt
  warning: 93
  error: 95
  category: Thermal
  msg: "GPU {gpu} temperature {value}°C exceeds {threshold}°C"
```

Similarly field watches can be implemented for any other DCGM field. For example, if workload is sensitive to PCIE Replays and nodes need to be drained for it, this can be expressed in the config file as follows:

```
DCGM_FI_DEV_PCIE_REPLAY_COUNTER:
  eval: gt
  warning: 100
  error: 1000
  category: PCIe
  msg: "GPU {gpu} PCIe replay rate {value:.0f}/min exceeds {threshold}/min"
```

Configuration overrides have been explained in the [Override Mechanism](#override-mechanism) section.

`health -s` output for GPU health checks

```
root@ccw-gpu-16:~# health -s | jq '.gpu'
{
  "GpuCountCheck": {
    "status": "OK",
    "last_update": "2026-07-13T21:40:08 UTC"
  },
  "GpuHealthCheck": {
    "status": "OK",
    "last_update": "2026-07-13T21:40:20 UTC",
    "error_count": 0,
    "warning_count": 0,
    "category": []
  },
  "GpuMemoryCheck": {
    "status": "OK",
    "description": "Memory allocation test passed",
    "last_update": "2026-07-13T19:32:27 UTC"
  },
  "GpuDiagnosticCheck": {
    "status": "OK",
    "last_update": "2026-07-13T19:33:15 UTC"
  }
}
```
### Systemd Module

Monitors critical systemd services via D-Bus for explicit failures. Not a liveness check — only detects transitions to the `failed` state.

| Check | Type | Description | Frequency |
|-------|------|-------------|-----------|
| `SystemdServiceCheck` | Background | Track systemd service health via D-Bus signals | Async (event-driven) |

**Default monitored services:**
- `munge.service`, `slurmd.service`, `slurmctld.service`, `slurmdbd.service`, `slurmrestd.service` (always)
- `nvidia-imex.service`, `nvidia-dcgm.service`, `nvidia-persistenced.service` (when GPUs are present — configured under `gpu.services`)

Services not loaded on the node are automatically ignored. If a previously-ignored service is later loaded by systemd, monitoring begins automatically.

**Failure detection:** Watches `active → failed` and `inactive → failed` transitions.

**Recovery detection:** Watches `failed → active (running)` transitions. Nodes return to healthy status automatically when services recover.

### Network Module

Monitors physical network interfaces (Ethernet and InfiniBand) for link failures. Virtual interfaces are excluded.

| Check | Type | Description | Frequency |
|-------|------|-------------|-----------|
| `NetworkInterfaceCheck` | Background | Monitor network interface health | 60s |

**Evaluation features:**

- **Windowed analysis (`window_gt`)**: Tracks counter deltas over a configurable time window. Used for detecting link flapping (e.g., `link_downed` increasing by 3+ within 3 hours).
- **Strikes-based degradation**: The `strikes` parameter controls how many OK→ERROR transitions are allowed before the node is permanently degraded. With `strikes: 1` (default for `link_downed`), the first error occurrence permanently marks the interface as failed — subsequent recoveries are not acknowledged. Set `strikes: 0` to allow unlimited recovery.

**InfiniBand monitoring:** Reads port-level attributes from sysfs including link state, physical state, rate, link_downed counter, and link_error_recovery counter.

```
root@ccw-gpu-16:~# health -s | jq '.network'
{
  "NetworkInterfaceCheck": {
    "status": "OK",
    "last_update": "2026-07-13T21:41:20 UTC",
    "eth0": {
      "operstate": "up"
    },
    "ib2": {
      "ib_device": {
        "mlx5_ib2": {
          "1": {
            "state": "4: ACTIVE",
            "phys_state": "5: LinkUp",
            "rate": "400 Gb/sec (4X NDR)"
          }
        }
      },
      "operstate": "up"
    },
    "ib0": {
      "ib_device": {
        "mlx5_ib0": {
          "1": {
            "state": "4: ACTIVE",
            "phys_state": "5: LinkUp",
            "rate": "400 Gb/sec (4X NDR)"
          }
        }
      },
      "operstate": "up"
    },
    "eth1": {
      "operstate": "up"
    },
    "ib3": {
      "ib_device": {
        "mlx5_ib3": {
          "1": {
            "state": "4: ACTIVE",
            "phys_state": "5: LinkUp",
            "rate": "400 Gb/sec (4X NDR)"
          }
        }
      },
      "operstate": "up"
    },
    "ib1": {
      "ib_device": {
        "mlx5_ib1": {
          "1": {
            "state": "4: ACTIVE",
            "phys_state": "5: LinkUp",
            "rate": "400 Gb/sec (4X NDR)"
          }
        }
      },
      "operstate": "up"
    }
  }
}
```

### Kernel Message Module

Monitors `/dev/kmsg` for critical kernel messages (severity levels 0–2: EMERG, ALERT, CRIT).

| Check | Type | Description | Frequency |
|-------|------|-------------|-----------|
| `KernelLogCheck` | Background | Monitor kernel log for critical messages | Async (event-driven) |

- Messages older than 1 hour are ignored
- Errors auto-clear every 5 minutes if no new critical messages have occurred in the past hour
- Reports at WARNING severity

### Process Module

Monitors `/proc` for zombie processes and unkillable hung tasks.

| Check | Type | Description | Frequency |
|-------|------|-------------|-----------|
| `ProcessStateCheck` | Background / Active (epilog) | Detect zombie and unkillable processes | 60s |

**Alert thresholds (configurable):**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `zombie_per_core` | 50 | Zombies per core before WARNING |
| `pid_max_warn_pct` | 10 | WARNING at this % of pid_max consumed by zombies |
| `pid_saturation_pct` | 50 | ERROR at this % of pid_max consumed by zombies |

**Hung task detection:** Processes in D-state (uninterruptible sleep) with pending SIGKILL/SIGTERM at the process level are detected as unkillable — reported as ERROR with a recommendation to reboot.

---

## Scheduler Integration

Healthagent ships with example scripts for Slurm integration:

| Script | Purpose | Location |
|--------|---------|----------|
| [health.sh.example](healthagent/etc/health.sh.example) | `HealthCheckProgram` — drains nodes on error, resumes on recovery | `/etc/healthagent/` |
| [epilog.sh.example](healthagent/etc/epilog.sh.example) | Slurm Epilog — runs epilog checks on job GPUs | `/etc/healthagent/` |
| [prolog.sh.example](healthagent/etc/prolog.sh.example) | Slurm Prolog — runs prolog checks on job GPUs | `/etc/healthagent/` |

**Epilog/Prolog example usage with Slurm:**

```bash
# In epilog.sh — runs only on GPUs allocated to the job
response=$(/usr/bin/health -e -c gpumemorycheck gpu_id=$SLURM_JOB_GPUS -c gpudiagnosticcheck gpu_id=$SLURM_JOB_GPUS)
```

**HealthCheckProgram example:** Uses `health -b` for bash-friendly output to determine whether to drain or resume a node.

---

## CycleCloud Integration

Healthagent reports node health status to CycleCloud via `jetpack`. This is enabled by default.

- If `jetpack` is not found on the node, CycleCloud reporting is automatically disabled.
- The `details` field in health reports is sent to CycleCloud for UI display but excluded from CLI output by default.

---

## Environment Variables

Configure these in the healthagent systemd unit file (`/etc/systemd/system/healthagent.service`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PUBLISH_CC` | `True` | Enable/disable CycleCloud health reporting |
| `DCGM_TEST_MODE` | `False` | Connect to DCGM via nv-hostengine (standalone mode) for error injection testing |
| `HEALTHAGENT_DIR` | `/opt/healthagent` | Base directory for healthagent runtime files |

Example:
```ini
[Service]
Environment="PUBLISH_CC=False"
Environment="DCGM_TEST_MODE=True"
```

After modifying, reload and restart:
```bash
systemctl daemon-reload
systemctl restart healthagent
```

---

## Developer Guide

For development setup, building, testing, and DCGM test mode instructions, see the [Developer Guide](Developer.md).