#!/usr/bin/env bash
#
# Fetch and stage everything needed to run LDM's E2E verification suite.
#
# Replaces the hand-run sequence of curl, unzip, chmod and a checksum check
# that nobody ran. The old one-file instruction fetched only the script and
# left `common/` behind -- LDM then merely WARNS about the missing activation
# key and Elasticsearch configuration, so the suite completed, exited 0 and
# reported success having applied neither (LDM-#1718).
#
# Two things this deliberately does NOT do:
#
#   * install the binary into a system directory. That needs sudo, and a
#     verification helper silently replacing the ldm on your PATH is a
#     surprise. It downloads and verifies it, then tells you the one command
#     to run.
#   * invent an activation key. `common/activation-key-*.xml` is gitignored
#     because it is licensed, so no release bundle can carry one (LDM-#1733).
#     Point --activation-key at yours, or the suite verifies an unlicensed DXP
#     and says nothing about it.
#
# Usage:
#   ./install_verification.sh --tag v2.22.0-pre.6 --activation-key ~/keys/act.xml
#   ./install_verification.sh --tag v2.22.0-pre.6 --no-binary
#
set -euo pipefail

REPO="peterrichards-lr/liferay-docker-manager"
TAG=""
TARGET_DIR="ldm-verification"
ACTIVATION_KEY="${LDM_ACTIVATION_KEY:-}"
WANT_BINARY=1

die() { printf '\033[0;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
note() { printf '\033[0;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33mWARNING:\033[0m %s\n' "$*" >&2; }

usage() {
    sed -n '3,25p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

Options:
  --tag <tag>             Release to install (e.g. v2.22.0-pre.6).
                          Defaults to the latest release.
  --dir <path>            Where to unpack (default: ./ldm-verification).
  --activation-key <path> Your DXP activation key; copied into common/.
                          May also be given as $LDM_ACTIVATION_KEY.
  --no-binary             Skip downloading the ldm binary.
  -h, --help              This text.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --tag) TAG="${2:-}"; shift 2 ;;
        --dir) TARGET_DIR="${2:-}"; shift 2 ;;
        --activation-key) ACTIVATION_KEY="${2:-}"; shift 2 ;;
        --no-binary) WANT_BINARY=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option '$1' (try --help)" ;;
    esac
done

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v unzip >/dev/null 2>&1 || die "unzip is required"

# `shasum` on macOS, `sha256sum` on most Linux. Checking is not optional: a
# truncated download is otherwise found by the suite failing strangely an hour
# later.
if command -v shasum >/dev/null 2>&1; then
    SHA_CHECK="shasum -a 256 -c"
elif command -v sha256sum >/dev/null 2>&1; then
    SHA_CHECK="sha256sum -c"
else
    die "need shasum or sha256sum to verify the download"
fi

if [ -z "$TAG" ]; then
    note "Resolving the latest release..."
    TAG=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
        | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
    [ -n "$TAG" ] || die "could not resolve the latest release; pass --tag"
    note "Using ${TAG}"
fi

BASE="https://github.com/${REPO}/releases/download/${TAG}"

# Resolve the binary asset before downloading anything, so an unsupported
# platform fails immediately rather than after a 23 MB transfer.
BINARY_ASSET=""
if [ "$WANT_BINARY" -eq 1 ]; then
    case "$(uname -s)" in
        Darwin)
            case "$(uname -m)" in
                arm64) BINARY_ASSET="ldm-macos-arm64" ;;
                x86_64) BINARY_ASSET="ldm-macos-x86_64" ;;
                *) warn "unrecognised macOS arch '$(uname -m)'; skipping the binary" ;;
            esac
            ;;
        Linux) BINARY_ASSET="ldm-linux" ;;
        *)
            warn "no binary for '$(uname -s)'; skipping it. On Windows use install_verification.ps1."
            ;;
    esac
fi

mkdir -p "$TARGET_DIR"
TARGET_DIR=$(cd "$TARGET_DIR" && pwd)

