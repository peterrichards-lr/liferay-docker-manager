#!/usr/bin/env bash
#
# PSScriptAnalyzer pre-commit hook (LDM-#1282).
#
# This used to be an inline one-liner ending in `else exit 0; fi`, so on any host
# without `pwsh` it exited 0 in silence and pre-commit reported "Passed". It had
# never once run on the maintainer's macOS machine, yet every commit touching a
# .ps1 file showed a green tick from it -- the same shape as LDM-#1246, where
# semgrep and detect-secrets were skipped for years and two security scanners
# never ran on any agent-driven commit.
#
# A gate that cannot run must say so. Skipping is still allowed locally, because
# requiring every contributor to install PowerShell to commit a Python change
# would be worse -- but it is now loud, names what is missing, and says how to
# fix it. In CI a missing analyzer is a broken gate, not an environment quirk,
# so it fails.
set -euo pipefail

in_ci() {
  [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]
}

unavailable() {
  # $1: what is missing, $2: how to install it
  if in_ci; then
    echo "[ERROR] PSScriptAnalyzer cannot run: $1"
    echo "        This is a required quality gate in CI, not an optional check."
    echo "        Install it in the workflow: $2"
    exit 1
  fi
  echo "[SKIP] PSScriptAnalyzer did NOT run: $1"
  echo "       This check reported nothing about your .ps1 changes."
  echo "       To enable it: $2"
  exit 0
}

if ! command -v pwsh >/dev/null 2>&1; then
  unavailable "'pwsh' is not installed" \
    "brew install powershell   (a core formula; the cask was removed)"
fi

if ! pwsh -NoProfile -NonInteractive -Command \
  'if (Get-Module -ListAvailable PSScriptAnalyzer) { exit 0 } else { exit 1 }' \
  >/dev/null 2>&1; then
  unavailable "the PSScriptAnalyzer module is not installed" \
    "pwsh -NoProfile -Command 'Install-Module PSScriptAnalyzer -Scope CurrentUser -Force'"
fi

# Analyse only git-tracked PowerShell. The previous `-Path . -Recurse` walked
# the whole working tree, including `.venv/**/activate.ps1` -- a Python-generated
# activation template whose `__BIN_NAME__` placeholders are not valid PowerShell
# and never will be. Third-party and generated files are not this repo's to fix,
# and including them would make the parse check below permanently red.
PS_FILES="$(git ls-files '*.ps1' '*.psm1')"
if [ -z "$PS_FILES" ]; then
  echo "[SKIP] No tracked PowerShell files to analyse."
  exit 0
fi
export PS_FILES

# `ParseError` is included deliberately, and matters more than `Error`.
# PSScriptAnalyzer reports a file that does not even parse as severity
# `ParseError`, NOT `Error` -- so the previous filter of `Severity -eq "Error"`
# let a syntactically broken .ps1 through the gate even on a machine where the
# hook did run. Verified against a file with an unterminated block: it reports
# `ParseError/MissingEndCurlyBrace` and the old filter passed it.
#
# Findings are also counted explicitly rather than testing `$error`. The
# previous version used `if ($error)`, which inspects PowerShell's automatic
# error variable -- it accumulates every error in the session from any source,
# so it could report failure for something the analyzer never found.
# shellcheck disable=SC2016  # $findings/$f/$_ are PowerShell variables; bash
# must NOT expand them, so single quotes are required here.
pwsh -NoProfile -NonInteractive -Command '
$files = $env:PS_FILES -split "`n" | Where-Object { $_ -ne "" }
$findings = foreach ($file in $files) {
    Invoke-ScriptAnalyzer -Path $file |
        Where-Object { $_.Severity -in @("Error", "ParseError") }
}
if ($findings) {
    foreach ($f in $findings) {
        Write-Host ("[ERROR] {0}:{1} {2} -- {3}" -f `
            $f.ScriptName, $f.Line, $f.RuleName, $f.Message)
    }
    exit 1
}
exit 0'
