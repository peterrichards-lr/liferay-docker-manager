# Comprehensive E2E Binary Verification for LDM (Windows Native PowerShell)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for Windows Native.

# LDM-#1011: version this script itself (kept in sync with ldm_core/constants.py
# by scripts/release.py on every bump) so a locally-held copy can be checked
# against what actually shipped, rather than guessing from a file mtime -- git
# checkout/pull doesn't preserve original commit timestamps.
# LDM_MAGIC_VERSION: 2.16.0
$SCRIPT_VERSION = "2.16.0"

# LDM-#1058: extracted into a named function (still in this same file -- the
# real verification workflow copies just this one file onto test rigs with
# no git checkout and no accompanying lib/ directory, see #1049, so
# dot-sourcing a separate file would break that) so it can be tested in
# isolation (see ldm_core/tests/test_verify_scripts.py) without needing a
# full E2E Docker/ldm run. This logic has had 3 real bugs this cycle already
# (#1047, #1049, #1058).
function Get-VersionBannerLines {
    param(
        [string]$ScriptVersion,
        [string]$LdmVer
    )
    $lines = @(
        "Version:   $LdmVer"
        "Script Ver: $ScriptVersion"
    )
    if ($LdmVer -match '(\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?)') {
        $installedVersion = $Matches[1]
        if ($installedVersion -ne $ScriptVersion) {
            # LDM-#1049: the real verification workflow copies this script
            # onto plain test rigs with no git checkout at all (upgrade the
            # target machine via `ldm system upgrade --beta`, copy the script
            # over, run it) -- `git checkout` is useless advice there. A raw-
            # file download keyed to the installed binary's own tag needs no
            # git and resolves correctly whether that binary is stable or
            # pre-release.
            $lines += @(
                "WARNING: this script (v$ScriptVersion) does not match the installed ldm binary (v$installedVersion)."
                "  This may be intentional (verifying a specific older/newer binary), but if not,"
                "  re-pull this script: Invoke-WebRequest -Uri `"https://raw.githubusercontent.com/peterrichards-lr/liferay-docker-manager/v$installedVersion/scripts/verify_e2e_refactor.ps1`" -OutFile `"scripts\verify_e2e_refactor.ps1`""
            )
        }
    }
    return $lines
}

$env:PYTHONUTF8 = 1
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false
$TEST_PORT = "8082"
if ($env:LDM_TEST_PORT) { $TEST_PORT = $env:LDM_TEST_PORT }
$TARGET_TEST_NODE = "e2e-target-${TEST_PORT}"
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
    # LDM-#1011 follow-up: Write-Host bypasses the pipeline entirely (unlike
    # Write-Output, which is captured below by Out-File), so this prints the
    # version lines to the console as the run starts, in addition to them
    # still landing in the report via Write-Output.
    #
    # LDM-#1058: Get-VersionBannerLines is a named function (still in this
    # same file -- the real verification workflow copies just this one file
    # onto test rigs with no git checkout and no accompanying lib/ directory,
    # see #1049, so splitting this into a separate dot-sourced file would
    # break that) so it can be tested in isolation (see
    # ldm_core/tests/test_verify_scripts.py) without needing a full E2E
    # Docker/ldm run. This logic has had 3 real bugs this cycle already
    # (#1047, #1049, #1058).
    (Get-VersionBannerLines -ScriptVersion $SCRIPT_VERSION -LdmVer $ldmVer) | ForEach-Object {
        Write-Output $_
        Write-Host $_
    }


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
    
    $FinalName = "verify-$slug-$Timestamp-$status.txt"
    
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

function ConvertFrom-LdmJson {
    <#
    .SYNOPSIS
    Parses JSON emitted by a native command, safely on Windows PowerShell 5.1.

    .DESCRIPTION
    LDM-#1300. `& native.exe` returns a string ARRAY -- one element per line.
    PowerShell 7 accumulates piped input before parsing, but Windows PowerShell
    5.1 does not treat multi-line pipeline input the same way, which is why the
    documented idiom is `Get-Content -Raw` or an explicit join. Piping the array
    straight into ConvertFrom-Json produced an object on 5.1 that carried some
    properties and silently lacked others, reporting a schema break in `ldm`
    that did not exist.

    Joining first, and passing -InputObject rather than using the pipeline,
    removes both the per-item semantics and the array entirely. On failure it
    dumps what it actually received, so a recurrence is diagnosable from the
    run log instead of needing another round trip to the machine.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowNull()]$Raw,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $joined = (@($Raw) -join "`n").Trim()
    if ([string]::IsNullOrWhiteSpace($joined)) {
        throw "$Label produced no output to parse"
    }

    try {
        return ConvertFrom-Json -InputObject $joined
    } catch {
        Write-Host "[ERROR] Could not parse $Label output as JSON." -ForegroundColor Red
        Write-Host "        PowerShell: $($PSVersionTable.PSVersion)"
        Write-Host "        Raw length: $($joined.Length) chars"
        Write-Host "--- begin raw ---"
        Write-Host $joined
        Write-Host "--- end raw ---"
        throw
    }
}

