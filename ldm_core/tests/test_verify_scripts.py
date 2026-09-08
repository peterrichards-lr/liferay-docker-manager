import os
import re
import shutil
import subprocess
import sys
import tempfile
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

    def test_mismatched_prerelease_hint_points_at_the_release_branch(self):
        # LDM-#1049: the re-pull hint must be keyed to the *installed*
        # binary's own version, not master -- this is the exact regression
        # this test suite exists to catch.
        #
        # LDM-#1613: for a `-pre.N` install that key is the RELEASE BRANCH,
        # not the tag. Tags are immutable under the Burn Rule, so a harness
        # fix landed after the tag was cut is unreachable from the tag URL
        # forever -- following the script's own advice re-downloaded the
        # broken script. Observed for real on #1610/#1612: the PS 5.1 parse
        # fix was on release/v2.21.0 and master, but
        # `git show v2.21.0-pre.2:scripts/verify_e2e_refactor.ps1` had none
        # of it. The release branch is the only ref carrying both the fix and
        # the matching SCRIPT_VERSION stamp.
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
            'release/v2.15.27/scripts/verify_e2e_refactor.sh" '
            "-o scripts/verify_e2e_refactor.sh",
            out,
        )
        # The immutable tag must not be what the operator is sent to.
        self.assertNotIn("/v2.15.27-pre.3/scripts/", out)
        # LDM-#1047: must never fall back to a fixed 'origin/master' hint.
        self.assertNotIn("origin/master", out)
        self.assertNotIn("git checkout", out)

    def test_mismatched_stable_hint_keeps_the_immutable_tag(self):
        # LDM-#1613: a stable version's tag is immutable *by design* -- the
        # shipped script for v2.15.27 is exactly what that tag holds, and no
        # release/v2.15.27 branch is guaranteed to survive the release. Only
        # the `-pre.N` case moves to the branch.
        out = _run_bash_banner("2.15.26", "ldm 2.15.27")
        self.assertIn(
            "curl -fsSL "
            '"https://raw.githubusercontent.com/peterrichards-lr/liferay-docker-manager/'
            'v2.15.27/scripts/verify_e2e_refactor.sh" '
            "-o scripts/verify_e2e_refactor.sh",
            out,
        )
        self.assertNotIn("release/", out)

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

    def test_mismatched_prerelease_hint_points_at_the_release_branch(self):
        # LDM-#1613: parity with the bash half -- a `-pre.N` install is sent
        # to the mutable release branch, never the immutable tag. See the
        # bash test of the same name for why.
        out = _run_powershell_banner("2.15.26", "ldm 2.15.27-pre.3")
        # LDM-#1529: ERROR, not WARNING -- the run now refuses. The #1049 and
        # #1047 assertions below are unchanged and remain the point of this test.
        self.assertIn("ERROR: this script (v2.15.26)", out)
        self.assertIn("does not match the installed ldm binary (v2.15.27-pre.3)", out)
        self.assertIn("Refusing to run", out)
        self.assertIn(
            "Invoke-WebRequest -Uri "
            '"https://raw.githubusercontent.com/peterrichards-lr/liferay-docker-manager/'
            'release/v2.15.27/scripts/verify_e2e_refactor.ps1"',
            out,
        )
        self.assertNotIn("/v2.15.27-pre.3/scripts/", out)
        self.assertNotIn("origin/master", out)
        self.assertNotIn("git checkout", out)

    def test_mismatched_stable_hint_keeps_the_immutable_tag(self):
        # LDM-#1613: parity with the bash half -- a stable version keeps the
        # tag URL, which is immutable by design.
        out = _run_powershell_banner("2.15.26", "ldm 2.15.27")
        self.assertIn(
            "Invoke-WebRequest -Uri "
            '"https://raw.githubusercontent.com/peterrichards-lr/liferay-docker-manager/'
            'v2.15.27/scripts/verify_e2e_refactor.ps1"',
            out,
        )
        self.assertNotIn("release/", out)

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


