# Comprehensive E2E Binary Verification for LDM (Windows Native PowerShell)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for Windows Native Docker/WSL2.

# Fix encoding for console output (rocket emoji etc)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
$ORIGINAL_PWD = Get-Location
echo "🚀 Starting Binary Verification (Windows Native)..."

# Determine the binary command
$LDM_CMD = "ldm"
if (-not (Get-Command $LDM_CMD -ErrorAction SilentlyContinue)) {
    echo "❌ ERROR: 'ldm' binary not found in PATH."
    echo "Please ensure LDM is installed and in your PATH."
    exit 1
}

# Unique filename based on machine identity
$Hostname = $env:COMPUTERNAME
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RESULTS_FILE_TMP = Join-Path $ORIGINAL_PWD ".ldm-verify-tmp-${Timestamp}.txt"

echo "📊 Results will be saved to temporary file: $RESULTS_FILE_TMP"

"=== LDM BINARY VERIFICATION REPORT ===" | Out-File -FilePath $RESULTS_FILE_TMP -Encoding utf8
"Timestamp: $(Get-Date)" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
"Hostname:  $Hostname" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
"Platform:  $($PSVersionTable.OS)" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
"Binary:    $((Get-Command $LDM_CMD).Source)" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
"Version:   $(& $LDM_CMD --version 2>$null)" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
"" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8

# Capture logs helper
function Capture-LogsOnFailure {
    "" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    "--- FAILURE DEBUG LOGS ---" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    $containers = @("liferay-proxy-global", "liferay-search-global", "ldm-smoke-test", "ldm-smoke-test-db-1")
    foreach ($c in $containers) {
        $check = docker ps -a --filter "name=$c" --format "{{.Name}}"
        if ($check -match $c) {
            ">> Logs for $c`:" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
            docker logs $c --tail 50 2>&1 | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        }
    }
}

# Cleanup helper
function Finalize-Verification {
    param($ExitCode)
    
    $status = "pass"
    if ($ExitCode -ne 0) {
        $status = "fail"
        Capture-LogsOnFailure
        echo ""
        echo "!!! VERIFICATION FAILED (Exit Code: $ExitCode) !!!"
    }

    # Final Rename based on environment slug
    $EnvSlug = & $LDM_CMD doctor --slug 2>$null
    $EnvSlug = $EnvSlug.Trim().Replace(" ", "-")
    if ([string]::IsNullOrWhiteSpace($EnvSlug) -or $EnvSlug -eq "unknown") {
        $EnvSlug = "unknown-env"
    }
    
    # Calculate short hash for uniqueness
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $hashBytes = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Timestamp))
    $shortHash = ([System.BitConverter]::ToString($hashBytes).Replace("-", "")).Substring(0, 8).ToLower()
    
    $FinalName = "verify-${EnvSlug}-${status}-${shortHash}.txt"
    $FinalPath = Join-Path $ORIGINAL_PWD $FinalName

    if (Test-Path $RESULTS_FILE_TMP) {
        Move-Item -Path $RESULTS_FILE_TMP -Destination $FinalPath -Force
        echo ""
        echo "================================================================"
        echo "✅ Verification Complete ($status)"
        echo "📊 Results: $FinalName"
        echo "================================================================"
    }

    echo "🧹 Cleaning up test artifacts..."
    # SURGICAL cleanup
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy smoke-test-app 2>$null | Out-Null
    
    # Project stack via LDM
    $env:LDM_WORKSPACE = Join-Path $ORIGINAL_PWD "e2e-work-dir"
    & $LDM_CMD -y rm ldm-smoke-test --delete > $null 2>&1
    
    if (Test-Path $env:LDM_WORKSPACE) {
        Remove-Item -Recurse -Force $env:LDM_WORKSPACE -ErrorAction SilentlyContinue
    }
}

# Find the test project template (Flexible search)
$SearchPaths = @(
    Join-Path (Split-Path $PSScriptRoot -Parent) "references\test-project",
    Join-Path $PSScriptRoot "test-project",
    Join-Path $ORIGINAL_PWD "test-project",
    Join-Path $ORIGINAL_PWD "references\test-project"
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
    exit 1
}
echo "ℹ  Using test template: $TemplateSrc"

# Isolate the LDM workspace
$LDM_WORKSPACE = Join-Path $ORIGINAL_PWD "e2e-work-dir"
$env:LDM_WORKSPACE = $LDM_WORKSPACE
if (-not (Test-Path $LDM_WORKSPACE)) {
    New-Item -ItemType Directory -Force -Path $LDM_WORKSPACE | Out-Null
}

