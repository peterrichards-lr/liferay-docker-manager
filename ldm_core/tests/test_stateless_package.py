"""Stateless `.ldmp` packages: assets only, deployed onto a vanilla instance (LDM-#2493).

`ldm package` always produced a snapshot -- database dump, `data/`, OSGi state
-- and import always restored one. A package of client extensions, fragments
and configs had no way to say "boot a fresh Liferay and deploy me", so the
samples pack could not be built with `ldm package` without shipping the CI
runner's state to every user.

These run the real `cmd_package` on a real project tree, read the package that
comes out, and hand that same package to the real `cmd_restore` -- the call
every import path (`ldm import <file>`, `ldm import <github-url>`, quickstart)
ends in -- so the join between producer and consumer is crossed, not modelled.
"""

import io
import json
import os
import tarfile
import tempfile
import typing
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.snapshot.package_kind import (
    PACKAGE_KIND_KEY,
    STATEFUL,
    STATELESS,
    is_stateless,
    package_kind,
)
from ldm_core.tests.test_snapshot import MockSnapshotManager
from ldm_core.utils import safe_extract

LICENCE = b"<?xml version='1.0'?><license><owner>Publisher Inc</owner></license>"

# What a project holds. The first group is what a stateless package ships.
ASSETS = {
    "osgi/client-extensions/my-cx.zip": b"cx",
    "client-extensions/my-cx/dist/my-cx.zip": b"cx-built",
    # An expanded container client extension: `ldm run` builds its sidecar.
    "client-extensions/my-svc/Dockerfile": b"FROM liferay/jar-runner\n",
    "client-extensions/my-svc/LCP.json": b'{"id": "my-svc"}',
    "client-extensions/my-svc/client-extension.yaml": b"my-svc:\n  type: x\n",
    # Shipped as the stateful snapshot ships the tree: whole, unfiltered.
    "client-extensions/my-cx/src/index.js": b"source",
    "client-extensions/my-cx/node_modules/dep/index.js": b"dep",
    "deploy/my-fragments.zip": b"fragments",
    "osgi/configs/com.example.Thing.config": b'enabled=B"true"',
    "configs/common/portal-ext.properties": b"x=1",
    "osgi/modules/my-module.jar": b"jar",
    "files/portal-ext.properties": b"company.default.web.id=demo\n",
    ".ldm/fragment-overrides.json": b'{"overrides": []}',
    "scripts/setup.sh": b"echo hello\n",
}
STATE = {
    "data/document_library/0/doc.bin": b"doc",
    "osgi/state/org.eclipse.osgi/.manager": b"state",
    "files/generated/cache.bin": b"generated",
    "logs/liferay.log": b"log",
    "snapshots/20261101_100000/meta": b"{}",
    "routes/default/my-cx/my-cx.client-extension-config.properties": b"secret",
}
PUBLISHER_ONLY = {"deploy/activation-key-dxp.xml": LICENCE}

PROJECT_META = {
    "tag": "2026.q1.4-lts",
    "db_type": "mysql",
    "container_hostname": "liferay",
    "host_name": "samples.local",
}


def _write_tree(root, files):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _payload_names(package_dir):
    with tarfile.open(package_dir / "files.tar.gz", "r:gz") as tar:
        return {m.name for m in tar.getmembers() if m.isfile()}


