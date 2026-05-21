# Comprehensive E2E Binary Verification for LDM (Windows Native PowerShell)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for Windows Native.

# Force UTF-8 for Python and PowerShell
$env:PYTHONUTF8 = 1
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
$ORIGINAL_PWD = Get-Location
Write-Host "--- Starting Binary Verification (Windows Native) ---"

# Determine the binary command
$LDM_CMD = "ldm"
if (-not (Get-Command $LDM_CMD -ErrorAction SilentlyContinue))
{
    Write-Host "❌ ERROR: 'ldm' binary not found in PATH."
    Write-Host "Please ensure LDM is installed and in your PATH."
    exit 1
}

# Unique filename based on machine identity
$Hostname = $env:COMPUTERNAME
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

# IMPORTANT: Keep RESULTS_FILE_TMP in ORIGINAL_PWD so it isn't deleted by work-dir cleanup
$RESULTS_FILE_TMP = Join-Path $ORIGINAL_PWD ".ldm-verify-tmp-${Timestamp}.txt"

& {
    Write-Output "=== LDM BINARY VERIFICATION REPORT ==="
    Write-Output "Timestamp: $(Get-Date)"
    Write-Output "Hostname:  $Hostname"
    Write-Output "Platform:  $($PSVersionTable.OS)"
    Write-Output "Binary:    $((Get-Command $LDM_CMD).Source)"
    Write-Output "Version:   $(& $LDM_CMD --version 2>$null)"
    
    # Capture Provider Versions explicitly for the header
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        $dv = & docker version --format '{{.Server.Version}}' 2>$null
        Write-Output "Docker:    $dv"
        if (docker compose version 2>$null) {
            $cv = & docker compose version --short 2>$null
            Write-Output "Compose:   $cv"
        }
    }
    Write-Output ""
} | Out-File -FilePath $RESULTS_FILE_TMP -Encoding utf8

# Capture logs helper
function Capture-LogsOnFailure 
{
    "" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    "--- FAILURE DEBUG LOGS ---" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    Write-Host "`n--- FAILURE DEBUG LOGS ---" -ForegroundColor Red
    
    $containers = @("liferay-proxy-global", "liferay-search-global", "ldm-smoke-test", "ldm-smoke-test-db-1")
    foreach ($c in $containers) 
    {
        # Get container name precisely
        $check = docker ps -a --filter "name=$c" --format "{{.Names}}"
        if ($check -match $c) 
        {
            $logHeader = ">> Logs for $c`:"
            $logHeader | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
            Write-Host $logHeader -ForegroundColor Cyan
            
            $logs = docker logs $c --tail 50 2>&1
            $logs | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
            Write-Host $logs
        }
    }
}

# Cleanup helper
function Finalize-Verification 
{
    param($ExitCode)
    
    # Determine status for filename
    $status = "pass"
    if ($ExitCode -ne 0) 
    {
        $status = "fail"
        Capture-LogsOnFailure
        Write-Host ""
        Write-Host "!!! VERIFICATION FAILED (Exit Code: $ExitCode) !!!" -ForegroundColor Red
    }

    # Final Rename based on environment slug
    $EnvSlug = & $LDM_CMD doctor --slug 2>$null
    if ($null -ne $EnvSlug) { $EnvSlug = $EnvSlug.Trim().Replace(" ", "-") }
    if ([string]::IsNullOrWhiteSpace($EnvSlug) -or $EnvSlug -eq "unknown") { $EnvSlug = "unknown-env" }
    
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $hashBytes = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Timestamp))
    $shortHash = ([System.BitConverter]::ToString($hashBytes).Replace("-", "")).Substring(0, 8).ToLower()
    
    $FinalName = "verify-${EnvSlug}-${status}-${shortHash}.txt"
    $FinalPath = Join-Path $ORIGINAL_PWD $FinalName

    # Move Playwright screenshots to root before deletion
    $playwrightResults = Join-Path $LDM_WORKSPACE "ldm-smoke-test\test-results"
    if (Test-Path $playwrightResults) 
    {
        Copy-Item -Path $playwrightResults -Destination $ORIGINAL_PWD -Recurse -Force
    }

    # Move report to final location BEFORE deleting the work dir
    if (Test-Path $RESULTS_FILE_TMP) 
    {
        Move-Item -Path $RESULTS_FILE_TMP -Destination $FinalPath -Force
        Write-Host ""
        Write-Host "================================================================"
        Write-Host "✅ Verification Complete ($status)"
        Write-Host "📊 Results: $FinalName"
        
        # Automatically archive passing reports
        if ($status -eq "pass") 
        {
            $archiveDir = Join-Path $ORIGINAL_PWD "references\verification-results"
            if (-not (Test-Path $archiveDir)) { New-Item -ItemType Directory -Path $archiveDir | Out-Null }
            Copy-Item -Path $FinalPath -Destination $archiveDir -Force
            Write-Host "📦 Archived to: references/verification-results/"
        }
        Write-Host "================================================================"
    }

    Write-Host "🧹 Cleaning up test artifacts..."
    # SURGICAL cleanup: only remove what we created
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy smoke-test-app 2>$null | Out-Null
    
    $env:LDM_WORKSPACE = Join-Path $ORIGINAL_PWD "e2e-work-dir"
    & $LDM_CMD -y rm ldm-smoke-test --delete > $null 2>&1
    
    if (Test-Path $env:LDM_WORKSPACE) { 
        Remove-Item -Recurse -Force $env:LDM_WORKSPACE -ErrorAction SilentlyContinue 
    }
}

