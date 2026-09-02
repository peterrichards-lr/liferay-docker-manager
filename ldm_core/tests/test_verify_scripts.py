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
    # LDM-#1309: encoding must be explicit. Without it, read_text() uses the
    # locale codec, which on Windows is cp1252 and cannot decode the UTF-8 in
    # verify_e2e_refactor.sh -- "'charmap' codec can't decode byte 0x9d". The
    # scripts are UTF-8 regardless of the host's locale.
    text = script_path.read_text(encoding="utf-8")
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


def _powershell_binaries():
    """Every PowerShell on this machine, not just the preferred one.

    LDM-#1301: `_pwsh_binary()` returns `pwsh` first, so on a Windows runner the
    banner tests exercise PowerShell 7 and never 5.1. The JSON defect in #1300
    existed *only* under 5.1 -- 7 enumerates a deserialized JSON array while 5.1
    hands it back unenumerated -- so a pwsh-only test could not have caught it.
    These tests therefore run under each shell present.
    """
    found = []
    for name in ("pwsh", "powershell"):
        path = shutil.which(name)
        if path:
            found.append((name, path))
    return found


def _run_json_helpers(binary, scenario):
    """Runs the .ps1's JSON helpers against a canned payload, in real PowerShell.

    Extracts only the two helper functions, so this costs a subprocess rather
    than a full E2E run with Docker, Liferay and a 10-minute hot-deploy wait.
    """
    parser = _extract_function(
        PS1_SCRIPT,
        re.compile(r"^function ConvertFrom-LdmJson\s*\{.*?^\}", re.M | re.S),
    )
    flattener = _extract_function(
        PS1_SCRIPT,
        re.compile(r"^function ConvertTo-LdmArray\s*\{.*?^\}", re.M | re.S),
    )
    script = f"{parser}\n{flattener}\n{scenario}\n"
    return subprocess.run(
        [binary, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )


@unittest.skipUnless(_powershell_binaries(), "no PowerShell available")
class TestPowerShellJsonHelpers(unittest.TestCase):
    """LDM-#1301: the .ps1's JSON schema checks could only be tested by running
    the whole suite -- a Liferay boot and a 10-minute hot-deploy wait -- so two
    Windows verification runs were spent finding a script bug (#1300).

    Worse, the defect was invisible to CI. Every temporary project is deleted
    before the schema check, so a clean runner has exactly ONE project;
    PowerShell unwraps a single-element array automatically, so the array path
    was never taken. It only failed on a developer machine with a second
    project registered.

    These tests exercise the multi-entry path directly, in seconds.
    """

    MULTI = (
        '$json = \'[{"project":"a","http_ready":false},'
        '{"project":"b","http_ready":true}]\'\n'
        "$parsed = ConvertFrom-LdmJson -Raw $json -Label 'list --json'\n"
        "$flat = ConvertTo-LdmArray -Value $parsed\n"
        "Write-Output ('count=' + @($flat).Count)\n"
        "foreach ($i in $flat) { Write-Output ('project=' + $i.project) }\n"
    )

    def test_multi_entry_array_is_enumerated(self):
        """The exact shape that failed on Windows PowerShell 5.1 (#1300).

        Without ConvertTo-LdmArray, 5.1 yields a one-element array *containing*
        the array, so the loop inspects the .NET array itself -- reporting
        'http_ready missing' with properties Count/Length/Rank/SyncRoot.
        """
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res = _run_json_helpers(binary, self.MULTI)
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertIn("count=2", res.stdout)
                self.assertIn("project=a", res.stdout)
                self.assertIn("project=b", res.stdout)

    def test_nested_array_is_unwrapped(self):
        """Feeds ConvertTo-LdmArray the 5.1 shape DIRECTLY, not via the parser.

        Honest limitation, measured rather than assumed: this test cannot fail on
        PowerShell 7 even with the unwrap deleted, because 7 both enumerates
        deserialized JSON arrays and unrolls arrays on function return, so the
        nested shape collapses either way. Verified by removing the unwrap and
        watching it stay green.

        It is therefore a *Windows-effective* guard: `_powershell_binaries()`
        includes `powershell` (5.1) when present, which is where the shape is
        real and where #1300 actually failed. On a pwsh-only machine it asserts
        the contract without being able to disprove it -- which is worth stating,
        since a test that cannot fail on the machine you are running it on
        proves nothing there.
        """
        scenario = (
            "$inner = @([pscustomobject]@{project='a'}, "
            "[pscustomobject]@{project='b'})\n"
            "$nested = ,$inner\n"
            "$flat = ConvertTo-LdmArray -Value $nested\n"
            "Write-Output ('count=' + @($flat).Count)\n"
            "Write-Output ('first=' + $flat[0].GetType().Name)\n"
        )
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res = _run_json_helpers(binary, scenario)
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertIn("count=2", res.stdout)
                self.assertIn("first=PSCustomObject", res.stdout)

    def test_single_entry_is_not_flattened_away(self):
        """A one-entry array must stay one entry, not become zero.

        This is the case CI has always had, and it must keep working -- the
        defensive unwrap in ConvertTo-LdmArray must not swallow it.
        """
        scenario = (
            '$json = \'[{"project":"solo","http_ready":true}]\'\n'
            "$flat = ConvertTo-LdmArray -Value "
            "(ConvertFrom-LdmJson -Raw $json -Label 'list --json')\n"
            "Write-Output ('count=' + @($flat).Count)\n"
            "Write-Output ('project=' + $flat[0].project)\n"
        )
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res = _run_json_helpers(binary, scenario)
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertIn("count=1", res.stdout)
                self.assertIn("project=solo", res.stdout)

    def test_schema_keys_are_visible_on_each_entry(self):
        """The assertion the suite actually makes, against a multi-entry payload.

        Guards the regression directly: `http_ready` must be reachable on every
        entry, which is precisely what #1300 broke.
        """
        scenario = (
            '$json = \'[{"project":"a","http_ready":false,"http_status":"x",'
            '"db_unhealthy":false},{"project":"b","http_ready":true,'
            '"http_status":"y","db_unhealthy":false}]\'\n'
            "$flat = ConvertTo-LdmArray -Value "
            "(ConvertFrom-LdmJson -Raw $json -Label 'list --json')\n"
            "foreach ($i in $flat) {\n"
            "  foreach ($k in @('http_ready','http_status','db_unhealthy')) {\n"
            "    if ($i.PSObject.Properties.Name -notcontains $k) {\n"
            "      Write-Output ('MISSING ' + $k + ' on ' + $i.project)\n"
            "    }\n"
            "  }\n"
            "}\n"
            "Write-Output 'checked'\n"
        )
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res = _run_json_helpers(binary, scenario)
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertNotIn("MISSING", res.stdout)
                self.assertIn("checked", res.stdout)

    def test_empty_output_fails_with_a_clear_message(self):
        scenario = (
            "try { ConvertFrom-LdmJson -Raw @() -Label 'list --json' } "
            "catch { Write-Output ('caught: ' + $_.Exception.Message) }\n"
        )
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res = _run_json_helpers(binary, scenario)
                self.assertIn("produced no output to parse", res.stdout)


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
        # LDM-#1529: this is now an ERROR, not a WARNING -- the run refuses
        # rather than producing a report that claims to verify one version
        # while exercising another. The #1049 and #1047 assertions below are
        # unchanged and remain the point of this test.
        self.assertIn("ERROR: this script (v2.15.26)", out)
        self.assertIn("does not match the installed ldm binary (v2.15.27-pre.3)", out)
        self.assertIn("Refusing to run", out)
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
        # LDM-#1529: ERROR, not WARNING -- the run now refuses. The #1049 and
        # #1047 assertions below are unchanged and remain the point of this test.
        self.assertIn("ERROR: this script (v2.15.26)", out)
        self.assertIn("does not match the installed ldm binary (v2.15.27-pre.3)", out)
        self.assertIn("Refusing to run", out)
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


class TestSharedDbBootParity(unittest.TestCase):
    """LDM-#1546: the shared-database BOOT check lived only in the bash script.

    Both scripts asserted the generated CONFIG for `--database-mode shared`,
    but only `verify_e2e_refactor.sh` ever STARTED one, so on Windows the
    headline feature of 2.19 was verified exactly as it was before LDM-#1494 --
    which is to say, not verified at all.

    A source-text parity check rather than a behavioural one, deliberately: the
    behaviour needs Docker, an `ldm` binary and minutes of runtime, and cannot
    run in unit tests. What it CAN catch is the failure that actually happened,
    which was a check existing in one script and not the other.
    """

    # The in-container database listings. Each is the assertion the config-level
    # checks cannot make -- that CREATE DATABASE ran inside the engine's global
    # container -- so their presence is a reasonable proxy for "this script
    # boots a shared stack", and neither string has any other use.
    LIST_COMMANDS = (
        ("MySQL", "SHOW DATABASES;"),
        ("PostgreSQL", "SELECT datname FROM pg_database;"),
    )

    def test_both_scripts_check_inside_both_global_containers(self):
        for script in (BASH_SCRIPT, PS1_SCRIPT):
            text = script.read_text(encoding="utf-8")
            for label, list_command in self.LIST_COMMANDS:
                with self.subTest(script=script.name, engine=label):
                    self.assertIn(
                        list_command,
                        text,
                        f"{script.name} never lists the databases inside the "
                        f"global {label} container, so it cannot prove a shared "
                        f"{label} stack booted (LDM-#1546)",
                    )

    def test_the_powershell_boot_port_is_arithmetic_not_concatenation(self):
        """LDM-#1546: $TEST_PORT is a string, and PowerShell `+` concatenates.

        The bash computes the boot port with `$((TEST_PORT + 3))`. Translated
        literally, `$TEST_PORT + 3` yields "80823" -- a string, and not a port
        number -- so the cast is load-bearing and easy to drop in a later edit.
        """
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("[int]$TEST_PORT + 3", text)
        self.assertIn("[int]$TEST_PORT + 4", text)


if __name__ == "__main__":
    unittest.main()
