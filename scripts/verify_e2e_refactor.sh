#!/bin/bash
set -e

# Comprehensive E2E Binary Verification for LDM
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for macOS (Intel/Silicon) and Linux.

# LDM-#1011: version this script itself (kept in sync with ldm_core/constants.py
# by scripts/release.py on every bump) so a locally-held copy can be checked
# against what actually shipped, rather than guessing from a file mtime -- git
# checkout/pull doesn't preserve original commit timestamps.
# LDM_MAGIC_VERSION: 2.15.33
SCRIPT_VERSION="2.15.33"

TEST_PORT="${LDM_TEST_PORT}"
if [ -z "$TEST_PORT" ]; then
    TEST_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
fi
export TEST_PORT

PROJECT_NAME="ldm-smoke-test-${TEST_PORT}"
COLLISION_PROJECT="collision-test-${TEST_PORT}"
TAG_VAL_PROJECT="tag-val-test-${TEST_PORT}"
TARGET_TEST_NODE="e2e-target-${TEST_PORT}"

KEEP_ARTIFACTS=false
for arg in "$@"; do
    if [ "$arg" == "-k" ] || [ "$arg" == "--keep" ]; then
        KEEP_ARTIFACTS=true
    fi
done

echo "⚡ Starting Standalone Binary Verification on Port ${TEST_PORT}..."

# Store the original directory for final report placement
ORIGINAL_PWD=$(pwd)

LDM_WORKSPACE_DIR_NAME="e2e-work-dir-${TEST_PORT}"
export LDM_WORKSPACE="${LDM_WORKSPACE:-${ORIGINAL_PWD}/${LDM_WORKSPACE_DIR_NAME}}"
export LDM_COMMON_DIR="${LDM_WORKSPACE}/common"

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

INSTALLED_VERSION_RAW=$("$LDM_CMD" --version 2>/dev/null || echo "unknown")

{
    echo "=== LDM BINARY VERIFICATION REPORT ==="
    echo "Timestamp:    $(date)"
    echo "Hostname:     $HOSTNAME"
    echo "Platform:     $PLATFORM_INFO"
    echo "Binary:       $(which "$LDM_CMD")"
} >"$RESULTS_FILE_TMP"

