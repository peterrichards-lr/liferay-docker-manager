import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.defaults import CONVENTION_DEFAULTS, DefaultsManager


class TestDefaultsManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

        # Override paths for testing
        self.global_path = self.root / "global.json"
        self.user_path = self.root / "user.json"

        # Mock get_actual_home to avoid polluting actual user dir
        with patch("ldm_core.defaults.get_actual_home", return_value=self.root):
            self.manager = DefaultsManager()
            self.manager.global_path = self.global_path
            self.manager.user_path = self.user_path
            # Re-init dicts since paths changed
            self.manager.global_defaults = {}
            self.manager.user_defaults = {}

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_get_convention_defaults(self):
        # Should return convention default if not overridden
        self.assertEqual(self.manager.get("db_type"), CONVENTION_DEFAULTS["db_type"])

    def test_set_user_default(self):
        self.manager.set_user_default("db_type", "mysql")
        self.assertEqual(self.manager.get("db_type"), "mysql")
        self.assertTrue(self.user_path.exists())
        self.assertIn("mysql", self.user_path.read_text())

    def test_set_global_default(self):
        self.manager.set_global_default("port", "9090")
        self.assertEqual(self.manager.get("port"), "9090")
        self.assertTrue(self.global_path.exists())

    def test_cascading_priority(self):
        self.manager.set_global_default("port", "9090")
        self.manager.set_user_default("port", "8081")
        # User default should override global
        self.assertEqual(self.manager.get("port"), "8081")

    def test_remove_user_default(self):
        self.manager.set_user_default("tag", "2024.q1.4-lts")
        self.assertEqual(self.manager.get("tag"), "2024.q1.4-lts")
        self.manager.remove_user_default("tag")
        self.assertEqual(self.manager.get("tag"), CONVENTION_DEFAULTS["tag"])


if __name__ == "__main__":
    unittest.main()


class TestDefaultsResetAll(unittest.TestCase):
    """LDM-#1853: `ldm config defaults --reset-all` returns to convention.

    Before this there was no way back to stock short of removing 23 keys one
    at a time or hand-editing `~/.ldmrc`.
    """

    def setUp(self):
        from unittest.mock import MagicMock

        from ldm_core.handlers.config import ConfigService

        self.tmp_dir = tempfile.TemporaryDirectory()
        root = Path(self.tmp_dir.name)

        with patch("ldm_core.defaults.get_actual_home", return_value=root):
            self.defaults = DefaultsManager()
        self.defaults.global_path = root / "global.json"
        self.defaults.user_path = root / "user.json"
        self.defaults.global_defaults = {}
        self.defaults.user_defaults = {}

        self.manager = MagicMock()
        self.manager.defaults = self.defaults
        self.manager.non_interactive = True
        self.manager.args = MagicMock(
            global_level=False, remove=False, reset_all=True, key=None, value=None
        )
        self.handler = ConfigService(self.manager)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _reset(self):
        return self.handler.cmd_defaults()

    def test_nothing_customised_is_an_idempotent_no_op(self):
        """Exit 5, not 0 and not 1: nothing failed and nothing changed."""
        with self.assertRaises(SystemExit) as caught:
            self._reset()
        self.assertEqual(5, caught.exception.code)

    def test_every_customised_default_is_cleared(self):
        self.defaults.set_user_default("port", "9090")
        self.defaults.set_user_default("db_type", "mysql")

        self._reset()

        self.assertEqual({}, self.defaults.user_defaults)
        self.assertEqual(CONVENTION_DEFAULTS["port"], self.defaults.get("port"))
        self.assertEqual(CONVENTION_DEFAULTS["db_type"], self.defaults.get("db_type"))

    def test_keys_the_defaults_do_not_own_are_left_alone(self):
        """`ldm config set` writes other keys into the same file (LDM-#1651).

        Those are not this command's to remove, and clearing the whole file
        would silently discard them.
        """
        self.defaults.set_user_default("port", "9090")
        self.defaults.set_user_default("lfr_tunnel_bin", "/opt/lfr-tunnel")

        self._reset()

        self.assertNotIn("port", self.defaults.user_defaults)
        self.assertEqual(
            "/opt/lfr-tunnel",
            self.defaults.user_defaults.get("lfr_tunnel_bin"),
            "a non-convention key must survive --reset-all",
        )

    def test_a_refused_confirmation_changes_nothing(self):
        self.defaults.set_user_default("port", "9090")
        self.manager.non_interactive = False

        with patch("ldm_core.handlers.config.UI.confirm", return_value=False) as ask:
            self._reset()

        # Asserting only that the value survived would pass against code with
        # no --reset-all at all, since nothing would happen either way. The
        # prompt having been asked is what distinguishes "declined" from
        # "feature absent".
        ask.assert_called_once()
        self.assertEqual("9090", self.defaults.user_defaults.get("port"))

    def test_the_global_level_clears_the_other_file(self):
        self.defaults.set_global_default("port", "9090")
        self.defaults.set_user_default("db_type", "mysql")
        self.manager.args.global_level = True

        self._reset()

        self.assertEqual({}, self.defaults.global_defaults)
        self.assertEqual(
            "mysql",
            self.defaults.user_defaults.get("db_type"),
            "--global must not touch the user file",
        )

    def test_the_message_says_it_only_affects_future_projects(self):
        """The scope is the whole point: a project that ran froze its own meta."""
        self.defaults.set_user_default("port", "9090")

        with patch("ldm_core.handlers.config.UI.info") as info:
            self._reset()

        said = " ".join(str(c) for c in info.call_args_list)
        self.assertIn("NEW projects", said)
        self.assertIn("1854", said)
