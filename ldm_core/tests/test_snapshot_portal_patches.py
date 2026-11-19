"""A snapshot and a package carry the project's portal patches (LDM-#2444).

`portal-patches/` (LDM-#1264) holds patched core JARs that `ldm run` copies
over the stock `osgi/portal` ones on every boot. It was not archived, so an
`.ldmp` of a patched project imported cleanly and then booted the stock JARs
against a database built with the patched ones -- silently, and with exactly
the behaviour the patches existed for missing.

These drive the real archive writer, the real package filter and the real
restore extraction, and the import-time refusal through the stages that call
it. Observed against the unfixed code before they were written: each fails.
"""

import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.runtime import portal_patches
from ldm_core.snapshot.archive import (
    ArchiveSnapshotService,
    describe_shipped_portal_patches,
    write_redistributable_payload,
)

PATCH_JAR = "com.liferay.site.initializer.impl.jar"
OTHER_JAR = "com.liferay.object.rest.impl.jar"
BUILT_ON = "2026.q1.12-lts"
OTHER_QUARTER = "2026.q2.0"


_parse_version = BaseHandler.__new__(BaseHandler).parse_version


class _Die(SystemExit):
    """UI.die's stand-in, carrying the message it was given."""

    def __init__(self, message, **kwargs):
        super().__init__(kwargs.get("exit_code", 1))
        self.message = message


def _die(message, **kwargs):
    raise _Die(message, **kwargs)


class _IsolatedHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        env = patch.dict(
            os.environ, {"LDM_HOME": str(self.tmp / "ldm-home"), "LDM_DRY_RUN": ""}
        )
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)


