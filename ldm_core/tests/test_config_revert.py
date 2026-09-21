"""LDM-#1854: `ldm config revert` returns a project to the resolved defaults.

`ldm run` freezes settings into a project's `meta`, which then overrides the
cascade for that project forever. This clears those overrides -- but only the
ones where the value is the whole of the decision. A key whose value has
already had an effect outside LDM (a name in Liferay's virtualhost table, data
in a particular engine's volume) is refused, because reverting the value does
not reverse the effect.

No binary is executed and no real `~/.ldm` is touched.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.defaults import (
    CONVENTION_DEFAULTS,
    STATE_BEARING_DEFAULTS,
    DefaultsManager,
)
from ldm_core.handlers.config import ConfigService


class _RevertBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "proj"
        self.root.mkdir()

        with patch(
            "ldm_core.defaults.get_actual_home", return_value=Path(self.tmp.name)
        ):
            self.defaults = DefaultsManager()
        self.defaults.global_path = Path(self.tmp.name) / "global.json"
        self.defaults.user_path = Path(self.tmp.name) / "user.json"
        self.defaults.global_defaults = {}
        self.defaults.user_defaults = {}

        self.manager = MagicMock()
        self.manager.defaults = self.defaults
        self.manager.non_interactive = True
        self.manager.detect_project_path.return_value = self.root
        self.manager.read_meta.side_effect = lambda _p: json.loads(
            (self.root / "meta").read_text()
        )
        self.manager.write_meta.side_effect = lambda _p, m: (
            self.root / "meta"
        ).write_text(json.dumps(m))
        self.manager.args = MagicMock(force=False, force_keys=None)
        self.service = ConfigService(self.manager)

    def _meta(self, **overrides):
        base = {
            "project_name": "proj",
            "container_name": "proj",
            "credentials": [{"email": "a@b.c", "password": "x", "type": "admin"}],
        }
        base.update(overrides)
        (self.root / "meta").write_text(json.dumps(base))
        return base

    def _read(self):
        return json.loads((self.root / "meta").read_text())

    def _revert(self):
        return self.service.cmd_revert()


class TestSafeKeysAreReverted(_RevertBase):
    def test_a_customised_safe_key_returns_to_the_default(self):
        self._meta(release_type="quarterly")
        self._revert()
        self.assertEqual(
            CONVENTION_DEFAULTS["release_type"], self._read()["release_type"]
        )

    def test_the_original_value_type_is_preserved(self):
        """A real project's meta carries `"port": 8080` -- an int, not a string."""
        self._meta(port=9099)
        self._revert()
        port = self._read()["port"]
        self.assertEqual(8080, port)
        self.assertIsInstance(port, int, "reverting must not change the type")

    def test_a_key_already_at_the_default_is_left_alone(self):
        self._meta(release_type=CONVENTION_DEFAULTS["release_type"], port=9099)
        self._revert()
        self.assertEqual(8080, self._read()["port"])

    def test_it_reverts_to_the_resolved_default_not_raw_convention(self):
        """A deliberate user-level default is what a project with no opinion gets."""
        self.defaults.set_user_default("release_type", "quarterly")
        self._meta(release_type="nightly")
        self._revert()
        self.assertEqual("quarterly", self._read()["release_type"])


class TestScopeIsLdmConfigurationOnly(_RevertBase):
    def test_keys_the_defaults_do_not_own_are_untouched(self):
        """meta also holds container names, credentials and run history."""
        self._meta(release_type="quarterly", last_run_liferay_version="2026.q3.0")
        self._revert()
        after = self._read()
        self.assertEqual("proj", after["container_name"])
        self.assertEqual("2026.q3.0", after["last_run_liferay_version"])
        self.assertEqual(
            [{"email": "a@b.c", "password": "x", "type": "admin"}],
            after["credentials"],
        )


class TestStateBearingKeysAreRefused(_RevertBase):
    def test_a_state_bearing_key_is_not_reverted_by_default(self):
        self._meta(host_name="custom.example.com")
        with self.assertRaises(SystemExit) as caught:
            self._revert()
        self.assertEqual(5, caught.exception.code, "nothing revertible -> no-op")
        self.assertEqual("custom.example.com", self._read()["host_name"])

    def test_the_refusal_states_the_reason_for_that_key(self):
        self._meta(host_name="custom.example.com", release_type="quarterly")
        with patch("ldm_core.handlers.config.UI.raw") as raw:
            self._revert()
        said = " ".join(str(c) for c in raw.call_args_list)
        self.assertIn("virtualhost", said, "must say WHY, not just refuse")

    def test_force_key_reverts_only_the_named_key(self):
        self._meta(host_name="custom.example.com", db_type="mysql")
        self.manager.args.force_keys = ["host_name"]
        self._revert()
        after = self._read()
        self.assertEqual(CONVENTION_DEFAULTS["host_name"], after["host_name"])
        self.assertEqual("mysql", after["db_type"], "db_type was not named")

    def test_the_blanket_force_reverts_every_state_bearing_key(self):
        self._meta(host_name="custom.example.com", db_type="mysql")
        self.manager.args.force = True
        self._revert()
        after = self._read()
        self.assertEqual(CONVENTION_DEFAULTS["host_name"], after["host_name"])
        self.assertEqual(CONVENTION_DEFAULTS["db_type"], after["db_type"])

    def test_force_key_refuses_a_key_that_is_not_state_bearing(self):
        """Naming a safe key means a misunderstanding -- say so rather than no-op."""
        self._meta(port=9099)
        self.manager.args.force_keys = ["port"]
        with patch("ldm_core.handlers.config.UI.die", side_effect=SystemExit(1)) as die:
            with self.assertRaises(SystemExit):
                self._revert()
        self.assertIn("not one", str(die.call_args))

    def test_every_state_bearing_key_is_one_the_defaults_own(self):
        """A classification naming a key that does not exist guards nothing."""
        self.assertLessEqual(set(STATE_BEARING_DEFAULTS), set(CONVENTION_DEFAULTS))

    def test_every_state_bearing_key_carries_a_reason(self):
        for key, reason in STATE_BEARING_DEFAULTS.items():
            with self.subTest(key=key):
                self.assertTrue(reason.strip(), f"{key} has no stated reason")


class TestTheUserIsTold(_RevertBase):
    def test_nothing_to_revert_is_an_idempotent_no_op(self):
        self._meta()
        with self.assertRaises(SystemExit) as caught:
            self._revert()
        self.assertEqual(5, caught.exception.code)

    def test_the_warning_names_the_scope(self):
        self._meta(release_type="quarterly")
        with patch("ldm_core.handlers.config.UI.warning") as warn:
            self._revert()
        said = " ".join(str(c) for c in warn.call_args_list)
        self.assertIn("LDM-controlled configuration only", said)

    def test_it_says_the_project_must_be_recreated(self):
        """Changing meta does not change a running container."""
        self._meta(release_type="quarterly")
        with patch("ldm_core.handlers.config.UI.info") as info:
            self._revert()
        self.assertIn("ldm run", " ".join(str(c) for c in info.call_args_list))

    def test_a_declined_confirmation_changes_nothing(self):
        self._meta(release_type="quarterly")
        self.manager.non_interactive = False
        with patch("ldm_core.handlers.config.UI.confirm", return_value=False) as ask:
            self._revert()
        # The prompt having been asked is what distinguishes "declined" from
        # "the command does not exist".
        ask.assert_called_once()
        self.assertEqual("quarterly", self._read()["release_type"])
