# Comprehensive E2E Binary Verification for LDM (Windows Native PowerShell)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for Windows Native Docker/WSL2.
# Version: 2.4.26-beta.79

# Fix encoding for console output (rocket emoji etc)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
$ORIGINAL_PWD = Get-Location
Write-Host "🚀 Starting Binary Verification (Windows Native)..."

# Determine the binary command
$LDM_CMD = "ldm"
if (-not (Get-Command $LDM_CMD -ErrorAction SilentlyContinue)) {
    Write-Host "❌ ERROR: 'ldm' binary not found in PATH."
    Write-Host "Please ensure LDM is installed and in your PATH."
    exit 1
}

# Unique filename based on machine identity
$Hostname = $env:COMPUTERNAME
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RESULTS_FILE_TMP = Join-Path $ORIGINAL_PWD ".ldm-verify-tmp-${Timestamp}.txt"

Write-Host "📊 Results will be saved to temporary file: $RESULTS_FILE_TMP"

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
    param([int]$ExitCode)
    
    $status = "pass"
    if ($ExitCode -ne 0) {
        $status = "fail"
        Capture-LogsOnFailure
        Write-Host ""
        Write-Host "!!! VERIFICATION FAILED (Exit Code: $ExitCode) !!!"
    }

    # Final Rename based on environment slug
    $EnvSlug = & $LDM_CMD doctor --slug 2>$null
    if ($null -eq $EnvSlug) {
        $EnvSlug = "unknown-env"
    } else {
        $EnvSlug = $EnvSlug.Trim().Replace(" ", "-")
    }
    
    # Calculate short hash for uniqueness
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $hashBytes = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Timestamp))
    $shortHash = ([System.BitConverter]::ToString($hashBytes).Replace("-", "")).Substring(0, 8).ToLower()
    
    $FinalName = "verify-${EnvSlug}-${status}-${shortHash}.txt"
    $FinalPath = Join-Path $ORIGINAL_PWD $FinalName

    if (Test-Path $RESULTS_FILE_TMP) {
        Move-Item -Path $RESULTS_FILE_TMP -Destination $FinalPath -Force
        Write-Host ""
        Write-Host "================================================================"
        Write-Host "✅ Verification Complete ($status)"
        Write-Host "📊 Results: $FinalName"
        Write-Host "================================================================"
    }

    Write-Host "🧹 Cleaning up test artifacts..."
    # Project stack via LDM (surgical)
    $env:LDM_WORKSPACE = Join-Path $ORIGINAL_PWD "e2e-work-dir"
    & $LDM_CMD -y rm ldm-smoke-test --delete --infra > $null 2>&1
    
    if (Test-Path $env:LDM_WORKSPACE) {
        Remove-Item -Recurse -Force $env:LDM_WORKSPACE -ErrorAction SilentlyContinue
    }
}

# Find the test project template
$TemplateSrc = ""
$PotentialPaths = @(
    Join-Path (Split-Path $PSScriptRoot -Parent) "references\test-project",
    Join-Path $PSScriptRoot "test-project",
    Join-Path $ORIGINAL_PWD "test-project",
    Join-Path $ORIGINAL_PWD "references\test-project"
)

foreach ($path in $PotentialPaths) {
    if (Test-Path $path) {
        $TemplateSrc = $path
        break
    }
}

if ([string]::IsNullOrEmpty($TemplateSrc)) {
    Write-Host "❌ ERROR: Test project template folder not found."
    exit 1
}
Write-Host "ℹ  Using test template: $TemplateSrc"

# Isolate the LDM workspace
$LDM_WORKSPACE = Join-Path $ORIGINAL_PWD "e2e-work-dir"
$env:LDM_WORKSPACE = $LDM_WORKSPACE
if (-not (Test-Path $LDM_WORKSPACE)) {
    New-Item -ItemType Directory -Force -Path $LDM_WORKSPACE | Out-Null
}

