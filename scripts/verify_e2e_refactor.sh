#!/bin/bash
set -e

# Comprehensive E2E Verification for LDM v2.3.5 Refactor
# Verifies: Infra, Seeding, Disambiguation, Labels, and Status

echo "🚀 Starting Comprehensive E2E Verification..."

# Ensure we are testing the LOCAL package
export PYTHONPATH=$PYTHONPATH:.
PYTHON_CMD="python3 liferay_docker.py"

# Cleanup
rm -rf test-e2e-refactor-project
docker rm -f liferay-docker-proxy liferay-search-global liferay-proxy-global test-e2e-refactor 2>/dev/null || true

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
mkdir -p test-e2e-refactor-project/files
# Use a tag that definitely has a seed to test seeding logic
{
  echo "tag=2026.q1.4-lts"
  echo "container_name=test-e2e-refactor"
  echo "image_tag=alpine"
  echo "port=8082"
} > test-e2e-refactor-project/.liferay-docker.meta

# Run it
$PYTHON_CMD -y run test-e2e-refactor-project --no-wait --no-tld-skip --no-jvm-verify

# Verify labels in compose
if ! grep -q "com.liferay.ldm.project=test-e2e-refactor" test-e2e-refactor-project/docker-compose.yml; then
    echo "❌ ERROR: Generated compose missing mandatory com.liferay.ldm.project label"
    exit 1
fi
echo "✅ Mandatory labels verified in docker-compose.yml"

# 3. Verify Status Reporting
echo "--- Step 3: Status Reporting ---"
# Patch alpine to stay alive
if [[ "$OSTYPE" == "darwin"* ]]; then
  sed -i '' 's/image: alpine/image: alpine\n    command: sleep 60/g' test-e2e-refactor-project/docker-compose.yml
else
  sed -i 's/image: alpine/image: alpine\n    command: sleep 60/g' test-e2e-refactor-project/docker-compose.yml
fi

docker compose -f test-e2e-refactor-project/docker-compose.yml up -d

# Now check ldm status
STATUS_OUT=$($PYTHON_CMD -y status test-e2e-refactor-project)
if ! echo "$STATUS_OUT" | grep -q "test-e2e-refactor"; then
    echo "❌ ERROR: ldm status failed to detect running project"
    echo "Full output: $STATUS_OUT"
    exit 1
fi
echo "✅ Project detected correctly in ldm status."

# 4. Verify CLI Disambiguation (Logs)
echo "--- Step 4: CLI Disambiguation (Logs) ---"
# Test: Logs with explicit project and service
LOG_OUT=$($PYTHON_CMD -y logs test-e2e-refactor-project liferay --no-wait 2>&1 || true)
if echo "$LOG_OUT" | grep -q "unrecognized arguments"; then
    echo "❌ ERROR: CLI parser rejected --no-wait flag"
    exit 1
fi

# Test: Service-only logs (Disambiguation Heuristic)
# This should correctly identify 'liferay' as a service even if it exists as a folder
LOG_OUT_SERVICE=$($PYTHON_CMD -y logs liferay --no-wait 2>&1 || true)
if echo "$LOG_OUT_SERVICE" | grep -q "Project 'liferay' not found"; then
    echo "❌ ERROR: CLI disambiguation failed (identified service as missing project)"
    exit 1
fi

# Verify infra logs access (checks env vars injection)
INFRA_LOG_OUT=$($PYTHON_CMD -y logs --infra --no-wait 2>&1 || true)
if echo "$INFRA_LOG_OUT" | grep -q "LDM_CERTS_DIR"; then
     echo "❌ ERROR: Infra logs failed due to missing environment variables"
     exit 1
fi
echo "✅ CLI Disambiguation & Infra Logs verified."

# 5. Verify Instance Isolation (IP-based port binding)
echo "--- Step 5: Instance Isolation Verification ---"
rm -rf test-isolation-a test-isolation-b
docker rm -f test-isolation-a test-isolation-b 2>/dev/null || true

create_isolation_project() {
    local dir=$1
    local name=$2
    local host=$3
    local port=$4
    mkdir -p "$dir/files"
    {
      echo "tag=2026.q1.4-lts"
      echo "container_name=$name"
      echo "host_name=$host"
      echo "image_tag=alpine"
      echo "port=$port"
      echo "db_type=hypersonic"
      echo "ssl=false"
    } > "$dir/.liferay-docker.meta"
}

