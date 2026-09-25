#!/usr/bin/env bash
set -e

# Change to the root of the repository
cd "$(dirname "$0")/.."

echo "=> Checking virtual environment..."
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
# shellcheck disable=SC1091
source .venv/bin/activate

# LDM-#1950: ask whether THIS venv has pre_commit, not whether any
# `pre-commit` exists on PATH.
#
# `command -v pre-commit` searches the whole PATH. `source .venv/bin/activate`
# above has prepended `.venv/bin`, but PATH still carries everything else --
# so a global Homebrew `pre-commit` satisfied the guard, the dev requirements
# were never installed into the fresh venv, and the script then failed at its
# own `python3 -m pre_commit run` step. A new clone or `git worktree` could not
# run the mandated gates at all.
#
# It also asked the wrong question twice over: the repo's own rule
# (.agents/skills/testing-and-ci/SKILL.md) records that console scripts like
# `pre-commit` are deleted by name by endpoint protection, which is why the
# module form is used everywhere else. Probing for that binary is unreliable
# even when it is the right venv.
#
# `python3` here IS the venv's interpreter, because activate ran above.
if ! python3 -m pre_commit --version &> /dev/null; then
    echo "=> pre_commit not present in this venv. Installing from requirements-dev.txt..."
    python3 -m pip install -r requirements-dev.txt
fi

# Hook installation runs UNCONDITIONALLY, outside the guard above.
#
# LDM-#1950: it used to be nested inside it, so once the dev requirements were
# present the hook was never (re)installed -- which is exactly when it most
# needs to be. `.git/hooks/pre-commit` is templated with an ABSOLUTE
# interpreter path, and it lives in the common `.git` directory shared by every
# worktree, so whichever checkout installed last owns the hook for all of them.
# When that checkout is deleted the hook points at a missing interpreter and
# silently falls through to `command -v pre-commit` -- a global binary, or
# nothing.
#
# `pre_commit install` is idempotent, like the sync_colors.py call below.
echo "=> Installing git hooks..."
python3 -m pre_commit install

# LDM-#1707: ui_colors.py is generated and gitignored, so a fresh clone or
# worktree does not have it. Without it ui.py falls back to empty colour codes,
# and five tests that assert ANSI output fail on invisible characters. Cheap and
# idempotent, so it runs unconditionally.
echo "=> Generating terminal colour module..."
python3 scripts/sync_colors.py

echo "=> Running pre-commit on all files..."
# We use || true so the script doesn't abort if pre-commit finds issues,
# allowing the user to see the output.
python3 -m pre_commit run --all-files || true