# Helper to log and run
function Log-AndRun {
    param(
        [string]$msg,
        [string]$cmd,
        [string]$args_list
    )
    Write-Host ">> $msg"
    ">> $msg" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    
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
    Write-Host $output
    
    if ($p.ExitCode -ne 0) {
        Write-Host "❌ ERROR: Command failed with exit code $($p.ExitCode)"
        "❌ ERROR: Command failed with exit code $($p.ExitCode)" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        throw "Critical failure in $cmd"
    }

    # Scan for FATAL or specific LDM error markers
    if ($output -match "FATAL" -or ($output -match "❌" -and $output -notmatch "not found" -and $output -notmatch "already in sync")) {
        Write-Host "❌ ERROR: Critical failure detected in output."
        "❌ ERROR: Critical failure detected in output." | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        throw "Critical failure marker detected"
    }
}

try {
    # --- Metadata Collection ---
    Write-Host "--- Capturing Environment State ---"
    "--- Capturing Environment State ---" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    & $LDM_CMD doctor --skip-project 2>&1 | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    "" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    "--- Test Execution Log ---" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8

    # 1. Prepare a Clean Slate
    Write-Host "--- Step 0: Targeted Cleanup ---"
    & $LDM_CMD -y rm ldm-smoke-test --delete --infra 2>&1 | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    Write-Host "✅ Clean slate established."

    # 2. Global Infra Setup
    Write-Host "--- Step 1: Global Infra Setup ---"
    Log-AndRun -msg "Initializing Infrastructure" -cmd $LDM_CMD -args_list "-y infra-setup --search"

    # 3. Project Lifecycle
    Write-Host "--- Step 2: Project Run ---"
    Write-Host "ℹ  Provisioning test project 'ldm-smoke-test'..."
    $projectDir = Join-Path $LDM_WORKSPACE "ldm-smoke-test"
    Copy-Item -Recurse "$TemplateSrc" $projectDir
    Set-Location $projectDir

    # Explicit check for the meta file
    if (Test-Path "meta") {
        Write-Host "✅ Project metadata verified (meta)."
    } else {
        Write-Host "❌ ERROR: Project metadata file (meta) missing!"
        throw "Missing meta file"
    }

    Log-AndRun -msg "Running LDM Project" -cmd $LDM_CMD -args_list "-y run . --no-wait --no-tld-skip --no-jvm-verify"

    # 4. Snapshot & Restore Verification
    Write-Host "--- Step 3: Snapshot & Restore ---"
    Log-AndRun -msg "Creating Snapshot" -cmd $LDM_CMD -args_list "-y snapshot --name Binary-Verify"
    if (-not (Test-Path "snapshots")) {
        Write-Host "❌ ERROR: Snapshot directory missing."
        throw "Missing snapshots directory"
    }

    Log-AndRun -msg "Restoring Snapshot" -cmd $LDM_CMD -args_list "-y restore --latest"
    Write-Host "✅ Snapshot and Restore verified."

    # 5. Status and Logs
    Write-Host "--- Step 4: Status & Logs ---"
    Log-AndRun -msg "Checking Status" -cmd $LDM_CMD -args_list "-y status"
    Log-AndRun -msg "Checking Logs" -cmd $LDM_CMD -args_list "-y logs --no-wait"
    Write-Host "✅ Status and Logs verified."

    # 6. Teardown
    Write-Host "--- Step 5: Teardown ---"
    Log-AndRun -msg "Tearing down stack" -cmd $LDM_CMD -args_list "-y down ldm-smoke-test --infra"
    Write-Host "✅ Teardown successful."

    Write-Host "🎯 ALL E2E VERIFICATIONS PASSED!"
    Finalize-Verification 0
}
catch {
    Write-Host "❌ An error occurred during verification: $($_.Exception.Message)"
    Finalize-Verification 1
}
finally {
    Set-Location $ORIGINAL_PWD
}
