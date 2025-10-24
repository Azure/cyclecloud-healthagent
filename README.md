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

