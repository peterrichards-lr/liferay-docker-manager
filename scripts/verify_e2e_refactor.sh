#!/bin/bash
set -e

# Comprehensive E2E Binary Verification for LDM
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for macOS (Intel/Silicon) and Linux.

# LDM-#1011: version this script itself (kept in sync with ldm_core/constants.py
# by scripts/release.py on every bump) so a locally-held copy can be checked
# against what actually shipped, rather than guessing from a file mtime -- git
# checkout/pull doesn't preserve original commit timestamps.
# LDM_MAGIC_VERSION: 2.17.0
SCRIPT_VERSION="2.17.0"

TEST_PORT="${LDM_TEST_PORT}"
if [ -z "$TEST_PORT" ]; then
    TEST_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
fi
export TEST_PORT

PROJECT_NAME="ldm-smoke-test-${TEST_PORT}"
COLLISION_PROJECT="collision-test-${TEST_PORT}"
TAG_VAL_PROJECT="tag-val-test-${TEST_PORT}"
TARGET_TEST_NODE="e2e-target-${TEST_PORT}"
ANNOUNCE_TEST_NODE="announce-node-${TEST_PORT}"
ANNOUNCE_TEST_PROJ="announce-proj-${TEST_PORT}"
PORTCONFLICT_PROJ="portconflict-${TEST_PORT}"
PORT_HOLDER="ldm-e2e-port-holder-${TEST_PORT}"
# Kibana publishes this host port unconditionally (composer.py
# _build_kibana_service). It is the LDM-#1350 lever -- see that check below.
KIBANA_HOST_PORT=5601

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