note "Downloading the verification bundle (${TAG})..."
curl -fsSL -o "${TARGET_DIR}/verification-bundle.zip" "${BASE}/verification-bundle.zip" \
    || die "could not download verification-bundle.zip for ${TAG} -- does that release exist?"

note "Unpacking..."
# Into TARGET_DIR itself, NOT a nested folder: LDM looks for common/ beside
# the script, so the layout is the point rather than a convenience.
unzip -oq "${TARGET_DIR}/verification-bundle.zip" -d "$TARGET_DIR"

note "Verifying checksums..."
( cd "$TARGET_DIR" && $SHA_CHECK SHA256SUMS >/dev/null ) \
    || die "checksum mismatch in the bundle -- re-download rather than run it"

# LDM-#1735: the repo keeps these 0644, so a faithful zip lands un-runnable.
chmod +x "${TARGET_DIR}/verify_e2e_refactor.sh" 2>/dev/null || true
[ -f "${TARGET_DIR}/verify_fragment_override.py" ] \
    && chmod +x "${TARGET_DIR}/verify_fragment_override.py" 2>/dev/null || true

if [ -n "$BINARY_ASSET" ]; then
    note "Downloading ${BINARY_ASSET}..."
    curl -fsSL -o "${TARGET_DIR}/ldm" "${BASE}/${BINARY_ASSET}" \
        || die "could not download ${BINARY_ASSET}"
    curl -fsSL -o "${TARGET_DIR}/checksums.txt" "${BASE}/checksums.txt" || true
    if [ -f "${TARGET_DIR}/checksums.txt" ]; then
        expected=$(grep "$BINARY_ASSET" "${TARGET_DIR}/checksums.txt" | awk '{print $1}' | head -1)
        if command -v shasum >/dev/null 2>&1; then
            actual=$(shasum -a 256 "${TARGET_DIR}/ldm" | awk '{print $1}')
        else
            actual=$(sha256sum "${TARGET_DIR}/ldm" | awk '{print $1}')
        fi
        if [ -n "$expected" ] && [ "$expected" != "$actual" ]; then
            die "binary checksum mismatch for ${BINARY_ASSET}"
        fi
        note "Binary checksum verified."
    else
        warn "could not fetch checksums.txt; the binary is UNVERIFIED"
    fi
    chmod +x "${TARGET_DIR}/ldm"
fi

KEY_OK=0
if [ -n "$ACTIVATION_KEY" ]; then
    if [ -f "$ACTIVATION_KEY" ]; then
        cp "$ACTIVATION_KEY" "${TARGET_DIR}/common/"
        note "Activation key copied into common/."
        KEY_OK=1
    else
        die "activation key not found: ${ACTIVATION_KEY}"
    fi
else
    # Not fatal: --no-binary runs and offline staging are both legitimate. But
    # it must be loud, because the failure it causes is SILENT -- LDM warns,
    # the suite passes, and it verified less than it claims.
    warn "No activation key supplied."
    warn "The bundle cannot ship one (it is licensed and gitignored, LDM-#1733)."
    warn "Without it Liferay runs UNLICENSED, LDM only warns, and the suite"
    warn "still reports success having verified a smaller system than it claims."
    warn "Copy yours in before running:"
    warn "    cp /path/to/activation-key-*.xml ${TARGET_DIR}/common/"
fi

printf '\n\033[0;32mReady.\033[0m  %s\n\n' "$TARGET_DIR"
if [ -n "$BINARY_ASSET" ]; then
    printf '  Install the matching binary (needs sudo, so run it yourself):\n'
    printf '    sudo mv %s/ldm /usr/local/bin/ldm\n\n' "$TARGET_DIR"
fi
[ "$KEY_OK" -eq 0 ] && printf '  Then copy your activation key into %s/common/\n\n' "$TARGET_DIR"
printf '  Run the suite:\n'
printf '    cd %s && ./verify_e2e_refactor.sh\n\n' "$TARGET_DIR"
