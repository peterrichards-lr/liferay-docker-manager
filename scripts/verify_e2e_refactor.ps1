# Comprehensive E2E Binary Verification for LDM (Windows Native PowerShell)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for Windows Native.

$env:PYTHONUTF8 = 1
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false
$TEST_PORT = "8082"
if ($env:LDM_TEST_PORT) { $TEST_PORT = $env:LDM_TEST_PORT }
$ORIGINAL_PWD = Get-Location
$LDM_CMD = "ldm"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RESULTS_FILE_TMP = Join-Path $ORIGINAL_PWD ".ldm-verify-tmp-${Timestamp}.txt"

Write-Host "* Starting Standalone Binary Verification (Windows Native)..."

# 0. Dependencies & Virtual Environment
$LDM_WORKSPACE = Join-Path $ORIGINAL_PWD "e2e-work-dir"
$env:LDM_WORKSPACE = $LDM_WORKSPACE
if (-not (Test-Path $LDM_WORKSPACE)) { New-Item -ItemType Directory -Path $LDM_WORKSPACE | Out-Null }

$TEST_VENV = Join-Path $LDM_WORKSPACE ".verify-venv"
$VENV_PYTHON = Join-Path $TEST_VENV "Scripts\python.exe"
$VENV_PYTEST = Join-Path $TEST_VENV "Scripts\pytest.exe"

Write-Host "[INFO]  Preparing isolated test environment..."
if (-not (Test-Path $TEST_VENV)) {
    & python -m venv $TEST_VENV
}

if (-not (Test-Path $VENV_PYTEST)) {
    Write-Host ">> Installing test dependencies into virtual environment..."
    & $VENV_PYTHON -m pip install pytest requests PyYAML --quiet --disable-pip-version-check
}

# Header
& {
    Write-Output "=== LDM BINARY VERIFICATION REPORT ==="
    Write-Output "Timestamp: $(Get-Date)"
    Write-Output "Platform:  $($PSVersionTable.OS)"
    
    $binaryPath = "Not Found"
    try {
        $cmdInfo = Get-Command $LDM_CMD -ErrorAction SilentlyContinue
        if ($null -ne $cmdInfo) { $binaryPath = $cmdInfo.Source }
    } catch {
        $binaryPath = "Not Found (Exception: $($_.Exception.Message))"
    }
    Write-Output "Binary:    $binaryPath"
    
    $ldmVer = "Unknown"
    try {
        $res = & $LDM_CMD --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $null -ne $res) {
            $ldmVer = ($res -join " ").Trim()
        } else {
            $errorMsg = ($res -join " ").Trim()
            $ldmVer = "Error (Exit code ${LASTEXITCODE}: $errorMsg)"
        }
    } catch {
        $ldmVer = "Unknown (Exception: $($_.Exception.Message))"
    }
    Write-Output "Version:   $ldmVer"
    
    $dockerVer = "Not Running"
    try {
        if (Get-Command docker -ErrorAction SilentlyContinue) {
            $res = & docker version --format '{{.Server.Version}}' 2>&1
            if ($LASTEXITCODE -eq 0 -and $null -ne $res) {
                $dockerVer = ($res -join " ").Trim()
            } else {
                $errorMsg = ($res -join " ").Trim()
                $dockerVer = "Not Running (Connection failed: $errorMsg)"
            }
        } else {
            $dockerVer = "Not Installed"
        }
    } catch {
        $dockerVer = "Not Running (Exception: $($_.Exception.Message))"
    }
    Write-Output "Docker:    $dockerVer"
} | Out-File -FilePath $RESULTS_FILE_TMP -Encoding utf8

function Invoke-Cleanup {
    param($cmd, $args_list)
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        if ($args_list) {
            & $cmd $args_list.Split(' ') 2>$null | Out-Null
        } else {
            & $cmd 2>$null | Out-Null
        }
    } catch {
        # ignore exceptions during cleanup
    } finally {
        $ErrorActionPreference = $oldEAP
    }
}