# Find the test project template (Flexible search)
$SearchPaths = @(
    Join-Path $PSScriptRoot "..\references\test-project"
    Join-Path $PSScriptRoot "test-project"
    Join-Path $ORIGINAL_PWD "test-project"
    Join-Path $ORIGINAL_PWD "references\test-project"
)

$TemplateSrc = ""
foreach ($path in $SearchPaths) 
{
    if (Test-Path $path) { $TemplateSrc = $path; break }
}

if ($TemplateSrc -eq "") 
{
    Write-Host "❌ ERROR: Test project template folder not found."
    Write-Host "Please ensure you are running from the repository root or that 'references\test-project' is present."
    exit 1
}
Write-Host "ℹ  Using test template: $TemplateSrc"

# Isolate the LDM workspace
$LDM_WORKSPACE = Join-Path $ORIGINAL_PWD "e2e-work-dir"
$env:LDM_WORKSPACE = $LDM_WORKSPACE
if (-not (Test-Path $LDM_WORKSPACE)) { New-Item -ItemType Directory -Force -Path $LDM_WORKSPACE | Out-Null }

# Helper to log and run
function Log-AndRun
{
    param($msg, $cmd, $args_list)
    Write-Host ">> $msg"
    ">> $msg" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    
    # Temporary file for capturing output (similar to mktemp)
    $tmp_output = [System.IO.Path]::GetTempFileName()
    
    $pinfo = New-Object System.Diagnostics.ProcessStartInfo
    $pinfo.FileName = $cmd
    $pinfo.Arguments = $args_list
    $pinfo.RedirectStandardError = $true
    $pinfo.RedirectStandardOutput = $true
    $pinfo.UseShellExecute = $false
    $pinfo.CreateNoWindow = $true
    $pinfo.WorkingDirectory = $PWD.Path
    $pinfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $pinfo
    $p.Start() | Out-Null
    
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    
    $output = $stdout + $stderr
    $output | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    $output | Out-File -FilePath $tmp_output -Encoding utf8
    Write-Host $output
    
    if ($p.ExitCode -ne 0) 
    {
        "❌ ERROR: Command failed with exit code $($p.ExitCode): $cmd $args_list" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        Remove-Item $tmp_output -ErrorAction SilentlyContinue
        throw "Critical failure in $cmd"
    }

    # Scan for FATAL or specific LDM error markers
    $content = Get-Content $tmp_output
    $hasError = $false
    foreach($line in $content) {
        if ($line -match "FATAL|❌|ERROR:" -and $line -notmatch "ℹ|>>|not found|already in sync") {
            $hasError = $true
            break
        }
    }

    if ($hasError)
    {
        "❌ ERROR: Critical failure detected in output of command: $cmd $args_list" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        Remove-Item $tmp_output -ErrorAction SilentlyContinue
        throw "Critical failure marker detected"
    }

    Remove-Item $tmp_output -ErrorAction SilentlyContinue
}

