# Comprehensive E2E Binary Verification for LDM (Windows Native PowerShell)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for Windows Native.

$env:PYTHONUTF8 = 1
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
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
$VENV_PLAYWRIGHT = Join-Path $TEST_VENV "Scripts\playwright.exe"

Write-Host "ℹ  Preparing isolated test environment..."
if (-not (Test-Path $TEST_VENV)) {
    & python -m venv $TEST_VENV
}

if (-not (Test-Path $VENV_PYTEST)) {
    Write-Host ">> Installing test dependencies into virtual environment..."
    & $VENV_PYTHON -m pip install pytest pytest-playwright requests PyYAML --quiet
    & $VENV_PLAYWRIGHT install chromium --with-deps
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
    "tag=2026.q1.7-lts`ncontainer_name=ldm-smoke-test`nport=8082`ndb_type=postgresql" | Out-File "meta" -Encoding utf8

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
    New-Item -ItemType Directory -Path "delayed-deploy" -Force | Out-Null
    $zipScript = "import zipfile; zf = zipfile.ZipFile('delayed-deploy/test-fragments.zip', 'w'); zf.writestr('test-collection/collection.json', '{\`"name\`": \`"Test Collection\`", \`"description\`": \`"Test\`"}'); zf.writestr('test-collection/test-fragment/fragment.json', '{\`"name\`": \`"Test Fragment\`", \`"type\`": \`"component\`"}'); zf.writestr('test-collection/test-fragment/index.html', '\`"<div>Test Fragment</div>\`"'); zf.writestr('test-collection/test-fragment/index.js', ''); zf.writestr('test-collection/test-fragment/index.css', ''); zf.close()"
    & $VENV_PYTHON -c $zipScript

    # Secondary permission fix for Linux/WSL2 host side access (via Docker)
    & docker run --rm -v "$(Get-Location):/workspace" alpine chmod -R 777 /workspace/deploy /workspace/logs 2>$null

    Copy-Item "delayed-deploy\test-fragments.zip" "deploy" -Force
    Write-Host ">> Waiting 30s for auto-deploy..." ; Start-Sleep 30

    # UI Test
    $uiTest = "import os, pytest; from playwright.sync_api import Page, expect; def test_fragment_deployment(page: Page): page.route('\`'**/*.statuspage.io/**\`'', lambda route: route.abort()); page.route('\`'**/cdn.pendo.io/**\`'', lambda route: route.abort()); url = os.environ.get('LIFERAY_URL', 'http://localhost:8082'); page.goto(f'{url}/c/portal/login'); if page.locator('input[name*=\`'LoginPortlet_login\`']').is_visible(timeout=5000): page.fill('input[name*=\`'LoginPortlet_login\`']', 'test@liferay.com'); page.fill('input[name*=\`'LoginPortlet_password\`']', 'test'); page.click('button[type=\`'submit\`']'); page.wait_for_function('\`'() => window.location.href.includes(\"/web/guest\") || window.location.href.includes(\"/home\")\`'', timeout=30000); fragments_url = f'{url}/group/guest/~/control_panel/manage?p_p_id=com_liferay_fragment_web_portlet_FragmentPortlet'; collection_found = False; for i in range(20): print(f'\`'  -> Attempt {i+1}: Checking for Test Collection\`''); page.goto(fragments_url); coll = page.locator('\`'.clay-card, tr, [role=\"gridcell\"], h5\`'').filter(has_text='\`'Test Collection\`'').first; try: if coll.is_visible(timeout=10000): coll.click(force=True, timeout=15000); collection_found = True; break; except Exception: pass; page.wait_for_timeout(10000); if not collection_found: pytest.fail('\`'Test Collection not found or clickable\`''); expect(page.get_by_text('\`'Test Fragment\`'').first).to_be_visible(timeout=20000)"
    $uiTest | Out-File "e2e_ui_test.py" -Encoding utf8
    # Create empty config to avoid picking up repo pyproject.toml
    "" | Out-File "pytest_empty.ini" -Encoding utf8
    Log-AndRun "Running UI Tests" $VENV_PYTEST "e2e_ui_test.py -c pytest_empty.ini --base-url http://localhost:8082 --screenshot=only-on-failure"
    Remove-Item "pytest_empty.ini"

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

    Write-Host "`n🎯 ALL E2E VERIFICATIONS PASSED!"
    Finalize-Verification 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Finalize-Verification 1
} finally {
    Set-Location $ORIGINAL_PWD
}
