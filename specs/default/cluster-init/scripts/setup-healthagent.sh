#!/bin/bash
set -x
set -e
set -o pipefail

# Standalone HealthAgent setup script.
# Performs all machine setup that does NOT require CycleCloud jetpack, so it can
# be run independently on any supported machine.
# Usage: setup-healthagent.sh <path-to-healthagent-X.Y.Z.tar.gz>

PACKAGE="$1"
HEALTHAGENT_DIR="/opt/healthagent"
VENV_DIR="$HEALTHAGENT_DIR/.venv"
LOG_FILE="$HEALTHAGENT_DIR/healthagent_install.log"
SERVICE_FILE="/etc/systemd/system/healthagent.service"
DCGM_VERSION="4.2.3"

if [ -z "$PACKAGE" ]; then
    echo "Usage: $0 <path-to-healthagent-package.tar.gz>"
    exit 1
fi

if [ ! -f "$PACKAGE" ]; then
    echo "HealthAgent package not found: $PACKAGE"
    exit 1
fi
# Resolve to an absolute path so it keeps working after we cd around.
PACKAGE="$(readlink -f "$PACKAGE")"

mkdir -p "$HEALTHAGENT_DIR"

# Send all output to both stdout and the install log, unless the caller has
# already configured logging (e.g. when invoked from 00-install.sh).
if [ -z "${HEALTHAGENT_LOG_CONFIGURED:-}" ]; then
    exec > >(tee -a "$LOG_FILE") 2>&1
fi

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

setup_venv() {
    set -x

    if [ "$OS" == "almalinux" ]; then
        echo "Detected AlmaLinux. Installing Python 3.12..."
        # healthagent takes a dependency on systemd and needs python bindings for systemd.
        # "python3-systemd" is the official package for this which is installed through yum/apt.
        # but only system python can access it. To use this package inside
        # a virtual env we need to do a pip install of the package inside the venv, which requires development headers
        # for python and systemd to be present, since the package is actually built during the install.
        yum install -y python3.12 python3.12-devel
        yum install -y pkg-config gcc systemd-devel
        PYTHON_BIN="/usr/bin/python3.12"
    elif [ "$OS" == "ubuntu" ] && [ "$VERSION_ID" == "22.04" ]; then
        echo "Detected Ubuntu 22.04. Installing Python 3.11..."
        apt update
        # We need python dev headers and systemd dev headers for same reason mentioned above.
        apt install -y python3.11 python3.11-venv python3.11-dev
        apt install -y pkg-config gcc libsystemd-dev
        PYTHON_BIN="/usr/bin/python3.11"
    elif [ "$OS" == "ubuntu" ] && [[ "$VERSION_ID" =~ ^24\.* ]]; then
        echo "Detected Ubuntu 24. Installing Python 3.12..."
        apt update
        apt install -y python3.12 python3.12-venv python3.12-dev
        apt install -y pkg-config gcc libsystemd-dev
        PYTHON_BIN="/usr/bin/python3.12"
    elif [ "$OS" == "rhel" ]; then
        echo "Detected RHEL, using system python3..."
        yum install -y python3-devel pkg-config gcc systemd-devel
        PYTHON_BIN="/usr/bin/python3"
    else
        echo "Unsupported operating system: $OS $VERSION_ID"
        exit 0
    fi
    # Create the virtual environment
    echo "Creating virtual environment at $VENV_DIR..."
    # Check if the virtual environment already exists and delete it if it does
    if [ -d "$VENV_DIR" ]; then
        echo "Virtual environment already exists at $VENV_DIR. Deleting it..."
        rm -rf "$VENV_DIR"
    fi
    $PYTHON_BIN -m venv $VENV_DIR
}

