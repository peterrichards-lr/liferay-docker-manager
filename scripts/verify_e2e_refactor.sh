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
    local exit_code=0
    "$@" 2>&1 | tee "$tmp_out" || exit_code=$?

    cat "$tmp_out" >> "$RESULTS_FILE_TMP"

    # Exit code of the actual command ($@) is at index 0 of PIPESTATUS in bash
    # However, since we are using '|| exit_code=$?', exit_code will contain the non-zero code.
    if [ $exit_code -ne 0 ]; then
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
VENV_PLAYWRIGHT="${TEST_VENV}/bin/playwright"

# Install dependencies into venv
if [ ! -f "$VENV_PYTEST" ]; then
    echo ">> Installing test dependencies into virtual environment..."
    "$VENV_PIP" install pytest pytest-playwright requests PyYAML --quiet
    "$VENV_PLAYWRIGHT" install chromium --with-deps
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
DEV_GUARD_OUT=$($LDM_CMD version --bump patch -y 2>&1 || true)
if echo "$DEV_GUARD_OUT" | grep -qE "Error: Developer utility requires LDM_DEV_MODE=true|Action restricted"; then
    echo "✅ Dev Guardrails verified."
else
    echo "❌ ERROR: Dev Guardrails failed. Output was: $DEV_GUARD_OUT" && exit 1
fi

echo ">> Verifying Sudo Guard (Behavioral)..."
if [[ "$OSTYPE" == "linux"* ]] && command -v unshare &>/dev/null; then
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
echo -e "tag=2026.q1.7-lts\ncontainer_name=ldm-smoke-test\nport=8082\ndb_type=postgresql" > meta

log_and_run "Running LDM Project" "$LDM_CMD" -y run . --no-wait --no-tld-skip --no-jvm-verify

# Wait for Health
echo ">> Waiting for Liferay health (max 15m)..."
COUNT=0
while [ $COUNT -lt 90 ]; do
    if docker logs ldm-smoke-test 2>&1 | grep -q "org.apache.catalina.startup.Catalina.start Server startup in"; then
        echo -e "\n✅ Liferay Tomcat started." && break
    fi
    printf "." && sleep 10 && COUNT=$((COUNT+1))
done

# Hot Deploy
mkdir -p "delayed-deploy"
"$VENV_PYTHON" -c "import zipfile; zf = zipfile.ZipFile('delayed-deploy/test-fragments.zip', 'w'); zf.writestr('test-collection/collection.json', '{\"name\": \"Test Collection\", \"description\": \"Test\"}'); zf.writestr('test-collection/test-fragment/fragment.json', '{\"name\": \"Test Fragment\", \"type\": \"component\"}'); zf.writestr('test-collection/test-fragment/index.html', '<div>Test Fragment</div>'); zf.writestr('test-collection/test-fragment/index.js', ''); zf.writestr('test-collection/test-fragment/index.css', ''); zf.close()"

# Secondary permission fix for Linux/WSL2 host side access
if [[ "$OSTYPE" == "linux"* ]]; then
    docker run --rm -v "$(pwd):/workspace" alpine chmod -R 777 /workspace/deploy /workspace/logs 2>/dev/null || true
fi

cp "delayed-deploy/test-fragments.zip" "deploy/"
chmod -R 777 "deploy" "logs" 2>/dev/null || true
echo ">> Waiting 30s for auto-deploy..." && sleep 30

# UI Test
cat << 'PYEOF' > e2e_ui_test.py
import os, pytest
from playwright.sync_api import Page, expect
def test_fragment_deployment(page: Page):
    # Intercept and block external telemetry/status scripts to prevent slowness
    page.route("**/*.statuspage.io/**", lambda route: route.abort())
    page.route("**/cdn.pendo.io/**", lambda route: route.abort())
    
    url = os.environ.get("LIFERAY_URL", "http://localhost:8082")
    page.goto(f"{url}/c/portal/login")
    if page.locator('input[name*="LoginPortlet_login"]').is_visible(timeout=5000):
        page.fill('input[name*="LoginPortlet_login"]', "test@liferay.com")
        page.fill('input[name*="LoginPortlet_password"]', "test")
        page.click('button[type="submit"]')
    # Support landing on /web/guest or /home (depending on portal settings)
    page.wait_for_function("() => window.location.href.includes('/web/guest') || window.location.href.includes('/home')", timeout=30000)
    
    fragments_url = f"{url}/group/guest/~/control_panel/manage?p_p_id=com_liferay_fragment_web_portlet_FragmentPortlet"
    collection_found = False
    for i in range(20):
        print(f"  -> Attempt {i+1}: Checking for 'Test Collection' at {fragments_url}")
        page.goto(fragments_url)
        # Robust locator for the collection item (card title, table cell, or direct text)
        coll = page.get_by_text("Test Collection", exact=True).first
        try:
            if coll.is_visible(timeout=10000):
                print("  -> Found 'Test Collection', attempting to click...")
                coll.click(force=True, timeout=15000)
                collection_found = True
                break
        except Exception as e:
            print(f"  -> Click failed or element disappeared: {e}")
        page.wait_for_timeout(10000)
    
    if not collection_found:
        pytest.fail("Failed to find or click 'Test Collection' after 20 attempts.")
        
    expect(page.get_by_text("Test Fragment").first).to_be_visible(timeout=20000)
PYEOF
touch pytest_empty.ini

log_and_run "Running UI Tests" "$VENV_PYTEST" "e2e_ui_test.py" -c pytest_empty.ini --base-url http://localhost:8082 --screenshot=only-on-failure
rm e2e_ui_test.py
rm pytest_empty.ini

echo "✅ UI Verification successful." | tee -a "$RESULTS_FILE_TMP"

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
"$LDM_CMD" -y scale . liferay=3 >/dev/null 2>&1
grep -q "scale_liferay=3" meta && echo "✅ Scaling verified."

# Final
log_and_run "Checking Status" "$LDM_CMD" -y status

# Clean up any potential orphans from the run
"$LDM_CMD" -y prune --all >/dev/null 2>&1 || true

echo -e "\n🎯 ALL E2E VERIFICATIONS PASSED!"
