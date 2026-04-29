# Comprehensive E2E Binary Verification for LDM (Windows Native PowerShell)
# Target: Standalone Binary Verification (Hardened for Windows)

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
    Write-Host "ERROR: 'ldm' binary not found in PATH."
    exit 1
}

# Unique filename based on machine identity
$Hostname = $env:COMPUTERNAME
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RESULTS_FILE_TMP = Join-Path $ORIGINAL_PWD ".ldm-verify-tmp-${Timestamp}.txt"

"=== LDM BINARY VERIFICATION REPORT ===" | Out-File -FilePath $RESULTS_FILE_TMP -Encoding utf8
"Timestamp: $(Get-Date)" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
"Hostname:  $Hostname" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
"Platform:  $($PSVersionTable.OS)" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
"Binary:    $((Get-Command $LDM_CMD).Source)" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
"Version:   $(& $LDM_CMD --version 2>$null)" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
"" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8

# Capture logs helper
function Capture-LogsOnFailure 
{
    "" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    "--- FAILURE DEBUG LOGS ---" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    $containers = @("liferay-proxy-global", "liferay-search-global", "ldm-smoke-test", "ldm-smoke-test-db-1")
    foreach ($c in $containers) 
    {
        $check = docker ps -a --filter "name=$c" --format "{{.Name}}"
        if ($check -match $c) 
        {
            ">> Logs for $c`:" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
            docker logs $c --tail 50 2>&1 | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        }
    }
}

# Cleanup helper
function Finalize-Verification 
{
    param($ExitCode)
    
    $status = "pass"
    if ($ExitCode -ne 0) 
    {
        $status = "fail"
        Capture-LogsOnFailure
        Write-Host "!!! VERIFICATION FAILED (Exit Code: $ExitCode) !!!"
    }

    $EnvSlug = & $LDM_CMD doctor --slug 2>$null
    if ($null -ne $EnvSlug) { $EnvSlug = $EnvSlug.Trim().Replace(" ", "-") }
    if ([string]::IsNullOrWhiteSpace($EnvSlug) -or $EnvSlug -eq "unknown") { $EnvSlug = "unknown-env" }
    
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $hashBytes = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Timestamp))
    $shortHash = ([System.BitConverter]::ToString($hashBytes).Replace("-", "")).Substring(0, 8).ToLower()
    
    $FinalName = "verify-${EnvSlug}-${status}-${shortHash}.txt"
    $FinalPath = Join-Path $ORIGINAL_PWD $FinalName

    if (Test-Path $RESULTS_FILE_TMP) 
    {
        Move-Item -Path $RESULTS_FILE_TMP -Destination $FinalPath -Force
        Write-Host "Verification Complete ($status). Results: $FinalName"
    }

    Write-Host "Cleaning up artifacts..."
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy smoke-test-app 2>$null | Out-Null
    $env:LDM_WORKSPACE = Join-Path $ORIGINAL_PWD "e2e-work-dir"
    & $LDM_CMD -y rm ldm-smoke-test --delete > $null 2>&1
    if (Test-Path $env:LDM_WORKSPACE) { Remove-Item -Recurse -Force $env:LDM_WORKSPACE -ErrorAction SilentlyContinue }
}

# Template Discovery
$SearchPaths = @()
$SearchPaths += Join-Path $ORIGINAL_PWD "references\test-project"
$SearchPaths += Join-Path $ORIGINAL_PWD "test-project"

$TemplateSrc = ""
foreach ($path in $SearchPaths) 
{
    if (Test-Path $path) { $TemplateSrc = $path; break }
}

if ($TemplateSrc -eq "") 
{
    Write-Host "ERROR: Test project template folder not found."
    exit 1
}

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
    
    $pinfo = New-Object System.Diagnostics.ProcessStartInfo
    $pinfo.FileName = $cmd
    $pinfo.Arguments = $args_list
    $pinfo.RedirectStandardError = $true
    $pinfo.RedirectStandardOutput = $true
    $pinfo.UseShellExecute = $false
    $pinfo.CreateNoWindow = $true
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
        "ERROR: Command failed with exit code $($p.ExitCode)" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        throw "Critical failure in $cmd"
    }

    if ($output -match "FATAL" -or ($output -match "ERROR:" -and $output -notmatch "not found" -and $output -notmatch "already in sync")) 
    {
        throw "Critical failure marker detected"
    }
}

try 
{
    Write-Host "--- Step 0: Targeted Cleanup ---"
    & $LDM_CMD -y rm ldm-smoke-test --delete --infra 2>&1 | Out-Null
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy 2>&1 | Out-Null
    
    # Wipe global search data to prevent mapping corruption on restart
    $esDataDir = Join-Path $HOME ".ldm\infra\search\data"
    if (Test-Path $esDataDir) { Remove-Item -Recurse -Force $esDataDir -ErrorAction SilentlyContinue }

    Write-Host "--- Step 1: Global Infra Setup ---"
    Log-AndRun -msg "Initializing Infrastructure" -cmd $LDM_CMD -args_list "-y infra-setup --search"

    Write-Host "--- Step 2: Project Run ---"
    $projectDir = Join-Path $LDM_WORKSPACE "ldm-smoke-test"
    
    # Surgical Copy
    if (-not (Test-Path $projectDir)) { New-Item -ItemType Directory -Force -Path $projectDir | Out-Null }
    Copy-Item -Path "$TemplateSrc\*" -Destination $projectDir -Recurse -Force
    Set-Location $projectDir

    # Hard-fix: Ensure the 'meta' file exists and is correctly encoded for Windows
    if (-not (Test-Path "meta")) {
        Write-Host "Warning: meta file missing after copy. Attempting to recreate..."
        # Fallback metadata content
        $metaContent = @(
            "tag=2026.q1.4-lts",
            "container_name=ldm-smoke-test",
            "image_tag=alpine",
            "port=8082",
            "db_type=hypersonic",
            "ssl=false"
        )
        $metaContent | Out-File -FilePath "meta" -Encoding utf8
    }

    # LOG: List files in report to debug
    "--- Project Files in $projectDir ---" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    Get-ChildItem | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    "" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8

    Log-AndRun -msg "Running LDM Project" -cmd $LDM_CMD -args_list "-y run `"$projectDir`" --no-wait --no-tld-skip --no-jvm-verify"

    Write-Host "--- Step 3: Snapshot & Restore ---"
    Log-AndRun -msg "Creating Snapshot" -cmd $LDM_CMD -args_list "-y snapshot --name Binary-Verify"
    Log-AndRun -msg "Restoring Snapshot" -cmd $LDM_CMD -args_list "-y restore --latest"

    Write-Host "--- Step 4: Status & Logs ---"
    Log-AndRun -msg "Checking Status" -cmd $LDM_CMD -args_list "-y status"
    Log-AndRun -msg "Checking Logs" -cmd $LDM_CMD -args_list "-y logs --no-wait"

    Write-Host "--- Step 5: Teardown ---"
    Log-AndRun -msg "Tearing down stack" -cmd $LDM_CMD -args_list "-y down ldm-smoke-test --infra"

    Write-Host "ALL E2E VERIFICATIONS PASSED!"
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
