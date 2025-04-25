## Healthagent for CycleCloud


### Setup

Healthagent runs as a cluster-init v1 project.

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

### Troubleshooting

Packaging script produces all its logging in `.build.log`. Make sure to check it for errors.

Packaging script does rely on artifact feeds access. If you get prompted for credentials, this may help:

```
python -m pip install --upgrade pip
pip install keyring artifacts-keyring
```

Note: Healthagent setup does not require any global package installation, but if you have the cyclecloud tools repo set and your pip.conf
pointed to cyclecloud artifacts feed, then the above maybe needed for setting up the venv for healthagent correctly.

After that:
```
cfs-helper "https://msazure.pkgs.visualstudio.com/CycleCloud/_packaging/CycleCloud-Prod/"
```

this should set up the creds correctly.

### Running healthagent

Nothing specifically should be required, installation process already sets up the healhtagent systemd service and starts it.

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

