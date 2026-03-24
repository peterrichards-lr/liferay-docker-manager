#!/bin/bash

# Liferay Docker Python Wrapper (macOS/Linux)
# This script ensures a Python virtual environment (venv) is setup and runs the manager

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$SCRIPT_DIR/.venv"
PYTHON_SCRIPT="$SCRIPT_DIR/liferay_docker.py"
PYTHON_EXEC="python3"
CACHE_FILE="$HOME/.liferay_docker_cache.json"

# Check for clear-cache command
if [[ "$1" == "clear-cache" ]]; then
    if [ -f "$CACHE_FILE" ]; then
        rm "$CACHE_FILE"
        echo "✅ Docker tag cache cleared."
    else
        echo "ℹ Cache is already empty."
    fi
    exit 0
fi

# Check if python3 is available
if ! command -v $PYTHON_EXEC &> /dev/null; then
    echo "Error: python3 is not installed."
    exit 1
fi

# Create venv if it doesn't exist
if [ ! -d "$VENV_PATH" ]; then
    echo "Creating virtual environment in $VENV_PATH..."
    $PYTHON_EXEC -m venv "$VENV_PATH" || exit 1
    "$VENV_PATH/bin/pip" install --upgrade pip
    if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
        "$VENV_PATH/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
    fi
fi

# Run the script using the venv's python interpreter
"$VENV_PATH/bin/python" "$PYTHON_SCRIPT" "$@"