# LDM-#1058: extracted into a named function (still in this same file --
# the real verification workflow copies just this one file onto test rigs
# with no git checkout and no accompanying lib/ directory, see #1049, so
# splitting this into a separate sourced file would break that) so it can be
# tested in isolation (see ldm_core/tests/test_verify_scripts.py) without
# needing a full E2E Docker/ldm run. This logic has had 3 real bugs this
# cycle already (#1047, #1049, #1058).
print_version_banner() {
    local installed_version_raw="$1"
    local installed_version
    installed_version=$(echo "$installed_version_raw" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?' | head -1)

    echo "Version:      $installed_version_raw"
    echo "Script Ver:   $SCRIPT_VERSION"

    if [ -n "$installed_version" ] && [ "$installed_version" != "$SCRIPT_VERSION" ]; then
        echo "⚠️  WARNING: this script (v$SCRIPT_VERSION) does not match the installed ldm binary (v$installed_version)."
        echo "   This may be intentional (verifying a specific older/newer binary), but if not,"
        # LDM-#1049: the real verification workflow copies this script onto
        # plain test rigs with no git checkout at all (upgrade the target
        # machine via `ldm system upgrade --beta`, copy the script over, run
        # it) -- `git checkout` is useless advice there. A raw-file download
        # keyed to the installed binary's own tag needs no git and resolves
        # correctly whether that binary is stable or pre-release.
        echo "   re-pull this script: curl -fsSL \"https://raw.githubusercontent.com/peterrichards-lr/liferay-docker-manager/v$installed_version/scripts/verify_e2e_refactor.sh\" -o scripts/verify_e2e_refactor.sh"
    fi
}

# LDM-#1011 follow-up: tee (not just write) the version lines so both the
# installed binary version and this script's own SCRIPT_VERSION are visible
# on the console as the run starts, not only inside the report afterward.
print_version_banner "$INSTALLED_VERSION_RAW" | tee -a "$RESULTS_FILE_TMP"

{
    if command -v docker &>/dev/null; then
        echo "Docker:    $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "running")"
        if docker compose version &>/dev/null; then
            echo "Compose:   $(docker compose version --short 2>/dev/null || echo "detected")"
        fi
    fi
    echo ""
} >>"$RESULTS_FILE_TMP"

# Helpers
get_hash() {
    if command -v md5sum >/dev/null 2>&1; then echo "$1" | md5sum | cut -c1-8
    elif command -v md5 >/dev/null 2>&1; then echo "$1" | md5 | cut -c1-8
    else date +%s | cut -c5-12; fi
}

capture_logs_on_failure() {
    echo -e "\n--- FAILURE DEBUG LOGS ---" >> "$RESULTS_FILE_TMP"
    for container in liferay-proxy-global liferay-search-global "${PROJECT_NAME}" "${PROJECT_NAME}-db-1"; do
        if docker ps -a | grep -q "$container"; then
            echo ">> Logs for $container:" >> "$RESULTS_FILE_TMP"
            docker logs "$container" --tail 50 >> "$RESULTS_FILE_TMP" 2>&1
        fi
    done
}

# LDM-#1255: Liferay writes its OSGi runtime state (state, modules, configs,
# marketplace, client-extensions, log4j) into the bind-mounted workspace as the
# *container's* UID. On Linux and WSL that ownership is preserved on the host,
# so the invoking user cannot unlink those directories and a plain `rm -rf`
# fails -- previously in silence, leaving an undeletable work dir behind.
#
# LDM already solves this internally: safe_rmtree() retries after
# reclaim_volume_permissions(), which fixes ownership via a throwaway
# container. This applies the same mechanism to the script's own fallback.
remove_workspace_dir() {
    local target="$1"
    [ -d "$target" ] || return 0

    if rm -rf "$target" 2>/dev/null && [ ! -d "$target" ]; then
        return 0
    fi

    local parent base
    parent="$(cd "$(dirname "$target")" && pwd)"
    base="$(basename "$target")"

    echo "ℹ  Permission denied removing ${target}; reclaiming ownership via container..."
    docker run --rm -v "${parent}:/target" alpine:3 rm -rf "/target/${base}" >/dev/null 2>&1 || true

    if [ -d "$target" ]; then
        echo "⚠  Could not remove ${target}. Remove it manually with:"
        echo "     docker run --rm -v \"${parent}:/target\" alpine:3 rm -rf \"/target/${base}\""
        return 1
    fi
    return 0
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
    env_slug=$("$LDM_CMD" system doctor --slug 2>/dev/null | tr -d '\r' | tr ' ' '-')
    local final_name
    final_name="verify-${env_slug:-unknown}-${TIMESTAMP}-${status}.txt"
    
    if [ -d "${LDM_WORKSPACE_DIR_NAME}/${PROJECT_NAME}/test-results" ]; then
        cp -r "${LDM_WORKSPACE_DIR_NAME}/${PROJECT_NAME}/test-results" "${ORIGINAL_PWD}/" 2>/dev/null || true
    fi

    if [ "$status" == "pass" ] && [ -f "$RESULTS_FILE_TMP" ]; then
        echo -e "\n🎯 ALL E2E VERIFICATIONS PASSED!" >> "$RESULTS_FILE_TMP"
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
        # Check if other LDM project containers are running before tearing down global Traefik/proxy
        local other_containers
        other_containers=$(docker ps --format '{{.Names}}' | grep -vE "^(liferay-proxy-global|liferay-search-global|liferay-docker-proxy|${PROJECT_NAME}|${PROJECT_NAME}-db-1)$" || true)
        if [ -z "$other_containers" ]; then
            echo "ℹ  No other LDM projects running. Cleaning up global infrastructure..."
            docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy 2>/dev/null || true
        else
            echo "ℹ  Other LDM projects are running (${other_containers//$'\n'/, }). Skipping global infrastructure cleanup."
        fi

        # LDM-#1255: do not discard the result. Previously this was
        # `>/dev/null 2>&1 || true`, which swallowed stdout, stderr *and* the
        # exit code -- so a failed project removal was completely invisible and
        # only surfaced later as an undeletable directory.
        if ! LDM_WORKSPACE="${LDM_WORKSPACE}" "$LDM_CMD" -y rm "${PROJECT_NAME}" --delete >/dev/null 2>&1; then
            echo "⚠  'ldm rm ${PROJECT_NAME} --delete' failed; the project directory may remain."
        fi

        # Keep the venv if we are in the repository for developer convenience, otherwise delete
        if [ ! -f "pyproject.toml" ]; then
            remove_workspace_dir "${LDM_WORKSPACE}"
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
TEST_VENV="${LDM_WORKSPACE}/.verify-venv"
mkdir -p "$LDM_WORKSPACE"

echo "ℹ  Preparing isolated test environment..."
if [ ! -d "$TEST_VENV" ]; then
    python3 -m venv "$TEST_VENV"
fi

# Determine venv binaries
VENV_PYTHON="${TEST_VENV}/bin/python3"
VENV_PIP="${TEST_VENV}/bin/pip"
VENV_PYTEST="${TEST_VENV}/bin/pytest"

# Install dependencies into venv
if [ ! -f "$VENV_PYTEST" ]; then
    if [ ! -f "$VENV_PIP" ]; then
        echo ">> pip is missing from the virtual environment (common on Debian/Ubuntu). Bootstrapping pip..."
        curl -sS https://bootstrap.pypa.io/get-pip.py | "$VENV_PYTHON"
    fi

    echo ">> Installing test dependencies into virtual environment..."
    "$VENV_PIP" install pytest requests PyYAML --quiet --disable-pip-version-check
fi

# 1. Cleanup & Setup
"$LDM_CMD" -y rm "${PROJECT_NAME}" --delete --infra >/dev/null 2>&1 || true
export LDM_WORKSPACE

# Pre-pull large images to avoid containerd lease timeouts during the timed E2E run
echo "ℹ  Pre-pulling required Docker images..."
docker pull liferay/dxp:2026.q1.7-lts --quiet
docker pull postgres:16.2 --quiet

log_and_run "Initializing Infrastructure" "$LDM_CMD" -y infra setup --search

echo ">> Verifying Custom SSL Port & Recreate..."
log_and_run "Custom SSL Port Setup" "$LDM_CMD" -y infra setup --ssl-port 8443 --force-recreate
if docker inspect liferay-proxy-global | grep -q '"HostPort": "8443"'; then
    echo "✅ Custom SSL Port & Recreate verified."
else
    echo "❌ ERROR: Traefik proxy was not recreated on custom port 8443!" && exit 1
fi


# 2. Guardrails
echo ">> Verifying Dev Guardrails..."
DEV_GUARD_OUT=$(env CI=true "$LDM_CMD" system version --bump patch 2>&1 || true)
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
    SUDO_BLOCK_OUT=$(unshare -r "$LDM_CMD" system version 2>&1 || true)
    if echo "$SUDO_BLOCK_OUT" | grep -q "Do not run LDM with 'sudo'"; then
        echo "✅ Sudo Guard verified (Blocked 'version')."
        
        # Verify that exempted commands are NOT blocked
        if unshare -r "$LDM_CMD" system fix-hosts --help >/dev/null 2>&1; then
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

echo ">> Verifying System Tray (GUI)..."
if [[ "$OSTYPE" == "linux"* ]] && [ -z "$DISPLAY" ]; then
    echo "⚠️  Skipping System Tray check (DISPLAY not set on Linux)."
else
    # Launch ldm tray in background
    "$LDM_CMD" tray > tray.log 2>&1 &
    TRAY_PID=$!
    
    # Wait to see if it crashes
    sleep 5
    if kill -0 $TRAY_PID 2>/dev/null; then
        echo "✅ System Tray application started successfully and remained alive."
        disown $TRAY_PID 2>/dev/null || true
        kill $TRAY_PID 2>/dev/null || true
    else
        echo "❌ ERROR: System Tray application crashed or failed to start."
        cat tray.log
        exit 1
    fi
fi

echo ">> Verifying ldm doctor Dependency Integrity..."
DOCTOR_OUT=$("$LDM_CMD" doctor --detailed --skip-project 2>&1 || true)
if echo "$DOCTOR_OUT" | grep -q "Dependency Integrity"; then
    if echo "$DOCTOR_OUT" | grep -Ei "Dependency Integrity.*(❌|Failed|Missing)"; then
        echo "❌ ERROR: ldm doctor Dependency Integrity check failed:" | tee -a "$RESULTS_FILE_TMP"
        echo "$DOCTOR_OUT" | grep -i "Dependency Integrity" | tee -a "$RESULTS_FILE_TMP"
        exit 1
    else
        echo "✅ ldm doctor Dependency Integrity verified." | tee -a "$RESULTS_FILE_TMP"
    fi
else
    echo "⚠️  Skipping Dependency Integrity check (binary install — no requirements.txt found)."
fi

echo ">> Verifying Project Collision Detection..."
# Use --no-seed to avoid 1GB download for a simple collision test
if ! "$LDM_CMD" -y run "${COLLISION_PROJECT}" --tag 2026.q1.4-lts --port 8099 --no-wait --no-up --no-seed > col_init.log 2>&1; then
    echo "❌ ERROR: Failed to initialize collision-test project." | tee -a "$RESULTS_FILE_TMP"
    tee -a "$RESULTS_FILE_TMP" < col_init.log
    exit 1
fi

mkdir -p "${COLLISION_PROJECT}/nested"
if (cd "${COLLISION_PROJECT}/nested" && echo "n" | env -u GITHUB_ACTIONS -u CI -u GITLAB_CI LDM_ALLOW_ROOT=true "$LDM_CMD" run "./${COLLISION_PROJECT}" --port 8099 --no-wait --no-up --no-seed 2>&1 | grep -qE "Project collision|already registered"); then
    echo "✅ Project Collision verified."
else
    echo "❌ ERROR: Collision detection failed." | tee -a "$RESULTS_FILE_TMP"
    # Print the log of the failed second run for debugging
    (cd "${COLLISION_PROJECT}/nested" && echo "n" | env -u GITHUB_ACTIONS -u CI -u GITLAB_CI LDM_ALLOW_ROOT=true "$LDM_CMD" run "./${COLLISION_PROJECT}" --port 8099 --no-wait --no-up --no-seed 2>&1) | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
"$LDM_CMD" -y rm "${COLLISION_PROJECT}" --delete >/dev/null 2>&1 && rm -rf "${COLLISION_PROJECT}" col_init.log

echo ">> Verifying Tag Validation Guardrail..."
TAG_WARN_OUT=$("$LDM_CMD" -y run "${TAG_VAL_PROJECT}" --tag invalid-tag --port 8099 --no-wait --no-up --no-seed 2>&1 || true)
if echo "$TAG_WARN_OUT" | grep -q "not listed in official Liferay releases"; then
    echo "✅ Tag Validation Guardrail verified."
else
    echo "❌ ERROR: Tag Validation Guardrail failed. Output was: $TAG_WARN_OUT" | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
"$LDM_CMD" -y rm "${TAG_VAL_PROJECT}" --delete >/dev/null 2>&1 && rm -rf "${TAG_VAL_PROJECT}"

echo ">> Verifying Nightly & Master Build Flags (--nightly / --master)..."
NIGHTLY_TEST_PROJ="nightly-test-${TEST_PORT}"
MASTER_TEST_PROJ="master-test-${TEST_PORT}"

"$LDM_CMD" -y run "${NIGHTLY_TEST_PROJ}" --nightly --port 8098 --no-wait --no-up >/dev/null 2>&1
if grep -q "nightly" "${NIGHTLY_TEST_PROJ}/meta"; then
    echo "✅ --nightly flag resolution verified."
else
    echo "❌ ERROR: --nightly flag resolution failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
"$LDM_CMD" -y rm "${NIGHTLY_TEST_PROJ}" --delete >/dev/null 2>&1 && rm -rf "${NIGHTLY_TEST_PROJ}"

"$LDM_CMD" -y run "${MASTER_TEST_PROJ}" --master --port 8097 --no-wait --no-up >/dev/null 2>&1
if grep -q "nightly" "${MASTER_TEST_PROJ}/meta"; then
    echo "✅ --master flag alias verified."
else
    echo "❌ ERROR: --master flag alias failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
"$LDM_CMD" -y rm "${MASTER_TEST_PROJ}" --delete >/dev/null 2>&1 && rm -rf "${MASTER_TEST_PROJ}"

echo ">> Verifying Compute Target Management & Connectivity Probe..."
log_and_run "Target List" "$LDM_CMD" target ls
log_and_run "Target Status (Local)" "$LDM_CMD" target status local

echo ">> Testing Target CRUD Cycle..."
log_and_run "Target Add (Mock Node)" "$LDM_CMD" target add "$TARGET_TEST_NODE" --host 127.0.0.1
if "$LDM_CMD" target ls | grep -q "$TARGET_TEST_NODE"; then
    echo "✅ Target registration verified."
else
    echo "❌ ERROR: Target $TARGET_TEST_NODE not found in registry." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
log_and_run "Target Remove (Mock Node)" "$LDM_CMD" target rm "$TARGET_TEST_NODE"

echo ">> Testing Loopback Subnet Target Registration & Local Context Resolution..."
LOOPBACK_TEST_NODE="loopback-node-${TEST_PORT}"
log_and_run "Target Add (127.0.0.2 Loopback)" "$LDM_CMD" target add "$LOOPBACK_TEST_NODE" --host 127.0.0.2
if "$LDM_CMD" target ls | grep -q "$LOOPBACK_TEST_NODE"; then
    echo "✅ Loopback target registration verified."
else
    echo "❌ ERROR: Target $LOOPBACK_TEST_NODE not found in registry." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
log_and_run "Target Status (Loopback Node)" "$LDM_CMD" target status "$LOOPBACK_TEST_NODE"
log_and_run "Target Remove (Loopback Node)" "$LDM_CMD" target rm "$LOOPBACK_TEST_NODE"

REMOTE_HOST="${LDM_TEST_REMOTE_HOST:-${LDM_REMOTE_TARGET}}"
if [ -n "$REMOTE_HOST" ]; then
    echo ">> Probing Remote Compute Target ($REMOTE_HOST)..."
    REMOTE_NODE_NAME="remote-${TARGET_TEST_NODE}"
    log_and_run "Target Add (Remote Host)" "$LDM_CMD" target add "$REMOTE_NODE_NAME" --host "$REMOTE_HOST"
    REMOTE_STATUS_OUT=$("$LDM_CMD" target status "$REMOTE_NODE_NAME" 2>&1 || true)
    echo "$REMOTE_STATUS_OUT" | tee -a "$RESULTS_FILE_TMP"
    if echo "$REMOTE_STATUS_OUT" | grep -q "ONLINE"; then
        echo "✅ Remote Target Probe verified (ONLINE)."
    else
        echo "⚠️  Remote Target Probe returned OFFLINE or unreachable for $REMOTE_HOST."
    fi
    "$LDM_CMD" target rm "$REMOTE_NODE_NAME" >/dev/null 2>&1 || true
fi

# 3. Project Run
echo "ℹ  Provisioning standalone test project..."
mkdir -p "$LDM_WORKSPACE/${PROJECT_NAME}/files"
cd "$LDM_WORKSPACE/${PROJECT_NAME}"
echo "{\"tag\": \"2026.q1.7-lts\", \"container_name\": \"${PROJECT_NAME}\", \"port\": ${TEST_PORT}, \"db_type\": \"postgresql\"}" > meta

log_and_run "Running LDM Project" "$LDM_CMD" -y run . --no-wait

# Wait for Health
echo "ℹ  Waiting for Liferay health..."
if ! "$LDM_CMD" -y wait . --timeout 600; then
    echo "❌ ERROR: Liferay failed to become healthy. Dumping logs..." | tee -a "$RESULTS_FILE_TMP"
    docker logs "${PROJECT_NAME}" --tail 300
    exit 1
fi

# Hot Deploy
echo ">> Deploying Test OSGi Bundle..."
mkdir -p "delayed-deploy"
# Use a minimal OSGi bundle which the Liferay AutoDeployer natively supports
"$VENV_PYTHON" -c "
import zipfile
with zipfile.ZipFile('delayed-deploy/test-bundle.jar', 'w') as zf:
    zf.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nBundle-ManifestVersion: 2\nBundle-Name: Test Bundle\nBundle-SymbolicName: com.liferay.test.bundle\nBundle-Version: 1.0.0\n')
"

# Fix host-side directory permissions for Linux/WSL2 host access (via Docker)
mkdir -p deploy logs
echo "ℹ  Adjusting host-side permissions on deploy/logs for WSL2/Linux bind mounts..."
chmod -R 777 deploy logs 2>/dev/null || docker run --rm -v "$(pwd):/workspace" alpine chmod -R 777 /workspace/deploy /workspace/logs 2>/dev/null || true

# We test hot-deploy via the LDM deploy command
log_and_run "Deploying artifact" "$LDM_CMD" -y deploy . "delayed-deploy/test-bundle.jar"
echo ">> Waiting for auto-deploy processing (up to 10m; WSL2 filesystem sync may introduce slight delays)..."

# Verify Hot Deploy via Logs with a polling loop
HOT_DEPLOY_SUCCESS=false
for _ in {1..60}; do
    if docker logs "${PROJECT_NAME}" --tail 200 2>&1 | grep -q "STARTED com.liferay.test.bundle"; then
        echo "✅ Hot Deploy verified." | tee -a "$RESULTS_FILE_TMP"
        HOT_DEPLOY_SUCCESS=true
        break
    fi
    printf "." && sleep 10
done

if [ "$HOT_DEPLOY_SUCCESS" = false ]; then
    echo -e "\n❌ ERROR: Hot Deploy failed. Test Bundle did not start." | tee -a "$RESULTS_FILE_TMP"
    docker logs "${PROJECT_NAME}" --tail 100
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

echo ">> Verifying Legacy Command Translation..."
if "$LDM_CMD" doctor --help >/dev/null && "$LDM_CMD" infra-setup --help >/dev/null; then
    echo "✅ Legacy command translation verified."
else
    echo "❌ ERROR: Legacy command translation failed." && exit 1
fi

echo ">> Verifying Share Command Layout..."
if "$LDM_CMD" share --help >/dev/null && \
   "$LDM_CMD" share start --help >/dev/null && \
   "$LDM_CMD" share status --help >/dev/null && \
   "$LDM_CMD" share stop --help >/dev/null; then
    echo "✅ Share command layout verified."
else
    echo "❌ ERROR: Share command layout verification failed." && exit 1
fi

# UX & Scaling
echo ">> Verifying Cascading Defaults..."
"$LDM_CMD" config defaults test_key test_value >/dev/null
if "$LDM_CMD" config defaults | grep -q "test_key.*test_value.*User"; then
    echo "✅ Set User Default verified."
else
    echo "❌ ERROR: Set User Default failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
"$LDM_CMD" config defaults --remove test_key >/dev/null
if ! "$LDM_CMD" config defaults | grep -q "test_key.*test_value.*User"; then
    echo "✅ Remove User Default verified."
else
    echo "❌ ERROR: Remove User Default failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi

echo ">> Verifying Env Sync..."
"$LDM_CMD" config env . TEST_SECRET=supersecret123 >/dev/null
if grep -q "TEST_SECRET=supersecret123" docker-compose.yml; then echo "✅ Env Sync verified."; else echo "❌ ERROR: Env Sync validation failed." && exit 1; fi

echo ">> Verifying Redaction..."
if "$LDM_CMD" status REDACT_SECRET=hidden 2>&1 | grep -q "REDACT_SECRET=\[REDACTED\]"; then echo "✅ Redaction verified."; else echo "❌ ERROR: Redaction validation failed." && exit 1; fi

echo ">> Verifying Scaling..."
log_and_run "Scaling Liferay" "$LDM_CMD" -y scale . liferay=3 --no-run
if grep -Eq "scale_liferay.*3" meta; then echo "✅ Scaling verified."; else echo "❌ ERROR: Scaling validation failed." && exit 1; fi

# Scale is 3, so --instance 4 should be invalid, and --instance 2 should look for the container
if "$LDM_CMD" logs . --instance 4 2>&1 | grep -q "Invalid instance index 4" && \
   "$LDM_CMD" logs . --instance 2 2>&1 | grep -q "Container '${PROJECT_NAME}-liferay-2' not found"; then
    echo "✅ logs --instance routing verified."
else
    echo "❌ ERROR: logs --instance routing validation failed." && exit 1
fi

echo ">> Verifying Trace Log and Logs Export..."
if [ -f "$HOME/.ldm/last-command.log" ]; then
    echo "✅ Trace Log (last-command.log) verified."
else
    echo "❌ ERROR: Trace Log file missing." && exit 1
fi

log_and_run "Scaling Liferay back to 1 for logs export check" "$LDM_CMD" -y scale . liferay=1 --no-run
log_and_run "Starting project for logs export check" "$LDM_CMD" -y run . --no-wait
log_and_run "Exporting project logs" "$LDM_CMD" logs . --export
EXPORT_FILE=""
for f in *.log; do
    if [ -f "$f" ]; then
        EXPORT_FILE="$f"
        break
    fi
done
if [ -n "$EXPORT_FILE" ]; then
    echo "✅ Logs Export verified ($EXPORT_FILE)."
    rm "$EXPORT_FILE"
else
    echo "❌ ERROR: Logs Export file not generated." && exit 1
fi

echo ">> Verifying ldm start UX fast-fail..."
START_FAIL_OUT=$("$LDM_CMD" start fake-non-existent-project 2>&1 || true)
if echo "$START_FAIL_OUT" | grep -q "Project not found or not initialized"; then
    echo "✅ ldm start fast-fail verified."
else
    echo "❌ ERROR: ldm start fast-fail message not found. Output was: $START_FAIL_OUT" && exit 1
fi

echo ">> Verifying ldm run reconfigure UX message..."
RUN_RECONFIG_OUT=$("$LDM_CMD" -y run . --no-wait --info 2>&1 || true)
if echo "$RUN_RECONFIG_OUT" | grep -q "already exists and this command will reconfigure it"; then
    echo "✅ ldm run reconfigure UX message verified."
else
    echo "❌ ERROR: ldm run reconfigure message not found. Output was: $RUN_RECONFIG_OUT" && exit 1
fi

echo ">> Verifying Safe SELECT SQL Query..."
DB_QUERY_OUT=$("$LDM_CMD" db query . -s "SELECT 1 as test_val;" --allow-db-query 2>&1 || true)
if echo "$DB_QUERY_OUT" | grep -q "test_val"; then
    echo "✅ Safe SELECT SQL Query verified."
else
    echo "❌ ERROR: Safe SELECT SQL Query failed. Output was: $DB_QUERY_OUT" && exit 1
fi

echo ">> Verifying Properties Override Cascade & Reset..."
log_and_run "Stopping project to release file locks" "$LDM_CMD" -y stop .
mkdir -p "$LDM_WORKSPACE/common"
echo "test.override.prop=456" > "$LDM_WORKSPACE/common/portal-ext.properties"
echo "test.override.prop=123 # !important" >> files/portal-ext.properties
log_and_run "Rebuilding properties" "$LDM_CMD" config rebuild-properties .
if grep -q "test.override.prop=123" files/portal-ext.properties; then
    echo "✅ Properties Override Cascade verified (rebuild)."
else
    echo "❌ ERROR: Properties Override Cascade rebuild failed." && exit 1
fi

log_and_run "Resetting properties" "$LDM_CMD" config reset-properties .
if grep -q "test.override.prop=456" files/portal-ext.properties && ! grep -q "123" files/portal-ext.properties; then
    echo "✅ Properties Override Reset verified."
else
    echo "❌ ERROR: Properties Override Reset failed." && exit 1
fi

# Clean up temporary test files
rm -rf "$LDM_WORKSPACE/common"

echo ">> Verifying --json Output Schemas (#1091 / #1115)..."
# NB stderr is deliberately NOT merged into these captures: `2>&1` would fold any
# warning into the payload and make json.loads() fail, reporting a schema break
# that never happened.
LIST_JSON_OUT=$("$LDM_CMD" list --json 2>/dev/null || true)
if "$VENV_PYTHON" -c "
import json, sys
data = json.loads(sys.stdin.read())
assert isinstance(data, list), 'list --json must return array'
# Must NOT be vacuous: a project exists at this point in the run, so an empty
# array means the contract is broken, not that there is nothing to check.
assert data, 'list --json returned an empty array; expected the test project'
for item in data:
    for key in ('http_ready', 'http_status', 'db_unhealthy'):
        assert key in item, f'{key} missing from list --json entry {item.get(\"project\")!r}'
" <<< "$LIST_JSON_OUT"; then
    echo "✅ ldm list --json schema verified."
else
    echo "❌ ERROR: ldm list --json schema verification failed. Output: $LIST_JSON_OUT" | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi

# `status --json` is shaped differently from `list --json` (see
# ldm_core/diagnostics/info.py: run_status vs run_list). It returns an object
# with `infrastructure` and `projects`, and the health keys live on each entry
# in `projects` -- not at the top level. It also does NOT emit `db_unhealthy`,
# which is a `list`-only field.
STATUS_JSON_OUT=$("$LDM_CMD" status . --json 2>/dev/null || true)
if "$VENV_PYTHON" -c "
import json, sys
data = json.loads(sys.stdin.read())
assert isinstance(data, dict), 'status --json must return an object'
assert 'projects' in data, 'projects missing from status --json'
projects = data['projects']
assert isinstance(projects, list), 'status --json projects must be an array'
assert projects, 'status --json returned no projects; expected the test project'
for item in projects:
    for key in ('http_ready', 'http_status'):
        assert key in item, f'{key} missing from status --json project {item.get(\"project\")!r}'
" <<< "$STATUS_JSON_OUT"; then
    echo "✅ ldm status --json schema verified."
else
    echo "❌ ERROR: ldm status --json schema verification failed. Output: $STATUS_JSON_OUT" | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi

echo ">> Verifying Idempotent Exit Code 5 (#1094)..."
# Exit 5 is only returned in non-interactive mode (ldm_core/pipelines/run.py:246);
# interactively LDM prompts instead, so `-y` is required.
#
# The project is STOPPED at this point -- "Stopping project to release file
# locks" above shuts it down and nothing restarts it. So the first `up`
# legitimately starts it and returns 0; the idempotent contract only applies to
# a second invocation, when the project is genuinely already running. Asserting
# 5 on the first call fails on every platform, and tolerating "5 or 0" instead
# would verify nothing at all, since 0 is the only value the first call can
# return.
log_and_run "Starting project for idempotency check" "$LDM_CMD" -y up .
set +e
"$LDM_CMD" -y up . >/dev/null 2>&1
UP_EXIT_CODE=$?
set -e
if [ "$UP_EXIT_CODE" -eq 5 ]; then
    echo "✅ Idempotent Exit Code 5 verified."
else
    echo "❌ ERROR: expected exit code 5 (Idempotent No-Op) from 'ldm -y up' on an already-running project, got $UP_EXIT_CODE." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi


echo ">> Verifying Client Extension deploy & staging (#1257 / #1262)..."
# LDM-#1262: this check previously passed a *directory* (`deploy . synthetic-cx/`).
# `cmd_deploy` only recognises trailing arguments that are existing *files* with a
# known extension (.jar/.war -> osgi/modules, .zip -> the CX sync); anything else
# falls through to the service-name branch and becomes `docker compose up -d
# synthetic-cx/`, which fails because no such service exists. A client extension
# is deployed as a ZIP, not a directory.
#
# Verified against the real command before landing (LDM-#1262): the ZIP form
# performs the documented 3-step sync in
# ldm_core/workspace/hydration.py:_sync_cx_artifact --
#   1. copy   ZIP -> client-extensions/<name>.zip
#   2. expand     -> client-extensions/<stem>/
#   3. MOVE   ZIP -> osgi/client-extensions/<name>.zip
# Step 3 is a move, so the intermediate copy must be gone afterwards. Asserting
# that is what distinguishes a completed sync from one that only got through
# step 1 -- the removed block asserted neither, while claiming "Staging" in its
# heading.
CX_NAME="synthetic-cx"
rm -rf "cx-build" "${CX_NAME}.zip"
mkdir -p "cx-build/${CX_NAME}"
cat > "cx-build/${CX_NAME}/client-extension.yaml" <<CXEOF
${CX_NAME}:
    name: Synthetic CX
    type: customElement
    url: ${CX_NAME}.js
CXEOF
echo 'console.log("synthetic");' > "cx-build/${CX_NAME}/${CX_NAME}.js"
"$VENV_PYTHON" -c "
import shutil, sys
shutil.make_archive(sys.argv[1], 'zip', sys.argv[2])
" "${CX_NAME}" "cx-build/${CX_NAME}"

log_and_run "Deploying Synthetic CX" "$LDM_CMD" -y deploy . "${CX_NAME}.zip"

CX_STAGED=true
if [ ! -f "osgi/client-extensions/${CX_NAME}.zip" ]; then
    echo "❌ ERROR: CX was not staged to osgi/client-extensions/${CX_NAME}.zip." | tee -a "$RESULTS_FILE_TMP"
    CX_STAGED=false
fi
if [ ! -f "client-extensions/${CX_NAME}/client-extension.yaml" ]; then
    echo "❌ ERROR: CX was not expanded to client-extensions/${CX_NAME}/." | tee -a "$RESULTS_FILE_TMP"
    CX_STAGED=false
fi
if [ -f "client-extensions/${CX_NAME}.zip" ]; then
    echo "❌ ERROR: intermediate client-extensions/${CX_NAME}.zip still present -- step 3 (move to osgi/client-extensions) did not complete." | tee -a "$RESULTS_FILE_TMP"
    CX_STAGED=false
fi

if [ "$CX_STAGED" = true ]; then
    echo "✅ Client Extension deploy & staging verified."
else
    echo "-- client-extensions/ --"; ls -la "client-extensions" 2>&1 || true
    echo "-- osgi/client-extensions/ --"; ls -la "osgi/client-extensions" 2>&1 || true
    exit 1
fi

rm -rf "cx-build"


# Final
log_and_run "Checking Status" "$LDM_CMD" -y status

# Clean up any potential orphans from the run
"$LDM_CMD" -y system prune >/dev/null 2>&1 || true

echo -e "\n🎯 ALL E2E VERIFICATIONS PASSED!"