class TestSharedDbAssertionCanFail(unittest.TestCase):
    """The per-project database check must be able to fail (LDM-#1552).

    It could not. `echo` appended a newline that `tr -c` turned into `_`, so
    the pattern was `sharedboot_mysql_8082_` -- with a trailing underscore the
    real name `lportal_sharedboot_mysql_8082` never matches. The `|lportal`
    alternative then always matched, because the global is created with
    MYSQL_DATABASE=lportal. So it passed against a global where the project
    database had never been created: the one thing it exists to prove.

    This runs the script's own expression rather than restating it, so a
    revert of either half is caught.
    """

    def _expected_name(self):
        """The expected-name expression, lifted from verify_shared_db_boots."""
        src = BASH_SCRIPT.read_text(encoding="utf-8")
        line = next(
            ln for ln in src.splitlines() if ln.strip().startswith('expected="lportal_')
        )
        expr = line.strip()
        script = f'proj="sharedboot-mysql-8082"\n{expr}\nprintf "%s" "$expected"\n'
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=True
        ).stdout

    def test_the_expected_name_matches_the_real_one(self):
        # utils.shared_database_name: "lportal_" + sanitized id, '-' -> '_'
        self.assertEqual(self._expected_name(), "lportal_sharedboot_mysql_8082")

    def test_no_trailing_separator_from_a_newline(self):
        self.assertFalse(
            self._expected_name().endswith("_"),
            "a trailing separator means the pattern can never match the real "
            "database name (LDM-#1552)",
        )

    def test_the_lportal_alternative_is_gone(self):
        # As an ALTERNATIVE it defeated the specific name, since every global
        # has an lportal database of its own.
        func = _extract_function(
            BASH_SCRIPT,
            re.compile(r"^verify_shared_db_boots\s*\(\)\s*\{.*?^\}", re.M | re.S),
        )
        self.assertNotIn(
            '|lportal"',
            func,
            "restoring the alternative makes the assertion unfailable again",
        )


def _run_bash_env_label(label):
    func_text = _extract_function(
        BASH_SCRIPT,
        re.compile(r"^print_env_label_line\s*\(\)\s*\{.*?^\}", re.M | re.S),
    )
    script = f'{func_text}\nprint_env_label_line "{label}"\n'
    res = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, f"bash exited {res.returncode}: {res.stderr}"
    return res.stdout


