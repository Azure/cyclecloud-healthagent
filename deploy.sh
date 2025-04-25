#!/bin/bash
set -x

# Variables
BLOB_STORAGE_PATH="blobs"  # Replace with the actual path to your blob storage
REMOTE="$1"  # Remote user and node in the format user@remote-node
REMOTE_DIR="/opt/healthagent"
VENV_DIR="$REMOTE_DIR/.venv"
PACKAGE=""

# Check if remote user@node is provided
if [ -z "$REMOTE" ]; then
    echo "Usage: $0 <user@remote-node>"
    exit 1
fi

# Find the latest artifact in the blob storage by timestamp
echo "Finding the latest artifact in $BLOB_STORAGE_PATH..."
PACKAGE=$(ls -t "$BLOB_STORAGE_PATH" | head -n 1)

if [ -z "$PACKAGE" ]; then
    echo "Error: No artifacts found in $BLOB_STORAGE_PATH."
    exit 1
fi

PACKAGE_PATH="$BLOB_STORAGE_PATH/$PACKAGE"
echo "Latest artifact found: $PACKAGE_PATH"


# Upload the artifact to the remote node
echo "Uploading $PACKAGE to $REMOTE:/tmp..."
scp -o StrictHostKeyChecking=no "$PACKAGE_PATH" "$REMOTE:/tmp/" || {
    echo "Error: Failed to upload $PACKAGE to $REMOTE."
    exit 1
}

# Stop the healthagent service on the remote node
echo "Stopping healthagent service on $REMOTE..."
ssh -o StrictHostKeyChecking=no "$REMOTE" "sudo systemctl stop healthagent" || {
    echo "Error: Failed to stop healthagent service on $REMOTE."
    exit 1
}

# Install the package on the remote node
echo "Installing $PACKAGE on $REMOTE..."
ssh -o StrictHostKeyChecking=no "$REMOTE" <<EOF
    set -e
    sudo mv /tmp/$PACKAGE $REMOTE_DIR/$PACKAGE
    echo "Activating virtual environment..."
    sudo bash -c "source $VENV_DIR/bin/activate && pip install --force-reinstall $REMOTE_DIR/$PACKAGE"
EOF

if [ $? -eq 0 ]; then
    echo "Deployment successful!"
else
    echo "Deployment failed!"
    exit 1
fi

# Restart the healthagent service on the remote node
echo "Restarting healthagent service on $REMOTE..."
ssh -o StrictHostKeyChecking=no "$REMOTE" "sudo systemctl start healthagent" || {
    echo "Error: Failed to restart healthagent service on $REMOTE."
    exit 1
}

echo "Healthagent service restarted successfully on $REMOTE."