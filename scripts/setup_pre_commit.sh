#!/usr/bin/env bash
set -e

# Change to the root of the repository
cd "$(dirname "$0")/.."

echo "=> Checking virtual environment..."
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
# shellcheck disable=SC1091
source .venv/bin/activate

# Check if pre-commit is installed
if ! command -v pre-commit &> /dev/null; then
    echo "=> pre-commit not found. Installing from requirements-dev.txt..."
    pip install -r requirements-dev.txt
    echo "=> Installing git hooks..."
    pre-commit install
fi

echo "=> Running pre-commit on all files..."
# We use || true so the script doesn't abort if pre-commit finds issues,
# allowing the user to see the output.
pre-commit run --all-files || true
