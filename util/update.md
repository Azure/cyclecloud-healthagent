# Healthagent Upgrade Process

### Add new project record.

In CC UI, navigate to settings om the top right corner, "Records" and then "Cluster-init Project".

In the CLuster init Project page, click "Create".

In the Name field, add "Healthagent"
Url: "https://github.com/Azure/cyclecloud-healthagent/releases/1.0.4"
Version : "1.0.4"

Click Save.

### Update Template

In the CycleCloud cluster template under ~hpcadmin/ccw1/slurm_template.txt, make the following changes.
_Note_ All of the commands should be run as `hpcadmin`.

0) Confirm that `~hpcadmin/ccw1/slurm_template.txt` is the latest version of the template in use.
1) Make a backup of the slurm template and parameters.
```bash
cp ~/ccw1/slurm_template.txt ~/ccw1/slurm_template_$(date +%s).txt
cp ~/ccw1/slurm_params.json ~/ccw1/slurm_params_$(date +%s).json
```
3) Change `[[[cluster-init cyclecloud/healthagent:*:1.0.3]]]` to `[[[cluster-init cyclecloud/healthagent:*:1.0.4]]]` in `slurm_template.txt`

4) Reimport the cluster
```bash
cyclecloud import_cluster ccw1 -f ~/ccw1/slurm_template.txt -p ~/ccw1/slurm_params.json -c Slurm --force
```

### Pause HealthCheckProgram to preserve Drain nodes

The reason we do this is to make sure temporary unavailability of healthagent does not cause drained nodes to go back into the pool. This should not affect running nodes or running jobs.

```
sudo vim /etc/slurm/slurm.conf
```
Comment out healthcheckprogram,Healthcheckinterval.
```
scontrol reconfigure
```

### Upgrade Healthagent

ssh to the scheduler node.

This script should NOT be run as root!

Run as regular hpcadmin user:


```
curl https://raw.githubusercontent.com/Azure/cyclecloud-healthagent/refs/heads/1.0.4-pre-release/util/update.sh | bash -
```

### Unpause HealthCheckProgram

```
sudo vim /etc/slurm/slurm.conf
```

```
scontrol reconfigure
```