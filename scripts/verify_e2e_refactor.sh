#!/bin/bash
set -e

# Comprehensive E2E Binary Verification for LDM
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for macOS (Intel/Silicon) and Linux.

TEST_PORT="${LDM_TEST_PORT:-8082}"
export TEST_PORT
KEEP_ARTIFACTS=false
for arg in "$@"; do
    if [ "$arg" == "-k" ] || [ "$arg" == "--keep" ]; then
        KEEP_ARTIFACTS=true
    fi
done

echo "⚡ Starting Standalone Binary Verification..."

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
RESULTS_FILE_TMP="${ORIGINAL_PWD}/.ldm-verify-tmp-${TIMESTAMP}.txt"

# Platform detection
PLATFORM_INFO="$OSTYPE"
if [[ "$OSTYPE" == "linux"* ]] && [ -f /etc/os-release ]; then
    DISTRO=$(grep "^PRETTY_NAME=" /etc/os-release | cut -d'=' -f2 | tr -d '"')
    PLATFORM_INFO="${DISTRO:-$OSTYPE}"
fi

{
    echo "=== LDM BINARY VERIFICATION REPORT ==="
    echo "Timestamp: $(date)"
    echo "Hostname:  $HOSTNAME"
    echo "Platform:  $PLATFORM_INFO"
    echo "Binary:    $(which "$LDM_CMD")"
    echo "Version:   $("$LDM_CMD" --version 2>/dev/null || echo "unknown")"
    
    if command -v docker &>/dev/null; then
        echo "Docker:    $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "running")"
        if docker compose version &>/dev/null; then
            echo "Compose:   $(docker compose version --short 2>/dev/null || echo "detected")"
        fi
    fi
    echo ""
} >"$RESULTS_FILE_TMP"

# Helpers
get_hash() {
    if command -v md5sum >/dev/null 2>&1; then echo "$1" | md5sum | cut -c1-8
    elif command -v md5 >/dev/null 2>&1; then echo "$1" | md5 | cut -c1-8
    else date +%s | cut -c5-12; fi
}

capture_logs_on_failure() {
    echo -e "\n--- FAILURE DEBUG LOGS ---" >> "$RESULTS_FILE_TMP"
    for container in liferay-proxy-global liferay-search-global ldm-smoke-test ldm-smoke-test-db-1; do
        if docker ps -a | grep -q "$container"; then
            echo ">> Logs for $container:" >> "$RESULTS_FILE_TMP"
            docker logs "$container" --tail 50 >> "$RESULTS_FILE_TMP" 2>&1
        fi
    done
}

cleanup_test_projects() {
    local EXIT_CODE=$?
    set +e
    local status="pass"
    if [ $EXIT_CODE -ne 0 ]; then
        status="fail"
        capture_logs_on_failure
        echo "!!! VERIFICATION FAILED (Exit Code: $EXIT_CODE) !!!"
    fi

    local env_slug
    env_slug=$("$LDM_CMD" doctor --slug 2>/dev/null | tr -d '\r' | tr ' ' '-')
    local final_name
    final_name="verify-${env_slug:-unknown}-${status}-$(get_hash "$TIMESTAMP").txt"
    
    if [ -d "e2e-work-dir/ldm-smoke-test/test-results" ]; then
        cp -r "e2e-work-dir/ldm-smoke-test/test-results" "${ORIGINAL_PWD}/" 2>/dev/null || true
    fi

    if [ -f "$RESULTS_FILE_TMP" ]; then
        mv "$RESULTS_FILE_TMP" "${ORIGINAL_PWD}/${final_name}"
        echo -e "\n✅ Verification Complete ($status)\n📊 Results: $final_name"
        if [ "$status" == "pass" ]; then
            mkdir -p "${ORIGINAL_PWD}/references/verification-results"
            cp "${ORIGINAL_PWD}/${final_name}" "${ORIGINAL_PWD}/references/verification-results/" 2>/dev/null || true
        fi
    fi

    if [ "$KEEP_ARTIFACTS" != "true" ]; then
        docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy 2>/dev/null || true
        LDM_WORKSPACE="${ORIGINAL_PWD}/e2e-work-dir" "$LDM_CMD" -y rm ldm-smoke-test --delete >/dev/null 2>&1 || true
        # Keep the venv if we are in the repository for developer convenience, otherwise delete
        if [ ! -f "pyproject.toml" ]; then
            rm -rf "${ORIGINAL_PWD}/e2e-work-dir"
        fi
    fi
}

trap cleanup_test_projects EXIT

