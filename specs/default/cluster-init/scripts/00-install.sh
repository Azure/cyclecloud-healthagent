#!/bin/bash
set -x
set -e
set -o pipefail

# Cluster-Init (v1) entry script to setup healthagent.
# Handles all CycleCloud jetpack interactions (config + package download), then
# delegates the actual machine setup to setup-healthagent.sh, which is
# jetpack-independent and can also be run standalone on any supported machine.
HEALTHAGENT_VERSION=2.0.0
HEALTHAGENT_DIR="/opt/healthagent"
LOG_FILE="$HEALTHAGENT_DIR/healthagent_install.log"
PACKAGE="healthagent-$HEALTHAGENT_VERSION.tar.gz"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_SCRIPT="$SCRIPT_DIR/setup-healthagent.sh"

mkdir -p "$HEALTHAGENT_DIR"
# Send all output to both stdout and the install log.
exec > >(tee -a "$LOG_FILE") 2>&1

# Check if OS is supported before proceeding
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    VERSION_ID=$VERSION_ID
    if [[ "$OS" != "almalinux" && "$OS" != "ubuntu" && "$OS" != "rhel" ]]; then
        echo "Unsupported operating system: $OS. HealthAgent only supports AlmaLinux, Ubuntu and RHEL. Exiting."
        exit 0
    fi
else
    echo "Cannot detect the operating system. Exiting."
    exit 0
fi

# Check if CycleCloud jetpack is available
if [ ! -f /opt/cycle/jetpack/bin/jetpack ]; then
    echo "CycleCloud jetpack not found at /opt/cycle/jetpack/bin/jetpack. Exiting."
    exit 0
fi

disable_healthagent=$(/opt/cycle/jetpack/bin/jetpack config cyclecloud.healthagent.disable False)
if [ "$disable_healthagent" == "True" ]; then
    echo "Healthagent is disabled"
    exit 0
fi

# If the expected version is already installed, just restart and exit.
if [ -f "$HEALTHAGENT_DIR/.install" ]; then
    # Source the file to load HEALTHAGENT variable
    source "$HEALTHAGENT_DIR/.install"
    if [ "$HEALTHAGENT_INSTALLED_VERSION" == "$HEALTHAGENT_VERSION" ]; then
        echo "HealthAgent version $HEALTHAGENT_INSTALLED_VERSION is already installed. Skipping installation."
        if ! systemctl restart healthagent; then
            echo "Failed to restart healthagent service. Please check the service configuration and logs."
            exit 1
        fi
        echo "HealthAgent service restarted successfully."
        exit 0
    else
        echo "Installed version ($HEALTHAGENT_INSTALLED_VERSION) does not match expected version ($HEALTHAGENT_VERSION). Reinstalling..."
        rm -f "$HEALTHAGENT_DIR/.install"
    fi
fi

# Download the healthagent package via jetpack.
cd "$HEALTHAGENT_DIR"
# Check if the package already exists and delete it if it does
if [ -f "$PACKAGE" ]; then
    echo "Package $PACKAGE already exists. Deleting it..."
    rm -f "$PACKAGE"
fi
echo "Downloading healthagent package: $PACKAGE"
/opt/cycle/jetpack/bin/jetpack download --project healthagent "$PACKAGE"

# Delegate the rest of the (jetpack-independent) setup to the standalone script.
if [ ! -f "$SETUP_SCRIPT" ]; then
    echo "Setup script not found: $SETUP_SCRIPT"
    exit 1
fi
# Logging is already configured here, so tell the setup script not to re-tee.
HEALTHAGENT_LOG_CONFIGURED=1 bash "$SETUP_SCRIPT" "$HEALTHAGENT_DIR/$PACKAGE"

# Record the installed version so subsequent runs can skip reinstalling.
echo "HEALTHAGENT_INSTALLED_VERSION=$HEALTHAGENT_VERSION" > "$HEALTHAGENT_DIR/.install"
echo "HealthAgent installation complete."