# Helper to log and run without scanning for error markers
function Log-AndRunNoScan
{
    param($msg, $cmd, $args_list)
    Write-Host ">> $msg"
    ">> $msg" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    
    $pinfo = New-Object System.Diagnostics.ProcessStartInfo
    $pinfo.FileName = $cmd
    $pinfo.Arguments = $args_list
    $pinfo.RedirectStandardError = $true
    $pinfo.RedirectStandardOutput = $true
    $pinfo.UseShellExecute = $false
    $pinfo.CreateNoWindow = $true
    $pinfo.WorkingDirectory = $PWD.Path
    $pinfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $pinfo
    $p.Start() | Out-Null
    
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    
    $output = $stdout + $stderr
    $output | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    Write-Host $output
    
    if ($p.ExitCode -ne 0) 
    {
        "❌ ERROR: Command failed with exit code $($p.ExitCode): $cmd $args_list" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        throw "Critical failure in $cmd"
    }
}

try 
{
    # --- Metadata Collection ---
    Write-Host "--- Capturing Environment State ---"
    "--- Capturing Environment State ---" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    # Capture doctor output to file and console (mimic tee)
    $doctorOut = & $LDM_CMD doctor --skip-project 2>&1
    $doctorOut | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    Write-Host $doctorOut
    "" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    "--- Test Execution Log ---" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8

    # 1. Prepare a Clean Slate (SURGICAL)
    Write-Host "--- Step 0: Targeted Cleanup ---"
    {
        Write-Output ">> Preparing clean slate (removing project and infra if they exist)"
        & $LDM_CMD -y rm ldm-smoke-test --delete --infra 2>&1
        docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy 2>&1
        
        # Wipe global search data to prevent mapping corruption on restart
        $esDataDir = Join-Path $HOME ".ldm\infra\search\data"
        if (Test-Path $esDataDir) { Remove-Item -Recurse -Force $esDataDir -ErrorAction SilentlyContinue }
    } | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    Write-Host "✅ Clean slate established."
    "✅ Clean slate established." | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8

    # 2. Global Infra Setup
    Write-Host "--- Step 1: Global Infra Setup ---"
    Log-AndRun -msg "Initializing Infrastructure" -cmd $LDM_CMD -args_list "-y infra-setup --search"

    # 2b. Edge Case Guardrails
    Write-Host "--- Step 1b: Edge Case Guardrails ---"
    
    # Test: No-Sudo Guard (Simulation)
    Write-Host ">> Verifying No-Sudo Guard..."
    $sudoScript = @"
import os
import sys
import ldm_core.cli
os.geteuid = lambda: 0
try:
    ldm_core.cli.main()
except SystemExit as e:
    sys.exit(e.code)
"@
    $sudoScript | Out-File -FilePath "fake_sudo.py" -Encoding utf8
    $res = & python fake_sudo.py run . --tag 2026.q1.4-lts --no-wait --no-up -y 2>&1
    if ($res -match "Do not run LDM with 'sudo'") {
        Write-Host "✅ Sudo Guard verified."
    } else {
        Write-Host "❌ ERROR: Sudo Guard failed!"
        exit 1
    }
    Remove-Item "fake_sudo.py"

    # Test: Dev Guardrails
    Write-Host ">> Verifying Dev Guardrails..."
    $res = & $LDM_CMD version --bump patch -y 2>&1
    if ($res -match "Developer utility requires LDM_DEV_MODE=true") {
        Write-Host "✅ Dev Guardrails verified."
    } else {
        Write-Host "❌ ERROR: Dev Guardrails failed!"
        exit 1
    }

    # Test: Project Collision
    Write-Host ">> Verifying Project Collision Detection..."
    & $LDM_CMD -y run "collision-test" --tag 2026.q1.4-lts --port 8099 --no-wait --no-up > $null 2>&1
    New-Item -ItemType Directory -Path "collision-test\nested" -Force | Out-Null
    Set-Location "collision-test\nested"
    $res = & $LDM_CMD -y run "collision-test" --port 8099 --no-wait --no-up 2>&1
    if ($res -match "Project collision" -or $res -match "already registered") {
        Write-Host "✅ Project Collision detection verified."
    } else {
        Write-Host "❌ ERROR: Collision detection failed!"
        exit 1
    }
    Set-Location ..\..
    & $LDM_CMD -y rm "collision-test" --delete > $null 2>&1
    Remove-Item -Recurse -Force "collision-test" -ErrorAction SilentlyContinue

    # 3. Project Lifecycle
    Write-Host "--- Step 2: Project Run ---"
    $msg = "ℹ  Provisioning test project 'ldm-smoke-test' from template..."
    Write-Host $msg
    $msg | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    
    $projectDir = Join-Path $LDM_WORKSPACE "ldm-smoke-test"
    if (-not (Test-Path $projectDir)) { New-Item -ItemType Directory -Force -Path $projectDir | Out-Null }
    Copy-Item -Path "$TemplateSrc\*" -Destination $projectDir -Recurse -Force
    Set-Location $projectDir

    if (Test-Path "meta") {
        Write-Host "✅ Project metadata verified (meta)."
        "✅ Project metadata verified (meta)." | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    } else {
        Write-Host "❌ ERROR: Project metadata file (meta) was not copied correctly!"
        "❌ ERROR: Project metadata file (meta) was not copied correctly!" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        exit 1
    }

    Log-AndRun -msg "Running LDM Project" -cmd $LDM_CMD -args_list "-y run . --no-wait --no-tld-skip --no-jvm-verify"

    # 2b. Wait for Liferay Health (Required for UI Tests)
    Write-Host "--- Step 2b: Wait for Liferay Health ---"
    $project_name = "ldm-smoke-test"
    $max_retries = 90
    $count = 0
    while ($true) {
        $health = docker inspect -f '{{.State.Health.Status}}' $project_name 2>$null

        # Fast-track: check logs for startup marker
        $logCheck = docker logs $project_name 2>&1
        if ($logCheck -match "org.apache.catalina.startup.Catalina.start Server startup in") {
            Write-Host "`n✅ Liferay Tomcat has started (detected via logs)."
            break
        }

        if ($health -eq "healthy") {
            Write-Host "`n✅ Liferay is healthy."
            break
        }

        if ($count -ge $max_retries) {
            Write-Error "Timeout waiting for Liferay health."
            exit 1
        }

        Write-Host -NoNewline "."
        Start-Sleep -Seconds 10
        $count++
    }
    Write-Host ""

    # 2b-Sequenced: Delayed Fragment Deployment
    Write-Host "--- Step 2b: Sequenced Fragment Deployment ---"
    $fragmentZip = Join-Path $LDM_WORKSPACE "ldm-smoke-test\delayed-deploy\test-fragments.zip"
    if (Test-Path $fragmentZip) {
        Write-Host ">> Triggering hot-deployment of test-fragments.zip..."
        Copy-Item -Path $fragmentZip -Destination (Join-Path $projectDir "deploy") -Force
        Write-Host ">> Waiting 30s for Liferay auto-deploy scanner to process the fragment..."
        Start-Sleep -Seconds 30
    } else {
        Write-Host "⚠️  WARNING: delayed-deploy/test-fragments.zip not found!"
    }

    # 2c. UI Verification (Fragments)
    Write-Host "--- Step 2c: UI Verification (Fragments) ---"
    $pyScript = Join-Path $ORIGINAL_PWD "ldm_core\tests\e2e_ui_fragments.py"
    Log-AndRun -msg "Running Playwright UI Tests" -cmd "pytest" -args_list "$pyScript --no-cov --base-url http://localhost:8082 --screenshot=only-on-failure"
    Write-Host "✅ UI Verification successful."

    # 4. Snapshot & Restore Verification
    Write-Host "--- Step 3: Snapshot & Restore ---"
    Log-AndRun -msg "Creating Snapshot" -cmd $LDM_CMD -args_list "-y snapshot --name Binary-Verify"

    if (-not (Test-Path "snapshots")) {
        Write-Host "❌ ERROR: Snapshot directory 'snapshots/' was not created."
        "❌ ERROR: Snapshot directory 'snapshots/' was not created!" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        exit 1
    }

    Log-AndRun -msg "Restoring Snapshot" -cmd $LDM_CMD -args_list "-y restore --latest"
    Write-Host "✅ Snapshot and Restore verified."

    # 3b. Integrity Verification
    Write-Host "--- Step 3b: Integrity Verification ---"
    $latestSnapDir = Get-ChildItem -Path "snapshots" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -ne $latestSnapDir) {
        $shaFile = Join-Path $latestSnapDir.FullName "files.tar.gz.sha256"
        if (Test-Path $shaFile) {
            Write-Host "ℹ  Tampering with checksum to test enforcement..."
            "CORRUPTED-DATA" | Out-File -FilePath $shaFile -Encoding utf8

            Write-Host ">> Attempting restore of tampered snapshot (Expected Failure)..."
            $res = & $LDM_CMD -y restore --latest 2>&1
            if ($res -match "Integrity check failed") {
                Write-Host "✅ Tampered snapshot correctly rejected."
            } else {
                Write-Host "❌ ERROR: Tampered snapshot was NOT rejected!"
                exit 1
            }

            Log-AndRun -msg "Restoring with --no-verify override" -cmd $LDM_CMD -args_list "-y restore --latest --no-verify"
            Write-Host "✅ Integrity override verified."
        }
    }

    # 4c. Advanced Configurations & UX
    Write-Host "--- Step 3c: Advanced Configurations & UX ---"

    # Test: Env Sync
    Write-Host ">> Verifying Env Sync..."
    & $LDM_CMD env . TEST_SECRET=supersecret123 > $null 2>&1
    $compose = Get-Content "docker-compose.yml" -Raw
    if ($compose -match "TEST_SECRET=supersecret123") {
        Write-Host "✅ Env Sync verified."
    } else {
        Write-Host "❌ ERROR: Env Sync failed!"
        exit 1
    }

    # Test: Redaction Check
    Write-Host ">> Verifying Redaction Check..."
    $redactScript = @"