log_and_run() {
    echo ">> $1" | tee -a "$RESULTS_FILE_TMP"
    shift
    local tmp_out
    tmp_out=$(mktemp)

    # We use PIPESTATUS to catch failure of the command even when piped to tee
    # PIPESTATUS[0] is the exit code of the first command in the pipe ($@)
    "$@" 2>&1 | tee "$tmp_out"
    local exit_code=${PIPESTATUS[0]}

    cat "$tmp_out" >> "$RESULTS_FILE_TMP"

    if [ "$exit_code" -ne 0 ]; then
        echo "❌ ERROR: Command failed with exit code $exit_code." | tee -a "$RESULTS_FILE_TMP"
        exit 1
    fi

    if grep -Ei "FATAL|❌|ERROR:" "$tmp_out" | grep -vEi "not found|already in sync|ℹ|>>" > /dev/null; then
        echo "❌ ERROR: Critical failure marker detected in output." | tee -a "$RESULTS_FILE_TMP"
        exit 1
    fi
}
# --- Execution ---

# 0. Dependencies & Virtual Environment
# We use a virtual environment to avoid PEP 668 'externally-managed-environment' errors.
LDM_WORKSPACE="${ORIGINAL_PWD}/e2e-work-dir"
TEST_VENV="${LDM_WORKSPACE}/.verify-venv"
mkdir -p "$LDM_WORKSPACE"

echo "ℹ  Preparing isolated test environment..."
if [ ! -d "$TEST_VENV" ]; then
    python3 -m venv "$TEST_VENV"
fi

# Determine venv binaries
VENV_PYTHON="${TEST_VENV}/bin/python"
VENV_PIP="${TEST_VENV}/bin/pip"
VENV_PYTEST="${TEST_VENV}/bin/pytest"

# Install dependencies into venv
if [ ! -f "$VENV_PYTEST" ]; then
    echo ">> Installing test dependencies into virtual environment..."
    "$VENV_PIP" install pytest requests PyYAML --quiet
fi

# 1. Cleanup & Setup
"$LDM_CMD" -y rm ldm-smoke-test --delete --infra >/dev/null 2>&1 || true
export LDM_WORKSPACE

# Pre-pull large images to avoid containerd lease timeouts during the timed E2E run
echo "ℹ  Pre-pulling required Docker images..."
docker pull liferay/dxp:2026.q1.7-lts --quiet
docker pull postgres:16.2 --quiet

log_and_run "Initializing Infrastructure" "$LDM_CMD" -y infra-setup --search

# 2. Guardrails
echo ">> Verifying Dev Guardrails..."
DEV_GUARD_OUT=$($LDM_CMD version --bump patch 2>&1 || true)
if echo "$DEV_GUARD_OUT" | grep -qE "Error: Developer utility requires LDM_DEV_MODE=true|Action restricted"; then
    echo "✅ Dev Guardrails verified."
else
    echo "❌ ERROR: Dev Guardrails failed. Output was: $DEV_GUARD_OUT" && exit 1
fi

echo ">> Verifying Sudo Guard (Behavioral)..."
if [ "$GITHUB_ACTIONS" = "true" ]; then
    echo "⚠️  Skipping behavioral Sudo Guard check (Sudo allowed in CI)."
elif [[ "$OSTYPE" == "linux"* ]] && command -v unshare &>/dev/null; then
    # unshare -r runs the command as simulated root (UID 0) in a new namespace
    SUDO_BLOCK_OUT=$(unshare -r "$LDM_CMD" version 2>&1 || true)
    if echo "$SUDO_BLOCK_OUT" | grep -q "Do not run LDM with 'sudo'"; then
        echo "✅ Sudo Guard verified (Blocked 'version')."
        
        # Verify that exempted commands are NOT blocked
        if unshare -r "$LDM_CMD" fix-hosts --help >/dev/null 2>&1; then
            echo "✅ Sudo Guard verified (Allowed 'fix-hosts')."
        else
            echo "❌ ERROR: Sudo Guard incorrectly blocked 'fix-hosts'!" && exit 1
        fi
    else
        # If unshare failed for other reasons (e.g. namespaces disabled), skip gracefully
        if echo "$SUDO_BLOCK_OUT" | grep -q "unshare: "; then
             echo "⚠️  Skipping behavioral Sudo Guard check (unshare simulation failed: $SUDO_BLOCK_OUT)."
        else
             echo "❌ ERROR: Sudo Guard failed to block simulated root execution." && exit 1
        fi
    fi
else
    echo "⚠️  Skipping behavioral Sudo Guard check (unshare not available or not Linux)."
fi

echo ">> Verifying Project Collision Detection..."
# Use --no-seed to avoid 1GB download for a simple collision test
if ! "$LDM_CMD" -y run "collision-test" --tag 2026.q1.4-lts --port 8099 --no-wait --no-up --no-seed > col_init.log 2>&1; then
    echo "❌ ERROR: Failed to initialize collision-test project." | tee -a "$RESULTS_FILE_TMP"
    tee -a "$RESULTS_FILE_TMP" < col_init.log
    exit 1