function ConvertTo-LdmArray {
    <#
    .SYNOPSIS
    Returns a parsed JSON value as a flat array of entries.

    .DESCRIPTION
    LDM-#1300. Windows PowerShell 5.1 emits a deserialized JSON array as a
    SINGLE object rather than enumerating it, so the usual `@(...)` idiom yields
    a one-element array *containing* the array. Iterating that gives the .NET
    array itself, whose properties are Count/Length/Rank/SyncRoot -- which is
    exactly what the failing Windows run reported when it looked for
    'http_ready'. PowerShell 7 enumerates, so the bug is invisible there.

    Assigning an existing array through unchanged, and wrapping only a scalar,
    is correct on both: it never double-wraps and never flattens a single entry
    into nothing.
    #>
    param([Parameter(Mandatory = $true)][AllowNull()]$Value)

    if ($null -eq $Value) { return @() }

    if ($Value -is [System.Array]) {
        # Defensive unwrap: if 5.1's PSObject wrapping produced a single-element
        # array whose only element is itself an array, that inner array is the
        # entry list. A legitimate single entry is a PSCustomObject, never an
        # array, so this cannot swallow real data.
        if ($Value.Count -eq 1 -and $Value[0] -is [System.Array]) {
            return $Value[0]
        }
        return $Value
    }

    return @($Value)
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

    Write-Host ">> Verifying Custom SSL Port & Recreate..."
    Log-AndRun "Custom SSL Port Setup" $LDM_CMD "-y infra setup --ssl-port 8443 --force-recreate"
    $dockerInspect = & docker inspect liferay-proxy-global
    if ($dockerInspect -match '"HostPort": "8443"') {
        Write-Host "[SUCCESS] Custom SSL Port & Recreate verified."
    } else {
        Write-Host "[ERROR] ERROR: Traefik proxy was not recreated on custom port 8443!" -ForegroundColor Red
        exit 1
    }


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

    Write-Host ">> Verifying System Tray (GUI)..."
    $trayProcess = Start-Process -FilePath $LDM_CMD -ArgumentList "tray" -NoNewWindow -PassThru -RedirectStandardOutput "tray.log" -RedirectStandardError "tray_err.log"
    Start-Sleep -Seconds 5
    if (-not $trayProcess.HasExited) {
        Write-Host "[SUCCESS] System Tray application started successfully and remained alive."
        Stop-Process -Id $trayProcess.Id -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "[ERROR] ERROR: System Tray application crashed or failed to start!" -ForegroundColor Red
        Write-Host "--- STDOUT ---"
        Get-Content "tray.log" -ErrorAction SilentlyContinue
        Write-Host "--- STDERR ---"
        Get-Content "tray_err.log" -ErrorAction SilentlyContinue
        exit 1
    }

    Write-Host ">> Verifying ldm doctor Dependency Integrity..."
    $doctorOut = & $LDM_CMD doctor --detailed --skip-project 2>&1
    $doctorStr = ($doctorOut -join "`n")
    if ($doctorStr -match "Dependency Integrity") {
        if ($doctorStr -match "Dependency Integrity.*(Failed|Missing|FAILED)") {
            Write-Host "[ERROR] ERROR: ldm doctor Dependency Integrity check failed!" -ForegroundColor Red
            $doctorOut | Where-Object { $_ -match "Dependency Integrity" } | Write-Host
            Add-Content -Path $RESULTS_FILE_TMP -Value "ERROR: ldm doctor Dependency Integrity failed"
            exit 1
        } else {
            Write-Host "[SUCCESS] ldm doctor Dependency Integrity verified."
            Add-Content -Path $RESULTS_FILE_TMP -Value "ldm doctor Dependency Integrity: PASSED"
        }
    } else {
        Write-Host "[WARNING] Skipping Dependency Integrity check (binary install - no requirements.txt found)."
    }

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

    Write-Host ">> Verifying Compute Target Management & Connectivity Probe..."
    Log-AndRun "Target List" $LDM_CMD "target ls"
    Log-AndRun "Target Status (Local)" $LDM_CMD "target status local"

    Write-Host ">> Testing Target CRUD Cycle..."
    Log-AndRun "Target Add (Mock Node)" $LDM_CMD "target add $TARGET_TEST_NODE --host 127.0.0.1"
    $targetLsRes = & $LDM_CMD target ls 2>&1
    if ($targetLsRes -match $TARGET_TEST_NODE) {
        Write-Host "[SUCCESS] Target registration verified."
    } else {
        Write-Host "[ERROR] ERROR: Target $TARGET_TEST_NODE not found in registry." -ForegroundColor Red
        exit 1
    }
    Log-AndRun "Target Remove (Mock Node)" $LDM_CMD "target rm $TARGET_TEST_NODE"

    Write-Host ">> Testing Loopback Subnet Target Registration & Local Context Resolution..."
    $loopbackNode = "loopback-node-${TEST_PORT}"
    Log-AndRun "Target Add (127.0.0.2 Loopback)" $LDM_CMD "target add $loopbackNode --host 127.0.0.2"
    $loopbackLsRes = & $LDM_CMD target ls 2>&1
    if ($loopbackLsRes -match $loopbackNode) {
        Write-Host "[SUCCESS] Loopback target registration verified."
    } else {
        Write-Host "[ERROR] ERROR: Target $loopbackNode not found in registry." -ForegroundColor Red
        exit 1
    }
    Log-AndRun "Target Status (Loopback Node)" $LDM_CMD "target status $loopbackNode"
    Log-AndRun "Target Remove (Loopback Node)" $LDM_CMD "target rm $loopbackNode"

    $remoteHost = $env:LDM_TEST_REMOTE_HOST
    if (-not $remoteHost) { $remoteHost = $env:LDM_REMOTE_TARGET }
    if ($remoteHost) {
        Write-Host ">> Probing Remote Compute Target ($remoteHost)..."
        $remoteNodeName = "remote-${TARGET_TEST_NODE}"
        Log-AndRun "Target Add (Remote Host)" $LDM_CMD "target add $remoteNodeName --host $remoteHost"
        $remoteStatusOut = & $LDM_CMD target status $remoteNodeName 2>&1
        Write-Host ($remoteStatusOut -join "`n")
        if ($remoteStatusOut -match "ONLINE") {
            Write-Host "[SUCCESS] Remote Target Probe verified (ONLINE)."
        } else {
            Write-Host "[WARNING] Remote Target Probe returned OFFLINE or unreachable for $remoteHost."
        }
        & $LDM_CMD target rm $remoteNodeName > $null 2>&1
    }

    Write-Host ">> Verifying Nightly and Master Build Flags..."
    $nightlyProj = "nightly-test-$TEST_PORT"
    & $LDM_CMD -y run $nightlyProj --nightly --port 8098 --no-wait --no-up > $null 2>&1
    $metaContent = Get-Content (Join-Path $nightlyProj "meta") -Raw 2>$null
    if ($metaContent -match "nightly") {
        Write-Host "[SUCCESS] --nightly flag resolution verified."
    } else {
        Write-Host "[ERROR] --nightly flag resolution failed." -ForegroundColor Red
        exit 1
    }
    & $LDM_CMD -y rm $nightlyProj --delete > $null 2>&1
    Remove-Item -Recurse -Force $nightlyProj -ErrorAction SilentlyContinue

    $masterProj = "master-test-$TEST_PORT"
    & $LDM_CMD -y run $masterProj --master --port 8097 --no-wait --no-up > $null 2>&1
    $masterMetaContent = Get-Content (Join-Path $masterProj "meta") -Raw 2>$null
    if ($masterMetaContent -match "nightly") {
        Write-Host "[SUCCESS] --master flag alias verified."
    } else {
        Write-Host "[ERROR] --master flag alias failed." -ForegroundColor Red
        exit 1
    }
    & $LDM_CMD -y rm $masterProj --delete > $null 2>&1
    Remove-Item -Recurse -Force $masterProj -ErrorAction SilentlyContinue

    # 3. Project Run
    # LDM-#1302: a leftover project from a previous run sends 'ldm run' down the
    # 'already exists -> reconfigure' path instead of a fresh provision. That
    # path reaches verify_runtime_environment()'s 'docker run ... alpine' mount
    # probe, which has no timeout, so a stalled pull or wedged mount hangs
    # indefinitely with nothing printed. A fresh CI runner never hits this, so
    # the reconfigure path is effectively untested. The suite already deletes
    # this project on exit, so removing it on entry is consistent.
    $existing = & $LDM_CMD list 2>$null | Select-String -SimpleMatch "ldm-smoke-test"
    if ($existing) {
        Write-Host "[WARNING] Test project 'ldm-smoke-test' already exists (leftover from a failed run)."
        Write-Host "          Removing it so this run provisions cleanly rather than reconfiguring."
        Invoke-Cleanup $LDM_CMD "-y rm ldm-smoke-test --delete"
        $stillThere = & $LDM_CMD list 2>$null | Select-String -SimpleMatch "ldm-smoke-test"
        if ($stillThere) {
            throw ("Could not remove pre-existing project 'ldm-smoke-test'. " +
                   "Refusing to continue: reconfiguring a stale project is the path that hangs. " +
                   "Remove it manually with: ldm -y rm ldm-smoke-test --delete")
        }
        Write-Host "[SUCCESS] Pre-existing test project removed."
    }

    Write-Host "[INFO]  Provisioning standalone test project..."
    $projectDir = Join-Path $LDM_WORKSPACE "ldm-smoke-test"
    if (-not (Test-Path $projectDir)) { New-Item -ItemType Directory -Path $projectDir -Force | Out-Null }
    New-Item -ItemType Directory -Path (Join-Path $projectDir "files") -Force | Out-Null
    Set-Location $projectDir
    '{"tag": "2026.q1.7-lts", "container_name": "ldm-smoke-test", "port": ' + $TEST_PORT + ', "db_type": "postgresql"}' | Out-File "meta" -Encoding utf8

    Log-AndRun "Running LDM Project" $LDM_CMD "-y run . --no-wait"

    # Wait for Health
    Log-AndRun "Waiting for Liferay health" $LDM_CMD "-y wait . --timeout 600"

    # Hot Deploy
    Write-Host ">> Deploying Test OSGi Bundle..."
    New-Item -ItemType Directory -Path "delayed-deploy" -Force | Out-Null
    $zipScript = @"
import zipfile
zf = zipfile.ZipFile('delayed-deploy/test-bundle.jar', 'w')
zf.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nBundle-ManifestVersion: 2\nBundle-Name: Test Bundle\nBundle-SymbolicName: com.liferay.test.bundle\nBundle-Version: 1.0.0\n')
zf.close()
"@
    Set-Content -Path "delayed-deploy/build_bundle.py" -Value $zipScript
    & $VENV_PYTHON "delayed-deploy/build_bundle.py"

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
    if ((Get-Content "meta" -Raw) -match "scale_liferay.*3") { 
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
    Write-Host ">> Verifying ldm start UX fast-fail..."
    $startFailOut = & $LDM_CMD start fake-non-existent-project 2>&1
    if ($startFailOut -match "Project not found or not initialized") {
        Write-Host "[SUCCESS] ldm start fast-fail verified."
    } else {
        throw "ldm start fast-fail message not found. Output: $startFailOut"
    }

    Write-Host ">> Verifying ldm run reconfigure UX message..."
    $runReconfigOut = & $LDM_CMD -y run . --no-wait --info 2>&1
    if ($runReconfigOut -match "already exists and this command will reconfigure it") {
        Write-Host "[SUCCESS] ldm run reconfigure UX message verified."
    } else {
        throw "ldm run reconfigure message not found. Output: $runReconfigOut"
    }

    Write-Host ">> Verifying Safe SELECT SQL Query..."
    $dbQueryOut = & $LDM_CMD db query . -s "SELECT 1 as test_val;" --allow-db-query 2>&1
    if ($dbQueryOut -match "test_val") {
        Write-Host "[SUCCESS] Safe SELECT SQL Query verified."
    } else {
        throw "Safe SELECT SQL Query failed. Output: $dbQueryOut"
    }

    Write-Host ">> Verifying Properties Override Cascade and Reset..."
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

    Write-Host ">> Verifying --json Output Schemas (#1091 / #1115)..."
    # stderr is deliberately discarded rather than merged: '2>&1' folds any
    # warning into the payload and makes ConvertFrom-Json fail, reporting a
    # schema break that never happened.
    $listJsonRaw = & $LDM_CMD list --json 2>$null
    try {
        $listData = ConvertTo-LdmArray -Value (ConvertFrom-LdmJson -Raw $listJsonRaw -Label "list --json")
        # Must not be vacuous: a project exists by this point in the run, so an
        # empty array means the contract is broken, not that there is nothing
        # to check.
        # LDM-#1309: @() is required. Windows PowerShell 5.1 gives a scalar no
        # .Count property (7 added it), so on a single-project machine -- the
        # normal CI case -- this vacuity guard evaluated .Count to empty,
        # compared empty -eq 0 as false, and passed without ever guarding.
        if (-not $listData -or @($listData).Count -eq 0) {
            throw "list --json returned an empty array; expected the test project"
        }
        foreach ($item in $listData) {
            foreach ($key in @("http_ready", "http_status", "db_unhealthy")) {
                if ($item.PSObject.Properties.Name -notcontains $key) {
                    $seen = ($item.PSObject.Properties.Name -join ", ")
                    throw "$key missing from list --json entry '$($item.project)' (properties present: $seen)"
                }
            }
        }
        Write-Host "[SUCCESS] ldm list --json schema verified."
    } catch {
        throw "ldm list --json schema verification failed: $_"
    }

    # 'status --json' is shaped differently from 'list --json' (see
    # ldm_core/diagnostics/info.py: run_status vs run_list). It returns an
    # object with 'infrastructure' and 'projects', and the health keys live on
    # each entry in 'projects' -- not at the top level. It also does NOT emit
    # 'db_unhealthy', which is a list-only field.
    $statusJsonRaw = & $LDM_CMD status . --json 2>$null
    try {
        $statusData = ConvertFrom-LdmJson -Raw $statusJsonRaw -Label "status --json"
        if ($statusData.PSObject.Properties.Name -notcontains "projects") {
            throw "projects missing from status --json"
        }
        $statusProjects = ConvertTo-LdmArray -Value $statusData.projects
        if (-not $statusProjects -or @($statusProjects).Count -eq 0) {
            throw "status --json returned no projects; expected the test project"
        }
        foreach ($item in $statusProjects) {
            foreach ($key in @("http_ready", "http_status")) {
                if ($item.PSObject.Properties.Name -notcontains $key) {
                    $seen = ($item.PSObject.Properties.Name -join ", ")
                    throw "$key missing from status --json project '$($item.project)' (properties present: $seen)"
                }
            }
        }
        Write-Host "[SUCCESS] ldm status --json schema verified."
    } catch {
        throw "ldm status --json schema verification failed: $_"
    }

    Write-Host ">> Verifying Idempotent Exit Code 5 (#1094)..."
    # Exit 5 is only returned in non-interactive mode
    # (ldm_core/pipelines/run.py:246); interactively LDM prompts instead, so
    # '-y' is required.
    #
    # The project is STOPPED at this point -- "Stopping project to release file
    # locks" above shuts it down and nothing restarts it. So the first 'up'
    # legitimately starts it and returns 0; the idempotent contract only applies
    # to a second invocation, when the project is genuinely already running.
    # Asserting 5 on the first call fails on every platform, and tolerating
    # "5 or 0" instead would verify nothing, since 0 is the only value the first
    # call can return.
    Log-AndRun "Starting project for idempotency check" $LDM_CMD "-y up ."
    & $LDM_CMD -y up . *> $null
    $upExitCode = $LASTEXITCODE
    if ($upExitCode -eq 5) {
        Write-Host "[SUCCESS] Idempotent Exit Code 5 verified."
    } else {
        throw "Expected exit code 5 (Idempotent No-Op) from 'ldm -y up' on an already-running project, got $upExitCode."
    }


    Write-Host ">> Verifying Client Extension deploy & staging (#1257 / #1262)..."
    # LDM-#1262: this check previously passed a *directory*
    # ('deploy . synthetic-cx/'). cmd_deploy only recognises trailing arguments
    # that are existing *files* with a known extension (.jar/.war ->
    # osgi/modules, .zip -> the CX sync); anything else falls through to the
    # service-name branch and becomes 'docker compose up -d synthetic-cx/',
    # which fails because no such service exists. A client extension is
    # deployed as a ZIP, not a directory.
    #
    # Verified against the real command before landing (LDM-#1262): the ZIP form
    # performs the documented 3-step sync in
    # ldm_core/workspace/hydration.py:_sync_cx_artifact --
    #   1. copy   ZIP -> client-extensions/<name>.zip
    #   2. expand     -> client-extensions/<stem>/
    #   3. MOVE   ZIP -> osgi/client-extensions/<name>.zip
    # Step 3 is a move, so the intermediate copy must be gone afterwards.
    # Asserting that is what distinguishes a completed sync from one that only
    # got through step 1 -- the removed block asserted neither, while claiming
    # "Staging" in its heading.
    $cxName = "synthetic-cx"
    Remove-Item -Recurse -Force "cx-build", "$cxName.zip" -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path "cx-build/$cxName" | Out-Null
    @(
        "${cxName}:",
        "    name: Synthetic CX",
        "    type: customElement",
        "    url: $cxName.js"
    ) | Set-Content -Path "cx-build/$cxName/client-extension.yaml" -Encoding ascii
    'console.log("synthetic");' | Set-Content -Path "cx-build/$cxName/$cxName.js" -Encoding ascii
    Compress-Archive -Path "cx-build/$cxName/*" -DestinationPath "$cxName.zip" -Force

    Log-AndRun "Deploying Synthetic CX" $LDM_CMD "-y deploy . $cxName.zip"

    $cxStaged = $true
    if (-not (Test-Path "osgi/client-extensions/$cxName.zip")) {
        Write-Host "[ERROR] CX was not staged to osgi/client-extensions/$cxName.zip." -ForegroundColor Red
        $cxStaged = $false
    }
    if (-not (Test-Path "client-extensions/$cxName/client-extension.yaml")) {
        Write-Host "[ERROR] CX was not expanded to client-extensions/$cxName/." -ForegroundColor Red
        $cxStaged = $false
    }
    if (Test-Path "client-extensions/$cxName.zip") {
        Write-Host "[ERROR] intermediate client-extensions/$cxName.zip still present -- step 3 (move to osgi/client-extensions) did not complete." -ForegroundColor Red
        $cxStaged = $false
    }

    if ($cxStaged) {
        Write-Host "[SUCCESS] Client Extension deploy & staging verified."
    } else {
        Get-ChildItem "client-extensions" -ErrorAction SilentlyContinue | Out-String | Write-Host
        Get-ChildItem "osgi/client-extensions" -ErrorAction SilentlyContinue | Out-String | Write-Host
        throw "Client Extension deploy & staging verification failed."
    }

    Remove-Item -Recurse -Force "cx-build" -ErrorAction SilentlyContinue

    Write-Host ">> Verifying Portal Patch Overlay (#1264)..."
    # The patch JAR is SYNTHETIC and deliberately inert: a valid OSGi bundle
    # with a unique Bundle-SymbolicName, no Import-Package, no Export-Package
    # and no activator, so OSGi resolves it and it then does nothing. Its name
    # matches no real core JAR, so it REPLACES nothing and cannot alter
    # Liferay's behaviour -- it is purely additive and carries a marker file
    # that makes its presence unambiguous.
    #
    # That also buys a free assertion: copy_patches_into() probes each patch for
    # upstream existence and refuses a JAR that is not already in the image,
    # because a patch whose target was removed upstream is a sharper problem
    # than a merely stale one. So the synthetic JAR must be REFUSED without
    # --force-portal-patches and applied with it. Both directions are checked.
    #
    # The sidecar manifest is written explicitly rather than letting LDM create
    # it: load_or_create_sidecar() stamps 'introduced_in' with the CURRENT tag
    # on first sight, which would mask a regression in classify_version_change().
    #
    # NOTE: the mode-600 case from #1264 -- where docker cp preserved a host
    # file's POSIX mode and Liferay (uid 1000) could not read the result -- is
    # asserted in the .sh script only. Windows has no POSIX file modes, so
    # _world_readable()'s trigger condition cannot be reproduced from here. The
    # readability check inside the container is still made, since that runs in
    # Linux regardless of the host.
    $patchJarName = "ldm-verify-noop-patch.jar"
    $patchDir = "portal-patches"
    $containerPortal = "/opt/liferay/osgi/portal"
    $patchTarget = "$containerPortal/$patchJarName"

    $patchTag = & $VENV_PYTHON -c "import json;print(json.load(open('meta',encoding='utf-8')).get('tag',''))"
    Remove-Item -Recurse -Force $patchDir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $patchDir | Out-Null

    & $VENV_PYTHON -c @"
import sys, zipfile
path = sys.argv[1]
manifest = (
    'Manifest-Version: 1.0\r\n'
    'Bundle-ManifestVersion: 2\r\n'
    'Bundle-SymbolicName: com.liferay.ldm.verify.noop\r\n'
    'Bundle-Name: LDM Verification No-Op Patch\r\n'
    'Bundle-Version: 1.0.0\r\n'
    '\r\n'
)
with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('META-INF/MANIFEST.MF', manifest)
    z.writestr('ldm-verify-marker.txt', 'LDM_PORTAL_PATCH_MARKER\n')
"@ "$patchDir/$patchJarName"

    & $VENV_PYTHON -c @"
import json, sys
json.dump({'jira': 'LDM-1264', 'introduced_in': sys.argv[1],
           'max_version': None, 'fail_on_mismatch': False},
          open(sys.argv[2], 'w', encoding='utf-8'), indent=2)
"@ "$patchTag" "$patchDir/$patchJarName.json"

    $patchHostSha = (Get-FileHash -Algorithm SHA256 -Path "$patchDir/$patchJarName").Hash.ToLower()
    $patchOk = $true

    # 1. Absent upstream => must be refused without --force-portal-patches.
    & $LDM_CMD -y restart . --force-recreate *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[ERROR] a patch absent from $containerPortal was applied without --force-portal-patches." -ForegroundColor Red
        $patchOk = $false
    }

    # 2. With the flag it applies.
    Log-AndRun "Applying portal patch" $LDM_CMD "-y restart . --force-recreate --force-portal-patches"

    & docker exec $PROJECT_NAME test -f $patchTarget *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] patch JAR not present at $patchTarget inside the container." -ForegroundColor Red
        & docker exec $PROJECT_NAME ls -la $containerPortal 2>&1 | Select-Object -First 5 | Write-Host
        $patchOk = $false
    } else {
        # Content must match exactly -- a truncated or empty copy would still
        # satisfy a bare existence check.
        $patchInSha = (& docker exec $PROJECT_NAME sha256sum $patchTarget 2>$null) -split '\s+' | Select-Object -First 1
        if ($patchInSha -ne $patchHostSha) {
            Write-Host "[ERROR] patch JAR content differs inside the container." -ForegroundColor Red
            Write-Host "   host:      $patchHostSha"
            Write-Host "   container: $patchInSha"
            $patchOk = $false
        }

        # The #1264 silent failure: readable by Liferay's uid, not merely present.
        & docker exec -u 1000 $PROJECT_NAME test -r $patchTarget *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] patch JAR is not readable by uid 1000 -- OSGi would fail to resolve it while the container still booted healthy (#1264)." -ForegroundColor Red
            & docker exec $PROJECT_NAME ls -l $patchTarget 2>&1 | Select-Object -First 2 | Write-Host
            $patchOk = $false
        }
    }

    # 3. --force-recreate replaces the container; the patch must survive it.
    if ($patchOk) {
        Log-AndRun "Re-creating with patches" $LDM_CMD "-y restart . --force-recreate --force-portal-patches"
        & docker exec $PROJECT_NAME test -f $patchTarget *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] patch JAR was dropped by 'restart --force-recreate' (#1264)." -ForegroundColor Red
            $patchOk = $false
        }
    }

    Remove-Item -Recurse -Force $patchDir -ErrorAction SilentlyContinue

    if ($patchOk) {
        Write-Host "[SUCCESS] Portal patch overlay verified (refused without --force, applied and readable with it, survives --force-recreate)."
    } else {
        throw "Portal patch overlay verification failed."
    }

    Write-Host ">> Verifying non-ASCII project naming (#1307 / #1308 / #1321)..."
    # Design intent: the project metadata records the name the user chose,
    # VERBATIM, while Docker receives a transcoded ASCII name. Both halves are
    # asserted. Checking only the Docker name would pass even if the real name
    # were being destroyed on the way in; checking only the metadata would pass
    # even if Compose were handed a name it cannot use.
    #
    # This file must remain PURE ASCII -- enforced by the 'Check PowerShell
    # ASCII Encoding' pre-commit hook -- so every test name is built from
    # explicit codepoints rather than written literally. That is also
    # self-documenting: the codepoint is the thing under test.
    #
    #   Zolc          U+017B U+00F3 U+0142 U+0107. Every character is
    #                 non-ASCII: the #1307 case, where the sanitized name came
    #                 out EMPTY and Compose refused to start. U+0142 (stroked
    #                 l) is #1308 -- NFKD cannot decompose it, so before the
    #                 explicit mapping it vanished and "Zolc" became "Zoc".
    #   Kaesespaetzle German umlauts EXPAND to "ae" rather than being stripped.
    #   Duoc          Vietnamese stacked diacritics.
    #
    # Windows matters disproportionately here: this is where the console is not
    # reliably UTF-8 (see #1309), so a name that survives on Linux can still be
    # mangled on the way through PowerShell.
    $namingCases = @(
        @{ Raw = [string]::Join('', [char]0x017B, [char]0x00F3, [char]0x0142, [char]0x0107)
           Docker = "Zolc" },
        @{ Raw = [string]::Join('', [char]0x004B, [char]0x00E4, [char]0x0073, [char]0x0065,
                                    [char]0x0073, [char]0x0070, [char]0x00E4, [char]0x0074,
                                    [char]0x007A, [char]0x006C, [char]0x0065)
           Docker = "Kaesespaetzle" },
        @{ Raw = [string]::Join('', [char]0x0110, [char]0x01B0, [char]0x1EE3, [char]0x0063)
           Docker = "Duoc" }
    )

    $namingWorkdir = Join-Path $LDM_WORKSPACE "naming-$TEST_PORT"
    Remove-Item -Recurse -Force $namingWorkdir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $namingWorkdir | Out-Null

    $namingOk = $true
    $namingPrevious = Get-Location

    foreach ($case in $namingCases) {
        $raw = $case.Raw
        $expected = $case.Docker
        $projDir = Join-Path $namingWorkdir $raw

        # Pre-clean per the #1302 pattern: a leftover project makes LDM
        # reconfigure rather than provision, which is the path that hangs
        # rather than failing.
        # Invoke-Cleanup splits on spaces, so the name must NOT be quoted here
        # -- quotes would be passed through as literal characters. Safe
        # because every test name above is a single word.
        Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"

        # --no-up so nothing boots, --no-seed so no ~1GB archive is fetched;
        # the name is resolved long before either matters.
        Set-Location $namingWorkdir
        & $LDM_CMD -y init "$raw" --no-up --no-seed *> $null
        $initExit = $LASTEXITCODE
        Set-Location $namingPrevious

        if ($initExit -ne 0) {
            Write-Host "[ERROR] 'ldm init $raw' failed with exit $initExit." -ForegroundColor Red
            $namingOk = $false
            continue
        }

        $metaPath = Join-Path $projDir "meta"
        if (-not (Test-Path $metaPath)) {
            Write-Host "[ERROR] no meta written for '$raw'; expected $metaPath." -ForegroundColor Red
            $namingOk = $false
            continue
        }

        # The metadata half. 'meta' is JSON, so the name is stored escaped
        # ("\u017b..." escapes) -- it must be PARSED, not string-matched, or the
        # assertion silently compares against the escape sequence rather than
        # the character it denotes.
        $meta = Get-Content -Raw -Path $metaPath -Encoding UTF8 | ConvertFrom-Json
        $metaOk = $true
        foreach ($key in @("project_name", "container_name", "liferay_container_name")) {
            if ($meta.$key -cne $raw) {
                Write-Host "[ERROR] meta['$key'] is '$($meta.$key)', expected the verbatim name." -ForegroundColor Red
                $metaOk = $false
            }
        }
        if (-not $metaOk) { $namingOk = $false; continue }

        # The Docker half. #1307 added the explicit top-level 'name:'; without
        # it Compose derives the project name from the directory and refuses to
        # start on a non-ASCII one.
        $composePath = Join-Path $projDir "docker-compose.yml"
        $nameLine = Select-String -Path $composePath -Pattern '^name:' | Select-Object -First 1
        if ($null -eq $nameLine) {
            Write-Host "[ERROR] docker-compose.yml has no top-level 'name:' key (#1307)." -ForegroundColor Red
            $namingOk = $false
            continue
        }
        $composeName = ($nameLine.Line -split ':', 2)[1].Trim()
        if ([string]::IsNullOrWhiteSpace($composeName)) {
            Write-Host "[ERROR] Compose project name is empty -- the #1307 failure exactly." -ForegroundColor Red
            $namingOk = $false
            continue
        }
        # -cne: case-sensitive. PowerShell's default comparisons are
        # case-INSENSITIVE, so -ne would accept 'zolc' for 'Zolc' and quietly
        # stop testing the transcoding's casing.
        if ($composeName -cne $expected) {
            Write-Host "[ERROR] Compose project name is '$composeName', expected '$expected'." -ForegroundColor Red
            $namingOk = $false
            continue
        }
        $asciiOnly = -not ($composeName.ToCharArray() | Where-Object { [int]$_ -gt 127 })
        if (-not $asciiOnly) {
            Write-Host "[ERROR] Compose project name '$composeName' is not ASCII; Docker will reject it." -ForegroundColor Red
            $namingOk = $false
            continue
        }

        # The registry must show the real name back to the user, not the
        # transcoded one -- keeping both is the entire point.
        $listOut = & $LDM_CMD list 2>$null | Out-String
        if ($listOut -cnotmatch [regex]::Escape($raw)) {
            Write-Host "[ERROR] 'ldm list' does not show '$raw'." -ForegroundColor Red
            $namingOk = $false
            continue
        }

        Write-Host "   [OK] $raw -> $expected"
        Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
    }

    Remove-Item -Recurse -Force $namingWorkdir -ErrorAction SilentlyContinue

    if ($namingOk) {
        Write-Host "[SUCCESS] Non-ASCII project naming verified (metadata verbatim, Docker transcoded)."
    } else {
        throw "Non-ASCII project naming verification failed."
    }


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
