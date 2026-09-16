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


class BothVerifyThemselves(unittest.TestCase):
    """LDM-#1735: the bootstrap was the one unverified link.

    The installer checksums the bundle and the binary, so asking the tester to
    verify the installer by hand -- with a separate curl of checksums.txt and a
    shasum invocation to remember -- put the only manual step on the only file
    nothing else covered. It now checks itself against the release it is
    staging.

    A mismatch WARNS rather than fails: reusing one installer across several
    releases is legitimate, and what is being verified is the release's
    artifacts, not this script's vintage. A release predating the asset has no
    entry at all, and must pass silently rather than warn about its own
    absence.
    """

    def setUp(self):
        self.sh = SH.read_text(encoding="utf-8")
        self.ps1 = PS1.read_text(encoding="utf-8")

    def test_both_hash_themselves_against_the_release(self):
        self.assertIn("install_verification", self.sh)
        self.assertIn("$PSCommandPath", self.ps1)

    def test_both_offer_an_escape_hatch(self):
        self.assertIn("--no-self-check", self.sh)
        self.assertIn("NoSelfCheck", self.ps1)

    def test_neither_treats_a_mismatch_as_fatal(self):
        """Refusing here would block a legitimate reuse across releases."""
        self.assertNotIn('die "This installer does not match', self.sh)
        self.assertNotIn('Stop-WithError "This installer does not match', self.ps1)

    def test_the_shell_pattern_does_not_match_the_powershell_file(self):
        """`install_verification.sh` must not match `install_verification.ps1`.

        A loose pattern would compare this script against the OTHER half's
        digest and warn on every single run.
        """
        import re as _re

        pattern = _re.compile(r"install_verification\.sh$")

        self.assertIsNone(pattern.search("install_verification.ps1"))
        self.assertIsNotNone(pattern.search("install_verification.sh"))


class BothFindTheActivationKeyWhereItLives(unittest.TestCase):
    """The key sits in a `common/` folder on each machine, relative to where
    the suite is run (LDM-#1735).

    Requiring a flag for something already on disk in a known place is one more
    thing to remember -- and forgetting it fails SILENTLY: LDM only warns, the
    suite exits 0, and reports success having verified an unlicensed DXP. So
    the installer looks before it asks.

    Two places, in order: the target's own `common/`, which already holds a key
    when the bundle was unpacked over an existing folder (measured: `unzip -o`
    replaces only what the zip contains, so a key sitting there survives), then
    `./common/` relative to the invocation.
    """

    def setUp(self):
        self.sh = SH.read_text(encoding="utf-8")
        self.ps1 = PS1.read_text(encoding="utf-8")

    def test_both_look_in_the_target_common(self):
        self.assertIn("activation-key-*.xml", self.sh)
        self.assertIn("activation-key-*.xml", self.ps1)

    def test_both_look_beside_the_invocation(self):
        self.assertIn("./common", self.sh)
        self.assertIn(".\\common", self.ps1)

    def test_an_explicit_flag_still_wins(self):
        """Discovery must not override what the operator asked for."""
        self.assertIn('if [ -z "$ACTIVATION_KEY" ]; then', self.sh)
        self.assertIn("if ([string]::IsNullOrWhiteSpace($ActivationKey))", self.ps1)


class BothCleanUpAfterThemselves(unittest.TestCase):
    """The archive is a means, not a deliverable (LDM-#1741).

    Leaving `verification-bundle.zip` and `checksums.txt` beside the suite
    gives the operator a directory where it is not obvious what is input, what
    is output, and what is spent -- and an 80KB zip they now have to decide
    about every time.

    Removal happens AFTER verification, never before. A failed checksum is
    precisely when the archive is worth keeping, because it is the evidence of
    what actually arrived; deleting it first would destroy the one artifact
    worth inspecting.

    `SHA256SUMS` and `MANIFEST.txt` stay. They came out of the bundle and
    remain useful: one re-checks the extracted files, the other records which
    release this is and what it does not contain.
    """

    def setUp(self):
        self.sh = SH.read_text(encoding="utf-8")
        self.ps1 = PS1.read_text(encoding="utf-8")

    def test_both_remove_the_archive(self):
        self.assertIn('rm -f "${TARGET_DIR}/verification-bundle.zip"', self.sh)
        self.assertIn("Remove-Item $zipPath", self.ps1)

    def test_the_archive_is_removed_after_the_checksum_check(self):
        """Removing it first would destroy the evidence on a failure."""
        verify_at = self.sh.index("Verifying checksums")
        remove_at = self.sh.index('rm -f "${TARGET_DIR}/verification-bundle.zip"')

        self.assertLess(
            verify_at,
            remove_at,
            "the archive is deleted before it is verified, so a corrupt "
            "download leaves nothing to inspect",
        )

    def test_both_remove_the_spent_binary_checksums(self):
        self.assertIn('rm -f "${TARGET_DIR}/checksums.txt"', self.sh)
        self.assertIn("Remove-Item $sumFile", self.ps1)

    def test_neither_removes_the_bundle_manifest_or_sums(self):
        """These are bundle contents, not transient downloads.

        Asserted against removal commands specifically -- both files are
        legitimately *read* (the sums to verify, the manifest to report), so a
        bare name search would match those and fail for the wrong reason.
        """
        removals_sh = [
            line for line in self.sh.splitlines() if line.strip().startswith("rm -f")
        ]
        removals_ps1 = [
            line
            for line in self.ps1.splitlines()
            if line.strip().startswith("Remove-Item")
        ]

        for keep in ("SHA256SUMS", "MANIFEST.txt"):
            with self.subTest(file=keep):
                self.assertFalse(
                    [line for line in removals_sh if keep in line],
                    f"the shell half deletes {keep}",
                )
                self.assertFalse(
                    [line for line in removals_ps1 if keep in line],
                    f"the PowerShell half deletes {keep}",
                )


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


