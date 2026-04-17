#!/bin/bash
set -e

# LDM Local Smoke Test Wrapper
# Sets up a clean virtual environment and executes the smoke test suite.

UI_YELLOW='\033[1;33m'
UI_GREEN='\033[0;32m'
UI_OFF='\033[0m'

VENV_DIR=".smoke_venv"

echo -e "${UI_YELLOW}=== Setting up LDM Smoke Test Environment ===${UI_OFF}"

# 1. Create/Refresh Virtual Environment
if [ -d "$VENV_DIR" ]; then
    echo "ℹ  Refreshing existing virtual environment..."
else
    echo "ℹ  Creating virtual environment in $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

# 2. Activate and Install
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "ℹ  Upgrading pip and installing dependencies..."
pip install --upgrade pip > /dev/null
pip install -r requirements.txt > /dev/null
pip install -e . > /dev/null

# 3. Run the Smoke Tests
# Note: We use the venv's python and pip
./scripts/run_smoke_tests.sh

echo -e "\n${UI_GREEN}✅ Smoke test wrapper completed.${UI_OFF}"
echo -e "To deactivate the environment, run: ${UI_YELLOW}deactivate${UI_OFF}"
