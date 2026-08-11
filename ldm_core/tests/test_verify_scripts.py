import re
import shutil
import subprocess
import unittest
from pathlib import Path

# LDM-#1058: verify_e2e_refactor.sh/.ps1's version-banner logic has had 3
# real bugs this cycle (#1047, #1049, #1058) with zero test coverage, since
# the scripts are monolithic E2E orchestrators that need real Docker/ldm to
# run wholesale. Rather than mocking that away, the banner logic was
# extracted into a named function *within each single-file script* (see the
# LDM-#1058 comments there for why it isn't split into a separate sourced
# file) -- these tests extract just that function's source text and execute
# it for real, in an isolated bash/pwsh subprocess, with no Docker/ldm
# involved at all.

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
BASH_SCRIPT = SCRIPTS_DIR / "verify_e2e_refactor.sh"
PS1_SCRIPT = SCRIPTS_DIR / "verify_e2e_refactor.ps1"


def _extract_function(script_path, pattern):
    text = script_path.read_text()
    match = pattern.search(text)
    if not match:
        raise AssertionError(
            f"Could not extract function from {script_path} using {pattern.pattern!r}"
        )
    return match.group(0)


def _run_bash_banner(script_version, installed_version_raw):
    func_text = _extract_function(
        BASH_SCRIPT,
        re.compile(r"^print_version_banner\s*\(\)\s*\{.*?^\}", re.M | re.S),
    )
    script = (
        f"{func_text}\n"
        f'SCRIPT_VERSION="{script_version}"\n'
        f'print_version_banner "{installed_version_raw}"\n'
    )
    res = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, f"bash exited {res.returncode}: {res.stderr}"
    return res.stdout


def _pwsh_binary():
    return shutil.which("pwsh") or shutil.which("powershell")


def _run_powershell_banner(script_version, ldm_ver):
    func_text = _extract_function(
        PS1_SCRIPT,
        re.compile(r"^function Get-VersionBannerLines\s*\{.*?^\}", re.M | re.S),
    )
    script = (
        f"{func_text}\n"
        f"(Get-VersionBannerLines -ScriptVersion '{script_version}' "
        f"-LdmVer '{ldm_ver}') | ForEach-Object {{ Write-Output $_ }}\n"
    )
    binary = _pwsh_binary()
    res = subprocess.run(
        [binary, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"{binary} exited {res.returncode}: {res.stderr}"
    return res.stdout


class TestBashVersionBanner(unittest.TestCase):
    """LDM-#1058: scripts/verify_e2e_refactor.sh's print_version_banner()."""

    def test_matching_version_no_warning(self):
        out = _run_bash_banner("2.15.27-pre.3", "ldm 2.15.27-pre.3")
        self.assertIn("Version:      ldm 2.15.27-pre.3", out)
        self.assertIn("Script Ver:   2.15.27-pre.3", out)
        self.assertNotIn("WARNING", out)

    def test_mismatched_version_warns_with_matching_tag_url(self):
        # LDM-#1049: the re-pull hint must be keyed to the *installed*
        # binary's own version, not master -- this is the exact regression
        # this test suite exists to catch.
        out = _run_bash_banner("2.15.26", "ldm 2.15.27-pre.3")
        self.assertIn("WARNING: this script (v2.15.26)", out)
        self.assertIn("does not match the installed ldm binary (v2.15.27-pre.3)", out)
        self.assertIn(
            "curl -fsSL "
            '"https://raw.githubusercontent.com/peterrichards-lr/liferay-docker-manager/'
            'v2.15.27-pre.3/scripts/verify_e2e_refactor.sh" '
            "-o scripts/verify_e2e_refactor.sh",
            out,
        )
        # LDM-#1047: must never fall back to a fixed 'origin/master' hint.
        self.assertNotIn("origin/master", out)
        self.assertNotIn("git checkout", out)

    def test_stable_release_matching_version_no_warning(self):
        out = _run_bash_banner("2.15.26", "ldm 2.15.26")
        self.assertNotIn("WARNING", out)

    def test_unparseable_installed_version_no_warning(self):
        # If the binary can't be queried at all (e.g. not installed), there's
        # nothing meaningful to warn about -- the installed_version regex
        # simply won't match anything.
        out = _run_bash_banner("2.15.27-pre.3", "unknown")
        self.assertIn("Version:      unknown", out)
        self.assertNotIn("WARNING", out)


@unittest.skipUnless(_pwsh_binary(), "pwsh/powershell not available on this machine")
class TestPowerShellVersionBanner(unittest.TestCase):
    """LDM-#1058: scripts/verify_e2e_refactor.ps1's Get-VersionBannerLines.

    Skipped when neither `pwsh` nor `powershell` is on PATH (e.g. local
    macOS/Linux dev machines without PowerShell installed) -- runs for real
    in CI, since GitHub-hosted ubuntu-latest/macos-latest/windows-latest
    runners all ship pwsh, matching the existing PSScriptAnalyzer pre-commit
    hook's same skip-if-absent convention.
    """

    def test_matching_version_no_warning(self):
        out = _run_powershell_banner("2.15.27-pre.3", "ldm 2.15.27-pre.3")
        self.assertIn("Version:   ldm 2.15.27-pre.3", out)
        self.assertIn("Script Ver: 2.15.27-pre.3", out)
        self.assertNotIn("WARNING", out)

    def test_mismatched_version_warns_with_matching_tag_url(self):
        out = _run_powershell_banner("2.15.26", "ldm 2.15.27-pre.3")
        self.assertIn("WARNING: this script (v2.15.26)", out)
        self.assertIn("does not match the installed ldm binary (v2.15.27-pre.3)", out)
        self.assertIn(
            "Invoke-WebRequest -Uri "
            '"https://raw.githubusercontent.com/peterrichards-lr/liferay-docker-manager/'
            'v2.15.27-pre.3/scripts/verify_e2e_refactor.ps1"',
            out,
        )
        self.assertNotIn("origin/master", out)
        self.assertNotIn("git checkout", out)

    def test_unparseable_ldm_version_no_warning(self):
        out = _run_powershell_banner("2.15.27-pre.3", "Unknown")
        self.assertNotIn("WARNING", out)


if __name__ == "__main__":
    unittest.main()