# Helper to log and run
function Log-AndRun($msg, $cmd, $args_list) {
    echo ">> $msg" | tee -a $RESULTS_FILE_TMP
    
    # Execute and capture
    $pinfo = New-Object System.Diagnostics.ProcessStartInfo
    $pinfo.FileName = $cmd
    $pinfo.Arguments = $args_list
    $pinfo.RedirectStandardError = $true
    $pinfo.RedirectStandardOutput = $true
    $pinfo.UseShellExecute = $false
    $pinfo.CreateNoWindow = $true
    
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $pinfo
    $p.Start() | Out-Null
    
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    
    $output = $stdout + $stderr
    $output | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    echo $output
    
    if ($p.ExitCode -ne 0) {
        echo "❌ ERROR: Command failed with exit code $($p.ExitCode)" | tee -a $RESULTS_FILE_TMP
        throw "Critical failure in $cmd"
    }

    # Scan for FATAL or specific LDM error markers (ignoring harmless ones)
    if ($output -match "FATAL" -or ($output -match "❌" -and $output -notmatch "not found" -and $output -notmatch "already in sync")) {
        echo "❌ ERROR: Critical failure detected in output of command: $cmd $args_list" | tee -a $RESULTS_FILE_TMP
        throw "Critical failure marker detected"
    }
}

try {
    # --- Metadata Collection ---
    echo "--- Capturing Environment State ---" | tee -a $RESULTS_FILE_TMP
    & $LDM_CMD doctor --skip-project 2>&1 | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    "" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    "--- Test Execution Log ---" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8

    # 1. Prepare a Clean Slate (SURGICAL)
    echo "--- Step 0: Targeted Cleanup ---" | tee -a $RESULTS_FILE_TMP
    # Silent cleanup attempt
    & $LDM_CMD -y rm ldm-smoke-test --delete --infra 2>&1 | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy 2>&1 | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    echo "✅ Clean slate established." | tee -a $RESULTS_FILE_TMP

    # 2. Global Infra Setup
    echo "--- Step 1: Global Infra Setup ---"
    Log-AndRun "Initializing Infrastructure" $LDM_CMD "-y infra-setup --search"

    # 3. Project Lifecycle
    echo "--- Step 2: Project Run ---"
    echo "ℹ  Provisioning test project 'ldm-smoke-test' from template..." | tee -a $RESULTS_FILE_TMP
    $projectDir = Join-Path $LDM_WORKSPACE "ldm-smoke-test"
    Copy-Item -Recurse "$TemplateSrc" $projectDir
    Set-Location $projectDir

    # Explicit check for the meta file
    if (Test-Path "meta") {
        echo "✅ Project metadata verified (meta)." | tee -a $RESULTS_FILE_TMP
    } else {
        echo "❌ ERROR: Project metadata file (meta) was not copied correctly!" | tee -a $RESULTS_FILE_TMP
        throw "Missing meta file"
    }

    Log-AndRun "Running LDM Project" $LDM_CMD "-y run . --no-wait --no-tld-skip --no-jvm-verify"

    # 4. Snapshot & Restore Verification
    echo "--- Step 3: Snapshot & Restore ---"
    Log-AndRun "Creating Snapshot" $LDM_CMD "-y snapshot --name Binary-Verify"
    if (-not (Test-Path "snapshots")) {
        echo "❌ ERROR: Snapshot directory 'snapshots/' was not created." | tee -a $RESULTS_FILE_TMP
        throw "Missing snapshots directory"
    }

    Log-AndRun "Restoring Snapshot" $LDM_CMD "-y restore --latest"
    echo "✅ Snapshot and Restore verified." | tee -a $RESULTS_FILE_TMP

    # 5. Status and Logs
    echo "--- Step 4: Status & Logs ---"
    Log-AndRun "Checking Status" $LDM_CMD "-y status"
    Log-AndRun "Checking Logs" $LDM_CMD "-y logs --no-wait"
    echo "✅ Status and Logs verified." | tee -a $RESULTS_FILE_TMP

    # 6. Teardown
    echo "--- Step 5: Teardown ---"
    Log-AndRun "Tearing down stack" $LDM_CMD "-y down ldm-smoke-test --infra"
    echo "✅ Teardown successful." | tee -a $RESULTS_FILE_TMP

    "" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    echo "🎯 ALL E2E VERIFICATIONS PASSED!" | tee -a $RESULTS_FILE_TMP
    Finalize-Verification 0
}
catch {
    Finalize-Verification 1
}
finally {
    Set-Location $ORIGINAL_PWD
}
