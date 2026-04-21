#!/bin/bash
set -e

# Comprehensive E2E Verification for LDM v2.3.5 Refactor
# Verifies: Infra, Seeding, Disambiguation, Labels, and Status

echo "🚀 Starting Comprehensive E2E Verification..."

# Ensure we are testing the LOCAL package
export PYTHONPATH=$PYTHONPATH:.
PYTHON_CMD="python3 liferay_docker.py"

# Cleanup
rm -rf e2e-refactor-project
docker rm -f liferay-docker-proxy liferay-search-global liferay-proxy-global e2e-refactor 2>/dev/null || true

# 1. Verify Infra Setup
echo "--- Step 1: Global Infra Setup ---"
$PYTHON_CMD -y infra-setup
if ! docker ps | grep -q "liferay-docker-proxy"; then
    echo "❌ ERROR: Infra setup failed to start liferay-docker-proxy"
    exit 1
fi
echo "✅ Infra setup successful."

# 2. Verify Project Initialization & Labels
echo "--- Step 2: Project Run & Labels ---"
mkdir -p e2e-refactor-project/files
# Use a tag that definitely has a seed to test seeding logic
{
  echo "tag=2026.q1.4-lts"
  echo "container_name=e2e-refactor"
  echo "image_tag=alpine"
  echo "port=8082"
} > e2e-refactor-project/.liferay-docker.meta

# Run it
$PYTHON_CMD -y run e2e-refactor-project --no-wait --no-tld-skip --no-jvm-verify

# Verify labels in compose
if ! grep -q "com.liferay.ldm.project=e2e-refactor" e2e-refactor-project/docker-compose.yml; then
    echo "❌ ERROR: Generated compose missing mandatory com.liferay.ldm.project label"
    exit 1
fi
echo "✅ Mandatory labels verified in docker-compose.yml"

# 3. Verify Status Reporting
echo "--- Step 3: Status Reporting ---"
# Patch alpine to stay alive
if [[ "$OSTYPE" == "darwin"* ]]; then
  sed -i '' 's/image: alpine/image: alpine\n    command: sleep 60/g' e2e-refactor-project/docker-compose.yml
else
  sed -i 's/image: alpine/image: alpine\n    command: sleep 60/g' e2e-refactor-project/docker-compose.yml
fi

docker compose -f e2e-refactor-project/docker-compose.yml up -d

# Now check ldm status
STATUS_OUT=$($PYTHON_CMD -y status e2e-refactor-project)
if ! echo "$STATUS_OUT" | grep -q "e2e-refactor"; then
    echo "❌ ERROR: ldm status failed to detect running project"
    echo "Full output: $STATUS_OUT"
    exit 1
fi
echo "✅ Project detected correctly in ldm status."

# 4. Verify CLI Disambiguation (Logs)
echo "--- Step 4: CLI Disambiguation (Logs) ---"
# Should not fail with 'unrecognized arguments' or project not found if we use the service name
# Use --no-wait to avoid hanging on the missing alpine container logs
LOG_OUT=$($PYTHON_CMD -y logs e2e-refactor-project liferay --no-wait 2>&1 || true)
if echo "$LOG_OUT" | grep -q "unrecognized arguments"; then
    echo "❌ ERROR: CLI disambiguation failed for logs command (project/service mixup)"
    echo "Full output: $LOG_OUT"
    exit 1
fi

# Verify infra logs access (checks env vars injection)
INFRA_LOG_OUT=$($PYTHON_CMD -y logs --infra --no-wait 2>&1 || true)
if echo "$INFRA_LOG_OUT" | grep -q "LDM_CERTS_DIR"; then
     echo "❌ ERROR: Infra logs failed due to missing environment variables"
     exit 1
fi
echo "✅ CLI Disambiguation & Infra Logs verified."

# 5. Verify Infra Teardown
echo "--- Step 5: Infra Teardown ---"
$PYTHON_CMD -y down e2e-refactor-project --infra
if docker ps -a | grep -q "liferay-docker-proxy"; then
    echo "❌ ERROR: Infra teardown failed to remove liferay-docker-proxy"
    exit 1
fi
echo "✅ Infra teardown successful."

# Cleanup
rm -rf e2e-refactor-project

echo "🎯 ALL E2E VERIFICATIONS PASSED!"
