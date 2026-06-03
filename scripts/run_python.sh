#!/bin/bash
# Portable Python resolver for pre-commit hooks.
# Prefers .venv/bin/python3 if present (local dev), otherwise falls back to
# the system python3 (CI environments install packages globally).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python3"

if [ -x "$VENV_PYTHON" ]; then
    exec "$VENV_PYTHON" "$@"
else
    exec python3 "$@"
fi
