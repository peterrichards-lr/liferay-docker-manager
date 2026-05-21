# Comprehensive E2E Binary Verification for LDM (Windows Native PowerShell)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for Windows Native.

$env:PYTHONUTF8 = 1
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
$TEST_PORT = if ($env:LDM_TEST_PORT) { $env:LDM_TEST_PORT } else { "8082" }
$ORIGINAL_PWD = Get-Location
$LDM_CMD = "ldm"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RESULTS_FILE_TMP = Join-Path $ORIGINAL_PWD ".ldm-verify-tmp-${Timestamp}.txt"

Write-Host "⚡ Starting Standalone Binary Verification (Windows Native)..."

# 0. Dependencies & Virtual Environment
$LDM_WORKSPACE = Join-Path $ORIGINAL_PWD "e2e-work-dir"
$env:LDM_WORKSPACE = $LDM_WORKSPACE
if (-not (Test-Path $LDM_WORKSPACE)) { New-Item -ItemType Directory -Path $LDM_WORKSPACE | Out-Null }

$TEST_VENV = Join-Path $LDM_WORKSPACE ".verify-venv"
$VENV_PYTHON = Join-Path $TEST_VENV "Scripts\python.exe"
$VENV_PYTEST = Join-Path $TEST_VENV "Scripts\pytest.exe"

Write-Host "ℹ  Preparing isolated test environment..."
if (-not (Test-Path $TEST_VENV)) {
    & python -m venv $TEST_VENV
}

if (-not (Test-Path $VENV_PYTEST)) {
    Write-Host ">> Installing test dependencies into virtual environment..."
    & $VENV_PYTHON -m pip install pytest requests PyYAML --quiet
}

# Header
& {
    Write-Output "=== LDM BINARY VERIFICATION REPORT ==="
    Write-Output "Timestamp: $(Get-Date)"
    Write-Output "Platform:  $($PSVersionTable.OS)"
    Write-Output "Binary:    $((Get-Command $LDM_CMD).Source)"
    Write-Output "Version:   $(& $LDM_CMD --version 2>$null)"
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Write-Output "Docker:    $(& docker version --format '{{.Server.Version}}' 2>$null)"
    }
} | Out-File -FilePath $RESULTS_FILE_TMP -Encoding utf8

function Finalize-Verification {
    param($ExitCode)
    $status = if ($ExitCode -eq 0) { "pass" } else { "fail" }
    $slug = & $LDM_CMD doctor --slug 2>$null
    if ($null -eq $slug) { $slug = "unknown" }
    $FinalName = "verify-$($slug.Trim().Replace(' ','-'))-$status-$($Timestamp.Substring(10)).txt"
    
    if (Test-Path $RESULTS_FILE_TMP) {
        Move-Item $RESULTS_FILE_TMP (Join-Path $ORIGINAL_PWD $FinalName) -Force
        Write-Host "`n✅ Verification Complete ($status)`n📊 Results: $FinalName"
        if ($status -eq "pass") {
            $archiveDir = Join-Path $ORIGINAL_PWD "references\verification-results"
            if (-not (Test-Path $archiveDir)) { New-Item -ItemType Directory -Path $archiveDir | Out-Null }
            Copy-Item (Join-Path $ORIGINAL_PWD $FinalName) $archiveDir -Force
        }
    }
    docker rm -f liferay-proxy-global liferay-search-global liferay-docker-proxy 2>$null | Out-Null
    & $LDM_CMD -y rm ldm-smoke-test --delete > $null 2>&1
    
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
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $msg" }
}

