#!/bin/bash
set -eo pipefail

# Get the directory where this script lives
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -o root -g root -m 0644 "$SCRIPT_DIR/healthagent-1.0.4.tar.gz" /opt/healthagent/

systemctl stop healthagent
source /opt/healthagent/.venv/bin/activate
/opt/healthagent/.venv/bin/pip install --force-reinstall /opt/healthagent/healthagent-1.0.4.tar.gz >/dev/null 2>&1
/opt/healthagent/.venv/bin/healthagent-install >/dev/null 2>&1
deactivate
systemctl restart healthagent
sleep 120

# Check if health -s returns 1.0.4 up to 10 times
MAX_ATTEMPTS=10
SLEEP_INTERVAL=2
EXPECTED_VERSION="1.0.4"

for i in $(seq 1 $MAX_ATTEMPTS); do
    VERSION=$(health -s 2>/dev/null || echo "")

    if [ "$VERSION" == "$EXPECTED_VERSION" ]; then
        echo "Health version $EXPECTED_VERSION confirmed!"
        exit 0
    fi

    if [ $i -lt $MAX_ATTEMPTS ]; then
        sleep $SLEEP_INTERVAL
    fi
done

echo "Failed to confirm health version $EXPECTED_VERSION after $MAX_ATTEMPTS attempts. Current version: $VERSION"
exit 1