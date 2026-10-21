"""A project created from an `.ldmp` gets its creating version, and is told so (LDM-#2267).

LDM-#2163 stamped `ldm_version` for `init`/`run`. The package import -- behind
`ldm quickstart` and `ldm import <github-url>` -- never did, so
`resolve_infrastructure_mode` read every such project as pre-2.14.0 and sent
it to isolated database / sidecar search whatever the user's defaults said.
Measured on a real quickstart project: no `ldm_version`, `database_mode:
isolated`, `search_mode: sidecar`.

Three seams, each crossed rather than bracketed:

- the hydrate writes the stamp to real meta on disk, and the REAL resolver
  then reads that meta and stops returning the legacy modes;
- the creation announcement LDM-#2163 shipped with its mode change reaches the
  first real boot, though the project's meta already exists by then -- and is
  not consumed by the import's own scaffold run, which starts nothing;
- quickstart seeds for the mode the project was provisioned with, not a guess.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.tests.test_workspace import MockWorkspaceManager


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.manager = MockWorkspaceManager()
        self.manager.args.port = None
        self.manager.args.host_name = None
        self.manager.args.ssl = None
        self._td = TemporaryDirectory()
        self.project_dir = Path(self._td.name) / "aica"
        self.project_dir.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def _hydrate(self):
        from ldm_core.workspace.importer import _hydrate_ldm_package_to_project

        with patch.object(MockWorkspaceManager, "verify_runtime_environment"):
            _hydrate_ldm_package_to_project(
                self.manager.workspace,
                self.project_dir,
                "aica",
                {"tag": "2026.q3.0", "db_type": "postgresql"},
                Path(self._td.name) / "extract",
            )
        return self.manager.read_meta(self.project_dir)


class TheImportStampsANewProject(_Fixture):
    def test_a_new_project_gets_the_creating_version(self):
        from ldm_core.constants import VERSION
        from ldm_core.pipelines.run import CREATION_ANNOUNCEMENT_PENDING

        meta = self._hydrate()
        self.assertEqual(meta.get("ldm_version"), VERSION)
        self.assertEqual(meta.get(CREATION_ANNOUNCEMENT_PENDING), "true")

    def test_the_resolver_no_longer_sends_it_to_legacy_modes(self):
        """The join: the stamp is only worth writing if the resolver reads it."""
        from ldm_core.utils import resolve_infrastructure_mode

        meta = self._hydrate()
        self.assertEqual(
            resolve_infrastructure_mode("database_mode", meta, {}), "shared"
        )
        self.assertEqual(resolve_infrastructure_mode("search_mode", meta, {}), "shared")

    def test_an_existing_project_is_not_back_filled(self):
        """#2163's rule: an existing project's data is isolated, and a version
        would flip it to shared on the next boot."""
        from ldm_core.pipelines.run import CREATION_ANNOUNCEMENT_PENDING

        self.manager.write_meta(self.project_dir, {"tag": "2025.q1.0-lts"})
        meta = self._hydrate()
        self.assertNotIn("ldm_version", meta)
        self.assertNotIn(CREATION_ANNOUNCEMENT_PENDING, meta)


class TheAnnouncementReachesTheFirstBoot(unittest.TestCase):
    def _context(self, pending, is_new=False):
        from ldm_core.pipelines.run import CREATION_ANNOUNCEMENT_PENDING

        meta = {"tag": "2026.q3.0"}
        if pending:
            meta[CREATION_ANNOUNCEMENT_PENDING] = "true"
        values = {
            "is_new_project": is_new,
            "creation_announcement_pending": pending,
            "search_mode": "shared",
            "project_id": "aica",
            "project_meta": meta,
            "paths": {"root": Path("/tmp/aica")},
        }
        context = MagicMock()
        context.get.side_effect = values.get
        return context, meta

    def _announce(self, context, started):
        from ldm_core.pipelines.run import announce_creation

        manager = MagicMock()
        manager.args.search_mode = None
        manager.args.db_mode = None
        said: list[str] = []
        with (
            patch("ldm_core.pipelines.run.UI.info", side_effect=said.append),
            patch("ldm_core.pipelines.run.offer_shared_database_tip"),
        ):
            announce_creation(manager, context, "shared", started=started)
        return manager, said

    def test_a_pending_project_is_announced_on_a_real_boot(self):
        from ldm_core.pipelines.run import CREATION_ANNOUNCEMENT_PENDING

        context, meta = self._context(pending=True)
        manager, said = self._announce(context, started=True)
        self.assertTrue(any(s.startswith("Search: shared") for s in said), said)
        self.assertNotIn(CREATION_ANNOUNCEMENT_PENDING, meta)
        manager.write_meta.assert_called_once()

    def test_the_scaffold_run_does_not_consume_it(self):
        """The import's own `no_up` run passes the same completion point."""
        from ldm_core.pipelines.run import CREATION_ANNOUNCEMENT_PENDING

        context, meta = self._context(pending=True)
        manager, said = self._announce(context, started=False)
        self.assertEqual(said, [])
        self.assertEqual(meta.get(CREATION_ANNOUNCEMENT_PENDING), "true")
        manager.write_meta.assert_not_called()

    def test_an_ordinary_boot_is_not_announced(self):
        """Control: once per project, never per boot (LDM-#1508/#1510)."""
        context, _ = self._context(pending=False)
        _, said = self._announce(context, started=True)
        self.assertEqual(said, [])


