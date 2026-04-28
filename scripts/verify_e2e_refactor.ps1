# Comprehensive E2E Binary Verification for LDM (Windows Native PowerShell)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for low-spec machines.

$ErrorActionPreference = "Stop"
$currentDir = Get-Location
echo "🚀 Starting Binary Verification (Windows Native)..."

# Determine the binary command
$LDM_CMD = "ldm"
if (-not (Get-Command $LDM_CMD -ErrorAction SilentlyContinue)) {
    echo "❌ ERROR: 'ldm' binary not found in PATH."
    echo "Please run 'ldm upgrade --beta' first."
    exit 1
}

# Unique filename based on machine identity
$Hostname = $env:COMPUTERNAME
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ResultsFile = Join-Path $currentDir "ldm-verify-${Hostname}-${Timestamp}.txt"

echo "🚀 Starting Binary Verification (Windows Native)..."
echo "📊 Results will be saved to: $ResultsFile"

"=== LDM BINARY VERIFICATION REPORT ===" | Out-File -FilePath $ResultsFile
"Timestamp: $(Get-Date)" | Out-File -FilePath $ResultsFile -Append
"Hostname:  $Hostname" | Out-File -FilePath $ResultsFile -Append
"Platform:  Windows Native (PowerShell)" | Out-File -FilePath $ResultsFile -Append
"Binary:    $((Get-Command $LDM_CMD).Source)" | Out-File -FilePath $ResultsFile -Append
"" | Out-File -FilePath $ResultsFile -Append

# Capture logs helper
function Capture-LogsOnFailure {
    "" | Out-File -FilePath $ResultsFile -Append
    "--- FAILURE DEBUG LOGS ---" | Out-File -FilePath $ResultsFile -Append
    $containers = @("liferay-proxy-global", "liferay-search-global", "ldm-smoke-test", "ldm-smoke-test-db-1")
    foreach ($c in $containers) {
        if ((docker ps -a) -match $c) {
            ">> Logs for $c`:" | Out-File -FilePath $ResultsFile -Append
            docker logs $c --tail 50 2>&1 | Out-File -FilePath $ResultsFile -Append
        }
    }
}

