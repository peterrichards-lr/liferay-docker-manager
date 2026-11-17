"""The upgrade banner must prefer a patch-specific announcement (LDM-#1663).

`check_and_display_upgrade_banner` resolves its content with
`resolve_release_announcements` (LDM-#2412): the exact VERSION, then VERSION
without its pre-release suffix, then the "X.Y" series key ONLY for an X.Y.0
release. Before #2412 it was

    RELEASE_ANNOUNCEMENTS.get(VERSION, RELEASE_ANNOUNCEMENTS.get(v_prefix, []))

so a patch with no key of its own (v2.26.10) re-printed 2.26.0's notes as new,
and a pre-release ("2.26.11-pre.2") could never reach its "2.26.11" key.

These tests drive the real function rather than re-evaluating an expression,
because the defect in LDM-#1663 was never in the expression -- it was in the
data the expression reached.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestPatchReleaseAnnouncements(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _render(self, version):
        """Run the real banner at `version` and return everything it printed.

        Mirrors the guard-clause handling in
        test_config_safe.test_upgrade_banner_dynamic_announcements: the function
        bails out early under pytest and on a non-tty, so both have to be
        neutralised or it silently prints nothing and every assertion below
        would pass vacuously.
        """
        from ldm_core.cli import check_and_display_upgrade_banner

        orig_argv = sys.argv
        orig_env = os.environ.copy()
        sys.argv = ["ldm"]
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            with (
                patch("sys.stdout.isatty", return_value=True),
                patch("ldm_core.utils.get_actual_home", return_value=self.home),
                patch("ldm_core.ui.UI.heading"),
                patch("builtins.print") as mock_print,
                patch("ldm_core.constants.VERSION", version),
            ):
                check_and_display_upgrade_banner()
                return " ".join(
                    str(call[0][0]) for call in mock_print.call_args_list if call[0]
                )
        finally:
            sys.argv = orig_argv
            os.environ.clear()
            os.environ.update(orig_env)

    def test_the_banner_actually_emits_something(self):
        """Guard against the whole suite passing vacuously.

        Every assertion below is about *which* text appears. If the function
        bailed out of its early-return clauses, it would print nothing and the
        `assertNotIn` checks would pass for the wrong reason.
        """
        printed = self._render("2.21.1")
        self.assertIn("upgraded to v2.21.1", printed)

    def test_patch_entry_wins_over_its_minor_series(self):
        printed = self._render("2.21.1")

        # From RELEASE_ANNOUNCEMENTS["2.21.1"]
        self.assertIn("--tag-latest", printed)
        self.assertIn("OLDEST", printed)

        # From RELEASE_ANNOUNCEMENTS["2.21"] -- the v2.21.0 feature list, which
        # a v2.21.0 user has already seen and must not be shown again.
        self.assertNotIn("--database-mode", printed)
        self.assertNotIn("lfr-tunnel", printed)

    def test_the_series_still_serves_a_version_with_no_patch_entry(self):
        """The fallback must survive: v2.21.0 itself has no exact key."""
        printed = self._render("2.21.0")
        self.assertIn("--database-mode", printed)
        self.assertNotIn("OLDEST", printed)

    def test_a_pre_release_of_the_patch_shows_the_patch_entry(self):
        """LDM-#2412: "2.21.1-pre.2" reaches "2.21.1", not the 2.21.0 list.

        This replaces a test that pinned the opposite as a "real limitation":
        pre-release testers were always shown the minor's old notes.
        """
        printed = self._render("2.21.1-pre.2")
        self.assertIn("OLDEST", printed)
        self.assertNotIn("--database-mode", printed)

    def test_a_patch_without_its_own_entry_does_not_reprint_the_minor(self):
        """The LDM-#2412 symptom, on the real data: v2.26.10 has no key.

        It was shown 2.26.0's highlights, including the stale LDM-#1944 note.
        It must still be told it was upgraded, just with no announcement block.
        """
        printed = self._render("2.26.10")
        self.assertIn("upgraded to v2.26.10", printed)
        self.assertNotIn("Client extensions receive their own config tree", printed)
        self.assertNotIn("Important Changes", printed)

    def test_every_shipped_entry_is_a_two_tuple_of_non_empty_strings(self):
        from ldm_core.constants import RELEASE_ANNOUNCEMENTS

        for command, description in RELEASE_ANNOUNCEMENTS["2.21.1"]:
            self.assertTrue(command.strip(), "empty command label")
            self.assertTrue(description.strip(), "empty description")


if __name__ == "__main__":
    unittest.main()
