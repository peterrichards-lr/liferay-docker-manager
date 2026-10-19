import os
import re
import shutil
import socket
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


# LDM-#2115: top-level PowerShell helpers that extracted functions may call.
# Listed explicitly rather than discovered, so a rename shows up as a test
# failure rather than a silently missing dependency.
_PS_HELPERS = ("ConvertTo-LdmText", "ConvertFrom-LdmJson", "ConvertTo-LdmArray")


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
    body = match.group(0)

    # LDM-#2115: an extracted PowerShell function runs standalone, so any
    # top-level helper it calls has to come with it. Without this the
    # extraction fails at RUNTIME with "The term 'ConvertTo-LdmText' is not
    # recognized", which reads like a script defect and is not one.
    #
    # Resolved here rather than at each of the sixteen call sites, so adding
    # a helper later does not silently break a harness that does not know to
    # ask for it.
    if script_path == PS1_SCRIPT:
        for helper in _PS_HELPERS:
            if helper in body and f"function {helper}" not in body:
                found = re.search(
                    rf"^function {re.escape(helper)} \{{.*?^\}}", text, re.M | re.S
                )
                if found:
                    body = found.group(0) + "\n\n" + body
    return body


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


def _run_bash_run_context(context, env=None):
    func_text = _extract_function(
        BASH_SCRIPT,
        re.compile(r"^print_run_context_line\s*\(\)\s*\{.*?^\}", re.M | re.S),
    )
    script = f'{func_text}\nprint_run_context_line "{context}"\n'
    res = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert res.returncode == 0, f"bash exited {res.returncode}: {res.stderr}"
    return res.stdout


