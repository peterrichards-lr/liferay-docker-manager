"""A promote's version stamps must not block compatibility-table normalisation.

LDM-#1810.

## What was actually wrong

`get_promotable_stable_version()` exists so a row verified at `X.Y.Z-pre.N`
displays as `X.Y.Z` once the promote is provably metadata-only. It refused for
every row of the `v2.23.0` promote, and the issue attributed that to the verify
scripts being absent from `_METADATA_ONLY_ALLOWLIST`. They were not absent --
they had been on it since `589651e0`, the commit that introduced the feature.

Measured instead against the real promote: 20 files changed, exactly one
unexplained.

    ldm_core/resources/ldm.1

The man page, blocked by the same mechanism the issue correctly identified --
a stamp applied on every bump by `ldm system version --bump`:

    -.TH LDM 1 "September 2026" "2.23.0-pre.8" "Liferay Docker Manager Manual"
    +.TH LDM 1 "September 2026" "2.23.0" "Liferay Docker Manager Manual"

## Why it is not simply allowlisted

`ldm.1` ships inside the binary, and the allowlist's stated contract is that
nothing on it does. A blanket entry would wave through a substantive man page
edit between pre.N and stable -- the case the guard exists to catch. So it is
admitted only on proof that its own diff is the stamp alone.

The verify scripts moved to that same footing. They were allowlisted
*unconditionally*, which meant a promote that changed what they actually
exercise was still treated as metadata-only. That is the hole the issue's own
acceptance criterion names, and it was open.

## What must NOT be lost

An inconclusive answer must never read as a green light: every predicate here
fails safe (False) on a git error, a missing ref, or any line it cannot
positively classify.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from sync_compatibility import (
    _CONDITIONALLY_ALLOWED,
    _METADATA_ONLY_ALLOWLIST,
    _is_man_page_diff_stamp_only,
    _is_metadata_only_diff,
)

MAN_PAGE = "ldm_core/resources/ldm.1"

STAMP_DIFF = (
    f"--- a/{MAN_PAGE}\n"
    f"+++ b/{MAN_PAGE}\n"
    "@@ -1,1 +1,1 @@\n"
    '-.TH LDM 1 "September 2026" "2.23.0-pre.8" "Liferay Docker Manager Manual"\n'
    '+.TH LDM 1 "September 2026" "2.23.0" "Liferay Docker Manager Manual"\n'
)

SUBSTANTIVE_MAN_DIFF = STAMP_DIFF + (
    "@@ -160,0 +161,2 @@\n+.TP\n+\\fBldm db settle\\fR\n"
)

VERIFY_STAMP_DIFF = (
    "--- a/scripts/verify_e2e_refactor.sh\n"
    "+++ b/scripts/verify_e2e_refactor.sh\n"
    '-SCRIPT_VERSION="2.23.0-pre.8"\n'
    '+SCRIPT_VERSION="2.23.0"\n'
)

VERIFY_LOGIC_DIFF = VERIFY_STAMP_DIFF + "+NEW_CHECK_ENABLED=true\n"

# The real v2.23.0 promote, trimmed to one representative of each shape.
PROMOTE_FILES = [
    "CHANGELOG.md",
    "docs/reference/compatibility.md",
    "ldm_core/constants.py",
    "pyproject.toml",
    "references/verification-results/verify-linux-workstation-fedora-44-native-docker-pass.txt",
    MAN_PAGE,
    "scripts/verify_e2e_refactor.ps1",
    "scripts/verify_e2e_refactor.sh",
]


def _result(stdout, returncode=0):
    res = MagicMock()
    res.returncode = returncode
    res.stdout = stdout
    return res


def _git_double(
    files, man_diff=STAMP_DIFF, verify_diff=VERIFY_STAMP_DIFF, returncode=0
):
    """Answers the several git calls `_is_metadata_only_diff` now makes.

    It asks for the changed-file list first, then -- only for paths the
    unconditional allowlist did not explain -- for that path's own diff. A
    single canned return value would feed the file list to the line-level
    predicates, so dispatch on the arguments instead.
    """

    def run(cmd, *_a, **_kw):
        if returncode != 0:
            return _result("", returncode=returncode)
        if "--name-only" in cmd:
            return _result("\n".join(files) + "\n")
        if MAN_PAGE in cmd:
            return _result(man_diff)
        return _result(verify_diff)

    return run


class TheManPageStampIsRecognised(unittest.TestCase):
    """The predicate itself, independent of the caller."""

    @patch("sync_compatibility.subprocess.run")
    def test_a_stamp_only_diff_is_accepted(self, run):
        run.return_value = _result(STAMP_DIFF)

        assert _is_man_page_diff_stamp_only("v2.23.0-pre.8", "v2.23.0") is True

    @patch("sync_compatibility.subprocess.run")
    def test_no_diff_at_all_is_accepted(self, run):
        run.return_value = _result("")

        assert _is_man_page_diff_stamp_only("v2.23.0-pre.8", "v2.23.0") is True

    @patch("sync_compatibility.subprocess.run")
    def test_a_real_documentation_change_is_refused(self, run):
        """The whole reason ldm.1 cannot just join the allowlist: it ships
        inside the binary, so a genuine edit must still block relabeling."""
        run.return_value = _result(SUBSTANTIVE_MAN_DIFF)

        assert _is_man_page_diff_stamp_only("v2.23.0-pre.8", "v2.23.0") is False

    @patch("sync_compatibility.subprocess.run")
    def test_a_git_error_fails_safe(self, run):
        run.return_value = _result("", returncode=1)

        assert _is_man_page_diff_stamp_only("no-such-ref", "v2.23.0") is False

    @patch("sync_compatibility.subprocess.run", side_effect=Exception("boom"))
    def test_an_exception_fails_safe(self, _run):
        assert _is_man_page_diff_stamp_only("v2.23.0-pre.8", "v2.23.0") is False


class ThePromoteIsRecognisedAsMetadataOnly(unittest.TestCase):
    """LDM-#1810's acceptance criterion, both directions.

    Driven through the real `_is_metadata_only_diff` rather than by asserting
    the allowlist's contents -- a test that checked membership would pass
    against a blanket entry too, which is exactly the fix that must not be
    made.
    """

    @patch("sync_compatibility.subprocess.run")
    def test_a_stamp_only_promote_normalises(self, run):
        """This is the case that could never fire before the fix."""
        run.side_effect = _git_double(PROMOTE_FILES)

        assert _is_metadata_only_diff("v2.23.0-pre.8", "v2.23.0") is True

    @patch("sync_compatibility.subprocess.run")
    def test_a_substantive_man_page_edit_still_refuses(self, run):
        run.side_effect = _git_double(PROMOTE_FILES, man_diff=SUBSTANTIVE_MAN_DIFF)

        assert _is_metadata_only_diff("v2.23.0-pre.8", "v2.23.0") is False

    @patch("sync_compatibility.subprocess.run")
    def test_a_verify_script_logic_change_still_refuses(self, run):
        """The hole the issue's acceptance criterion names. Before this change
        the verify scripts were allowlisted unconditionally, so a promote that
        altered what they exercise was accepted as metadata-only."""
        run.side_effect = _git_double(PROMOTE_FILES, verify_diff=VERIFY_LOGIC_DIFF)

        assert _is_metadata_only_diff("v2.23.0-pre.8", "v2.23.0") is False

    @patch("sync_compatibility.subprocess.run")
    def test_a_shipped_source_file_still_refuses(self, run):
        """Nothing here may weaken the original guard: a real code change
        between pre.N and stable keeps the honest pre-release label. Measured
        on v2.22.0, whose promote carried eight such files."""
        run.side_effect = _git_double([*PROMOTE_FILES, "ldm_core/diagnostics/info.py"])

        assert _is_metadata_only_diff("v2.22.0-pre.9", "v2.22.0") is False

    @patch("sync_compatibility.subprocess.run")
    def test_a_git_error_fails_safe(self, run):
        run.side_effect = _git_double(PROMOTE_FILES, returncode=1)

        assert _is_metadata_only_diff("no-such-ref", "v2.23.0") is False


class TheConditionalFilesAreNotAlsoUnconditional(unittest.TestCase):
    """Belt and braces: a file admitted on proof must not ALSO sit on the
    unconditional allowlist, or the proof is dead code and the refusal tests
    above would be the only thing holding the line."""

    def test_neither_conditional_path_is_unconditionally_allowed(self):
        for pattern, _predicate in _CONDITIONALLY_ALLOWED:
            for sample in (MAN_PAGE, "scripts/verify_e2e_refactor.sh"):
                if not pattern.match(sample):
                    continue
                assert not any(
                    allowed.match(sample) for allowed in _METADATA_ONLY_ALLOWLIST
                ), f"{sample} is allowlisted outright, so its proof never runs"


if __name__ == "__main__":
    unittest.main()
