#!/bin/bash
set -e

# LDM Package Script: Option A (Shiv / ZipApp)
# Builds a self-contained Python ZipApp for local testing.

OUTNAME="ldm-local"
INSTALL_PATH="/usr/local/bin/ldm"
VENV_TMP=".venv_build_tmp"

echo "🔨 Preparing build environment..."
python3 -m venv "$VENV_TMP"
# shellcheck disable=SC1091
source "$VENV_TMP/bin/activate"

echo "📦 Installing build dependencies..."
pip install --upgrade pip --quiet
pip install shiv --quiet

echo "📝 Injecting build metadata..."
# Temporarily mark as a local build with timestamp
python3 -c "
import re
from datetime import datetime
path = 'ldm_core/constants.py'
ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
content = open(path).read()
content = re.sub(r'BUILD_INFO = None', f'BUILD_INFO = \"Local Build - {ts}\"', content)
open(path, 'w').write(content)
"

echo "🔨 Building LDM binary with Shiv..."
shiv -e ldm_core.cli:main -o "$OUTNAME" -p '/usr/bin/env python3' .
chmod +x "$OUTNAME"

echo "🧹 Reverting build metadata..."
python3 -c "
import re
path = 'ldm_core/constants.py'
content = open(path).read()
content = re.sub(r'BUILD_INFO = \".*?\"', 'BUILD_INFO = None', content)
open(path, 'w').write(content)
"

# Clean up venv
deactivate
rm -rf "$VENV_TMP"

echo "✅ Binary built: ./$OUTNAME"

if [[ "$1" == "--install" ]]; then
    echo "🚀 Installing to $INSTALL_PATH (requires sudo)..."
    sudo mv "$OUTNAME" "$INSTALL_PATH"
    echo "✅ LDM successfully installed to $INSTALL_PATH"
    # Verify the installation
    $INSTALL_PATH --version
else
    echo "💡 Tip: Run with --install to override $INSTALL_PATH"
fi
