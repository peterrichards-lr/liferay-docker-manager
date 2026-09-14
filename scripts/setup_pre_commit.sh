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
    python3 -m pip install -r requirements-dev.txt
    echo "=> Installing git hooks..."
    python3 -m pre_commit install
fi

# LDM-#1707: ui_colors.py is generated and gitignored, so a fresh clone or
# worktree does not have it. Without it ui.py falls back to empty colour codes,
# and five tests that assert ANSI output fail on invisible characters. Cheap and
# idempotent, so it runs unconditionally.
echo "=> Generating terminal colour module..."
python3 scripts/sync_colors.py

echo "=> Running pre-commit on all files..."
# We use || true so the script doesn't abort if pre-commit finds issues,
# allowing the user to see the output.
python3 -m pre_commit run --all-files || true
