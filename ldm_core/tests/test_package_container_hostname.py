"""An `.ldmp` carries the project's container hostname, and import applies it (LDM-#2445).

Part A made the hostname a persisted project setting. A package did not record
it, so a consumer's container came up with a random hostname and a host-locked
Marketplace activation key failed its licence check.

Each half is driven through the real code, and the join between them is
crossed: the manifest the real snapshot writer produces is the one handed to
the real import paths -- the release-asset hydrate behind `quickstart` and
`ldm import <github-url>`, and `ProjectSetupStage` behind `ldm import <file>`.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.snapshot.archive import ArchiveSnapshotService
from ldm_core.tests.test_workspace import MockWorkspaceManager


def _manifest_for(project_meta):
    """The manifest `_generate_snapshot_metadata` writes for `project_meta`."""
    with TemporaryDirectory() as td:
        root = Path(td)
        facade = MagicMock()
        facade.manager.args = MagicMock(host_name=None, ssl=None)
        facade.manager.defaults = {}
        facade.manager.run_command.return_value = ""
        svc = ArchiveSnapshotService(facade)
        svc._generate_snapshot_metadata(
            "n", "ts", project_meta, root, {"root": root}, root / "snap", None, None
        )
        # Through JSON, as `write_meta` stores it and the importer reads it.
        return json.loads(json.dumps(facade.manager.write_meta.call_args.args[1]))


class TheManifestRecordsIt(unittest.TestCase):
    def test_a_configured_hostname_is_recorded(self):
        manifest = _manifest_for({"container_hostname": "liferay"})
        self.assertEqual(manifest.get("container_hostname"), "liferay")

    def test_none_configured_records_nothing(self):
        self.assertIsNone(_manifest_for({}).get("container_hostname"))


class TheReleaseAssetImportAppliesIt(unittest.TestCase):
    """`_hydrate_ldm_package_to_project`: quickstart and `import <github-url>`."""

    def setUp(self):
        self.manager = MockWorkspaceManager()
        self.manager.args.port = None
        self.manager.args.host_name = None
        self.manager.args.ssl = None
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.project_dir = Path(self._td.name) / "dsr"
        self.project_dir.mkdir()

    def _hydrate(self, manifest):
        from ldm_core.workspace.importer import _hydrate_ldm_package_to_project

        with patch.object(MockWorkspaceManager, "verify_runtime_environment"):
            _hydrate_ldm_package_to_project(
                self.manager.workspace,
                self.project_dir,
                "dsr",
                manifest,
                Path(self._td.name) / "extract",
            )
        return self.manager.read_meta(self.project_dir)

    def test_the_packaged_hostname_reaches_project_meta(self):
        manifest = _manifest_for({"container_hostname": "liferay"})
        self.assertEqual(self._hydrate(manifest).get("container_hostname"), "liferay")

    def test_the_package_wins_over_what_the_project_had(self):
        self.manager.write_meta(self.project_dir, {"container_hostname": "old"})
        meta = self._hydrate({"container_hostname": "liferay"})
        self.assertEqual(meta.get("container_hostname"), "liferay")

    def test_a_package_without_one_leaves_the_project_alone(self):
        self.manager.write_meta(self.project_dir, {"container_hostname": "mine"})
        self.assertEqual(self._hydrate({}).get("container_hostname"), "mine")

    def test_an_invalid_packaged_value_is_reported_not_written(self):
        """A package is untrusted; `ldm run` would die on this value."""
        with patch("ldm_core.utils.UI.warning") as warn:
            meta = self._hydrate({"container_hostname": "liferay.example.com"})
        self.assertNotIn("container_hostname", meta)
        self.assertIn("liferay.example.com", warn.call_args.args[0])


class TheLocalImportAppliesIt(unittest.TestCase):
    """`ProjectSetupStage`: `ldm import <file.ldmp>`."""

    def _setup_stage(self, manifest):
        from ldm_core.pipelines.import_pipeline import (
            ImportPipelineContext,
            ProjectSetupStage,
        )

        with TemporaryDirectory() as td:
            backup_dir = Path(td) / "extract"
            backup_dir.mkdir()
            (backup_dir / "meta").write_text("{}")
            project_path = Path(td) / "dsr"

            manager = MagicMock()
            manager.non_interactive = True
            manager.args = MagicMock(
                project="dsr", project_flag=None, host_name=None, ssl=None, port=None
            )
            manager.detect_project_path.return_value = project_path
            manager.setup_paths.return_value = {"root": project_path}
            manager.read_meta.side_effect = lambda p: (
                dict(manifest) if p == backup_dir else {}
            )

            context = ImportPipelineContext(manager=manager)
            context.set("backup_dir", backup_dir)
            with (
                patch("ldm_core.pipelines.import_pipeline.UI"),
                patch.object(ProjectSetupStage, "_record_linked_workspace"),
                patch.object(ProjectSetupStage, "_resolve_cloud_project_id"),
            ):
                ProjectSetupStage().execute(context)
            return manager.write_meta.call_args.args[1]

    def test_the_packaged_hostname_reaches_project_meta(self):
        manifest = _manifest_for({"container_hostname": "liferay"})
        meta = self._setup_stage(manifest)
        self.assertEqual(meta.get("container_hostname"), "liferay")

    def test_a_package_without_one_sets_nothing(self):
        self.assertNotIn("container_hostname", self._setup_stage({}))


if __name__ == "__main__":
    unittest.main()
