#!/bin/bash
set -eo pipefail


# Get the directory where this script lives
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -o root -g root -m 0644 "$SCRIPT_DIR/healthagent-1.0.4.tar.gz" /opt/healthagent/

source /opt/healthagent/.venv/bin/activate
/opt/healthagent/.venv/bin/pip install --force-reinstall /opt/healthagent/healthagent-1.0.4.tar.gz >/dev/null 2>&1
/opt/healthagent/.venv/bin/healthagent-install >/dev/null 2>&1
deactivate
systemctl restart healthagent

# Check if health -s returns 1.0.4 with exponential backoff up to 180 seconds total
MAX_TOTAL_TIME=180
EXPECTED_VERSION="1.0.4"
ELAPSED_TIME=0
SLEEP_INTERVAL=2

while [ $ELAPSED_TIME -lt $MAX_TOTAL_TIME ]; do
    VERSION=$(health -v 2>/dev/null || echo "")

    if [ "$VERSION" == "$EXPECTED_VERSION" ]; then
        echo "Health version $EXPECTED_VERSION confirmed after ${ELAPSED_TIME}s!"
        exit 0
    fi

    # Calculate remaining time
    REMAINING_TIME=$((MAX_TOTAL_TIME - ELAPSED_TIME))

    # Sleep for the minimum of SLEEP_INTERVAL and remaining time
    if [ $SLEEP_INTERVAL -lt $REMAINING_TIME ]; then
        sleep $SLEEP_INTERVAL
        ELAPSED_TIME=$((ELAPSED_TIME + SLEEP_INTERVAL))
        # Double the sleep interval for next iteration (exponential backoff), max 30s
        SLEEP_INTERVAL=$((SLEEP_INTERVAL * 2))
        if [ $SLEEP_INTERVAL -gt 30 ]; then
            SLEEP_INTERVAL=30
        fi
    else
        sleep $REMAINING_TIME
        ELAPSED_TIME=$MAX_TOTAL_TIME
    fi
done

echo "Failed to confirm health version $EXPECTED_VERSION after ${MAX_TOTAL_TIME}s. Current version: $VERSION"
exit 1