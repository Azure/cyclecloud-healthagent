#!/bin/bash

set -eo pipefail

install_parallel_ssh() {
    local package_name="pssh"

    # Check if parallel-ssh is already installed
    if command -v parallel-ssh >/dev/null 2>&1; then
        return 0
    fi

    echo "parallel-ssh not found. Installing..."

    # Update package list
    echo "Updating package list..."
    if ! sudo apt update >/dev/null 2>&1; then
        echo "Error: Failed to update package list"
        return 1
    fi

    # Install pssh package
    echo "Installing pssh package..."
    if sudo apt install -y "$package_name" >/dev/null 2>&1; then

        # Verify installation
        if command -v parallel-ssh >/dev/null 2>&1; then
            echo "Installation verified: $(parallel-ssh --version 2>/dev/null || echo 'parallel-ssh is ready')"
            return 0
        else
            echo "Warning: Installation completed but parallel-ssh command not found in PATH"
            return 1
        fi
    else
        echo "Error: Failed to install pssh package"
        return 1
    fi
}

user=$(whoami)

# Get cluster name
CLUSTERNAME=$(scontrol show config | grep -i '^ClusterName' | awk -F= '{print $2}' | xargs)
if [ -z "$CLUSTERNAME" ]; then
    echo "ERROR: Could not determine cluster name" >&2
    exit 1
fi

cd ~
workdir="update_healthagent"

if ! install_parallel_ssh; then
    echo "Error: Failed to install parallel-ssh. Exiting script."
    exit 1
fi

# Delete workdir if it exists
if [[ -d ~/$workdir ]]; then
    echo "Removing existing workdir: ~/$workdir"
    rm -rf ~/$workdir
fi

mkdir -p ~/$workdir
cd $workdir

if ! sudo /opt/azurehpc/slurm/venv/bin/azslurm debug_cluster_status --nodes > cluster.json; then
    echo "Could not get cluster status"
    exit 1
fi

echo "Getting list of login nodes"

if ! cat cluster.json | jq -r '.nodes[] | select(.Hostname | contains("login")) | select(.Status == "Ready") | .Hostname' > login_nodes.txt; then
    echo "Failed to get login nodes"
    exit 1
fi

echo "Getting list of Slurm nodes..."
if ! sudo /opt/azurehpc/slurm/venv/bin/azslurm nodes --output-columns name -F table_headerless > avail_hosts.txt; then
    echo "Error: Failed to get list of Slurm nodes"
    exit 1
fi

if [[ ! -s avail_hosts.txt ]]; then
    echo "Error: No hosts found"
    exit 1
fi

echo "Downloading healthagent tarball..."
if ! curl -LO https://github.com/Azure/cyclecloud-healthagent/releases/download/1.0.4/healthagent-1.0.4.tar.gz; then
    echo "Error: Failed to download healthagent tarball"
    exit 1
fi

echo "Downloading update script..."
if ! curl -LO https://raw.githubusercontent.com/Azure/cyclecloud-healthagent/refs/heads/1.0.4-pre-release/util/update_healthagent.sh; then
    echo "Error: Failed to download update script"
    exit 1
fi

chmod +x update_healthagent.sh
echo "Running update on current node $(/bin/hostname)"

sudo $(pwd)/update_healthagent.sh
sudo install -o root -g root -m 0755 /etc/healthagent/health.sh.example /sched/$CLUSTERNAME/health.sh.tmp
sudo mv /sched/$CLUSTERNAME/health.sh.tmp /sched/$CLUSTERNAME/health.sh
echo "Updated SlurmHealthProgram"
echo "Running update on login nodes"
parallel-ssh -h login_nodes.txt -i -x "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10" "sudo $(pwd)/update_healthagent.sh"
echo "Running update on remote hosts..."
parallel-ssh -h avail_hosts.txt -t 300 -i -x "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10" "sudo $(pwd)/update_healthagent.sh"