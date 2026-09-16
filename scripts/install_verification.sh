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
SELF_CHECK=1

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
  --no-self-check         Skip verifying this script against the release.
  -h, --help              This text.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --tag) TAG="${2:-}"; shift 2 ;;
        --dir) TARGET_DIR="${2:-}"; shift 2 ;;
        --activation-key) ACTIVATION_KEY="${2:-}"; shift 2 ;;
        --no-binary) WANT_BINARY=0; shift ;;
        --no-self-check) SELF_CHECK=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option '$1' (try --help)" ;;
    esac
done

command -v curl >/dev/null 2>&1 || die "curl is required"

# LDM-#1746: `unzip` is NOT installed by default on many minimal Linux images
# and WSL distributions, and refusing there is refusing for no reason -- the
# archive can be opened by at least three things that are already present on a
# machine capable of running the suite. Reported from a WSL box where the whole
# staging run died on `ERROR: unzip is required`, a message that did not even
# say how to fix it.
#
# Order is by directness: unzip is purpose-built, bsdtar reads zip natively
# (GNU tar does not), and python3's zipfile is the broadest fallback.
EXTRACTOR=""
if command -v unzip >/dev/null 2>&1; then
    EXTRACTOR="unzip"
elif command -v bsdtar >/dev/null 2>&1; then
    EXTRACTOR="bsdtar"
elif command -v python3 >/dev/null 2>&1 && python3 -c "import zipfile" >/dev/null 2>&1; then
    EXTRACTOR="python3"
else
    die "need one of unzip, bsdtar or python3 to open the bundle.
   Install one, e.g.:   sudo apt-get install -y unzip
                        sudo dnf install -y unzip
                        brew install unzip"
fi

extract_bundle() {
    # $1 = archive, $2 = destination
    case "$EXTRACTOR" in
        unzip) unzip -oq "$1" -d "$2" ;;
        bsdtar) bsdtar -xf "$1" -C "$2" ;;
        python3)
            python3 - "$1" "$2" <<'PYEOF'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    archive.extractall(sys.argv[2])
PYEOF
            ;;
    esac
}

# `shasum` on macOS, `sha256sum` on most Linux. Checking is not optional: a
# truncated download is otherwise found by the suite failing strangely an hour
# later.
if command -v shasum >/dev/null 2>&1; then
    SHA_CHECK="shasum -a 256 -c"
    sha_of() { shasum -a 256 "$1" | awk '{print $1}'; }
elif command -v sha256sum >/dev/null 2>&1; then
    SHA_CHECK="sha256sum -c"
    sha_of() { sha256sum "$1" | awk '{print $1}'; }
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

# LDM-#1735: verify THIS FILE against the release it is staging, so the
# bootstrap is not the one unverified link in a chain that checksums
# everything else. It is a warning rather than an error on purpose: reusing
# one installer across several releases is legitimate and common, and the
# thing being verified is the release's artifacts, not this script's vintage.
if [ "$SELF_CHECK" -eq 1 ]; then
    self_sums=$(mktemp)
    if curl -fsSL -o "$self_sums" "${BASE}/checksums.txt" 2>/dev/null; then
        expected_self=$(awk '$2 ~ /install_verification\.sh$/ {print $1; exit}' "$self_sums")
        if [ -n "$expected_self" ]; then
            actual_self=$(sha_of "$0")
            if [ "$expected_self" = "$actual_self" ]; then
                note "Installer verified against ${TAG}."
            else
                warn "This installer does not match the one published with ${TAG}."
                warn "That is expected if you are reusing an older copy, and fine --"
                warn "everything it downloads below is still checksummed. Fetch the"
                warn "matching one if you would rather it were identical:"
                warn "    curl -fsSL -O ${BASE}/install_verification.sh"
            fi
        fi
    fi
    rm -f "$self_sums"
fi

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

note "Unpacking (using ${EXTRACTOR})..."
# Into TARGET_DIR itself, NOT a nested folder: LDM looks for common/ beside
# the script, so the layout is the point rather than a convenience.
extract_bundle "${TARGET_DIR}/verification-bundle.zip" "$TARGET_DIR"

note "Verifying checksums..."
( cd "$TARGET_DIR" && $SHA_CHECK SHA256SUMS >/dev/null ) \
    || die "checksum mismatch in the bundle -- re-download rather than run it"

# LDM-#1741: the archive has done its job once the contents are extracted AND
# verified, so remove it. Deliberately AFTER the checksum check, never before:
# a failed verification is exactly when the archive is worth keeping, because
# it is the evidence of what actually arrived.
rm -f "${TARGET_DIR}/verification-bundle.zip"

# LDM-#1735: the repo keeps these 0644, so a faithful zip lands un-runnable.
chmod +x "${TARGET_DIR}/verify_e2e_refactor.sh" 2>/dev/null || true
[ -f "${TARGET_DIR}/fragment_override_harness.py" ] \
    && chmod +x "${TARGET_DIR}/fragment_override_harness.py" 2>/dev/null || true

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
        # LDM-#1741: spent once the binary is verified. SHA256SUMS stays --
        # it covers the extracted files and lets them be re-checked later.
        rm -f "${TARGET_DIR}/checksums.txt"
    else
        warn "could not fetch checksums.txt; the binary is UNVERIFIED"
    fi
    chmod +x "${TARGET_DIR}/ldm"
fi

# LDM-#1735: the key lives in a `common/` folder on each machine, relative to
# where the suite is run -- so look there before asking for it. Two places, in
# order:
#
#   1. the target's own common/, which already holds one when the bundle was
#      unpacked over an existing folder. `unzip -o` overwrites only what the
#      zip contains, so a key sitting there survives -- measured, not assumed.
#   2. ./common/ relative to where THIS script was invoked, which is the
#      layout the suite has always used.
#
# Discovery beats a flag here: the flag is one more thing to remember, and
# forgetting it fails silently.
KEY_OK=0
if [ -z "$ACTIVATION_KEY" ]; then
    existing=$(find "${TARGET_DIR}/common" -maxdepth 1 -name 'activation-key-*.xml' 2>/dev/null | head -1)
    if [ -n "$existing" ]; then
        note "Using the activation key already in $(basename "$TARGET_DIR")/common/."
        KEY_OK=1
    else
        nearby=$(find ./common -maxdepth 1 -name 'activation-key-*.xml' 2>/dev/null | head -1)
        if [ -n "$nearby" ]; then
            ACTIVATION_KEY="$nearby"
            note "Found an activation key in ./common/ -- using it."
        fi
    fi
fi

if [ "$KEY_OK" -eq 1 ]; then
    :
elif [ -n "$ACTIVATION_KEY" ]; then
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
