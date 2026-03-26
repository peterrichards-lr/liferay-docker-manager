#!/bin/bash
# LDM Environment Doctor - Confidence Check for SEs

echo "🚀 LDM Doctor: Diagnosing Environment..."
echo "---------------------------------------"

# 1. Check Architecture
ARCH=$(uname -m)
OS=$(uname -s)
echo "💻 Host Hardware: $OS ($ARCH)"

# 2. Check Docker Provider
if ! DOCKER_NAME=$(docker info --format '{{.Name}}' 2>/dev/null); then
    echo "❌ ERROR: Docker is not running."
    exit 1
fi

# Detect actual provider from Operating System string
DOCKER_OS=$(docker info --format '{{.Operating System}}' 2>/dev/null)
echo "🐳 Docker Engine: $DOCKER_NAME ($DOCKER_OS)"

# 3. Memory Check (The #1 reason Liferay fails)
MEM_BYTES=$(docker info --format '{{.MemTotal}}')
MEM_GB=$(awk "BEGIN {print $MEM_BYTES / 1073741824}")
echo "🧠 Available RAM: ${MEM_GB}GB"

# Use awk for comparison to avoid 'bc' dependency
IS_LOW_MEM=$(awk "BEGIN {print ($MEM_GB < 7.5) ? 1 : 0}")

if [[ "$IS_LOW_MEM" -eq 1 ]]; then
    echo "⚠️  WARNING: Liferay requires 8GB+. Your Docker environment has ${MEM_GB}GB."
    echo "    (Threshold: 7.5GB to account for system overhead)."
    echo "    Expect timeouts or startup failures."
else
    echo "✅ Memory looks sufficient."
fi

# 4. Volume Mounting Test (The 'EcoPulse' PNG Test)
TEST_DIR="./ldm-test-volume"
mkdir -p "$TEST_DIR"
touch "$TEST_DIR/test.txt"

if docker run --rm -v "$(pwd)/ldm-test-volume:/test" alpine ls /test/test.txt > /dev/null 2>&1; then
    echo "📂 Volume Mounting: PASS"
else
    echo "❌ Volume Mounting: FAIL (Check Docker File Sharing settings)"
    echo "    Ensure '$(pwd)' is shared with Docker."
fi

rm -rf "$TEST_DIR"

echo "---------------------------------------"
echo "✅ Environment Check Complete."
