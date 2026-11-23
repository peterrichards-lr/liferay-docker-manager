"""LDM-#2256: tell the owner of an existing project, once, that its mode
disagrees with their explicit default.

Projects created by `init`/`run` between v2.14.0 and the LDM-#2163 fix carry
no `ldm_version`, so the legacy guard in `resolve_infrastructure_mode` keeps
them on sidecar/isolated whatever the user's defaults say. LDM-#2163 refused to
back-fill the version -- their on-disk state is isolated, and a version would
flip them to shared on the next boot -- so the only remedy is to tell the owner
and let them choose.

These tests drive `announce_creation`, the function both completion points of
a run call, rather than the helper alone: a helper proven in isolation says
nothing about whether a boot ever reaches it.

The defaults object is a real `DefaultsManager` with its layers set in memory,
so `has_explicit` and `get` are the shipped code. Nothing reads or writes the
real `~/.ldmrc`.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core.defaults import DefaultsManager
from ldm_core.pipelines.run import (
    MODE_DEFAULT_NOTICE_SHOWN,
    RunPipelineContext,
    announce_creation,
)


def _defaults(**user):
    """A DefaultsManager whose layers are in memory, never on disk."""
    dm = DefaultsManager.__new__(DefaultsManager)
    dm.global_defaults = {}
    dm.user_defaults = dict(user)
    return dm


class TheNoticeSpeaksOnceAndOnlyWhenItShould(unittest.TestCase):
    def setUp(self):
        # The persisted shape of a project created by `init`/`run` before the
        # LDM-#2163 fix: modes recorded (LDM-#1359), no `ldm_version`.
        self.meta = {
            "project_name": "demo",
            "search_mode": "sidecar",
            "database_mode": "isolated",
        }
        self.manager = MagicMock()
        self.manager.args.search_mode = None
        self.manager.args.sidecar = False
        self.manager.args.database_mode = None
        self.manager.defaults = _defaults(search_mode="shared", database_mode="shared")

    def _boot(self, search_mode="sidecar", db_mode="isolated", started=True):
        context = RunPipelineContext(self.manager)
        context.set("project_id", "demo")
        context.set("project_meta", self.meta)
        context.set("paths", {"root": "/tmp/not-touched/demo"})
        context.set("is_new_project", False)
        context.set("search_mode", search_mode)
        said: list[str] = []
        with (
            patch("ldm_core.pipelines.run.UI.info", side_effect=said.append),
            patch("ldm_core.pipelines.run.UI.detail") as detail,
            patch("ldm_core.pipelines.run.offer_shared_database_tip"),
        ):
            announce_creation(self.manager, context, db_mode, started=started)
        self.assertFalse(detail.called, "must not hide behind --info (LDM-#1790)")
        return " ".join(said)

    def test_it_speaks_when_explicit_and_mismatched(self):
        said = self._boot()
        self.assertIn("Search: sidecar, Database: isolated", said)
        self.assertIn("your config default is Search: shared, Database: shared", said)
        self.assertIn("ldm run demo --search-mode shared --database-mode shared", said)
        # Point 3 of the issue: there is no in-place migration, and a command
        # offered without saying so invites data loss.
        self.assertIn("does not migrate its data", said)
        self.assertEqual(self.meta.get(MODE_DEFAULT_NOTICE_SHOWN), "true")
        self.manager.write_meta.assert_called_once_with(
            "/tmp/not-touched/demo", self.meta
        )

    def test_the_offered_command_parses(self):
        # The creation announcement shipped `--db-mode`, which `ldm run` does
        # not accept. Parse what this one offers with the real parser.
        from ldm_core.cli import get_parser

        said = self._boot()
        command = said.split("To move it: ldm ", 1)[1].split("  ", 1)[0].split()
        args = get_parser()[0].parse_args(command)
        self.assertEqual(
            (args.project, args.search_mode, args.database_mode),
            ("demo", "shared", "shared"),
        )

    def test_it_names_only_the_axis_that_disagrees(self):
        self.manager.defaults = _defaults(database_mode="shared")
        said = self._boot()
        self.assertIn("Database: isolated", said)
        self.assertNotIn("Search:", said)
        self.assertIn("ldm run demo --database-mode shared", said)
        self.assertNotIn("--search-mode", said)

    def test_silent_when_the_default_is_not_explicit(self):
        # Convention says shared, but nobody chose it: the project is not
        # ignoring a decision, so there is nothing to tell.
        self.manager.defaults = _defaults()
        self.assertEqual(self._boot(), "")
        self.assertNotIn(MODE_DEFAULT_NOTICE_SHOWN, self.meta)
        self.manager.write_meta.assert_not_called()

    def test_silent_when_the_modes_agree(self):
        self.manager.defaults = _defaults(
            search_mode="sidecar", database_mode="isolated"
        )
        self.assertEqual(self._boot(), "")
        self.manager.write_meta.assert_not_called()

    def test_silent_the_second_time(self):
        self.assertNotEqual(self._boot(), "")
        self.assertEqual(self._boot(), "")
        self.manager.write_meta.assert_called_once()

    def test_it_extinguishes_once_acted_on(self):
        # The user ran the offered command: the flags win this run, and the
        # persisted mode agrees with the default on every run after.
        self.manager.args.search_mode = "shared"
        self.manager.args.database_mode = "shared"
        self.assertEqual(self._boot(search_mode="shared", db_mode="shared"), "")
        self.manager.args.search_mode = None
        self.manager.args.database_mode = None
        self.meta.update({"search_mode": "shared", "database_mode": "shared"})
        self.assertEqual(self._boot(search_mode="shared", db_mode="shared"), "")
        self.manager.write_meta.assert_not_called()

    def test_it_never_writes_ldm_version_or_changes_the_mode(self):
        # LDM-#2163: back-filling the version flips an isolated project to
        # shared on its next boot -- the hijack the version guard prevents.
        self._boot()
        written = self.manager.write_meta.call_args.args[1]
        self.assertNotIn("ldm_version", written)
        self.assertEqual(written["search_mode"], "sidecar")
        self.assertEqual(written["database_mode"], "isolated")

    def test_silent_when_the_stack_did_not_start(self):
        self.assertEqual(self._boot(started=False), "")

    def test_silent_for_a_mode_one_flag_cannot_reach(self):
        # `external` needs a JDBC URL; a one-line command would not be honest.
        self.manager.defaults = _defaults(database_mode="external")
        self.assertEqual(self._boot(), "")


if __name__ == "__main__":
    unittest.main()
