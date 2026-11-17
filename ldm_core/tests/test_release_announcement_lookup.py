"""Which announcements the upgrade banner shows, and the promotion gate (LDM-#2412).

Three layers, so the seam between them is crossed rather than bracketed:

* `resolve_release_announcements` -- the pure lookup, against a fixture map.
* `check_and_display_upgrade_banner` -- the real banner, with stdout captured
  as a TTY, so what is asserted is what a user's terminal receives.
* `check_patch_announcement_for_promotion` in `scripts/release.py` -- the
  LDM-#1663 rule enforced when a patch is promoted to stable.

No test here asserts that the CURRENT VERSION has a key of its own: a shipped
patch without one (v2.26.10) would turn the suite red with nothing to fix.
That rule belongs at promotion time, which is where the third class tests it.
"""

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from ldm_core.cli import resolve_release_announcements

_SPEC = importlib.util.spec_from_file_location(
    "ldm_release_2412",
    Path(__file__).resolve().parent.parent.parent / "scripts" / "release.py",
)
assert _SPEC is not None, "could not locate scripts/release.py"
assert _SPEC.loader is not None, "no loader for scripts/release.py"
_release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_release)

MINOR = [("minor-cmd", "MINOR_NOTE for X.Y.0")]
PATCH = [("patch-cmd", "PATCH_NOTE for X.Y.3")]
PRE = [("pre-cmd", "PRE_NOTE for exactly X.Y.3-pre.1")]

FIXTURE = {"9.8": MINOR, "9.8.3": PATCH, "9.8.3-pre.1": PRE}


class TestResolveReleaseAnnouncements(unittest.TestCase):
    def test_exact_version_wins(self):
        self.assertEqual(resolve_release_announcements("9.8.3-pre.1", FIXTURE), PRE)

    def test_pre_release_falls_back_to_its_own_stable_key(self):
        self.assertEqual(resolve_release_announcements("9.8.3-pre.2", FIXTURE), PATCH)

    def test_stable_patch_uses_its_own_key(self):
        self.assertEqual(resolve_release_announcements("9.8.3", FIXTURE), PATCH)

    def test_patch_without_a_key_gets_nothing_not_the_minor(self):
        self.assertEqual(resolve_release_announcements("9.8.4", FIXTURE), [])

    def test_pre_release_of_a_patch_without_a_key_gets_nothing(self):
        self.assertEqual(resolve_release_announcements("9.8.4-pre.1", FIXTURE), [])

    def test_minor_release_uses_the_series_key(self):
        self.assertEqual(resolve_release_announcements("9.8.0", FIXTURE), MINOR)

    def test_minor_pre_release_uses_the_series_key(self):
        self.assertEqual(resolve_release_announcements("9.8.0-pre.3", FIXTURE), MINOR)

    def test_unknown_series_gets_nothing(self):
        self.assertEqual(resolve_release_announcements("9.9.0", FIXTURE), [])


class _TtyBuffer(io.StringIO):
    def isatty(self):
        return True


class TestBannerPrintsTheResolvedAnnouncements(unittest.TestCase):
    """The real banner, writing to a captured TTY stdout."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def _run_banner(self, version):
        from ldm_core.cli import check_and_display_upgrade_banner
        from ldm_core.constants import RELEASE_ANNOUNCEMENTS

        buf = _TtyBuffer()
        orig_argv = sys.argv
        orig_env = os.environ.copy()
        sys.argv = ["ldm"]
        # The banner returns early under pytest; without this it prints
        # nothing and every assertNotIn below would pass vacuously.
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            with (
                patch.dict(RELEASE_ANNOUNCEMENTS, FIXTURE, clear=True),
                patch("ldm_core.constants.VERSION", version),
                patch("ldm_core.utils.get_actual_home", return_value=self.home),
                patch("ldm_core.diagnostics.upgrade.repair_binary_mode"),
                patch(
                    "ldm_core.shiv_cache.prune_stale_extractions",
                    return_value=(0, 0),
                ),
                patch("sys.stdout", buf),
            ):
                check_and_display_upgrade_banner()
        finally:
            sys.argv = orig_argv
            os.environ.clear()
            os.environ.update(orig_env)
        return buf.getvalue()

    def test_pre_release_prints_its_stable_entry(self):
        out = self._run_banner("9.8.3-pre.2")
        self.assertIn("upgraded to v9.8.3-pre.2", out)
        self.assertIn("PATCH_NOTE", out)
        self.assertNotIn("MINOR_NOTE", out)

    def test_patch_without_a_key_prints_upgrade_line_but_no_block(self):
        out = self._run_banner("9.8.4")
        self.assertIn("upgraded to v9.8.4", out)
        self.assertNotIn("MINOR_NOTE", out)
        self.assertNotIn("Important Changes", out)

    def test_minor_release_prints_the_series_entry(self):
        out = self._run_banner("9.8.0")
        self.assertIn("Important Changes", out)
        self.assertIn("MINOR_NOTE", out)


class TestPromotionRequiresAPatchEntry(unittest.TestCase):
    """`release.py --promote` refuses a stable X.Y.Z (Z > 0) with no key."""

    ANNOUNCEMENTS: ClassVar[dict] = {"2.26": ["a"], "2.26.11": ["x", "y"]}

    def test_patch_with_its_own_key_is_allowed(self):
        ok, msg = _release.check_patch_announcement_for_promotion(
            "2.26.11-pre.2", self.ANNOUNCEMENTS
        )
        self.assertTrue(ok)
        self.assertIn("2.26.11", msg)

    def test_patch_without_its_own_key_is_refused(self):
        """The v2.26.10 shape: only the series key exists."""
        ok, msg = _release.check_patch_announcement_for_promotion(
            "2.26.10-pre.1", self.ANNOUNCEMENTS
        )
        self.assertFalse(ok)
        self.assertIn("'2.26.10'", msg)

    def test_patch_with_an_empty_entry_is_refused(self):
        ok, _ = _release.check_patch_announcement_for_promotion(
            "2.26.12-pre.1", {"2.26.12": []}
        )
        self.assertFalse(ok)

    def test_minor_release_does_not_need_a_patch_key(self):
        ok, _ = _release.check_patch_announcement_for_promotion(
            "2.27.0-pre.4", {"2.27": ["a"]}
        )
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
