import sys
import typing
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import sync_compatibility
from sync_compatibility import (
    _VERIFY_SCRIPT_SAFE_LINE,
    _is_verify_script_diff_cosmetic_only,
)


def _mock_diff_result(diff_text, returncode=0):
    res = MagicMock()
    res.returncode = returncode
    res.stdout = diff_text
    return res


class TestVerifyScriptSafeLine:
    """LDM-#1058: unit coverage for the line-classification regex itself,
    independent of the higher-level diff function."""

    def test_bash_comment_is_safe(self):
        assert _VERIFY_SCRIPT_SAFE_LINE.match("-# some comment")
        assert _VERIFY_SCRIPT_SAFE_LINE.match("+# updated comment")

    def test_script_version_assignment_is_safe(self):
        assert _VERIFY_SCRIPT_SAFE_LINE.match('-SCRIPT_VERSION="2.15.26"')
        assert _VERIFY_SCRIPT_SAFE_LINE.match('+SCRIPT_VERSION="2.15.27-pre.3"')

    def test_powershell_script_version_assignment_is_safe(self):
        assert _VERIFY_SCRIPT_SAFE_LINE.match('-$SCRIPT_VERSION = "2.15.26"')
        assert _VERIFY_SCRIPT_SAFE_LINE.match('+$SCRIPT_VERSION = "2.15.27-pre.3"')

    def test_plain_message_lines_are_safe(self):
        assert _VERIFY_SCRIPT_SAFE_LINE.match('+        echo "new message"')
        assert _VERIFY_SCRIPT_SAFE_LINE.match('-Write-Output "old message"')
        assert _VERIFY_SCRIPT_SAFE_LINE.match('+Write-Host "new message"')

    def test_bare_quoted_array_element_is_safe(self):
        # Regression: the real #1049 fix built its PowerShell message as a
        # bare string element in a $warnLines = @(...) array, looped over
        # separately with Write-Output/Write-Host rather than called
        # inline -- this first tripped the original, narrower pattern.
        assert _VERIFY_SCRIPT_SAFE_LINE.match(
            '+                "  re-pull this script: Invoke-WebRequest -Uri '
            '`"https://example.com/script.ps1`" -OutFile `"script.ps1`""'
        )

    def test_control_flow_line_is_not_safe(self):
        assert not _VERIFY_SCRIPT_SAFE_LINE.match('+if [ "$FOO" = "baz" ]; then')

    def test_arbitrary_command_line_is_not_safe(self):
        assert not _VERIFY_SCRIPT_SAFE_LINE.match(
            "+docker ps -a --filter status=running"
        )


class TestIsVerifyScriptDiffCosmeticOnly:
    """LDM-#1058: the standalone verify-script's own version can lag the
    installed binary's version between refreshes (see #1049) even when
    nothing it checks changed -- these tests lock in that a provably
    cosmetic-only diff (comments, the version line, message text) is
    accepted, while any real logic change is rejected, and that any git
    error fails safe (rejected), never open."""

    @patch("sync_compatibility.subprocess.run")
    def test_cosmetic_only_diff_accepted(self, mock_run):
        diff = (
            "diff --git a/scripts/verify_e2e_refactor.sh b/scripts/verify_e2e_refactor.sh\n"
            "--- a/scripts/verify_e2e_refactor.sh\n"
            "+++ b/scripts/verify_e2e_refactor.sh\n"
            "@@ -9,2 +9,2 @@\n"
            '-SCRIPT_VERSION="2.15.26"\n'
            '+SCRIPT_VERSION="2.15.27-pre.3"\n'
            "@@ -80,1 +80,1 @@\n"
            '-        echo "old re-pull hint"\n'
            '+        echo "new re-pull hint"\n'
        )
        mock_run.return_value = _mock_diff_result(diff)
        assert _is_verify_script_diff_cosmetic_only("v2.15.26") is True

    @patch("sync_compatibility.subprocess.run")
    def test_no_diff_at_all_is_cosmetic(self, mock_run):
        mock_run.return_value = _mock_diff_result("")
        assert _is_verify_script_diff_cosmetic_only("v2.15.27-pre.3") is True

    @patch("sync_compatibility.subprocess.run")
    def test_logic_change_diff_rejected(self, mock_run):
        diff = (
            "diff --git a/scripts/verify_e2e_refactor.sh b/scripts/verify_e2e_refactor.sh\n"
            "--- a/scripts/verify_e2e_refactor.sh\n"
            "+++ b/scripts/verify_e2e_refactor.sh\n"
            "@@ -50,1 +50,1 @@\n"
            '-if [ "$FOO" = "bar" ]; then\n'
            '+if [ "$FOO" = "baz" ]; then\n'
        )
        mock_run.return_value = _mock_diff_result(diff)
        assert _is_verify_script_diff_cosmetic_only("v2.15.26") is False

    @patch("sync_compatibility.subprocess.run")
    def test_mixed_diff_with_one_logic_line_rejected(self, mock_run):
        diff = (
            "--- a/scripts/verify_e2e_refactor.sh\n"
            "+++ b/scripts/verify_e2e_refactor.sh\n"
            '-SCRIPT_VERSION="2.15.26"\n'
            '+SCRIPT_VERSION="2.15.27-pre.3"\n'
            "+NEW_CHECK_ENABLED=true\n"
        )
        mock_run.return_value = _mock_diff_result(diff)
        assert _is_verify_script_diff_cosmetic_only("v2.15.26") is False

    @patch("sync_compatibility.subprocess.run")
    def test_git_error_fails_safe(self, mock_run):
        mock_run.return_value = _mock_diff_result("", returncode=1)
        assert _is_verify_script_diff_cosmetic_only("badref") is False

    @patch("sync_compatibility.subprocess.run", side_effect=Exception("boom"))
    def test_exception_fails_safe(self, mock_run):
        assert _is_verify_script_diff_cosmetic_only("v2.15.26") is False