def _write_patch(root, name, introduced_in, max_version=None):
    directory = root / "portal-patches"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(b"PK\x03\x04patched")
    sidecar = {
        "jira": "LPD-1",
        "introduced_in": introduced_in,
        "max_version": max_version,
        "fail_on_mismatch": False,
    }
    (directory / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")


class TestArchiveRoundTrip(_IsolatedHome):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "publisher"
        self.root.mkdir()
        _write_patch(self.root, PATCH_JAR, BUILT_ON)
        _write_patch(self.root, OTHER_JAR, BUILT_ON, max_version="2026.q1.99")
        self.paths = {"root": self.root, "state": None}

        facade = MagicMock()
        facade.manager.args = MagicMock(force=False, no_osgi_seed=False)
        facade.manager.run_command.return_value = ""
        facade.manager.read_meta.return_value = {"tag": BUILT_ON}
        self.svc = ArchiveSnapshotService(facade)

    def _archive(self):
        with patch("ldm_core.utils.reclaim_volume_permissions"):
            return self.svc._create_archive(self.paths, self.tmp / "snap", None)

    def test_snapshot_archives_jars_and_sidecars(self):
        with tarfile.open(self._archive()) as tar:
            names = set(tar.getnames())

        for jar in (PATCH_JAR, OTHER_JAR):
            self.assertIn(f"portal-patches/{jar}", names)
            self.assertIn(f"portal-patches/{jar}.json", names)

    def test_package_payload_keeps_them_and_restore_puts_them_back(self):
        """Through the #2443 publisher filter, then the real restore."""
        payload = self.tmp / "package-files.tar.gz"
        omitted = write_redistributable_payload(self._archive(), payload)
        self.assertEqual(omitted, [])

        consumer = self.tmp / "consumer"
        consumer.mkdir()
        self.svc._extract_snapshot_archive(payload, {"root": consumer})

        restored = consumer / "portal-patches"
        self.assertEqual((restored / PATCH_JAR).read_bytes(), b"PK\x03\x04patched")
        sidecar = json.loads((restored / f"{OTHER_JAR}.json").read_text())
        self.assertEqual(sidecar["introduced_in"], BUILT_ON)
        self.assertEqual(sidecar["max_version"], "2026.q1.99")

    def test_meta_records_the_patch_set(self):
        self.svc._generate_snapshot_metadata(
            "name", "ts", {}, self.root, self.paths, self.tmp / "snap", None, None
        )
        meta = self.svc.manager.write_meta.call_args.args[1]

        self.assertEqual(
            meta["portal_patches"],
            [
                {
                    "name": OTHER_JAR,
                    "introduced_in": BUILT_ON,
                    "max_version": "2026.q1.99",
                },
                {"name": PATCH_JAR, "introduced_in": BUILT_ON, "max_version": None},
            ],
        )

    def test_package_lists_what_the_payload_ships(self):
        recorded = portal_patches.package_patch_set(self.root)
        lines = describe_shipped_portal_patches(self._archive(), recorded)

        self.assertEqual(
            lines,
            [
                f"{OTHER_JAR}: introduced in {BUILT_ON}, max_version 2026.q1.99",
                f"{PATCH_JAR}: introduced in {BUILT_ON}",
            ],
        )

    def test_an_unreadable_sidecar_is_recorded_as_unknown_origin(self):
        (self.root / "portal-patches" / f"{PATCH_JAR}.json").write_text("{not json")
        entries = {e["name"]: e for e in portal_patches.package_patch_set(self.root)}

        self.assertTrue(entries[PATCH_JAR].get("unreadable"))
        self.assertNotIn("unreadable", entries[OTHER_JAR])


class TestImportRefusesAQuarterMismatch(_IsolatedHome):
    """The same check, and the same words, as `ldm run`."""

    def _manager(self, **args):
        manager = MagicMock()
        manager.parse_version = _parse_version
        manager.defaults = {}
        manager.args = MagicMock(
            tag=None,
            tag_latest=False,
            tag_prefix=None,
            nightly=False,
            master=False,
            force_portal_patches=False,
        )
        for key, value in args.items():
            setattr(manager.args, key, value)
        return manager

    def _run_message(self, tag):
        """What `ldm run` says booting a project carrying these patches on `tag`."""
        root = self.tmp / "run-project"
        _write_patch(root, PATCH_JAR, BUILT_ON)
        with (
            patch.object(portal_patches.UI, "die", side_effect=_die),
            self.assertRaises(_Die) as raised,
        ):
            portal_patches.plan_patches(self._manager(), root, tag)
        return raised.exception.message

    def _check(self, manifest, **args):
        from ldm_core.workspace.importer import check_package_portal_patches

        with patch.object(portal_patches.UI, "die", side_effect=_die):
            check_package_portal_patches(self._manager(**args), manifest)

    def _manifest(self, tag):
        return {
            "tag": tag,
            "portal_patches": [
                {"name": PATCH_JAR, "introduced_in": BUILT_ON, "max_version": None}
            ],
        }

    def test_a_different_quarter_is_refused_with_the_run_message(self):
        with self.assertRaises(_Die) as raised:
            self._check(self._manifest(OTHER_QUARTER))

        self.assertEqual(raised.exception.message, self._run_message(OTHER_QUARTER))
        self.assertEqual(raised.exception.code, 1)

    def test_an_explicit_tag_is_what_is_checked(self):
        with self.assertRaises(_Die):
            self._check(self._manifest(BUILT_ON), tag=f"dxp-{OTHER_QUARTER}")

    def test_the_same_release_imports(self):
        self._check(self._manifest(BUILT_ON))

    def test_force_portal_patches_imports_anyway(self):
        self._check(self._manifest(OTHER_QUARTER), force_portal_patches=True)

    def test_a_discovered_tag_is_left_to_run(self):
        self._check(self._manifest(OTHER_QUARTER), tag_latest=True)

    def test_a_package_from_before_2444_is_not_checked(self):
        self._check({"tag": OTHER_QUARTER})


class TestPackageVerificationStageRefusesBeforeSetup(_IsolatedHome):
    """`ldm import ./x.ldmp` stops before the project or any container exists."""

    def test_stage_refuses_and_runs_before_project_setup(self):
        from ldm_core.pipelines.import_pipeline import (
            DatabaseRestoreStage,
            ImportPipeline,
            ImportPipelineContext,
            PackageVerificationStage,
            ProjectSetupStage,
        )
        from ldm_core.utils import read_meta

        order = [type(s) for s in ImportPipeline().stages]
        self.assertLess(
            order.index(PackageVerificationStage), order.index(ProjectSetupStage)
        )
        self.assertLess(
            order.index(PackageVerificationStage), order.index(DatabaseRestoreStage)
        )

        backup = self.tmp / "extract"
        backup.mkdir()
        manifest = {
            "tag": OTHER_QUARTER,
            "db_type": "postgresql",
            "github_repository": "acme/widget",
            "portal_patches": [
                {"name": PATCH_JAR, "introduced_in": BUILT_ON, "max_version": None}
            ],
        }
        (backup / "meta").write_text(json.dumps(manifest), encoding="utf-8")

        manager = MagicMock()
        manager.parse_version = _parse_version
        manager.defaults = {}
        manager.args = MagicMock(tag=None, force_portal_patches=False)
        manager.read_meta.side_effect = lambda p, strict=False: read_meta(
            Path(p) / "meta", strict=strict
        )
        context = ImportPipelineContext(manager, source_path=str(backup))
        context.set("backup_dir", backup)

        with (
            patch.object(portal_patches.UI, "die", side_effect=_die),
            self.assertRaises(_Die) as raised,
        ):
            PackageVerificationStage().execute(context)

        self.assertIn("different Liferay release line", raised.exception.message)


class TestReleaseAssetImportRefusesBeforeHydration(unittest.TestCase):
    """The GitHub-release `.ldmp` path: refused before the project is written."""

    def _import(self, manifest_extra):
        from ldm_core.tests import test_package_listing_recovery as harness

        class _Manager(harness._ImportManager):
            parse_version = staticmethod(_parse_version)

            def __init__(self, project_path):
                super().__init__(project_path)
                self.defaults = {}
                for flag in ("tag_latest", "nightly", "master"):
                    setattr(self.args, flag, False)
                self.args.tag_prefix = None
                self.args.force_portal_patches = False

        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(harness, "_ImportManager", _Manager),
            patch.object(portal_patches.UI, "die", side_effect=_die),
        ):
            outcome = harness._import_package(
                Path(d), manifest_extra, [f"portal-patches/{PATCH_JAR}"]
            )
            return outcome, outcome.project_meta

    def test_a_different_quarter_never_reaches_restore(self):
        outcome, project_meta = self._import(
            {
                "tag": OTHER_QUARTER,
                "db_type": "postgresql",
                "portal_patches": [
                    {"name": PATCH_JAR, "introduced_in": BUILT_ON, "max_version": None}
                ],
            }
        )

        self.assertTrue(outcome.aborted)
        self.assertIsNone(project_meta)
        self.assertEqual(outcome.restore_calls, [])

    def test_the_same_release_is_restored(self):
        outcome, project_meta = self._import(
            {
                "tag": BUILT_ON,
                "db_type": "postgresql",
                "portal_patches": [
                    {"name": PATCH_JAR, "introduced_in": BUILT_ON, "max_version": None}
                ],
            }
        )

        self.assertFalse(outcome.aborted)
        self.assertEqual(project_meta["tag"], BUILT_ON)
        self.assertEqual(len(outcome.restore_calls), 1)


if __name__ == "__main__":
    unittest.main()
