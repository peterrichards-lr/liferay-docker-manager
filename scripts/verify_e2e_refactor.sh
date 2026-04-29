#!/bin/bash
set -e

# Comprehensive E2E Binary Verification for LDM
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for macOS (Intel/Silicon) and Linux.

echo "🚀 Starting Binary Verification..."

# Store the original directory for final report placement
ORIGINAL_PWD=$(pwd)

# Determine the binary command
LDM_CMD="ldm"
if ! command -v "$LDM_CMD" &>/dev/null; then
    echo "❌ ERROR: 'ldm' binary not found in PATH."
    echo "Please ensure LDM is installed and in your PATH."
    exit 1
fi

# Unique filename based on machine identity
HOSTNAME=$(hostname)
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# IMPORTANT: Keep RESULTS_FILE_TMP in ORIGINAL_PWD so it isn't deleted by work-dir cleanup
# Use a specific prefix to avoid glob matches with other files
RESULTS_FILE_TMP="${ORIGINAL_PWD}/.ldm-verify-tmp-${TIMESTAMP}.txt"

{
    echo "=== LDM BINARY VERIFICATION REPORT ==="
    echo "Timestamp: $(date)"
    echo "Hostname:  $HOSTNAME"
    echo "Platform:  $OSTYPE"
    echo "Binary:    $(which "$LDM_CMD")"
    echo "Version:   $("$LDM_CMD" --version 2>/dev/null || echo "unknown")"
    echo ""
} >"$RESULTS_FILE_TMP"

# Cross-platform MD5 helper
get_hash() {
    local input=$1
    if command -v md5sum >/dev/null 2>&1; then
        echo "$input" | md5sum | cut -c1-8
    elif command -v md5 >/dev/null 2>&1; then
        # macOS md5 command
        echo "$input" | md5 | cut -c1-8
    else
        # Fallback: simple numeric hash from date if no md5 tool found
        date +%s | cut -c5-12
    fi
}

# Capture logs helper
capture_logs_on_failure() {
    echo "" >>"$RESULTS_FILE_TMP"
    echo "--- FAILURE DEBUG LOGS ---" >>"$RESULTS_FILE_TMP"
    for container in liferay-proxy-global liferay-search-global ldm-smoke-test ldm-smoke-test-db-1; do
        if docker ps -a | grep -q "$container"; then
            echo ">> Logs for $container:" >>"$RESULTS_FILE_TMP"
            docker logs "$container" --tail 50 >>"$RESULTS_FILE_TMP" 2>&1
        fi
    done
}

