## Healthagent for HPC/AI workloads

Healthagent is currently in heavy development and many features are still WIP. New features and updates to existing features will be added here.

### Core Concepts

Healthagent's goal is to provide node health checks for HPC/AI workloads running on datacenter class GPU's.

Healthagent classifies tests into 2 categories:

1. *Active Health checks*: These are checks that are performed when a user application or a job is not running on the node. These tests are intrusive and attempt to fully use the Nodes. These must be explicitly invoked.

2. *Background Health checks*: These are checks that are safe to be run alongside workloads and provide background monitoring. They are designed to be lightweight and minimally disruptive to user jobs. These are always running.


These concepts generally translate well to almost every scheduler/workload orchestrator. Healthagent by-default only runs background health checks.
When a scheduler (for example slurm)-- deems a node ready for active health checks -- the active health checks can be explicitly performed.

Healthagent provides a simple CLI command for invocation as well as for checking status.

```
root@ccw4-gpu-2:~# health --help
usage: health [-h] [-e | -p | -s | -v] [-b]

Healthagent Client

options:
  -h, --help     show this help message and exit
  -e, --epilog   Run the epilog/post-job validation healthchecks
  -p, --prolog   Run the prolog/pre-job validation healthchecks
  -s, --status   Get the current health status of the node
  -v, --version  Return Healthagent version
  -b, --bash     Export results into bash friendly variables
```


### Background Health checks

Healthagent does the following background health checks.

| Check Name | Description | Frequency |
|------------|-------------|-----------|
| Systemd checks | Monitor critical systemd services for failures or error. Services being started/stopped using regular systemctl operations are not counted as failures. In that sense this is not a liveness check. But an explicit failure check. Uses dbus under the hood to capture failure events | Asynchronous |
| Kernel Message checks | Monitor KMSG for severity level critical or above | Asynchronous |
| GPU Checks | Equivalent of running `dcgmi health -c`. Runs on all discovered GPU's. All health watches are set. | Every 60 seconds |
| GPU Policy Violations | Sets up GPU policy violations. Policy violations are set for NVLINK errors, all XID errors, DBE/SBE memory errors. Thresholds for these are hardcoded in DCGM. | Asynchronous |
| GPU Field tracking | We explicitly track DCGM certain fields for errors such as clock throttling, temperature,persistence mode and fabric manager errors | Every 60 seconds |


References:
- [DCGM Policy](https://docs.nvidia.com/datacenter/dcgm/2.4/user-guide/feature-overview.html#policy)
- [DCGM health](https://docs.nvidia.com/datacenter/dcgm/2.4/user-guide/feature-overview.html#background-health-checks)

#### Fetching results of background health checks

`health -s` command returns the result of background health checks. Since background healthchecks are always running-- this command does not actually run any checks but returns the last ran status of the checks.

In job schedulers like slurm -- background health checks can easily be integrated with `SlurmHealthCheckProgram` which can call `health -s` to down/drain nodes. This also allows `health -s` to always return quickly since it does not launch any background operations. An example script that can be used with healthagent is provided and ships with healthagent: [Slurm Health Check Example](healthagent/etc/health.sh.example). Eventually some example setups like for k8's may also be provided in the future.


Example status output of background health checks:

```
root@ccw4-gpu-2:~# health -s | jq
{
  "gpu": {
    "BackgroundGPUHealthChecks": {
      "status": "Error",
      "message": "BackgroundGPUHealthChecks reports errors",
      "description": "BackgroundGPUHealthChecks report Error count=4 subsystem=System",
      "details": "Persistence Mode not set for GPU: 0, Restart nvidia-persistenced or Reboot the system.\nPersistence Mode not set for GPU: 1, Restart nvidia-persistenced or Reboot the system.\nPersistence Mode not set for GPU: 2, Restart nvidia-persistenced or Reboot the system.\nPersistence Mode not set for GPU: 3, Restart nvidia-persistenced or Reboot the system.",
      "last_update": "2025-08-07T19:43:50 UTC",
      "categories": [
        "System"
      ],
      "error_count": 4
    }
  },
  "systemd": {
    "slurmd.service": {
      "status": "OK",
      "last_update": "2025-08-07T00:00:01 UTC"
    }
  },
  "kmsg": {}
}
```

### Active Health Checks

Active health checks can be classified into:

1) pre-workload validation check or "prolog" check-- Ideal for asserting system readiness for the current workload
2) post workload stress test or "epilog" check -- Ideal for stress testing a system (esp for power/thermal stress tests).

