#!/bin/bash

# Ensure the script exits if any command fails
set -e

log_file=".build.log"
MODE="NON-PRODUCTION"
PROD=false
PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$PROJECT_DIR"

usage() {
  cat <<EOF
Usage: ./package.sh [--prod|-p]

Build healthagent package artifacts.

Options:
  -p, --prod   Build in PRODUCTION mode using a fresh .prod_venv and a clean git tree
  -h, --help   Show this help message
EOF
}

for arg in "$@"; do
  case "$arg" in
    -p|--prod)
      PROD=true
      MODE="PRODUCTION"
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
  local candidates=(python3.20 python3.19 python3.18 python3.17 python3.16 python3.15 python3.14 python3.13 python3.12 python3.11 python3)
  local candidate

  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_supported "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done

  return 1
}

check_dirty_changes() {
  if [ -n "$(git status --porcelain)" ]; then
    echo "Error: There are uncommitted changes in the current branch. Please commit or stash them before running this script."
    exit 1
  fi
}

print_branch_and_last_commit() {
  branch=$(git rev-parse --abbrev-ref HEAD)
  last_commit=$(git log -1 --pretty=format:"%h - %s (%ci)")
  commit_author=$(git log -1 --pretty=format:"%an")

  printf "%-20s: %s\n" "Current branch" "$branch"
  printf "%-20s: %s\n" "Last commit" "$last_commit"
  printf "%-20s: %s\n" "Commit author" "$commit_author"
}

delete_existing_blobs() {
  printf "%-20s: %s\n" "Deleting existing Blob Files" ""
  while IFS= read -r file; do
    if [ -f "blobs/$file" ]; then
      printf "%-20s: %s\n" "" "$file"
      rm -f "blobs/$file"
    fi
  done < <(awk -F' *= *' '/^\[blobs\]/ {found=1} found && /^Files/ {gsub(/, */, "\n", $2); print $2; exit}' project.ini)
}

check_blobs_files_exist() {
  local version="$1"
  local missing_files=0

  printf "%-20s: %s\n" "Blob Files" ""
  while IFS= read -r file; do
    printf "%-20s: %s\n" "" "$file"
    if [ ! -f "blobs/$file" ]; then
      echo "Error: File blobs/$file does not exist."
      missing_files=1
    fi
  done < <(awk -F' *= *' '/^\[blobs\]/ {found=1} found && /^Files/ {gsub(/, */, "\n", $2); print $2; exit}' project.ini)

  if [ $missing_files -eq 1 ]; then
    echo "One or more required files are missing in the blobs directory."
    exit 1

  fi
}

get_version_from_project_ini() {
  version=$(awk -F' *= *' '/^\[project\]/ {found=1} found && /^version/ {print $2; exit}' project.ini)
  printf "%-20s: %s\n" "Project Version" "$version"
  check_blobs_files_exist "$version"
}


build() {
  delete_existing_blobs

  PYTHON_BIN=$(find_python) || {
    echo "Error: Could not find Python 3.11 or newer on PATH." >&2
    exit 1
  }
  echo "Using Python: $PYTHON_BIN ($(python_version "$PYTHON_BIN"))"

  if [ "$PROD" = true ]; then
    echo "Using Production environment"
    # Production builds must start from a clean virtual environment.
    VENV_DIR="$PROJECT_DIR/.prod_venv"
    if [ -d "$VENV_DIR" ]; then
      echo "Deleting existing production virtual environment at $VENV_DIR..."
      rm -rf "$VENV_DIR"
    fi
  else
    echo "Using Development environment"
    # Non-production builds reuse the existing development virtual environment.
    VENV_DIR="$PROJECT_DIR/.dev_venv"
  fi

  if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  else
    echo "Virtual environment already exists at $VENV_DIR"
  fi

  if ! "$VENV_DIR/bin/python" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
  then
    echo "Error: Virtual environment at $VENV_DIR uses Python older than 3.11." >&2
    exit 1
  fi

  # Ensure setuptools and wheel are installed
  "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel build
  # Clean build output to avoid moving stale artifacts into blobs.
  rm -rf "$PROJECT_DIR/.build"
  # Build the distribution
  "$VENV_DIR/bin/python" -m build --outdir "$PROJECT_DIR/.build"
  # Create the blobs directory if it doesn't exist
  BLOBS_DIR="$PROJECT_DIR/blobs"
  mkdir -p "$BLOBS_DIR"
  # Move only the source distribution (.tar.gz) to the blobs directory
  mv .build/*.tar.gz "$BLOBS_DIR/"
}

printf "%-20s: %s\n" "Mode" "$MODE"

if [ "$PROD" = true ]; then
  rm -rf "$PROJECT_DIR/.prod_venv"
  check_dirty_changes
fi

set +e
build &> "$log_file"
build_status=$?
set -e

if [ $build_status -ne 0 ]; then
  echo "Error: Build failed. Check the log file ($log_file) for details."
  exit 1
else
    rm -rf *.egg-info
fi

print_branch_and_last_commit
get_version_from_project_ini