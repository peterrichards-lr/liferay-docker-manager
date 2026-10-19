"""LDM-#2174: `ldm config` must answer from the store that decides.

`ldm config` is two stores behind one command name:

- the **document root** of `~/.ldmrc`, which `ldm config set` writes
- the **`defaults` block**, which `ldm defaults` writes and which
  `DefaultsManager.get_resolved()` reads -- the resolver every
  project-creation decision consults

`DefaultsManager._load` returns `data["defaults"]` whenever that key is
present, so a top-level `database_mode` is invisible to the resolver. From a
real `~/.ldmrc` on a maintainer's machine:

    {"defaults": {"database_mode": "isolated"}, "database_mode": "shared"}

`ldm config get database_mode` reported `shared`. Every project got
`isolated`. The command reported the opposite of what the system did.

Every fixture here carries that conflict shape deliberately. As the issue puts
it: a test written against a tidy config cannot see this defect at all.

**Part of #2174 was already fixed and is not re-tested here.** LDM-#1651
already refuses `ldm config set` for a `CONVENTION_DEFAULTS` key, and the
success wording is already "Configuration key 'x' set to 'y'" rather than the
"Set user default" the issue quotes. `TheGuardCoversTheWholeDefaultsBlock`
covers only the gap that remained: a key living in someone's `defaults` block
WITHOUT being a convention, which that guard does not see.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.defaults import DefaultsManager
from ldm_core.handlers.config import ConfigService
from ldm_core.tests.test_config import MockConfigManager

# The shape from the issue: the same key in both stores, with different
# values. `database_mode` is a CONVENTION_DEFAULTS key; `ngrok_authtoken` is
# not, which is the case the LDM-#1651 guard does not cover.
CONFLICTED_LDMRC = {
    "defaults": {
        "database_mode": "isolated",
        "ngrok_authtoken": "secret-value-in-the-store-that-decides",
    },
    "database_mode": "shared",
    "share_domain": "dev.example.com",
}


class ConfigGetAnswersFromTheCascade(unittest.TestCase):
    def setUp(self):
        self.manager = MockConfigManager()
        self.config = ConfigService(self.manager)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.fake_home = Path(self.temp_dir.name)
        (self.fake_home / ".ldmrc").write_text(json.dumps(CONFLICTED_LDMRC))

        for module in ("ldm_core.handlers.config", "ldm_core.defaults"):
            patcher = patch(f"{module}.get_actual_home", return_value=self.fake_home)
            patcher.start()
            self.addCleanup(patcher.stop)

        # A real resolver over the real file, so the cascade under test is the
        # one projects actually use rather than a stand-in that agrees with the
        # assertion by construction.
        self.manager.defaults = DefaultsManager()

    def _get(self, key):
        with patch("builtins.print") as mock_print:
            self.config.cmd_config(key=key)
        return [c.args[0] for c in mock_print.call_args_list if c.args]

    def test_a_conflicted_key_reports_what_projects_actually_get(self):
        # The whole defect in one assertion: top level says "shared", the
        # cascade says "isolated", and projects get "isolated".
        self.assertEqual(self._get("database_mode"), ["isolated"])

    def test_the_reported_value_matches_the_resolver(self):
        # The round trip the issue names as the contract.
        resolved = self.manager.defaults.get_resolved()["database_mode"]
        self.assertEqual(self._get("database_mode"), [resolved])

    def test_a_key_set_only_by_convention_is_not_reported_as_missing(self):
        # `search_mode` has a convention default and is what every project
        # resolves against; this used to die with "not found".
        printed = self._get("search_mode")
        self.assertEqual(len(printed), 1)
        self.assertTrue(printed[0], "search_mode resolved to an empty value")

    def test_a_plain_top_level_key_still_answers_from_the_root(self):
        # The cascade must not swallow keys that genuinely live at the root.
        self.assertEqual(self._get("share_domain"), ["dev.example.com"])

    def test_an_unset_key_is_still_an_error(self):
        with (
            patch("ldm_core.handlers.config.UI.die", side_effect=SystemExit),
            self.assertRaises(SystemExit),
        ):
            self.config.cmd_config(key="no_such_key_anywhere")


class TheBareListingDoesNotHideTheDecidingStore(unittest.TestCase):
    def setUp(self):
        self.manager = MockConfigManager()
        self.config = ConfigService(self.manager)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.fake_home = Path(self.temp_dir.name)
        (self.fake_home / ".ldmrc").write_text(json.dumps(CONFLICTED_LDMRC))
        for module in ("ldm_core.handlers.config", "ldm_core.defaults"):
            patcher = patch(f"{module}.get_actual_home", return_value=self.fake_home)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager.defaults = DefaultsManager()

    def _listing(self):
        with patch("builtins.print") as mock_print:
            self.config.cmd_config()
        return "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)

    def test_the_defaults_block_is_expanded_not_dumped_as_one_value(self):
        listing = self._listing()
        # The old output was a single line: `defaults = {'database_mode': ...}`
        self.assertNotIn("{'database_mode'", listing)
        self.assertIn("defaults:", listing)
        self.assertIn("isolated", listing)

    def test_a_shadowed_top_level_key_is_marked_as_such(self):
        self.assertIn("shadowed", self._listing())

    def test_a_credential_inside_the_defaults_block_is_redacted(self):
        # LDM-#1970 masks credential-shaped KEY NAMES, and `defaults` is not
        # one -- so dumping the block printed every credential inside it
        # verbatim, however carefully the top level was masked.
        listing = self._listing()
        self.assertNotIn("secret-value-in-the-store-that-decides", listing)
        self.assertIn("[REDACTED]", listing)


class TheGuardCoversTheWholeDefaultsBlock(unittest.TestCase):
    """LDM-#1651 refuses CONVENTION_DEFAULTS keys. This is the rest."""

    def setUp(self):
        self.manager = MockConfigManager()
        self.config = ConfigService(self.manager)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.fake_home = Path(self.temp_dir.name)
        (self.fake_home / ".ldmrc").write_text(json.dumps(CONFLICTED_LDMRC))
        for module in ("ldm_core.handlers.config", "ldm_core.defaults"):
            patcher = patch(f"{module}.get_actual_home", return_value=self.fake_home)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager.defaults = DefaultsManager()

    @patch("ldm_core.handlers.config.UI.die", side_effect=SystemExit)
    def test_a_non_convention_key_already_in_defaults_is_refused(self, mock_die):
        # `ngrok_authtoken` is in the defaults block but is not a convention,
        # so the LDM-#1651 guard does not see it. Writing it at the root
        # shadows nothing and changes nothing.
        with self.assertRaises(SystemExit):
            self.config.cmd_config(key="ngrok_authtoken", value="new-token")
        message = str(mock_die.call_args)
        self.assertIn("ldm defaults", message)

    @patch("ldm_core.handlers.config.UI.die", side_effect=SystemExit)
    def test_a_genuinely_new_top_level_key_is_still_accepted(self, mock_die):
        # The refusal must not become a blanket one; plain config values are
        # exactly what this command is for.
        self.config.cmd_config(key="brand_new_key", value="fine")
        mock_die.assert_not_called()
        written = json.loads((self.fake_home / ".ldmrc").read_text())
        self.assertEqual(written["brand_new_key"], "fine")
