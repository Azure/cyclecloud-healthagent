#!/bin/bash
set +x
set -e

# Cluster-Init (v1) script to setup healthagent
VERSION=1.0.1
HEALTHAGENT_DIR="/opt/healthagent"
VENV_DIR="$HEALTHAGENT_DIR/.venv"
LOG_FILE="$HEALTHAGENT_DIR/healthagent_install.log"
SERVICE_FILE="/etc/systemd/system/healthagent.service"
PACKAGE="healthagent-$VERSION.tar.gz"
DCGM_VERSION="3.3.7"


setup_venv() {
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
        yum install -y systemd-devel
        PYTHON_BIN="/usr/bin/python3.12"
    elif [ "$OS" == "ubuntu" ] && [ "$VERSION_ID" == "22.04" ]; then
        echo "Detected Ubuntu 22.04. Installing Python 3.11..."
        apt update
        # We need python dev headers and systemd dev headers for same reaosn mentioned above.
        apt install -y python3.11 python3.11-venv python3.11-dev
        apt install -y libsystemd-dev
        PYTHON_BIN="/usr/bin/python3.11"
    elif [ "$OS" == "ubuntu" ] && [[ $VERSION =~ ^24\.* ]]; then
        apt install -y python3.12 python3.12-venv python3.12-dev
        apt install -y libsystemd-dev
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
    cp $(which health) /usr/bin/
    deactivate
}

setup_environment_variables() {
    echo "Setting environment variables"
    # hacky #TODO: need to really remove this here
    # Check if nv-hostengine exists and retrieve the version
    if command -v nv-hostengine &> /dev/null; then
        DCGM_VERSION=$(nv-hostengine --version | grep -i Version | awk '{print $3}' || echo "")
    else
        DCGM_VERSION=""
    fi
    echo "export DCGM_VERSION=$DCGM_VERSION" >> $VENV_DIR/bin/activate
    echo "export HEALTHAGENT_DIR=$HEALTHAGENT_DIR" >> $VENV_DIR/bin/activate
}


setup_systemd() {
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
    setup_venv
    download_install_healthagent
    setup_environment_variables
    setup_systemd
    systemctl start healthagent
} &> $LOG_FILE