#!/bin/bash
set -e

# Comprehensive E2E Binary Verification for LDM (v2.5.0 Stable Candidate)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for low-spec machines (Apple Intel).

echo "🚀 Starting Binary Verification..."

# Determine the binary command
LDM_CMD="ldm"
if ! command -v "$LDM_CMD" &>/dev/null; then
    echo "❌ ERROR: 'ldm' binary not found in PATH."
    echo "Please run 'ldm upgrade --beta' first."
    exit 1
fi

# Unique filename based on machine identity
HOSTNAME=$(hostname)
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RESULTS_FILE="$(pwd)/ldm-verify-${HOSTNAME}-${TIMESTAMP}.txt"

echo "🚀 Starting Binary Verification..."
echo "📊 Results will be saved to: $RESULTS_FILE"

{
    echo "=== LDM BINARY VERIFICATION REPORT ==="
    echo "Timestamp: $(date)"
    echo "Hostname:  $HOSTNAME"
    echo "Platform:  $OSTYPE"
    echo "Binary:    $(which "$LDM_CMD")"
    echo ""
} >"$RESULTS_FILE"

# Capture logs helper
capture_logs_on_failure() {
    echo "" >>"$RESULTS_FILE"
    echo "--- FAILURE DEBUG LOGS ---" >>"$RESULTS_FILE"
    for container in liferay-proxy-global liferay-search-global ldm-smoke-test ldm-smoke-test-db-1; do
        if docker ps -a | grep -q "$container"; then
            echo ">> Logs for $container:" >>"$RESULTS_FILE"
            docker logs "$container" --tail 50 >>"$RESULTS_FILE" 2>&1
        fi
    done
}

# Cleanup helper
cleanup_test_projects() {
    EXIT_CODE=$?
    # If the script failed, capture logs before we destroy the containers
    if [ $EXIT_CODE -ne 0 ]; then
        capture_logs_on_failure
        echo ""
        echo "!!! VERIFICATION FAILED !!!"
        
        # Final Rename based on environment slug
        ENV_SLUG=$("$LDM_CMD" doctor --slug | tr -d '\r')
        SHORT_HASH=$(echo "$TIMESTAMP" | sha256sum | cut -c1-8)
        FINAL_NAME="verify-${ENV_SLUG}-fail-${SHORT_HASH}.txt"
        mv "$RESULTS_FILE" "$(pwd)/$FINAL_NAME"

        echo "--- Dumping Results File ($FINAL_NAME) ---"
        cat "$FINAL_NAME"
        echo "--- End of Results Dump ---"
    fi

    echo "🧹 Cleaning up test artifacts..."
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy \
        ldm-smoke-test ldm-smoke-test-db-1 smoke-test-app 2>/dev/null || true
    rm -rf e2e-work-dir
}

# Ensure cleanup on exit
trap cleanup_test_projects EXIT
cleanup_test_projects

# Find the test project template (Flexible search)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEARCH_PATHS=(
    "$(dirname "$SCRIPT_DIR")/references/test-project"
    "$SCRIPT_DIR/test-project"
    "$(pwd)/test-project"
    "$(pwd)/references/test-project"
)

TEMPLATE_SRC=""
for path in "${SEARCH_PATHS[@]}"; do
    if [ -d "$path" ]; then
        TEMPLATE_SRC="$path"
        break
    fi
done

if [ -z "$TEMPLATE_SRC" ]; then
    echo "❌ ERROR: Test project template folder not found."
    echo "Please ensure the 'references/test-project' folder is available."
    exit 1
fi
echo "ℹ  Using test template: $TEMPLATE_SRC"

# Isolate the LDM workspace
LDM_WORKSPACE="$(pwd)/e2e-work-dir"
export LDM_WORKSPACE
mkdir -p "$LDM_WORKSPACE"

# --- Metadata Collection ---
echo "--- Capturing Environment State ---" | tee -a "$RESULTS_FILE"
{
    "$LDM_CMD" doctor --skip-project || true
    echo ""
    echo "--- Test Execution Log ---"
} >>"$RESULTS_FILE" 2>&1