At this point in development, the only active check being performed is the epilog (post job verification check). This is equivalent of `dcgmi diag -r 2`. In the future there will be other tests added to this suite of tests.

Active health checks must be explicitly run -- and therefore the call to run them is a blocking call.

`health -e` is used to run epilog checks.

```
root@ccw4-gpu-2:~# health -e
{
    "gpu": {
        "ActiveGPUHealthChecks": {
            "status": "OK",
            "last_update": "2025-08-07T19:58:38 UTC"
        }
    },
    "systemd": {},
    "kmsg": {}
}
```


While any active health checking is being performed, background checks are still always run in the background.

#### DCGM Test mode

DCGM supports 2 modes of operation described [here](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/getting-started.html#modes-of-operation) - Standalone mode and Embedded mode.

*Healthagent by default loads DCGM in `embedded` mode which implies that we load the DCGM library directly as a shared library*. This is to avoid reliance on nvidia-dcgm service (nv-hostengine) in production environments, since if the service crashes then it disables GPU tests.

However DCGM has a robust error injection framework that can be used to inject values into specific field ID's. This is described in detail [here](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-error-injection.html#error-injection-workflow). This error injection framework only works in Standalone mode since injected values are stored in nv-hostnengine's cache.

Its valuable to test healthagent behaviour by simulating DCGM errors. To do this healthagent supports `DCGM_TEST_MODE` which attempts to connect to DCGM via nv-hostengine (nvidia-dcgm.service). To do this, set the environment variable in healthagent systemd file in `/etc/systemd/system/healthagent.service` as shown under the `service` section:

```bash
[service]
Environment="DCGM_TEST_MODE=True"
```

And then restart healthagent:
```bash
systemctl daemon-reload
systemctl restart healthagent
```

Then in another terminal, inject values, For example injecting a XID 63 error:

```bash
root@ccw-1-3-gpu-9:~# dcgmi test --inject --gpuid 0 -f 230 -v 63
Successfully injected field info.
```

And then `health -s` should show the injected values:

```
"gpu": {
        "BackgroundGPUHealthChecks": {
            "status": "OK",
            "last_update": "2025-10-24T20:09:42 UTC"
        },
        "GPUPolicyChecks": {
            "status": "Error",
            "message": "GPUPolicyChecks reports errors",
            "description": "GPU Policy Violations detected",
            "details": "XID errors found: XID {63} on GPU 0",
            "last_update": "2025-10-24T20:10:02 UTC",
            "XID Violation": {
                "0": {
                    "xid_error": [
                        63
                    ],
                    "details": "XID errors found: XID {63} on GPU 0"
                }
            }
        }
    }
```

[DCGM Modes](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/getting-started.html#modes-of-operation)

[DCGM Test Injection Framework](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-error-injection.html#error-injection-workflow)

### System Software Monitoring

Healthagent monitors critical systemd services using D-Bus to detect explicit service failures in real-time. This monitoring is *not* a liveness check - normal service start/stop operations are ignored. Only explicit failures trigger health status changes.

**Monitored Services:**

The following services are monitored by default:

| Category | Services | Condition |
|----------|----------|-----------|
| **Slurm Services** | `munge.service`, `slurmd.service`, `slurmctld.service`, `slurmdbd.service`, `slurmrestd.service` | Always monitored |
| **NVIDIA GPU Services** | `nvidia-imex.service`, `nvidia-dcgm.service`, `nvidia-persistenced.service` | When GPUs are present |

Services are added to monitoring at startup. If a service is not loaded on the node, it is automatically ignored. When a previously-ignored service is later loaded by systemd, monitoring is automatically enabled through the D-Bus `UnitNew` event handler.

**Failure Detection:**

The systemd monitor tracks state transitions asynchronously via D-Bus property change signals. It specifically watches for:
- `active` → `failed` transition: Indicates service crash or explicit failure
- `inactive` → `failed` transition: Indicates service failed during activation

Transient states like `activating` and `deactivating` are ignored since they don't represent actual failures.

**Response to Failures:**

When a service enters the `failed` state, healthagent:
1. Changes the service health status from `OK` to `ERROR`
2. Captures the last 10 lines of journal logs for the failed service
3. Updates the health report with:
   - Status: `Error`
   - Description: `<service-name> Service unhealthy`
   - Details: Recent journal entries showing the failure context
4. Reports the failure to CycleCloud (if enabled)

Example failure response:
```json
{
  "systemd": {
    "slurmd.service": {
      "status": "Error",
      "message": "slurmd.service reports errors",
      "description": "slurmd.service Service unhealthy",
      "details": "[2025-08-07 19:43:50] slurmd: error: Node configuration differs...\n[2025-08-07 19:43:50] slurmd: fatal: Unable to register...",
      "last_update": "2025-08-07T19:43:50 UTC"
    }
  }
}
```

**Recovery Detection:**

When a previously failed service transitions from `failed` → `active` (with substate `running`), healthagent:
1. Changes the service health status from `ERROR` back to `OK`
2. Clears the error details
3. Updates the health report timestamp
4. Reports the recovery to CycleCloud (if enabled)

This automatic recovery detection ensures that nodes return to healthy status once services are successfully restarted, without requiring manual intervention.

### Kernel Message Monitoring

Healthagent continuously monitors kernel messages from `/dev/kmsg` to detect critical system-level issues that may indicate hardware failures, driver problems, or severe system conditions. This monitoring runs asynchronously and captures severe kernel events as they occur in real-time.

**Monitored Severity Levels:**

The kernel message monitor only triggers alerts for the most critical kernel log levels:

| Level | Kernel Constant | Description |
|-------|-----------------|-------------|
| 0 | `KERN_EMERG` | Emergency - System is unusable |
| 1 | `KERN_ALERT` | Alert - Action must be taken immediately |
| 2 | `KERN_CRIT` | Critical - Critical conditions detected |

**Monitoring Behavior:**

- **Asynchronous Capture**: Uses non-blocking I/O with an event loop to read `/dev/kmsg` continuously without impacting system performance
- **Time-Based Filtering**: Only reports messages from the last hour, ignoring older kernel messages
- **Auto-Clearing**: Automatically clears error reports every 5 minutes if no new critical messages have been received in the past hour
- **Message Accumulation**: Collects all critical messages during a monitoring period and includes them in a single detailed report

**Response to Critical Kernel Messages:**

When critical kernel messages (levels 0-2) are detected, healthagent:
1. Sets the health status to `WARNING`
2. Creates a detailed report containing:
   - Status: `Warning`
   - Message: `KernelMonitor Detected Alerts`
   - Description: `Kernel Log Monitor reports Critical/Emergency Alerts`
   - Details: Formatted list of all detected kernel messages with timestamps
3. Reports the issue to CycleCloud (if enabled)

Example kernel message alert:
```json
{
  "kmsg": {
    "KernelMonitor": {
      "status": "Warning",
      "message": "KernelMonitor Detected Alerts",
      "description": "Kernel Log Monitor reports Critical/Emergency Alerts",
      "details": "2025-08-07T19:43:50 UTC - KERNEL CRITICAL - Hardware error detected\n2025-08-07T19:43:51 UTC - KERNEL ALERT - Memory corruption detected",
      "last_update": "2025-08-07T19:43:51 UTC"
    }
  }
}
```

### Network Interface Monitoring

Healthagent monitors physical network interfaces to detect link failures, flapping, and operational state issues. This monitoring focuses on physical hardware interfaces (Ethernet and InfiniBand) and excludes virtual interfaces to avoid false alerts from expected virtual network behavior.

**Monitored Interface Types:**

The network monitor tracks the following physical interface types:

| Interface Type | Description |
|----------------|-------------|
| Ethernet | Standard Ethernet network adapters |
| InfiniBand | High-performance InfiniBand HPC fabrics |

Virtual interfaces (such as bridges, VLAN interfaces, and container networks) are automatically excluded from monitoring.

**Monitoring Behavior:**

- **Periodic Checks**: Runs every 60 seconds to sample network interface state
- **Sliding Window Analysis**: Tracks link down events over a 60-sample window to calculate link flap rates
- **Physical Interfaces Only**: Automatically filters out virtual interfaces by checking sysfs device paths
- **Auto-Clearing**: Automatically clears error reports when interfaces return to healthy operational state

**Alert Conditions:**

The network monitor triggers alerts based on two conditions:

1. **Link Flapping (WARNING)**: When an interface goes down 1 or more times per hour
   - Status: `Warning`
   - Indicates unstable network connectivity that may impact workloads
   - Includes `link_down_rate_per_hour` metric in custom fields

2. **Interface Not Operational (ERROR)**: When an interface is not in the `up` operational state
   - Status: `Error`
   - Indicates a critical network failure or misconfiguration
   - Reports the current operational state (down, lowerlayerdown, etc.)

**Response to Network Issues:**

When network problems are detected, healthagent:
1. Sets appropriate health status (`WARNING` for flapping, `ERROR` for down interfaces)
2. Creates a detailed report with:
   - List of affected interfaces
   - Specific error conditions for each interface
   - Link flap rates and state transition counts
   - Current carrier and operational states
3. Includes per-interface custom fields:
   - `link_down_rate_per_hour`: Recent link failure rate
   - `link_flap_since_uptime`: Total link state transitions since boot
   - `error_count`: Number of errors for the interface
   - `carrier`: Physical link status
4. Reports the issue to CycleCloud (if enabled)

Example network interface alert:
```json
{
  "network": {
    "Network": {
      "status": "Error",
      "message": "Network reports errors",
      "description": "Network interfaces eth0,ib0 are not operational",
      "details": "Network interface eth0 is not operational and in state down.\nNetwork interface ib0 went down 3 times in the last hour",
      "last_update": "2025-08-07T19:43:50 UTC",
      "eth0": {
        "link_down_rate_per_hour": 0,
        "link_flap_since_uptime": 2,
        "error_count": 1,
        "carrier": "0"
      },
      "ib0": {
        "link_down_rate_per_hour": 3,
        "link_flap_since_uptime": 15,
        "error_count": 0
      }
    }
  }
}
```

### CycleCloud Integration

Healthagent reports status of the nodes to CycleCloud via `jetpack`. This can be explicitly disabled by setting `PUBLISH_CC` to `False`. Default value is `True`. Additionally if healthagent is unable to find jetpack binary, then CC reporting is automatically disabled regardless of the value of the environment variable. The environment variable can be configured in healthagent systemd file.

```bash
[Service]
Environment="PUBLISH_CC=False"
```

### WIP

- configurability of health checks
- prolog tests
- OS based checks.
### Setup

To build:

```
./package.sh
```

This script should produce blobs in the blobs directory.

Since healthagent runs as a cluster init project, you can upload the blobs to your storage locker:

```
cyclecloud project upload <locker>
```

And later in your cluster setup, point "AdditionalClusterInit" to healthagent through the UI or through the template.

### Running healthagent

Nothing specifically should be required, installation process already sets up the healthagent systemd service and starts it.

Here are some runtime details:

- Actual on-the node installation for healthagent lives in `specs/default/cluster-init/00-install.sh`
- Healthagent installation directory on a node running healthagent is `/opt/healthagent`
- Installation logs for healthagent go to: `/opt/healthagent/healthagent_install.log`
- Healthagent service logs live in `/opt/healthagent/healthagent.log`

TODO:
configuration file based initialization, CLI , and checkpointing.


#### Developer Setup

Healthagent relies on DCGM bindings.

Vscode integration is essential to a smooth dev process. #TODO: A script that can set up the bindings so the imports load up in vscode will be added in the future.

Right now vscode settings file adds import paths. A manual step needed would be to grab the bindings from a running node, and place it in the `"${workspaceFolder}/.bindings/dcgm-3.3.7/"` directory.

