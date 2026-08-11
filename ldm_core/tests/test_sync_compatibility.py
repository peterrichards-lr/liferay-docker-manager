import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

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