def _run_powershell_run_context(binary, context, in_ci=False):
    func_text = _extract_function(
        PS1_SCRIPT,
        re.compile(r"^function Get-RunContextLine\s*\{.*?^\}", re.M | re.S),
    )
    script = (
        f"{func_text}\n"
        f"$env:GITHUB_ACTIONS = '{'true' if in_ci else ''}'\n"
        f"Write-Output (Get-RunContextLine -RunContext '{context}')\n"
    )
    res = subprocess.run(
        [binary, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"{binary} exited {res.returncode}: {res.stderr}"
    return res.stdout


class TestBashRunContextLine(unittest.TestCase):
    """LDM-#1909: WHO ran the verification has to reach the committed report.

    sync_compatibility.py derives the matrix row from the report's CONTENT, so
    a CI container and a workstation that report the same distro collapse onto
    one row unless the report itself says which it was. A Fedora 44 workstation
    row at Docker 29.8.1 was destroyed that way.

    Executed rather than text-matched: the default is chosen by a branch on
    GITHUB_ACTIONS, and reading the branch is not evidence it takes.
    """

    def _env(self, **overrides):
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
        env.update(overrides)
        return env

    def test_an_explicit_context_wins(self):
        out = _run_bash_run_context("workstation", env=self._env())
        self.assertIn("Run Context:", out)
        self.assertIn("workstation", out)

    def test_github_actions_defaults_to_ci(self):
        out = _run_bash_run_context("", env=self._env(GITHUB_ACTIONS="true"))
        self.assertRegex(out, r"^Run Context:[ \t]*ci\s*$")

    def test_anywhere_else_defaults_to_workstation(self):
        """The load-bearing default. An unmarked report is treated as a
        superseded CI one by the publish step's transitional cleanup, so a
        local run that failed to declare itself would be deleted."""
        out = _run_bash_run_context("", env=self._env())
        self.assertRegex(out, r"^Run Context:[ \t]*workstation\s*$")

    def test_the_line_matches_what_sync_compatibility_parses(self):
        """The two halves are in different languages and different files; the
        only thing joining them is this regex."""
        pattern = re.compile(r"^Run Context:[ \t]*([^\n]*)", re.M)
        for env, expected in (
            (self._env(GITHUB_ACTIONS="true"), "ci"),
            (self._env(), "workstation"),
        ):
            out = _run_bash_run_context("", env=env)
            match = pattern.search(out)
            self.assertIsNotNone(match, f"sync_compatibility cannot parse {out!r}")
            assert match is not None  # for mypy
            self.assertEqual(match.group(1).strip(), expected)


@unittest.skipUnless(_powershell_binaries(), "no PowerShell available")
class TestPowerShellRunContextLine(unittest.TestCase):
    """LDM-#1909 parity: without this half a Windows verification produces an
    unmarked report, which the publish step's transitional cleanup deletes."""

    def test_an_explicit_context_wins(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                out = _run_powershell_run_context(binary, "workstation", in_ci=True)
                self.assertIn("workstation", out)

    def test_github_actions_defaults_to_ci(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                out = _run_powershell_run_context(binary, "", in_ci=True)
                self.assertRegex(out, r"^Run Context:[ \t]*ci\s*$")

    def test_anywhere_else_defaults_to_workstation(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                out = _run_powershell_run_context(binary, "", in_ci=False)
                self.assertRegex(out, r"^Run Context:[ \t]*workstation\s*$")


@unittest.skipUnless(_powershell_binaries(), "no PowerShell available")
class TestPowerShellSlugOsSuffix(unittest.TestCase):
    """LDM-#1907: PowerShell 5.1 and 7 on one host are different environments
    and produced the same slug, so whichever ran second replaced the other.

    The edition is knowable only to the shell -- the CLI would have to guess at
    its parent process -- so the script supplies it. Both editions are asserted
    from ONE shell deliberately: the mapping must be a function of the argument,
    not of whichever interpreter happens to be running the test.
    """

    def _suffix(self, binary, edition):
        func_text = _extract_function(
            PS1_SCRIPT,
            re.compile(r"^function Get-SlugOsSuffix\s*\{.*?^\}", re.M | re.S),
        )
        script = f"{func_text}\nWrite-Output (Get-SlugOsSuffix -Edition '{edition}')\n"
        res = subprocess.run(
            [binary, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"{binary} exited {res.returncode}: {res.stderr}"
        return res.stdout.strip()

    def test_the_two_editions_do_not_share_a_suffix(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                desktop = self._suffix(binary, "Desktop")
                core = self._suffix(binary, "Core")
                self.assertNotEqual(
                    desktop,
                    core,
                    "PowerShell 5.1 and 7 map to the same slug again, so one "
                    "host's two shells share a matrix row and the second run "
                    "replaces the first (LDM-#1907).",
                )
                self.assertEqual(desktop, "powershell-5.1")
                self.assertEqual(core, "powershell-7")


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
# LDM-#1975: the tail now opens with the section-aware banner rather than the
# bare "ALL E2E VERIFICATIONS PASSED!" line, because a slice must not claim to
# have run everything. The anchor moved with it; what this class asserts -- that
# the epilogue propagates an exit status -- is unchanged, and the extracted tail
# still runs from the banner to the end of the file.
_EXIT_TAIL_RE = re.compile(
    r'^    if \(\$LDM_E2E_SECTIONS -eq "all"\) \{$.*\Z',
    re.M | re.S,
)

# Anything the extracted tail refers to but does not define. Finalize-Verification
# is stubbed rather than mocked away: the real one runs `ldm`, `docker` and
# `chcp`, and no test may invoke those binaries for real.
_EXIT_TAIL_PREAMBLE = (
    "$ORIGINAL_PWD = (Get-Location).Path\n"
    # LDM-#1975: the tail reads this to decide which banner to print. A full run
    # is the case being modelled here -- the exit machinery is identical either
    # way, and leaving it undefined would exercise a state the script cannot be
    # in, since it defaults the variable before anything else runs.
    '$LDM_E2E_SECTIONS = "all"\n'
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

    The stub is deliberately NOT named ``ldm`` (LDM-#1899). The verification
    scripts take the binary as a path argument -- ``local ldm_cmd="$1"`` -- and
    never look it up on ``PATH``, so the basename is free. A file named ``ldm``
    presents to an endpoint-protection agent as that binary launching from a
    temp directory, which is the signature it acts on; LDM-#1898 cost two
    developer sessions to the same shape with ``lfr-tunnel``.

    ``conftest``'s guard cannot cover this one. It intercepts spawns issued
    from Python, and these tests run ``subprocess.run(["bash", "-c", script])``
    -- the argv crossing that seam is ``bash``, and the shell execs the stub
    out of the guard's sight. The name is the only control that reaches here.
    """
    if windows:
        path = directory / "ldm-stub.cmd"
        path.write_text(f"@echo off\r\necho {message}\r\nexit /b {exit_code}\r\n")
        return path
    path = directory / "ldm-stub"
    path.write_text(f'#!/bin/sh\necho "{message}"\nexit {exit_code}\n')
    # lint: executable-stub -- bash must exec it to drive the verify function; named ldm-stub, which no agent watches (LDM-#1899)
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
    path = directory / "ldm-stub"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$2" = "import" ]; then\n'
        f'  tar -xzOf "$3" meta >"{capture_to}" 2>/dev/null || true\n'
        "fi\n"
        f'echo "{message}"\n'
        f"exit {exit_code}\n"
    )
    # lint: executable-stub -- bash must exec it to drive the verify function; named ldm-stub, which no agent watches (LDM-#1899)
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


class TestPowerShellPlatformHeader(unittest.TestCase):
    """LDM-#1639: the .ps1 read its platform from `$PSVersionTable.OS`.

    That property arrived in PowerShell 6, so on Windows PowerShell 5.1 it does
    not exist and the header rendered as a bare `Platform:` with nothing after
    it -- which is what the committed
    `verify-windows-pc-windows-11-powershell-5.1-docker-desktop-pass.txt` has.
    The `.sh` half always emits a value (from `$OSTYPE`, refined by
    `PRETTY_NAME` on Linux), so a Windows report carried strictly less than a
    Unix one.

    Until LDM-#1633 the empty field was worse than missing: `sync_compatibility`
    captured across the newline and recorded the FOLLOWING line as the platform.
    """

    PLATFORM_BLOCK = re.compile(
        r"^\s*\$platformInfo = \$PSVersionTable\.OS.*?"
        r'^\s*Write-Output "Platform:.*?$',
        re.M | re.S,
    )

    def test_the_platform_line_is_never_empty_in_any_powershell_present(self):
        """Runs the shipped block, under every PowerShell on this machine.

        This is the one place the 5.1 branch can be exercised honestly. The
        block cannot be driven with a synthetic `$PSVersionTable` -- PowerShell
        refuses to shadow it in any scope, it is a read-only variable -- but on
        a real 5.1 host the genuine table simply has no `OS` key, so running the
        block unmodified there takes the fallback for real. On a pwsh 7 host the
        same run exercises the pass-through branch instead. Between a Windows
        CI runner and a developer machine, both branches get covered.
        """
        binaries = _powershell_binaries()
        if not binaries:
            self.skipTest("no PowerShell available on this machine")

        block = _extract_function(PS1_SCRIPT, self.PLATFORM_BLOCK)

        for name, binary in binaries:
            with self.subTest(shell=name):
                res = subprocess.run(
                    [binary, "-NoProfile", "-NonInteractive", "-Command", block],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    res.returncode, 0, f"{name} exited {res.returncode}: {res.stderr}"
                )
                line = res.stdout.strip()
                self.assertTrue(
                    line.startswith("Platform:"),
                    f"{name} emitted {line!r} rather than a Platform: line",
                )
                value = line[len("Platform:") :].strip()
                self.assertTrue(
                    value,
                    f"{name} emitted an empty Platform: line -- the header field "
                    "that LDM-#1639 exists to populate (LDM-#1639)",
                )

    # The fallback must be reached ONLY when the PowerShell 6+ property is
    # absent or blank, so this asserts the shape of the whole block rather than
    # the relative offsets of two substrings. Comparing offsets passed against
    # an *unconditional* fallback -- the one regression the test exists to
    # catch -- because a straight-line pair of assignments is still "primary
    # before fallback". It also read the fallback's offset with `str.index`
    # over the entire file, which is unique only by luck: the explanatory
    # comment above the block names `OSVersion.VersionString` unqualified, and
    # writing it fully-qualified there (the natural edit) would have matched
    # the comment instead and failed a correct script.
    GUARDED_FALLBACK = re.compile(
        r"\$platformInfo = \$PSVersionTable\.OS[ \t]*\n"
        r"[ \t]*if \(\[string\]::IsNullOrWhiteSpace\(\$platformInfo\)\) \{[ \t]*\n"
        r"[ \t]*\$platformInfo = \[System\.Environment\]::OSVersion\.VersionString[ \t]*\n"
        r"[ \t]*\}",
        re.M,
    )

    def test_the_powershell_6_property_is_still_preferred(self):
        """The fallback must not displace the value PowerShell 7 already gives.

        Always using `OSVersion.VersionString` would change the PowerShell 7
        row's platform string for no reason, and that row is published. On this
        macOS host the two differ -- `$PSVersionTable.OS` gives `macOS 26.6.2`
        where `OSVersion.VersionString` gives `Unix 26.6.2` -- so the ordering
        is load-bearing, not cosmetic.
        """
        self.assertRegex(
            _extract_function(PS1_SCRIPT, self.PLATFORM_BLOCK),
            self.GUARDED_FALLBACK,
            "the fallback must sit inside an IsNullOrWhiteSpace guard AFTER the "
            "$PSVersionTable.OS read, not replace it",
        )

    def test_both_halves_emit_a_platform_line(self):
        """Parity, at the level the failure actually took: one half, not both."""
        self.assertIn(
            'Write-Output "Platform:  $platformInfo"',
            PS1_SCRIPT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            'echo "Platform:     $PLATFORM_INFO"',
            BASH_SCRIPT.read_text(encoding="utf-8"),
        )


class TestTheClientExtensionFixtureCanActuallyBeShadowed(unittest.TestCase):
    """LDM-#1911: the runtime check is only worth having if it can fail.

    The suite builds a synthetic client-extension SERVICE and, since #1911,
    runs it to confirm the extension's own code at /opt/liferay/routes is
    still visible inside the container. That check is vacuous unless the
    fixture image actually PUTS something there -- with the old `FROM alpine`
    fixture the directory does not exist, nothing can be shadowed, and the
    check passes against the very mount that broke a live container.

    Same shape as LDM-#1529, where three tests passed against a deliberately
    neutered gate. Asserting on script text is normally weak, but here the
    fixture's content IS the subject rather than a proxy for it.
    """

    @staticmethod
    def _text(path):
        return path.read_text(encoding="utf-8")

    def _fixture_region(self, path):
        text = self._text(path)
        marker = "The Dockerfile is what makes it a service"
        self.assertIn(marker, text, f"CX service fixture is gone from {path.name}")
        start = text.index(marker)
        return text[start : start + 900]

    def test_the_bash_fixture_declares_application_code(self):
        self.assertIn(
            "/opt/liferay/routes/app.cjs",
            self._fixture_region(BASH_SCRIPT),
            "The CX fixture image no longer creates application code at "
            "/opt/liferay/routes, so the runtime shadowing check cannot fail "
            "and protects nothing (LDM-#1911).",
        )

    def test_the_powershell_fixture_declares_application_code(self):
        self.assertIn(
            "/opt/liferay/routes/app.cjs",
            self._fixture_region(PS1_SCRIPT),
            "Parity: the Windows half's CX fixture has no application code, so "
            "the runtime shadowing check is vacuous there (LDM-#1911).",
        )

    def test_both_halves_actually_look_inside_the_container(self):
        """A compose-level assertion cannot see a runtime shadowing."""
        for name, path in (("bash", BASH_SCRIPT), ("powershell", PS1_SCRIPT)):
            with self.subTest(half=name):
                self.assertIn(
                    "test -f /opt/liferay/routes/app.cjs",
                    self._text(path),
                    f"The {name} half no longer runs the extension container "
                    "to check its own code survived LDM's mounts. Every "
                    "remaining CX assertion reads the generated compose file, "
                    "which is exactly what #1919 satisfied while breaking a "
                    "real container (LDM-#1911).",
                )


def _multiline_native_args(text):
    """Native-command arguments whose literal payload spans lines.

    A `-c` (or `-Command`) followed by a quote that is the last thing on its
    line opens a literal argument that continues onto the following lines.
    Returns `(line_number, opening_line)` for each.
    """
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        if re.search(r"-(?:c|Command)\s+['\"][ \t]*$", line):
            found.append((number, line.strip()))
    return found


class TestNoMultiLineNativeCommandArguments(unittest.TestCase):
    """LDM-#2026: the defect that failed a verification run at the last mile.

    `verify_e2e_refactor.ps1` handed `docker` a multi-line `sh -c '...'`
    payload containing double quotes. Windows PowerShell 5.1 does not escape
    those quotes when building the command line for a native process, so
    docker.exe's own argument parser re-split the script at them; `sh` was
    handed a truncated `-c` payload and the trailing words became positional
    parameters:

        for: line 6: syntax error: unexpected end of file (expecting "}")

    pwsh 7 passes the argument intact, and the only CI job that runs Windows
    PowerShell 5.1 exercises this file's *helper functions* rather than the
    file, so nothing in the pipeline could observe it. This static guard is
    what stands in for the end-to-end 5.1 run that CI does not have.

    The fix is to build the payload in a variable and hand the container
    something inert -- base64 -- rather than to quote more carefully. Quoting
    correctly across 5.1, pwsh 7 and docker.exe's parser is not a thing this
    repository should be trying to get right twice.

    Only the `.ps1` is scanned. bash receives a multi-line single-quoted
    argument as one argument, so the same construct in `verify_e2e_refactor.sh`
    is correct and is left alone.
    """

    def test_the_powershell_script_has_no_multi_line_native_argument(self):
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        offenders = _multiline_native_args(text)
        self.assertEqual(
            offenders,
            [],
            "a native-command argument spans lines, which Windows PowerShell "
            "5.1 mangles (LDM-#2026). Put the payload in a here-string, "
            "base64-encode it, and pass a single inert token:\n  "
            + "\n  ".join(f"line {n}: {line}" for n, line in offenders),
        )

    def test_the_guard_catches_the_construct_it_exists_for(self):
        """The guard, proven against the exact text that failed.

        Without this, a guard that matches nothing passes forever and says
        nothing about the script -- which is how the original shipped green.
        """
        offenders = _multiline_native_args(
            '    $x = & docker run --rm -v "${r}:/w" alpine sh -c \'\n'
            "probe=/w/.p\n"
            '[ "$back" != "640" ] && { echo "NOHONOUR $back"; exit 0; }\n'
            "' 2>&1 | Out-String\n"
        )
        self.assertEqual(len(offenders), 1, offenders)
        self.assertEqual(offenders[0][0], 1)

    def test_the_guard_does_not_flag_the_fixed_form(self):
        offenders = _multiline_native_args(
            "    $b64 = [Convert]::ToBase64String("
            "[Text.Encoding]::UTF8.GetBytes($probe))\n"
            "    $x = & docker run --rm alpine sh -c "
            '"echo $b64 | base64 -d | sh" 2>&1 | Out-String\n'
        )
        self.assertEqual(offenders, [])


# Verb-Noun cmdlets the script calls that exist only on Windows, so they do not
# resolve when this runs on Linux or macOS. Externals (python, docker, chcp.com)
# are not Verb-Noun shaped and are skipped by the check itself.
_WINDOWS_ONLY_CMDLETS = frozenset({"Get-NetTCPConnection"})

_UNDEFINED_COMMAND_PROBE = r"""
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$null, [ref]$null)
$defined = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) |
    ForEach-Object { $_.Name }
$ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object {
    $name = $_.GetCommandName()
    # Verb-Noun only: anything else is an external program, whose presence is
    # the host's business rather than this script's.
    if ($name -and $name -match '^[A-Za-z]+-[A-Za-z]' -and $name -notmatch '\.') {
        if ($defined -notcontains $name -and -not (Get-Command $name -ErrorAction SilentlyContinue)) {
            "{0}:{1}" -f $name, $_.Extent.StartLineNumber
        }
    }
}
"""


def _undefined_commands(binary, script_path):
    """Verb-Noun commands the script calls that are neither defined in it nor
    resolvable as a cmdlet. Returns a sorted list of "Name:line" strings."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(_UNDEFINED_COMMAND_PROBE)
        probe = fh.name
    try:
        res = subprocess.run(
            [binary, "-NoProfile", "-File", probe, str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.unlink(probe)
    if res.returncode != 0:
        raise AssertionError(f"probe failed: {res.stderr.strip()}")
    out = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.split(":", 1)[0] in _WINDOWS_ONLY_CMDLETS:
            continue
        out.append(line)
    return sorted(out)


@unittest.skipUnless(_powershell_binaries(), "no PowerShell available")
class TestEveryCommandTheScriptCallsExists(unittest.TestCase):
    """LDM-#2028: `Invoke-LoggedCommand` was called twice and defined nowhere.

    The real helper is `Log-AndRun`, and it takes its arguments as a
    space-separated **string** (it does `$args_list.Split(' ')`), not as an
    array -- so the two bad call sites had the wrong name *and* the wrong
    argument shape.

    Both were introduced during the v2.26.0 cycle (#1919 and #1953) and neither
    had ever executed: every Windows-native run died earlier, at the LDM-#2026
    quoting defect on line 2783. Fixing that unmasked this, which is the
    general hazard -- a suite that aborts halfway hides every defect after the
    abort, and each fix reveals the next one at the cost of a burnt
    pre-release number.

    So this checks the whole file at once rather than the line that happened to
    fail. PowerShell resolves an undefined function only when the line runs,
    which is why neither CI nor PSScriptAnalyzer objected: the parse is valid
    and the name is a perfectly good command reference until it isn't.

    Verb-Noun names only. `python`, `docker` and `chcp.com` are external
    programs whose presence is the host's business, and the one Windows-only
    cmdlet the script uses is allowlisted above so this stays portable.
    """

    def test_no_command_is_called_that_nothing_defines(self):
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                offenders = _undefined_commands(binary, PS1_SCRIPT)
                self.assertEqual(
                    offenders,
                    [],
                    "these are called but neither defined in the script nor "
                    "resolvable as a cmdlet, so the line throws the moment it "
                    f"is reached (LDM-#2028): {offenders}",
                )

    def test_the_guard_catches_an_undefined_call(self):
        """Neuter probe: without this, a guard that matches nothing passes
        forever and says nothing about the script."""
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake.ps1"
            fake.write_text(
                "function Log-AndRun { param($m) }\n"
                'Log-AndRun "fine"\n'
                'Invoke-LoggedCommand "boom" $x @("-y")\n',
                encoding="utf-8",
            )
            name, binary = _powershell_binaries()[0]
            offenders = _undefined_commands(binary, fake)
        self.assertEqual(len(offenders), 1, offenders)
        self.assertTrue(offenders[0].startswith("Invoke-LoggedCommand:"), offenders)


def _case_insensitive_success_assertions(text):
    """`-match` lines whose success branch declares the behaviour CORRECT.

    Polarity decides which operator is right, and it is derivable rather than
    a list someone maintains:

    * a match that leads to `[SUCCESS]` is a POSITIVE assertion -- narrow is
      stricter, so it must be `-cmatch`. Case-insensitive here passes on
      output LDM never produced.
    * a match that leads to `[ERROR]`/`throw` is a failure detector or a
      regression guard -- BROAD is safer, so `-match` is correct and must be
      left alone. Narrowing those would miss failures, which is the opposite
      of the point.

    Returns `(line_number, text)` for each positive assertion still using the
    case-insensitive operator.
    """
    out = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"\s*#", line) or " -match " not in line:
            continue
        follow = " ".join(lines[i + 1 : i + 3])
        if "[SUCCESS]" in follow and "[ERROR]" not in follow and "throw" not in follow:
            out.append((i + 1, line.strip()[:100]))
    return out


class TestPositiveAssertionsAreCaseSensitive(unittest.TestCase):
    """LDM-#2031: PowerShell's `-match` is case-insensitive by default.

    The `.sh` half greps case-sensitively almost everywhere (111 plain `grep -q`
    against 6 `grep -qi`), so a `-match` in the `.ps1` asserts strictly less
    than its `.sh` counterpart. That difference never fails a run -- it passes
    output LDM never produced -- which is exactly why it survives review.

    Only POSITIVE assertions are required to be case-sensitive. Failure
    detectors and regression guards are deliberately left broad; narrowing
    `if ($out -match "the pre-flight check will select port")` would make it
    miss the very wording it exists to catch.
    """

    def test_no_success_assertion_matches_case_insensitively(self):
        offenders = _case_insensitive_success_assertions(
            PS1_SCRIPT.read_text(encoding="utf-8")
        )
        self.assertEqual(
            offenders,
            [],
            "these assert success with the case-INSENSITIVE operator, so they "
            "pass on output LDM never produced. Use -cmatch (LDM-#2031):\n  "
            + "\n  ".join(f"line {n}: {t}" for n, t in offenders),
        )

    def test_the_guard_distinguishes_polarity(self):
        """Neuter probe, and a check that it does not over-reach."""
        positive = 'if ($out -match "Thing") {\n    Write-Verdict "[SUCCESS] ok."\n}'
        negative = 'if ($out -match "Thing") {\n    throw "[ERROR] bad."\n}'
        self.assertEqual(len(_case_insensitive_success_assertions(positive)), 1)
        self.assertEqual(_case_insensitive_success_assertions(negative), [])


class TestCleanupDistinguishesNeverCreatedFromFailedRemoval(unittest.TestCase):
    """LDM-#2035: a run that dies early must not invent a second failure.

    When the suite aborts before provisioning, `ldm rm` correctly reports the
    project missing. Both halves treated that as a removal failure and added
    "the project directory may remain", so a reader saw two problems and the
    louder one was the fabricated one. Reported on `v2.26.0-pre.15`, where the
    run died at infra setup on a Windows reserved port and then claimed a
    leftover project that had never existed.

    The benign branch must be evidenced, not assumed -- LDM says missing AND no
    container carries the name -- because the warning it bypasses is
    LDM-#1436's signal, which fired on runs that otherwise PASSED and is still
    unexplained. Swallowing that to tidy this output would trade a real unknown
    for a cosmetic one.

    Checked in both halves because they have drifted before (LDM-#1982).
    """

    def test_both_halves_have_the_benign_branch(self):
        for script in (BASH_SCRIPT, PS1_SCRIPT):
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                self.assertIn(
                    "No project to remove",
                    text,
                    "a run that ends before the project exists still reports a "
                    "removal failure (LDM-#2035)",
                )

    def test_the_benign_branch_also_requires_no_leftover_containers(self):
        """The guard that stops this becoming a blanket suppression."""
        for script, token in ((BASH_SCRIPT, "rm_leftover"), (PS1_SCRIPT, "rmLeftover")):
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                idx = text.index("No project to remove")
                window = text[max(0, idx - 600) : idx]
                self.assertIn(
                    token,
                    window,
                    "the benign branch must also prove no container carries the "
                    "project name, or it suppresses LDM-#1436",
                )

    def test_the_failure_warning_still_exists(self):
        """The counterweight: LDM-#1436's signal must survive."""
        for script in (BASH_SCRIPT, PS1_SCRIPT):
            with self.subTest(script=script.name):
                self.assertIn(
                    "the project directory may remain",
                    script.read_text(encoding="utf-8"),
                )


INSTALLER_PS1 = SCRIPTS_DIR / "install_verification.ps1"


class TestInstallerRunInstructionWorksOnADefaultPolicy(unittest.TestCase):
    """LDM-#2037: the line the installer tells you to run must actually run.

    `install_verification.ps1` closed with `cd <dir>; .\\verify_e2e_refactor.ps1`,
    which on a default Windows execution policy fails with *"cannot be loaded
    because running scripts is disabled on this system"*.

    The hint cannot live in the suite: PowerShell refuses to **load** the file,
    so nothing inside it ever executes. It has to come from the installer,
    which is where the instruction is copied from.

    Nor is a one-time note enough. The reporter had already run
    `Set-ExecutionPolicy -Scope Process` to run the installer itself --
    `-Scope Process` is the right scope precisely because it changes nothing
    permanently, and is exactly why it did not survive into the next window.
    """

    def _text(self):
        return INSTALLER_PS1.read_text(encoding="utf-8")

    def test_the_primary_instruction_needs_no_policy_change(self):
        self.assertIn(
            "-ExecutionPolicy Bypass -File",
            self._text(),
            "the installer's run instruction must work on a default execution "
            "policy without a prior Set-ExecutionPolicy (LDM-#2037)",
        )

    def test_the_direct_form_states_that_the_policy_does_not_persist(self):
        """Offering `Set-ExecutionPolicy -Scope Process` without saying it is
        per-window is what produced the report."""
        text = self._text()
        if "Set-ExecutionPolicy" not in text:
            self.skipTest("the direct form is no longer offered")
        self.assertIn(
            "does not persist",
            text,
            "the direct form is offered without warning that -Scope Process "
            "dies with the window (LDM-#2037)",
        )

    def test_the_host_is_resolved_rather_than_assumed(self):
        """`powershell` and `pwsh` are different binaries; printing the wrong
        one hands the user a command their machine may not have."""
        self.assertIn("PSEdition", self._text())


class TestTheConfigTreeProbeRunsAfterTheDeploys(unittest.TestCase):
    """LDM-#2042: assert on the tree only once the thing that creates it has run.

    Liferay publishes `routes/default/dxp` when a client extension is
    **deployed**, not when the portal starts serving pages. The probe sat
    beside the umask check, immediately after the health wait, announcing
    "Expected the dxp tree once the healthcheck curled /c/portal/layout".

    Measured on Windows: LDM scaffolds `routes/default/dxp` at 18:36 and
    Liferay writes the four `com.liferay.lxc.dxp.*` files into it at 18:52 --
    three minutes after the synthetic extension was deployed, in the same
    minute as the derived-routes one.

    So the check skipped on every Windows run, and that skip was investigated
    three times as a platform defect. Linux and macOS passed throughout, which
    was luck: equally misplaced, and they happened to find a published tree.

    Ordering is the property worth pinning. A wait alone would not fix it --
    in the old position it would have been waiting sixteen minutes on elapsed
    time rather than on the event that matters.
    """

    # A tuple, not a dict: RUF012 forbids a mutable class attribute, and this
    # is a fixed table rather than something a test should be able to edit.
    MARKERS = (
        (
            BASH_SCRIPT,
            'log_and_run "Deploying derived-routes CX"',
            ">> Verifying the published config trees are readable on disk",
        ),
        (
            PS1_SCRIPT,
            'Log-AndRun "Deploying derived-routes CX"',
            ">> Verifying the published config trees are readable on disk",
        ),
    )

    def test_the_probe_follows_every_client_extension_deploy(self):
        for script, deploy, probe in self.MARKERS:
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                self.assertIn(deploy, text)
                self.assertIn(probe, text)
                self.assertLess(
                    text.index(deploy),
                    text.index(probe),
                    f"{script.name}: the config-tree probe runs before the last "
                    "client-extension deploy, so it asserts on a tree Liferay "
                    "has not published yet (LDM-#2042)",
                )

    def test_neither_half_still_blames_the_healthcheck(self):
        """That sentence sent three investigations the wrong way."""
        for script in (BASH_SCRIPT, PS1_SCRIPT):
            with self.subTest(script=script.name):
                for line in script.read_text(encoding="utf-8").split("\n"):
                    if line.lstrip().startswith(("#", "//")):
                        continue  # the explanatory comment quotes it deliberately
                    self.assertNotIn(
                        "healthcheck curled /c/portal/layout",
                        line,
                        "a skip message still names the healthcheck as the "
                        "trigger for publication (LDM-#2042)",
                    )


class TheSuiteRemovesTheImagesItBuilds(unittest.TestCase):
    """LDM-#2084: both halves removed an image name compose never produces.

    Compose builds `<project>-<service>`, so the real image is
    `ldm-smoke-test-<PORT>-syntheticsvc:latest`. Both suites ran
    `docker rmi -f "<service>:latest"`, which matched nothing on every run
    since it was written, and both swallowed the failure. Measured on a
    developer machine: six orphaned images from three past runs, inside 29 GB
    of reclaimable images.

    The fix is not a corrected name -- reconstructing the name is what broke,
    and compose already changed `_` to `-` between v1 and v2. The ID is asked
    of compose instead. These assertions are structural because the claim is
    structural: the scripts must not rebuild that name again.
    """

    @staticmethod
    def _code_only(path):
        """The script with comment lines removed.

        The first version of this test searched the raw file and failed
        against the explanatory comment directly above the fix, which quotes
        the broken line it replaced. That is the same defect the comment
        describes: a measurement matching prose ABOUT the symptom rather than
        the symptom. Both halves use `#` for comments.
        """
        return "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_neither_half_reconstructs_the_image_name(self):
        for path, pattern in (
            (BASH_SCRIPT, r'rmi\s+-f\s+"\$\{CXSVC_SERVICE\}:latest"'),
            (PS1_SCRIPT, r'rmi\s+-f\s+"\$\{cxSvcService\}:latest"'),
        ):
            with self.subTest(script=path.name):
                self.assertIsNone(
                    re.search(pattern, self._code_only(path)),
                    f"{path.name} rebuilds the image name compose does not use",
                )

    def test_both_halves_ask_compose_for_the_image_id(self):
        for path in (BASH_SCRIPT, PS1_SCRIPT):
            with self.subTest(script=path.name):
                self.assertIn(
                    "compose images -q",
                    path.read_text(encoding="utf-8"),
                    f"{path.name} does not ask compose for the image it built",
                )

    def test_the_id_is_captured_before_the_container_is_removed(self):
        """Order matters: `compose images` returns nothing once the container
        is gone, so capturing afterwards would silently reclaim nothing --
        the same failure in a new costume."""
        for path in (BASH_SCRIPT, PS1_SCRIPT):
            text = path.read_text(encoding="utf-8")
            with self.subTest(script=path.name):
                capture = text.index("compose images -q")
                removal = text.index("compose rm -fsv", capture - 2000)
                self.assertLess(
                    capture, removal, f"{path.name} captures the ID too late"
                )

    def test_both_halves_sweep_the_run_s_own_images(self):
        """Scoped to PROJECT_NAME, which carries the random test port, so it
        cannot reach a base image or another developer's work."""
        for path in (BASH_SCRIPT, PS1_SCRIPT):
            with self.subTest(script=path.name):
                self.assertIn(
                    "reference=$PROJECT_NAME-*"
                    if path is PS1_SCRIPT
                    else "reference=${PROJECT_NAME}-*",
                    path.read_text(encoding="utf-8"),
                    f"{path.name} does not sweep the images this run built",
                )


def _preflight_offset(text):
    """Where the LDM-#2085 pre-flight actually begins.

    LDM-#2121: these tests used `text.index("LDM-#2085")`, the FIRST mention of
    the tag anywhere in the file. That is not an anchor, it is a coincidence --
    any later comment that cites the issue moves it. LDM-#2121 added one in the
    cleanup path, several hundred lines earlier, and the breakage was not
    symmetric: one test failed loudly while `test_the_pre_flight_runs_before_the_pulls`
    started PASSING for the wrong reason, comparing a comment's position
    against the image pulls instead of the guard's.

    A silent false pass in a test that exists to police ordering is worse than
    the failure beside it, so all three now anchor on the sentence that opens
    the pre-flight in both halves. It is unique to that block, and it sits
    ahead of the parsing the array test needs to see -- which the refusal
    message, tried first, does not.
    """
    return text.index("refuse to run while another LDM project is live.")


class TheSuiteRefusesToRunBesideLiveProjects(unittest.TestCase):
    """LDM-#2085: the suite reconfigures shared infrastructure.

    Without this it pays two image pulls and, if Elasticsearch misses its
    readiness window, a destroyed shared search index (LDM-#2083) before
    failing at the SSL section it was always going to fail at.
    """

    def test_both_halves_check_for_running_projects(self):
        for path in (BASH_SCRIPT, PS1_SCRIPT):
            with self.subTest(script=path.name):
                self.assertIn(
                    "LDM-#2085",
                    path.read_text(encoding="utf-8"),
                    f"{path.name} has no running-project pre-flight",
                )

    def test_the_check_runs_before_the_image_pulls(self):
        """The ordering IS the fix.

        A check after the pulls would still prevent the index loss but waste
        the two pulls that made the original failure expensive; a check after
        `infra setup` would prevent neither.
        """
        for path in (BASH_SCRIPT, PS1_SCRIPT):
            text = path.read_text(encoding="utf-8")
            with self.subTest(script=path.name):
                guard = _preflight_offset(text)
                pull = text.index("Pre-pulling required Docker images")
                self.assertLess(
                    guard, pull, f"{path.name} checks only after pulling images"
                )

    def test_the_powershell_half_enumerates_every_project(self):
        """PowerShell 5.1 does not enumerate a deserialized JSON array.

        Without ConvertTo-LdmArray the check sees one object and names only
        the first running project, so a second one is silently permitted to
        have its index destroyed -- LDM-#1300 in a new place.
        """
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        guard = _preflight_offset(text)
        block = text[guard : guard + 2500]
        self.assertIn(
            "ConvertTo-LdmArray",
            block,
            "the PS pre-flight reads list --json without the 5.1 array fix",
        )

    def test_the_refusal_names_what_is_at_risk(self):
        """A refusal a user cannot act on gets bypassed.

        The index risk is the part nobody can guess, so it has to be said.
        """
        for path in (BASH_SCRIPT, PS1_SCRIPT):
            text = path.read_text(encoding="utf-8")
            guard = _preflight_offset(text)
            block = text[guard : guard + 3500]
            with self.subTest(script=path.name):
                self.assertIn("ldm stop", block, "no actionable remedy given")
                self.assertIn("LDM-#2083", block, "the index risk is not named")


class TheSuiteRestoresTheSharedStateItChanges(unittest.TestCase):
    """LDM-#2100 / LDM-#2103: the suite mutated shared state and left it.

    The custom-SSL-port check moves `liferay-proxy-global` -- global by name
    and by effect -- to 8443 and nothing put it back. Because LDM adopts the
    RUNNING container's ports when --force-recreate is absent (LDM-#1568),
    every project created on that machine afterwards inherited it, with no
    stale config value anywhere to explain why. Reported from a quickstart
    that came up on `https://<host>:8443` with 443 free, and reproduced on a
    second machine within the hour.

    Structural assertions, because the claim is structural: the restore must
    exist, in BOTH halves, and must run from the exit path rather than
    inline. An inline restore is skipped by exactly the failing runs that
    leave the machine dirty, which is the shape shared with LDM-#2084 and
    LDM-#2103.

    The behaviour itself was exercised directly: both restore functions were
    run against a stubbed docker and ldm, confirming they reissue
    `infra setup --ssl-port <original> --force-recreate` only when the port
    has drifted, and that the sweep removes `sharedboot-*` while leaving
    neighbouring directories alone.
    """

    @staticmethod
    def _code_only(path):
        return "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_both_halves_define_a_restore(self):
        self.assertIn("restore_shared_infrastructure()", self._code_only(BASH_SCRIPT))
        self.assertIn(
            "function Restore-SharedInfrastructure", self._code_only(PS1_SCRIPT)
        )

    def test_the_port_to_restore_comes_from_config_not_the_live_container(self):
        """LDM-#2121 inverts what this test used to assert, deliberately.

        It required `ORIGINAL_SSL_PORT=$(docker inspect`, reasoning that
        restoring to a hardcoded 443 would be wrong on a machine deliberately
        on another port. The concern is real; reading the live container is
        not a way to meet it.

        On a machine already polluted by an earlier run the proxy is ALREADY
        on 8443 when the capture happens. The suite then sets 8443, the two
        match, and the restore correctly concludes there is nothing to do --
        leaving the machine exactly as wrong as it found it. A container
        cannot distinguish a deliberate choice from this bug's own residue.

        LDM's own resolution can: `handlers/infra.py` takes --ssl-port, else
        LDM_SSL_PORT, else 443, and no global default is persisted. So an
        operator who means it exports LDM_SSL_PORT, and everyone else gets
        443 -- the documented default, and what the suite found on a clean
        machine anyway.
        """
        bash = self._code_only(BASH_SCRIPT)
        self.assertIn('ORIGINAL_SSL_PORT="${LDM_SSL_PORT:-443}"', bash)
        self.assertNotIn(
            "ORIGINAL_SSL_PORT=$(docker inspect",
            bash,
            "reading the live proxy is the defect: on a polluted machine it "
            "captures 8443 and the restore becomes a no-op",
        )

        ps1 = self._code_only(PS1_SCRIPT)
        self.assertIn("$env:LDM_SSL_PORT", ps1)
        self.assertNotIn("$probed = (& docker inspect liferay-proxy-global", ps1)

    def test_neither_half_identifies_other_projects_by_container_name(self):
        """LDM-#2121 defect 1: the exclusion pattern could not keep up.

        It named `${PROJECT_NAME}` and `${PROJECT_NAME}-db-1`, but a run also
        creates `-db`, `-syntheticsvc` and `-derivedsvc`. So the suite counted
        its own containers as other projects, `other_containers` was never
        empty, and the global cleanup was skipped on EVERY run -- including on
        a machine with nothing else on it. Both pre.4 reports show it, and
        both passed.

        `ldm list --json` is authoritative about what is an LDM project; the
        LDM-#2085 pre-flight already asks it, and its comment names this call
        site as the one that does not.
        """
        for path in (BASH_SCRIPT, PS1_SCRIPT):
            self.assertNotIn(
                "liferay-docker-proxy|",
                self._code_only(path),
                f"{path.name} still filters docker ps output by a "
                f"hand-maintained name pattern",
            )
        self.assertIn("list_running_projects_except", self._code_only(BASH_SCRIPT))
        self.assertIn("Get-RunningProjectsExcept", self._code_only(PS1_SCRIPT))

    def test_an_unknown_answer_does_not_license_a_teardown(self):
        """LDM-#2121: the EXIT trap can run before VENV_PYTHON is assigned.

        The trap is registered around line 729; VENV_PYTHON is set around line
        874. An early failure in between reaches the cleanup with no
        interpreter, and an empty answer there would read as "nothing else is
        running" -- licensing the removal of the shared proxy and search node
        on the strength of a question that was never asked.

        Both halves return a sentinel instead, and the caller treats any
        non-empty value as "skip", which fails safe in the same direction the
        old grep did.
        """
        func = _extract_function(
            BASH_SCRIPT,
            re.compile(r"^list_running_projects_except\s*\(\)\s*\{.*?^\}", re.M | re.S),
        )
        script = (
            "set -e\n"
            'VENV_PYTHON=""\n'
            'LDM_CMD="ldm"\n'
            'PATH="/nonexistent"\n'
            f"{func}\n"
            'list_running_projects_except "mine"\n'
        )
        res = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False
        )
        self.assertEqual(
            res.stdout.strip(),
            "__unknown__",
            "with no interpreter the helper returned an empty answer, which "
            "the caller reads as 'nothing else is running'",
        )

        self.assertIn("__unknown__", self._code_only(PS1_SCRIPT))

    def test_the_restore_runs_after_the_project_teardown(self):
        """LDM-#2112: it ran FIRST in the trap and was refused every time.

        `infra setup --force-recreate` declines while LDM projects are
        running. The suite's own project is torn down ~70 lines further
        down, so at restore time it was still up. Three verification runs --
        Fedora, WSL2, macOS -- reported pass with the proxy still on 8443.
        """
        bash = self._code_only(BASH_SCRIPT)
        trap = bash[bash.index("cleanup_test_projects()") :]
        call = trap.index("\n    restore_shared_infrastructure")
        teardown = trap.index("--delete")

        self.assertGreater(
            call,
            teardown,
            "the restore runs before this run's project is torn down, so the "
            "recreate guard will refuse it",
        )

    def test_both_halves_pass_force_not_just_yes(self):
        """LDM-#2112. `-y` is --non-interactive (cli.py); the recreate guard
        honours `args.force`, so -y alone was refused on every platform.

        Asserted against the function BODY, not a single line: the bash
        invocation spans a continuation, and selecting "the line with
        --ssl-port and --force-recreate" picks the WARNING MESSAGE instead,
        which names the manual command. That is how the first version of
        this test failed against correct code.

        `--force-recreate` also contains the substring "--force", so a
        containment check is trivially true -- it passed against a script
        with the flag removed. The flag must be standalone and attached to
        the invocation.
        """
        for path, start in (
            (BASH_SCRIPT, "restore_shared_infrastructure() {"),
            (PS1_SCRIPT, "function Restore-SharedInfrastructure {"),
        ):
            code = self._code_only(path)
            body = code[code.index(start) :]
            body = body[: body.index("\n}")]
            self.assertRegex(
                body,
                r"--force\s+infra setup",
                f"{path.name} invokes infra setup without a standalone "
                f"--force, so the recreate guard will refuse it",
            )

    def test_both_halves_record_the_outcome_in_the_report(self):
        """LDM-#2111 is what hid LDM-#2112: the failure branch used a bare
        echo, so the artifact recorded nothing and three green runs went out
        with the proxy still moved."""
        self.assertIn("FINAL_REPORT_PATH", self._code_only(BASH_SCRIPT))
        self.assertIn("Write-TrapNote", self._code_only(PS1_SCRIPT))

    def test_both_halves_verify_the_port_actually_changed(self):
        """ "Reported success" is not "is on 443"."""
        self.assertIn("Global proxy restored to SSL port", self._code_only(BASH_SCRIPT))
        self.assertIn("Global proxy restored to SSL port", self._code_only(PS1_SCRIPT))

    def test_the_restore_runs_from_the_exit_path_not_inline(self):
        """The whole point. The suite exits from dozens of places between
        the port change and the end of the run."""
        bash = self._code_only(BASH_SCRIPT)
        trap_body = bash[bash.index("cleanup_test_projects()") :]
        self.assertIn("restore_shared_infrastructure", trap_body)

        ps1 = self._code_only(PS1_SCRIPT)
        final_body = ps1[ps1.index("function Finalize-Verification") :]
        self.assertIn("Restore-SharedInfrastructure", final_body)

    def test_both_halves_sweep_the_shared_db_boot_directories(self):
        """LDM-#2103: removed inline at the end of its own function, which
        covers its failure paths but not an abort in between. An empty
        `sharedboot-mysql-9911` from September was found in a user's
        workspace."""
        self.assertIn("sharedboot-*", self._code_only(BASH_SCRIPT))
        self.assertIn("sharedboot-*", self._code_only(PS1_SCRIPT))

    def test_the_bash_sweep_cannot_run_against_an_unset_workspace(self):
        """`rm -rf $LDM_WORKSPACE/sharedboot-*` with the variable unset is a
        different command entirely. The `:?` guard and the enclosing test
        are what stop it."""
        bash = self._code_only(BASH_SCRIPT)
        line = next(
            ln for ln in bash.splitlines() if "sharedboot-*" in ln and "rm -rf" in ln
        )
        self.assertIn("${LDM_WORKSPACE:?}", line)


class BothHalvesGuardTheSharedInfrastructure(unittest.TestCase):
    """LDM-#2113: the PowerShell half destroyed the shared proxy blindly.

    bash has always checked for other running containers before
    `docker rm -f liferay-proxy-global liferay-search-global
    liferay-docker-proxy`. PowerShell did not, so a Docker Desktop
    verification run removed the shared proxy and search node out from
    under any other LDM project -- whose containers keep running, so
    nothing reports a failure. The same silent shape as LDM-#2083.

    Verified behaviourally as well: the extracted PowerShell block was run
    against a stubbed `docker`, cleaning up when nothing else was running
    and skipping (naming the project) when something was.
    """

    @staticmethod
    def _code_only(path):
        return "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_neither_half_removes_the_proxy_unconditionally(self):
        for path in (BASH_SCRIPT, PS1_SCRIPT):
            code = self._code_only(path)
            idx = code.index("rm -f liferay-proxy-global")
            preceding = code[max(0, idx - 1200) : idx]
            self.assertIn(
                "other",
                preceding.lower(),
                f"{path.name} removes the global proxy with no check for "
                f"other running projects",
            )

    def test_both_halves_say_which_branch_they_took(self):
        for path in (BASH_SCRIPT, PS1_SCRIPT):
            code = self._code_only(path)
            self.assertIn("Skipping global infrastructure cleanup", code, path.name)
            self.assertIn("Cleaning up global infrastructure", code, path.name)


class TheTrapWritesItsDecisionsToTheReport(unittest.TestCase):
    """LDM-#2111: the exit trap spoke only to the console.

    "Other LDM projects are running ... Skipping global infrastructure
    cleanup" was printed on three verification runs and recorded on none.
    It is the line that explains why the proxy survived on 8443, so having
    it would have identified LDM-#2112 on the first run rather than the
    fourth.
    """

    @staticmethod
    def _code_only(path):
        return "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_both_halves_have_a_reporter_that_survives_finalisation(self):
        """The trap runs on both sides of the report being renamed, so the
        reporter has to write to whichever file currently exists."""
        for path, fn in (
            (BASH_SCRIPT, "trap_report()"),
            (PS1_SCRIPT, "Write-TrapNote"),
        ):
            code = self._code_only(path)
            self.assertIn(fn, code, path.name)
            body_at = code.index(fn)
            body = code[body_at : body_at + 900]
            self.assertIn("RESULTS_FILE_TMP", body, f"{path.name} reporter")
            self.assertIn("FINAL_REPORT_PATH", body, f"{path.name} reporter")

    def test_the_infrastructure_decision_is_reported_not_just_printed(self):
        for path, call in (
            (BASH_SCRIPT, "trap_report"),
            (PS1_SCRIPT, "Write-TrapNote"),
        ):
            code = self._code_only(path)
            line = next(
                ln
                for ln in code.splitlines()
                if "Skipping global infrastructure cleanup" in ln
            )
            self.assertIn(call, line, f"{path.name}: {line.strip()}")

    def test_the_failure_banner_is_reported(self):
        """A failed run's report should say it failed."""
        self.assertIn(
            'trap_report "!!! VERIFICATION FAILED', self._code_only(BASH_SCRIPT)
        )
        self.assertIn('Write-TrapNote "`n[FAILED]', self._code_only(PS1_SCRIPT))


class NativeCapturesDoNotGoThroughTheFormatter(unittest.TestCase):
    """LDM-#2115: PowerShell 5.1 wrapped native stderr at the console width.

    `& cmd 2>&1 | Out-String` turns stderr into ErrorRecord objects and then
    FORMATS them; NormalView on Windows PowerShell 5.1 prefixes `ldm.exe :`
    and hard-wraps at the console width. A phrase split across the wrap no
    longer matches, so `.Contains('ldm run')` failed against a binary whose
    message was correct:

        ldm.exe : ... Please use 'ldm
        run' to recreate it.

    Console-width dependent, which is the worst property an assertion can
    have -- it passes in a wide terminal and fails in a narrow one.

    pwsh 7 renders ConciseView and does not reproduce it, so these are
    structural: they pin that the formatter is not in the path.
    """

    @staticmethod
    def _code_only(path):
        return "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_no_native_capture_still_pipes_to_out_string(self):
        code = self._code_only(PS1_SCRIPT)
        offenders = [
            ln.strip() for ln in code.splitlines() if "2>&1 | Out-String" in ln
        ]
        self.assertEqual(
            offenders,
            [],
            "these capture native stderr through the formatter and will wrap "
            f"on a narrow Windows console: {offenders}",
        )

    def test_the_helper_exists(self):
        self.assertIn("function ConvertTo-LdmText", self._code_only(PS1_SCRIPT))

    def test_the_helper_reads_the_error_records_own_message(self):
        """Taking .Exception.Message is the whole fix: it bypasses the
        formatter, where any width-based workaround only moves the
        threshold."""
        code = self._code_only(PS1_SCRIPT)
        start = code.index("function ConvertTo-LdmText")
        body = code[start : start + 1400]
        self.assertIn("ErrorRecord", body)
        self.assertIn(".Exception.Message", body)

    def test_display_formatting_still_uses_out_string(self):
        """Guard against over-reach. `Out-String` is correct for formatting
        objects for display, and for captures using 2>$null which produce no
        ErrorRecords at all."""
        code = self._code_only(PS1_SCRIPT)
        self.assertIn("| Out-String", code)


class TheNodeTargetGuardAsksLdmNotTheFile(unittest.TestCase):
    """LDM-#2117: the guard proved the bytes were written, not that LDM read them.

    The PowerShell half wrote the target with `Set-Content -Encoding UTF8`,
    which emits a UTF-8 BOM on Windows PowerShell 5.1. `read_meta` sniffs
    the format with `content.strip().startswith("{")`, and `str.strip()`
    does not remove a BOM -- so BOM-prefixed JSON took the key=value
    branch, no line in a JSON document contains "=", and the meta came back
    empty. The target vanished, `ldm rm --delete` took the LOCAL path, and
    the check failed against a binary behaving correctly.

    The old guard grepped the meta file for the node name. The bytes were
    there, so it passed, while the only consumer that matters could not
    parse the document at all. Reading the file is testing the test.
    """

    @staticmethod
    def _code_only(path):
        return "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )

    def _fn(self, path, start):
        code = self._code_only(path)
        body = code[code.index(start) :]
        end = body.index("\n}")
        return body[:end]

    def test_the_powershell_meta_write_is_bom_free(self):
        body = self._fn(PS1_SCRIPT, "function Test-NodeDeleteAnnouncesAndDegrades")
        self.assertIn("WriteAllText", body)
        self.assertIn("UTF8Encoding", body)
        self.assertNotIn(
            "Set-Content -Path $metaFile",
            body,
            "Set-Content -Encoding UTF8 writes a BOM on Windows PowerShell 5.1",
        )

    def test_both_halves_confirm_the_target_via_ldm(self):
        """The assertion that would have caught it: ask the binary what it
        resolved, not the file we just wrote."""
        for path, start in (
            (BASH_SCRIPT, "verify_node_delete_announces_and_degrades() {"),
            (PS1_SCRIPT, "function Test-NodeDeleteAnnouncesAndDegrades"),
        ):
            body = self._fn(path, start)
            self.assertIn("list --json", body, f"{path.name} does not ask LDM")

    def test_neither_half_still_greps_the_meta_file_as_its_guard(self):
        for path, start, needle in (
            (
                BASH_SCRIPT,
                "verify_node_delete_announces_and_degrades() {",
                'grep -qF "$node" "$meta_file"',
            ),
            (
                PS1_SCRIPT,
                "function Test-NodeDeleteAnnouncesAndDegrades",
                "Get-Content -Raw -Path $metaFile) -cmatch",
            ),
        ):
            body = self._fn(path, start)
            self.assertNotIn(needle, body, f"{path.name} still tests the test")

    def test_the_failure_message_shows_both_values(self):
        """'wrote X, LDM resolved Y' is what makes the next occurrence
        diagnosable in one read."""
        for path, start in (
            (BASH_SCRIPT, "verify_node_delete_announces_and_degrades() {"),
            (PS1_SCRIPT, "function Test-NodeDeleteAnnouncesAndDegrades"),
        ):
            body = self._fn(path, start)
            self.assertIn("LDM resolved", body, path.name)


class TestTheProxyRestoreCorrectsAPollutedMachine(unittest.TestCase):
    """LDM-#2121, behaviourally. The starting condition IS the bug.

    An assertion that the restore RAN would not have caught this: it ran, and
    correctly did nothing, because it had been told the machine's current
    (wrong) port was the original one. So these tests begin where the reports
    began -- a proxy already on 8443 before the suite touches anything.

    Both v2.26.5-pre.4 reports contain no restore lines at all, and both
    passed.
    """

    def _run_restore(self, env, proxy_port):
        """Runs the real restore_shared_infrastructure against a fake docker.

        The fake reports the port from a file and lets the fake `ldm` rewrite
        it, so it behaves like a recreate rather than always agreeing. A stub
        that echoed a constant could not tell a restore that worked from one
        that was skipped -- which is the distinction this whole issue turns
        on.
        """
        func = _extract_function(
            BASH_SCRIPT,
            re.compile(
                r"^restore_shared_infrastructure\s*\(\)\s*\{.*?^\}", re.M | re.S
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            state = tmp_path / "port"
            state.write_text(str(proxy_port))

            docker = bin_dir / "docker"
            docker.write_text(
                f'#!/bin/bash\nif [ "$1" = "inspect" ]; then cat "{state}"; fi\n'
            )
            # lint: executable-stub -- bash must exec it; "docker" is not a watched name
            docker.chmod(0o755)

            # NOT named "ldm". LDM-#1898: a test stub called lfr-tunnel was
            # spawned from a temporary directory and endpoint protection killed
            # two developer sessions before anyone connected the two, and "ldm"
            # is on the same watched list (scripts/check_test_binary_stubs.py).
            # The function under test invokes "$LDM_CMD", so the name is ours
            # to choose and there is no reason to choose a watched one.
            ldm = tmp_path / "fake-ldm-cli"
            ldm.write_text(
                "#!/bin/bash\n"
                'prev=""\n'
                'for a in "$@"; do\n'
                f'  if [ "$prev" = "--ssl-port" ]; then printf %s "$a" > "{state}"; fi\n'
                '  prev="$a"\n'
                "done\n"
                "exit 0\n"
            )
            # lint: executable-stub -- bash must exec it; renamed off the watched list
            ldm.chmod(0o755)

            script = (
                "set -u\n"
                'trap_report() { echo "$*"; }\n'
                f'LDM_CMD="{ldm}"\n'
                "LDM_WORKSPACE=\n"
                f"{func}\n"
                "restore_shared_infrastructure\n"
            )
            run_env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
            run_env.update(env)
            res = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                check=False,
                env=run_env,
            )
            return res, state.read_text()

    def test_a_machine_already_on_8443_is_corrected(self):
        """The reported machines. Previously a silent no-op."""
        res, final = self._run_restore({"ORIGINAL_SSL_PORT": "443"}, proxy_port=8443)
        self.assertEqual(
            final,
            "443",
            f"the proxy was left on {final}; stdout was:\n{res.stdout}",
        )
        self.assertIn("Global proxy restored to SSL port 443", res.stdout)

    def test_a_clean_machine_is_left_alone(self):
        """Already correct means nothing to do, and nothing to report."""
        res, final = self._run_restore({"ORIGINAL_SSL_PORT": "443"}, proxy_port=443)
        self.assertEqual(final, "443")
        self.assertNotIn("Restoring the global proxy", res.stdout)

    def test_a_deliberate_non_default_port_is_honoured(self):
        """The old test's concern, met through configuration instead.

        An operator who genuinely runs the proxy elsewhere exports
        LDM_SSL_PORT, and the suite puts it back there rather than on 443.
        """
        res, final = self._run_restore({"ORIGINAL_SSL_PORT": "9443"}, proxy_port=8443)
        self.assertEqual(final, "9443", res.stdout)


def _ps_strip_comments(line):
    """Remove a trailing PowerShell `#` comment, respecting quotes."""
    out: list[str] = []
    quote = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _ps_split_top_level(text, sep=","):
    """Split on `sep` at bracket depth 0, outside quotes."""
    parts: list[str] = []
    buf: list[str] = []
    depth, quote = 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _ps_param_blocks(text):
    """Map each PowerShell function name -> ordered list of parameter dicts.

    Each entry is {"name", "mandatory", "switch"}. Only the function's own
    `param(...)` block is read -- the first one after the `function` keyword,
    which is the only form this script uses.
    """
    blocks: dict[str, list[dict[str, object]]] = {}
    for m in re.finditer(r"^function\s+([A-Za-z][\w-]*)\s*\{", text, re.M):
        name = m.group(1)
        rest = text[m.end() :]
        pm = re.search(r"\bparam\s*\(", rest)
        if not pm:
            blocks[name] = []
            continue
        # The param block must belong to THIS function, not a later one.
        nxt = re.search(r"^function\s+[A-Za-z][\w-]*\s*\{", rest, re.M)
        if nxt and nxt.start() < pm.start():
            blocks[name] = []
            continue
        depth, i = 1, pm.end()
        while i < len(rest) and depth:
            if rest[i] == "(":
                depth += 1
            elif rest[i] == ")":
                depth -= 1
            i += 1
        body = rest[pm.end() : i - 1]
        params = []
        for decl in _ps_split_top_level(body):
            # Separate the attribute brackets from the variable itself before
            # looking for either. Taking the first `$` in the declaration finds
            # the `$true` inside `[Parameter(Mandatory = $true)]`, names the
            # parameter "true", and -- because the attribute text is then cut
            # short of its own `$true` -- reports every mandatory parameter as
            # optional. The sweep below passes vacuously when that happens,
            # which is what test_the_binder_can_see_a_missing_mandatory_
            # parameter exists to catch.
            attrs: list[str] = []
            bare: list[str] = []
            depth = 0
            for ch in decl:
                if ch == "[":
                    depth += 1
                (attrs if depth else bare).append(ch)
                if ch == "]":
                    depth -= 1
            attr_text = "".join(attrs)
            vm = re.search(r"\$([A-Za-z_]\w*)", "".join(bare))
            if not vm:
                continue
            params.append(
                {
                    "name": vm.group(1),
                    "mandatory": bool(
                        re.search(r"Mandatory\s*=\s*\$true", attr_text, re.I)
                    ),
                    "switch": "[switch]" in attr_text.replace(" ", "").lower(),
                }
            )
        blocks[name] = params
    return blocks


def _ps_call_arguments(text, index):
    """Tokenise the arguments of a call beginning at `index` (just past the
    function name), stopping at the end of the command."""
    tokens: list[str] = []
    buf: list[str] = []
    depth, quote = 0, None
    i = index
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "`" and i + 1 < len(text) and text[i + 1] == "\n":
            i += 2  # line continuation: the command carries on
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and ch in "|;\n#":
            break  # end of this command, or the start of a trailing comment
        if depth == 0 and ch in " \t":
            if buf:
                tokens.append("".join(buf))
                buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        tokens.append("".join(buf))
    return tokens


def _ps_unbound_mandatory(text):
    """Every in-script call that leaves a Mandatory parameter unsupplied.

    Returns a list of (function, line number, missing parameter names).
    """
    blocks = _ps_param_blocks(text)
    interesting = {n: p for n, p in blocks.items() if any(q["mandatory"] for q in p)}
    findings = []
    for name, params in interesting.items():
        for m in re.finditer(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            prefix = _ps_strip_comments(text[line_start : m.start()])
            if len(prefix) != m.start() - line_start:
                continue  # the name sits inside a comment
            if re.search(r"(function|filter)\s*$", prefix):
                continue  # the definition itself
            args = _ps_call_arguments(text, m.end())
            if any(a.startswith("@") for a in args):
                continue  # splatted: not statically analysable
            named, positional, idx = set(), 0, 0
            lookup = {p["name"].lower(): p for p in params}
            while idx < len(args):
                tok = args[idx]
                key = tok[1:].lower() if tok.startswith("-") else None
                if key and key in lookup:
                    named.add(lookup[key]["name"])
                    if not lookup[key]["switch"]:
                        idx += 1
                elif tok.startswith("-") and re.match(r"^-[A-Za-z]", tok):
                    idx += 1  # an unrecognised named argument; skip its value
                else:
                    positional += 1
                idx += 1
            remaining = [p for p in params if p["name"] not in named]
            missing = [
                p["name"]
                for pos, p in enumerate(remaining)
                if p["mandatory"] and pos >= positional
            ]
            if missing:
                findings.append((name, text.count("\n", 0, m.start()) + 1, missing))
    return findings


class EveryPowerShellCallSuppliesItsMandatoryParameters(unittest.TestCase):
    """LDM-#2132: a missing Mandatory parameter is an interactive PROMPT.

    `Get-RunningProjectsExcept` called `ConvertFrom-LdmJson $raw`, binding the
    one positional argument to `-Raw` and leaving `-Label` -- declared
    `Mandatory = $true` -- unsupplied. PowerShell does not fail on that; it
    stops and asks:

        cmdlet ConvertFrom-LdmJson at command pipeline position 1
        Supply values for the following parameters:
        Label:

    The call sits in `Finalize-Verification`, which runs on every exit path,
    so EVERY PowerShell run of v2.26.5-pre.5 stopped there -- pass or fail.
    Unattended, it hangs until the harness kills it.

    Nothing existing could see it. PSScriptAnalyzer does not check a call
    against a local function's parameter block; the suite's own test asserted
    only that the string "Get-RunningProjectsExcept" appeared in the file,
    which a broken call site preserves perfectly; and the bash twin -- the
    half that IS executed by a test -- was correct. This is the Behaviour
    Coverage Gate's first tell, applied to the half that was convenient to
    run.

    So this test reads the parameter blocks and binds every call site the way
    PowerShell would: named arguments first, then positional arguments
    filling what remains in declaration order.
    """

    def test_no_call_site_leaves_a_mandatory_parameter_unsupplied(self):
        findings = _ps_unbound_mandatory(PS1_SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(
            [],
            findings,
            "these calls would stop for an interactive prompt: "
            + "; ".join(
                f"{name} at line {line} is missing {', '.join(missing)}"
                for name, line, missing in findings
            ),
        )

    def test_the_binder_can_see_a_missing_mandatory_parameter(self):
        """The probe, inline: the checker must fail on the shape it exists for.

        Without this, a parser that silently found no functions at all would
        report a clean result forever.
        """
        sample = (
            "function Get-Thing {\n"
            "    param(\n"
            "        [Parameter(Mandatory = $true)][AllowNull()]$Raw,\n"
            "        [Parameter(Mandatory = $true)][string]$Label\n"
            "    )\n"
            "    return $Raw\n"
            "}\n"
            "$x = Get-Thing $data\n"
        )
        self.assertEqual(
            [("Get-Thing", 8, ["Label"])],
            _ps_unbound_mandatory(sample),
        )

    def test_the_binder_accepts_a_correctly_supplied_call(self):
        """And it must not cry wolf -- named, positional and switch forms."""
        sample = (
            "function Get-Thing {\n"
            "    param(\n"
            "        [Parameter(Mandatory = $true)][AllowNull()]$Raw,\n"
            "        [Parameter(Mandatory = $true)][string]$Label,\n"
            "        [switch]$Quiet\n"
            "    )\n"
            "    return $Raw\n"
            "}\n"
            '$a = Get-Thing -Raw $data -Label "list --json"\n'
            '$b = Get-Thing $data "list --json"\n'
            '$c = Get-Thing -Quiet -Raw $data -Label "list --json"\n'
            '$d = (Get-Thing -Raw $data -Label "x") | ForEach-Object { $_ }\n'
            '$e = Get-Thing -Label "x" $data\n'
        )
        self.assertEqual([], _ps_unbound_mandatory(sample))

    def test_the_helper_that_shipped_broken_is_now_bound_by_name(self):
        """LDM-#2132, specifically: the call that caused it.

        Pinned separately from the sweep so the reason survives if the sweep
        is ever narrowed -- and because `-Label` is what names the helper in
        its own error output, so a positional fix would be worse than none.
        """
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        func = re.search(
            r"^function Get-RunningProjectsExcept\s*\{.*?^\}", text, re.M | re.S
        )
        assert func is not None, "Get-RunningProjectsExcept is gone"
        body = func.group(0)
        self.assertIn('ConvertFrom-LdmJson -Raw $raw -Label "list --json"', body)

    def test_the_parse_failure_returns_the_sentinel_rather_than_throwing(self):
        """LDM-#2132: the second half. ConvertFrom-LdmJson ends its catch with
        a bare `throw`, so a parse outside the try defeated the whole point of
        the function -- its contract is to answer "__unknown__" when it cannot
        tell, and `Finalize-Verification` has no handler, so the throw skipped
        `Restore-SharedInfrastructure` on its last line. That is exactly the
        outcome LDM-#2121 exists to prevent, reached by a different route, and
        it is what a run with Docker stopped actually did.
        """
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        found = re.search(
            r"^function Get-RunningProjectsExcept\s*\{.*?^\}", text, re.M | re.S
        )
        assert found is not None, "Get-RunningProjectsExcept is gone"
        try_body = re.search(r"try\s*\{(.*?)\}\s*catch", found.group(0), re.S)
        assert try_body is not None, "the helper no longer guards its probe"
        self.assertIn(
            "ConvertFrom-LdmJson",
            try_body.group(1),
            "the JSON parse sits outside the try, so an unparseable answer "
            "propagates instead of returning the __unknown__ sentinel",
        )


_PS_JSON_HELPERS = (
    r"^function ConvertTo-LdmText\s*\{.*?^\}",
    r"^function ConvertFrom-LdmJson\s*\{.*?^\}",
    r"^function ConvertTo-LdmArray\s*\{.*?^\}",
    r"^function Get-RunningProjectsExcept\s*\{.*?^\}",
)


def _run_get_running_projects_except(binary, payload):
    """Drives the REAL Get-RunningProjectsExcept against a fake CLI.

    `-NonInteractive` is the whole point: it turns PowerShell's
    missing-mandatory-parameter PROMPT into an error, so LDM-#2132 shows up as
    a failure here instead of as a hung run on someone's Windows box.
    """
    funcs = "\n".join(
        _extract_function(PS1_SCRIPT, re.compile(p, re.M | re.S))
        for p in _PS_JSON_HELPERS
    )
    with tempfile.TemporaryDirectory() as tmp:
        # A PowerShell script, not a compiled stub: nothing is chmod'd and
        # nothing is named `ldm`. LDM-#1898 -- a stub called lfr-tunnel run
        # from a temporary directory had endpoint protection kill two
        # developer sessions, and `ldm` is on the same watched list
        # (scripts/check_test_binary_stubs.py).
        stub = Path(tmp) / "fake-ldm-cli.ps1"
        stub.write_text(payload, encoding="utf-8")
        script = (
            f"{funcs}\n"
            f"$LDM_CMD = '{stub.as_posix()}'\n"
            "$r = @(Get-RunningProjectsExcept 'mine')\n"
            "Write-Output ('RESULT:' + ($r -join ','))\n"
        )
        res = subprocess.run(
            [binary, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
        )
    marker = [ln for ln in res.stdout.splitlines() if ln.startswith("RESULT:")]
    return res, (marker[-1][len("RESULT:") :] if marker else None)


@unittest.skipUnless(_powershell_binaries(), "no PowerShell on this machine")
class TheRunningProjectProbeAnswersWithoutAskingTheOperator(unittest.TestCase):
    """LDM-#2132, behaviourally -- the static sweep's executed counterpart.

    `Get-RunningProjectsExcept` is the finalizer's gate on tearing down the
    shared proxy and search node, and its contract is that it answers
    `__unknown__` when it cannot tell. Two defects shipped in v2.26.5-pre.5
    broke that contract in opposite directions:

    - `ConvertFrom-LdmJson $raw` left the Mandatory `-Label` unsupplied, so
      PowerShell stopped and asked the operator for it on EVERY run;
    - the parse sat outside the `try`, and `ConvertFrom-LdmJson` ends its
      catch with a bare `throw`, so unparseable output escaped the function
      entirely. `Finalize-Verification` has no handler and calls
      `Restore-SharedInfrastructure` on its last line, so the proxy was left
      on whatever port the run had moved it to -- the exact outcome
      LDM-#2121 exists to prevent.

    Both are invisible to a test that greps the file for a function name,
    which is what the suite had. These drive the real function.
    """

    def _assert_no_prompt(self, res):
        """Both faces of the same defect.

        Interactively PowerShell PRINTS the first phrase and waits, which is
        what the operator saw. Under -NonInteractive it never asks: it writes
        the second phrase to stderr and leaves the variable `$null`, so the
        function returns `__unknown__` and the shared teardown is skipped on
        every run with nothing in the report to say why. Asserting only on the
        prompt would therefore pass against the broken script in exactly the
        mode CI runs in.
        """
        combined = res.stdout + res.stderr
        self.assertNotIn(
            "Supply values for the following parameters",
            combined,
            "the helper stopped to ask the operator for a parameter",
        )
        self.assertNotIn(
            "missing mandatory parameters",
            combined,
            "a call site left a Mandatory parameter unsupplied; the answer "
            "below is the binder failing, not the helper deciding",
        )

    def test_unparseable_output_answers_unknown_instead_of_throwing(self):
        """Docker stopped is not an exotic case -- it is how #2132 was found.

        `ldm list --json` printed a connection error, which is not JSON. The
        answer must be the sentinel, not an exception.
        """
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res, result = _run_get_running_projects_except(
                    binary,
                    "Write-Output 'failed to connect to the docker API'\n",
                )
                self._assert_no_prompt(res)
                self.assertEqual(
                    "__unknown__",
                    result,
                    f"stdout={res.stdout!r} stderr={res.stderr!r}",
                )

    def test_no_output_at_all_answers_unknown(self):
        """ConvertFrom-LdmJson throws its own error on empty input, so this
        reaches the catch by a different route from the one above."""
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res, result = _run_get_running_projects_except(binary, "\n")
                self._assert_no_prompt(res)
                self.assertEqual(
                    "__unknown__",
                    result,
                    f"stdout={res.stdout!r} stderr={res.stderr!r}",
                )

    def test_another_running_project_is_named(self):
        """And it must still do its job: the sentinel is not a way to pass."""
        payload = (
            'Write-Output \'[{"project":"other","status":"Running"},'
            '{"project":"mine","status":"Running"}]\'' + "\n"
        )
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res, result = _run_get_running_projects_except(binary, payload)
                self._assert_no_prompt(res)
                self.assertEqual(
                    "other", result, f"stdout={res.stdout!r} stderr={res.stderr!r}"
                )

    def test_only_our_own_project_running_licenses_the_cleanup(self):
        """An empty answer is what permits the shared teardown, so it has to
        be reachable -- a helper that always said `__unknown__` would pass the
        two tests above and never clean up anything."""
        payload = 'Write-Output \'[{"project":"mine","status":"Running"}]\'\n'
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res, result = _run_get_running_projects_except(binary, payload)
                self._assert_no_prompt(res)
                self.assertEqual(
                    "", result, f"stdout={res.stdout!r} stderr={res.stderr!r}"
                )


@unittest.skipUnless(_powershell_binaries(), "no PowerShell on this machine")
class TheProxyRestoreSurvivesAFailingFinalizer(unittest.TestCase):
    """LDM-#2132: the restore is in a `finally`, not merely on the last line.

    Three issues have now surfaced as "the proxy restore did not run":
    LDM-#2112 (it ran first and the recreate was refused), LDM-#2121 (it
    compared the machine's polluted port against itself), and LDM-#2132 (a
    terminating parameter-binding error above it). Each was fixed as an
    instance. Being last is what LDM-#2112 required and is also what made it
    fragile, so the ordering stays and the guarantee is added around it.

    The bash half has always had this: `cleanup_test_projects` opens with
    `set +e`, so no failure in the EXIT trap can abort it before
    `restore_shared_infrastructure`. PowerShell's
    `$ErrorActionPreference = "Continue"` covers ordinary cmdlet errors but
    not an explicit `throw`, so the halves did not really agree.
    """

    def _run_finalizer(self, body):
        func = _extract_function(
            PS1_SCRIPT,
            re.compile(r"^function Finalize-Verification\s*\{.*?^\}", re.M | re.S),
        )
        script = (
            f"function Invoke-FinalizerBody {{ param($ExitCode) {body} }}\n"
            "function Restore-SharedInfrastructure { Write-Output 'RESTORED' }\n"
            f"{func}\n"
            "try { Finalize-Verification 0 } catch { Write-Output 'THREW' }\n"
        )
        results = []
        for name, binary in _powershell_binaries():
            res = subprocess.run(
                [binary, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                check=False,
            )
            results.append((name, res))
        return results

    def test_a_throwing_body_still_reaches_the_restore(self):
        """The failing case. Without the `finally` the restore is skipped and
        the operator's proxy is left on whatever port the run moved it to."""
        for name, res in self._run_finalizer("throw 'boom'"):
            with self.subTest(shell=name):
                self.assertIn(
                    "RESTORED",
                    res.stdout,
                    f"the finalizer threw and never restored the proxy: "
                    f"stdout={res.stdout!r} stderr={res.stderr!r}",
                )

    def test_a_clean_body_still_reaches_the_restore(self):
        """And the ordinary path is unchanged -- a `finally` that only ran on
        failure would be worse than none."""
        for name, res in self._run_finalizer("Write-Output 'BODY'"):
            with self.subTest(shell=name):
                self.assertIn("BODY", res.stdout, res.stderr)
                self.assertIn("RESTORED", res.stdout, res.stderr)

    def test_the_restore_still_runs_after_the_body_not_before(self):
        """LDM-#2112 is still load-bearing: the recreate is refused while this
        run's project is up, so the restore must follow the teardown."""
        for name, res in self._run_finalizer("Write-Output 'BODY'"):
            with self.subTest(shell=name):
                lines = [
                    ln.strip()
                    for ln in res.stdout.splitlines()
                    if ln.strip() in ("BODY", "RESTORED")
                ]
                self.assertEqual(["BODY", "RESTORED"], lines, res.stdout)

    def test_the_body_is_not_also_called_from_the_old_name(self):
        """The rename has to be complete: a surviving call to the body under
        its old name would run the teardown twice."""
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(
            1,
            len(re.findall(r"^function Finalize-Verification\s*\{", text, re.M)),
        )
        self.assertEqual(
            1,
            len(re.findall(r"^function Invoke-FinalizerBody\s*\{", text, re.M)),
        )
        self.assertEqual(
            1,
            len(re.findall(r"(?<![\w-])Invoke-FinalizerBody(?![\w-])", text))
            - 1,  # minus the definition itself
            "Invoke-FinalizerBody should be called exactly once, by the wrapper",
        )


def _ps_code_for_variable_scan(text):
    """The script with comments and single-quoted string bodies removed.

    A single-quoted PowerShell string does not interpolate, so `$f` inside one
    is not a variable read. This script embeds whole bash programs that way --
    `$p`, `$f`, `$m`, `$back`, `$bad` all live inside single-quoted payloads
    passed to `docker exec` -- and treating them as PowerShell reads would
    bury the one finding that matters under five that do not exist.

    A literal single quote inside such a string is written by doubling it,
    which the scanner has to honour or the rest of the file is swallowed as
    string content.
    """
    out = []
    i, n = 0, len(text)
    quote = None
    while i < n:
        ch = text[i]
        if quote == "'":
            if ch == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                quote = None
                out.append(ch)
            elif ch == "\n":
                # Keep the line structure. The BODY is still dropped, so this
                # changes no finding -- it only means a reported line number
                # is the one a developer can open, rather than one shifted up
                # by every newline inside the embedded bash payloads.
                out.append(ch)
            i += 1
            continue
        if quote == '"':
            out.append(ch)
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch == "'":
            quote = "'"
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            quote = '"'
            out.append(ch)
            i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# PowerShell's own variables, which no script assigns.
_PS_AUTOMATIC_VARIABLES = frozenset(
    [
        "_",
        "args",
        "confirmpreference",
        "consolefilename",
        "debugpreference",
        "enabledexperimentalfeatures",
        "erroractionpreference",
        "error",
        "executioncontext",
        "false",
        "foreach",
        "home",
        "informationpreference",
        "input",
        "iscoreclr",
        "islinux",
        "ismacos",
        "iswindows",
        "lastexitcode",
        "matches",
        "myinvocation",
        "nestedpromptlevel",
        "null",
        "ofs",
        "outputencoding",
        "profile",
        "progresspreference",
        "psboundparameters",
        "pscommandpath",
        "psculture",
        "psemailserver",
        "pshome",
        "psitem",
        "psnativecommanduseerroractionpreference",
        "psscriptroot",
        "psuiculture",
        "psversiontable",
        "pwd",
        "shellid",
        "stacktrace",
        "switch",
        "true",
        "verbosepreference",
        "warningpreference",
        "host",
        "pid",
    ]
)


def _ps_unassigned_variables(text):
    """Every PowerShell variable the script reads and never assigns."""
    code = _ps_code_for_variable_scan(text)

    assigned = {
        m.group(1).lower()
        for m in re.finditer(
            r"\$(?:script:|global:|local:|private:)?([A-Za-z_]\w*)\s*=", code
        )
    }
    for pm in re.finditer(r"\bparam\s*\(", code, re.I):
        depth, i = 1, pm.end()
        while i < len(code) and depth:
            if code[i] == "(":
                depth += 1
            elif code[i] == ")":
                depth -= 1
            i += 1
        assigned |= {
            v.group(1).lower()
            for v in re.finditer(r"\$([A-Za-z_]\w*)", code[pm.end() : i - 1])
        }
    assigned |= {
        m.group(1).lower()
        for m in re.finditer(r"\bforeach\s*\(\s*\$([A-Za-z_]\w*)", code, re.I)
    }

    env = {m.group(1).lower() for m in re.finditer(r"\$env:([A-Za-z_]\w*)", code)}

    findings: dict[str, int] = {}
    # `env:` belongs in the prefix list, not the allowlist: without it
    # `$env:FOO` reads as a variable literally named `env`.
    for m in re.finditer(
        r"\$(?:script:|global:|local:|private:|using:|env:)?([A-Za-z_]\w*)", code
    ):
        name = m.group(1).lower()
        if name in assigned or name in env or name in _PS_AUTOMATIC_VARIABLES:
            continue
        findings.setdefault(name, code.count("\n", 0, m.start()) + 1)
    return sorted(findings.items())


class EveryPowerShellVariableReadIsAssigned(unittest.TestCase):
    """LDM-#2135: `$PROJECT_NAME` was read four times and assigned never.

    PowerShell does not error on an unassigned variable. It yields `$null`,
    which stringifies to empty, so each reader silently did something
    plausible and wrong:

    - the finalizer excluded nothing, counted the run's own project as another
      project, and skipped the shared teardown on EVERY Windows run;
    - two LDM-#1944 checks watched `<workspace>\\routes` rather than the
      project directory, so they reported `SKIPPED (not run)` after a 180s
      wait and had never actually run on Windows -- which also refutes the
      recorded diagnosis that those skips isolated to the Docker Desktop
      backend, since the WSL2 run that "passed" them executes the bash half;
    - the LDM-#2084 image sweep filtered on `reference=-*` and matched
      nothing.

    One missing assignment, four broken checks, three verification cycles, and
    every run reported pass.
    """

    def test_no_variable_is_read_without_being_assigned(self):
        findings = _ps_unassigned_variables(PS1_SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(
            [],
            findings,
            "read but never assigned (PowerShell yields $null, it does not "
            "error): "
            + "; ".join(f"${name} at line {line}" for name, line in findings),
        )

    def test_the_scanner_sees_a_missing_assignment(self):
        sample = (
            '$KNOWN = "yes"\n'
            "function Use-It {\n"
            "    param($Given)\n"
            '    Write-Host "$KNOWN $Given $MISSING"\n'
            "}\n"
        )
        self.assertEqual([("missing", 4)], _ps_unassigned_variables(sample))

    def test_the_scanner_ignores_single_quoted_payloads(self):
        """The five false positives this file would otherwise carry.

        Single-quoted strings do not interpolate, and this script passes whole
        bash programs through them.
        """
        sample = "$KNOWN = 'literal $notavariable here'\n"
        self.assertEqual([], _ps_unassigned_variables(sample))

        doubled = "$KNOWN = 'it''s fine $nope'\n$OTHER = $KNOWN\n"
        self.assertEqual([], _ps_unassigned_variables(doubled))

    def test_the_project_name_matches_the_one_passed_to_ldm(self):
        """The assignment is only right while it agrees with the literal the
        script already hands to `ldm rm`; nothing else ties them together."""
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        assigned = re.search(r'^\$PROJECT_NAME = "([^"]+)"', text, re.M)
        assert assigned is not None, "$PROJECT_NAME is not assigned"
        removed = re.findall(r"-y rm (\S+) --delete", text)
        self.assertTrue(removed, "the script no longer removes any project")
        self.assertIn(
            assigned.group(1),
            removed,
            f"$PROJECT_NAME is '{assigned.group(1)}' but the script removes "
            f"{removed} -- the variable names a project nothing tears down",
        )


# LDM-#2197: the automatic variables PowerShell supplies. Reading one before
# any assignment in this file is correct, not a defect.
_PS_AUTOMATIC = frozenset(
    [
        "true",
        "false",
        "null",
        "args",
        "input",
        "matches",
        "error",
        "this",
        "switch",
        "foreach",
        "psscriptroot",
        "pscommandpath",
        "psversiontable",
        "myinvocation",
        "lastexitcode",
        "psboundparameters",
        "host",
        "pwd",
        "home",
        "pid",
        "stacktrace",
        "shellid",
        "nestedpromptlevel",
        "executioncontext",
        "ofs",
        "iswindows",
        "ismacos",
        "islinux",
        "iscoreclr",
        "psedition",
        "psculture",
        "erroractionpreference",
        "progresspreference",
        "warningpreference",
        "informationpreference",
        "confirmpreference",
        "verbosepreference",
        "debugpreference",
        "env",
    ]
)

_PS_TOP_ASSIGN = re.compile(
    r"^\s*\$(?:script:|global:|local:|private:)?([A-Za-z_]\w*)\s*=(?!=)"
)
_PS_READ = re.compile(r"\$(?:script:|global:|local:|private:)?([A-Za-z_]\w*)")


def _ps_use_before_assignment(text):
    """Every top-level read that happens before its own first assignment.

    Brace depth, not line position, is the boundary. Top-level straight-line
    code runs in file order, so a read there binds whatever exists at that
    moment; a function body is deferred until something calls it, by which
    time the whole top-level block has run. This file interleaves the two --
    the first `function` is at line 26, well above the configuration block --
    so a scan keyed on position would report most of the script.
    """
    code = _ps_code_for_variable_scan(text)
    first_assigned, reads, depth = {}, [], 0

    for lineno, line in enumerate(code.split("\n"), 1):
        if depth == 0:
            match = _PS_TOP_ASSIGN.match(line)
            name = match.group(1).lower() if match else None
            if name and name not in first_assigned:
                first_assigned[name] = lineno
            for read in _PS_READ.findall(line[match.end() :] if match else line):
                reads.append((read.lower(), lineno))
        depth = max(0, depth + line.count("{") - line.count("}"))

    return [
        (name, lineno, first_assigned[name])
        for name, lineno in reads
        if name not in _PS_AUTOMATIC
        and name in first_assigned
        and first_assigned[name] > lineno
    ]


class EveryPowerShellVariableIsAssignedBeforeItIsRead(unittest.TestCase):
    """LDM-#2197: `$ROUNDTRIP_PKG_DIR` was built from `$LDM_WORKSPACE` 66
    lines before `$LDM_WORKSPACE` was assigned.

    `Join-Path` bound a null `-Path` and threw, non-terminating, so the run
    carried on with `$ROUNDTRIP_PKG_DIR` left `$null` until the `.ldmp` round
    trip tried to create it some 6300 lines later -- at the end of a
    multi-hour suite, after a full Liferay boot. The bash half had the two
    lines in the correct order, so only Windows was broken, and the round trip
    had never once run there.

    **Why the LDM-#2135 scanner beside this one could not see it.** That one
    asks whether a variable is assigned ANYWHERE in the file.
    `$LDM_WORKSPACE` is assigned, 66 lines too late, so it passes. The weaker
    question is the whole difference: `$PROJECT_NAME` was never assigned and
    was caught; `$LDM_WORKSPACE` was assigned out of order and was not. Same
    family, same `$null`, same silent plausible-but-wrong behaviour.
    """

    def test_no_top_level_read_precedes_its_assignment(self):
        findings = _ps_use_before_assignment(PS1_SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(
            [],
            findings,
            "read at top level before being assigned (PowerShell binds $null "
            "rather than erroring): "
            + "; ".join(
                f"${name} read at line {use}, first assigned at line {assigned}"
                for name, use, assigned in findings
            ),
        )

    def test_the_scanner_sees_the_defect_that_prompted_it(self):
        """The real ordering, reduced -- not a synthetic stand-in."""
        sample = (
            '$ROUNDTRIP_PKG_DIR = Join-Path $LDM_WORKSPACE ".ldm-roundtrip-pkg"\n'
            '$LDM_WORKSPACE = Join-Path $ORIGINAL_PWD "e2e-work-dir"\n'
        )
        self.assertEqual([("ldm_workspace", 1, 2)], _ps_use_before_assignment(sample))

    def test_a_function_body_may_read_what_is_assigned_below_it(self):
        """Deferred execution is the reason depth is the boundary.

        `Get-VersionBannerLines` sits at line 26 and legitimately reads
        configuration assigned a hundred lines further down, because nothing
        calls it until the whole top-level block has run. A scan keyed on line
        position would reject the file as it stands.
        """
        sample = (
            "function Show-It {\n"
            '    Write-Host "$LATER"\n'
            "}\n"
            '$LATER = "assigned after the function that reads it"\n'
        )
        self.assertEqual([], _ps_use_before_assignment(sample))

    def test_an_automatic_variable_is_not_a_finding(self):
        sample = '$RESULTS = Join-Path $PSScriptRoot "out.txt"\n'
        self.assertEqual([], _ps_use_before_assignment(sample))


_PS_STRING_ASSIGN = re.compile(r"^[^\S\n]*\$([A-Za-z_]\w*)\s*=\s*[\"']", re.M)
_PS_NUMERIC_ADD = re.compile(
    r"(\[(?:int|long|uint\d*|int\d+|byte|double|decimal)\]\s*)?"
    r"\$([A-Za-z_]\w*)\s*\+\s*\d"
)


def _ps_string_arithmetic(text):
    """Every `$VAR + <number>` where $VAR is somewhere assigned a string.

    PowerShell types `+` by its LEFT operand, so a string on the left
    concatenates and a number on the left adds. There is no warning and no
    error -- the result is a plausible-looking value of the wrong kind, which
    is why this has to be caught by reading the script rather than by running
    it. An explicit cast on the left is the fix, and its absence is the only
    thing this looks for.
    """
    code = _ps_code_for_variable_scan(text)
    stringy = {m.group(1).lower() for m in _PS_STRING_ASSIGN.finditer(code)}

    return [
        (m.group(2).lower(), code.count("\n", 0, m.start()) + 1)
        for m in _PS_NUMERIC_ADD.finditer(code)
        if m.group(1) is None and m.group(2).lower() in stringy
    ]


class NoStringIsUsedAsTheLeftOperandOfNumericAddition(unittest.TestCase):
    """LDM-#2213: `$TEST_PORT + 6` produced the port "80826".

    `$TEST_PORT` is assigned `"8082"` -- a string -- so the round trip asked
    `ldm import` for port 80826. LDM did exactly as it was told and wrote it
    into the compose file, where `docker compose config` refused it as an
    invalid hostPort. The failure therefore surfaced as an LDM defect, two
    pre-releases into a cycle, at the end of a multi-hour run.

    **The trap was already documented in the file it bit.** The translation
    notes above the shared-database check spell out this precise arithmetic
    -- "`$TEST_PORT + 3` is "80823", not 8085" -- and three call sites cast
    correctly because of it. The round trip, added last for LDM-#2131, is the
    one that did not. A comment is not a guard: it informs whoever reads it
    and says nothing to whoever does not.

    The bash half has no equivalent exposure. `$((TEST_PORT + 6))` is
    arithmetic by construction, so this is a parity defect of the kind that
    can only ever break Windows -- the same family as LDM-#2197, where the
    round trip had never once run there.
    """

    def test_no_string_variable_is_added_to_a_number(self):
        findings = _ps_string_arithmetic(PS1_SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(
            [],
            findings,
            "PowerShell concatenates when the left operand is a string; cast "
            "with [int] to add: "
            + "; ".join(f"${name} at line {line}" for name, line in findings),
        )

    def test_the_scanner_sees_the_defect_that_prompted_it(self):
        """The real two lines, reduced -- not a synthetic stand-in."""
        sample = '$TEST_PORT = "8082"\n$roundTripPort = $TEST_PORT + 6\n'
        self.assertEqual([("test_port", 2)], _ps_string_arithmetic(sample))

    def test_an_explicit_cast_is_accepted(self):
        sample = '$TEST_PORT = "8082"\n$naPort = [int]$TEST_PORT + 5\n'
        self.assertEqual([], _ps_string_arithmetic(sample))

    def test_a_numeric_variable_is_not_a_finding(self):
        sample = "$COUNT = 0\n$next = $COUNT + 1\n"
        self.assertEqual([], _ps_string_arithmetic(sample))

    def test_deliberate_concatenation_is_not_a_finding(self):
        """Building a string with `+` is legitimate and common in this script.

        Only a NUMERIC right operand is a finding, because only then was
        addition the evident intent.
        """
        sample = '$PROJ = "demo"\n$label = $PROJ + "-suffix"\n'
        self.assertEqual([], _ps_string_arithmetic(sample))

    def test_the_comment_that_documents_the_trap_is_not_itself_a_finding(self):
        """The scanner reads code, not the prose warning about the code."""
        sample = '$TEST_PORT = "8082"\n# `$TEST_PORT + 3` is "80823", not 8085\n'
        self.assertEqual([], _ps_string_arithmetic(sample))


def _occupied_port():
    """A port held open for the lifetime of the caller's `with` block."""
    s = socket.socket()
    s.bind(("", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def _run_bash_pick_free_port(preferred):
    func = _extract_function(
        BASH_SCRIPT, re.compile(r"^pick_free_port\s*\(\)\s*\{.*?^\}", re.M | re.S)
    )
    script = f'VENV_PYTHON=""\n{func}\npick_free_port {preferred}\n'
    res = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    return res, res.stdout.strip()


def _run_ps_free_port(binary, preferred):
    func = _extract_function(
        PS1_SCRIPT, re.compile(r"^function Get-FreeSslTestPort\s*\{.*?^\}", re.M | re.S)
    )
    script = f"{func}\nWrite-Output (Get-FreeSslTestPort -Preferred {preferred})\n"
    res = subprocess.run(
        [binary, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return res, res.stdout.strip()


class TheSslPortCheckDoesNotDemandAnOccupiedPort(unittest.TestCase):
    """LDM-#2136: the suite asked for 8443 and then demanded 8443.

    When something else holds it, LDM detects the conflict and selects 8444 --
    correct behaviour, and the same mechanism that prints "Global HTTP proxy
    port 80 is in use on the host. Using 81 instead." The check had no branch
    for it and exited 1, so the suite reported a product failure that had not
    happened. It aborted the WSL2 verification of v2.26.5-pre.5 at section 2.

    Not hypothetical: on that host `docker ps -a` was empty while ports 80 and
    18080 were permanently LISTENing, held by a non-Docker listener on the
    Windows side. A machine where LDM's own defaults are routinely taken is
    the machine where a hand-picked 8443 is taken sooner or later. Same shape
    as LDM-#2122 and LDM-#2123.

    The assertion stays strict deliberately -- accepting whatever LDM reported
    would pass if LDM ignored `--ssl-port` altogether, which is the thing
    being verified.
    """

    def test_neither_half_still_demands_the_literal_port(self):
        for path in (BASH_SCRIPT, PS1_SCRIPT):
            text = path.read_text(encoding="utf-8")
            body = "\n".join(
                ln for ln in text.splitlines() if not ln.strip().startswith("#")
            )
            self.assertNotIn(
                '"HostPort": "8443"',
                body,
                f"{path.name} still asserts the literal port",
            )

    def test_both_halves_resolve_a_port_before_asking_for_it(self):
        bash = BASH_SCRIPT.read_text(encoding="utf-8")
        self.assertLess(
            bash.index("SSL_TEST_PORT=$(pick_free_port 8443)"),
            bash.index('--ssl-port "$SSL_TEST_PORT"'),
            "the bash half asks before it resolves",
        )
        ps1 = PS1_SCRIPT.read_text(encoding="utf-8")
        self.assertLess(
            ps1.index("$SSL_TEST_PORT = Get-FreeSslTestPort -Preferred 8443"),
            ps1.index("--ssl-port $SSL_TEST_PORT"),
            "the PowerShell half asks before it resolves",
        )

    def test_bash_prefers_the_requested_port_when_it_is_free(self):
        sock, port = _occupied_port()
        sock.close()  # free again, and unlikely to be retaken immediately
        res, got = _run_bash_pick_free_port(port)
        self.assertEqual(str(port), got, f"stderr={res.stderr!r}")

    def test_bash_moves_off_a_port_that_is_held(self):
        sock, port = _occupied_port()
        try:
            res, got = _run_bash_pick_free_port(port)
            self.assertNotEqual(str(port), got, f"stderr={res.stderr!r}")
            self.assertTrue(got.isdigit(), f"got {got!r}, stderr={res.stderr!r}")
        finally:
            sock.close()

    @unittest.skipUnless(_powershell_binaries(), "no PowerShell on this machine")
    def test_powershell_prefers_the_requested_port_when_it_is_free(self):
        sock, port = _occupied_port()
        sock.close()
        for name, binary in _powershell_binaries():
            with self.subTest(shell=name):
                res, got = _run_ps_free_port(binary, port)
                self.assertEqual(str(port), got, f"stderr={res.stderr!r}")

    @unittest.skipUnless(_powershell_binaries(), "no PowerShell on this machine")
    def test_powershell_moves_off_a_port_that_is_held(self):
        sock, port = _occupied_port()
        try:
            for name, binary in _powershell_binaries():
                with self.subTest(shell=name):
                    res, got = _run_ps_free_port(binary, port)
                    self.assertNotEqual(str(port), got, f"stderr={res.stderr!r}")
                    self.assertTrue(got.isdigit(), f"got {got!r}")
        finally:
            sock.close()


class TheFailureLogsCoverTheContainerThatActuallyFailed(unittest.TestCase):
    """LDM-#2216: a round-trip boot failure dumped the SOURCE project's logs.

    On macOS at v2.26.6-pre.8 the `.ldmp` round trip failed with compose
    reporting `dependency failed to start ... is unhealthy`. The report's
    debug section carried three log dumps, none of them the container that
    failed -- the round-trip project was absent from the capture list, so
    what landed was the source project's log, stamped an hour before the
    failure it was supposed to explain.

    Three defects, each of which looked like it worked:

    * ``${PROJECT_NAME}-db-1`` never matched. LDM sets an explicit
      ``container_name`` (``composer.py``: ``f"{project_name}-db"``), so
      compose does not append its ``-1`` index. Database logs had never been
      captured on any platform.
    * ``${ROUNDTRIP_PROJECT}`` was absent entirely.
    * ``docker ps -a | grep -q "$container"`` matched a SUBSTRING, so
      ``${PROJECT_NAME}`` was satisfied by ``${PROJECT_NAME}-roundtrip``.
      The guard passed, ``docker logs`` resolved the exact name only, and
      nothing looked wrong. The same family as LDM-#2197's anchoring bug.

    And the PowerShell half captured nothing at all, which is why the pre.7
    Windows round-trip failure had to be reproduced by hand.
    """

    @staticmethod
    def _capture_body(path, start_marker):
        code = path.read_text(encoding="utf-8")
        at = code.find(start_marker)
        assert at != -1, (
            f"{path.name} has no {start_marker!r} -- the failure capture is "
            "gone entirely, which is the state LDM-#2216 found the "
            "PowerShell half in"
        )
        return code[at : at + 3000]

    def _bodies(self):
        """The per-container MECHANICS, wherever they live.

        LDM-#2221 moved them into a helper that takes names, because the
        finalizer cannot capture a project the round trip has already deleted.
        The assertions below are about HOW a container is inspected, not about
        which ones -- that is TheRoundTripIsCapturedBeforeItIsDestroyed.
        """
        return (
            (
                "bash",
                self._capture_body(BASH_SCRIPT, "capture_container_diagnostics() {"),
            ),
            (
                "ps1",
                self._capture_body(PS1_SCRIPT, "function Write-FailureDebugLogs {"),
            ),
        )

    def test_both_halves_capture_the_round_trip_project(self):
        """Captured at the point of failure now, not from the finalizer."""
        for half, path in (("bash", BASH_SCRIPT), ("ps1", PS1_SCRIPT)):
            text = path.read_text(encoding="utf-8")
            at = text.find("ROUND TRIP CONTAINER STATE")
            self.assertNotEqual(-1, at, f"{half}: round trip not captured at all")
            self.assertIn(
                "ROUNDTRIP_PROJECT", text[at : at + 700], f"{half}: not by name"
            )

    def test_neither_half_asks_for_a_db_container_that_cannot_exist(self):
        """`-db-1` is the name compose picks when nothing sets container_name.

        LDM always sets it, so this suffix matched nothing for the whole life
        of the helper -- a capture list entry that silently captured nothing.
        """
        for half, body in self._bodies():
            self.assertNotIn("-db-1", body, f"{half}: compose never names it that")
            self.assertIn("-db", body, f"{half}: database not captured at all")

    def test_both_halves_match_a_container_name_exactly(self):
        """An unanchored match lets a project be satisfied by its own sibling."""
        bash, ps1 = (b for _, b in self._bodies())
        self.assertIn("--format '{{.Names}}'", bash)
        self.assertIn("grep -qxF", bash)
        self.assertNotIn("docker ps -a | grep", bash)
        self.assertIn("-ceq", ps1)

    def test_both_halves_record_the_state_not_only_the_log(self):
        """`unhealthy` and `OOMKilled` appear nowhere in `docker logs`.

        A portal killed for memory, and one still booting happily when compose
        gave up waiting, produce the same log tail. The state line is the only
        thing that separates them -- and that separation decides whether a fix
        belongs in a script or in `ldm_core`.
        """
        for half, body in self._bodies():
            self.assertIn("docker inspect", body, f"{half}: no state captured")
            self.assertIn("OOMKilled", body, f"{half}: cannot see a memory kill")
            self.assertIn("Health", body, f"{half}: cannot see the healthcheck")

    def test_the_powershell_half_runs_it_on_the_failure_path(self):
        """It is not enough to define it. LDM-#2135's family of defect."""
        code = PS1_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'if ($status -eq "fail") { Write-FailureDebugLogs }',
            code,
            "defined but never called",
        )


class TheRoundTripIsCapturedBeforeItIsDestroyed(unittest.TestCase):
    """LDM-#2221: LDM-#2216 named the round-trip containers and never saw one.

    That fix added `${ROUNDTRIP_PROJECT}` to the failure-log capture, and the
    capture runs from the FINALIZER -- at the end of the script. The round trip
    removes its own project six lines after reporting the failure, so by then
    there is nothing to inspect. Three reports on three platforms carried the
    `--- FAILURE DEBUG LOGS ---` heading with no round-trip container under it,
    which is indistinguishable from the project never having been created.

    The cost was a whole pre-release. `v2.26.6-pre.9` existed largely to answer
    one question -- was the macOS round-trip failure a host out of memory or an
    `ldm_core` defect -- and it came back unable to answer it, because
    `oom-killed` lives on a container that had already been deleted.

    Nothing in LDM-#2216's own tests could see this: they assert that the names
    appear in the capture list, and they do. The defect is **ordering**, which
    is the same shape as LDM-#2197 -- a value read before it was assigned,
    passing every test that asked whether it was assigned at all.
    """

    @staticmethod
    def _positions(text, *markers):
        return [text.find(m) for m in markers]

    def test_bash_captures_the_round_trip_before_removing_it(self):
        text = BASH_SCRIPT.read_text(encoding="utf-8")
        capture, cleanup = self._positions(
            text,
            "--- ROUND TRIP CONTAINER STATE (captured before cleanup) ---",
            "# Removed whether or not it passed: ~1 GB per run",
        )
        self.assertNotEqual(-1, capture, "the round trip captures no state at all")
        self.assertNotEqual(
            -1, cleanup, "the round-trip cleanup moved; re-anchor this test"
        )
        self.assertLess(
            capture,
            cleanup,
            "the round trip is removed before its state is captured, so the "
            "report explains nothing -- exactly the LDM-#2216 defect",
        )

    def test_powershell_captures_the_round_trip_before_removing_it(self):
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        capture, cleanup = self._positions(
            text,
            "--- ROUND TRIP CONTAINER STATE (captured before cleanup) ---",
            "# Removed whether or not it passed: ~1 GB per run",
        )
        self.assertNotEqual(-1, capture, "the round trip captures no state at all")
        self.assertNotEqual(
            -1, cleanup, "the round-trip cleanup moved; re-anchor this test"
        )
        self.assertLess(capture, cleanup, "removed before captured")

    def test_both_halves_capture_the_services_that_gate_the_boot(self):
        """`derivedsvc`/`syntheticsvc` are what compose was waiting ON.

        The macOS failure is `dependency failed to start ... is unhealthy` --
        reported against the dependents, not against Liferay. Capturing only
        the portal would omit the containers the message names.
        """
        for half, path in (("bash", BASH_SCRIPT), ("ps1", PS1_SCRIPT)):
            text = path.read_text(encoding="utf-8")
            at = text.find("ROUND TRIP CONTAINER STATE")
            window = text[at : at + 700]
            for svc in ("-db", "-derivedsvc", "-syntheticsvc"):
                self.assertIn(svc, window, f"{half}: {svc} not captured")

    def test_the_finalizer_no_longer_claims_the_round_trip(self):
        """Listing it there is what made the omission look like coverage."""
        text = BASH_SCRIPT.read_text(encoding="utf-8")
        at = text.find("capture_logs_on_failure() {")
        self.assertNotEqual(-1, at)
        body = text[at : at + 600]
        self.assertNotIn(
            "${ROUNDTRIP_PROJECT}",
            body,
            "the finalizer still lists the round trip, which cannot exist by "
            "the time it runs -- a capture list that silently matches nothing",
        )


class NeitherHalfRefusesARedirectWhenItAsksThePortal(unittest.TestCase):
    """LDM-#2227: the two halves asked the same URL different questions.

    The shell half probes with `urllib.request.urlopen`, which FOLLOWS
    redirects, and both of its call sites accept the followed result (`2*|3*`
    for LDM-#1574, `200 | 302` for the round trip). The PowerShell half passed
    `-MaximumRedirection 0`, which does not hand back a 3xx response -- it
    raises a terminating error, leaving the status to be recovered from
    `$_.Exception.Response`.

    That recovery is not version-portable. PowerShell 7 is HttpClient-based
    and populates `.Response` for the blocked redirect; Windows PowerShell 5.1
    is HttpWebRequest-based and does not, so the probe fell through to its
    `"000"` initialiser. `/c/portal/layout` 302-redirects an anonymous GET and
    `/` does not, which is precisely why `v2.26.6-pre.9` failed the round trip
    on 5.1 while the LDM-#1574 probe passed in the same run, and why PS 7 on
    the same machine and port reported 302.

    Measured on pwsh 7.6.7 against a server mimicking Liferay's redirect:
    following turns the 302 into a 200 and leaves 200/500/refused unchanged.

    The second assertion here is about diagnosis rather than correctness. The
    old `catch` discarded `$_.Exception.Message`, so a refused connection, a
    timeout and an unreadable redirect were all the same bare `'000'` -- which
    is why pre.9's failure needed a second run on a different shell to
    interpret at all.
    """

    @staticmethod
    def _uncommented(text):
        """PowerShell lines with the comment tail removed.

        The fix's own explanation names `-MaximumRedirection 0`, so a plain
        substring search over the file would match the comment that documents
        why it is gone -- a test that fails on its own rationale.
        """
        out = []
        for line in text.splitlines():
            stripped = line.split("#", 1)[0]
            if stripped.strip():
                out.append(stripped)
        return "\n".join(out)

    def test_no_powershell_probe_refuses_redirects(self):
        code = self._uncommented(PS1_SCRIPT.read_text(encoding="utf-8"))
        self.assertNotIn(
            "-MaximumRedirection",
            code,
            "a PowerShell probe refuses redirects again: on Windows "
            "PowerShell 5.1 the blocked 3xx is unreadable and reads as '000', "
            "which is LDM-#2227",
        )

    def test_both_portal_probes_go_through_the_shared_helper(self):
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "function Get-HttpStatus {",
            text,
            "the shared probe helper is gone; the twin of http_status()",
        )
        # Both assertions that ask a portal whether it serves, by the issue
        # each exists for.
        for marker in (
            "$urlProbe = Get-HttpStatus -Uri $statusUrl",
            "$roundTripProbe = Get-HttpStatus -Uri "
            '"http://localhost:$roundTripPort/c/portal/layout"',
        ):
            self.assertIn(
                marker,
                text,
                f"a probe stopped using Get-HttpStatus: {marker!r}. A raw "
                "Invoke-WebRequest here is how the 5.1 divergence returns",
            )

    def test_the_shell_half_still_follows_redirects(self):
        """The other side of the parity claim, asserted rather than assumed."""
        text = BASH_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "urllib.request.urlopen",
            text,
            "http_status() no longer uses urlopen; if it has moved to "
            "something that refuses redirects, the PowerShell half is now the "
            "one asking the right question and this parity is inverted",
        )

    def test_a_failed_probe_says_why_it_failed(self):
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "$note = $_.Exception.Message",
            text,
            "Get-HttpStatus discards the exception message again, so every "
            "failure mode renders as a bare '000' (LDM-#2227)",
        )
        self.assertIn(
            "The probe reported: $($roundTripProbe.Note)",
            text,
            "the round-trip failure verdict no longer reports the reason",
        )
        self.assertIn(
            "The probe reported: $($urlProbe.Note)",
            text,
            "the access-URL failure no longer reports the reason",
        )


class BothHalvesReapOnlyWhatTheRunItselfCreated(unittest.TestCase):
    """LDM-#2214: a verification run should leave the host as it found it.

    Two mechanisms already existed and neither covered it. The LDM-#2084 sweep
    removes images compose BUILT, matched on the run's own project name.
    `--prune-after` runs `docker system prune -af --volumes`, which is
    machine-wide and is correctly off by default -- it would re-cost a 5 GB
    Liferay pull on the next run. The gap was everything *pulled* rather than
    built, plus any volume outliving the project that made it.

    The design is a diff, not a prune: inventory images and volumes before
    anything is pulled, inventory again at the end, remove only the
    difference. That is what makes it safe as a default. Anything already on
    the host is in the start inventory and is never touched, so a warm cache
    survives and so does a concurrent project's data -- a distinction a
    machine-wide prune cannot make.

    Two properties this asserts beyond "the code is present", because both are
    the kind of thing that reads correct and behaves wrongly:

    - the inventory is taken by image **ID**, not tag. A tag the run merely
      re-pointed is not a download, and a re-pulled tag whose digest was
      already present must not be reaped.
    - the reap is gated on a PASS. A failed run is about to be re-run, and
      re-downloading DXP before each diagnosis costs more than the disk.
    """

    def test_both_halves_snapshot_before_pulling(self):
        bash = BASH_SCRIPT.read_text(encoding="utf-8")
        ps1 = PS1_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "INVENTORY_START_IMAGES", bash, "the shell half takes no image inventory"
        )
        self.assertIn(
            "INVENTORY_START_VOLUMES", bash, "the shell half takes no volume inventory"
        )
        self.assertIn(
            "InventoryStartImages", ps1, "the PowerShell half takes no image inventory"
        )
        self.assertIn(
            "InventoryStartVolumes",
            ps1,
            "the PowerShell half takes no volume inventory",
        )

    def test_every_image_inventory_is_taken_by_full_id(self):
        """`--no-trunc` on `image ls -q` is what makes it a full ID.

        Asserted over EVERY occurrence, not merely that the string appears
        somewhere. The first version of this test checked only that
        `docker image ls -q --no-trunc` was present in the file, and passed
        against a snapshot that had been edited down to `docker image ls -q`
        -- because the other three call sites still carried the flag. A reap
        comparing truncated IDs against full ones finds every image "new".
        """
        for script in (BASH_SCRIPT, PS1_SCRIPT):
            text = script.read_text(encoding="utf-8")
            occurrences = [
                line.strip()
                for line in text.splitlines()
                if "docker image ls -q" in line
            ]
            self.assertTrue(occurrences, f"{script.name} inventories no images at all")
            bare = [line for line in occurrences if "--no-trunc" not in line]
            self.assertEqual(
                [],
                bare,
                f"{script.name} inventories images without --no-trunc, so it "
                "compares truncated IDs against full ones and treats every "
                "image as new (LDM-#2214)",
            )

    def test_the_snapshot_is_taken_before_anything_is_pulled(self):
        """Ordering -- but of the PULL, not of the reap.

        Note what this deliberately does NOT assert. The reap lives inside
        `cleanup_test_projects()`, which is DEFINED near the top of the file
        and CALLED from the trap at the very end, so the reap's text sits
        above the snapshot's while executing long after it. An ordering test
        anchored on the reap therefore fails against correct code -- it did,
        when this test was first written that way.

        What has to hold is that the inventory is taken before the pre-pull.
        Taken after, the delta is empty and the reap is a silent no-op that
        every other assertion here still passes.
        """
        for script, snapshot_marker in (
            (BASH_SCRIPT, "INVENTORY_START_IMAGES="),
            (PS1_SCRIPT, "$script:InventoryStartImages = @("),
        ):
            text = script.read_text(encoding="utf-8")
            snapshot = text.find(snapshot_marker)
            pull = text.find("Pre-pull large images")
            self.assertNotEqual(-1, snapshot, f"{script.name}: no inventory; re-anchor")
            self.assertNotEqual(
                -1, pull, f"{script.name}: no pre-pull; re-anchor this test"
            )
            self.assertLess(
                snapshot,
                pull,
                f"{script.name} inventories Docker AFTER the pre-pull, so every "
                "pulled image is already in the start inventory, the delta is "
                "empty, and the reap silently removes nothing (LDM-#2214)",
            )

    def test_both_halves_reap_only_on_a_pass(self):
        bash = BASH_SCRIPT.read_text(encoding="utf-8")
        ps1 = PS1_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'if [ "$status" = "pass" ] && [ "$KEEP_PULLED" != true ]; then',
            bash,
            "the shell reap is no longer gated on a passing run, so a failure "
            "now deletes the images its own re-run needs (LDM-#2214)",
        )
        self.assertIn(
            "if ($script:VerificationExitCode -eq 0 -and -not $KeepPulled) {",
            ps1,
            "the PowerShell reap is no longer gated on a passing run",
        )

    def test_neither_half_forces_the_removal(self):
        """`docker rmi -f` would untag an image a running container still uses.

        The LDM-#2084 sweep above does force, and is right to: it matches only
        images this run's own compose built. This one matches anything new on
        the host, so the blast radius is different and the refusal is wanted.
        """
        bash = BASH_SCRIPT.read_text(encoding="utf-8")
        ps1 = PS1_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'docker rmi "$img"', bash, "the shell reap no longer removes images by ID"
        )
        self.assertNotIn(
            'docker rmi -f "$img"', bash, "the shell reap forces removal (LDM-#2214)"
        )
        self.assertIn(
            "& docker rmi $img 2>&1",
            ps1,
            "the PowerShell reap no longer removes images",
        )
        self.assertNotIn(
            "docker rmi -f $img", ps1, "the PowerShell reap forces removal"
        )

    def test_both_halves_offer_the_same_opt_out(self):
        bash = BASH_SCRIPT.read_text(encoding="utf-8")
        ps1 = PS1_SCRIPT.read_text(encoding="utf-8")
        # LDM-#2241 replaced the chain of equality tests with a `case`, so
        # the accept ARM is what to pin -- the old `"$arg" == ...` form is
        # gone. TheHarnessRefusesUnknownArguments below executes the parser;
        # this one only asserts the two halves still offer the same opt-out.
        self.assertIn("--keep-pulled)", bash, "the shell half has no --keep-pulled arm")
        self.assertIn(
            "[switch]$KeepPulled", ps1, "the PowerShell half has no -KeepPulled"
        )


def _block_after(text, open_pattern, close_pattern="^}"):
    """Lines of a column-zero block, from its opening line to its closing brace.

    Both scripts declare their top-level functions and their top-level
    try/catch/finally at column zero and close them the same way, so the
    extent is unambiguous without parsing braces -- which would have to cope
    with braces inside strings.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(open_pattern, line):
            start = index
            break
    if start is None:
        raise AssertionError(f"no line matching {open_pattern!r}")
    for index in range(start + 1, len(lines)):
        if re.match(close_pattern, lines[index]):
            return lines[start : index + 1], start
    raise AssertionError(f"block opened by {open_pattern!r} is never closed")


class EveryLineAfterFinalisationReachesTheReport(unittest.TestCase):
    """LDM-#2238: the report is moved, then written to at the old path.

    `cleanup_test_projects` finalises the report -- and takes the archived
    copy that becomes the compatibility matrix -- and then keeps writing to
    `RESULTS_FILE_TMP`, which no longer exists. `tee -a` recreates it, so the
    text lands in a dotfile nothing reads. Measured over the 224 reports in
    references/verification-results: `Disk: this run consumed` appears in
    none of them, and neither does `image(s) built by this run`, while the
    one line in the same region that uses the helper appears in 12 of the 14
    current reports.

    The PowerShell half drops the same text two ways. Its finalizer calls
    `Write-Verdict`, which appends unconditionally to the moved path. Its
    top-level `finally` -- which runs after the report has been archived --
    used bare `Write-Host`, which reaches no file at all, so on Windows the
    text was not merely misfiled but unrecoverable.

    What this costs is not cosmetic. LDM-#2214's reap, LDM-#2084's built-image
    sweep and LDM-#1438's per-run disk figure are each reported for exactly
    one reason, written in their own comments: a cleanup that removes nothing
    must be visible, because an `rmi` that silently removed nothing is how
    LDM-#2084 hid. None of the three has ever appeared in a report.

    This asserts the mechanism, not the message. A test that looked for the
    text would pass against the defect -- the text is present and correct, it
    is the destination that is wrong -- which is the failure mode the release
    skill names as reading the source rather than running it. So the
    invariant here is positional: after the line that moves the report,
    nothing in that block may name the moved path as a write target, and
    every message must go through the helper that resolves the destination.
    """

    # Writes, not mentions. The fix leaves comments naming the path, and a
    # removal of the stale path would also be legitimate; only redirection
    # into it is the defect.
    _BASH_WRITES = (
        r'tee\s+-a\s+"\$RESULTS_FILE_TMP"',
        r'>>\s*"\$RESULTS_FILE_TMP"',
        r'>\s*"\$RESULTS_FILE_TMP"',
    )

    def test_the_bash_trap_never_writes_to_the_moved_path(self):
        text = BASH_SCRIPT.read_text(encoding="utf-8")
        block, offset = _block_after(text, r"^cleanup_test_projects\(\) \{")

        moved = [i for i, line in enumerate(block) if 'mv "$RESULTS_FILE_TMP"' in line]
        self.assertEqual(
            len(moved),
            1,
            "expected exactly one line moving the report out of the temp path",
        )

        offenders = []
        for index in range(moved[0] + 1, len(block)):
            line = block[index]
            if line.lstrip().startswith("#"):
                continue
            for pattern in self._BASH_WRITES:
                if re.search(pattern, line):
                    offenders.append(f"{offset + index + 1}: {line.strip()}")
                    break

        self.assertEqual(
            offenders,
            [],
            "these lines write to the report's temp path after it has been "
            "moved away, so the text reaches a recreated dotfile instead of "
            "the report (LDM-#2238). Route them through trap_report:\n"
            + "\n".join(offenders),
        )

    def test_the_powershell_finalizer_never_writes_to_the_moved_path(self):
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        block, offset = _block_after(text, r"^function Invoke-FinalizerBody \{")

        moved = [
            i for i, line in enumerate(block) if "Move-Item $RESULTS_FILE_TMP" in line
        ]
        self.assertEqual(len(moved), 1, "expected exactly one Move-Item of the report")

        offenders = []
        for index in range(moved[0] + 1, len(block)):
            line = block[index]
            if line.lstrip().startswith("#"):
                continue
            # Write-Verdict appends unconditionally to RESULTS_FILE_TMP, so it
            # is as wrong here as a direct Out-File at that path.
            if re.search(
                r"Write-Verdict|Out-File\s+-FilePath\s+\$RESULTS_FILE_TMP", line
            ):
                offenders.append(f"{offset + index + 1}: {line.strip()}")

        self.assertEqual(
            offenders,
            [],
            "these lines append to the report's temp path after Move-Item has "
            "renamed it, so Out-File recreates a dotfile nothing reads "
            "(LDM-#2238). Route them through Write-TrapNote:\n" + "\n".join(offenders),
        )

    def test_the_powershell_finally_block_reaches_a_file_at_all(self):
        text = PS1_SCRIPT.read_text(encoding="utf-8")
        block, offset = _block_after(text, r"^\} finally \{")

        offenders = []
        for index, line in enumerate(block):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"\bWrite-Host\b", line):
                offenders.append(f"{offset + index + 1}: {line.strip()}")

        self.assertEqual(
            offenders,
            [],
            "Write-Host in the top-level finally reaches the console and no "
            "file. This block runs after the report has been archived, so its "
            "text -- the whole of the LDM-#2214 reap on Windows -- is "
            "unrecoverable once the console scrolls (LDM-#2238). Use "
            "Write-TrapNote:\n" + "\n".join(offenders),
        )

    def test_both_halves_record_why_the_preflight_refused(self):
        """A -fail report that does not say what failed (LDM-#2238).

        The LDM-#2085 pre-flight refuses to run beside a live project. It is
        the earliest thing that can fail a run, and it wrote to stderr only --
        so the report for a refused run carried an exit code, a note about
        cleanup it skipped, and no cause. One such report was produced on
        v2.26.6-pre.10. That is the defect LDM-#1255 and LDM-#1440 each
        removed from a later call site.
        """
        bash = BASH_SCRIPT.read_text(encoding="utf-8")
        ps1 = PS1_SCRIPT.read_text(encoding="utf-8")

        bash_refusal = [
            line
            for line in bash.splitlines()
            if "other LDM projects are running:" in line and "echo" in line
        ]
        self.assertTrue(bash_refusal, "the shell pre-flight refusal has moved")
        for line in bash_refusal:
            self.assertIn(
                'tee -a "$RESULTS_FILE_TMP"',
                line,
                "the shell pre-flight refusal goes to stderr only, so a run it "
                f"refuses writes a report naming no cause: {line.strip()}",
            )

        ps_refusal = [
            line
            for line in ps1.splitlines()
            if "other LDM projects are running:" in line
        ]
        self.assertTrue(ps_refusal, "the PowerShell pre-flight refusal has moved")
        for line in ps_refusal:
            self.assertNotIn(
                "Write-Host",
                line,
                "the PowerShell pre-flight refusal reaches no file, so a run it "
                f"refuses writes a report naming no cause: {line.strip()}",
            )


class TheReportHelpersDeliverAcrossFinalisation(unittest.TestCase):
    """The other half of LDM-#2238: do the helpers actually deliver?

    The class above asserts that every call site routes through the helper.
    That is necessary and not sufficient -- it would pass just as happily if
    the helper itself wrote nowhere, which is the whole defect one level up.
    So these extract `trap_report` and `Write-TrapNote` and run them for real,
    across the move that finalises the report, and read the file back.

    The move is the part worth simulating. Before LDM-#2238 every one of these
    messages was emitted after `mv`/`Move-Item` had renamed the path they were
    addressed to, and `tee -a` and `Out-File -Append` both *create* a missing
    file rather than failing -- which is exactly why the loss was silent and
    survived 224 reports.
    """

    def test_bash_trap_report_follows_the_renamed_report(self):
        func = _extract_function(
            BASH_SCRIPT, re.compile(r"^trap_report\(\) \{.*?^\}", re.M | re.S)
        )
        with tempfile.TemporaryDirectory() as tmp:
            script = f"""{func}
RESULTS_FILE_TMP="{tmp}/tmp-report.txt"
echo "header" > "$RESULTS_FILE_TMP"
trap_report "before the move"
FINAL_REPORT_PATH="{tmp}/final-report.txt"
mv "$RESULTS_FILE_TMP" "$FINAL_REPORT_PATH"
trap_report "Removing 2 volume(s) created by this run..."
trap_report "$(printf 'multi\\nline\\n' | sed 's/^/     /')"
[ -f "$RESULTS_FILE_TMP" ] && echo "TEMP-RECREATED"
exit 0
"""
            res = subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True, check=False
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn(
                "TEMP-RECREATED",
                res.stdout,
                "trap_report recreated the moved-away temp path",
            )

            final = Path(tmp, "final-report.txt").read_text(encoding="utf-8")
            self.assertIn("before the move", final)
            self.assertIn(
                "Removing 2 volume(s) created by this run...",
                final,
                "the reap's line did not reach the finalised report",
            )
            # The piped call sites pass sed's output as one argument; the
            # indentation it adds is the readable part and must survive.
            self.assertIn("     multi\n     line", final)

            # Still printed for the operator, not only filed.
            self.assertIn("Removing 2 volume(s) created by this run...", res.stdout)

    @unittest.skipIf(_pwsh_binary() is None, "no PowerShell on this machine")
    def test_powershell_write_trapnote_follows_the_renamed_report(self):
        func = _extract_function(
            PS1_SCRIPT, re.compile(r"^function Write-TrapNote \{.*?^\}", re.M | re.S)
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_report = Path(tmp, "tmp-report.txt").as_posix()
            final_report = Path(tmp, "final-report.txt").as_posix()
            script = f"""{func}
$RESULTS_FILE_TMP = "{tmp_report}"
"header" | Out-File -FilePath $RESULTS_FILE_TMP -Encoding utf8
Write-TrapNote "before the move"
Move-Item $RESULTS_FILE_TMP "{final_report}" -Force
$script:FINAL_REPORT_PATH = "{final_report}"
Write-TrapNote "Removing 2 volume(s) created by this run..."
Write-TrapNote "a coloured warning" -Color Yellow
if (Test-Path $RESULTS_FILE_TMP) {{ Write-Output "TEMP-RECREATED" }}
"""
            binary = _pwsh_binary()
            res = subprocess.run(
                [binary, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn(
                "TEMP-RECREATED",
                res.stdout,
                "Write-TrapNote recreated the moved-away temp path",
            )

            final = Path(tmp, "final-report.txt").read_text(encoding="utf-8")
            self.assertIn("before the move", final)
            self.assertIn(
                "Removing 2 volume(s) created by this run...",
                final,
                "the reap's line did not reach the finalised report",
            )
            # -Color is new (LDM-#2238) and exists so the callers that used
            # Write-Host only to colour a warning could move over. A wrong
            # signature here is a mandatory-parameter prompt in a non-
            # interactive run, which is how LDM shipped one in pre.5 -- so the
            # colour path is executed, not merely named.
            self.assertIn("a coloured warning", final)


def _run_bash_arg_parser(*argv):
    """Execute the shell half's argument loop for real (LDM-#2241).

    Extracted and run rather than grepped: the defect was that an unknown
    argument was ACCEPTED silently, and no amount of reading the source
    distinguishes a parser that refuses from one that happens to mention the
    flag in a comment.
    """
    text = BASH_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'^for arg in "\$@"; do.*?^done$', text, re.M | re.S)
    assert match, "the shell argument loop was not found"
    flags = ("KEEP_ARTIFACTS", "PRUNE_AFTER", "KEEP_PULLED", "ALLOW_VERSION_MISMATCH")
    preamble = "".join(f"{name}=false\n" for name in flags)
    report = " ".join(f"{name}=${name}" for name in flags)
    script = f'{preamble}{match.group(0)}\necho "{report}"\n'

    return subprocess.run(
        ["bash", "-c", script, "bash", *argv],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_powershell_arg_guard(*argv):
    """Execute the PowerShell half's param block and guards, via -File.

    -File matters: that is how the suite is invoked, and it is the mode in
    which PowerShell does NOT reject unknown named parameters.
    """
    text = PS1_SCRIPT.read_text(encoding="utf-8")
    head = text.split("# Target: Verifies the INSTALLED binary", 1)[0]
    with tempfile.NamedTemporaryFile(
        "w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(
            head + '\nWrite-Output "FELL-THROUGH KeepPulled=$KeepPulled '
            'Avm=$AllowVersionMismatch"\n'
        )
        path = handle.name
    try:
        return subprocess.run(
            [_pwsh_binary(), "-NoProfile", "-NonInteractive", "-File", path, *argv],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.unlink(path)


class TheHarnessRefusesUnknownArguments(unittest.TestCase):
    """LDM-#2241: an unrecognised argument was silently discarded.

    `--keep` is used at exactly one moment -- when a run has failed and
    someone wants the wreckage preserved -- and that is the one time the
    artefacts cannot be recreated, because a re-run draws a new random
    TEST_PORT and a fresh project. A mistyped `-keep` therefore destroyed the
    only copy of the evidence the flag exists to protect, and the person found
    out two hours later once teardown had run.
    """

    def test_the_documented_flags_are_accepted(self):
        for flag, marker in (
            ("--keep", "KEEP_ARTIFACTS=true"),
            ("-k", "KEEP_ARTIFACTS=true"),
            ("--prune-after", "PRUNE_AFTER=true"),
            ("--keep-pulled", "KEEP_PULLED=true"),
            ("--allow-version-mismatch", "ALLOW_VERSION_MISMATCH=true"),
        ):
            with self.subTest(flag=flag):
                res = _run_bash_arg_parser(flag)
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertIn(marker, res.stdout)

    def test_a_single_dash_keep_is_refused(self):
        # The reported slip. One character from `--keep`, and single-dash long
        # options are accepted by enough tools (-version, -help) that typing
        # it is not unreasonable.
        res = _run_bash_arg_parser("-keep")
        self.assertEqual(res.returncode, 1)
        self.assertIn("-keep", res.stderr)
        self.assertNotIn("KEEP_ARTIFACTS=true", res.stdout)

    def test_an_unknown_flag_is_refused_naming_itself(self):
        res = _run_bash_arg_parser("--nonsense")
        self.assertEqual(res.returncode, 1)
        self.assertIn("--nonsense", res.stderr)

    def test_the_refusal_lists_what_is_accepted(self):
        res = _run_bash_arg_parser("--nonsense")
        self.assertIn("--keep", res.stderr)
        self.assertIn("--allow-version-mismatch", res.stderr)


@unittest.skipUnless(_pwsh_binary(), "pwsh/powershell not available on this machine")
class ThePowerShellHarnessRefusesUnknownArguments(unittest.TestCase):
    """LDM-#2241, PowerShell half -- which was worse, and not in the issue.

    Measured with pwsh 7.6.7 under -File: unknown NAMED parameters are not
    rejected, they land in $args; and an unambiguous PREFIX binds to the full
    parameter name. So `-keep` silently switched on `-KeepPulled`, an
    unrelated flag, and the run tore everything down anyway.
    """

    def test_an_unknown_argument_is_refused(self):
        res = _run_powershell_arg_guard("-nonsense")
        self.assertEqual(res.returncode, 1)
        self.assertNotIn("FELL-THROUGH", res.stdout)

    def test_keep_no_longer_binds_to_keep_pulled(self):
        # The regression guard that matters. Without [switch]$Keep declared,
        # `-keep` resolves to `-KeepPulled` by prefix and the script runs on
        # with an unrelated flag enabled.
        res = _run_powershell_arg_guard("-keep")
        self.assertEqual(res.returncode, 1)
        self.assertNotIn("FELL-THROUGH", res.stdout)
        self.assertNotIn("KeepPulled=True", res.stdout)

    def test_the_keep_refusal_names_the_parity_issue(self):
        res = _run_powershell_arg_guard("-Keep")
        self.assertIn("2262", res.stdout + res.stderr)

    def test_the_documented_switches_still_fall_through(self):
        res = _run_powershell_arg_guard("-KeepPulled")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("KeepPulled=True", res.stdout)

    def test_no_arguments_falls_through(self):
        res = _run_powershell_arg_guard()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("FELL-THROUGH", res.stdout)