function Finalize-Verification {
    param($ExitCode)
    $status = "fail"
    if ($ExitCode -eq 0) { $status = "pass" }
    
    $slug = "unknown"
    try {
        $slugOut = & $LDM_CMD system doctor --slug 2>$null
        if ($null -ne $slugOut) {
            $slug = ($slugOut -join "-") -replace '[^a-zA-Z0-9-]', '-'
        }
    } catch {
        # ignore exceptions during finalization
    }
    
    $FinalName = "verify-$slug-$status.txt"
    
    if (Test-Path $RESULTS_FILE_TMP) {
        if ($status -eq "pass") {
            "`n[SUCCESS] ALL E2E VERIFICATIONS PASSED!" | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
        }
        Move-Item $RESULTS_FILE_TMP (Join-Path $ORIGINAL_PWD $FinalName) -Force
        Write-Host "`n[SUCCESS] Verification Complete ($status)`n[RESULTS] Results: $FinalName"
        if ($status -eq "pass") {
            $archiveDir = Join-Path $ORIGINAL_PWD "references\verification-results"
            if (-not (Test-Path $archiveDir)) { New-Item -ItemType Directory -Path $archiveDir | Out-Null }
            Copy-Item (Join-Path $ORIGINAL_PWD $FinalName) $archiveDir -Force
        }
    }
    Invoke-Cleanup "docker" "rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy"
    Invoke-Cleanup $LDM_CMD "-y rm ldm-smoke-test --delete"
    
    # Keep venv if in repo, otherwise clean up
    if (-not (Test-Path "pyproject.toml")) {
        if (Test-Path $LDM_WORKSPACE) { Remove-Item -Recurse -Force $LDM_WORKSPACE -ErrorAction SilentlyContinue }
    }
}

function Log-AndRun {
    param($msg, $cmd, $args_list)
    Write-Host ">> $msg"
    $res = & $cmd $args_list.Split(' ') 2>&1
    $res | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) { 
        Write-Host $res -ForegroundColor Red
        throw "Command failed: $msg" 
    }
}