# LDM-#1383: the artefacts of the two checks below are torn down inline as
# soon as each check finishes, and again from the exit trap. Leaving a Docker
# context behind is not merely untidy: every later `ldm list`/`ldm status` in
# this suite would then resolve `docker --context` against an unroutable
# TEST-NET-1 address and block on an SSH connect timeout, turning one failed
# check into a suite that appears to hang.
cleanup_1383_artifacts() {
    "$LDM_CMD" -y target rm "$ANNOUNCE_TEST_NODE" >/dev/null 2>&1 || true
    docker context rm "$ANNOUNCE_TEST_NODE" >/dev/null 2>&1 || true
    "$LDM_CMD" -y rm "$ANNOUNCE_TEST_PROJ" --delete >/dev/null 2>&1 || true
    rm -rf "${LDM_WORKSPACE:?}/${ANNOUNCE_TEST_PROJ}"
    docker rm -f "$PORT_HOLDER" >/dev/null 2>&1 || true
    "$LDM_CMD" -y rm "$PORTCONFLICT_PROJ" --delete >/dev/null 2>&1 || true
    rm -rf "${LDM_WORKSPACE:?}/${PORTCONFLICT_PROJ}"
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

    cleanup_1383_artifacts

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
# LDM-#1327: a verification verdict must reach the REPORT, not just the console.
#
# Until now no check's success line did. `log_and_run` tee'd the step header and
# the command's own output into $RESULTS_FILE_TMP, but every "verified" line in
# this suite was a bare `echo` -- so the durable record showed which steps ran
# and what ldm printed, never which assertions actually passed. The entire
# verdict lived in the `-pass` suffix of the filename, derived from the script's
# exit code, which meant a reader had to know that this script aborts on failure
# to interpret its own report.
#
# Failure paths already tee; it was only success that was invisible.
report_ok() {
    echo "$1" | tee -a "$RESULTS_FILE_TMP"
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

# LDM-#1406: refuse to start without room to finish.
#
# A run that exhausts the disk fails somewhere in the middle, and surfaces as
# whatever broke first -- a PostgreSQL PANIC, an Elasticsearch write block, a
# truncated image layer -- rather than as "you are out of disk". The report it
# produces then reads as a defect finding, and a verification report is the
# project's honest record of what was tested.
#
# Asked of DOCKER, not the host. On Docker Desktop/Colima/OrbStack the engine's
# storage lives inside a VM with its own, far smaller disk; a host-side `df`
# would pass on exactly the machines most likely to fail. Measured on one
# developer machine mid-verification: host 109.2 GB free, Docker VM 12.5 GB.
# This is the same reasoning as Doctor._check_absolute_disk_space (LDM-#1095),
# and using `docker run alpine df` keeps the .sh and .ps1 implementations
# identical rather than needing two host-specific ones.
#
# Provisional threshold: the images alone are ~7.5 GB (liferay/dxp ~5.3,
# postgres ~0.7, elasticsearch ~1.5) before the running stack grows. Override
# with LDM_VERIFY_MIN_DISK_GB when you know better than this default.
MIN_DISK_GB="${LDM_VERIFY_MIN_DISK_GB:-10}"
echo "ℹ  Checking Docker has room to finish (need ${MIN_DISK_GB} GB)..."
DISK_FREE_KB=$(docker run --rm alpine df -P -k / 2>/dev/null | awk 'NR==2 {print $4}')
if [ -z "$DISK_FREE_KB" ]; then
    echo "⚠️  Could not determine Docker's free space; continuing without the check." | tee -a "$RESULTS_FILE_TMP"
else
    DISK_FREE_GB=$((DISK_FREE_KB / 1024 / 1024))
    if [ "$DISK_FREE_GB" -lt "$MIN_DISK_GB" ]; then
        echo "❌ ERROR: not enough disk space for a verification run." | tee -a "$RESULTS_FILE_TMP"
        echo "   Docker has ${DISK_FREE_GB} GB free; this run needs about ${MIN_DISK_GB} GB." | tee -a "$RESULTS_FILE_TMP"
        echo "   (The host may report far more -- Docker's storage is inside its own VM.)" | tee -a "$RESULTS_FILE_TMP"
        echo "" | tee -a "$RESULTS_FILE_TMP"
        echo "   Free some space, then re-run:" | tee -a "$RESULTS_FILE_TMP"
        echo "     ldm prune --seeds --samples     # reclaim LDM seed and sample archives" | tee -a "$RESULTS_FILE_TMP"
        echo "     ldm prune --all                 # also images, volumes and build cache" | tee -a "$RESULTS_FILE_TMP"
        echo "     docker system prune -a          # everything Docker considers unused" | tee -a "$RESULTS_FILE_TMP"
        echo "" | tee -a "$RESULTS_FILE_TMP"
        echo "   Refusing before pulling anything, so no half-finished report is written." | tee -a "$RESULTS_FILE_TMP"
        exit 1
    fi
    echo "✅ Docker has ${DISK_FREE_GB} GB free."
fi

# Pre-pull large images to avoid containerd lease timeouts during the timed E2E run
echo "ℹ  Pre-pulling required Docker images..."
docker pull liferay/dxp:2026.q1.7-lts --quiet
docker pull postgres:16.2 --quiet

log_and_run "Initializing Infrastructure" "$LDM_CMD" -y infra setup --search

echo ">> Verifying Custom SSL Port & Recreate..."
log_and_run "Custom SSL Port Setup" "$LDM_CMD" -y infra setup --ssl-port 8443 --force-recreate
if docker inspect liferay-proxy-global | grep -q '"HostPort": "8443"'; then
    report_ok "✅ Custom SSL Port & Recreate verified."
else
    echo "❌ ERROR: Traefik proxy was not recreated on custom port 8443!" && exit 1
fi


# 2. Guardrails
echo ">> Verifying Dev Guardrails..."
DEV_GUARD_OUT=$(env CI=true "$LDM_CMD" system version --bump patch 2>&1 || true)
if echo "$DEV_GUARD_OUT" | grep -qE "Error: Developer utility requires LDM_DEV_MODE=true|Action restricted"; then
    report_ok "✅ Dev Guardrails verified."
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
        report_ok "✅ Sudo Guard verified (Blocked 'version')."
        
        # Verify that exempted commands are NOT blocked
        if unshare -r "$LDM_CMD" system fix-hosts --help >/dev/null 2>&1; then
            report_ok "✅ Sudo Guard verified (Allowed 'fix-hosts')."
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
        report_ok "✅ System Tray application started successfully and remained alive."
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
        report_ok "✅ ldm doctor Dependency Integrity verified." | tee -a "$RESULTS_FILE_TMP"
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
    report_ok "✅ Project Collision verified."
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
    report_ok "✅ Tag Validation Guardrail verified."
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
    report_ok "✅ --nightly flag resolution verified."
else
    echo "❌ ERROR: --nightly flag resolution failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
"$LDM_CMD" -y rm "${NIGHTLY_TEST_PROJ}" --delete >/dev/null 2>&1 && rm -rf "${NIGHTLY_TEST_PROJ}"

"$LDM_CMD" -y run "${MASTER_TEST_PROJ}" --master --port 8097 --no-wait --no-up >/dev/null 2>&1
if grep -q "nightly" "${MASTER_TEST_PROJ}/meta"; then
    report_ok "✅ --master flag alias verified."
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
    report_ok "✅ Target registration verified."
else
    echo "❌ ERROR: Target $TARGET_TEST_NODE not found in registry." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
log_and_run "Target Remove (Mock Node)" "$LDM_CMD" target rm "$TARGET_TEST_NODE"

echo ">> Testing Loopback Subnet Target Registration & Local Context Resolution..."
LOOPBACK_TEST_NODE="loopback-node-${TEST_PORT}"
log_and_run "Target Add (127.0.0.2 Loopback)" "$LDM_CMD" target add "$LOOPBACK_TEST_NODE" --host 127.0.0.2
if "$LDM_CMD" target ls | grep -q "$LOOPBACK_TEST_NODE"; then
    report_ok "✅ Loopback target registration verified."
else
    echo "❌ ERROR: Target $LOOPBACK_TEST_NODE not found in registry." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
log_and_run "Target Status (Loopback Node)" "$LDM_CMD" target status "$LOOPBACK_TEST_NODE"
log_and_run "Target Remove (Loopback Node)" "$LDM_CMD" target rm "$LOOPBACK_TEST_NODE"

# --- LDM-#1383: E2E cover for the v2.18.0 remote-node / port-conflict UX ----
#
# #1341, #1345 and #1350 shipped with unit tests only, each deferred on the
# same principle: an assertion must depend on nothing the script cannot
# control. That principle stands. What #1383 got wrong was assuming the only
# way to satisfy it was a real remote node and a real timing race. Two of the
# three have a deterministic lever that touches no network at all.
#
# #1345 genuinely does not, and is left to its unit/integration tests. See
# the note after the second check.

echo ">> Verifying Remote Compute Node Announcement (LDM-#1341)..."
#
# No remote node is needed. announce_remote_targets() (ldm_core/utils.py)
# decides remoteness via DockerService.get_docker_cmd_prefix(), which consults
# only the LDM target registry -- name != "local" AND host outside
# 127.0.0.0/8 -- and never checks that the Docker context actually exists.
#
# So: register a target on TEST-NET-1 (RFC 5737, permanently unroutable),
# delete the Docker context that `target add` created, and point a project at
# it. The project is classified remote and therefore announced, while
# `docker --context` fails instantly with "context not found" rather than
# opening an SSH connection. Measured end to end at 0s.
#
# Observed to fail against the unfixed code before being committed: at
# cfcde7c9^ (the commit before #1341) announce_remote_targets does not exist
# and `ldm list` prints no such line, so this check is not vacuous.
mkdir -p "${LDM_WORKSPACE}/${ANNOUNCE_TEST_PROJ}/files"
cat > "${LDM_WORKSPACE}/${ANNOUNCE_TEST_PROJ}/meta" <<EOF
{"tag": "2026.q1.7-lts", "container_name": "${ANNOUNCE_TEST_PROJ}", "port": 8099, "db_type": "postgresql", "target": "${ANNOUNCE_TEST_NODE}"}
EOF
log_and_run "Target Add (TEST-NET-1 Remote Node)" "$LDM_CMD" -y target add "$ANNOUNCE_TEST_NODE" --host 192.0.2.10
docker context rm "$ANNOUNCE_TEST_NODE" >/dev/null 2>&1 || true

# Refuse to continue rather than hang: with the context still present, the
# `ldm list` below would dial 192.0.2.10 over SSH and block.
if docker context ls --format '{{.Name}}' 2>/dev/null | grep -qx "$ANNOUNCE_TEST_NODE"; then
    echo "❌ ERROR: could not remove Docker context '${ANNOUNCE_TEST_NODE}'." | tee -a "$RESULTS_FILE_TMP"
    echo "   Refusing to continue: 'ldm list' would block on an SSH connect to 192.0.2.10." | tee -a "$RESULTS_FILE_TMP"
    cleanup_1383_artifacts
    exit 1
fi

ANNOUNCE_OUT=$("$LDM_CMD" list 2>&1 || true)
echo "$ANNOUNCE_OUT" | tee -a "$RESULTS_FILE_TMP"
if echo "$ANNOUNCE_OUT" | grep -q "a remote compute node" &&
    echo "$ANNOUNCE_OUT" | grep -q "${ANNOUNCE_TEST_PROJ} -> ${ANNOUNCE_TEST_NODE}"; then
    report_ok "✅ Remote compute node announced up front, naming project -> node (LDM-#1341)."
else
    echo "❌ ERROR: 'ldm list' did not announce the remote node before resolving it." | tee -a "$RESULTS_FILE_TMP"
    echo "   Expected a line naming '${ANNOUNCE_TEST_PROJ} -> ${ANNOUNCE_TEST_NODE}'." | tee -a "$RESULTS_FILE_TMP"
    cleanup_1383_artifacts
    exit 1
fi

# LDM-#1093: --json is a machine-readable contract, so the announcement is
# deliberately suppressed there. Asserted because a future edit that moves the
# announcement earlier would silently corrupt every --json consumer.
ANNOUNCE_JSON_OUT=$("$LDM_CMD" list --json 2>&1 || true)
if echo "$ANNOUNCE_JSON_OUT" | grep -q "a remote compute node"; then
    echo "❌ ERROR: the remote-node announcement leaked into 'ldm list --json'." | tee -a "$RESULTS_FILE_TMP"
    cleanup_1383_artifacts
    exit 1
fi
report_ok "✅ Announcement correctly suppressed under --json (LDM-#1093)."
cleanup_1383_artifacts

echo ">> Verifying Late Port Conflict Guidance (LDM-#1350)..."
#
# The late check in ComposerStage fires when a port written into the generated
# docker-compose.yml is taken by the time compose validation runs. #1383
# assumed reproducing it meant racing the seed download that sits between the
# pre-flight check and this one. It does not: the pre-flight only covers the
# Liferay port and custom_containers, so any *other* compose-published port
# reaches the late check with no race whatsoever.
#
# Kibana is the lever -- _build_kibana_service publishes a hardcoded 5601 and
# the pre-flight never looks at it. Enabling it via meta costs nothing: the
# run dies at the port check, several stages before anything is started, so no
# Kibana container is ever created and no image is pulled.
#
# Note this cannot be done with the Liferay port instead. Under -y the
# pre-flight calls UI.die() on a taken port (handlers/base.py) and exits 1
# long before ComposerStage -- observed. The obvious "bind 8080 and run"
# approach asserts the wrong check.
#
# Observed to fail against the unfixed code: at d5749b38^ the same conflict
# prints "Please stop the service currently using port 5601" and no tip. The
# exit code was already 4 before #1350 (from #996), so the tip -- not the exit
# code -- is what makes this check non-vacuous. Both are asserted.
docker rm -f "$PORT_HOLDER" >/dev/null 2>&1 || true
docker run -d --name "$PORT_HOLDER" -p "${KIBANA_HOST_PORT}:80" alpine sleep 300 >/dev/null 2>&1 || true

# `docker run -d` returns before the published port is necessarily accepting
# connections, and LDM's check_port() treats a refused connect as "free". Wait
# for the bind to actually be live rather than assuming it.
PORT_HELD=false
for _ in $(seq 1 30); do
    if "$VENV_PYTHON" -c "
import socket, sys
s = socket.socket()
s.settimeout(1)
sys.exit(0 if s.connect_ex(('127.0.0.1', ${KIBANA_HOST_PORT})) == 0 else 1)
" 2>/dev/null; then
        PORT_HELD=true
        break
    fi
    sleep 1
done

if [ "$PORT_HELD" != "true" ]; then
    echo "❌ ERROR: could not occupy port ${KIBANA_HOST_PORT}; the LDM-#1350 check cannot run." | tee -a "$RESULTS_FILE_TMP"
    echo "   Refusing to skip silently: an assertion that quietly stops running is worse than a red one." | tee -a "$RESULTS_FILE_TMP"
    cleanup_1383_artifacts
    exit 1
fi

mkdir -p "${LDM_WORKSPACE}/${PORTCONFLICT_PROJ}/files"
cat > "${LDM_WORKSPACE}/${PORTCONFLICT_PROJ}/meta" <<EOF
{"tag": "2026.q1.7-lts", "container_name": "${PORTCONFLICT_PROJ}", "port": 8097, "db_type": "postgresql", "search_kibana_enabled": "true"}
EOF

set +e
PORTCONFLICT_OUT=$("$LDM_CMD" -y run "${LDM_WORKSPACE}/${PORTCONFLICT_PROJ}" --no-wait 2>&1)
PORTCONFLICT_RC=$?
set -e
echo "$PORTCONFLICT_OUT" | tee -a "$RESULTS_FILE_TMP"

if [ "$PORTCONFLICT_RC" -ne 4 ]; then
    echo "❌ ERROR: late port conflict exited ${PORTCONFLICT_RC}, expected 4 (Orchestration/Deployment Error)." | tee -a "$RESULTS_FILE_TMP"
    cleanup_1383_artifacts
    exit 1
fi
if ! echo "$PORTCONFLICT_OUT" | grep -q "Port conflict detected: Port ${KIBANA_HOST_PORT}"; then
    echo "❌ ERROR: no port-conflict message naming port ${KIBANA_HOST_PORT}." | tee -a "$RESULTS_FILE_TMP"
    cleanup_1383_artifacts
    exit 1
fi
# LDM-#1397: this deliberately uses a FIXED-port service (kibana publishes a
# literal 5601 in the compose builder), so the correct tip is *not* the
# next-free-port promise. #1350 originally emitted that promise for every
# service, which was false here -- a re-run regenerates the same literal and
# fails identically. Asserting the promise would now re-enshrine that bug.
if ! echo "$PORTCONFLICT_OUT" | grep -Eq "has a fixed port"; then
    echo "❌ ERROR: the tip did not say the port is fixed (LDM-#1397)." | tee -a "$RESULTS_FILE_TMP"
    echo "   kibana's port is a literal in the compose builder, so a re-run cannot" | tee -a "$RESULTS_FILE_TMP"
    echo "   move it. Promising a re-run would send the user round a loop that" | tee -a "$RESULTS_FILE_TMP"
    echo "   cannot terminate." | tee -a "$RESULTS_FILE_TMP"
    cleanup_1383_artifacts
    exit 1
fi
if echo "$PORTCONFLICT_OUT" | grep -Eq "the pre-flight check will select port [0-9]+ instead"; then
    echo "❌ ERROR: the tip promised a pre-flight re-select for a fixed-port service (LDM-#1397)." | tee -a "$RESULTS_FILE_TMP"
    cleanup_1383_artifacts
    exit 1
fi
report_ok "✅ Late port conflict exits 4 and gives honest advice for a fixed-port service (LDM-#1350/#1397)."
cleanup_1383_artifacts

# LDM-#1345 (diagnose an SSH failure instead of dumping the connect blob) is
# deliberately NOT asserted here; the deferral stays tracked in #1398, the
# successor issue opened when #1383 was closed.
#
# Unlike the two checks above, its trigger cannot be faked: the diagnosis is
# produced from real `ssh`/`docker context` stderr, so provoking it means
# actually attempting and failing a connection. Every honest trigger has a
# duration this script does not own -- a TEST-NET-1 address fails via a
# connect timeout set by the host network stack, a `.invalid` hostname depends
# on the resolver, and "connection refused" depends on whether the machine
# happens to run sshd. An assertion whose runtime is decided by the network is
# the flaky release-gate check #1383 exists to avoid.
#
# It is covered by TestRemoteContextFailureDiagnosis in
# ldm_core/tests/test_utils.py, which drives the real diagnosis function with
# captured stderr from a genuine failure, plus an integration test over the
# real CommandRunner path that was confirmed to fail against the unfixed
# wiring.

REMOTE_HOST="${LDM_TEST_REMOTE_HOST:-${LDM_REMOTE_TARGET}}"
if [ -n "$REMOTE_HOST" ]; then
    echo ">> Probing Remote Compute Target ($REMOTE_HOST)..."
    REMOTE_NODE_NAME="remote-${TARGET_TEST_NODE}"
    log_and_run "Target Add (Remote Host)" "$LDM_CMD" target add "$REMOTE_NODE_NAME" --host "$REMOTE_HOST"
    REMOTE_STATUS_OUT=$("$LDM_CMD" target status "$REMOTE_NODE_NAME" 2>&1 || true)
    echo "$REMOTE_STATUS_OUT" | tee -a "$RESULTS_FILE_TMP"
    if echo "$REMOTE_STATUS_OUT" | grep -q "ONLINE"; then
        report_ok "✅ Remote Target Probe verified (ONLINE)."
    else
        echo "⚠️  Remote Target Probe returned OFFLINE or unreachable for $REMOTE_HOST."
    fi
    "$LDM_CMD" target rm "$REMOTE_NODE_NAME" >/dev/null 2>&1 || true
fi

# 3. Project Run
# LDM-#1302: a leftover project from a previous run sends `ldm run` down the
# `already exists -> reconfigure` path instead of a fresh provision. That path
# then reaches verify_runtime_environment()'s `docker run ... alpine` mount
# probe, which has no timeout -- so a stalled pull or wedged mount hangs
# indefinitely with nothing printed. Observed on WSL2 during v2.16.0-pre.3.
#
# The name is stable whenever LDM_TEST_PORT is exported, so this recurs on any
# machine where an earlier run died -- which happened repeatedly this cycle. A
# fresh CI runner never hits it, so the reconfigure path is effectively
# untested. Removing here makes re-runs idempotent; the suite already deletes
# this project on exit, so doing it on entry is consistent, not destructive.
if "$LDM_CMD" list 2>/dev/null | grep -q "${PROJECT_NAME}"; then
    echo "⚠  Test project '${PROJECT_NAME}' already exists (leftover from a failed run)."
    echo "   Removing it so this run provisions cleanly rather than reconfiguring."
    "$LDM_CMD" -y rm "${PROJECT_NAME}" --delete >/dev/null 2>&1 || true
    rm -rf "${LDM_WORKSPACE:?}/${PROJECT_NAME}"
    if "$LDM_CMD" list 2>/dev/null | grep -q "${PROJECT_NAME}"; then
        echo "❌ ERROR: could not remove pre-existing project '${PROJECT_NAME}'." | tee -a "$RESULTS_FILE_TMP"
        echo "   Refusing to continue: reconfiguring a stale project is the path that hangs." | tee -a "$RESULTS_FILE_TMP"
        echo "   Remove it manually with: ${LDM_CMD} -y rm ${PROJECT_NAME} --delete" | tee -a "$RESULTS_FILE_TMP"
        exit 1
    fi
    report_ok "✅ Pre-existing test project removed."
fi

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
        report_ok "✅ Hot Deploy verified." | tee -a "$RESULTS_FILE_TMP"
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
    report_ok "✅ Integrity check verified."
else
    echo "❌ ERROR: Integrity check failed to block corruption." && exit 1
fi
log_and_run "Bypassing Integrity" "$LDM_CMD" -y restore --latest --no-verify

echo ">> Verifying Legacy Command Translation..."
if "$LDM_CMD" doctor --help >/dev/null && "$LDM_CMD" infra-setup --help >/dev/null; then
    report_ok "✅ Legacy command translation verified."
else
    echo "❌ ERROR: Legacy command translation failed." && exit 1
fi

echo ">> Verifying Share Command Layout..."
if "$LDM_CMD" share --help >/dev/null && \
   "$LDM_CMD" share start --help >/dev/null && \
   "$LDM_CMD" share status --help >/dev/null && \
   "$LDM_CMD" share stop --help >/dev/null; then
    report_ok "✅ Share command layout verified."
else
    echo "❌ ERROR: Share command layout verification failed." && exit 1
fi

# UX & Scaling
echo ">> Verifying Cascading Defaults..."
"$LDM_CMD" config defaults test_key test_value >/dev/null
if "$LDM_CMD" config defaults | grep -q "test_key.*test_value.*User"; then
    report_ok "✅ Set User Default verified."
else
    echo "❌ ERROR: Set User Default failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi
"$LDM_CMD" config defaults --remove test_key >/dev/null
if ! "$LDM_CMD" config defaults | grep -q "test_key.*test_value.*User"; then
    report_ok "✅ Remove User Default verified."
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
    report_ok "✅ logs --instance routing verified."
else
    echo "❌ ERROR: logs --instance routing validation failed." && exit 1
fi

echo ">> Verifying Trace Log and Logs Export..."
if [ -f "$HOME/.ldm/last-command.log" ]; then
    report_ok "✅ Trace Log (last-command.log) verified."
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
    report_ok "✅ Logs Export verified ($EXPORT_FILE)."
    rm "$EXPORT_FILE"
else
    echo "❌ ERROR: Logs Export file not generated." && exit 1
fi

echo ">> Verifying ldm start UX fast-fail..."
START_FAIL_OUT=$("$LDM_CMD" start fake-non-existent-project 2>&1 || true)
if echo "$START_FAIL_OUT" | grep -q "Project not found or not initialized"; then
    report_ok "✅ ldm start fast-fail verified."
else
    echo "❌ ERROR: ldm start fast-fail message not found. Output was: $START_FAIL_OUT" && exit 1
fi

echo ">> Verifying ldm run reconfigure UX message..."
RUN_RECONFIG_OUT=$("$LDM_CMD" -y run . --no-wait --info 2>&1 || true)
if echo "$RUN_RECONFIG_OUT" | grep -q "already exists and this command will reconfigure it"; then
    report_ok "✅ ldm run reconfigure UX message verified."
else
    echo "❌ ERROR: ldm run reconfigure message not found. Output was: $RUN_RECONFIG_OUT" && exit 1
fi

echo ">> Verifying Safe SELECT SQL Query..."
DB_QUERY_OUT=$("$LDM_CMD" db query . -s "SELECT 1 as test_val;" --allow-db-query 2>&1 || true)
if echo "$DB_QUERY_OUT" | grep -q "test_val"; then
    report_ok "✅ Safe SELECT SQL Query verified."
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
    report_ok "✅ Properties Override Cascade verified (rebuild)."
else
    echo "❌ ERROR: Properties Override Cascade rebuild failed." && exit 1
fi

log_and_run "Resetting properties" "$LDM_CMD" config reset-properties .
if grep -q "test.override.prop=456" files/portal-ext.properties && ! grep -q "123" files/portal-ext.properties; then
    report_ok "✅ Properties Override Reset verified."
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
    report_ok "✅ ldm list --json schema verified."
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
    report_ok "✅ ldm status --json schema verified."
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
    report_ok "✅ Idempotent Exit Code 5 verified."
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
    report_ok "✅ Client Extension deploy & staging verified."
else
    echo "-- client-extensions/ --"; ls -la "client-extensions" 2>&1 || true
    echo "-- osgi/client-extensions/ --"; ls -la "osgi/client-extensions" 2>&1 || true
    exit 1
fi

rm -rf "cx-build"

echo ">> Verifying Portal Patch Overlay (#1264)..."
# The patch JAR is SYNTHETIC and deliberately inert. It is a valid OSGi bundle
# -- unique Bundle-SymbolicName, no Import-Package, no Export-Package, no
# activator -- so OSGi resolves it and it then does nothing. Its name does not
# match any real core JAR, so it REPLACES nothing and cannot alter Liferay's
# behaviour; it is purely additive, and carries a marker file that makes its
# presence unambiguous.
#
# That choice also buys a free assertion. copy_patches_into() probes each patch
# for upstream existence (`docker cp <container>:<target> -`, which fails for a
# missing path) and refuses to copy a JAR that is not already in the image,
# because a patch whose target was removed upstream is sharper than a merely
# stale one. So the synthetic JAR MUST be refused without
# --force-portal-patches, and applied with it. Both directions are checked.
#
# The two assertions after that are the two defects live testing found in #1264
# and that reading the code did not catch:
#
#   1. `docker cp` preserves the host file's mode and stamps the host UID. A
#      mode-600 patch landed as `-rw------- 501 root` beside its
#      `-rw-r--r-- liferay liferay` neighbours; Liferay runs as uid 1000 and
#      could not read it, so OSGi failed to resolve that one bundle WHILE THE
#      CONTAINER STILL BOOTED AND REPORTED HEALTHY -- exactly the silent
#      failure this feature exists to remove. _world_readable() stages a 644
#      copy to fix it, so a 600 JAR on the host must still land readable.
#
#   2. `ldm start`/`ldm restart` bypass the run pipeline and build their own
#      compose commands. Their plain forms are safe, but --force-recreate
#      replaces the container and would silently drop every patch, leaving a
#      developer debugging against a JAR they believe they replaced.
#
# A sidecar manifest is written explicitly rather than letting LDM create one:
# load_or_create_sidecar() stamps `introduced_in` with the CURRENT tag on first
# sight, which would make a version-mismatch abort untestable and mask a real
# regression in classify_version_change().
PATCH_JAR_NAME="ldm-verify-noop-patch.jar"
PATCH_DIR="portal-patches"
PATCH_TAG=$("$VENV_PYTHON" -c "
import json,sys
print(json.load(open('meta', encoding='utf-8')).get('tag',''))
")
rm -rf "$PATCH_DIR"
mkdir -p "$PATCH_DIR"

"$VENV_PYTHON" -c "
import sys, zipfile
path = sys.argv[1]
manifest = (
    'Manifest-Version: 1.0\r\n'
    'Bundle-ManifestVersion: 2\r\n'
    'Bundle-SymbolicName: com.liferay.ldm.verify.noop\r\n'
    'Bundle-Name: LDM Verification No-Op Patch\r\n'
    'Bundle-Version: 1.0.0\r\n'
    '\r\n'
)
with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('META-INF/MANIFEST.MF', manifest)
    z.writestr('ldm-verify-marker.txt', 'LDM_PORTAL_PATCH_MARKER\n')
" "${PATCH_DIR}/${PATCH_JAR_NAME}"

"$VENV_PYTHON" -c "
import json, sys
json.dump({'jira': 'LDM-1264', 'introduced_in': sys.argv[1],
           'max_version': None, 'fail_on_mismatch': False},
          open(sys.argv[2], 'w', encoding='utf-8'), indent=2)
" "$PATCH_TAG" "${PATCH_DIR}/${PATCH_JAR_NAME}.json"

PATCH_HOST_SHA=$("$VENV_PYTHON" -c "
import hashlib,sys
print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())
" "${PATCH_DIR}/${PATCH_JAR_NAME}")

PATCH_OK=true
CONTAINER_PORTAL="/opt/liferay/osgi/portal"
PATCH_TARGET="${CONTAINER_PORTAL}/${PATCH_JAR_NAME}"

# 1. A patch absent upstream must be REFUSED without --force-portal-patches.
set +e
"$LDM_CMD" -y restart . --force-recreate >/dev/null 2>&1
PATCH_REFUSE_RC=$?
set -e
if [ "$PATCH_REFUSE_RC" -eq 0 ]; then
    echo "❌ ERROR: a patch absent from ${CONTAINER_PORTAL} was applied without --force-portal-patches." | tee -a "$RESULTS_FILE_TMP"
    PATCH_OK=false
fi

# 2. With the flag it applies. Mode 600 on the host deliberately exercises
#    _world_readable(): the container must still be able to read it.
chmod 600 "${PATCH_DIR}/${PATCH_JAR_NAME}"
log_and_run "Applying portal patch" "$LDM_CMD" -y restart . --force-recreate --force-portal-patches

if ! docker exec "${PROJECT_NAME}" test -f "${PATCH_TARGET}" 2>/dev/null; then
    echo "❌ ERROR: patch JAR not present at ${PATCH_TARGET} inside the container." | tee -a "$RESULTS_FILE_TMP"
    docker exec "${PROJECT_NAME}" ls -la "${CONTAINER_PORTAL}" 2>&1 | head -5
    PATCH_OK=false
else
    # Content must match the host file exactly -- a truncated or empty copy
    # would still satisfy a mere existence check.
    PATCH_IN_SHA=$(docker exec "${PROJECT_NAME}" sha256sum "${PATCH_TARGET}" 2>/dev/null | awk '{print $1}')
    if [ "$PATCH_IN_SHA" != "$PATCH_HOST_SHA" ]; then
        echo "❌ ERROR: patch JAR content differs inside the container." | tee -a "$RESULTS_FILE_TMP"
        echo "   host: ${PATCH_HOST_SHA}" | tee -a "$RESULTS_FILE_TMP"
        echo "   container: ${PATCH_IN_SHA}" | tee -a "$RESULTS_FILE_TMP"
        PATCH_OK=false
    fi

    # The #1264 silent failure: readable by Liferay (uid 1000), not just present.
    if ! docker exec -u 1000 "${PROJECT_NAME}" test -r "${PATCH_TARGET}" 2>/dev/null; then
        echo "❌ ERROR: patch JAR is not readable by uid 1000 -- OSGi would fail to resolve it while the container still booted healthy (#1264)." | tee -a "$RESULTS_FILE_TMP"
        docker exec "${PROJECT_NAME}" ls -l "${PATCH_TARGET}" 2>&1 | head -2
        PATCH_OK=false
    fi
fi

# 3. --force-recreate replaces the container; the patch must survive it.
if [ "$PATCH_OK" = true ]; then
    log_and_run "Re-creating with patches" "$LDM_CMD" -y restart . --force-recreate --force-portal-patches
    if ! docker exec "${PROJECT_NAME}" test -f "${PATCH_TARGET}" 2>/dev/null; then
        echo "❌ ERROR: patch JAR was dropped by 'restart --force-recreate' (#1264)." | tee -a "$RESULTS_FILE_TMP"
        PATCH_OK=false
    fi
fi

rm -rf "$PATCH_DIR"

if [ "$PATCH_OK" = true ]; then
    report_ok "✅ Portal patch overlay verified (refused without --force, applied and readable with it, survives --force-recreate)."
else
    echo "❌ ERROR: portal patch overlay verification failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi

echo ">> Verifying non-ASCII project naming (#1307 / #1308 / #1321)..."
# Design intent: the project metadata records the name the user chose,
# VERBATIM, while Docker receives a transcoded ASCII name. Both halves are
# asserted. Checking only the Docker name would pass even if the real name were
# being destroyed on the way in; checking only the metadata would pass even if
# Compose were handed a name it cannot use.
#
# Why these three names specifically:
#   Zolc          every character is non-ASCII. This is the #1307 case -- the
#                 sanitized name came out EMPTY and Compose refused to start
#                 with "project name must not be empty". Its stroked l (U+0142)
#                 is #1308: NFKD cannot decompose it, so before the explicit
#                 mapping it vanished silently instead of transcoding to "l".
#   Kaesespaetzle German umlauts EXPAND (ae) rather than being stripped, so a
#                 naive "drop anything non-ASCII" regression would still pass a
#                 length check but fail here.
#   Duoc          Vietnamese stacked diacritics -- multiple combining marks on
#                 one base character.
#
# --no-up so nothing boots, --no-seed so no ~1GB archive is fetched; the name is
# resolved long before either matters, which keeps this block to a few seconds.
#
# Assertions read the meta JSON and the Compose file rather than the directory
# name: macOS normalises filenames to NFD, so comparing the on-disk name is not
# portable across the platforms this script must pass on.
# Projects are created in a NESTED sub-directory, deliberately.
#
# find_dxp_roots() scans with iterdir(), i.e. exactly one level deep, so a
# project at <workspace>/naming-<port>/<name> cannot be found by the directory
# scan -- it is reachable only through the global registry. That makes this
# block assert two things at once: that the name survives round-trip, and that
# the project was actually REGISTERED (LDM-#1324).
#
# Flattening these into $LDM_WORKSPACE would make the assertion pass off the
# one-level scan alone and silently stop testing registration, which is the
# defect this suite found on Windows in the first place.
#
# Names are prefixed so they cannot collide with a real project. The prefix is
# ASCII and passes through sanitize_id() unchanged, so the expected Docker name
# is derived rather than guessed.
NAMING_PREFIX="test-naming-"
NAMING_WORKDIR="${LDM_WORKSPACE}/naming-${TEST_PORT}"
rm -rf "$NAMING_WORKDIR"
mkdir -p "$NAMING_WORKDIR"

naming_check() {
    local raw="${NAMING_PREFIX}$1"
    local expected_docker="${NAMING_PREFIX}$2"
    local dir="${NAMING_WORKDIR}/${raw}"
    local rc

    # Pre-clean, following the #1302 pattern: a leftover project from a failed
    # run makes LDM reconfigure rather than provision, which is the path that
    # hangs rather than failing.
    "$LDM_CMD" -y rm "${raw}" --delete >/dev/null 2>&1 || true

    set +e
    ( cd "$NAMING_WORKDIR" && "$LDM_CMD" -y init "${raw}" --no-up --no-seed ) >/dev/null 2>&1
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
        echo "❌ ERROR: 'ldm init ${raw}' failed with exit ${rc}." | tee -a "$RESULTS_FILE_TMP"
        return 1
    fi

    if [ ! -f "${dir}/meta" ]; then
        echo "❌ ERROR: no meta written for '${raw}'; expected ${dir}/meta." | tee -a "$RESULTS_FILE_TMP"
        return 1
    fi

    # The metadata half. `meta` is JSON, so the name is stored escaped
    # (Ż...) -- it must be parsed, not grepped, or the assertion silently
    # compares against the escape sequence instead of the character.
    if ! "$VENV_PYTHON" -c "
import json, sys
raw = sys.argv[1]
meta = json.load(open(sys.argv[2], encoding='utf-8'))
for key in ('project_name', 'container_name', 'liferay_container_name'):
    got = meta.get(key)
    assert got == raw, f'meta[{key!r}] is {got!r}, expected the verbatim {raw!r}'
" "$raw" "${dir}/meta"; then
        echo "❌ ERROR: metadata did not record '${raw}' verbatim." | tee -a "$RESULTS_FILE_TMP"
        echo "-- meta --"; head -40 "${dir}/meta" 2>&1
        return 1
    fi

    # The Docker half. #1307 added the explicit top-level `name:`; without it
    # Compose derives the project name from the directory and refuses to start.
    if ! "$VENV_PYTHON" -c "
import sys
expected = sys.argv[1]
name = None
for line in open(sys.argv[2], encoding='utf-8'):
    if line.startswith('name:'):
        name = line.split(':', 1)[1].strip()
        break
assert name is not None, 'docker-compose.yml has no top-level name: key (#1307)'
assert name, 'Compose project name is empty -- the #1307 failure exactly'
assert name.isascii(), f'Compose project name {name!r} is not ASCII; Docker will reject it'
assert name == expected, f'Compose project name is {name!r}, expected {expected!r}'
" "$expected_docker" "${dir}/docker-compose.yml"; then
        echo "❌ ERROR: Compose project name wrong for '${raw}' (expected '${expected_docker}')." | tee -a "$RESULTS_FILE_TMP"
        echo "-- docker-compose.yml (head) --"; head -5 "${dir}/docker-compose.yml" 2>&1
        return 1
    fi

    # The registry must show the real name back to the user, not the transcoded
    # one -- that is the whole point of keeping both.
    if ! "$LDM_CMD" list 2>/dev/null | grep -q -- "${raw}"; then
        echo "❌ ERROR: 'ldm list' does not show '${raw}'." | tee -a "$RESULTS_FILE_TMP"
        "$LDM_CMD" list 2>&1 | head -20
        return 1
    fi

    # LDM-#1351: `ldm info` must report the name APPLIED to each thing, because
    # the Provisioned Containers block exists to be pasted into `docker logs` /
    # `docker exec`. It used to print the verbatim metadata values, offering
    # container names that do not exist. Asserted on the command's OUTPUT rather
    # than on generated files: this is the contract a user consumes, and it is
    # the half the file-level assertions cannot see.
    #
    # No boot required -- these values are all resolved by `init`.
    info_out=$("$LDM_CMD" info "${raw}" 2>&1) || true

    # The heading keeps the verbatim name: that is what the user typed.
    if ! printf '%s' "$info_out" | grep -q -- "${raw}"; then
        echo "❌ ERROR: 'ldm info ${raw}' does not show the verbatim project name." | tee -a "$RESULTS_FILE_TMP"
        printf '%s\n' "$info_out" | head -20
        return 1
    fi

    # Every Docker-facing row must carry the transcoded name, and must NOT
    # carry the verbatim one -- a name Docker cannot resolve.
    if ! "$VENV_PYTHON" -c "
import sys

raw, expected, out = sys.argv[1], sys.argv[2], sys.argv[3]
rows = [
    line
    for line in out.splitlines()
    if any(k in line for k in ('Liferay:', 'Database:', 'Tunnel:'))
]
assert rows, 'ldm info printed no Provisioned Containers rows'

for line in rows:
    assert raw not in line, (
        'ldm info offers a container name Docker does not have (#1351): %r' % line.strip()
    )

liferay = [line for line in rows if 'Liferay:' in line]
assert liferay, 'no Liferay row in ldm info output'
assert expected in liferay[0], (
    'Liferay row is %r, expected the transcoded name %r' % (liferay[0].strip(), expected)
)
" "${raw}" "${expected_docker}" "$info_out"; then
        echo "❌ ERROR: 'ldm info ${raw}' reported names that are not in effect." | tee -a "$RESULTS_FILE_TMP"
        printf '%s\n' "$info_out" | sed -n '1,16p'
        return 1
    fi

    report_ok "   ✅ ${raw} -> ${expected_docker}"
    "$LDM_CMD" -y rm "${raw}" --delete >/dev/null 2>&1 || true
    return 0
}

NAMING_OK=true
naming_check "Żółć" "Zolc" || NAMING_OK=false
naming_check "Käsespätzle" "Kaesespaetzle" || NAMING_OK=false
naming_check "Được" "Duoc" || NAMING_OK=false

rm -rf "$NAMING_WORKDIR"

if [ "$NAMING_OK" = true ]; then
    report_ok "✅ Non-ASCII project naming verified (metadata verbatim, Docker transcoded)."
else
    echo "❌ ERROR: non-ASCII project naming verification failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi


echo ">> Verifying shared database mode (#1359 / #1354 / #1357)..."
# Every assertion here is derivable from `init --no-up --no-seed`, so the whole
# block costs seconds and boots nothing.
#
# It exists because this combination was completely broken and no test noticed:
# the composer tests set `database_mode` in META, which both call sites read,
# while the CLI flag lands in ARGS, which only one read. The two then disagreed
# within a single run -- no database service was emitted, yet the liferay
# service still declared `depends_on: <project>-db` -- so `docker compose
# config` rejected the file and `ldm run --database-mode shared` failed for
# every project. The untested axis was the one users take.
#
# The name is capitalised deliberately. The derived database name is lowercased
# (#1354) because PostgreSQL folds an unquoted CREATE DATABASE; an all-lowercase
# fixture would assert nothing about that.
SHARED_DB_NAME="TestSharedDb"
SHARED_DB_WORKDIR="${LDM_WORKSPACE}/shareddb-${TEST_PORT}"
rm -rf "$SHARED_DB_WORKDIR"
mkdir -p "$SHARED_DB_WORKDIR"
SHARED_DB_OK=true

"$LDM_CMD" -y rm "$SHARED_DB_NAME" --delete >/dev/null 2>&1 || true

set +e
( cd "$SHARED_DB_WORKDIR" && "$LDM_CMD" -y init "$SHARED_DB_NAME" \
    --no-up --no-seed --database-mode shared --db postgresql ) >/dev/null 2>&1
SHARED_DB_RC=$?
set -e

SHARED_DB_DIR="${SHARED_DB_WORKDIR}/${SHARED_DB_NAME}"
if [ "$SHARED_DB_RC" -ne 0 ]; then
    # This is the #1359 signature: compose refuses a file whose liferay service
    # depends on a database service that shared mode deliberately did not emit.
    echo "❌ ERROR: 'ldm init --database-mode shared' failed with exit ${SHARED_DB_RC}." | tee -a "$RESULTS_FILE_TMP"
    SHARED_DB_OK=false
elif ! "$VENV_PYTHON" -c "
import json, sys
import yaml

compose_path, meta_path, props_path = sys.argv[1:4]

compose = yaml.safe_load(open(compose_path, encoding='utf-8')) or {}
services = compose.get('services') or {}
defined = set(services)
for name, conf in services.items():
    deps = conf.get('depends_on') or []
    if isinstance(deps, dict):
        deps = list(deps)
    for dep in deps:
        assert dep in defined, (
            f'service {name!r} depends on undefined service {dep!r} -- '
            'docker compose will refuse this file (#1359)'
        )

meta = json.load(open(meta_path, encoding='utf-8'))
mode = meta.get('database_mode')
assert mode == 'shared', (
    'meta database_mode is %r, expected shared -- later commands will resolve '
    'the mode from defaults instead (#1359)' % (mode,)
)

url = ''
for line in open(props_path, encoding='utf-8'):
    if line.startswith('jdbc.default.url'):
        url = line.split('=', 1)[1].strip()
        break
assert url, 'no jdbc.default.url written'
assert 'liferay-db-global' in url, (
    f'JDBC URL {url!r} does not target the shared cluster -- the CLI flag was '
    'not honoured by _inject_liferay_db_env (#1359)'
)
db_part = url.rsplit('/', 1)[-1]
assert db_part == db_part.lower(), (
    f'shared database name {db_part!r} is not lowercase; PostgreSQL folds an '
    'unquoted CREATE DATABASE, so this name can never be connected to (#1354)'
)
" "${SHARED_DB_DIR}/docker-compose.yml" "${SHARED_DB_DIR}/meta" "${SHARED_DB_DIR}/files/portal-ext.properties"; then
    echo "❌ ERROR: shared database mode produced an inconsistent project." | tee -a "$RESULTS_FILE_TMP"
    SHARED_DB_OK=false
fi

"$LDM_CMD" -y rm "$SHARED_DB_NAME" --delete >/dev/null 2>&1 || true

# #1357: the global shared database is PostgreSQL only -- `liferay-db-global`
# is provisioned as postgres and the per-project create runs psql, so a MariaDB
# driver aimed at port 3306 of it could never connect. Refusal must be loud.
set +e
( cd "$SHARED_DB_WORKDIR" && "$LDM_CMD" -y init "${SHARED_DB_NAME}Mysql" \
    --no-up --no-seed --database-mode shared --db mysql ) >/dev/null 2>&1
SHARED_DB_MYSQL_RC=$?
set -e
if [ "$SHARED_DB_MYSQL_RC" -eq 0 ]; then
    echo "❌ ERROR: '--database-mode shared --db mysql' was accepted; it cannot work (#1357)." | tee -a "$RESULTS_FILE_TMP"
    SHARED_DB_OK=false
fi
"$LDM_CMD" -y rm "${SHARED_DB_NAME}Mysql" --delete >/dev/null 2>&1 || true

rm -rf "$SHARED_DB_WORKDIR"

if [ "$SHARED_DB_OK" = true ]; then
    report_ok "✅ Shared database mode verified (valid compose, shared URL, lowercase name, MySQL refused)."
else
    echo "❌ ERROR: shared database mode verification failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi


echo ">> Verifying 'ldm db start' / 'ldm db stop' (LDM-#1400)..."
# These were dead in every release up to v2.18.0-pre.4: both built
# `docker compose -f infra-compose.yml start db`, but that file defines only
# `traefik` -- there is no `db` service, and the global database is created by
# a bare `docker run`. It matters because cmd_reset_admin tells shared-DB users
# to run `ldm db start`, so LDM directed people into a command that could not
# work. Fully within this script's control: no boot, no timing dependency.
DB_GLOBAL="liferay-db-global"
DB_CMD_OK=true

if ! "$LDM_CMD" -y db start >/dev/null 2>&1; then
    echo "❌ ERROR: 'ldm db start' exited non-zero (LDM-#1400)." | tee -a "$RESULTS_FILE_TMP"
    DB_CMD_OK=false
elif ! docker ps --filter "name=^${DB_GLOBAL}$" --format '{{.Names}}' | grep -q "$DB_GLOBAL"; then
    echo "❌ ERROR: 'ldm db start' returned 0 but ${DB_GLOBAL} is not running." | tee -a "$RESULTS_FILE_TMP"
    echo "   A silent success is what made the original breakage hard to notice." | tee -a "$RESULTS_FILE_TMP"
    DB_CMD_OK=false
fi

# Idempotence: a second start must not fail, and must say something. A command
# that succeeds silently is indistinguishable from one that did nothing.
if [ "$DB_CMD_OK" = true ]; then
    DB_AGAIN_OUT=$("$LDM_CMD" -y db start 2>&1)
    if ! echo "$DB_AGAIN_OUT" | grep -qi "already running"; then
        echo "❌ ERROR: a second 'ldm db start' did not report the container was already running." | tee -a "$RESULTS_FILE_TMP"
        DB_CMD_OK=false
    fi
fi

if [ "$DB_CMD_OK" = true ]; then
    if ! "$LDM_CMD" -y db stop >/dev/null 2>&1; then
        echo "❌ ERROR: 'ldm db stop' exited non-zero (LDM-#1400)." | tee -a "$RESULTS_FILE_TMP"
        DB_CMD_OK=false
    elif docker ps --filter "name=^${DB_GLOBAL}$" --format '{{.Names}}' | grep -q "$DB_GLOBAL"; then
        echo "❌ ERROR: 'ldm db stop' returned 0 but ${DB_GLOBAL} is still running." | tee -a "$RESULTS_FILE_TMP"
        DB_CMD_OK=false
    fi
fi

if [ "$DB_CMD_OK" = true ]; then
    report_ok "✅ 'ldm db start'/'db stop' drive the real global container, idempotently (LDM-#1400)."
else
    echo "❌ ERROR: shared database start/stop verification failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi


echo ">> Verifying project UUID ownership labels (LDM-#1393 / #1395)..."
# Ownership was labelled by NAME, which is only as stable as the name: a renamed
# project's volumes keep the old label and belong to nothing, so `ldm prune`
# reports live resources as orphans. Artefact inspection only -- generated by
# `init --no-up --no-seed`, nothing booted.
UUID_DIR="${LDM_WORKSPACE}/uuidcheck-${TEST_PORT}"
mkdir -p "$UUID_DIR"
UUID_OK=true

if ! ( cd "$UUID_DIR" && "$LDM_CMD" -y init UuidCheck --no-up --no-seed ) >/dev/null 2>&1; then
    echo "❌ ERROR: 'ldm init' failed; the LDM-#1393 check cannot run." | tee -a "$RESULTS_FILE_TMP"
    UUID_OK=false
else
    PROJ_META="${UUID_DIR}/UuidCheck/meta"
    PROJ_UUID=$("$VENV_PYTHON" -c "
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8')).get('uuid', ''))
" "$PROJ_META")
    if [ -z "$PROJ_UUID" ]; then
        echo "❌ ERROR: the project meta carries no uuid (LDM-#1393)." | tee -a "$RESULTS_FILE_TMP"
        UUID_OK=false
    elif ! "$VENV_PYTHON" -c "
import sys, yaml
compose, want = sys.argv[1], sys.argv[2]
c = yaml.safe_load(open(compose, encoding='utf-8'))
label = 'com.liferay.ldm.project.uuid'

for name, svc in (c.get('services') or {}).items():
    labels = [str(x) for x in (svc.get('labels') or [])]
    assert f'{label}={want}' in labels, (
        f'service {name!r} is not labelled with the project uuid -- prune matches '
        'owners by name, so a renamed project would look like an orphan (#1395)'
    )

for vname, vdef in (c.get('volumes') or {}).items():
    got = ((vdef or {}).get('labels') or {}).get(label)
    assert got == want, (
        f'volume {vname!r} carries {got!r}, expected the project uuid (#1395)'
    )
" "${UUID_DIR}/UuidCheck/docker-compose.yml" "$PROJ_UUID"; then
        UUID_OK=false
    fi
fi

rm -rf "$UUID_DIR"

if [ "$UUID_OK" = true ]; then
    report_ok "✅ Every service and volume carries the project UUID ownership label (LDM-#1393/#1395)."
else
    echo "❌ ERROR: project UUID label verification failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi


echo ">> Verifying shared search mode (#1362 / #1363 / #1353)..."
# Derivable from `init --no-up --no-seed`: no boot, nothing outside this
# script's control. Deliberately NOT asserting that Liferay indexes into the
# shared cluster: that needs a boot plus indexing, and the wait is externally
# timed (measured: indices appeared 15s after health, but health itself takes
# minutes). Asserting it would make this suite fail for reasons unrelated to
# the change under test.
#
# Three bugs, each of which hid the others:
#   #1362  `--search-mode shared` was ignored -- the flag produced a sidecar
#   #1363  `ldm run` never provisioned the global cluster
#   #1353  the LIFERAY_ELASTICSEARCH* env vars do not reach Liferay at all;
#          the OSGi .config is the mechanism that works
SHARED_SEARCH_NAME="TestSharedSearch"
SHARED_SEARCH_WORKDIR="${LDM_WORKSPACE}/sharedsearch-${TEST_PORT}"
rm -rf "$SHARED_SEARCH_WORKDIR"
mkdir -p "$SHARED_SEARCH_WORKDIR"
SHARED_SEARCH_OK=true

"$LDM_CMD" -y rm "$SHARED_SEARCH_NAME" --delete >/dev/null 2>&1 || true

set +e
( cd "$SHARED_SEARCH_WORKDIR" && "$LDM_CMD" -y init "$SHARED_SEARCH_NAME" \
    --no-up --no-seed --search-mode shared ) >/dev/null 2>&1
SHARED_SEARCH_RC=$?
set -e

SHARED_SEARCH_DIR="${SHARED_SEARCH_WORKDIR}/${SHARED_SEARCH_NAME}"
if [ "$SHARED_SEARCH_RC" -ne 0 ]; then
    echo "❌ ERROR: 'ldm init --search-mode shared' failed with exit ${SHARED_SEARCH_RC}." | tee -a "$RESULTS_FILE_TMP"
    SHARED_SEARCH_OK=false
elif ! "$VENV_PYTHON" -c "
import json, sys
from pathlib import Path

root = Path(sys.argv[1])

meta = json.loads((root / 'meta').read_text(encoding='utf-8'))
assert meta.get('search_mode') == 'shared', (
    'meta search_mode is %r, expected shared -- the CLI flag was ignored (#1362)'
    % (meta.get('search_mode'),)
)

configs_dir = root / 'osgi' / 'configs'
configs = sorted(configs_dir.glob('*ElasticsearchConfiguration.config'))
assert configs, (
    'no ElasticsearchConfiguration.config written; the LIFERAY_ELASTICSEARCH* '
    'env vars alone do not configure Liferay (#1353)'
)

# LDM-#1418: there can be BOTH an elasticsearch7 and an elasticsearch8 config.
# The common/ baseline ships one per major version, and LDM writes the one
# matching the project's tag. This used to read configs[0] -- alphabetically the
# es7 file -- which for a modern (ES8) project is the inert baseline copy, so the
# assertion tested a file the project never uses. It passed only on machines with
# no common/ folder, where LDM's own file was the sole match.
#
# Take the highest major version present: that is the one a current tag uses.
def _major(path):
    marker = 'elasticsearch'
    tail = path.name.split(marker, 1)[1]
    digits = ''
    for ch in tail:
        if not ch.isdigit():
            break
        digits += ch
    return int(digits or 0)

active = max(configs, key=_major)
body = active.read_text(encoding='utf-8')
assert 'productionModeEnabled=B' in body, body

# LDM-#1418: the shared cluster address is valid in EITHER shape --
# networkHostAddresses inline in this file, or a remoteClusterConnectionId
# pointing at a sibling ElasticsearchConnectionConfiguration that carries it.
# Both reach the same cluster; asserting only the inline form rejected a correct
# project.
sibling = configs_dir / active.name.replace(
    'ElasticsearchConfiguration', 'ElasticsearchConnectionConfiguration'
)
address_sources = [body]
if sibling.exists():
    address_sources.append(sibling.read_text(encoding='utf-8'))
assert any('liferay-search-global:9200' in text for text in address_sources), (
    'neither %s nor its connection config points at the shared cluster'
    % (active.name,)
)

prefix = [l for l in body.splitlines() if l.startswith('indexNamePrefix')]
assert prefix, body
value = prefix[0].split('=', 1)[1].strip().strip(chr(34))
assert value == value.lower(), (
    'indexNamePrefix %r is not lowercase; Liferay lowercases it, so a '
    'mixed-case value cannot match the indices it creates' % (value,)
)

compose = (root / 'docker-compose.yml').read_text(encoding='utf-8')
assert 'osgi/configs:/opt/liferay/osgi/configs' in compose, (
    'osgi/configs is not mounted, so the config cannot reach Liferay (#1364)'
)
" "$SHARED_SEARCH_DIR"; then
    echo "❌ ERROR: shared search mode produced an inconsistent project." | tee -a "$RESULTS_FILE_TMP"
    SHARED_SEARCH_OK=false
fi

"$LDM_CMD" -y rm "$SHARED_SEARCH_NAME" --delete >/dev/null 2>&1 || true
rm -rf "$SHARED_SEARCH_WORKDIR"

if [ "$SHARED_SEARCH_OK" = true ]; then
    report_ok "✅ Shared search mode verified (flag honoured, mode persisted, OSGi config written and mounted)."
else
    echo "❌ ERROR: shared search mode verification failed." | tee -a "$RESULTS_FILE_TMP"
    exit 1
fi


# Final
log_and_run "Checking Status" "$LDM_CMD" -y status

# Clean up any potential orphans from the run
"$LDM_CMD" -y system prune >/dev/null 2>&1 || true

echo -e "\n🎯 ALL E2E VERIFICATIONS PASSED!"
