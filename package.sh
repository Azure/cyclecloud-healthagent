#!/bin/bash

# Ensure the script exits if any command fails
set -e

log_file=".build.log"
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
    # Define the project directory
    PROJECT_DIR=$(dirname "$0")
    # Create a virtual environment
    VENV_DIR="$PROJECT_DIR/.venv"
    # Create a virtual environment if it does not exist
    if [ ! -d "$VENV_DIR" ]; then
        echo "Creating virtual environment at $VENV_DIR..."
        python3 -m venv "$VENV_DIR"
    else
        echo "Virtual environment already exists at $VENV_DIR"
    fi
    # Activate the virtual environment
    source "$VENV_DIR/bin/activate"
    # Ensure setuptools and wheel are installed
    pip install --upgrade setuptools wheel build
    # Build the distribution
    python3 -m build --outdir .build
    # Deactivate the virtual environment
    deactivate
    # Create the blobs directory if it doesn't exist
    BLOBS_DIR="$PROJECT_DIR/blobs"
    mkdir -p "$BLOBS_DIR"
    # Move only the source distribution (.tar.gz) to the blobs directory
    mv .build/*.tar.gz "$BLOBS_DIR/"
}

#check_dirty_changes
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