class _Home(unittest.TestCase):
    """An isolated LDM_HOME and a project tree, both in a temp directory."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        env = patch.dict(os.environ, {"LDM_HOME": str(self.base / "home")})
        env.start()
        self.addCleanup(env.stop)


class _PackageHarness(_Home):
    def setUp(self):
        super().setUp()
        self.root = self.base / "demo"
        self.root.mkdir()
        _write_tree(self.root, {**ASSETS, **STATE, **PUBLISHER_ONLY})
        self.out = self.base / "out"
        self.manager = MockSnapshotManager()
        self.manager.write_meta(self.root, dict(PROJECT_META))

    def paths(self, root):
        return {
            "root": root,
            "backups": root / "snapshots",
            "data": root / "data",
            "deploy": root / "deploy",
            "files": root / "files",
            "configs": root / "osgi" / "configs",
            "state": root / "osgi" / "state",
            "modules": root / "osgi" / "modules",
            "cx": root / "osgi" / "client-extensions",
            "ce_dir": root / "client-extensions",
            "scripts": root / "scripts",
        }

    def package(self, **kwargs):
        """The real cmd_package. Returns the extracted package directory."""
        with (
            patch(
                "ldm_core.handlers.base.BaseHandler.detect_project_path",
                return_value=self.root,
            ),
            patch(
                "ldm_core.handlers.base.BaseHandler.setup_paths",
                side_effect=self.paths,
            ),
            patch.object(self.manager.snapshot, "cmd_snapshot") as self.cmd_snapshot,
            patch.object(
                self.manager.snapshot.database, "_snapshot_database"
            ) as self.db_dump,
            patch("ldm_core.ui.UI.info"),
            patch("ldm_core.ui.UI.success"),
        ):
            self.manager.snapshot.cmd_package(
                project_id="demo",
                output_dir=str(self.out),
                repo="owner/samples",
                **kwargs,
            )
        unpacked = self.out / "unpacked"
        with tarfile.open(self.out / "demo.ldmp", "r:gz") as tar:
            safe_extract(tar, unpacked)
        return unpacked


class AStatelessPackageHoldsAssetsOnly(_PackageHarness):
    def setUp(self):
        super().setUp()
        self.pkg = self.package(stateless=True)
        self.manifest = json.loads((self.pkg / "meta").read_text())

    def test_it_declares_itself_stateless(self):
        self.assertEqual(self.manifest.get(PACKAGE_KIND_KEY), STATELESS)

    def test_no_snapshot_or_database_dump_is_taken(self):
        self.cmd_snapshot.assert_not_called()
        self.db_dump.assert_not_called()

    def test_the_package_has_only_the_manifest_and_the_payload(self):
        """No database dump, no search snapshot, no snapshots/ beside them."""
        top = {p.name for p in self.pkg.iterdir()}
        self.assertEqual(top, {"meta", "files.tar.gz", "files.tar.gz.sha256"})

    def test_the_payload_ships_every_asset(self):
        self.assertTrue(set(ASSETS) <= _payload_names(self.pkg))

    def test_the_payload_ships_no_state(self):
        shipped = _payload_names(self.pkg)
        self.assertEqual(shipped & set(STATE), set(), "state was packaged")
        for prefix in ("data/", "osgi/state", "snapshots/", "logs/", "routes/"):
            self.assertFalse(
                [n for n in shipped if n.startswith(prefix)],
                f"{prefix} was packaged",
            )

    def test_an_expanded_container_client_extension_is_shipped(self):
        """The live AICA check: zips only, so no microservice sidecar."""
        shipped = _payload_names(self.pkg)
        for name in ("Dockerfile", "LCP.json"):
            self.assertIn(f"client-extensions/my-svc/{name}", shipped)

    def test_publisher_material_is_still_left_out(self):
        self.assertNotIn("deploy/activation-key-dxp.xml", _payload_names(self.pkg))

    def test_the_manifest_fields_are_written_as_for_a_stateful_package(self):
        self.assertEqual(self.manifest.get("tag"), "2026.q1.4-lts")
        self.assertEqual(self.manifest.get("db_type"), "mysql")
        self.assertEqual(self.manifest.get("container_hostname"), "liferay")
        self.assertEqual(self.manifest.get("github_repository"), "owner/samples")
        self.assertEqual(self.manifest.get("includes_database"), "false")
        self.assertEqual(self.manifest.get("includes_volume_assets"), "false")
        self.assertIsNone(self.manifest.get("search_snapshot"))
        self.assertIn("my-cx.zip", self.manifest.get("client_extensions"))

    def test_the_payload_keeps_the_layout_an_older_samples_reader_copies(self):
        """LDM v2.26.x `--samples` extracts files.tar.gz and copies the tree
        over the project: the payload must be project-root-relative."""
        payload = self.base / "payload"
        with tarfile.open(self.pkg / "files.tar.gz", "r:gz") as tar:
            safe_extract(tar, payload)
        self.assertTrue((payload / "osgi/client-extensions/my-cx.zip").is_file())
        self.assertTrue((payload / ".ldm/fragment-overrides.json").is_file())


class AStatefulPackageIsUnchanged(_PackageHarness):
    def test_the_stateful_manifest_carries_no_kind(self):
        """Absent means stateful; a stateful manifest is what it always was."""
        snap = self.root / "snapshots" / "20261123_100000"
        snap.mkdir(parents=True)
        with tarfile.open(snap / "files.tar.gz", "w:gz") as tar:
            info = tarfile.TarInfo("data/document_library/0/doc.bin")
            info.size = 3
            tar.addfile(info, io.BytesIO(b"doc"))
        (snap / "meta").write_text('{"tag": "2026.q1.4-lts"}')
        with patch(
            "ldm_core.snapshot.utils.UtilsSnapshotService._list_backups",
            return_value=[{"path": snap}],
        ):
            pkg = self.package(use_latest=True)
        self.assertNotIn(PACKAGE_KIND_KEY, json.loads((pkg / "meta").read_text()))
        self.assertIn("data/document_library/0/doc.bin", _payload_names(pkg))

    def test_stateless_with_a_snapshot_is_refused(self):
        for kwargs in ({"use_latest": True}, {"snapshot": "20261101_100000"}):
            with self.subTest(**kwargs), self.assertRaises(SystemExit) as raised:
                self.package(stateless=True, **kwargs)
            self.assertEqual(raised.exception.code, 1)


class TheKindIsReadSafely(unittest.TestCase):
    def test_a_manifest_without_the_field_is_stateful(self):
        self.assertEqual(package_kind({"tag": "2026.q1.4-lts"}), STATEFUL)
        self.assertFalse(is_stateless({}))

    def test_only_stateless_selects_the_stateless_path(self):
        self.assertTrue(is_stateless({PACKAGE_KIND_KEY: "stateless"}))
        self.assertTrue(is_stateless({PACKAGE_KIND_KEY: " Stateless "}))
        for value in ("stateful", "assets", "", None, 1):
            self.assertFalse(is_stateless({PACKAGE_KIND_KEY: value}), value)

    def test_a_non_dict_is_stateful(self):
        self.assertFalse(is_stateless(None))
        self.assertFalse(is_stateless(MagicMock()))


class _RestoreHarness(_PackageHarness):
    """The package from the real `cmd_package`, into the real `cmd_restore`."""

    def _flag_reindex(self, *_args):
        self.calls.append("reindex")
        return True

    def restore(self, package_dir):
        target = self.base / "imported"
        target.mkdir()
        self.calls: list[str] = []
        consumer = MockSnapshotManager()
        consumer.args.verify = True
        consumer.args.no_run = True
        consumer.args.list = False
        setattr(consumer, "infra", MagicMock())  # noqa: B010
        consumer.write_meta(target, {"db_type": "mysql"})
        snap = consumer.snapshot
        with (
            patch(
                "ldm_core.handlers.base.BaseHandler.detect_project_path",
                return_value=target,
            ),
            patch(
                "ldm_core.handlers.base.BaseHandler.setup_paths",
                side_effect=self.paths,
            ),
            patch.object(snap.volumes, "hydrate_named_volumes_with_sync_wait"),
            patch.object(
                snap.database,
                "_restore_database",
                side_effect=lambda *_a, **_k: self.calls.append("restore-db"),
            ),
            patch.object(
                snap.search,
                "_restore_search",
                side_effect=lambda *_a, **_k: self.calls.append("restore-search"),
            ),
            patch.object(
                snap.custom_containers,
                "_restore_custom_images",
                side_effect=lambda *_a, **_k: self.calls.append("restore-images"),
            ),
            patch.object(
                snap,
                "flag_reindex",
                side_effect=self._flag_reindex,
            ),
            patch("ldm_core.docker_service.DockerService.exists", return_value=True),
            patch("time.sleep"),
        ):
            snap.cmd_restore("imported", backup_dir=package_dir, no_run=True)
        return target


class AStatelessImportRestoresNothing(_RestoreHarness):
    def setUp(self):
        super().setUp()
        self.target = self.restore(self.package(stateless=True))

    def test_no_database_search_or_image_restore_and_no_reindex(self):
        self.assertEqual(self.calls, [])

    def test_the_assets_are_deployed(self):
        for name in ASSETS:
            self.assertTrue((self.target / name).is_file(), name)
        self.assertFalse((self.target / "data").exists())

    def test_the_container_client_extension_reaches_the_project(self):
        svc = self.target / "client-extensions" / "my-svc"
        self.assertTrue((svc / "Dockerfile").is_file())
        self.assertTrue((svc / "LCP.json").is_file())
        self.assertFalse((self.target / "osgi" / "state").exists())

    def test_the_manifest_fields_still_reach_the_project(self):
        meta = MockSnapshotManager().read_meta(self.target)
        self.assertEqual(meta.get("tag"), "2026.q1.4-lts")


class AStatefulImportRestoresAsBefore(_RestoreHarness):
    def test_a_package_without_the_field_restores_everything(self):
        pkg = self.package(stateless=True)
        manifest = json.loads((pkg / "meta").read_text())
        del manifest[PACKAGE_KIND_KEY]
        (pkg / "meta").write_text(json.dumps(manifest))
        self.restore(pkg)
        self.assertEqual(
            self.calls,
            ["restore-db", "restore-search", "restore-images", "reindex"],
        )


class EveryImportPathEndsInTheGatedRestore(unittest.TestCase):
    """The gate lives in `cmd_restore`; each import path must reach it with
    the extracted package, or a stateless package would skip it."""

    def test_the_release_asset_path_restores_from_the_package(self):
        from ldm_core.tests.test_workspace import MockWorkspaceManager
        from ldm_core.workspace.importer import _hydrate_ldm_package_to_project

        manager = MockWorkspaceManager()
        manager.args.port = None
        manager.args.host_name = None
        manager.args.ssl = None
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "dsr"
            project.mkdir()
            extract = Path(td) / "extract"
            with patch.object(MockWorkspaceManager, "verify_runtime_environment"):
                _hydrate_ldm_package_to_project(
                    manager.workspace,
                    project,
                    "dsr",
                    {PACKAGE_KIND_KEY: STATELESS, "tag": "2026.q1.4-lts"},
                    extract,
                )
            meta = manager.read_meta(project)
        manager.snapshot.cmd_restore.assert_called_once()
        self.assertEqual(
            manager.snapshot.cmd_restore.call_args.kwargs.get("backup_dir"), extract
        )
        # Quickstart reads this to boot the project without a seed.
        self.assertEqual(meta.get(PACKAGE_KIND_KEY), STATELESS)

    def test_a_stateful_release_asset_leaves_no_kind_in_the_project(self):
        from ldm_core.tests.test_workspace import MockWorkspaceManager
        from ldm_core.workspace.importer import _hydrate_ldm_package_to_project

        manager = MockWorkspaceManager()
        manager.args.port = None
        manager.args.host_name = None
        manager.args.ssl = None
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "dsr"
            project.mkdir()
            manager.write_meta(project, {PACKAGE_KIND_KEY: STATELESS})
            with patch.object(MockWorkspaceManager, "verify_runtime_environment"):
                _hydrate_ldm_package_to_project(
                    manager.workspace, project, "dsr", {}, Path(td) / "x"
                )
            self.assertNotIn(PACKAGE_KIND_KEY, manager.read_meta(project))

    def test_the_local_file_path_restores_from_the_package(self):
        from ldm_core.pipelines.import_pipeline import (
            DatabaseRestoreStage,
            ImportPipelineContext,
        )

        manager = MagicMock()
        manager.read_meta.return_value = {PACKAGE_KIND_KEY: STATELESS}
        context = ImportPipelineContext(manager=manager, project_name="dsr")
        context.set("is_ldmp", True)
        context.set("backup_dir", Path("/pkg"))
        with patch("ldm_core.pipelines.import_pipeline.UI"):
            DatabaseRestoreStage().execute(context)
        manager.snapshot.cmd_restore.assert_called_once_with(
            "dsr", backup_dir=Path("/pkg")
        )


class QuickstartBootsAStatelessPackageVanilla(unittest.TestCase):
    """Quickstart seeds a package with no database. Not a stateless one."""

    def _quickstart(self, project_meta):
        from ldm_core.tests.test_workspace import MockWorkspaceManager

        manager = MockWorkspaceManager()
        manager.args.no_seed = False
        manager.args.share = False
        with (
            tempfile.TemporaryDirectory() as td,
            patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import"),
            patch.object(
                MockWorkspaceManager, "detect_project_path", return_value=Path(td)
            ),
            patch(
                "ldm_core.handlers.base.BaseHandler.read_meta",
                return_value=dict(project_meta),
            ),
            patch("ldm_core.handlers.base.BaseHandler.write_meta"),
            patch.object(MockWorkspaceManager, "setup_paths", return_value={}),
            patch.object(MockWorkspaceManager, "ensure_hostnames_resolve"),
            patch(
                "ldm_core.handlers.assets.AssetService._fetch_seed", return_value=True
            ) as fetch_seed,
            patch(
                "ldm_core.handlers.runtime.RuntimeService.cmd_run", return_value=False
            ),
        ):
            manager.workspace.cmd_quickstart("aica")
        return fetch_seed

    IMPORTED: typing.ClassVar[dict] = {
        "tag": "2026.q1.4-lts",
        "db_type": "postgresql",
        "restored_from_package": "true",
        "package_includes_database": "false",
    }

    def test_a_stateless_package_is_not_seeded(self):
        fetch_seed = self._quickstart({**self.IMPORTED, PACKAGE_KIND_KEY: STATELESS})
        fetch_seed.assert_not_called()

    def test_a_stateful_package_without_a_database_is_still_seeded(self):
        self._quickstart(self.IMPORTED).assert_called_once()


if __name__ == "__main__":
    unittest.main()
