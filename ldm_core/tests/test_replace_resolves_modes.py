"""`quickstart --replace` resolves a pre-#2274 project's modes afresh (LDM-#2275).

#2274 stamped `ldm_version` only on a project the package import creates. A
replaced project keeps its old meta, so one made by an earlier quickstart --
no `ldm_version`, `database_mode: isolated`, `search_mode: sidecar`, the
#2267 signature -- kept booting isolated/sidecar whatever the defaults said,
and nothing announced it. #2163's reason not to back-fill (the data on disk is
isolated) does not apply to a replacement: the restore resets the project.

The rule, approved on the issue:

- no `ldm_version`: resolve the modes from defaults, as for a new project;
- `ldm_version` present: keep the recorded modes;
- either way: announce the modes on that boot, saying which of the two it was.

Seams crossed: the hydrate writes real meta and the REAL resolver reads it; the
marker reaches the run's context through `ProjectInitializationStage`; the
announcement wording; quickstart turning a confirmed prompt into a replacement;
and the seed following the resolver when the restore has recorded no mode.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.tests.test_workspace import MockWorkspaceManager

_PRE_2274_META = {
    "tag": "2025.q1.0-lts",
    "database_mode": "isolated",
    "search_mode": "sidecar",
    "use_shared_search": "false",
}


class _Hydrate(unittest.TestCase):
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

    def _hydrate(self, old_meta, replace):
        from ldm_core.workspace.importer import _hydrate_ldm_package_to_project

        self.manager.write_meta(self.project_dir, dict(old_meta))
        self.manager.args.replace = replace
        with patch.object(MockWorkspaceManager, "verify_runtime_environment"):
            _hydrate_ldm_package_to_project(
                self.manager.workspace,
                self.project_dir,
                "aica",
                {"tag": "2026.q3.0", "db_type": "postgresql"},
                Path(self._td.name) / "extract",
            )
        return self.manager.read_meta(self.project_dir)

    @staticmethod
    def _resolved(meta):
        from ldm_core.utils import resolve_infrastructure_mode

        return (
            resolve_infrastructure_mode("database_mode", meta, {}),
            resolve_infrastructure_mode("search_mode", meta, {}),
        )


class AReplacedUnversionedProjectIsResolvedAfresh(_Hydrate):
    def test_it_is_stamped_and_its_recorded_modes_are_dropped(self):
        from ldm_core.constants import VERSION
        from ldm_core.pipelines.run import CREATION_ANNOUNCEMENT_PENDING

        meta = self._hydrate(_PRE_2274_META, replace=True)
        self.assertEqual(meta.get("ldm_version"), VERSION)
        for key in ("database_mode", "search_mode", "use_shared_search"):
            self.assertNotIn(key, meta)
        self.assertEqual(meta.get(CREATION_ANNOUNCEMENT_PENDING), "true")

    def test_the_resolver_then_follows_the_defaults(self):
        """The join: a stamp alone is not enough while the old modes remain."""
        meta = self._hydrate(_PRE_2274_META, replace=True)
        self.assertEqual(self._resolved(meta), ("shared", "shared"))


class AReplacedVersionedProjectKeepsItsModes(_Hydrate):
    def test_its_modes_survive_and_are_marked_kept(self):
        from ldm_core.pipelines.run import (
            CREATION_ANNOUNCEMENT_PENDING,
            CREATION_MODES_KEPT,
        )

        old = {**_PRE_2274_META, "ldm_version": "2.26.6"}
        meta = self._hydrate(old, replace=True)
        self.assertEqual(meta.get("ldm_version"), "2.26.6")
        self.assertEqual(self._resolved(meta), ("isolated", "sidecar"))
        self.assertEqual(meta.get(CREATION_ANNOUNCEMENT_PENDING), CREATION_MODES_KEPT)


class WithoutReplaceNothingIsBackFilled(_Hydrate):
    def test_the_2163_rule_stands(self):
        """Control: an import over an existing project that was not asked to
        replace it keeps #2163's no-back-fill rule."""
        from ldm_core.pipelines.run import CREATION_ANNOUNCEMENT_PENDING

        meta = self._hydrate(_PRE_2274_META, replace=False)
        self.assertNotIn("ldm_version", meta)
        self.assertEqual(self._resolved(meta), ("isolated", "sidecar"))
        self.assertNotIn(CREATION_ANNOUNCEMENT_PENDING, meta)


