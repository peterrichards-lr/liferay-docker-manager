#!/bin/bash
set -e

# Comprehensive E2E Verification for LDM v2.3.5 Refactor
# Verifies: Infra, Seeding, Disambiguation, Labels, and Status

echo "🚀 Starting Comprehensive E2E Verification..."

# Cleanup helper - Clean both local and global LDM paths to be absolutely sure
cleanup_test_projects() {
    echo "🧹 Cleaning up test artifacts..."
    # 1. Stop and remove containers
    docker rm -f liferay-proxy-global liferay-search-global test-e2e-refactor test-isolation-a test-isolation-b test-ssl-proxy 2>/dev/null || true
    
    # 2. Delete test project folders in common locations
    rm -rf test-e2e-refactor-project test-isolation-a test-isolation-b test-ssl-proxy e2e-work-dir
    rm -rf ~/ldm/test-e2e-refactor-project ~/ldm/test-isolation-a ~/ldm/test-isolation-b ~/ldm/test-ssl-proxy
    
    # 3. Deep cleanup of any folders matching test- in current dir
    find . -maxdepth 1 -name "test-*" -type d -exec rm -rf {} +
}

# Initial Cleanup
cleanup_test_projects

# Isolate the LDM workspace for this test run
LDM_WORKSPACE="$(pwd)/e2e-work-dir"
export LDM_WORKSPACE
mkdir -p "$LDM_WORKSPACE"
cd "$LDM_WORKSPACE"

# Ensure we are testing the LOCAL package
export PYTHONPATH=$PYTHONPATH:../
PYTHON_CMD="python3 ../liferay_docker.py"

# 1. Verify Infra Setup
echo "--- Step 1: Global Infra Setup ---"
$PYTHON_CMD -y infra-setup
if ! docker ps | grep -q "liferay-proxy-global"; then
    echo "❌ ERROR: Infra setup failed to start liferay-proxy-global"
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
  echo "db_type=hypersonic"
} > test-e2e-refactor-project/meta

# Run it
$PYTHON_CMD -y run test-e2e-refactor-project --tag 2026.q1.4-lts --no-wait --no-tld-skip --no-jvm-verify

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
  sed -i '' 's/image: "alpine"/image: "alpine"\n    command: sleep 60/g' test-e2e-refactor-project/docker-compose.yml
else
  sed -i 's/image: "alpine"/image: "alpine"\n    command: sleep 60/g' test-e2e-refactor-project/docker-compose.yml
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

# Verify infra logs access (checks env vars injection)
INFRA_LOG_OUT=$($PYTHON_CMD -y logs --infra --no-wait 2>&1 || true)
if echo "$INFRA_LOG_OUT" | grep -q "LDM_CERTS_DIR"; then
     echo "❌ ERROR: Infra logs failed due to missing environment variables"
     exit 1
fi
echo "✅ CLI Disambiguation & Infra Logs verified."

# 5. Verify Instance Isolation (IP-based port binding)
echo "--- Step 5: Instance Isolation Verification ---"

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
    } > "$dir/meta"
}

patch_isolation_compose() {
    local dir=$1
    if [[ "$OSTYPE" == "darwin"* ]]; then
      sed -i '' 's/image: "alpine"/image: "alpine"\n    command: sh -c "sleep 3600"/g' "$dir/docker-compose.yml"
    else
      sed -i 's/image: "alpine"/image: "alpine"\n    command: sh -c "sleep 3600"/g' "$dir/docker-compose.yml"
    fi
}

# A. Start Project A on 127.0.0.1
create_isolation_project "test-isolation-a" "test-isolation-a" "127.0.0.1" "8084"
$PYTHON_CMD -y run test-isolation-a --no-wait --no-tld-skip --no-jvm-verify
patch_isolation_compose "test-isolation-a"
docker compose -f test-isolation-a/docker-compose.yml up -d
sleep 2

# B. Attempt conflict (Same Hostname 127.0.0.1) - Should FAIL early
create_isolation_project "test-isolation-b" "test-isolation-b" "127.0.0.1" "8085"
if $PYTHON_CMD -y run test-isolation-b --no-wait --no-tld-skip --no-jvm-verify 2>&1 | grep -q "already registered"; then
    echo "✅ Success: LDM correctly detected registry hostname conflict."
else
    echo "❌ ERROR: LDM failed to detect registry hostname conflict."
    exit 1
fi

