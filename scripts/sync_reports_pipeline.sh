#!/bin/bash
set -e

# Sync Compatibility Pipeline Automation Script
# Automates branch creation, synchronization, linting, committing, and pushing of compatibility reports.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$WORKSPACE_DIR"

NON_INTERACTIVE=false
for arg in "$@"; do
    if [ "$arg" == "-y" ] || [ "$arg" == "--non-interactive" ]; then
        NON_INTERACTIVE=true
    fi
done

# Ensure we are in virtual environment if it exists
if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# 1. Verification
# Ensure working directory has untracked or modified files in references/verification-results/
NEW_REPORTS=$(git status --porcelain references/verification-results/ | grep -E "^[AMR?][A-Z? ]|^.[AMR?]" || true)
if [ -z "$NEW_REPORTS" ]; then
    echo "❌ ERROR: No new or modified reports found in references/verification-results/."
    echo "Please copy your E2E verification report files there first."
    exit 1
fi

# Ensure we start from a clean master branch
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" != "master" ]; then
    if [ "$NON_INTERACTIVE" == "true" ]; then
        echo "❌ ERROR: Not on 'master' branch in non-interactive mode. Please checkout master first."
        exit 1
    fi
    echo "⚠️  WARNING: You are on branch '$CURRENT_BRANCH', not 'master'."
    read -p "Do you want to checkout master first? (y/N): " -r CHECKOUT_MASTER
    if [[ "$CHECKOUT_MASTER" =~ ^[Yy]$ ]]; then
        git checkout master
        git pull origin master
    else
        echo "Proceeding on current branch '$CURRENT_BRANCH'..."
    fi
fi

# Extract version from Constants
VERSION=$(python3 -c "import re; print(re.search(r'VERSION\s*=\s*[\"\']([^\"\']+)[\"\']', open('ldm_core/constants.py').read()).group(1))")
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BRANCH_NAME="docs/compat-sync-v${VERSION}-${TIMESTAMP}"

echo "ℹ  Creating new feature branch: $BRANCH_NAME..."
git checkout -b "$BRANCH_NAME"

echo "ℹ  Running sync_compatibility.py..."
python3 scripts/sync_compatibility.py

echo "ℹ  Running lint.sh to verify formatting and styling..."
./lint.sh

echo "ℹ  Staging changes..."
git add references/verification-results/ docs/ 2>/dev/null || true

# Check if there are changes staged for commit
if git diff --cached --quiet; then
    echo "ℹ  No changes to commit (everything already in sync)."
    # Clean up the branch we created
    git checkout "$CURRENT_BRANCH"
    git branch -d "$BRANCH_NAME"
    exit 0
fi

echo "ℹ  Committing changes..."
git commit -m "docs: update compatibility matrix for v${VERSION}"

echo "ℹ  Pushing branch to origin..."
git push -u origin "$BRANCH_NAME"

if command -v gh &>/dev/null && gh auth status &>/dev/null; then
    echo "ℹ  Creating Pull Request on GitHub..."
    gh pr create --title "docs: Update compatibility matrix for v${VERSION}" --body "Automated compatibility table synchronization for LDM version v${VERSION}." --web
else
    echo "✅ Success! Branch '$BRANCH_NAME' pushed."
    echo "You can now open a Pull Request on GitHub manually."
fi