from ldm_core.utils import run_command
from ldm_core.ui import UI
UI.VERBOSE = True
run_command("echo DB_PASSWORD=hidden_secret", shell=True, verbose=True)
"@
    $redactScript | Out-File -FilePath "test_redact.py" -Encoding utf8
    $res = & python test_redact.py 2>&1
    if ($res -match "DB_PASSWORD=\[REDACTED\]" -and -not ($res -match "hidden_secret")) {
        Write-Host "✅ Redaction Check verified."
    } else {
        Write-Host "❌ ERROR: Redaction Check failed!"
        exit 1
    }
    Remove-Item "test_redact.py"

    # Test: Multi-Node Scaling (Metadata Update)
    Write-Host ">> Verifying Multi-Node Scaling Metadata..."
    $scaleScript = @"
import sys
from unittest.mock import patch
import ldm_core.cli
with patch("ldm_core.handlers.runtime.RuntimeService.cmd_run"):
    sys.argv = ["ldm", "-y", "scale", "ldm-smoke-test", "liferay=3"]
    try:
        ldm_core.cli.main()
    except SystemExit:
        pass
"@
    $scaleScript | Out-File -FilePath "test_scale.py" -Encoding utf8
    & python test_scale.py > $null 2>&1
    $meta = Get-Content "meta" -Raw
    if ($meta -match "scale_liferay=3") {
        Write-Host "✅ Multi-Node Scaling metadata verified."
    } else {
        Write-Host "❌ ERROR: Scale metadata update failed!"
        exit 1
    }
    Remove-Item "test_scale.py"

    # 5. Status and Logs
    Write-Host "--- Step 4: Status & Logs ---"
    Log-AndRun -msg "Checking Status" -cmd $LDM_CMD -args_list "-y status"
    Log-AndRunNoScan -msg "Checking Logs" -cmd $LDM_CMD -args_list "-y logs --no-wait"
    Write-Host "✅ Status and Logs verified."
    "✅ Status and Logs verified." | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8

    # 6. Teardown
    Write-Host "--- Step 5: Teardown ---"
    Log-AndRun -msg "Tearing down stack" -cmd $LDM_CMD -args_list "-y down ldm-smoke-test --infra"
    Write-Host "✅ Teardown successful."
    "✅ Teardown successful." | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8

    "" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    Write-Host "🎯 ALL E2E VERIFICATIONS PASSED!"
    "🎯 ALL E2E VERIFICATIONS PASSED!" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    
    Finalize-Verification 0
}
catch 
{
    Write-Host $_.Exception.Message
    Finalize-Verification 1
}
finally 
{
    Set-Location $ORIGINAL_PWD
}
