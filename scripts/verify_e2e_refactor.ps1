# Comprehensive E2E Binary Verification for LDM (Windows Native PowerShell)
# Target: Verifies the INSTALLED binary, not the source code.
# Optimized for Windows Native.

# LDM-#1011: version this script itself (kept in sync with ldm_core/constants.py
# by scripts/release.py on every bump) so a locally-held copy can be checked
# against what actually shipped, rather than guessing from a file mtime -- git
# checkout/pull doesn't preserve original commit timestamps.
# LDM_MAGIC_VERSION: 2.18.0-pre.10
$SCRIPT_VERSION = "2.18.0-pre.10"

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
$ANNOUNCE_TEST_NODE = "announce-node-${TEST_PORT}"
$ANNOUNCE_TEST_PROJ = "announce-proj-${TEST_PORT}"
$PORTCONFLICT_PROJ = "portconflict-${TEST_PORT}"
$PORT_HOLDER = "ldm-e2e-port-holder-${TEST_PORT}"
# Kibana publishes this host port unconditionally (composer.py
# _build_kibana_service). It is the LDM-#1350 lever -- see that check below.
$KIBANA_HOST_PORT = 5601
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

function Remove-Ldm1383Artifacts {
    # LDM-#1383: the artefacts of the two checks below are torn down inline as
    # soon as each check finishes, and again from Finalize-Verification.
    # Leaving a Docker context behind is not merely untidy: every later
    # `ldm list`/`ldm status` in this suite would then resolve
    # `docker --context` against an unroutable TEST-NET-1 address and block on
    # an SSH connect timeout, turning one failed check into a suite that
    # appears to hang.
    Invoke-Cleanup $LDM_CMD "-y target rm $ANNOUNCE_TEST_NODE"
    Invoke-Cleanup "docker" "context rm $ANNOUNCE_TEST_NODE"
    Invoke-Cleanup $LDM_CMD "-y rm $ANNOUNCE_TEST_PROJ --delete"
    $announceDir = Join-Path $LDM_WORKSPACE $ANNOUNCE_TEST_PROJ
    if (Test-Path $announceDir) { Remove-Item -Recurse -Force $announceDir -ErrorAction SilentlyContinue }
    Invoke-Cleanup "docker" "rm -f $PORT_HOLDER"
    Invoke-Cleanup $LDM_CMD "-y rm $PORTCONFLICT_PROJ --delete"
    $conflictDir = Join-Path $LDM_WORKSPACE $PORTCONFLICT_PROJ
    if (Test-Path $conflictDir) { Remove-Item -Recurse -Force $conflictDir -ErrorAction SilentlyContinue }
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
    Remove-Ldm1383Artifacts
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

function Write-Verdict {
    # LDM-#1327: a verification verdict must reach the REPORT, not just the
    # console. Write-Host bypasses the pipeline entirely, so every "[SUCCESS]"
    # line in this suite was console-only and the durable record showed which
    # steps ran but never which assertions passed -- the whole verdict lived in
    # the "-pass" suffix of the filename, derived from the exit code.
    #
    # Mirrors the Get-VersionBannerLines pattern already used for the version
    # banner: print for the operator, append for the record.
    param([string]$Message)
    Write-Host $Message
    $Message | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
}

function Test-DockerDiskSpace {
    # LDM-#1430: a single up-front check cannot cover a run whose disk usage
    # peaks late. Between the pre-flight and the snapshot the run pulls two
    # large images, starts the stack, deploys a bundle and generates logs -- so
    # the headroom at the check says little about the headroom at peak. Hence a
    # function, called again before the snapshot phase.
    #
    # Asked of DOCKER, not the host: on Docker Desktop the engine's storage
    # lives in a VM with its own, far smaller disk, so Get-PSDrive would pass on
    # exactly the machines most likely to fail.
    #
    # Returns $true when there is room, $false when there is not. The caller
    # decides whether that is fatal.
    param([int]$NeedGb, [string]$Label)

    Write-Host "[INFO]  Checking Docker has room $Label (need $NeedGb GB)..."
    $dfOut = docker run --rm alpine df -P -k / 2>$null
    $freeKb = $null
    if ($dfOut) {
        $line = ($dfOut | Select-Object -Last 1)
        $fields = ($line -split '\s+') | Where-Object { $_ }
        if ($fields.Count -ge 4) { $freeKb = [int64]$fields[3] }
    }
    if (-not $freeKb) {
        Write-Host "[WARN]  Could not determine Docker's free space; continuing without the check." -ForegroundColor Yellow
        return $true
    }

    $freeGb = [math]::Floor($freeKb / 1024 / 1024)
    if ($freeGb -ge $NeedGb) {
        Write-Host "[SUCCESS] Docker has $freeGb GB free."
        return $true
    }

    Write-Verdict "[ERROR] Not enough disk space $Label."
    Write-Verdict "        Docker has $freeGb GB free; this needs about $NeedGb GB."
    Write-Verdict "        (The host may report far more -- Docker's storage is inside its own VM.)"
    Write-Verdict ""
    Write-Verdict "        Free some space, then re-run:"
    Write-Verdict "          ldm prune --seeds --samples     # reclaim LDM seed and sample archives"
    Write-Verdict "          ldm prune --all                 # also images, volumes and build cache"
    Write-Verdict "          docker system prune -a          # everything Docker considers unused"
    Write-Verdict ""
    return $false
}

function Get-PortHolderDiagnostic {
    # LDM-#1428: name what is holding a port when a port check cannot proceed.
    #
    # Two sources are queried, and BOTH are needed, because neither can answer
    # the question alone:
    #
    #   docker ps --filter publish=   is the only thing that names a CONTAINER.
    #   Get-NetTCPConnection          is the only thing that sees a NON-container
    #                                 holder (a stray service, an unrelated tunnel).
    #
    # The native lookup never names the container, because a published port is
    # held on the host by the runtime's forwarder: com.docker.backend on Docker
    # Desktop, wslrelay/vpnkit under WSL2, docker-proxy on native Linux, and
    # ssh on Colima/Lima. Reporting that alone sends the operator chasing a
    # process that is working perfectly -- so a known forwarder is labelled as
    # such and the reader is pointed back at the Docker line.
    param([int]$Port)

    Write-Verdict "[DIAG] What is holding port ${Port}?"

    $containers = @()
    try {
        $raw = & docker ps --filter "publish=$Port" --format '{{.Names}}  {{.Image}}  {{.Ports}}' 2>$null
        if ($LASTEXITCODE -eq 0 -and $raw) { $containers = @($raw) }
    } catch { }

    if ($containers.Count -gt 0) {
        Write-Verdict "   Container(s) publishing ${Port}:"
        foreach ($c in $containers) { Write-Verdict "     $c" }
    } else {
        Write-Verdict "   No running container publishes ${Port}."
    }

    $procs = @()
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        foreach ($conn in $conns) {
            $procName = "unknown"
            try {
                $procName = (Get-Process -Id $conn.OwningProcess -ErrorAction Stop).ProcessName
            } catch { }
            $procs += "$procName (PID $($conn.OwningProcess))  $($conn.LocalAddress):$($conn.LocalPort)"
        }
    } catch {
        # Get-NetTCPConnection is absent on some editions; netstat always exists.
        try {
            $net = & netstat -ano 2>$null | Select-String ":$Port\s.*LISTENING"
            foreach ($line in $net) { $procs += $line.ToString().Trim() }
        } catch { }
    }

    if ($procs.Count -eq 0) {
        Write-Verdict "   No host process found listening on ${Port}."
        return
    }

    Write-Verdict "   Host process(es) listening on ${Port}:"
    foreach ($pr in $procs) { Write-Verdict "     $pr" }

    if ($procs -match "com\.docker|dockerd|docker-proxy|wslrelay|vpnkit|ssh") {
        Write-Verdict "   [INFO] That is a container-runtime port forwarder, not the owner."
        if ($containers.Count -gt 0) {
            Write-Verdict "          The container named above is what actually holds ${Port}."
        } else {
            Write-Verdict "          A container in another Docker context may hold ${Port};"
            Write-Verdict "          try: docker context ls, then docker ps --filter publish=$Port"
        }
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

    # LDM-#1406: refuse to start without room to finish.
    #
    # A run that exhausts the disk fails somewhere in the middle and surfaces as
    # whatever broke first -- a PostgreSQL PANIC, an Elasticsearch write block,
    # a truncated layer -- rather than as "you are out of disk". The report then
    # reads as a defect finding, and these reports are the project's honest
    # record of what was tested.
    #
    # Asked of DOCKER, not the host. On Docker Desktop the engine's storage
    # lives in a VM with its own, far smaller disk, so Get-PSDrive would pass on
    # exactly the machines most likely to fail. Measured on one developer
    # machine: host 109.2 GB free, Docker VM 12.5 GB. Same reasoning as
    # Doctor._check_absolute_disk_space (LDM-#1095); using `docker run alpine
    # df` also keeps this identical to the .sh implementation.
    # LDM-#1430: was 10, and the gate is -lt, so exactly 10 GB passed -- then the
    # run died mid-snapshot with ENOSPC on a machine that had just been pruned.
    # The images alone are ~7.5 GB before the running stack grows, and the
    # snapshot then writes a database dump plus a tar of every payload directory
    # on top of that. 10 GB covered the pull and nothing after it.
    $minDiskGb = if ($env:LDM_VERIFY_MIN_DISK_GB) { [int]$env:LDM_VERIFY_MIN_DISK_GB } else { 15 }
    # LDM-#1419: clear leftovers from a PREVIOUS run before starting. The port
    # holder is named per-run, so only that run's cleanup removes it; a run
    # killed mid-flight leaves a container publishing 5601 that no later run
    # touches, and the next run then reports "Port 5601 is already in use"
    # during work unrelated to the port-conflict check. Sweep by prefix, because
    # this run cannot know what its predecessors were called.
    $staleHolders = docker ps -aq --filter "name=^ldm-e2e-port-holder-" 2>$null
    if ($staleHolders) {
        Write-Host "[INFO]  Removing leftover port holder(s) from a previous run..."
        $staleHolders | ForEach-Object { docker rm -f $_ 2>$null | Out-Null }
    }
    $staleConflict = docker ps -aq --filter "name=portconflict-" 2>$null
    if ($staleConflict) {
        $staleConflict | ForEach-Object { docker rm -f $_ 2>$null | Out-Null }
    }

    # LDM-#1419: record whether the global database pre-existed, so the #1400
    # check can put the machine back as it found it -- `ldm db start` provisions
    # it when absent and only stops it afterwards.
    $dbGlobalPreexisted = [bool](docker ps -aq --filter "name=^liferay-db-global$" 2>$null)

    if (-not (Test-DockerDiskSpace -NeedGb $minDiskGb -Label "to finish")) {
        throw "Insufficient Docker disk space, need $minDiskGb GB. Refusing before pulling anything, so no half-finished report is written."
    }

    # Pre-pull large images to avoid containerd lease timeouts during the timed E2E run
    Write-Host "[INFO]  Pre-pulling required Docker images..."
    & docker pull liferay/dxp:2026.q1.7-lts --quiet
    & docker pull postgres:16.2 --quiet

    Log-AndRun "Initializing Infrastructure" $LDM_CMD "-y infra setup --search"

    Write-Host ">> Verifying Custom SSL Port & Recreate..."
    Log-AndRun "Custom SSL Port Setup" $LDM_CMD "-y infra setup --ssl-port 8443 --force-recreate"
    $dockerInspect = & docker inspect liferay-proxy-global
    if ($dockerInspect -match '"HostPort": "8443"') {
        Write-Verdict "[SUCCESS] Custom SSL Port & Recreate verified."
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
        Write-Verdict "[SUCCESS] Dev Guardrails verified."
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
        Write-Verdict "[SUCCESS] System Tray application started successfully and remained alive."
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
            Write-Verdict "[SUCCESS] ldm doctor Dependency Integrity verified."
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
        Write-Verdict "[SUCCESS] Project Collision verified."
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
        Write-Verdict "[SUCCESS] Tag Validation Guardrail verified."
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
        Write-Verdict "[SUCCESS] Target registration verified."
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
        Write-Verdict "[SUCCESS] Loopback target registration verified."
    } else {
        Write-Host "[ERROR] ERROR: Target $loopbackNode not found in registry." -ForegroundColor Red
        exit 1
    }
    Log-AndRun "Target Status (Loopback Node)" $LDM_CMD "target status $loopbackNode"
    Log-AndRun "Target Remove (Loopback Node)" $LDM_CMD "target rm $loopbackNode"

    # --- LDM-#1383: E2E cover for the v2.18.0 remote-node / port-conflict UX
    #
    # #1341, #1345 and #1350 shipped with unit tests only, each deferred on the
    # same principle: an assertion must depend on nothing the script cannot
    # control. That principle stands. What #1383 got wrong was assuming the
    # only way to satisfy it was a real remote node and a real timing race.
    # Two of the three have a deterministic lever that touches no network.
    #
    # #1345 genuinely does not, and is left to its unit/integration tests. See
    # the note after the second check.
    #
    # Kept in step with the bash twin (scripts/verify_e2e_refactor.sh) --
    # cross-platform script parity is a hard rule, see
    # .agents/skills/testing-and-ci/SKILL.md.

    Write-Host ">> Verifying Remote Compute Node Announcement (LDM-#1341)..."
    #
    # No remote node is needed. announce_remote_targets() (ldm_core/utils.py)
    # decides remoteness via DockerService.get_docker_cmd_prefix(), which
    # consults only the LDM target registry -- name != "local" AND host outside
    # 127.0.0.0/8 -- and never checks that the Docker context actually exists.
    #
    # So: register a target on TEST-NET-1 (RFC 5737, permanently unroutable),
    # delete the Docker context that `target add` created, and point a project
    # at it. The project is classified remote and therefore announced, while
    # `docker --context` fails instantly with "context not found" rather than
    # opening an SSH connection. Measured end to end at 0s.
    #
    # Observed to fail against the unfixed code before being committed: at
    # cfcde7c9^ (the commit before #1341) announce_remote_targets does not
    # exist and `ldm list` prints no such line, so this check is not vacuous.
    $announceDir = Join-Path $LDM_WORKSPACE $ANNOUNCE_TEST_PROJ
    New-Item -ItemType Directory -Path (Join-Path $announceDir "files") -Force | Out-Null
    $announceMeta = '{"tag": "2026.q1.7-lts", "container_name": "' + $ANNOUNCE_TEST_PROJ + '", "port": 8099, "db_type": "postgresql", "target": "' + $ANNOUNCE_TEST_NODE + '"}'
    Set-Content -Path (Join-Path $announceDir "meta") -Value $announceMeta -Encoding ASCII

    Log-AndRun "Target Add (TEST-NET-1 Remote Node)" $LDM_CMD "-y target add $ANNOUNCE_TEST_NODE --host 192.0.2.10"
    Invoke-Cleanup "docker" "context rm $ANNOUNCE_TEST_NODE"

    # Refuse to continue rather than hang: with the context still present, the
    # `ldm list` below would dial 192.0.2.10 over SSH and block.
    $ctxNames = & docker context ls --format '{{.Name}}' 2>$null
    if (($ctxNames -join "`n") -split "`n" -contains $ANNOUNCE_TEST_NODE) {
        Remove-Ldm1383Artifacts
        throw "Could not remove Docker context '$ANNOUNCE_TEST_NODE'; refusing to continue because 'ldm list' would block on an SSH connect to 192.0.2.10."
    }

    $announceOut = (& $LDM_CMD list 2>&1) -join "`n"
    Write-Host $announceOut
    $announceOut | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8
    if (($announceOut -match "a remote compute node") -and ($announceOut -match "$ANNOUNCE_TEST_PROJ -> $ANNOUNCE_TEST_NODE")) {
        Write-Verdict "[SUCCESS] Remote compute node announced up front, naming project -> node (LDM-#1341)."
    } else {
        Remove-Ldm1383Artifacts
        throw "'ldm list' did not announce the remote node before resolving it; expected a line naming '$ANNOUNCE_TEST_PROJ -> $ANNOUNCE_TEST_NODE'."
    }

    # LDM-#1093: --json is a machine-readable contract, so the announcement is
    # deliberately suppressed there. Asserted because a future edit that moves
    # the announcement earlier would silently corrupt every --json consumer.
    $announceJsonOut = (& $LDM_CMD list --json 2>&1) -join "`n"
    if ($announceJsonOut -match "a remote compute node") {
        Remove-Ldm1383Artifacts
        throw "The remote-node announcement leaked into 'ldm list --json'."
    }
    Write-Verdict "[SUCCESS] Announcement correctly suppressed under --json (LDM-#1093)."
    Remove-Ldm1383Artifacts

    Write-Host ">> Verifying Late Port Conflict Guidance (LDM-#1350)..."
    #
    # The late check in ComposerStage fires when a port written into the
    # generated docker-compose.yml is taken by the time compose validation
    # runs. #1383 assumed reproducing it meant racing the seed download that
    # sits between the pre-flight check and this one. It does not: the
    # pre-flight only covers the Liferay port and custom_containers, so any
    # *other* compose-published port reaches the late check with no race.
    #
    # Kibana is the lever -- _build_kibana_service publishes a hardcoded 5601
    # and the pre-flight never looks at it. Enabling it via meta costs nothing:
    # the run dies at the port check, several stages before anything is
    # started, so no Kibana container is created and no image is pulled.
    #
    # Note this cannot be done with the Liferay port instead. Under -y the
    # pre-flight calls UI.die() on a taken port (handlers/base.py) and exits 1
    # long before ComposerStage -- observed. The obvious "bind 8080 and run"
    # approach asserts the wrong check.
    #
    # Observed to fail against the unfixed code: at d5749b38^ the same conflict
    # prints "Please stop the service currently using port 5601" and no tip.
    # The exit code was already 4 before #1350 (from #996), so the tip -- not
    # the exit code -- is what makes this check non-vacuous. Both are asserted.
    Invoke-Cleanup "docker" "rm -f $PORT_HOLDER"

    # LDM-#1428: this check must be the ONLY thing holding the port. If something
    # else already has it, our holder silently fails to start, the connect probe
    # below still succeeds against the foreign listener, and the assertion then
    # passes for entirely the wrong reason. Detect that up front and name it.
    & $VENV_PYTHON -c "import socket, sys; s = socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', $KIBANA_HOST_PORT)) == 0 else 1)" *> $null
    if ($LASTEXITCODE -eq 0) {
        Get-PortHolderDiagnostic -Port $KIBANA_HOST_PORT
        Remove-Ldm1383Artifacts
        throw "Port $KIBANA_HOST_PORT is already in use before the LDM-#1350 check starts. This check must own the port; a foreign listener would make it pass for the wrong reason."
    }

    & docker run -d --name $PORT_HOLDER -p "${KIBANA_HOST_PORT}:80" alpine sleep 300 *> $null

    # `docker run -d` returns before the published port is necessarily
    # accepting connections, and LDM's check_port() treats a refused connect as
    # "free". Wait for the bind to actually be live rather than assuming it.
    $portHeld = $false
    foreach ($attempt in 1..30) {
        & $VENV_PYTHON -c "import socket, sys; s = socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', $KIBANA_HOST_PORT)) == 0 else 1)" *> $null
        if ($LASTEXITCODE -eq 0) { $portHeld = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $portHeld) {
        # LDM-#1428: say what holds it, rather than leaving the operator to work
        # it out per-OS. Most often a leftover container from an interrupted run.
        Get-PortHolderDiagnostic -Port $KIBANA_HOST_PORT
        Remove-Ldm1383Artifacts
        throw "Could not occupy port $KIBANA_HOST_PORT, so the LDM-#1350 check cannot run. Refusing to skip silently: an assertion that quietly stops running is worse than a red one."
    }

    $conflictDir = Join-Path $LDM_WORKSPACE $PORTCONFLICT_PROJ
    New-Item -ItemType Directory -Path (Join-Path $conflictDir "files") -Force | Out-Null
    $conflictMeta = '{"tag": "2026.q1.7-lts", "container_name": "' + $PORTCONFLICT_PROJ + '", "port": 8097, "db_type": "postgresql", "search_kibana_enabled": "true"}'
    Set-Content -Path (Join-Path $conflictDir "meta") -Value $conflictMeta -Encoding ASCII

    $conflictOut = (& $LDM_CMD -y run $conflictDir --no-wait 2>&1) -join "`n"
    $conflictRc = $LASTEXITCODE
    Write-Host $conflictOut
    $conflictOut | Out-File -FilePath $RESULTS_FILE_TMP -Append -Encoding utf8

    if ($conflictRc -ne 4) {
        # LDM-#1428: exit 1 here means LDM did not detect the conflict and Docker
        # hit it instead. The question that decides whether that is an LDM bug or
        # a broken fixture is "was the port actually held?" -- so answer it, in
        # the report, at the moment of failure.
        Get-PortHolderDiagnostic -Port $KIBANA_HOST_PORT
        Remove-Ldm1383Artifacts
        throw "Late port conflict exited $conflictRc, expected 4 (Orchestration/Deployment Error)."
    }
    if ($conflictOut -notmatch "Port conflict detected: Port $KIBANA_HOST_PORT") {
        Remove-Ldm1383Artifacts
        throw "No port-conflict message naming port $KIBANA_HOST_PORT."
    }
    # LDM-#1397: kibana publishes a literal 5601 in the compose builder, so the
    # correct tip is NOT the next-free-port promise -- a re-run regenerates the
    # same literal and fails identically. Asserting the promise would re-enshrine
    # the bug #1397 fixed.
    if ($conflictOut -notmatch "has a fixed port") {
        Remove-Ldm1383Artifacts
        throw "The tip did not say the port is fixed (LDM-#1397). kibana's port is a literal in the compose builder, so a re-run cannot move it; promising one sends the user round a loop that cannot terminate."
    }
    if ($conflictOut -match "the pre-flight check will select port \d+ instead") {
        Remove-Ldm1383Artifacts
        throw "The tip promised a pre-flight re-select for a fixed-port service (LDM-#1397)."
    }
    Write-Verdict "[SUCCESS] Late port conflict exits 4 and gives honest advice for a fixed-port service (LDM-#1350/#1397)."
    Remove-Ldm1383Artifacts

    # LDM-#1345 (diagnose an SSH failure instead of dumping the connect blob)
    # is deliberately NOT asserted here; the deferral stays tracked in #1398,
    # the successor issue opened when #1383 was closed.
    #
    # Unlike the two checks above, its trigger cannot be faked: the diagnosis
    # is produced from real `ssh`/`docker context` stderr, so provoking it
    # means actually attempting and failing a connection. Every honest trigger
    # has a duration this script does not own -- a TEST-NET-1 address fails via
    # a connect timeout set by the host network stack, a `.invalid` hostname
    # depends on the resolver, and "connection refused" depends on whether the
    # machine happens to run sshd. An assertion whose runtime is decided by the
    # network is the flaky release-gate check #1383 exists to avoid.
    #
    # It is covered by TestRemoteContextFailureDiagnosis in
    # ldm_core/tests/test_utils.py, which drives the real diagnosis function
    # with captured stderr from a genuine failure, plus an integration test
    # over the real CommandRunner path that was confirmed to fail against the
    # unfixed wiring.

    $remoteHost = $env:LDM_TEST_REMOTE_HOST
    if (-not $remoteHost) { $remoteHost = $env:LDM_REMOTE_TARGET }
    if ($remoteHost) {
        Write-Host ">> Probing Remote Compute Target ($remoteHost)..."
        $remoteNodeName = "remote-${TARGET_TEST_NODE}"
        Log-AndRun "Target Add (Remote Host)" $LDM_CMD "target add $remoteNodeName --host $remoteHost"
        $remoteStatusOut = & $LDM_CMD target status $remoteNodeName 2>&1
        Write-Host ($remoteStatusOut -join "`n")
        if ($remoteStatusOut -match "ONLINE") {
            Write-Verdict "[SUCCESS] Remote Target Probe verified (ONLINE)."
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
        Write-Verdict "[SUCCESS] --nightly flag resolution verified."
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
        Write-Verdict "[SUCCESS] --master flag alias verified."
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
        Write-Verdict "[SUCCESS] Pre-existing test project removed."
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
            Write-Verdict "[SUCCESS] Hot Deploy verified."
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
    # LDM-#1430: the snapshot is the disk-hungry phase -- a database dump plus a
    # tar of every payload directory, on top of two large images and a running
    # stack. This is where the OrbStack run died with ENOSPC after passing the
    # up-front check. Failing at a named check beats failing inside tar; 5 GB is
    # the headroom the snapshot itself needs, not the run total.
    if (-not (Test-DockerDiskSpace -NeedGb 5 -Label "for the snapshot")) {
        Write-Verdict "[ERROR] Refusing to start the snapshot without room to finish it."
        Write-Verdict "        Continuing would fail inside tar, and a snapshot that cannot"
        Write-Verdict "        write its payload is not a snapshot (see LDM-#1429)."
        throw "Insufficient Docker disk space for the snapshot phase."
    }

    Log-AndRun "Creating Snapshot" $LDM_CMD "-y snapshot --name Binary-Verify"
    $latestSnapshotDir = (Get-ChildItem snapshots | Sort LastWriteTime -Desc | Select -First 1).FullName
    $shaFile = Join-Path $latestSnapshotDir "files.tar.gz.sha256"
    "CORRUPTED" | Out-File $shaFile -Encoding utf8
    if ((& $LDM_CMD -y restore --latest 2>&1) -match "Integrity check failed") { 
        Write-Verdict "[SUCCESS] Integrity check verified."
    } else { 
        throw "Integrity block failed" 
    }
    Log-AndRun "Bypassing Integrity" $LDM_CMD "-y restore --latest --no-verify"

    Write-Host ">> Verifying Legacy Command Translation..."
    $legacyDoc = & $LDM_CMD doctor --help 2>&1
    $legacySetup = & $LDM_CMD infra-setup --help 2>&1
    if ($legacyDoc -match "Usage" -and $legacySetup -match "Usage") {
        Write-Verdict "[SUCCESS] Legacy command translation verified."
    } else {
        throw "Legacy command translation failed."
    }

    # UX & Defaults & Scaling
    Write-Host ">> Verifying Cascading Defaults..."
    & $LDM_CMD config defaults test_key test_value > $null 2>&1
    $defaultsOut = & $LDM_CMD config defaults 2>&1
    if ($defaultsOut -match "test_key" -and $defaultsOut -match "test_value" -and $defaultsOut -match "User") {
        Write-Verdict "[SUCCESS] Set User Default verified."
    } else {
        throw "Set User Default failed. Output: $defaultsOut"
    }
    & $LDM_CMD config defaults --remove test_key > $null 2>&1
    $defaultsOut2 = & $LDM_CMD config defaults 2>&1
    if ($defaultsOut2 -notmatch "test_key") {
        Write-Verdict "[SUCCESS] Remove User Default verified."
    } else {
        throw "Remove User Default failed. Output: $defaultsOut2"
    }

    Write-Host ">> Verifying Env Sync..."
    & $LDM_CMD config env . TEST_SECRET=supersecret123 > $null 2>&1
    if ((Get-Content "docker-compose.yml" -Raw) -match "TEST_SECRET=supersecret123") { 
        Write-Verdict "[SUCCESS] Env Sync verified."
    } else {
        throw "Env Sync verification failed."
    }

    Write-Host ">> Verifying Redaction..."
    $redactOut = & $LDM_CMD status REDACT_SECRET=hidden 2>&1
    if ($redactOut -match "REDACT_SECRET=\[REDACTED\]") { 
        Write-Verdict "[SUCCESS] Redaction verified."
    } else {
        throw "Redaction verification failed. Output: $redactOut"
    }

    Write-Host ">> Verifying Scaling..."
    Log-AndRun "Scaling Liferay" $LDM_CMD "-y scale . liferay=3 --no-run"
    if ((Get-Content "meta" -Raw) -match "scale_liferay.*3") { 
        Write-Verdict "[SUCCESS] Scaling verified."
    } else {
        throw "Scaling verification failed."
    }

    Write-Host ">> Verifying logs --instance..."
    $logErr4 = & $LDM_CMD logs . --instance 4 2>&1
    $logErr2 = & $LDM_CMD logs . --instance 2 2>&1
    if ($logErr4 -match "Invalid instance index 4" -and $logErr2 -match "Container 'ldm-smoke-test-liferay-2' not found") {
        Write-Verdict "[SUCCESS] logs --instance routing verified."
    } else {
        throw "logs --instance routing validation failed."
    }

    Write-Host ">> Verifying Trace Log and Logs Export..."
    $traceLogPath = Join-Path $HOME ".ldm/last-command.log"
    if (Test-Path $traceLogPath) {
        Write-Verdict "[SUCCESS] Trace Log (last-command.log) verified."
    } else {
        throw "Trace Log file missing."
    }

    Log-AndRun "Scaling Liferay back to 1 for logs export check" $LDM_CMD "-y scale . liferay=1 --no-run"
    Log-AndRun "Starting project for logs export check" $LDM_CMD "-y run . --no-wait"
    Log-AndRun "Exporting project logs" $LDM_CMD "logs . --export"
    $exportFiles = Resolve-Path *.log -ErrorAction SilentlyContinue
    if ($exportFiles) {
        $exportFile = $exportFiles[0].Path
        Write-Verdict "[SUCCESS] Logs Export verified ($exportFile)."
        Remove-Item $exportFile -Force
    } else {
        throw "Logs Export file not generated."
    }
    Write-Host ">> Verifying ldm start UX fast-fail..."
    $startFailOut = & $LDM_CMD start fake-non-existent-project 2>&1
    if ($startFailOut -match "Project not found or not initialized") {
        Write-Verdict "[SUCCESS] ldm start fast-fail verified."
    } else {
        throw "ldm start fast-fail message not found. Output: $startFailOut"
    }

    Write-Host ">> Verifying ldm run reconfigure UX message..."
    $runReconfigOut = & $LDM_CMD -y run . --no-wait --info 2>&1
    if ($runReconfigOut -match "already exists and this command will reconfigure it") {
        Write-Verdict "[SUCCESS] ldm run reconfigure UX message verified."
    } else {
        throw "ldm run reconfigure message not found. Output: $runReconfigOut"
    }

    Write-Host ">> Verifying Safe SELECT SQL Query..."
    $dbQueryOut = & $LDM_CMD db query . -s "SELECT 1 as test_val;" --allow-db-query 2>&1
    if ($dbQueryOut -match "test_val") {
        Write-Verdict "[SUCCESS] Safe SELECT SQL Query verified."
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
        Write-Verdict "[SUCCESS] Properties Override Cascade verified (rebuild)."
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
        Write-Verdict "[SUCCESS] Properties Override Reset verified."
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
        Write-Verdict "[SUCCESS] ldm list --json schema verified."
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
        Write-Verdict "[SUCCESS] ldm status --json schema verified."
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
        Write-Verdict "[SUCCESS] Idempotent Exit Code 5 verified."
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
        Write-Verdict "[SUCCESS] Client Extension deploy & staging verified."
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

    # Read the container name from meta rather than reusing a variable from
    # elsewhere in this script. PowerShell resolves an undefined variable to
    # $null WITHOUT erroring, so a wrong name here does not fail loudly -- it
    # silently produces `docker exec  ls -la ...`, where docker takes the next
    # token as the container and reports "No such container: ls". Every
    # assertion below then fails for a reason unrelated to the feature. That is
    # exactly what happened on the first Windows run of this block.
    $patchContainer = & $VENV_PYTHON -c "import json;print(json.load(open('meta',encoding='utf-8')).get('container_name',''))"
    if ([string]::IsNullOrWhiteSpace($patchContainer)) {
        throw "Portal patch check could not determine the container name from meta."
    }
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

    & docker exec $patchContainer test -f $patchTarget *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] patch JAR not present at $patchTarget inside the container." -ForegroundColor Red
        & docker exec $patchContainer ls -la $containerPortal 2>&1 | Select-Object -First 5 | Write-Host
        $patchOk = $false
    } else {
        # Content must match exactly -- a truncated or empty copy would still
        # satisfy a bare existence check.
        $patchInSha = (& docker exec $patchContainer sha256sum $patchTarget 2>$null) -split '\s+' | Select-Object -First 1
        if ($patchInSha -ne $patchHostSha) {
            Write-Host "[ERROR] patch JAR content differs inside the container." -ForegroundColor Red
            Write-Host "   host:      $patchHostSha"
            Write-Host "   container: $patchInSha"
            $patchOk = $false
        }

        # The #1264 silent failure: readable by Liferay's uid, not merely present.
        & docker exec -u 1000 $patchContainer test -r $patchTarget *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] patch JAR is not readable by uid 1000 -- OSGi would fail to resolve it while the container still booted healthy (#1264)." -ForegroundColor Red
            & docker exec $patchContainer ls -l $patchTarget 2>&1 | Select-Object -First 2 | Write-Host
            $patchOk = $false
        }
    }

    # 3. --force-recreate replaces the container; the patch must survive it.
    if ($patchOk) {
        Log-AndRun "Re-creating with patches" $LDM_CMD "-y restart . --force-recreate --force-portal-patches"
        & docker exec $patchContainer test -f $patchTarget *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] patch JAR was dropped by 'restart --force-recreate' (#1264)." -ForegroundColor Red
            $patchOk = $false
        }
    }

    Remove-Item -Recurse -Force $patchDir -ErrorAction SilentlyContinue

    if ($patchOk) {
        Write-Verdict "[SUCCESS] Portal patch overlay verified (refused without --force, applied and readable with it, survives --force-recreate)."
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

    # Projects are created in a NESTED sub-directory, deliberately.
    #
    # find_dxp_roots() scans with iterdir(), i.e. exactly one level deep, so a
    # project at <workspace>\naming-<port>\<name> cannot be found by the
    # directory scan -- it is reachable only through the global registry. That
    # makes this block assert two things at once: that the name survives
    # round-trip, and that the project was actually REGISTERED (LDM-#1324).
    #
    # Flattening these into $LDM_WORKSPACE would make the assertion pass off
    # the one-level scan alone and silently stop testing registration, which is
    # the defect this suite found on Windows in the first place.
    #
    # Names are prefixed so they cannot collide with a real project. The prefix
    # is ASCII and passes through sanitize_id() unchanged, so the expected
    # Docker name is derived rather than guessed.
    $namingPrefix = "test-naming-"
    $namingWorkdir = Join-Path $LDM_WORKSPACE "naming-$TEST_PORT"
    Remove-Item -Recurse -Force $namingWorkdir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $namingWorkdir | Out-Null

    $namingOk = $true
    $namingPrevious = Get-Location

    foreach ($case in $namingCases) {
        # $raw is the name under test; $projName is what is actually created.
        $raw = $namingPrefix + $case.Raw
        $expected = $namingPrefix + $case.Docker
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
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
            continue
        }

        $metaPath = Join-Path $projDir "meta"
        if (-not (Test-Path $metaPath)) {
            Write-Host "[ERROR] no meta written for '$raw'; expected $metaPath." -ForegroundColor Red
            $namingOk = $false
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
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
        if (-not $metaOk) {
            $namingOk = $false
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
            continue
        }

        # The Docker half. #1307 added the explicit top-level 'name:'; without
        # it Compose derives the project name from the directory and refuses to
        # start on a non-ASCII one.
        $composePath = Join-Path $projDir "docker-compose.yml"
        $nameLine = Select-String -Path $composePath -Pattern '^name:' | Select-Object -First 1
        if ($null -eq $nameLine) {
            Write-Host "[ERROR] docker-compose.yml has no top-level 'name:' key (#1307)." -ForegroundColor Red
            $namingOk = $false
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
            continue
        }
        $composeName = ($nameLine.Line -split ':', 2)[1].Trim()
        if ([string]::IsNullOrWhiteSpace($composeName)) {
            Write-Host "[ERROR] Compose project name is empty -- the #1307 failure exactly." -ForegroundColor Red
            $namingOk = $false
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
            continue
        }
        # -cne: case-sensitive. PowerShell's default comparisons are
        # case-INSENSITIVE, so -ne would accept 'zolc' for 'Zolc' and quietly
        # stop testing the transcoding's casing.
        if ($composeName -cne $expected) {
            Write-Host "[ERROR] Compose project name is '$composeName', expected '$expected'." -ForegroundColor Red
            $namingOk = $false
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
            continue
        }
        $asciiOnly = -not ($composeName.ToCharArray() | Where-Object { [int]$_ -gt 127 })
        if (-not $asciiOnly) {
            Write-Host "[ERROR] Compose project name '$composeName' is not ASCII; Docker will reject it." -ForegroundColor Red
            $namingOk = $false
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
            continue
        }

        # LDM must report the REAL name back to the user, not the transcoded
        # one -- keeping both is the entire point.
        #
        # Asserted against `list --json`, never the rendered table. --json
        # bypasses the table and colour formatting entirely (#1093) and emits
        # UTF-8 JSON, whereas the table is a presentation layer: box-drawing
        # characters, ANSI colour, and column widths that truncate. Regex-
        # matching that output means the assertion depends on the console code
        # page, which on Windows PowerShell 5.1 is not reliably UTF-8. Same
        # principle already applied to `meta` above: parse the data, do not
        # string-match a rendering of it.
        $listJson = (& $LDM_CMD list --json 2>$null | Out-String)
        $listed = $null
        try {
            $listed = $listJson | ConvertFrom-Json
        } catch {
            Write-Host "[ERROR] 'ldm list --json' did not return parseable JSON for '$raw'." -ForegroundColor Red
            Write-Host $listJson
            $namingOk = $false
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
            continue
        }

        # The entry key has varied between 'project' and 'name'; accept either
        # rather than pinning the assertion to one and failing for the wrong
        # reason if it changes.
        $listedNames = @($listed | ForEach-Object {
            if ($null -ne $_.project) { $_.project } elseif ($null -ne $_.name) { $_.name }
        })
        if ($listedNames -cnotcontains $raw) {
            Write-Host "[ERROR] 'ldm list --json' does not report '$raw'." -ForegroundColor Red
            Write-Host ("         reported: " + ($listedNames -join ", "))
            $namingOk = $false
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
            continue
        }

        # LDM-#1351: `ldm info` must report the name APPLIED to each thing --
        # the Provisioned Containers block exists to be pasted into
        # `docker logs`/`docker exec`, and it used to print the verbatim
        # metadata values, offering names Docker cannot resolve. Asserted on the
        # command's OUTPUT, which is the contract a user consumes and the half
        # the file-level assertions cannot see. No boot needed.
        $infoOut = (& $LDM_CMD info $raw 2>&1 | Out-String)

        if ($infoOut -notmatch [regex]::Escape($raw)) {
            Write-Host "[ERROR] 'ldm info $raw' does not show the verbatim project name." -ForegroundColor Red
            $namingOk = $false
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
            continue
        }

        # Parsed in Python for parity with the bash script and to keep the
        # assertion identical across platforms.
        & $VENV_PYTHON -c @"
import sys

raw, expected, out = sys.argv[1], sys.argv[2], sys.argv[3]
rows = [
    line
    for line in out.splitlines()
    if any(k in line for k in ('Liferay:', 'Database:', 'Tunnel:'))
]
assert rows, 'ldm info printed no Provisioned Containers rows'

for line in rows:
    assert raw not in line, (
        'ldm info offers a container name Docker does not have (#1351): %r' % line.strip()
    )

liferay = [line for line in rows if 'Liferay:' in line]
assert liferay, 'no Liferay row in ldm info output'
assert expected in liferay[0], (
    'Liferay row is %r, expected the transcoded name %r' % (liferay[0].strip(), expected)
)
"@ $raw $expected $infoOut
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] 'ldm info $raw' reported names that are not in effect." -ForegroundColor Red
            $namingOk = $false
            Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
            continue
        }

        Write-Verdict "   [OK] $raw -> $expected"
        Invoke-Cleanup $LDM_CMD "-y rm $raw --delete"
    }

    Remove-Item -Recurse -Force $namingWorkdir -ErrorAction SilentlyContinue

    if ($namingOk) {
        Write-Verdict "[SUCCESS] Non-ASCII project naming verified (metadata verbatim, Docker transcoded)."
    } else {
        throw "Non-ASCII project naming verification failed."
    }


    Write-Host ">> Verifying shared database mode (#1359 / #1354 / #1357)..."
    # Every assertion is derivable from `init --no-up --no-seed`, so this boots
    # nothing and costs seconds.
    #
    # It exists because the combination was completely broken and no unit test
    # noticed: the composer tests set database_mode in META, which both call
    # sites read, while the CLI flag lands in ARGS, which only one read. The two
    # then disagreed inside one run -- no database service was emitted, yet the
    # liferay service still declared depends_on for it -- so compose refused the
    # file for every project.
    #
    # The project name is capitalised deliberately: the derived database name is
    # lowercased (#1354) because PostgreSQL folds an unquoted CREATE DATABASE,
    # and an all-lowercase fixture would assert nothing about that.
    $sharedDbName = "TestSharedDb"
    $sharedDbWorkdir = Join-Path $env:LDM_WORKSPACE "shareddb-$TEST_PORT"
    Remove-Item -Recurse -Force $sharedDbWorkdir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $sharedDbWorkdir -Force | Out-Null
    $sharedDbOk = $true

    Invoke-Cleanup $LDM_CMD "-y rm $sharedDbName --delete"

    Push-Location $sharedDbWorkdir
    try {
        & $LDM_CMD -y init $sharedDbName --no-up --no-seed --database-mode shared --db postgresql *> $null
        $sharedDbRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    $sharedDbDir = Join-Path $sharedDbWorkdir $sharedDbName
    if ($sharedDbRc -ne 0) {
        # The #1359 signature: compose refuses a file whose liferay service
        # depends on a database service shared mode deliberately did not emit.
        Write-Host "[ERROR] 'ldm init --database-mode shared' failed with exit $sharedDbRc." -ForegroundColor Red
        $sharedDbOk = $false
    } else {
        $composePath = Join-Path $sharedDbDir "docker-compose.yml"
        $metaPath = Join-Path $sharedDbDir "meta"
        $propsPath = Join-Path $sharedDbDir "files/portal-ext.properties"

        # Parsed in Python rather than PowerShell: 5.1 has no YAML reader, and
        # the meta name is JSON-escaped so a string match would compare against
        # the escape sequence (the same trap the naming block documents).
        & $VENV_PYTHON -c @"
import json, sys
import yaml

compose_path, meta_path, props_path = sys.argv[1:4]

compose = yaml.safe_load(open(compose_path, encoding='utf-8')) or {}
services = compose.get('services') or {}
defined = set(services)
for name, conf in services.items():
    deps = conf.get('depends_on') or []
    if isinstance(deps, dict):
        deps = list(deps)
    for dep in deps:
        assert dep in defined, (
            'service %r depends on undefined service %r -- docker compose will refuse this file (#1359)'
            % (name, dep)
        )

meta = json.load(open(meta_path, encoding='utf-8'))
assert meta.get('database_mode') == 'shared', (
    'meta database_mode is %r, expected shared -- later commands will resolve the mode from defaults (#1359)'
    % (meta.get('database_mode'),)
)

url = ''
for line in open(props_path, encoding='utf-8'):
    if line.startswith('jdbc.default.url'):
        url = line.split('=', 1)[1].strip()
        break
assert url, 'no jdbc.default.url written'
assert 'liferay-db-global' in url, (
    'JDBC URL %r does not target the shared cluster -- the CLI flag was not honoured (#1359)' % (url,)
)
db_part = url.rsplit('/', 1)[-1]
assert db_part == db_part.lower(), (
    'shared database name %r is not lowercase; PostgreSQL folds an unquoted CREATE DATABASE (#1354)'
    % (db_part,)
)
"@ $composePath $metaPath $propsPath
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] shared database mode produced an inconsistent project." -ForegroundColor Red
            $sharedDbOk = $false
        }
    }

    Invoke-Cleanup $LDM_CMD "-y rm $sharedDbName --delete"

    # #1357: the global shared database is PostgreSQL only, so a MariaDB driver
    # aimed at port 3306 of it could never connect. Refusal must be loud.
    Push-Location $sharedDbWorkdir
    try {
        & $LDM_CMD -y init "$($sharedDbName)Mysql" --no-up --no-seed --database-mode shared --db mysql *> $null
        $sharedDbMysqlRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($sharedDbMysqlRc -eq 0) {
        Write-Host "[ERROR] '--database-mode shared --db mysql' was accepted; it cannot work (#1357)." -ForegroundColor Red
        $sharedDbOk = $false
    }
    Invoke-Cleanup $LDM_CMD "-y rm $($sharedDbName)Mysql --delete"

    Remove-Item -Recurse -Force $sharedDbWorkdir -ErrorAction SilentlyContinue

    if ($sharedDbOk) {
        Write-Verdict "[SUCCESS] Shared database mode verified (valid compose, shared URL, lowercase name, MySQL refused)."
    } else {
        throw "Shared database mode verification failed."
    }


    Write-Host ">> Verifying 'ldm db start' / 'ldm db stop' (LDM-#1400)..."
    # Dead in every release up to v2.18.0-pre.4: both built
    # `docker compose -f infra-compose.yml start db`, but that file defines only
    # `traefik` -- there is no `db` service, and the global database is created
    # by a bare `docker run`. It matters because cmd_reset_admin tells shared-DB
    # users to run `ldm db start`, so LDM directed people into a command that
    # could not work.
    $dbGlobal = "liferay-db-global"
    $dbCmdOk = $true

    & $LDM_CMD -y db start *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] 'ldm db start' exited non-zero (LDM-#1400)." -ForegroundColor Red
        $dbCmdOk = $false
    } else {
        $running = docker ps --filter "name=^$dbGlobal$" --format "{{.Names}}"
        if ($running -notmatch $dbGlobal) {
            Write-Host "[ERROR] 'ldm db start' returned 0 but $dbGlobal is not running. A silent success is what made the original breakage hard to notice." -ForegroundColor Red
            $dbCmdOk = $false
        }
    }

    # Idempotence: a second start must succeed and must say something. A command
    # that succeeds silently is indistinguishable from one that did nothing.
    if ($dbCmdOk) {
        $dbAgain = & $LDM_CMD -y db start 2>&1 | Out-String
        if ($dbAgain -notmatch "already running") {
            Write-Host "[ERROR] A second 'ldm db start' did not report the container was already running." -ForegroundColor Red
            $dbCmdOk = $false
        }
    }

    if ($dbCmdOk) {
        & $LDM_CMD -y db stop *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] 'ldm db stop' exited non-zero (LDM-#1400)." -ForegroundColor Red
            $dbCmdOk = $false
        } else {
            $stillUp = docker ps --filter "name=^$dbGlobal$" --format "{{.Names}}"
            if ($stillUp -match $dbGlobal) {
                Write-Host "[ERROR] 'ldm db stop' returned 0 but $dbGlobal is still running." -ForegroundColor Red
                $dbCmdOk = $false
            }
        }
    }

    if ($dbCmdOk) {
        # LDM-#1419: leave the machine as we found it. If this check provisioned
        # the global database, remove it -- including its volume, which would
        # otherwise survive as an orphan (see #1414).
        if (-not $dbGlobalPreexisted) {
            Write-Host "[INFO]  Removing the global database this check provisioned..."
            docker rm -f $dbGlobal 2>$null | Out-Null
            docker volume rm liferay-db-global-data 2>$null | Out-Null
        }
        Write-Verdict "[SUCCESS] 'ldm db start'/'db stop' drive the real global container, idempotently (LDM-#1400)."
    } else {
        throw "Shared database start/stop verification failed."
    }

    Write-Host ">> Verifying project UUID ownership labels (LDM-#1393 / #1395)..."
    # Ownership was labelled by NAME, which is only as stable as the name: a
    # renamed project's volumes keep the old label and belong to nothing, so
    # `ldm prune` reports live resources as orphans. Artefact inspection only.
    $uuidWorkdir = Join-Path $env:LDM_WORKSPACE "uuidcheck-$TEST_PORT"
    Remove-Item -Recurse -Force $uuidWorkdir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $uuidWorkdir -Force | Out-Null
    $uuidOk = $true

    Invoke-Cleanup $LDM_CMD "-y rm UuidCheck --delete"

    Push-Location $uuidWorkdir
    try {
        & $LDM_CMD -y init UuidCheck --no-up --no-seed *> $null
        $uuidRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    if ($uuidRc -ne 0) {
        Write-Host "[ERROR] 'ldm init' failed; the LDM-#1393 check cannot run." -ForegroundColor Red
        $uuidOk = $false
    } else {
        $uuidDir = Join-Path $uuidWorkdir "UuidCheck"
        & $VENV_PYTHON -c @"
import json, sys
import yaml

meta_path, compose_path = sys.argv[1:3]
label = 'com.liferay.ldm.project.uuid'

want = json.load(open(meta_path, encoding='utf-8')).get('uuid', '')
assert want, 'the project meta carries no uuid (#1393)'

compose = yaml.safe_load(open(compose_path, encoding='utf-8')) or {}

for name, svc in (compose.get('services') or {}).items():
    labels = [str(x) for x in (svc.get('labels') or [])]
    assert '%s=%s' % (label, want) in labels, (
        'service %r is not labelled with the project uuid -- prune matches owners '
        'by name, so a renamed project would look like an orphan (#1395)' % (name,)
    )

for vname, vdef in (compose.get('volumes') or {}).items():
    got = ((vdef or {}).get('labels') or {}).get(label)
    assert got == want, (
        'volume %r carries %r, expected the project uuid (#1395)' % (vname, got)
    )
"@ (Join-Path $uuidDir "meta") (Join-Path $uuidDir "docker-compose.yml")
        if ($LASTEXITCODE -ne 0) { $uuidOk = $false }
    }

    Remove-Item -Recurse -Force $uuidWorkdir -ErrorAction SilentlyContinue

    if ($uuidOk) {
        Write-Verdict "[SUCCESS] Every service and volume carries the project UUID ownership label (LDM-#1393/#1395)."
    } else {
        throw "Project UUID label verification failed."
    }

    Write-Host ">> Verifying shared search mode (#1362 / #1363 / #1353)..."
    # Derivable from `init --no-up --no-seed`: no boot, nothing outside this
    # script's control. Deliberately NOT asserting that Liferay indexes into
    # the shared cluster -- that needs a boot plus indexing and the wait is
    # externally timed, which would fail this suite for unrelated reasons.
    $sharedSearchName = "TestSharedSearch"
    $sharedSearchWorkdir = Join-Path $env:LDM_WORKSPACE "sharedsearch-$TEST_PORT"
    Remove-Item -Recurse -Force $sharedSearchWorkdir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $sharedSearchWorkdir -Force | Out-Null
    $sharedSearchOk = $true

    Invoke-Cleanup $LDM_CMD "-y rm $sharedSearchName --delete"

    Push-Location $sharedSearchWorkdir
    try {
        & $LDM_CMD -y init $sharedSearchName --no-up --no-seed --search-mode shared *> $null
        $sharedSearchRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    $sharedSearchDir = Join-Path $sharedSearchWorkdir $sharedSearchName
    if ($sharedSearchRc -ne 0) {
        Write-Host "[ERROR] 'ldm init --search-mode shared' failed with exit $sharedSearchRc." -ForegroundColor Red
        $sharedSearchOk = $false
    } else {
        & $VENV_PYTHON -c @"
import json, sys
from pathlib import Path

root = Path(sys.argv[1])

meta = json.loads((root / 'meta').read_text(encoding='utf-8'))
assert meta.get('search_mode') == 'shared', (
    'meta search_mode is %r, expected shared -- the CLI flag was ignored (#1362)'
    % (meta.get('search_mode'),)
)

configs_dir = root / 'osgi' / 'configs'
configs = sorted(configs_dir.glob('*ElasticsearchConfiguration.config'))
assert configs, (
    'no ElasticsearchConfiguration.config written; the LIFERAY_ELASTICSEARCH* '
    'env vars alone do not configure Liferay (#1353)'
)

# LDM-#1418: both an elasticsearch7 and an elasticsearch8 config can exist -- the
# common/ baseline ships one per major version, and LDM writes the one matching
# the tag. Reading configs[0] took the es7 file alphabetically, which for a
# modern ES8 project is the inert baseline copy.
def _major(path):
    tail = path.name.split('elasticsearch', 1)[1]
    digits = ''
    for ch in tail:
        if not ch.isdigit():
            break
        digits += ch
    return int(digits or 0)

active = max(configs, key=_major)
body = active.read_text(encoding='utf-8')
assert 'productionModeEnabled=B' in body, body

# The address is valid inline here, or in a sibling connection config referenced
# by remoteClusterConnectionId. Both reach the same cluster.
sibling = configs_dir / active.name.replace(
    'ElasticsearchConfiguration', 'ElasticsearchConnectionConfiguration'
)
address_sources = [body]
if sibling.exists():
    address_sources.append(sibling.read_text(encoding='utf-8'))
assert any('liferay-search-global:9200' in text for text in address_sources), (
    'neither %s nor its connection config points at the shared cluster'
    % (active.name,)
)

prefix = [l for l in body.splitlines() if l.startswith('indexNamePrefix')]
assert prefix, body
value = prefix[0].split('=', 1)[1].strip().strip(chr(34))
assert value == value.lower(), (
    'indexNamePrefix %r is not lowercase; Liferay lowercases it, so a '
    'mixed-case value cannot match the indices it creates' % (value,)
)

compose = (root / 'docker-compose.yml').read_text(encoding='utf-8')
assert 'osgi/configs:/opt/liferay/osgi/configs' in compose, (
    'osgi/configs is not mounted, so the config cannot reach Liferay (#1364)'
)
"@ $sharedSearchDir
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] shared search mode produced an inconsistent project." -ForegroundColor Red
            $sharedSearchOk = $false
        }
    }

    Invoke-Cleanup $LDM_CMD "-y rm $sharedSearchName --delete"
    Remove-Item -Recurse -Force $sharedSearchWorkdir -ErrorAction SilentlyContinue

    if ($sharedSearchOk) {
        Write-Verdict "[SUCCESS] Shared search mode verified (flag honoured, mode persisted, OSGi config written and mounted)."
    } else {
        throw "Shared search mode verification failed."
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