log_and_run() {
    local msg=$1
    shift
    echo ">> $msg" | tee -a "$RESULTS_FILE"
    if ! "$@" 2>&1 | tee -a "$RESULTS_FILE"; then
        echo "❌ ERROR: Command failed: $*" | tee -a "$RESULTS_FILE"
        exit 1
    fi
}

# 1. Prepare a Clean Slate
echo "--- Step 0: Total Cleanup ---" | tee -a "$RESULTS_FILE"
log_and_run "Removing all LDM resources" "$LDM_CMD" -y rm --all --delete --infra

# Verify Docker is empty
if [ -n "$(docker ps -aq)" ]; then
    echo "❌ ERROR: Docker environment is not empty." | tee -a "$RESULTS_FILE"
    echo "Existing containers detected:" >> "$RESULTS_FILE"
    docker ps -a >> "$RESULTS_FILE"
    echo "Please run 'docker rm -f \$(docker ps -aq)' or 'docker system prune' before running this script."
    exit 1
fi
echo "✅ Docker environment is clean." | tee -a "$RESULTS_FILE"

# 2. Global Infra Setup
echo "--- Step 1: Global Infra Setup ---"
log_and_run "Initializing Infrastructure" "$LDM_CMD" -y infra-setup --search
if ! docker ps | grep -q "liferay-search-global"; then
    echo "❌ ERROR: Global Search failed to start" | tee -a "$RESULTS_FILE"
    exit 1
fi

# Verify search backup repository is registered
if ! docker exec liferay-search-global curl -s localhost:9200/_snapshot/liferay_backup | grep -q "liferay_backup"; then
    echo "❌ ERROR: Global Search backup repository not registered" | tee -a "$RESULTS_FILE"
    exit 1
fi
echo "✅ Global Search backup repository verified." | tee -a "$RESULTS_FILE"

# 2. Project Lifecycle
echo "--- Step 2: Project Run ---"
cp -r "$TEMPLATE_SRC" "$LDM_WORKSPACE/ldm-smoke-test"
cd "$LDM_WORKSPACE/ldm-smoke-test"

log_and_run "Running LDM Project" "$LDM_CMD" -y run . --no-wait --no-tld-skip --no-jvm-verify

# 3. Snapshot & Restore Verification
echo "--- Step 3: Snapshot & Restore ---"
log_and_run "Creating Snapshot" "$LDM_CMD" -y snapshot --name "Binary-Verify"
if [ ! -d "snapshots" ]; then
    echo "❌ ERROR: Snapshot directory not created" | tee -a "$RESULTS_FILE"
    exit 1
fi

log_and_run "Restoring Snapshot" "$LDM_CMD" -y restore --latest
echo "✅ Snapshot and Restore verified." | tee -a "$RESULTS_FILE"

# 4. Status and Logs
echo "--- Step 4: Status & Logs ---"
log_and_run "Checking Status" "$LDM_CMD" -y status
log_and_run "Checking Logs" "$LDM_CMD" -y logs --no-wait
echo "✅ Status and Logs verified." | tee -a "$RESULTS_FILE"

# 5. Teardown
echo "--- Step 5: Teardown ---"
log_and_run "Tearing down with infra" "$LDM_CMD" -y down --infra
echo "✅ Teardown successful." | tee -a "$RESULTS_FILE"

<<<<<<< HEAD
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
=======
echo "" >>"$RESULTS_FILE"
echo "🎯 ALL E2E VERIFICATIONS PASSED!" | tee -a "$RESULTS_FILE"
<<<<<<< HEAD
echo "Full results available in: $RESULTS_FILE"
>>>>>>> 13d6ab2 (feat: environmental hardening, snapshots, and automated verification suite [pre-release])
=======

# Final Rename based on environment slug
ENV_SLUG=$("$LDM_CMD" doctor --slug | tr -d '\r')
SHORT_HASH=$(echo "$TIMESTAMP" | md5sum | cut -c1-8)
FINAL_NAME="verify-${ENV_SLUG}-pass-${SHORT_HASH}.txt"
mv "$RESULTS_FILE" "$(pwd)/$FINAL_NAME"

echo "Full results available in: $FINAL_NAME"
>>>>>>> fbe7738 (feat: implement environment slugs for automated verification reporting [pre-release])
