# Fetch and stage everything needed to run LDM's E2E verification suite.
#
# The Windows half of scripts/install_verification.sh. The two MUST stay in
# functional parity -- a Windows developer running a staging script that
# silently does less than its Unix twin gets a green result that means nothing.
#
# Two things this deliberately does NOT do:
#
#   * install the binary onto PATH. That is a machine-wide change, and a
#     verification helper making it silently is a surprise. It downloads and
#     verifies, then tells you what to run.
#   * invent an activation key. common\activation-key-*.xml is gitignored
#     because it is licensed, so no release bundle can carry one (LDM-#1733).
#     Pass -ActivationKey, or the suite verifies an unlicensed DXP and says
#     nothing about it.
#
# Usage:
#   .\install_verification.ps1 -Tag v2.22.0-pre.6 -ActivationKey C:\keys\act.xml
#   .\install_verification.ps1 -Tag v2.22.0-pre.6 -NoBinary
#
# LDM-#1529: `param` MUST be the first statement -- comments may precede it,
# code may not.
param(
    [string]$Tag = "",
    [string]$Dir = "ldm-verification",
    [string]$ActivationKey = $env:LDM_ACTIVATION_KEY,
    [switch]$NoBinary,
    [switch]$NoSelfCheck,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$Repo = "peterrichards-lr/liferay-docker-manager"

function Write-Note { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Warn { param([string]$Message) Write-Host "WARNING: $Message" -ForegroundColor Yellow }
function Stop-WithError {
    param([string]$Message)
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

if ($Help) {
    Write-Host @"
Fetch and stage the LDM E2E verification suite.

Options:
  -Tag <tag>             Release to install (e.g. v2.22.0-pre.6).
                         Defaults to the latest release.
  -Dir <path>            Where to unpack (default: .\ldm-verification).
  -ActivationKey <path>  Your DXP activation key; copied into common\.
                         May also be given as `$env:LDM_ACTIVATION_KEY.
  -NoBinary              Skip downloading the ldm binary.
  -NoSelfCheck           Skip verifying this script against the release.
  -Help                  This text.
"@
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Tag)) {
    Write-Note "Resolving the latest release..."
    try {
        $latest = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
            -Headers @{ "User-Agent" = "ldm-install-verification" }
        $Tag = $latest.tag_name
    } catch {
        Stop-WithError "could not resolve the latest release; pass -Tag. ($_)"
    }
    if ([string]::IsNullOrWhiteSpace($Tag)) {
        Stop-WithError "could not resolve the latest release; pass -Tag"
    }
    Write-Note "Using $Tag"
}

$Base = "https://github.com/$Repo/releases/download/$Tag"

# LDM-#1735: verify THIS FILE against the release it is staging, so the
# bootstrap is not the one unverified link in a chain that checksums
# everything else. A warning rather than an error on purpose: reusing one
# installer across several releases is legitimate, and what is being verified
# is the release's artifacts, not this script's vintage.
if (-not $NoSelfCheck) {
    $selfSums = Join-Path ([System.IO.Path]::GetTempPath()) "ldm-selfcheck-$PID.txt"
    try {
        Invoke-WebRequest -Uri "$Base/checksums.txt" -OutFile $selfSums -UseBasicParsing
        $entry = Get-Content $selfSums |
            Where-Object { $_ -match 'install_verification\.ps1\s*$' } |
            Select-Object -First 1
        if ($entry -and $entry -match '^\s*([0-9a-fA-F]{64})') {
            $expectedSelf = $Matches[1].ToLower()
            $actualSelf = (Get-FileHash -Algorithm SHA256 -Path $PSCommandPath).Hash.ToLower()
            if ($expectedSelf -eq $actualSelf) {
                Write-Note "Installer verified against $Tag."
            } else {
                Write-Warn "This installer does not match the one published with $Tag."
                Write-Warn "That is expected if you are reusing an older copy, and fine --"
                Write-Warn "everything it downloads below is still checksummed. Fetch the"
                Write-Warn "matching one if you would rather it were identical:"
                Write-Warn "    $Base/install_verification.ps1"
            }
        }
    } catch {
        # An older release simply has no such asset; that is not a failure.
        Write-Verbose "Self-check skipped: $_"
    } finally {
        Remove-Item $selfSums -ErrorAction SilentlyContinue
    }
}

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$TargetDir = (Resolve-Path $Dir).Path

Write-Note "Downloading the verification bundle ($Tag)..."
$zipPath = Join-Path $TargetDir "verification-bundle.zip"
try {
    Invoke-WebRequest -Uri "$Base/verification-bundle.zip" -OutFile $zipPath -UseBasicParsing
} catch {
    Stop-WithError "could not download verification-bundle.zip for $Tag -- does that release exist? ($_)"
}

Write-Note "Unpacking..."
# Into TargetDir itself, NOT a nested folder: LDM looks for common\ beside the
# script, so the layout is the point rather than a convenience.
Expand-Archive -Path $zipPath -DestinationPath $TargetDir -Force

Write-Note "Verifying checksums..."
# A truncated download is otherwise found by the suite failing strangely an
# hour later, which is a far more expensive way to learn it.
$sumsPath = Join-Path $TargetDir "SHA256SUMS"
if (-not (Test-Path $sumsPath)) {
    Stop-WithError "the bundle has no SHA256SUMS -- refusing to continue"
}
$bad = @()
foreach ($line in Get-Content $sumsPath) {
    if ($line -match '^\s*([0-9a-fA-F]{64})\s+(.+?)\s*$') {
        $expected = $Matches[1].ToLower()
        $member = Join-Path $TargetDir ($Matches[2] -replace '/', '\')
        if (-not (Test-Path $member)) { $bad += "$($Matches[2]) (missing)"; continue }
        $actual = (Get-FileHash -Algorithm SHA256 -Path $member).Hash.ToLower()
        if ($actual -ne $expected) { $bad += $Matches[2] }
    }
}
if ($bad.Count -gt 0) {
    Stop-WithError ("checksum mismatch in the bundle -- re-download rather than run it: " + ($bad -join ", "))
}

if (-not $NoBinary) {
    Write-Note "Downloading ldm-windows.exe..."
    $binPath = Join-Path $TargetDir "ldm.exe"
    try {
        Invoke-WebRequest -Uri "$Base/ldm-windows.exe" -OutFile $binPath -UseBasicParsing
    } catch {
        Stop-WithError "could not download ldm-windows.exe ($_)"
    }
    $sumFile = Join-Path $TargetDir "checksums.txt"
    try {
        Invoke-WebRequest -Uri "$Base/checksums.txt" -OutFile $sumFile -UseBasicParsing
        $entry = Get-Content $sumFile | Where-Object { $_ -match 'ldm-windows\.exe' } | Select-Object -First 1
        if ($entry -and $entry -match '^\s*([0-9a-fA-F]{64})') {
            $expected = $Matches[1].ToLower()
            $actual = (Get-FileHash -Algorithm SHA256 -Path $binPath).Hash.ToLower()
            if ($expected -ne $actual) { Stop-WithError "binary checksum mismatch for ldm-windows.exe" }
            Write-Note "Binary checksum verified."
        } else {
            Write-Warn "checksums.txt has no entry for ldm-windows.exe; the binary is UNVERIFIED"
        }
    } catch {
        Write-Warn "could not fetch checksums.txt; the binary is UNVERIFIED"
    }
}

$keyOk = $false
if (-not [string]::IsNullOrWhiteSpace($ActivationKey)) {
    if (Test-Path $ActivationKey) {
        Copy-Item -Path $ActivationKey -Destination (Join-Path $TargetDir "common") -Force
        Write-Note "Activation key copied into common\."
        $keyOk = $true
    } else {
        Stop-WithError "activation key not found: $ActivationKey"
    }
} else {
    # Not fatal -- offline staging is legitimate. But it must be loud, because
    # the failure it causes is SILENT: LDM warns, the suite passes, and it
    # verified less than it claims.
    Write-Warn "No activation key supplied."
    Write-Warn "The bundle cannot ship one (it is licensed and gitignored, LDM-#1733)."
    Write-Warn "Without it Liferay runs UNLICENSED, LDM only warns, and the suite"
    Write-Warn "still reports success having verified a smaller system than it claims."
    Write-Warn "Copy yours in before running:"
    Write-Warn "    Copy-Item C:\path\to\activation-key-*.xml $TargetDir\common\"
}

Write-Host ""
Write-Host "Ready.  $TargetDir" -ForegroundColor Green
Write-Host ""
if (-not $NoBinary) {
    Write-Host "  Put the matching binary on PATH (run it yourself):"
    Write-Host "    Move-Item $TargetDir\ldm.exe C:\Windows\System32\ldm.exe"
    Write-Host ""
}
if (-not $keyOk) {
    Write-Host "  Then copy your activation key into $TargetDir\common\"
    Write-Host ""
}
Write-Host "  Run the suite:"
Write-Host "    cd $TargetDir; .\verify_e2e_refactor.ps1"
Write-Host ""
