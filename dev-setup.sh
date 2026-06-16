#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VENV_DIR="$REPO_ROOT/.dev_venv"

usage() {
    cat <<EOF
Usage: ./dev-setup.sh [--recreate]

Creates a Python 3.11+ virtual environment at .dev_venv and installs healthagent
in editable mode with its dev dependencies from pyproject.toml.

Options:
    --recreate   Delete and recreate the existing .dev_venv
  -h, --help   Show this help message
EOF
}

RECREATE=false
for arg in "$@"; do
    case "$arg" in
        --recreate)
            RECREATE=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

python_is_supported() {
    "$1" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

python_version() {
    "$1" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PY
}

find_python() {
    local candidates=()

    if [[ -n "${PYTHON:-}" ]]; then
        candidates+=("$PYTHON")
    fi

    candidates+=(python3.20 python3.19 python3.18 python3.17 python3.16 python3.15 python3.14 python3.13 python3.12 python3.11 python3)

    local candidate
    for candidate in "${candidates[@]}"; do
        if command -v "$candidate" >/dev/null 2>&1 && python_is_supported "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done

    return 1
}

PYTHON_BIN=$(find_python) || {
    echo "Could not find Python 3.11 or newer on PATH." >&2
    echo "Install Python 3.11+ and rerun this script, or set PYTHON=/path/to/python." >&2
    exit 1
}

echo "Using Python: $PYTHON_BIN ($(python_version "$PYTHON_BIN"))"

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

if [[ "$RECREATE" == "true" && -d "$VENV_DIR" ]]; then
    echo "Removing existing virtual environment: $VENV_DIR"
    rm -rf "$VENV_DIR"
fi

if [[ -x "$VENV_PYTHON" ]] && ! python_is_supported "$VENV_PYTHON"; then
    echo "Existing virtual environment uses Python $(python_version "$VENV_PYTHON"), which is older than 3.11." >&2
    echo "Rerun with ./dev-setup.sh --recreate to rebuild it with $PYTHON_BIN." >&2
    exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating virtual environment: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
else
    echo "Using existing virtual environment: $VENV_DIR"
fi

echo "Upgrading packaging tools..."
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel

echo "Installing healthagent in editable mode with dev dependencies..."
cd "$REPO_ROOT"
"$VENV_PIP" install -e ".[dev]"

echo "Verifying installation..."
"$VENV_PYTHON" - <<'PY'
import importlib.metadata
import healthagent

print(f"healthagent package: {healthagent.__file__}")
print(f"healthagent version: {importlib.metadata.version('healthagent')}")
PY

cat <<EOF

Development environment is ready.

Activate it with:
    source .dev_venv/bin/activate

Run tests with:
  pytest tests/

Build with:
  python -m build --outdir .build
EOF