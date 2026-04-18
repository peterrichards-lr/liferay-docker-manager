#!/bin/bash
set -e

# Local Smoke Test for Liferay Docker Manager
# Replicates the CI smoke test logic

echo "=== Starting Local Smoke Test ==="

# Cleanup old project if exists
rm -rf smoke-project

# Create a mock project
echo "Creating mock project..."
mkdir -p smoke-project/files
touch smoke-project/files/portal-ext.properties
{
  echo "tag=7.4.13-u100"
  echo "container_name=smoke-test"
  echo "image_tag=alpine"
  echo "port=8081"
} > smoke-project/.liferay-docker.meta

# 1. Generate Compose
echo "Generating docker-compose.yml..."
python3 liferay_docker.py -y run smoke-project --no-up --sidecar --no-wait

# 2. Patch Healthcheck (since we use alpine, it won't respond on 8081)
echo "Patching healthcheck for alpine compatibility..."
if [[ "$OSTYPE" == "darwin"* ]]; then
  sed -i '' 's/curl -f http:\/\/localhost:8081\/c\/portal\/layout/true/g' smoke-project/docker-compose.yml
else
  sed -i 's/curl -f http:\/\/localhost:8081\/c\/portal\/layout/true/g' smoke-project/docker-compose.yml
fi

# 3. Verify it starts and exits quickly with --no-wait
echo "Running LDM run with --no-wait..."
python3 liferay_docker.py -y run smoke-project --no-wait

# 4. Cleanup
echo "Tearing down smoke test stack..."
python3 liferay_docker.py -y down smoke-project
rm -rf smoke-project

echo "✅ Smoke test passed!"