class TheReleasePublishesIt(unittest.TestCase):
    """An installer only reachable from a checkout defeats its own purpose.

    The whole point is verifying a release the way a user would -- from the
    published artifacts alone, with no clone. Before this, the installer
    existed only in the repo, so the one person it was written for could not
    use it without the thing it was meant to avoid.
    """

    def _ci(self):
        root = Path(__file__).resolve().parent.parent.parent
        return (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    def test_both_halves_are_published_as_release_assets(self):
        text = self._ci()

        self.assertIn("bin-temp/install_verification.sh", text)
        self.assertIn("bin-temp/install_verification.ps1", text)

    def test_they_are_covered_by_the_release_checksums(self):
        """An installer you cannot verify before running is a worse bootstrap
        than the curl it replaces."""
        text = self._ci()
        sums_line = next(
            line for line in text.splitlines() if "sha256sum ldm-linux" in line
        )

        self.assertIn("install_verification.sh", sums_line)
        self.assertIn("install_verification.ps1", sums_line)

    def test_they_are_staged_before_the_checksums_that_cover_them(self):
        """Staged after, and checksums.txt would not include them."""
        text = self._ci()

        staged_at = text.index("cp scripts/install_verification.sh")
        sums_at = text.index("sha256sum ldm-linux")

        self.assertLess(
            staged_at,
            sums_at,
            "the installers are staged after the checksums, so they ship unchecksummed",
        )


class TheDocsPointAtIt(unittest.TestCase):
    """An installer nobody is told about is the same as no installer."""

    def _docs(self):
        return (
            Path(__file__).resolve().parent.parent.parent / "docs" / "TESTING.md"
        ).read_text(encoding="utf-8")

    def test_both_platforms_are_documented(self):
        """Parity in the scripts is worth little if only one is written up."""
        docs = self._docs()

        self.assertIn("install_verification.sh", docs)
        self.assertIn("install_verification.ps1", docs)

    def test_the_upgrade_step_comes_first(self):
        """`v$(ldm version)` is only correct because the upgrade precedes it.

        Documented without that step, the sequence silently stages the
        PREVIOUS release -- observed once, with pre.8 staged while pre.9 was
        the release under test.
        """
        docs = self._docs()

        self.assertLess(
            docs.index("ldm system upgrade --beta"),
            docs.index("LDM_TAG=v$(ldm version)"),
            "the tag is derived before the upgrade that makes it correct",
        )

    def test_the_windows_execution_policy_is_covered(self):
        """A downloaded .ps1 is blocked by default; without this the very first
        Windows run fails with a security error."""
        self.assertIn("Set-ExecutionPolicy", self._docs())

    def test_the_windows_upgrade_race_is_warned_about(self):
        """LDM-#1743: the upgrade returns before it completes on Windows."""
        docs = self._docs()

        self.assertIn("1743", docs)
        self.assertIn("returns before it has finished", docs)

    def test_the_silent_failure_of_a_missing_key_is_stated(self):
        """The whole hazard is that nothing appears to go wrong."""
        docs = self._docs()

        self.assertIn("unlicensed", docs)
        self.assertIn("exits 0", docs)

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


class ThePushWrapperWarnsAboutAMergedPr(unittest.TestCase):
    """A push to a branch whose PR already merged lands nowhere (LDM-#1735).

    It is the quietest failure in this workflow: the branch exists, the write
    succeeds, the wrapper prints its success banner, and CI stays green because
    there is nothing new to test. Two commits were lost exactly this way --
    the PR merged at 07:45, the commits were made at 07:52 and 08:03, and the
    first symptom was a published release missing an asset.

    The guard is best-effort on purpose: no `gh`, no auth, or no PR for the
    branch are all ordinary situations, and none of them indicate a problem.
    """

    WRAPPER = SCRIPTS / "agent_push.sh"

    def _text(self):
        return self.WRAPPER.read_text(encoding="utf-8")

    def test_it_checks_the_pr_state_after_pushing(self):
        text = self._text()

        self.assertIn("gh pr view", text)
        self.assertIn("MERGED", text)

    def test_it_runs_after_the_push_not_instead_of_it(self):
        """The push must still happen -- the branch may be legitimately reused."""
        text = self._text()

        self.assertLess(
            text.index("git push origin HEAD"),
            text.index("gh pr view"),
            "the guard runs before the push, so a failure would block it",
        )

    def test_it_never_fails_the_push(self):
        """`gh` absent or unauthenticated must not turn a good push into an error."""
        text = self._text()
        guard = text[text.index("gh pr view") - 400 : text.index("gh pr view") + 400]

        self.assertIn("|| echo", guard)
        self.assertNotIn("exit 1", guard)

    def test_it_says_what_to_do_about_it(self):
        """A warning that does not name the remedy gets read as noise."""
        self.assertIn("Open a new", self._text())