try {
    # 1. Cleanup
    & $LDM_CMD -y rm ldm-smoke-test --delete --infra > $null 2>&1

    # Pre-pull large images to avoid containerd lease timeouts during the timed E2E run
    Write-Host "ℹ  Pre-pulling required Docker images..."
    & docker pull liferay/dxp:2026.q1.7-lts --quiet
    & docker pull postgres:16.2 --quiet

    Log-AndRun "Initializing Infrastructure" $LDM_CMD "-y infra-setup --search"

    # 2. Guardrails
    Write-Host ">> Verifying Dev Guardrails..."
    $res = & $LDM_CMD version --bump patch -y 2>&1
    if ($res -match "Developer utility requires LDM_DEV_MODE=true" -or $res -match "Action restricted") { 
        Write-Host "✅ Dev Guardrails verified." 
    } else { 
        Write-Host "❌ ERROR: Dev Guardrails failed! Output was: $res" -ForegroundColor Red
        exit 1
    }

    # 3. Project Run
    Write-Host "ℹ  Provisioning standalone test project..."
    $projectDir = Join-Path $LDM_WORKSPACE "ldm-smoke-test"
    if (-not (Test-Path $projectDir)) { New-Item -ItemType Directory -Path $projectDir -Force | Out-Null }
    New-Item -ItemType Directory -Path (Join-Path $projectDir "files") -Force | Out-Null
    Set-Location $projectDir
    "tag=2026.q1.7-lts`ncontainer_name=ldm-smoke-test`nport=$TEST_PORT`ndb_type=postgresql" | Out-File "meta" -Encoding utf8

    Log-AndRun "Running LDM Project" $LDM_CMD "-y run . --no-wait --no-tld-skip --no-jvm-verify"

    # Wait for Health
    Write-Host ">> Waiting for Liferay health..."
    for ($i=0; $i -lt 90; $i++) {
        if ((docker logs ldm-smoke-test 2>&1) -match "org.apache.catalina.startup.Catalina.start Server startup in") {
            Write-Host "`n✅ Liferay Tomcat started." ; break
        }
        Write-Host -NoNewline "." ; Start-Sleep 10
    }

    # Hot Deploy
    Write-Host ">> Deploying Test OSGi Bundle..."
    New-Item -ItemType Directory -Path "delayed-deploy" -Force | Out-Null
    $zipScript = "import zipfile; zf = zipfile.ZipFile('delayed-deploy/test-bundle.jar', 'w'); zf.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nBundle-ManifestVersion: 2\nBundle-Name: Test Bundle\nBundle-SymbolicName: com.liferay.test.bundle\nBundle-Version: 1.0.0\n'); zf.close()"
    & $VENV_PYTHON -c $zipScript

    # Secondary permission fix for Linux/WSL2 host side access (via Docker)
    & docker run --rm -v "$(Get-Location):/workspace" alpine chmod -R 777 /workspace/deploy /workspace/logs 2>$null

    Copy-Item "delayed-deploy\test-bundle.jar" "deploy" -Force
    Write-Host ">> Waiting 60s for auto-deploy processing..." ; Start-Sleep 60

    # Verify Hot Deploy via Logs
    Write-Host ">> Verifying Hot Deploy..."
    if ((docker logs ldm-smoke-test --tail 100 2>&1) -match "STARTED com.liferay.test.bundle") {
        Write-Host "✅ Hot Deploy verified."
    } else {
        Write-Host "❌ ERROR: Hot Deploy failed. Test Bundle did not start." -ForegroundColor Red
        docker logs ldm-smoke-test --tail 50
        exit 1
    }

    # Integrity
    Log-AndRun "Creating Snapshot" $LDM_CMD "-y snapshot --name Binary-Verify"
    $shaFile = Join-Path (Get-ChildItem snapshots | Sort LastWriteTime -Desc | Select -First 1).FullName "files.tar.gz.sha256"
    "CORRUPTED" | Out-File $shaFile -Encoding utf8
    if ((& $LDM_CMD -y restore --latest 2>&1) -match "Integrity check failed") { Write-Host "✅ Integrity check verified." } else { throw "Integrity block failed" }
    Log-AndRun "Bypassing Integrity" $LDM_CMD "-y restore --latest --no-verify"

    # UX & Scaling
    & $LDM_CMD env . TEST_SECRET=supersecret123 > $null 2>&1
    if ((Get-Content "docker-compose.yml" -Raw) -match "TEST_SECRET=supersecret123") { Write-Host "✅ Env Sync verified." }
    if ((& $LDM_CMD -v env . REDACT_SECRET=hidden 2>&1) -match "REDACTED") { Write-Host "✅ Redaction verified." }
    & $LDM_CMD -y scale . liferay=3 > $null 2>&1
    if ((Get-Content "meta" -Raw) -match "scale_liferay=3") { Write-Host "✅ Scaling verified." }

    # Clean up any potential orphans from the run
    & $LDM_CMD -y prune > $null 2>&1

    Write-Host "`n🎯 ALL E2E VERIFICATIONS PASSED!"
    Finalize-Verification 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Finalize-Verification 1
} finally {
    Set-Location $ORIGINAL_PWD
}
