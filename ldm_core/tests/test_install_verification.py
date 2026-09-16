"""The verification installer, and its two halves staying in step (LDM-#1735).

Staging the suite by hand was four steps -- curl, unzip, chmod, and a checksum
check nobody ran -- and the old single-file instruction skipped `common/`
entirely, which LDM only WARNS about, so the suite completed, exited 0 and
reported success having applied neither the activation key nor the search
configuration (LDM-#1718).

Parity between the `.sh` and `.ps1` halves is a hard rule in
`.agents/skills/testing-and-ci`, and it has so far been kept by habit. Habit
works right up until the person maintaining it changes: a Windows developer
running a staging script that quietly does less than its Unix twin gets a green
result that means nothing. These tests make the two files answer for each
other.

The behavioural tests drive the real script rather than reading it, but only
along paths that touch no network: `--help`, and argument rejection. Anything
past that downloads ~23 MB from GitHub, which a unit test must not do.
"""

import re
import subprocess  # nosec B404 - runs the installer's --help deliberately
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
SH = SCRIPTS / "install_verification.sh"
PS1 = SCRIPTS / "install_verification.ps1"


class BothHalvesExist(unittest.TestCase):
    def test_the_shell_half_is_present(self):
        self.assertTrue(SH.is_file(), f"{SH} is missing")

    def test_the_powershell_half_is_present(self):
        self.assertTrue(
            PS1.is_file(),
            "the Windows half is missing -- a cross-platform utility with one "
            "half is worse than none, because the gap is invisible",
        )


class TheyOfferTheSameOptions(unittest.TestCase):
    """Drift here is silent: the flag simply does nothing on one platform."""

    OPTIONS = (
        ("--tag", "-Tag"),
        ("--dir", "-Dir"),
        ("--activation-key", "-ActivationKey"),
        ("--no-binary", "-NoBinary"),
    )

    def setUp(self):
        self.sh = SH.read_text(encoding="utf-8")
        self.ps1 = PS1.read_text(encoding="utf-8")

    def test_every_option_exists_in_both(self):
        for posix, windows in self.OPTIONS:
            with self.subTest(option=posix):
                self.assertIn(posix, self.sh, f"{posix} missing from the shell half")
                self.assertIn(
                    windows, self.ps1, f"{windows} missing from the PowerShell half"
                )

    def test_both_honour_the_activation_key_environment_variable(self):
        self.assertIn("LDM_ACTIVATION_KEY", self.sh)
        self.assertIn("LDM_ACTIVATION_KEY", self.ps1)


class BothRefuseAnUnverifiedBundle(unittest.TestCase):
    """A truncated download is otherwise found by the suite failing strangely
    an hour later, which is a far more expensive way to learn it."""

    def setUp(self):
        self.sh = SH.read_text(encoding="utf-8")
        self.ps1 = PS1.read_text(encoding="utf-8")

    def test_both_check_the_bundle_checksums(self):
        self.assertIn("SHA256SUMS", self.sh)
        self.assertIn("SHA256SUMS", self.ps1)

    def test_both_verify_the_binary_against_the_release_checksums(self):
        self.assertIn("checksums.txt", self.sh)
        self.assertIn("checksums.txt", self.ps1)


class BothAreLoudAboutTheActivationKey(unittest.TestCase):
    """The failure it causes is SILENT -- LDM warns, the suite passes, and it
    verified a smaller system than it claims. Quiet here would be the same
    defect one layer out."""

    def setUp(self):
        self.sh = SH.read_text(encoding="utf-8")
        self.ps1 = PS1.read_text(encoding="utf-8")

    def test_both_say_the_bundle_cannot_carry_one(self):
        for name, text in (("sh", self.sh), ("ps1", self.ps1)):
            with self.subTest(half=name):
                self.assertIn("UNLICENSED", text)
                self.assertIn("1733", text, "point at why it cannot be bundled")

    def test_neither_treats_a_missing_key_as_fatal(self):
        """Offline staging is legitimate; the warning is the safeguard."""
        self.assertNotIn('die "No activation key', self.sh)
        self.assertNotIn('Stop-WithError "No activation key', self.ps1)


class TheShellHalfBehaves(unittest.TestCase):
    """Driven, not read -- but only along paths that touch no network."""

    def _run(self, *args):
        return subprocess.run(  # nosec B603 - fixed path, no shell
            ["bash", str(SH), *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_help_exits_zero_and_lists_the_options(self):
        result = self._run("--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--tag", "--dir", "--activation-key", "--no-binary"):
            self.assertIn(flag, result.stdout)

    def test_an_unknown_option_is_refused_rather_than_ignored(self):
        """Silently ignoring it would stage the wrong thing and look fine."""
        result = self._run("--not-a-real-flag")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown option", result.stderr)

    def test_it_is_syntactically_valid(self):
        result = subprocess.run(  # nosec B603 - fixed path, no shell
            ["bash", "-n", str(SH)], capture_output=True, text=True, check=False
        )

        self.assertEqual(result.returncode, 0, result.stderr)


class ThePowerShellHalfIsParseable(unittest.TestCase):
    def test_param_is_the_first_statement(self):
        """LDM-#1529: the parser accepts a later `param` block and then ignores
        the arguments, so every flag silently does nothing. Comments may
        precede it; code may not."""
        lines = [
            line.strip()
            for line in PS1.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        self.assertTrue(
            lines[0].startswith("param("),
            f"first non-comment line is {lines[0]!r}, not `param(`",
        )

    def test_it_is_pure_ascii(self):
        """Windows PowerShell 5.1 misparses non-ASCII on ANSI code pages."""
        raw = PS1.read_bytes()

        non_ascii = [(i, b) for i, b in enumerate(raw) if b > 0x7F]

        self.assertEqual(non_ascii, [], f"non-ASCII bytes at {non_ascii[:3]}")


class TheDocsPointAtIt(unittest.TestCase):
    """An installer nobody is told about is the same as no installer."""

    def test_testing_md_mentions_the_installer(self):
        docs = (
            Path(__file__).resolve().parent.parent.parent / "docs" / "TESTING.md"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "install_verification",
            docs,
            "docs/TESTING.md still documents only the manual curl/unzip dance",
        )


class TheHelpTextIsNotAStaleCopy(unittest.TestCase):
    """The shell half prints its own header comment as usage, so a moved
    comment block silently empties the help text."""

    def test_help_output_is_not_empty(self):
        result = subprocess.run(  # nosec B603 - fixed path, no shell
            ["bash", str(SH), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        self.assertGreater(
            len(result.stdout.strip().splitlines()),
            5,
            "usage collapsed to almost nothing -- the sed line range that "
            "prints the header comment has drifted",
        )

    def test_the_usage_range_still_lands_on_comment_lines(self):
        """`sed -n '3,25p'` is positional: inserting a line above it prints
        code as if it were help."""
        text = SH.read_text(encoding="utf-8")
        match = re.search(r"sed -n '(\d+),(\d+)p'", text)
        self.assertIsNotNone(match, "the usage() sed range has been rewritten")
        assert match is not None  # narrowing for mypy

        start, end = int(match.group(1)), int(match.group(2))
        lines = text.splitlines()[start - 1 : end]

        offenders = [line for line in lines if line and not line.startswith("#")]
        self.assertEqual(
            offenders,
            [],
            f"usage() would print non-comment lines: {offenders[:2]}",
        )


if __name__ == "__main__":
    unittest.main()
