#!/bin/bash
set -e

# Comprehensive E2E Binary Verification for LDM
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for macOS (Intel/Silicon) and Linux.

KEEP_ARTIFACTS=false
for arg in "$@"; do
    if [ "$arg" == "-k" ] || [ "$arg" == "--keep" ]; then
        KEEP_ARTIFACTS=true
    fi
done

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

# Determine the OS platform precisely
PLATFORM_INFO="$OSTYPE"
if [[ "$OSTYPE" == "linux"* ]]; then
    if [ -f /etc/os-release ]; then
        # Extract PRETTY_NAME or ID+VERSION_ID
        DISTRO=$(grep "^PRETTY_NAME=" /etc/os-release | cut -d'=' -f2 | tr -d '"')
        if [ -n "$DISTRO" ]; then
            PLATFORM_INFO="$DISTRO"
        else
            ID=$(grep "^ID=" /etc/os-release | cut -d'=' -f2 | tr -d '"')
            VER=$(grep "^VERSION_ID=" /etc/os-release | cut -d'=' -f2 | tr -d '"')
            PLATFORM_INFO="${ID}-${VER}"
        fi
    fi
fi

{
    echo "=== LDM BINARY VERIFICATION REPORT ==="
    echo "Timestamp: $(date)"
    echo "Hostname:  $HOSTNAME"
    echo "Platform:  $PLATFORM_INFO"
    echo "Binary:    $(which "$LDM_CMD")"
    echo "Version:   $("$LDM_CMD" --version 2>/dev/null || echo "unknown")"
    
    # Capture Provider Versions explicitly for the header
    if command -v colima &>/dev/null; then
        echo "Colima:    $(colima version | grep -o 'v[0-9.]*' | head -n 1 || echo "installed")"
    fi
    if command -v orb &>/dev/null; then
        echo "OrbStack:  $(orb version | grep -o '[0-9.]*' | head -n 1 || echo "installed")"
    fi
    if command -v docker &>/dev/null; then
        echo "Docker:    $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "running")"
        if docker compose version &>/dev/null; then
            echo "Compose:   $(docker compose version --short 2>/dev/null || echo "detected")"
        fi
    fi
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
    echo "" | tee -a "$RESULTS_FILE_TMP"
    echo "--- FAILURE DEBUG LOGS ---" | tee -a "$RESULTS_FILE_TMP"
    for container in liferay-proxy-global liferay-search-global ldm-smoke-test ldm-smoke-test-db-1; do
        if docker ps -a | grep -q "$container"; then
            echo ">> Logs for $container:" | tee -a "$RESULTS_FILE_TMP"
            docker logs "$container" --tail 50 2>&1 | tee -a "$RESULTS_FILE_TMP"
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
        
        # Automatically archive passing reports
        if [ "$status" == "pass" ]; then
            local archive_dir="${ORIGINAL_PWD}/references/verification-results"
            mkdir -p "$archive_dir"
            cp "$final_path" "$archive_dir/"
            echo "📦 Archived to: references/verification-results/"
        fi
        echo "================================================================"
    fi

    if [ "$KEEP_ARTIFACTS" = true ]; then
        echo "⚠️  --keep flag detected. Skipping artifact cleanup."
        echo "Container 'ldm-smoke-test' and workspace 'e2e-work-dir' have been preserved."
        return
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
LDM_COMMON_DIR="${ORIGINAL_PWD}/common"
export LDM_WORKSPACE
export LDM_COMMON_DIR
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

log_and_run_no_scan() {
    local msg=$1
    shift
    echo ">> $msg" | tee -a "$RESULTS_FILE_TMP"
    if ! "$@" 2>&1 | tee -a "$RESULTS_FILE_TMP"; then
        echo "❌ ERROR: Command failed with exit code $?: $*" | tee -a "$RESULTS_FILE_TMP"
        exit 1
    fi
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
{
    echo ">> Preparing clean slate (removing project and infra if they exist)"
    "$LDM_CMD" -y rm ldm-smoke-test --delete --infra 2>&1
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy 2>&1
    # Wipe global search data to prevent mapping corruption on restart
    rm -rf ~/.ldm/infra/search/data
} >>"$RESULTS_FILE_TMP" 2>&1 || true
echo "✅ Clean slate established."
echo "✅ Clean slate established." >>"$RESULTS_FILE_TMP"

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

# 2b. Wait for Liferay Health (Required for UI Tests)
echo "--- Step 2b: Wait for Liferay Health ---"
# LDM-383: Use metadata to get the actual container name if possible
PROJECT_NAME="ldm-smoke-test"
if [ -f "meta" ]; then
    C_NAME=$(grep "container_name=" meta | cut -d'=' -f2)
    if [ -n "$C_NAME" ]; then
        PROJECT_NAME="$C_NAME"
    fi
fi

echo ">> Waiting for Liferay container '$PROJECT_NAME' to be healthy (this can take 5-15 minutes)..." | tee -a "$RESULTS_FILE_TMP"
MAX_RETRIES=90 # 15 minutes total
COUNT=0

until [ "$(docker inspect -f '{{.State.Health.Status}}' "$PROJECT_NAME" 2>/dev/null)" == "healthy" ]; do
    # LDM-385: Check for Tomcat startup log marker as a faster/more reliable 'ready' signal
    if docker logs "$PROJECT_NAME" 2>&1 | grep -q "org.apache.catalina.startup.Catalina.start Server startup in"; then
        echo ""
        echo "✅ Liferay Tomcat has started (detected via logs)." | tee -a "$RESULTS_FILE_TMP"
        break
    fi

    # Fallback: if the container is running and we've reached 12 minutes, check if it's actually responding
    # Some images might have broken healthchecks in specific environments
    if [ $COUNT -gt 72 ]; then
        if [ "$(docker inspect -f '{{.State.Status}}' "$PROJECT_NAME" 2>/dev/null)" == "running" ]; then
            echo "⚠️  Container is running but not reporting healthy. Attempting to proceed..."
            break
        fi
    fi

    if [ $COUNT -ge $MAX_RETRIES ]; then
        echo "❌ ERROR: Timeout waiting for Liferay health." | tee -a "$RESULTS_FILE_TMP"
        echo "Current status: $(docker inspect -f '{{.State.Health.Status}}' "$PROJECT_NAME" 2>/dev/null)" | tee -a "$RESULTS_FILE_TMP"
        echo "Container State:" | tee -a "$RESULTS_FILE_TMP"
        docker inspect "$PROJECT_NAME" | tee -a "$RESULTS_FILE_TMP"
        echo "Recent Logs:" | tee -a "$RESULTS_FILE_TMP"
        docker logs "$PROJECT_NAME" --tail 200 | tee -a "$RESULTS_FILE_TMP"
        exit 1
    fi
    printf "."
    sleep 10
    COUNT=$((COUNT+1))
done
echo ""
echo "✅ Liferay is healthy (or running)." | tee -a "$RESULTS_FILE_TMP"

# 2b-Sequenced: Delayed Fragment Deployment
echo "--- Step 2b: Sequenced Fragment Deployment ---"
if [ -f "${LDM_WORKSPACE}/ldm-smoke-test/delayed-deploy/test-fragments.zip" ]; then
    echo ">> Triggering hot-deployment of test-fragments.zip..." | tee -a "$RESULTS_FILE_TMP"
    cp "${LDM_WORKSPACE}/ldm-smoke-test/delayed-deploy/test-fragments.zip" "${LDM_WORKSPACE}/ldm-smoke-test/deploy/"
    
    # Wait for Liferay's DirectoryWatcher to process the zip
    echo ">> Waiting 30s for Liferay auto-deploy scanner to process the fragment..."
    sleep 30
else
    echo "⚠️  WARNING: delayed-deploy/test-fragments.zip not found!" | tee -a "$RESULTS_FILE_TMP"
fi

# 2c. UI Verification (Fragments)
echo "--- Step 2c: UI Verification (Fragments) ---"
# Note: we disable coverage with --no-cov to avoid 0% coverage failure on installed binary
log_and_run "Running Playwright UI Tests" pytest "${ORIGINAL_PWD}/ldm_core/tests/e2e_ui_fragments.py" --no-cov --base-url http://localhost:8082 --screenshot=only-on-failure
echo "✅ UI Verification successful." | tee -a "$RESULTS_FILE_TMP"

# 3. Snapshot & Restore Verification
echo "--- Step 3: Snapshot & Restore ---"
log_and_run "Creating Snapshot" "$LDM_CMD" -y snapshot --name "Binary-Verify"
if [ ! -d "snapshots" ]; then
    echo "❌ ERROR: Snapshot directory 'snapshots/' was not created." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi

log_and_run "Restoring Snapshot" "$LDM_CMD" -y restore --latest
echo "✅ Snapshot and Restore verified." | tee -a "$RESULTS_FILE_TMP"

# 4b. Integrity Verification (Failure & Override)
echo "--- Step 3b: Integrity Verification ---"
# Resolve the latest snapshot directory
# shellcheck disable=SC2012
LATEST_SNAP=$(ls -td snapshots/*/ | head -n 1)
SHA_FILE="${LATEST_SNAP}files.tar.gz.sha256"

if [ -f "$SHA_FILE" ]; then
    echo "ℹ  Tampering with checksum to test enforcement..." | tee -a "$RESULTS_FILE_TMP"
    echo "CORRUPTED-DATA" > "$SHA_FILE"
    
    echo ">> Attempting restore of tampered snapshot (Expected Failure)..."
    # We don't use log_and_run here because we EXPECT it to fail (non-zero exit)
    if "$LDM_CMD" -y restore --latest 2>&1 | grep -q "Integrity check failed"; then
         echo "✅ Tampered snapshot correctly rejected." | tee -a "$RESULTS_FILE_TMP"
    else
         echo "❌ ERROR: Tampered snapshot was NOT rejected!" | tee -a "$RESULTS_FILE_TMP"
         exit 1
    fi
    
    log_and_run "Restoring with --no-verify override" "$LDM_CMD" -y restore --latest --no-verify
    echo "✅ Integrity override verified." | tee -a "$RESULTS_FILE_TMP"
else
    echo "❌ ERROR: No SHA256 file found. Integrity generation failed!" | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi

# 5. Status and Logs
echo "--- Step 4: Status & Logs ---"
log_and_run "Checking Status" "$LDM_CMD" -y status
log_and_run_no_scan "Checking Logs" "$LDM_CMD" -y logs --no-wait
echo "✅ Status and Logs verified." | tee -a "$RESULTS_FILE_TMP"

# 6. Teardown
echo "--- Step 5: Teardown ---"
log_and_run "Tearing down stack" "$LDM_CMD" -y down ldm-smoke-test --infra
echo "✅ Teardown successful." | tee -a "$RESULTS_FILE_TMP"

echo "" >>"$RESULTS_FILE_TMP"
echo "🎯 ALL E2E VERIFICATIONS PASSED!" | tee -a "$RESULTS_FILE_TMP"