class TestArgumentHandling:
    """LDM-#1252: the script had no argument parsing, so `--help` -- and any
    typo -- fell through to a full sync, archiving reports and rewriting the
    compatibility table. Asking a destructive tool what it does must be safe."""

    def teardown_method(self):
        # DRY_RUN is module-level state; never leak it into another test.
        sync_compatibility.DRY_RUN = False

    @patch("sync_compatibility.sync_reports")
    def test_help_prints_usage_without_syncing(self, mock_sync):
        with pytest.raises(SystemExit) as exc:
            sync_compatibility.main(["--help"])
        assert exc.value.code == 0
        mock_sync.assert_not_called()

    @patch("sync_compatibility.sync_reports")
    def test_unknown_argument_aborts_instead_of_syncing(self, mock_sync):
        """A typo must fail loudly, not silently rewrite the matrix."""
        with pytest.raises(SystemExit) as exc:
            sync_compatibility.main(["--drynrun"])
        assert exc.value.code != 0
        mock_sync.assert_not_called()

    @patch("sync_compatibility.sync_reports")
    def test_dry_run_sets_the_flag(self, mock_sync):
        sync_compatibility.main(["--dry-run"])
        assert sync_compatibility.DRY_RUN is True
        mock_sync.assert_called_once()

    @patch("sync_compatibility.sync_reports")
    def test_default_run_is_not_dry(self, mock_sync):
        sync_compatibility.main([])
        assert sync_compatibility.DRY_RUN is False
        mock_sync.assert_called_once()

    def test_mutate_skips_the_action_when_dry(self):
        called = []
        sync_compatibility.DRY_RUN = True
        sync_compatibility._mutate("do a thing", lambda: called.append(1))
        assert called == []

    def test_mutate_performs_the_action_when_not_dry(self):
        called = []
        sync_compatibility.DRY_RUN = False
        sync_compatibility._mutate("do a thing", lambda: called.append(1))
        assert called == [1]


class TestMismatchedCheckoutGuard:
    """LDM-#1390: a raw report whose version does not match this checkout used to
    be archived after nothing but a `UI.warning`. A warning is easy to miss in a
    long run, and by then the file has moved -- while each report can represent
    hours of real multi-platform testing. The mismatch almost always means the
    operator is on the wrong ref, not that the report is obsolete."""

    def teardown_method(self):
        sync_compatibility.DRY_RUN = False
        sync_compatibility.ARCHIVE_STALE = False

    STALE: typing.ClassVar[list[tuple[str, str]]] = [
        ("verify-linux-ubuntu-pass.txt", "binary version 9.9.9 != 2.18.0")
    ]

    def test_it_exits_rather_than_archiving(self):
        sync_compatibility.DRY_RUN = False
        with pytest.raises(SystemExit) as exc:
            sync_compatibility._report_mismatched_checkout(self.STALE)
        assert exc.value.code != 0, "must be a failing exit so CI/tooling notices"

    def test_dry_run_reports_without_failing(self):
        """A preview must stay safe to run from tooling."""
        sync_compatibility.DRY_RUN = True
        sync_compatibility._report_mismatched_checkout(self.STALE, fatal=False)

    def test_the_message_names_the_report_and_both_versions(self, capsys):
        """The whole point is that the operator can see what to fix."""
        with pytest.raises(SystemExit):
            sync_compatibility._report_mismatched_checkout(self.STALE)
        out = capsys.readouterr()
        combined = out.out + out.err
        assert "verify-linux-ubuntu-pass.txt" in combined
        assert "9.9.9" in combined and "2.18.0" in combined
        assert "--archive-stale" in combined, "must name the deliberate opt-out"

    @patch("sync_compatibility.sync_reports")
    def test_archive_stale_flag_is_off_by_default(self, mock_sync):
        sync_compatibility.main([])
        assert sync_compatibility.ARCHIVE_STALE is False

    @patch("sync_compatibility.sync_reports")
    def test_archive_stale_flag_can_be_opted_into(self, mock_sync):
        sync_compatibility.main(["--archive-stale"])
        assert sync_compatibility.ARCHIVE_STALE is True
