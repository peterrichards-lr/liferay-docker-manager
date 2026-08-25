#!/bin/bash
#
# LDM-#1337 tier 2: the `ldm share` plumbing, end to end, against a local
# target.
#
# Asserts that the containerised provider actually creates a tunnel container
# and removes it again on stop. It does NOT assert remote routing -- a local
# target never produces a `docker --context`, and all of 127.0.0.0/8 counts as
# local, so a loopback target cannot stand in for a remote one either. Routing
# is asserted by tier 1 (ldm_core/tests/test_share_target_routing.py), which
# needs no tunnel at all; reaching a real node is tier 3.
#
# Kept out of verify_e2e_refactor.sh deliberately: this starts a real tunnel,
# which means a publicly reachable URL for the duration of the run, and it
# needs a token. Neither belongs in a suite that runs on every push.
set -e

LDM_CMD="${LDM_CMD:-ldm}"
PROJECT="share-verify-$$"
WORKDIR="${LDM_SHARE_WORKDIR:-$(pwd)/share-verify-work}"
FAILED=false

if [ -z "$LFT_CLIENT_TOKEN" ]; then
    echo "❌ ERROR: LFT_CLIENT_TOKEN is not set."
    echo "   'ldm share' calls UI.die without a token (share.py), so this"
    echo "   check cannot run. Set the token and retry."
    exit 1
fi

if ! command -v "$LDM_CMD" >/dev/null 2>&1; then
    echo "❌ ERROR: '$LDM_CMD' not found on PATH."
    exit 1
fi

cleanup() {
    echo ">> Cleaning up..."
    "$LDM_CMD" -y share stop "$PROJECT" >/dev/null 2>&1 || true
    "$LDM_CMD" -y rm "$PROJECT" --delete >/dev/null 2>&1 || true
    rm -rf "${WORKDIR:?}" 2>/dev/null || true
}
trap cleanup EXIT

echo ">> Provisioning a project for the share check..."
rm -rf "${WORKDIR:?}"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
"$LDM_CMD" -y init "$PROJECT" --no-up --no-seed >/dev/null 2>&1
cd "$PROJECT"

TUNNEL_CONTAINER="${PROJECT}-lfr-tunnel"

echo ">> Starting the tunnel (containerised provider)..."
if ! "$LDM_CMD" -y share start . --provider lfr-tunnel-docker >/dev/null 2>&1; then
    echo "❌ ERROR: 'ldm share start' failed."
    exit 1
fi

# The assertion: a container, by the name LDM records in meta, actually exists.
# Checking only the command's exit status would pass even if no tunnel were
# created -- the failure mode #1338 was about, where a tunnel appears to start
# and nothing is listening.
if docker ps -a --format '{{.Names}}' | grep -qx "$TUNNEL_CONTAINER"; then
    echo "✅ Tunnel container '$TUNNEL_CONTAINER' created."
else
    echo "❌ ERROR: tunnel container '$TUNNEL_CONTAINER' was not created."
    echo "-- containers --"
    docker ps -a --format '{{.Names}}' | head -20
    FAILED=true
fi

# The name must match what meta records, or `share stop` and `share status`
# would be looking for something that does not exist.
META_NAME=$(python3 -c "
import json
print(json.load(open('meta', encoding='utf-8')).get('tunnel_container_name', ''))
" 2>/dev/null || echo "")
if [ "$META_NAME" != "$TUNNEL_CONTAINER" ]; then
    echo "❌ ERROR: meta records tunnel_container_name='$META_NAME', expected '$TUNNEL_CONTAINER'."
    FAILED=true
fi

echo ">> Stopping the tunnel..."
"$LDM_CMD" -y share stop . >/dev/null 2>&1 || true

# Teardown matters: a tunnel left running is a publicly reachable URL nobody
# is watching.
if docker ps -a --format '{{.Names}}' | grep -qx "$TUNNEL_CONTAINER"; then
    echo "❌ ERROR: tunnel container '$TUNNEL_CONTAINER' still present after 'share stop'."
    FAILED=true
else
    echo "✅ Tunnel container removed on stop."
fi

if [ "$FAILED" = true ]; then
    echo "❌ Share tunnel verification FAILED."
    exit 1
fi

echo "🎯 Share tunnel verification passed (tier 2, local target)."
