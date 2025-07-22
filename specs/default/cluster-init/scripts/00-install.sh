#!/bin/bash
set -x
set -e

# Cluster-Init (v1) script to setup healthagent
HEALTHAGENT_VERSION=1.0.3
HEALTHAGENT_DIR="/opt/healthagent"
VENV_DIR="$HEALTHAGENT_DIR/.venv"
LOG_FILE="$HEALTHAGENT_DIR/healthagent_install.log"
SERVICE_FILE="/etc/systemd/system/healthagent.service"
PACKAGE="healthagent-$HEALTHAGENT_VERSION.tar.gz"
DCGM_VERSION="4.2.3"


setup_venv() {
    set -x

    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VERSION_ID=$VERSION_ID
    else
        echo "Cannot detect the operating system."
        exit 1
    fi

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
        # We need python dev headers and systemd dev headers for same reaosn mentioned above.
        apt install -y python3.11 python3.11-venv python3.11-dev
        apt install -y pkg-config gcc libsystemd-dev
        PYTHON_BIN="/usr/bin/python3.11"
    elif [ "$OS" == "ubuntu" ] && [[ $VERSION =~ ^24\.* ]]; then
        echo "Detected Ubuntu 24. Installing Python 3.12..."
        apt update
        apt install -y python3.12 python3.12-venv python3.12-dev
        apt install -y pkg-config gcc libsystemd-dev
        PYTHON_BIN="/usr/bin/python3.12"
    else
        echo "Unsupported operating system: $OS $VERSION_ID"
        # dont exit 0
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

download_install_healthagent() {

    set -x

    cd $HEALTHAGENT_DIR
    # Check if the package already exists and delete it if it does
    if [ -f "$PACKAGE" ]; then
        echo "Package $PACKAGE already exists. Deleting it..."
        rm -f "$PACKAGE"
    fi

    echo "Downloading healthagent package: $PACKAGE"
    jetpack download --project healthagent $PACKAGE
    echo "Installing healthagent package..."
    source $VENV_DIR/bin/activate
    pip install --force-reinstall $PACKAGE
    # Copy the "health" script to /usr/bin
    healthagent-install
    deactivate
}
setup_dcgm() {
    set -x

    echo "Setting up DCGM (Datacenter GPU Manager)..."

    # Detect the operating system
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VERSION_ID=$VERSION_ID
    else
        echo "Cannot detect the operating system."
        exit 1
    fi

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
        if [ "$OS" == "almalinux" ]; then
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

mkdir -p $HEALTHAGENT_DIR
# Redirect all stdout and stderr to the logfile
{
    if [ -f "$HEALTHAGENT_DIR/.install" ]; then
        # Source the file to load HEALTHAGENT variable
        source "$HEALTHAGENT_DIR/.install"
        if [ "$HEALTHAGENT_INSTALLED_VERSION" == "$HEALTHAGENT_VERSION" ]; then
            echo "HealthAgent version $HEALTHAGENT_INSTALLED_VERSION is already installed. Skipping installation."
            systemctl restart healthagent
            if [ $? -ne 0 ]; then
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


    setup_venv
    download_install_healthagent
    # Check if gpu's exist
    if nvidia-smi -L > /dev/null 2>&1; then
        echo "NVIDIA GPU is present and nvidia-smi is working"
        setup_dcgm
    fi
    setup_systemd
    echo "HEALTHAGENT_INSTALLED_VERSION=$HEALTHAGENT_VERSION" > $HEALTHAGENT_DIR/.install
    systemctl start healthagent
    if [ $? -ne 0 ]; then
        echo "Failed to start healthagent service. Please check the service configuration and logs."
        exit 1
    fi
} 2>&1 | tee "$LOG_FILE"