class TheKeptMarkerReachesTheBoot(unittest.TestCase):
    def test_the_stage_reads_kept_as_pending_and_kept(self):
        import tempfile

        from ldm_core.pipelines.run import (
            CREATION_ANNOUNCEMENT_PENDING,
            CREATION_MODES_KEPT,
            ProjectInitializationStage,
            RunPipelineContext,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "aica"
            root.mkdir()
            (root / "meta").write_text("{}")
            context = RunPipelineContext(MagicMock())
            context.manager.non_interactive = True
            context.manager.detect_project_path.return_value = root
            context.manager.read_meta.return_value = {
                CREATION_ANNOUNCEMENT_PENDING: CREATION_MODES_KEPT
            }
            context.manager.setup_paths.return_value = {"root": root}
            context.manager.args.scale_list = None
            context.manager.args.vanilla = False
            context.manager.args.command = "quickstart"
            with patch("ldm_core.pipelines.run.UI"):
                ProjectInitializationStage().execute(context)
        self.assertTrue(context.get("creation_announcement_pending"))
        self.assertTrue(context.get("creation_modes_kept"))


class TheAnnouncementSaysTheModesWereKept(unittest.TestCase):
    def _said(self, kept, search_flag=None):
        from ldm_core.pipelines.run import announce_resolved_infrastructure

        manager = MagicMock()
        manager.args.search_mode = search_flag
        manager.args.db_mode = None
        said: list[str] = []
        with patch("ldm_core.pipelines.run.UI.info", side_effect=said.append):
            announce_resolved_infrastructure(
                manager, "sidecar", "isolated", True, "aica", kept=kept
            )
        return said[0]

    def test_kept_modes_are_not_called_default(self):
        line = self._said(kept=True)
        self.assertIn("Search: sidecar (kept from the project it replaced)", line)
        self.assertIn("Database: isolated (kept from the project it replaced)", line)

    def test_a_flag_still_wins(self):
        self.assertIn("Search: sidecar (chosen with a flag)", self._said(True, "x"))


class _Quickstart(unittest.TestCase):
    def _run(self, meta, *, interactive=False, defaults=None, existing=True):
        manager = MockWorkspaceManager()
        manager.args.no_seed = False
        manager.args.replace = False
        manager.args.name = None
        manager.args.search_mode = None
        manager.non_interactive = not interactive
        if defaults is not None:
            manager.defaults = defaults
        seen: dict[str, object] = {}
        with TemporaryDirectory() as td:
            project_dir = Path(td) / "aica"
            project_dir.mkdir()
            if existing:
                (project_dir / "meta").write_text("{}")
            with (
                patch(
                    "ldm_core.handlers.workspace.WorkspaceService.cmd_import",
                    side_effect=lambda *_a, **_k: seen.setdefault(
                        "replace", manager.args.replace
                    ),
                ),
                patch.object(
                    MockWorkspaceManager,
                    "detect_project_path",
                    return_value=project_dir,
                ),
                patch("ldm_core.handlers.workspace.WorkspaceService._ensure_stopped"),
                patch("ldm_core.ui.UI.confirm", return_value=True),
                patch(
                    "ldm_core.handlers.base.BaseHandler.read_meta", return_value=meta
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
        return seen.get("replace"), fetch


class AConfirmedPromptIsAReplacement(_Quickstart):
    def test_the_import_sees_replace(self):
        replace, _ = self._run({"tag": "2026.q3.0"}, interactive=True)
        self.assertIs(replace, True)


class TheSeedFollowsTheResolver(_Quickstart):
    def test_with_no_recorded_mode_the_configured_default_is_seeded(self):
        """2026.q3.0 makes the tag guess say "shared"; the config says sidecar,
        and so will the boot."""
        from ldm_core.constants import VERSION

        _, fetch = self._run(
            {"tag": "2026.q3.0", "db_type": "postgresql", "ldm_version": VERSION},
            defaults={"search_mode": "sidecar"},
            existing=False,
        )
        self.assertEqual(fetch.call_args.args[2], "sidecar")


if __name__ == "__main__":
    unittest.main()
