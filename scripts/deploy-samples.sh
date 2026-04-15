#!/bin/bash
# scripts/deploy-samples.sh
# Deploys local sample assets from the repository to a target LDM project for testing.
# This allows testing sample changes without needing to build a binary or release.

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Determine the repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SAMPLES_DIR="$REPO_ROOT/references/samples"

usage() {
    echo "Usage: $0 <target-project-path>"
    echo ""
    echo "Example: $0 ~/dev/my-test-project"
    exit 1
}

# 1. Validate Arguments
if [ -z "$1" ]; then
    # Auto-detect if we are inside a project
    if [ -f ".liferay-docker.meta" ]; then
        TARGET_DIR="$(pwd)"
        echo -e "ℹ  No target specified. Auto-detected project at: ${CYAN}$TARGET_DIR${NC}"
    else
        usage
    fi
else
    TARGET_DIR="$(cd "$1" 2>/dev/null && pwd || echo "$1")"
fi

# 2. Validate Source
if [ ! -d "$SAMPLES_DIR" ]; then
    echo -e "${RED}❌ Error: Samples directory not found at $SAMPLES_DIR${NC}"
    exit 1
fi

# 3. Validate Target
if [ ! -d "$TARGET_DIR" ]; then
    echo -e "${RED}❌ Error: Target directory does not exist: $TARGET_DIR${NC}"
    exit 1
fi

if [ ! -f "$TARGET_DIR/.liferay-docker.meta" ]; then
    echo -e "${YELLOW}⚠️  Warning: Target directory does not appear to be an LDM project (missing .liferay-docker.meta).${NC}"
    read -p "Are you sure you want to deploy samples to $TARGET_DIR? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
fi

echo -e "🚀 Deploying samples from ${GREEN}$SAMPLES_DIR${NC} to ${GREEN}$TARGET_DIR${NC}..."

# 4. Perform the Sync
# We use rsync if available for cleaner syncing (handles deletions/updates better)
if command -v rsync &>/dev/null; then
    rsync -av --exclude=".gitkeep" "$SAMPLES_DIR/" "$TARGET_DIR/"
else
    # Fallback to cp if rsync is not present
    cp -Rv "$SAMPLES_DIR/"* "$TARGET_DIR/"
fi

echo -e "\n${GREEN}✅ Successfully deployed samples to $TARGET_DIR${NC}"
echo -e "To apply changes to a running project, run: ${CYAN}ldm deploy ${TARGET_DIR##*/}${NC}"
echo "This will trigger a re-scan and rebuild of any changed client extensions."