class QuickstartSeedsForTheRecordedMode(unittest.TestCase):
    def test_the_seed_follows_the_provisioned_search_mode(self):
        manager = MockWorkspaceManager()
        manager.args.no_seed = False
        manager.args.replace = False
        with TemporaryDirectory() as td:
            project_dir = Path(td) / "aica"
            project_dir.mkdir()
            with (
                patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import"),
                patch.object(
                    MockWorkspaceManager,
                    "detect_project_path",
                    return_value=project_dir,
                ),
                patch(
                    "ldm_core.handlers.base.BaseHandler.read_meta",
                    # 2026.q3.0 made the old guess say "shared".
                    return_value={
                        "tag": "2026.q3.0",
                        "db_type": "postgresql",
                        "search_mode": "sidecar",
                    },
                ),
                patch.object(MockWorkspaceManager, "setup_paths", return_value={}),
                patch(
                    "ldm_core.handlers.assets.AssetService._fetch_seed",
                    return_value=True,
                ) as fetch,
                patch(
                    "ldm_core.handlers.runtime.RuntimeService.cmd_run",
                    return_value=True,
                ),
            ):
                manager.workspace.cmd_quickstart("aica")
        self.assertEqual(fetch.call_args.args[2], "sidecar")


class TheFirstBootSeesTheMarker(unittest.TestCase):
    """The seam between the meta the import wrote and the run's context. Without
    it the announcement helper is proven and never reached."""

    def _stage_flag(self, meta):
        import tempfile

        from ldm_core.pipelines.run import (
            ProjectInitializationStage,
            RunPipelineContext,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "aica"
            root.mkdir()
            (root / "meta").write_text("{}")  # an existing project: not "new"
            context = RunPipelineContext(MagicMock())
            context.manager.non_interactive = True
            context.manager.detect_project_path.return_value = root
            context.manager.read_meta.return_value = dict(meta)
            context.manager.setup_paths.return_value = {"root": root}
            context.manager.args.scale_list = None
            context.manager.args.vanilla = False
            context.manager.args.command = "quickstart"
            with patch("ldm_core.pipelines.run.UI"):
                ProjectInitializationStage().execute(context)
            return context.get("is_new_project"), context.get(
                "creation_announcement_pending"
            )

    def test_a_pending_marker_reaches_the_context(self):
        from ldm_core.pipelines.run import CREATION_ANNOUNCEMENT_PENDING

        is_new, pending = self._stage_flag({CREATION_ANNOUNCEMENT_PENDING: "true"})
        self.assertFalse(is_new, "is_new_project must keep its meaning")
        self.assertTrue(pending)

    def test_no_marker_no_flag(self):
        _, pending = self._stage_flag({})
        self.assertFalse(pending)


if __name__ == "__main__":
    unittest.main()