patch_isolation_compose() {
    local dir=$1
    if [[ "$OSTYPE" == "darwin"* ]]; then
      sed -i '' 's/image: "alpine"/image: "alpine"\n    command: sh -c "sleep 3600"/g' "$dir/docker-compose.yml"
    else
      sed -i 's/image: "alpine"/image: "alpine"\n    command: sh -c "sleep 3600"/g' "$dir/docker-compose.yml"
    fi
}

create_isolation_project "test-isolation-a" "test-isolation-a" "127.0.0.1" "8082"
$PYTHON_CMD -y run test-isolation-a --no-wait --no-tld-skip --no-jvm-verify
patch_isolation_compose "test-isolation-a"
docker compose -f test-isolation-a/docker-compose.yml up -d
sleep 2

# Attempt conflict
create_isolation_project "test-isolation-b" "test-isolation-b" "127.0.0.1" "8082"
if $PYTHON_CMD -y run test-isolation-b --no-wait --no-tld-skip --no-jvm-verify 2>&1 | grep -q "already in use"; then
    echo "✅ Success: LDM correctly detected port conflict on 127.0.0.1:8082"
else
    echo "❌ ERROR: LDM failed to detect port conflict on 127.0.0.1:8082"
    exit 1
fi

# Attempt isolation
create_isolation_project "test-isolation-b" "test-isolation-b" "127.0.0.2" "8082"
if ! ping -c 1 -t 1 127.0.0.2 >/dev/null 2>&1; then
    echo "⚠️  WARNING: 127.0.0.2 not routable. Verifying generated config only."
    $PYTHON_CMD -y run test-isolation-b --no-up --no-tld-skip --no-jvm-verify
    if grep -q "127.0.0.2:8082:8080" test-isolation-b/docker-compose.yml; then
        echo "✅ Success: Generated compose correctly uses 127.0.0.2:8082"
    else
        echo "❌ ERROR: Generated compose missing IP-prefixed port binding"
        exit 1
    fi
else
    $PYTHON_CMD -y run test-isolation-b --no-wait --no-tld-skip --no-jvm-verify
    patch_isolation_compose "test-isolation-b"
    if docker compose -f test-isolation-b/docker-compose.yml up -d; then
        echo "✅ Success: Both projects running side-by-side on port 8082 via different IPs."
    else
        echo "❌ ERROR: Docker failed to bind second instance even with different IP."
        exit 1
    fi
fi

# Cleanup isolation
docker rm -f test-isolation-a test-isolation-b 2>/dev/null || true
rm -rf test-isolation-a test-isolation-b

# 6. Verify Proxy-Only Routing for SSL Custom Domains
echo "--- Step 6: Proxy-Only SSL Routing Verification ---"
rm -rf test-ssl-proxy
mkdir -p test-ssl-proxy/files
{
  echo "tag=2026.q1.4-lts"
  echo "container_name=test-ssl-proxy"
  echo "host_name=my-custom-domain.com"
  echo "ssl=true"
  echo "port=8085"
  echo "db_type=hypersonic"
} > test-ssl-proxy/.liferay-docker.meta

# Run in no-up mode to check generated config. 
$PYTHON_CMD -y run test-ssl-proxy --no-up --no-tld-skip --no-jvm-verify

if grep -q "8085:8080" test-ssl-proxy/docker-compose.yml; then
    echo "❌ ERROR: SSL custom domain exposed port 8085 to host. Should be proxy-only."
    grep "8085:8080" test-ssl-proxy/docker-compose.yml
    exit 1
fi
echo "✅ Success: SSL custom domain has no direct host port mapping."
rm -rf test-ssl-proxy

# 7. Verify Infra Teardown
echo "--- Step 7: Infra Teardown ---"
$PYTHON_CMD -y down test-e2e-refactor-project --infra
if docker ps -a | grep -q "liferay-docker-proxy"; then
    echo "❌ ERROR: Infra teardown failed to remove liferay-docker-proxy"
    exit 1
fi
echo "✅ Infra teardown successful."

# Cleanup
rm -rf e2e-refactor-project

echo "🎯 ALL E2E VERIFICATIONS PASSED!"
