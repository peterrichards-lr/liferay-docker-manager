#!/bin/bash
#
# LDM-#1325: run the system shellcheck, but refuse to run the WRONG one.
#
# The pre-commit hook is `language: system` with `entry: shellcheck`, so it runs
# whatever binary happens to be on PATH. That is deliberate: the upstream hook
# is `language: docker_image` (a Docker pull on every commit, removed for CI
# resilience in 19227f6c), and installing a pinned shellcheck-py would drop a
# 61MB executable into a Python env's bin/ -- exactly the shape endpoint
# protection has quarantined on developer machines here before (LDM-#1240).
#
# But `rev: v0.10.0` in .pre-commit-config.yaml never controlled the binary; it
# only supplied the hook's display name. So the gate printed a reassuring
# "ShellCheck v0.10.0" while running something else entirely, and local and CI
# could silently disagree.
#
# That is not hypothetical. A `cat file | head` in verify_e2e_refactor.sh passed
# the local gate -- and therefore passed agent_push.sh, the mandated push path
# -- while failing all four CI legs on SC2002. Local was 0.11.0, which demoted
# SC2002 out of the default set; CI's was 0.10.
#
# This wrapper installs nothing. It asserts the version first, so a mismatch
# fails loudly with instructions instead of quietly weakening the gate.
set -e

EXPECTED_VERSION="0.11.0"

if ! command -v shellcheck >/dev/null 2>&1; then
    echo "[ERROR] shellcheck not found on PATH."
    echo "        Install it:  brew install shellcheck"
    exit 1
fi

ACTUAL_VERSION="$(shellcheck --version | awk '/^version:/ {print $2}')"

if [ "$ACTUAL_VERSION" != "$EXPECTED_VERSION" ]; then
    echo "[ERROR] shellcheck version mismatch."
    echo "        expected: ${EXPECTED_VERSION}  (pinned here and in .github/workflows/ci.yml)"
    echo "        found:    ${ACTUAL_VERSION}"
    echo
    echo "        Versions disagree about which checks are on by default -- 0.11"
    echo "        demoted SC2002, for instance -- so a mismatched local binary"
    echo "        makes this gate weaker or stricter than CI without saying so."
    echo
    echo "        macOS:  brew install shellcheck   (then 'brew upgrade shellcheck')"
    echo "        Linux:  see https://github.com/koalaman/shellcheck/releases"
    echo
    echo "        If you are deliberately moving to a new version, update"
    echo "        EXPECTED_VERSION here and the pinned version in ci.yml together."
    exit 1
fi

exec shellcheck "$@"