install_healthagent() {

    set -x
    cd "$HEALTHAGENT_DIR"
    echo "Installing healthagent package: $PACKAGE"
    source $VENV_DIR/bin/activate
    if ! pip install --force-reinstall "$PACKAGE"; then
        echo "ERROR: Failed to install $PACKAGE"
        deactivate || true
        exit 1
    fi
    # Copy the "health" script to /usr/bin
    if ! healthagent-install; then
        echo "ERROR: Failed to run healthagent-install"
        deactivate || true
        exit 1
    fi

    deactivate
}

setup_dcgm() {
    set -x

    echo "Setting up DCGM (Datacenter GPU Manager)..."

    # Define the minimum required version
    REQUIRED_VERSION="4.2.3"

    # Check if dcgmi is installed and get the installed version
    if command -v dcgmi &> /dev/null; then
        INSTALLED_VERSION=$(dcgmi --version | grep -i "version" | awk '{print $3}')
        echo "Installed DCGM version: $INSTALLED_VERSION"
    else
        echo "DCGM is not installed."
        INSTALLED_VERSION=""
    fi

    # Compare the installed version with the required version
    if [ -z "$INSTALLED_VERSION" ] || [ "$(printf '%s\n' "$REQUIRED_VERSION" "$INSTALLED_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
        echo "DCGM version is older than $REQUIRED_VERSION or not installed. Installing the latest package..."
        if [ "$OS" == "almalinux" ] || [ "$OS" == "rhel" ]; then
            yum install --allowerasing -y datacenter-gpu-manager-4-core
            yum install --allowerasing -y datacenter-gpu-manager-4-cuda12
        elif [ "$OS" == "ubuntu" ]; then
            apt update
            apt install -y datacenter-gpu-manager-4-core
            apt install -y datacenter-gpu-manager-4-cuda12
        else
            echo "Unsupported operating system: $OS $VERSION_ID"
            exit 1
        fi
    else
        echo "DCGM is up-to-date."
    fi

    # Set environment variables
    echo "Setting environment variables..."
    if command -v nv-hostengine &> /dev/null; then
        DCGM_VERSION=$(nv-hostengine --version | grep -i Version | awk '{print $3}' || echo "")
    else
        DCGM_VERSION=""
    fi

    systemctl daemon-reload
    systemctl restart nvidia-dcgm
    echo "export DCGM_VERSION=$DCGM_VERSION" >> $VENV_DIR/bin/activate
    echo "export HEALTHAGENT_DIR=$HEALTHAGENT_DIR" >> $VENV_DIR/bin/activate
}

setup_systemd() {
    set -x

    # Create the systemd service file
    echo "Creating systemd service file at $SERVICE_FILE..."
    cat <<EOL > $SERVICE_FILE
[Unit]
Description=HealthAgent Service
After=network.target

[Service]
Environment="DCGM_VERSION=$DCGM_VERSION"
Environment="HEALTHAGENT_DIR=$HEALTHAGENT_DIR"
WorkingDirectory=$HEALTHAGENT_DIR
ExecStart=$VENV_DIR/bin/python3 $VENV_DIR/bin/healthagent
Restart=always
User=root
Group=root
WatchdogSec=600s

[Install]
WantedBy=multi-user.target
EOL

    # Reload systemd, enable and start the service
    echo "Reloading systemd, enabling and starting healthagent service..."
    systemctl daemon-reload
    systemctl enable healthagent.service
    #systemctl start healthagent.service

    echo "HealthAgent service setup complete."
}

setup_venv
install_healthagent
# Check if gpu's exist
if [ -e "/dev/nvidia0" ]; then
    echo "NVIDIA GPU is present"
    setup_dcgm
    source $VENV_DIR/bin/activate
    if ! pip install cuda-bindings; then
        echo "WARNING: Failed to install cuda-bindings"
    fi
    deactivate
fi
setup_systemd
if ! systemctl restart healthagent; then
    echo "Failed to restart healthagent service. Please check the service configuration and logs."
    exit 1
fi
echo "HealthAgent setup complete."
