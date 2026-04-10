#!/bin/bash
set -e

# LDM Package Script: Option B (PyInstaller / Standalone)
# Builds a true standalone binary with the Python interpreter bundled.

OS=$(uname -s)
OUTNAME="ldm-macos"
INSTALL_PATH="/usr/local/bin/ldm"
VENV_TMP=".venv_build_tmp"

# Handle cross-platform path separators for --add-data
if [[ "$OS" == "Darwin" ]]; then
    DATA_SEP=":"
else
    DATA_SEP=";"
fi

echo "🔨 Preparing build environment..."
python3 -m venv "$VENV_TMP"
# shellcheck disable=SC1091
source "$VENV_TMP/bin/activate"

echo "📦 Installing build dependencies..."
pip install --upgrade pip --quiet
pip install pyinstaller -r requirements.txt --quiet

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

echo "🔨 Building LDM binary with PyInstaller..."
pyinstaller --onefile --clean --name "$OUTNAME" --add-data "ldm_core/resources${DATA_SEP}ldm_core/resources" liferay_docker.py

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

echo "✅ Binary built: ./dist/$OUTNAME"

if [[ "$1" == "--install" ]]; then
    echo "🚀 Installing to $INSTALL_PATH (requires sudo)..."
    sudo cp "dist/$OUTNAME" "$INSTALL_PATH"
    echo "✅ LDM successfully installed to $INSTALL_PATH"
    # Verify the installation
    $INSTALL_PATH --version
else
    echo "💡 Tip: Run with --install to override $INSTALL_PATH"
fi