try {
    # 1. Cleanup
    Invoke-Cleanup $LDM_CMD "-y rm ldm-smoke-test --delete --infra"

    # Pre-pull large images to avoid containerd lease timeouts during the timed E2E run
    Write-Host "[INFO]  Pre-pulling required Docker images..."
    & docker pull liferay/dxp:2026.q1.7-lts --quiet
    & docker pull postgres:16.2 --quiet

    Log-AndRun "Initializing Infrastructure" $LDM_CMD "-y infra setup --search"

    # 2. Guardrails
    Write-Host ">> Verifying Dev Guardrails..."
    $env:CI = "true"
    $res = & $LDM_CMD system version --bump patch 2>&1
    $env:CI = "false"
    if ($res -match "Developer utility requires LDM_DEV_MODE=true" -or $res -match "Action restricted") { 
        Write-Host "[SUCCESS] Dev Guardrails verified." 
    } else { 
        Write-Host "[ERROR] ERROR: Dev Guardrails failed! Output was: $res" -ForegroundColor Red
        exit 1
    }

    Write-Host ">> Verifying Sudo Guard (Behavioral)..."
    Write-Host "[WARNING]  Skipping behavioral Sudo Guard check (Sudo allowed in CI/Windows environment)."

    Write-Host ">> Verifying Project Collision Detection..."
    $colRes = & $LDM_CMD -y run "collision-test" --tag "2026.q1.4-lts" --port 8099 --no-wait --no-up --no-seed 2>&1
    # Check if collision-test directory exists
    if (-not (Test-Path "collision-test")) {
        Write-Host "[ERROR] ERROR: Failed to initialize collision-test project." -ForegroundColor Red
        exit 1
    }
    New-Item -ItemType Directory -Path "collision-test/nested" -Force | Out-Null
    $nestedRes = & {
        $prev = Get-Location
        Set-Location "collision-test/nested"
        $origGA = $env:GITHUB_ACTIONS
        $origCI = $env:CI
        $origGL = $env:GITLAB_CI
        $origAR = $env:LDM_ALLOW_ROOT
        $env:GITHUB_ACTIONS = $null
        $env:CI = $null
        $env:GITLAB_CI = $null
        $env:LDM_ALLOW_ROOT = "true"
        $out = "n" | & $LDM_CMD run "./collision-test" --port 8099 --no-wait --no-up --no-seed 2>&1
        $env:GITHUB_ACTIONS = $origGA
        $env:CI = $origCI
        $env:GITLAB_CI = $origGL
        $env:LDM_ALLOW_ROOT = $origAR
        Set-Location $prev
        $out
    }
    if ($nestedRes -match "Project collision" -or $nestedRes -match "already registered") {
        Write-Host "[SUCCESS] Project Collision verified."
    } else {
        Write-Host "[ERROR] ERROR: Project Collision detection failed! Output was: $nestedRes" -ForegroundColor Red
        Invoke-Cleanup $LDM_CMD "-y rm collision-test --delete"
        exit 1
    }
    Invoke-Cleanup $LDM_CMD "-y rm collision-test --delete"
    if (Test-Path "collision-test") { Remove-Item -Recurse -Force "collision-test" }

    Write-Host ">> Verifying Tag Validation Guardrail..."
    $tagRes = & $LDM_CMD -y run "tag-val-test" --tag "invalid-tag" --port 8099 --no-wait --no-up --no-seed 2>&1
    if ($tagRes -match "not listed in official Liferay releases") {
        Write-Host "[SUCCESS] Tag Validation Guardrail verified."
    } else {
        Write-Host "[ERROR] ERROR: Tag Validation Guardrail failed! Output was: $tagRes" -ForegroundColor Red
        Invoke-Cleanup $LDM_CMD "-y rm tag-val-test --delete"
        exit 1
    }
    Invoke-Cleanup $LDM_CMD "-y rm tag-val-test --delete"
    if (Test-Path "tag-val-test") { Remove-Item -Recurse -Force "tag-val-test" }

    # 3. Project Run
    Write-Host "[INFO]  Provisioning standalone test project..."
    $projectDir = Join-Path $LDM_WORKSPACE "ldm-smoke-test"
    if (-not (Test-Path $projectDir)) { New-Item -ItemType Directory -Path $projectDir -Force | Out-Null }
    New-Item -ItemType Directory -Path (Join-Path $projectDir "files") -Force | Out-Null
    Set-Location $projectDir
    "tag=2026.q1.7-lts`ncontainer_name=ldm-smoke-test`nport=$TEST_PORT`ndb_type=postgresql" | Out-File "meta" -Encoding utf8

    Log-AndRun "Running LDM Project" $LDM_CMD "-y run . --no-wait"

    # Wait for Health
    Log-AndRun "Waiting for Liferay health" $LDM_CMD "-y wait . --timeout 600"

    # Hot Deploy
    Write-Host ">> Deploying Test OSGi Bundle..."
    New-Item -ItemType Directory -Path "delayed-deploy" -Force | Out-Null
    $zipScript = "import zipfile; zf = zipfile.ZipFile('delayed-deploy/test-bundle.jar', 'w'); zf.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nBundle-ManifestVersion: 2\nBundle-Name: Test Bundle\nBundle-SymbolicName: com.liferay.test.bundle\nBundle-Version: 1.0.0\n'); zf.close()"
    & $VENV_PYTHON -c $zipScript

    # Secondary permission fix for Linux/WSL2 host side access (via Docker)
    & docker run --rm -v "$(Get-Location):/workspace" alpine chmod -R 777 /workspace/deploy /workspace/logs 2>$null

    Log-AndRun "Deploying artifact" $LDM_CMD "-y deploy . delayed-deploy/test-bundle.jar"
    Write-Host ">> Waiting for auto-deploy processing (up to 10m)..."

    $hotDeploySuccess = $false
    for ($i=0; $i -lt 60; $i++) {
        if ((docker logs ldm-smoke-test --tail 200 2>&1) -match "STARTED com.liferay.test.bundle") {
            Write-Host "[SUCCESS] Hot Deploy verified."
            $hotDeploySuccess = $true
            break
        }
        Write-Host -NoNewline "."
        Start-Sleep 10
    }
    if (-not $hotDeploySuccess) {
        Write-Host "`n[ERROR] ERROR: Hot Deploy failed. Test Bundle did not start." -ForegroundColor Red
        docker logs ldm-smoke-test --tail 100
        exit 1
    }
    Write-Host ""

    # Integrity
    Log-AndRun "Creating Snapshot" $LDM_CMD "-y snapshot --name Binary-Verify"
    $latestSnapshotDir = (Get-ChildItem snapshots | Sort LastWriteTime -Desc | Select -First 1).FullName
    $shaFile = Join-Path $latestSnapshotDir "files.tar.gz.sha256"
    "CORRUPTED" | Out-File $shaFile -Encoding utf8
    if ((& $LDM_CMD -y restore --latest 2>&1) -match "Integrity check failed") { 
        Write-Host "[SUCCESS] Integrity check verified." 
    } else { 
        throw "Integrity block failed" 
    }
    Log-AndRun "Bypassing Integrity" $LDM_CMD "-y restore --latest --no-verify"

    Write-Host ">> Verifying Legacy Command Translation..."
    $legacyDoc = & $LDM_CMD doctor --help 2>&1
    $legacySetup = & $LDM_CMD infra-setup --help 2>&1
    if ($legacyDoc -match "Usage" -and $legacySetup -match "Usage") {
        Write-Host "[SUCCESS] Legacy command translation verified."
    } else {
        throw "Legacy command translation failed."
    }

    # UX & Defaults & Scaling
    Write-Host ">> Verifying Cascading Defaults..."
    & $LDM_CMD config defaults test_key test_value > $null 2>&1
    $defaultsOut = & $LDM_CMD config defaults 2>&1
    if ($defaultsOut -match "test_key" -and $defaultsOut -match "test_value" -and $defaultsOut -match "User") {
        Write-Host "[SUCCESS] Set User Default verified."
    } else {
        throw "Set User Default failed. Output: $defaultsOut"
    }
    & $LDM_CMD config defaults --remove test_key > $null 2>&1
    $defaultsOut2 = & $LDM_CMD config defaults 2>&1
    if ($defaultsOut2 -notmatch "test_key") {
        Write-Host "[SUCCESS] Remove User Default verified."
    } else {
        throw "Remove User Default failed. Output: $defaultsOut2"
    }

    Write-Host ">> Verifying Env Sync..."
    & $LDM_CMD config env . TEST_SECRET=supersecret123 > $null 2>&1
    if ((Get-Content "docker-compose.yml" -Raw) -match "TEST_SECRET=supersecret123") { 
        Write-Host "[SUCCESS] Env Sync verified." 
    } else {
        throw "Env Sync verification failed."
    }

    Write-Host ">> Verifying Redaction..."
    $redactOut = & $LDM_CMD status REDACT_SECRET=hidden 2>&1
    if ($redactOut -match "REDACT_SECRET=\[REDACTED\]") { 
        Write-Host "[SUCCESS] Redaction verified." 
    } else {
        throw "Redaction verification failed. Output: $redactOut"
    }

    Write-Host ">> Verifying Scaling..."
    Log-AndRun "Scaling Liferay" $LDM_CMD "-y scale . liferay=3 --no-run"
    if ((Get-Content "meta" -Raw) -match "scale_liferay=3") { 
        Write-Host "[SUCCESS] Scaling verified." 
    } else {
        throw "Scaling verification failed."
    }

    Write-Host ">> Verifying logs --instance..."
    $logErr4 = & $LDM_CMD logs . --instance 4 2>&1
    $logErr2 = & $LDM_CMD logs . --instance 2 2>&1
    if ($logErr4 -match "Invalid instance index 4" -and $logErr2 -match "Container 'ldm-smoke-test-liferay-2' not found") {
        Write-Host "[SUCCESS] logs --instance routing verified."
    } else {
        throw "logs --instance routing validation failed."
    }

    Write-Host ">> Verifying Trace Log and Logs Export..."
    $traceLogPath = Join-Path $HOME ".ldm/last-command.log"
    if (Test-Path $traceLogPath) {
        Write-Host "[SUCCESS] Trace Log (last-command.log) verified."
    } else {
        throw "Trace Log file missing."
    }

    Log-AndRun "Scaling Liferay back to 1 for logs export check" $LDM_CMD "-y scale . liferay=1 --no-run"
    Log-AndRun "Starting project for logs export check" $LDM_CMD "-y run . --no-wait"
    Log-AndRun "Exporting project logs" $LDM_CMD "logs . --export"
    $exportFiles = Resolve-Path *.log -ErrorAction SilentlyContinue
    if ($exportFiles) {
        $exportFile = $exportFiles[0].Path
        Write-Host "[SUCCESS] Logs Export verified ($exportFile)."
        Remove-Item $exportFile -Force
    } else {
        throw "Logs Export file not generated."
    }

    Write-Host ">> Verifying Safe SELECT SQL Query..."
    $dbQueryOut = & $LDM_CMD db query . -s "SELECT 1 as test_val;" --allow-db-query 2>&1
    if ($dbQueryOut -match "test_val") {
        Write-Host "[SUCCESS] Safe SELECT SQL Query verified."
    } else {
        throw "Safe SELECT SQL Query failed. Output: $dbQueryOut"
    }

    Write-Host ">> Verifying Properties Override Cascade & Reset..."
    Log-AndRun "Stopping project to release file locks" $LDM_CMD "-y stop ."
    $commonDir = Join-Path $LDM_WORKSPACE "common"
    New-Item -ItemType Directory -Force -Path $commonDir | Out-Null
    "test.override.prop=456" | Out-File (Join-Path $commonDir "portal-ext.properties") -Encoding utf8
    $pePath = Join-Path $PWD.Path "files\portal-ext.properties"
    try {
        [System.IO.File]::AppendAllText($pePath, "`ntest.override.prop=123 # !important", [System.Text.Encoding]::UTF8)
    } catch {
        Write-Warning "Failed to append to portal-ext.properties via IO.File. File may be locked. Attempting retry..."
        Start-Sleep -Seconds 2
        [System.IO.File]::AppendAllText($pePath, "`ntest.override.prop=123 # !important", [System.Text.Encoding]::UTF8)
    }
    Log-AndRun "Rebuilding properties" $LDM_CMD "config rebuild-properties ."
    
    # Safely read file content, avoiding OutOfMemoryException on corrupted/massive files
    $peContent = ""
    try {
        $peContent = [System.IO.File]::ReadAllText($pePath, [System.Text.Encoding]::UTF8)
    } catch {
        throw "Failed to read portal-ext.properties: $_"
    }

    if ($peContent -match "test.override.prop=123") {
        Write-Host "[SUCCESS] Properties Override Cascade verified (rebuild)."
    } else {
        throw "Properties Override Cascade rebuild failed."
    }

    Log-AndRun "Resetting properties" $LDM_CMD "config reset-properties ."
    try {
        $resetPE = [System.IO.File]::ReadAllText($pePath, [System.Text.Encoding]::UTF8)
    } catch {
        throw "Failed to read portal-ext.properties after reset: $_"
    }
    if ($resetPE -match "test.override.prop=456" -and $resetPE -notmatch "123") {
        Write-Host "[SUCCESS] Properties Override Reset verified."
    } else {
        throw "Properties Override Reset failed."
    }

    # Clean up temporary test files
    Remove-Item $commonDir -Recurse -Force -ErrorAction SilentlyContinue

    Log-AndRun "Checking Status" $LDM_CMD "-y status"

    # Clean up any potential orphans from the run
    Invoke-Cleanup $LDM_CMD "-y system prune"

    Write-Host "`n[SUCCESS] ALL E2E VERIFICATIONS PASSED!"
    Finalize-Verification 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Finalize-Verification 1
} finally {
    Set-Location $ORIGINAL_PWD
}
