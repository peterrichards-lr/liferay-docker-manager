#!/bin/bash
set -e

# Comprehensive E2E Binary Verification for LDM (v2.5.0 Stable Candidate)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for low-spec machines (Apple Intel).

echo "🚀 Starting Binary Verification..."

# Store the original directory for final report placement
ORIGINAL_PWD=$(pwd)

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
RESULTS_FILE="${ORIGINAL_PWD}/ldm-verify-${HOSTNAME}-${TIMESTAMP}.txt"

echo "📊 Results will be saved to: $RESULTS_FILE"

{
    echo "=== LDM BINARY VERIFICATION REPORT ==="
    echo "Timestamp: $(date)"
    echo "Hostname:  $HOSTNAME"
    echo "Platform:  $OSTYPE"
    echo "Binary:    $(which "$LDM_CMD")"
    echo ""
} >"$RESULTS_FILE"

# Cross-platform MD5 helper
get_hash() {
    if command -v md5sum >/dev/null 2>&1; then
        echo "$1" | md5sum | cut -c1-8
    elif command -v md5 >/dev/null 2>&1; then
        echo "$1" | md5 | cut -c1-8
    else
        # Fallback to just the timestamp if no hash tool found
        echo "$1" | cut -c1-8
    fi
}

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
    local EXIT_CODE=$?
    
    # If the script failed, capture logs before we destroy the containers
    if [ $EXIT_CODE -ne 0 ]; then
        capture_logs_on_failure
        echo ""
        echo "!!! VERIFICATION FAILED !!!"
        
        # Final Rename based on environment slug
        ENV_SLUG=$("$LDM_CMD" doctor --slug 2>/dev/null | tr -d '\r' || echo "unknown")
        SHORT_HASH=$(get_hash "$TIMESTAMP")
        FINAL_NAME="verify-${ENV_SLUG}-fail-${SHORT_HASH}.txt"
        
        # Ensure we are in the original dir or use absolute path
        mv "$RESULTS_FILE" "${ORIGINAL_PWD}/${FINAL_NAME}"

        echo "--- Dumping Results File ($FINAL_NAME) ---"
        cat "${ORIGINAL_PWD}/${FINAL_NAME}"
        echo "--- End of Results Dump ---"
    fi

    echo "🧹 Cleaning up test artifacts..."
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy \
        ldm-smoke-test ldm-smoke-test-db-1 smoke-test-app 2>/dev/null || true
    rm -rf "${ORIGINAL_PWD}/e2e-work-dir"
}

# Ensure cleanup on exit
trap cleanup_test_projects EXIT

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
LDM_WORKSPACE="${ORIGINAL_PWD}/e2e-work-dir"
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
    
    # We use a temporary file to capture output so we can scan for FATAL
    local tmp_output
    tmp_output=$(mktemp)
    
    # Execute and capture
    if ! "$@" 2>&1 | tee "$tmp_output"; then
        cat "$tmp_output" >> "$RESULTS_FILE"
        echo "❌ ERROR: Command failed: $*" | tee -a "$RESULTS_FILE"
        rm -f "$tmp_output"
        exit 1
    fi
    
    cat "$tmp_output" >> "$RESULTS_FILE"
    
    # Scan for FATAL or specific LDM error markers that should trigger a script failure
    if grep -Ei "FATAL|❌|ERROR:" "$tmp_output" | grep -v "ℹ" | grep -v ">>" > /dev/null; then
        echo "❌ ERROR: Critical failure detected in output of: $*" | tee -a "$RESULTS_FILE"
        rm -f "$tmp_output"
        exit 1
    fi
    
    rm -f "$tmp_output"
}

# 1. Prepare a Clean Slate
echo "--- Step 0: Total Cleanup ---" | tee -a "$RESULTS_FILE"
log_and_run "Removing all LDM resources" "$LDM_CMD" -y rm --all --delete --infra

# Verify Docker is empty
if [ -n "$(docker ps -aq)" ]; then
    echo "❌ ERROR: Docker environment is not empty." | tee -a "$RESULTS_FILE"
    echo "Existing containers detected:" >> "$RESULTS_FILE"
    docker ps -a >> "$RESULTS_FILE"
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

echo "" >>"$RESULTS_FILE"
echo "🎯 ALL E2E VERIFICATIONS PASSED!" | tee -a "$RESULTS_FILE"

# Final Rename based on environment slug
ENV_SLUG=$("$LDM_CMD" doctor --slug 2>/dev/null | tr -d '\r' || echo "unknown")
SHORT_HASH=$(get_hash "$TIMESTAMP")
FINAL_NAME="verify-${ENV_SLUG}-pass-${SHORT_HASH}.txt"
mv "$RESULTS_FILE" "${ORIGINAL_PWD}/${FINAL_NAME}"

echo "Full results available in: $FINAL_NAME"
