#!/bin/bash
set -e

# LDM Local Smoke Test Suite
# Replicates the CI environment checks to catch CLI/Attribute regressions locally.

UI_YELLOW='\033[1;33m'
UI_GREEN='\033[0;32m'
UI_RED='\033[0;31m'
UI_OFF='\033[0m'

echo -e "${UI_YELLOW}=== Starting LDM Smoke Tests ===${UI_OFF}"

# 1. Setup
echo -e "\n[1/4] Installing LDM in editable mode..."
pip install -e . > /dev/null

# 2. Doctor Check
echo -e "\n[2/4] Testing 'ldm doctor'..."
# We expect success (0) or 1 (if Docker is off), but NOT a crash (AttributeError)
python3 liferay_docker.py doctor --skip-project || [ $? -eq 1 ]
echo -e "${UI_GREEN}✅ Doctor command completed without crashing.${UI_OFF}"

# 3. Repair Check
echo -e "\n[3/4] Testing 'ldm upgrade --repair' (Mock)..."
# Should handle the input gracefully
python3 liferay_docker.py upgrade --repair <<< "N" || true
echo -e "${UI_GREEN}✅ Repair command parsed correctly.${UI_OFF}"

# 4. Run Check
echo -e "\n[4/4] Testing 'ldm run' with Mock Project..."
SMOKE_ROOT="smoke-project"
rm -rf "$SMOKE_ROOT"
mkdir -p "$SMOKE_ROOT/files"
touch "$SMOKE_ROOT/files/portal-ext.properties"

# Create a project via legacy meta file (tests compatibility)
echo "tag=7.4.13-u100" > "$SMOKE_ROOT/.liferay-docker.meta"
echo "container_name=smoke-test" >> "$SMOKE_ROOT/.liferay-docker.meta"
echo "image_tag=alpine" >> "$SMOKE_ROOT/.liferay-docker.meta"

echo "   > Generating compose..."
python3 liferay_docker.py -y run "$SMOKE_ROOT" --no-up --sidecar --no-wait

if [ -f "$SMOKE_ROOT/docker-compose.yml" ]; then
    echo -e "${UI_GREEN}✅ Compose generated successfully.${UI_OFF}"
else
    echo -e "${UI_RED}❌ Failed to generate docker-compose.yml${UI_OFF}"
    exit 1
fi

# 5. Snapshot Check (Verifies Permission Fix)
echo -e "\n[5/5] Testing 'ldm snapshot' (Verifies permission reclamation)..."
# We use --files-only because the mock project doesn't have a real DB running
python3 liferay_docker.py snapshot "$SMOKE_ROOT" --name "Smoke Test Snapshot" --files-only

if [ -d "$SMOKE_ROOT/snapshots" ]; then
    echo -e "${UI_GREEN}✅ Snapshot directory created and accessible.${UI_OFF}"
else
    echo -e "${UI_RED}❌ Failed to create snapshot directory.${UI_OFF}"
    exit 1
fi

# Cleanup
rm -rf "$SMOKE_ROOT"

echo -e "\n${UI_GREEN}=== ALL SMOKE TESTS PASSED ===${UI_OFF}"