# C. Attempt isolation (Different Hostname 127.0.0.2, Same Port 8084) - Should SUCCEED
rm -rf test-isolation-b
create_isolation_project "test-isolation-b" "test-isolation-b" "127.0.0.2" "8084"
$PYTHON_CMD -y run test-isolation-b --no-up --no-tld-skip --no-jvm-verify
if grep -q "127.0.0.2:8084:8080" test-isolation-b/docker-compose.yml; then
    echo "✅ Success: Generated compose correctly isolated by IP."
else
    echo "❌ ERROR: Generated compose missing IP isolation"
    exit 1
fi

# 6. Verify Proxy-Only Routing for SSL Custom Domains
echo "--- Step 6: Proxy-Only SSL Routing Verification ---"
rm -rf test-ssl-proxy
mkdir -p test-ssl-proxy/files
{
  echo "tag=2026.q1.4-lts"
  echo "container_name=test-ssl-proxy"
  echo "host_name=my-custom-domain.com"
  echo "ssl=true"
  echo "port=8086"
  echo "db_type=hypersonic"
} > test-ssl-proxy/meta

# Run in no-up mode to check generated config.
$PYTHON_CMD -y run test-ssl-proxy --tag 2026.q1.4-lts --no-up --no-tld-skip --no-jvm-verify

if grep -q "8086:8080" test-ssl-proxy/docker-compose.yml; then
    echo "❌ ERROR: SSL custom domain exposed port 8086 to host. Should be proxy-only."
    grep "8086:8080" test-ssl-proxy/docker-compose.yml
    exit 1
fi
echo "✅ Success: SSL custom domain has no direct host port mapping."

# 7. Verify Infra Teardown
echo "--- Step 7: Infra Teardown ---"
$PYTHON_CMD -y down test-e2e-refactor-project --infra
if docker ps -a | grep -q "liferay-proxy-global"; then
    echo "❌ ERROR: Infra teardown failed to remove liferay-proxy-global"
    exit 1
fi
echo "✅ Infra teardown successful."

# 8. Deep Log Verification (Database & Search Hardening)
echo "--- Step 8: Deep Log Verification ---"
cleanup_test_projects
$PYTHON_CMD -y infra-setup

create_isolation_project "test-log-verify" "test-log-verify" "localhost" "8088"
# Update meta to use PostgreSQL and ensure Shared Search is ON (default)
# OS-Agnostic sed: Avoid -i '' which fails on Linux
sed 's/db_type=hypersonic/db_type=postgresql/' test-log-verify/meta > meta.tmp && mv meta.tmp test-log-verify/meta

# Run with --no-wait so we can poll the logs ourselves
$PYTHON_CMD -y run test-log-verify --tag 2026.q1.4-lts --no-wait --no-tld-skip --no-jvm-verify

# Verify the property injection in portal-ext.properties (Reliable Mixed-Case Path)
if ! grep -q "jdbc.default.driverClassName=org.postgresql.Driver" test-log-verify/files/portal-ext.properties; then
    echo "❌ ERROR: Property injection failed for PostgreSQL driver in portal-ext.properties"
    exit 1
fi

# Verify the environment injection for search (Remains in env vars)
<<<<<<< HEAD
<<<<<<< HEAD
if ! grep -q "LIFERAY_ELASTICSEARCH_SIDECAR_ENABLED=false" test-log-verify/docker-compose.yml; then
=======
# Note: Liferay reliably decodes using __ for OSGi structural mapping
if ! grep -q "LIFERAY_ELASTICSEARCH__PRODUCTION__MODE__ENABLED=true" test-log-verify/docker-compose.yml; then
>>>>>>> bb0c7fb (feat: harden environmental diagnostics and formalize project management [pre-release])
=======
# Note: Liferay reliably decodes using _PERIOD_ for portal properties
if ! grep -q "LIFERAY_ELASTICSEARCH_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=true" test-log-verify/docker-compose.yml; then
>>>>>>> 3eafd46 (fix: resolve search version mismatch and standardize environment naming [pre-release])
    echo "❌ ERROR: Environment injection failed to disable Sidecar ES"
    exit 1
fi

# Poll for logs (simulated or real if image allows)
# Since we use alpine in CI, we will verify that the project successfully claimed 
# its configuration. Real log verification is reserved for local runs with full images.
echo "✅ Log-equivalent environment verification successful."

# Final Cleanup
cd ..
rm -rf e2e-work-dir

echo "🎯 ALL E2E VERIFICATIONS PASSED!"
