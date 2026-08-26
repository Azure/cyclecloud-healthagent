#!/bin/bash
set -euo pipefail

# Standalone HealthAgent bootstrap installer for non-CycleCloud environments.
# Downloads a HealthAgent release tarball from GitHub, which bundles
# setup-healthagent.sh, and delegates the actual machine setup to that script.
#
# Quick start (latest release):
#   curl -fsSL https://raw.githubusercontent.com/Azure/cyclecloud-healthagent/main/install.sh | sudo bash
#
# Pin a specific version:
#   curl -fsSL https://raw.githubusercontent.com/Azure/cyclecloud-healthagent/main/install.sh | sudo bash -s -- --version 2.0.1

REPO="Azure/cyclecloud-healthagent"
# Oldest release that bundles setup-healthagent.sh inside the tarball.
MIN_VERSION="2.0.1"
VERSION="${HEALTHAGENT_VERSION:-}"

usage() {
    cat <<EOF
Usage: install.sh [--version X.Y.Z]

Installs HealthAgent from a GitHub release on this machine (Python venv, DCGM,
and systemd service). Must be run as root.

Options:
  --version X.Y.Z
        Install this specific release ($MIN_VERSION or newer). Defaults to the
        latest release. May also be set via the HEALTHAGENT_VERSION variable.
  -h, --help
        Show this help message and exit.

Environment variables:
  HEALTHAGENT_VERSION   Same as --version.

Examples:
  # Latest release
  curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | sudo bash

  # Specific version
  curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | sudo bash -s -- --version 2.0.1
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --version)
            if [ -z "${2:-}" ]; then
                echo "ERROR: --version requires an argument" >&2
                usage >&2
                exit 2
            fi
            VERSION="$2"
            shift 2
            ;;
        --version=*)
            VERSION="${1#*=}"
            shift
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This installer must be run as root (e.g. via sudo)." >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl is required but not installed." >&2
    exit 1
fi

# Resolve the latest release tag from the GitHub 'latest' redirect if no
# version was requested. This avoids the API (no token / rate limits).
if [ -z "$VERSION" ]; then
    echo "Resolving latest HealthAgent release..."
    effective_url="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest")"
    VERSION="${effective_url##*/tag/}"
    if [ -z "$VERSION" ] || [ "$VERSION" == "$effective_url" ]; then
        echo "ERROR: Could not determine the latest release version." >&2
        exit 1
    fi
fi
echo "Installing HealthAgent version: $VERSION"

# This installer only supports releases that bundle setup-healthagent.sh.
if [ "$(printf '%s\n' "$MIN_VERSION" "$VERSION" | sort -V | head -n1)" != "$MIN_VERSION" ]; then
    echo "ERROR: This installer supports HealthAgent $MIN_VERSION and newer (requested: $VERSION)." >&2
    exit 1
fi

TARBALL_URL="https://github.com/$REPO/releases/download/$VERSION/healthagent-$VERSION.tar.gz"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

TARBALL_PATH="$WORK_DIR/healthagent-$VERSION.tar.gz"

echo "Downloading release tarball: $TARBALL_URL"
if ! curl -fsSL -o "$TARBALL_PATH" "$TARBALL_URL"; then
    echo "ERROR: Failed to download release tarball for version $VERSION." >&2
    echo "       Check that the version exists at https://github.com/$REPO/releases" >&2
    exit 1
fi

# Extract the version-locked setup script bundled in the release tarball.
if ! tar -xzf "$TARBALL_PATH" -C "$WORK_DIR" --strip-components=1 --wildcards \
        '*/install/setup-healthagent.sh' 2>/dev/null; then
    echo "ERROR: Release $VERSION does not bundle install/setup-healthagent.sh." >&2
    exit 1
fi
SETUP_PATH="$WORK_DIR/install/setup-healthagent.sh"

echo "Running HealthAgent setup..."
bash "$SETUP_PATH" "$TARBALL_PATH"