fi

mkdir -p "collision-test/nested"
if (cd collision-test/nested && "$LDM_CMD" -y run "collision-test" --port 8099 --no-wait --no-up --no-seed 2>&1 | grep -qE "Project collision|already registered"); then
    echo "✅ Project Collision verified."
else
    echo "❌ ERROR: Collision detection failed." | tee -a "$RESULTS_FILE_TMP"
    # Print the log of the failed second run for debugging
    (cd collision-test/nested && "$LDM_CMD" -y run "collision-test" --port 8099 --no-wait --no-up --no-seed 2>&1) | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
"$LDM_CMD" -y rm "collision-test" --delete >/dev/null 2>&1 && rm -rf "collision-test" col_init.log

# 3. Project Run
echo "ℹ  Provisioning standalone test project..."
mkdir -p "$LDM_WORKSPACE/ldm-smoke-test/files"
cd "$LDM_WORKSPACE/ldm-smoke-test"
echo -e "tag=2026.q1.7-lts\ncontainer_name=ldm-smoke-test\nport=${TEST_PORT}\ndb_type=postgresql" > meta

log_and_run "Running LDM Project" "$LDM_CMD" -y run . --no-wait --no-tld-skip --no-jvm-verify

# Wait for Health
log_and_run "Waiting for Liferay health" "$LDM_CMD" -y wait . --timeout 300

echo ">> Allowing system to stabilize (60s)..."
sleep 60

# Hot Deploy
echo ">> Deploying Test OSGi Bundle..."
mkdir -p "delayed-deploy"
# Use a minimal OSGi bundle which the Liferay AutoDeployer natively supports
"$VENV_PYTHON" -c "
import zipfile
with zipfile.ZipFile('delayed-deploy/test-bundle.jar', 'w') as zf:
    zf.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nBundle-ManifestVersion: 2\nBundle-Name: Test Bundle\nBundle-SymbolicName: com.liferay.test.bundle\nBundle-Version: 1.0.0\n')
"

# We test hot-deploy via the LDM deploy command
log_and_run "Deploying artifact" "$LDM_CMD" -y deploy . "delayed-deploy/test-bundle.jar"
echo ">> Waiting for auto-deploy processing (up to 6m)..."

# Verify Hot Deploy via Logs with a polling loop
HOT_DEPLOY_SUCCESS=false
for _ in {1..36}; do
    if docker logs ldm-smoke-test --tail 200 2>&1 | grep -q "STARTED com.liferay.test.bundle"; then
        echo "✅ Hot Deploy verified." | tee -a "$RESULTS_FILE_TMP"
        HOT_DEPLOY_SUCCESS=true
        break
    fi
    printf "." && sleep 10
done

if [ "$HOT_DEPLOY_SUCCESS" = false ]; then
    echo -e "\n❌ ERROR: Hot Deploy failed. Test Bundle did not start." | tee -a "$RESULTS_FILE_TMP"
    docker logs ldm-smoke-test --tail 100
    exit 1
fi
echo ""

# Integrity
log_and_run "Creating Snapshot" "$LDM_CMD" -y snapshot --name "Binary-Verify"
LATEST_DIR=$(find snapshots -maxdepth 1 -mindepth 1 -type d -print0 | xargs -0 ls -td | head -n 1)
SHA_FILE="${LATEST_DIR}/files.tar.gz.sha256"
echo "CORRUPTED" > "$SHA_FILE"
if "$LDM_CMD" -y restore --latest 2>&1 | grep -q "Integrity check failed"; then
    echo "✅ Integrity check verified."
else
    echo "❌ ERROR: Integrity check failed to block corruption." && exit 1
fi
log_and_run "Bypassing Integrity" "$LDM_CMD" -y restore --latest --no-verify

# UX & Scaling
echo ">> Verifying Env Sync..."
"$LDM_CMD" env . TEST_SECRET=supersecret123 >/dev/null
grep -q "TEST_SECRET=supersecret123" docker-compose.yml && echo "✅ Env Sync verified."

echo ">> Verifying Redaction..."
"$LDM_CMD" -v env . REDACT_SECRET=hidden 2>&1 | grep -q "REDACT_SECRET=\[REDACTED\]" && echo "✅ Redaction verified."

echo ">> Verifying Scaling..."
log_and_run "Scaling Liferay" "$LDM_CMD" -y scale . liferay=3
grep -q "scale_liferay=3" meta && echo "✅ Scaling verified."

# Final
log_and_run "Checking Status" "$LDM_CMD" -y status

# Clean up any potential orphans from the run
"$LDM_CMD" -y prune >/dev/null 2>&1 || true

echo -e "\n🎯 ALL E2E VERIFICATIONS PASSED!"
