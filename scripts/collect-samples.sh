#!/bin/bash
# scripts/collect-samples.sh
# Collects built assets (CX, Fragments, Configs) from the samples workspace into LDM references.

set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# Determine paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SAMPLES_DEST="$REPO_ROOT/references/samples"

usage() {
    echo "Usage: $0 <path-to-ldm-cx-samples-workspace>"
    echo ""
    echo "Example: $0 ~/repos/ldm-cx-samples"
    exit 1
}

# 1. Validate Arguments
if [ -z "$1" ]; then
    usage
fi

SOURCE_WS="$(cd "$1" 2>/dev/null && pwd || echo "$1")"

if [ ! -d "$SOURCE_WS/client-extensions" ]; then
    echo -e "${RED}❌ Error: Directory does not look like a Liferay Workspace (missing client-extensions/): $SOURCE_WS${NC}"
    exit 1
fi

echo -e "📥 Collecting built assets from ${CYAN}$SOURCE_WS${NC}..."

# 2. Cleanup Destination (Remove old samples to avoid stale assets)
echo -e "🧹 Cleaning old assets in $SAMPLES_DEST..."
rm -f "$SAMPLES_DEST/client-extensions"/*.zip
rm -f "$SAMPLES_DEST/deploy"/*.zip
rm -f "$SAMPLES_DEST/osgi/configs"/*.{config,cfg}

# 3. Collect Client Extension ZIPs
# Path: client-extensions/*/dist/*.zip
echo -e "📦 Collecting Client Extension ZIPs..."
find "$SOURCE_WS/client-extensions" -path "*/dist/*.zip" -exec cp -v {} "$SAMPLES_DEST/client-extensions/" \;

# 4. Collect Fragment ZIPs
# Fragments are typically built in 'fragments/' or as specific CX types. 
echo -e "🧩 Collecting Fragment ZIPs..."
if [ -d "$SOURCE_WS/fragments" ]; then
    find "$SOURCE_WS/fragments" -path "*/dist/*.zip" -exec cp -v {} "$SAMPLES_DEST/deploy/" \;
fi
# Also check for CX-style fragments that might have been built inside client-extensions/
find "$SOURCE_WS/client-extensions" -name "*fragment*.zip" -path "*/dist/*" -exec cp -v {} "$SAMPLES_DEST/deploy/" \;

# 5. Collect OSGi Configurations
echo -e "⚙️  Collecting OSGi configurations..."
find "$SOURCE_WS" -name "*.config" -o -name "*.cfg" | grep -v "node_modules" | while read -r cfg; do
    cp -v "$cfg" "$SAMPLES_DEST/osgi/configs/"
done

# 6. Collect Portal Properties
if [ -f "$SOURCE_WS/portal-ext.properties" ]; then
    echo -e "📝 Updating portal-ext.properties..."
    cp -v "$SOURCE_WS/portal-ext.properties" "$SAMPLES_DEST/files/portal-ext.properties"
fi

echo -e "\n${GREEN}✅ References updated in $SAMPLES_DEST${NC}"
echo "You can now commit these assets to the LDM repository."