# Cleanup helper
function Cleanup-TestProjects {
    # If a failure occurred, capture logs first
    if ($global:Error.Count -gt 0) {
        Capture-LogsOnFailure
        echo ""
        echo "!!! VERIFICATION FAILED !!!"
        
        # Final Rename based on environment slug
        $EnvSlug = & $LDM_CMD doctor --slug
        $EnvSlug = $EnvSlug.Trim()
        $Hash = [System.BitConverter]::ToString((New-Object System.Security.Cryptography.SHA256Managed).ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Timestamp))).Replace("-", "").Substring(0, 8).ToLower()
        $FinalName = "verify-${EnvSlug}-fail-${Hash}.txt"
        
        Move-Item -Path $ResultsFile -Destination (Join-Path $currentDir $FinalName) -Force
        $ResultsFile = Join-Path $currentDir $FinalName

        echo "--- Dumping Results File ($FinalName) ---"
        Get-Content $ResultsFile
        echo "--- End of Results Dump ---"
    }

    echo "🧹 Cleaning up test artifacts..."
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy `
              ldm-smoke-test ldm-smoke-test-db-1 smoke-test-app 2>$null
    if (Test-Path "e2e-work-dir") {
        Remove-Item -Recurse -Force "e2e-work-dir"
    }
}

# Initial Cleanup
Cleanup-TestProjects

# Find the test project template (Flexible search)
$PSScriptDir = $PSScriptRoot
$SearchPaths = @(
    Join-Path (Split-Path $PSScriptDir -Parent) "references\test-project",
    Join-Path $PSScriptDir "test-project",
    Join-Path (Get-Location) "test-project",
    Join-Path (Get-Location) "references\test-project"
)

$TemplateSrc = ""
foreach ($path in $SearchPaths) {
    if (Test-Path $path) {
        $TemplateSrc = $path
        break
    }
}

if ($TemplateSrc -eq "") {
    echo "❌ ERROR: Test project template folder not found."
    echo "Please ensure the 'references/test-project' folder is available."
    exit 1
}
echo "ℹ  Using test template: $TemplateSrc"

# Isolate the LDM workspace
$LDM_WORKSPACE = Join-Path $currentDir "e2e-work-dir"
$env:LDM_WORKSPACE = $LDM_WORKSPACE
New-Item -ItemType Directory -Force -Path $LDM_WORKSPACE
Set-Location $LDM_WORKSPACE

# Helper to log and run
function Log-AndRun($msg, $cmd) {
    echo ">> $msg" | tee -a $ResultsFile
    Invoke-Expression $cmd 2>&1 | tee -a $ResultsFile
}

try {
    # --- Metadata Collection ---
    echo "--- Capturing Environment State ---" | tee -a $ResultsFile
    Invoke-Expression "$LDM_CMD doctor --skip-project" 2>&1 | Out-File -FilePath $ResultsFile -Append
    "" | Out-File -FilePath $ResultsFile -Append
    "--- Test Execution Log ---" | Out-File -FilePath $ResultsFile -Append

    # 0. Prepare a Clean Slate
    echo "--- Step 0: Total Cleanup ---" | tee -a $ResultsFile
    Log-AndRun "Removing all LDM resources" "$LDM_CMD -y rm --all --delete --infra"

    # Verify Docker is empty
    $containers = docker ps -aq
    if ($containers) {
        "❌ ERROR: Docker environment is not empty." | tee -a $ResultsFile
        "Existing containers detected:" | Out-File -FilePath $ResultsFile -Append
        docker ps -a | Out-File -FilePath $ResultsFile -Append
        throw "Docker environment is not empty. Please run 'docker rm -f (docker ps -aq)' first."
    }
    echo "✅ Docker environment is clean." | tee -a $ResultsFile

    # 1. Global Infra Setup
    echo "--- Step 1: Global Infra Setup ---"
    Log-AndRun "Initializing Infrastructure" "$LDM_CMD -y infra-setup --search"
    if ((docker ps) -notmatch "liferay-search-global") {
        throw "ERROR: Global Search failed to start"
    }

    # Verify search backup repository is registered
    $regCheck = docker exec liferay-search-global curl -s localhost:9200/_snapshot/liferay_backup
    if ($regCheck -notmatch "liferay_backup") {
        "❌ ERROR: Global Search backup repository not registered" | tee -a $ResultsFile
        throw "Global Search backup repository not registered"
    }
    "✅ Global Search backup repository verified." | tee -a $ResultsFile

    # 2. Project Lifecycle
    echo "--- Step 2: Project Run ---"
    Copy-Item -Recurse "$TemplateSrc" "ldm-smoke-test"
    Set-Location "ldm-smoke-test"

    Log-AndRun "Running project init" "$LDM_CMD -y run . --no-wait --no-tld-skip --no-jvm-verify"

    # 3. Snapshot & Restore
    echo "--- Step 3: Snapshot & Restore ---"
    Log-AndRun "Creating Snapshot" "$LDM_CMD -y snapshot --name Binary-Verify"
    if (-not (Test-Path "snapshots")) {
        throw "ERROR: Snapshot directory not created"
    }
    Log-AndRun "Restoring Snapshot" "$LDM_CMD -y restore --latest"

    # 4. Status & Logs
    echo "--- Step 4: Status & Logs ---"
    Log-AndRun "Checking LDM Status" "$LDM_CMD -y status"
    Log-AndRun "Checking logs" "$LDM_CMD -y logs --no-wait"

    # 5. Teardown
    echo "--- Step 5: Teardown ---"
    Log-AndRun "Tearing down with infra" "$LDM_CMD -y down --infra"

    echo "" | Out-File -FilePath $ResultsFile -Append
    echo "🎯 ALL E2E VERIFICATIONS PASSED!" | tee -a $ResultsFile
    
    # Final Rename based on environment slug
    $EnvSlug = & $LDM_CMD doctor --slug
    $EnvSlug = $EnvSlug.Trim()
    $Hash = [System.BitConverter]::ToString((New-Object System.Security.Cryptography.SHA256Managed).ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Timestamp))).Replace("-", "").Substring(0, 8).ToLower()
    $FinalName = "verify-${EnvSlug}-pass-${Hash}.txt"
    
    Move-Item -Path $ResultsFile -Destination (Join-Path $currentDir $FinalName) -Force

    echo "Full results available in: $FinalName"
}
finally {
    Set-Location $currentDir
    Cleanup-TestProjects
}