def _run_powershell_env_label(binary, label):
    func_text = _extract_function(
        PS1_SCRIPT,
        re.compile(r"^function Get-EnvLabelLine\s*\{.*?^\}", re.M | re.S),
    )
    script = (
        f"{func_text}\n"
        f"$line = Get-EnvLabelLine -EnvLabel '{label}'\n"
        "if ($line) { Write-Output $line }\n"
    )
    res = subprocess.run(
        [binary, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"{binary} exited {res.returncode}: {res.stderr}"
    return res.stdout


class TestBashEnvLabelLine(unittest.TestCase):
    """LDM-#1614: the declared environment identity has to reach the committed
    report. sync_compatibility.py derives the compatibility-matrix row from the
    report's CONTENT, so a value that exists only in the runner's environment
    would not survive -- three containerised distros collapsed onto one row
    precisely because nothing in the report distinguished them."""

    def test_a_label_becomes_a_header_line(self):
        out = _run_bash_env_label("debian")
        self.assertIn("Env Label:    debian", out)

    def test_no_label_emits_nothing(self):
        """Every existing local run has no label, and must produce a report
        byte-identical to the one it produced before."""
        self.assertEqual(_run_bash_env_label("").strip(), "")


@unittest.skipUnless(_powershell_binaries(), "no PowerShell available")
class TestPowerShellEnvLabelLine(unittest.TestCase):
    """LDM-#1614 parity: the mechanism has to exist in both halves, or a
    labelled run on one platform silently produces an unlabelled report on the
    other (the .sh/.ps1 parity rule)."""

    def test_a_label_becomes_a_header_line(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                self.assertIn(
                    "Env Label: alpine", _run_powershell_env_label(binary, "alpine")
                )

    def test_blank_and_whitespace_labels_emit_nothing(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                self.assertEqual(_run_powershell_env_label(binary, "").strip(), "")
                self.assertEqual(_run_powershell_env_label(binary, "   ").strip(), "")


# LDM-#1611: the suite's own exit status.
#
# The .ps1's top-level `catch` wrote the "-fail" report, printed
# "[FAILED] Verification FAILED (fail)", and then execution fell off the end of
# the script -- and PowerShell exits 0, because a caught `throw` is not a
# nonzero exit status. `verify-windows` therefore reported `success` on the
# v2.21.0-pre.2 and v2.21.0 tag runs while uploading a `-fail.txt` report.
#
# This is measured, not text-matched. The whole point of #1611 is that a green
# signal could not be trusted, so a guard over that root cause which passes
# whenever the source text merely survives would be the same class of mistake.
# The 2800-line suite cannot run here (Docker, a Liferay boot, a real `ldm`),
# but the exit machinery is the last thirty lines and needs none of it: the
# success assignment, the `catch`, the `finally` and the epilogue are extracted
# verbatim from the real script, and only the `try` body -- the part that needs
# Docker -- is supplied by the harness.
_EXIT_TAIL_RE = re.compile(
    r'^    Write-Host "`n\[SUCCESS\] ALL E2E VERIFICATIONS PASSED!".*\Z',
    re.M | re.S,
)

# Anything the extracted tail refers to but does not define. Finalize-Verification
# is stubbed rather than mocked away: the real one runs `ldm`, `docker` and
# `chcp`, and no test may invoke those binaries for real.
_EXIT_TAIL_PREAMBLE = (
    "$ORIGINAL_PWD = (Get-Location).Path\n"
    'function Finalize-Verification { param($ExitCode) Write-Output "finalize:$ExitCode" }\n'
    "try {\n"
)

_FAILING_BODY = "    throw 'simulated infrastructure failure'\n"
_PASSING_BODY = ""

# Drops both `$script:VerificationExitCode = N` assignments, leaving the
# epilogue's $null guard as the only thing that can set an exit status.
_ASSIGNMENT_RE = re.compile(r"^\s*\$script:VerificationExitCode = [01]\s*$\n", re.M)


def _exit_tail():
    return _extract_function(PS1_SCRIPT, _EXIT_TAIL_RE)


def _run_exit_tail(binary, body, tail=None, via_file=False):
    """Runs the real script's exit machinery over a stubbed `try` body."""
    script = _EXIT_TAIL_PREAMBLE + body + (tail if tail is not None else _exit_tail())
    if not via_file:
        return subprocess.run(
            [binary, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
        )
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "exit_tail.ps1"
        fixture.write_text(script, encoding="utf-8")
        return subprocess.run(
            [binary, "-NoProfile", "-NonInteractive", "-File", str(fixture)],
            capture_output=True,
            text=True,
            check=False,
        )


@unittest.skipUnless(_powershell_binaries(), "no PowerShell available")
class TestPowerShellSuiteExitStatus(unittest.TestCase):
    """LDM-#1611: a failed verification run must exit nonzero.

    Runs under every PowerShell on the machine, for the same reason as
    TestPowerShellJsonHelpers: #1611 was reported against both `powershell`
    (5.1) and `pwsh` (7), and only a Windows host has the former.
    """

    def test_a_failing_run_exits_nonzero(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res = _run_exit_tail(binary, _FAILING_BODY)
                self.assertIn(
                    "finalize:1",
                    res.stdout,
                    f"{name}: the failure branch did not run at all; "
                    f"stderr={res.stderr!r}",
                )
                self.assertNotEqual(
                    0,
                    res.returncode,
                    f"{name}: the suite reported SUCCESS on a failed run. This "
                    "is LDM-#1611 exactly: two release cycles of Windows "
                    f"coverage were fictional because of it. stdout={res.stdout!r}",
                )

    def test_a_passing_run_exits_zero(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res = _run_exit_tail(binary, _PASSING_BODY)
                self.assertIn("finalize:0", res.stdout, f"{name}: {res.stderr!r}")
                self.assertEqual(
                    0,
                    res.returncode,
                    f"{name}: a passing run must exit 0, or every green run "
                    f"turns red. stdout={res.stdout!r} stderr={res.stderr!r}",
                )

    def test_an_unrecorded_outcome_exits_nonzero(self):
        """The $null default: no branch recorded an outcome.

        This is the precise failure mode #1611 was, and the one a text match
        can never demonstrate. With both assignments removed, the only thing
        left that can produce an exit status is the epilogue's $null guard --
        so if that guard is dropped, this exits 0 and the test fails.
        """
        tail = _ASSIGNMENT_RE.sub("", _exit_tail())
        # Self-check on the harness, not on the fix: confirm the fixture really
        # records nothing in either branch, or this would measure the ordinary
        # failure path again. Deliberately NOT asserting that the guard is
        # present in the text -- `exit $null` exits 0 (measured), so dropping
        # the guard shows up in the exit status below, and a text check here
        # would short-circuit the behavioural assertion that proves it.
        self.assertIsNone(
            _ASSIGNMENT_RE.search(tail),
            "the fixture still records an outcome in a branch, so it is not "
            "exercising the $null default at all",
        )
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res = _run_exit_tail(binary, _FAILING_BODY, tail=tail)
                self.assertNotEqual(
                    0,
                    res.returncode,
                    f"{name}: an unrecorded outcome exited 0 -- a silent pass. "
                    "The epilogue must default it to failure (LDM-#1611). "
                    f"stdout={res.stdout!r} stderr={res.stderr!r}",
                )

    def test_the_workflow_file_invocation_propagates_the_status(self):
        """`-File` is the form .github/workflows/scheduled-verification.yml uses.

        Both invocation forms exited 0 from the unfixed script, so neither is a
        substitute for the script reporting its own status -- but the one CI
        actually runs is worth asserting directly.
        """
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res = _run_exit_tail(binary, _FAILING_BODY, via_file=True)
                self.assertNotEqual(
                    0,
                    res.returncode,
                    f"{name} -File: a failed run exited 0, so the workflow step "
                    f"would report success. stdout={res.stdout!r}",
                )


# ---------------------------------------------------------------------------
# LDM-#1621: the local-.ldmp manifest refusal check, in both scripts.
# ---------------------------------------------------------------------------


def _stub_ldm(directory, exit_code, message, *, windows=False):
    """A fake `ldm` that prints one line and exits with a chosen code.

    The assertion under test is "exit 1 AND the parse message", and that logic
    is what can silently be wrong -- an inverted comparison, a grep that never
    matches, a cleanup that runs on the wrong path. Driving it with a stub
    exercises it for real without a compiled `ldm`, without Docker and without
    the developer's home directory, none of which a unit test may touch.

    The `rm` the function issues during cleanup lands on this stub too, which
    is harmless: it prints and exits, exactly as it does for the import.
    """
    if windows:
        path = directory / "ldm.cmd"
        path.write_text(f"@echo off\r\necho {message}\r\nexit /b {exit_code}\r\n")
        return path
    path = directory / "ldm"
    path.write_text(f'#!/bin/sh\necho "{message}"\nexit {exit_code}\n')
    path.chmod(0o755)
    return path


PARSE_REFUSAL = "Invalid LDM Package: manifest 'meta' could not be parsed. Extra data"
# The real thing, observed on 2026-09-07 against a binary without LDM-#1621:
# the same package reached verify_runtime_environment and exited 1 there. A
# check that only looked at the exit code would have called that a pass.
WRONG_REASON = "FATAL: VOLUME MOUNTING IS BROKEN"


def _capturing_stub_ldm(directory, exit_code, message, capture_to):
    """A stub `ldm` that also writes out the manifest of the .ldmp it was given.

    LDM-#1629 added a `shape` argument that changes which corruption the check
    builds, and a parameter that silently did nothing would leave the new
    assertion passing against unfixed code. The only way to observe what was
    built is from inside the fake `ldm`: the function tears its fixture down
    before it returns, deliberately, so nothing is inspectable afterwards.
    """
    path = directory / "ldm"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$2" = "import" ]; then\n'
        f'  tar -xzOf "$3" meta >"{capture_to}" 2>/dev/null || true\n'
        "fi\n"
        f'echo "{message}"\n'
        f"exit {exit_code}\n"
    )
    path.chmod(0o755)
    return path


def _run_bash_ldmp_refusal(work, ldm_path, shape=None):
    func = _extract_function(
        BASH_SCRIPT,
        re.compile(r"^verify_ldmp_manifest_refusal\s*\(\)\s*\{.*?^\}", re.M | re.S),
    )
    shape_arg = f" '{shape}'" if shape else ""
    script = (
        f"{func}\n"
        f"verify_ldmp_manifest_refusal '{ldm_path}' '{work}' 'ldmp-refusal-unit'"
        f"{shape_arg}\n"
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )


class TestBashLdmpManifestRefusal(unittest.TestCase):
    """LDM-#1621: verify_e2e_refactor.sh's verify_ldmp_manifest_refusal().

    LDM-#1588 recorded that no E2E assertion could exist for package manifest
    verification, because the only input reaching it was a GitHub repo URL
    against a hardcoded API host. #1621 put the manifest controls on the import
    pipeline, so the parse refusal became assertable from a tarball the script
    builds itself -- and this is that assertion's own test.
    """

    def test_a_correct_refusal_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "workspace"
            work.mkdir()
            stub = _stub_ldm(Path(d), 1, PARSE_REFUSAL)
            res = _run_bash_ldmp_refusal(work, stub)

        self.assertEqual(res.returncode, 0, f"rejected a correct refusal: {res.stdout}")
        self.assertIn("Unparseable .ldmp manifest refused", res.stdout)

    def test_a_successful_import_is_rejected(self):
        """The regression this exists to catch: the package imports anyway."""
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "workspace"
            work.mkdir()
            stub = _stub_ldm(Path(d), 0, "Project created/imported at: /somewhere")
            res = _run_bash_ldmp_refusal(work, stub)

        self.assertEqual(res.returncode, 1, "an import that succeeded was accepted")
        self.assertIn("expected exit 1", res.stdout)

    def test_exit_one_for_another_reason_is_rejected(self):
        """Observed for real, which is why the message half is not optional."""
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "workspace"
            work.mkdir()
            stub = _stub_ldm(Path(d), 1, WRONG_REASON)
            res = _run_bash_ldmp_refusal(work, stub)

        self.assertEqual(res.returncode, 1, "exit 1 alone was treated as a pass")
        self.assertIn("not for the manifest parse", res.stdout)

    def test_the_wrong_exit_code_is_named_in_the_failure(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "workspace"
            work.mkdir()
            stub = _stub_ldm(Path(d), 3, PARSE_REFUSAL)
            res = _run_bash_ldmp_refusal(work, stub)

        self.assertEqual(res.returncode, 1)
        self.assertIn("got 3", res.stdout)

    def test_nothing_is_left_behind_on_either_outcome(self):
        """A check that leaks a fixture is a cost on every contributor."""
        for exit_code, message in ((1, PARSE_REFUSAL), (0, "imported fine")):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as d:
                work = Path(d) / "workspace"
                work.mkdir()
                stub = _stub_ldm(Path(d), exit_code, message)
                _run_bash_ldmp_refusal(work, stub)
                self.assertEqual(
                    sorted(p.name for p in work.iterdir()),
                    [],
                    "the check left its fixture behind",
                )

    def test_the_shape_argument_changes_the_manifest_that_is_built(self):
        """LDM-#1629: the two corruptions must really be two corruptions.

        The `shape` argument exists because the shapes take different branches
        of `read_meta` -- trailing junk after a closing brace is rejected by
        `json.loads`, while a saved HTTP error page has to be caught by the
        flat parser. If the argument were ignored, the new check would build
        the old fixture and assert nothing new, so this observes what the
        function actually handed to `ldm`.
        """
        expectations = (
            (None, "{"),
            ("trailing-junk", "{"),
            ("html-error-page", "<html>"),
        )
        for shape, prefix in expectations:
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as d:
                work = Path(d) / "workspace"
                work.mkdir()
                captured = Path(d) / "captured-meta"
                stub = _capturing_stub_ldm(Path(d), 1, PARSE_REFUSAL, captured)
                res = _run_bash_ldmp_refusal(work, stub, shape=shape)

                self.assertEqual(res.returncode, 0, res.stdout)
                self.assertTrue(
                    captured.exists(), "the stub never saw a manifest at all"
                )
                self.assertTrue(
                    captured.read_text(encoding="utf-8").startswith(prefix),
                    f"shape {shape!r} built the wrong manifest: "
                    f"{captured.read_text(encoding='utf-8')!r}",
                )

    def test_the_failure_message_names_the_shape(self):
        """Two runs of one check need to be distinguishable when one fails."""
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "workspace"
            work.mkdir()
            stub = _stub_ldm(Path(d), 0, "imported fine")
            res = _run_bash_ldmp_refusal(work, stub, shape="html-error-page")

        self.assertEqual(res.returncode, 1)
        self.assertIn("html-error-page", res.stdout)


@unittest.skipUnless(_powershell_binaries(), "no PowerShell available")
class TestPowerShellLdmpManifestRefusal(unittest.TestCase):
    """The .ps1 twin of the class above (LDM-#1621).

    Parity is a hard rule in this repository, and a Windows developer running a
    verification script that silently checks less than its Unix twin gets a
    green result that means nothing. Both halves are therefore driven here.
    """

    def _run(self, binary, work, ldm_path):
        func = _extract_function(
            PS1_SCRIPT,
            re.compile(r"^function Test-LdmpManifestRefusal\s*\{.*?^\}", re.M | re.S),
        )
        script = (
            f"{func}\n"
            f"$r = Test-LdmpManifestRefusal -LdmCmd '{ldm_path}' "
            f"-VenvPython '{sys.executable}' -WorkDir '{work}' "
            f"-ProjectName 'ldmp-refusal-unit'\n"
            "Write-Output ('Ok=' + $r.Ok)\n"
            "Write-Output ('Message=' + $r.Message)\n"
        )
        return subprocess.run(
            [binary, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_a_correct_refusal_is_accepted(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name), tempfile.TemporaryDirectory() as d:
                work = Path(d) / "workspace"
                work.mkdir()
                stub = _stub_ldm(Path(d), 1, PARSE_REFUSAL, windows=os.name == "nt")
                res = self._run(binary, work, stub)
                self.assertIn("Ok=True", res.stdout, f"{res.stdout}{res.stderr}")

    def test_a_successful_import_is_rejected(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name), tempfile.TemporaryDirectory() as d:
                work = Path(d) / "workspace"
                work.mkdir()
                stub = _stub_ldm(Path(d), 0, "imported fine", windows=os.name == "nt")
                res = self._run(binary, work, stub)
                self.assertIn("Ok=False", res.stdout, f"{res.stdout}{res.stderr}")
                self.assertIn("expected exit 1", res.stdout)

    def test_exit_one_for_another_reason_is_rejected(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name), tempfile.TemporaryDirectory() as d:
                work = Path(d) / "workspace"
                work.mkdir()
                stub = _stub_ldm(Path(d), 1, WRONG_REASON, windows=os.name == "nt")
                res = self._run(binary, work, stub)
                self.assertIn("Ok=False", res.stdout, f"{res.stdout}{res.stderr}")
                self.assertIn("not for the manifest parse", res.stdout)

    def test_nothing_is_left_behind(self):
        binary = _pwsh_binary()
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "workspace"
            work.mkdir()
            stub = _stub_ldm(Path(d), 1, PARSE_REFUSAL, windows=os.name == "nt")
            self._run(binary, work, stub)
            self.assertEqual(sorted(p.name for p in work.iterdir()), [])


class TestLdmpRefusalParity(unittest.TestCase):
    """LDM-#1621: the check must exist in BOTH scripts.

    A source-text parity check rather than a behavioural one, deliberately, and
    for the same reason as TestSharedDbBootParity above: the end-to-end
    behaviour needs a real `ldm`, Docker and the developer's home directory,
    none of which a unit test may touch. The behaviour of each half is covered
    by the stub-driven classes above; what this catches is the failure that has
    actually happened here before -- a check present in one script and missing
    from the other.
    """

    # The exit-code assertion and the message assertion, in each script's own
    # dialect. Neither string has any other use in either file.
    EXPECTATIONS = (
        (BASH_SCRIPT, ('[ "$code" -ne 1 ]', "manifest 'meta' could not be parsed")),
        (PS1_SCRIPT, ("$code -ne 1", "manifest 'meta' could not be parsed")),
    )

    def test_both_scripts_assert_the_code_and_the_reason(self):
        for script, needles in self.EXPECTATIONS:
            text = script.read_text(encoding="utf-8")
            for needle in needles:
                with self.subTest(script=script.name, needle=needle):
                    self.assertIn(
                        needle,
                        text,
                        f"{script.name} does not assert the .ldmp manifest "
                        f"refusal on both the exit code and the reason "
                        f"(LDM-#1621)",
                    )

    def test_both_scripts_remove_their_fixture(self):
        """A leaked .ldmp or project directory is a cost on every contributor."""
        for script in (BASH_SCRIPT, PS1_SCRIPT):
            text = script.read_text(encoding="utf-8")
            with self.subTest(script=script.name):
                self.assertIn("bad-manifest.ldmp", text)
                self.assertIn("ldmp-manifest-src", text)
