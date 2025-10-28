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