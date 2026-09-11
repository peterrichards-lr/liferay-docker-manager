"""The upgrade banner must prefer a patch-specific announcement (LDM-#1663).

`check_and_display_upgrade_banner` resolves its content with

    RELEASE_ANNOUNCEMENTS.get(VERSION, RELEASE_ANNOUNCEMENTS.get(v_prefix, []))

so an exact-version key outranks the minor-series key. Nothing asserted that
precedence before, and nothing asserted that a patch release has an entry of its
own -- `test_release_announcements_contract` only requires the *series* key, which
"2.21" satisfied while a v2.21.0 -> v2.21.1 upgrade re-printed the v2.21.0
highlights verbatim.

These tests drive the real function rather than re-evaluating the expression
above, because the defect was never in the expression -- it was in the data the
expression reached.
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

    def test_a_pre_release_of_the_patch_falls_back_to_the_series(self):
        """Documents a real limitation, so nobody 'fixes' it by accident.

        During the cycle VERSION is e.g. "2.21.1-pre.2". Its series prefix is
        "2.21", so the patch entry is unreachable and cannot be exercised by
        pre-release manual E2E -- which is precisely why this file exists.
        """
        printed = self._render("2.21.1-pre.2")
        self.assertIn("--database-mode", printed)
        self.assertNotIn("OLDEST", printed)

    def test_every_shipped_entry_is_a_two_tuple_of_non_empty_strings(self):
        from ldm_core.constants import RELEASE_ANNOUNCEMENTS

        for command, description in RELEASE_ANNOUNCEMENTS["2.21.1"]:
            self.assertTrue(command.strip(), "empty command label")
            self.assertTrue(description.strip(), "empty description")


if __name__ == "__main__":
    unittest.main()