# Cleanup helper
cleanup_test_projects() {
    local EXIT_CODE=$?
    
    # We are inside the trap. We must be very careful not to trigger set -e again.
    set +e

    # Determine status for filename
    local status="pass"
    if [ $EXIT_CODE -ne 0 ]; then
        status="fail"
        capture_logs_on_failure
        echo ""
        echo "!!! VERIFICATION FAILED (Exit Code: $EXIT_CODE) !!!"
    fi

    # Final Rename based on environment slug
    local env_slug
    env_slug=$("$LDM_CMD" doctor --slug 2>/dev/null | tr -d '\r' | tr ' ' '-')
    if [ -z "$env_slug" ] || [ "$env_slug" == "unknown" ]; then
        env_slug="unknown-env"
    fi
    
    local short_hash
    short_hash=$(get_hash "$TIMESTAMP")
    local final_name="verify-${env_slug}-${status}-${short_hash}.txt"
    local final_path="${ORIGINAL_PWD}/${final_name}"

    # Move report to final location BEFORE deleting the work dir
    if [ -f "$RESULTS_FILE_TMP" ]; then
        mv "$RESULTS_FILE_TMP" "$final_path"
        echo ""
        echo "================================================================"
        echo "✅ Verification Complete ($status)"
        echo "📊 Results: $final_name"
        echo "================================================================"
    fi

    echo "🧹 Cleaning up test artifacts..."
    # SURGICAL cleanup: only remove what we created
    # 1. Infrastructure containers
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy smoke-test-app 2>/dev/null || true
    
    # 2. Project stack via LDM (this handles volumes and registry cleanup)
    # We ensure LDM_WORKSPACE is set to the isolated work dir so it doesn't touch other projects
    LDM_WORKSPACE="${ORIGINAL_PWD}/e2e-work-dir" "$LDM_CMD" -y rm ldm-smoke-test --delete >/dev/null 2>&1 || true
    
    # 3. Isolated work directory
    if [ -d "${ORIGINAL_PWD}/e2e-work-dir" ]; then
        rm -rf "${ORIGINAL_PWD}/e2e-work-dir"
    fi
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
    echo "Please ensure you are running from the repository root or that 'references/test-project' is present."
    exit 1
fi
echo "ℹ  Using test template: $TEMPLATE_SRC"

# Isolate the LDM workspace
LDM_WORKSPACE="${ORIGINAL_PWD}/e2e-work-dir"
export LDM_WORKSPACE
mkdir -p "$LDM_WORKSPACE"

log_and_run() {
    local msg=$1
    shift
    echo ">> $msg" | tee -a "$RESULTS_FILE_TMP"
    
    # We use a temporary file to capture output so we can scan for FATAL
    local tmp_output
    tmp_output=$(mktemp -t ldm-verify-XXXX)
    
    # Execute and capture
    if ! "$@" 2>&1 | tee "$tmp_output"; then
        cat "$tmp_output" >> "$RESULTS_FILE_TMP"
        echo "❌ ERROR: Command failed with exit code $?: $*" | tee -a "$RESULTS_FILE_TMP"
        rm -f "$tmp_output"
        exit 1
    fi
    
    cat "$tmp_output" >> "$RESULTS_FILE_TMP"
    
    # Scan for FATAL or specific LDM error markers that should trigger a script failure
    # We ignore standard "not found" or "already in sync" messages
    if grep -Ei "FATAL|❌|ERROR:" "$tmp_output" | grep -v "ℹ" | grep -v ">>" | grep -vEi "not found|already in sync" > /dev/null; then
        echo "❌ ERROR: Critical failure detected in output of command: $*" | tee -a "$RESULTS_FILE_TMP"
        rm -f "$tmp_output"
        exit 1
    fi
    
    rm -f "$tmp_output"
}

# --- Metadata Collection ---
echo "--- Capturing Environment State ---" | tee -a "$RESULTS_FILE_TMP"
{
    "$LDM_CMD" doctor --skip-project || true
    echo ""
    echo "--- Test Execution Log ---"
} >>"$RESULTS_FILE_TMP" 2>&1

# 1. Prepare a Clean Slate (SURGICAL)
echo "--- Step 0: Targeted Cleanup ---"
# We completely silence this step to avoid confusing "not found" messages in the terminal,
# but we still log the attempt to the results file for completeness.
{
    echo ">> Preparing clean slate (removing project and infra if they exist)"
    "$LDM_CMD" -y rm ldm-smoke-test --delete --infra
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy
    # Wipe global search data to prevent mapping corruption on restart
    rm -rf ~/.ldm/infra/search/data
} >>"$RESULTS_FILE_TMP" 2>&1 || true


echo "✅ Clean slate established." | tee -a "$RESULTS_FILE_TMP"

# 2. Global Infra Setup
echo "--- Step 1: Global Infra Setup ---"
log_and_run "Initializing Infrastructure" "$LDM_CMD" -y infra-setup --search

# 3. Project Lifecycle
echo "--- Step 2: Project Run ---"
echo "ℹ  Provisioning test project 'ldm-smoke-test' from template..." | tee -a "$RESULTS_FILE_TMP"
cp -r "$TEMPLATE_SRC" "$LDM_WORKSPACE/ldm-smoke-test"
cd "$LDM_WORKSPACE/ldm-smoke-test"

# Explicit check for the meta file to answer user question
if [ -f "meta" ]; then
    echo "✅ Project metadata verified (meta)." | tee -a "$RESULTS_FILE_TMP"
else
    echo "❌ ERROR: Project metadata file (meta) was not copied correctly!" | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi

log_and_run "Running LDM Project" "$LDM_CMD" -y run . --no-wait --no-tld-skip --no-jvm-verify

# 4. Snapshot & Restore Verification
echo "--- Step 3: Snapshot & Restore ---"
log_and_run "Creating Snapshot" "$LDM_CMD" -y snapshot --name "Binary-Verify"
if [ ! -d "snapshots" ]; then
    echo "❌ ERROR: Snapshot directory 'snapshots/' was not created." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi

log_and_run "Restoring Snapshot" "$LDM_CMD" -y restore --latest
echo "✅ Snapshot and Restore verified." | tee -a "$RESULTS_FILE_TMP"

# 5. Status and Logs
echo "--- Step 4: Status & Logs ---"
log_and_run "Checking Status" "$LDM_CMD" -y status
log_and_run "Checking Logs" "$LDM_CMD" -y logs --no-wait
echo "✅ Status and Logs verified." | tee -a "$RESULTS_FILE_TMP"

# 6. Teardown
echo "--- Step 5: Teardown ---"
log_and_run "Tearing down stack" "$LDM_CMD" -y down ldm-smoke-test --infra
echo "✅ Teardown successful." | tee -a "$RESULTS_FILE_TMP"

echo "" >>"$RESULTS_FILE_TMP"
echo "🎯 ALL E2E VERIFICATIONS PASSED!" | tee -a "$RESULTS_FILE_TMP"
