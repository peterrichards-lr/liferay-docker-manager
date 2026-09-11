"""A failed import must leave an existing project as it was found (LDM-#1677).

`VolumeSyncStage` copies client extensions, fragments, modules and services
into the project directory. `ProjectSetupStage.rollback` deletes the project
directory on failure -- but only when `is_brand_new`. For an import into a
directory that already existed it correctly declines, so everything this stage
wrote used to remain after a later stage failed.

The fix snapshots the artifact directories before any write and restores them
wholesale. Wholesale rather than per-write because much of the copying happens
inside `workspace/hydration.py`, which this stage never sees -- a journal of
this stage's own writes would have covered about half of them while appearing
to succeed.

Every test runs inside a temporary CWD: `_snapshot_targets` puts its scratch
copy under `Path.cwd()/.ldm_temp`, exactly as `ExtractionStage` does, so a test
that skipped this would litter the repository.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ldm_core.pipelines.import_pipeline import VolumeSyncStage


class _Manager:
    """The only manager surface rollback touches."""

    @staticmethod
    def safe_rmtree(path):
        shutil.rmtree(path, ignore_errors=True)


class _Context:
    """Minimal stand-in for ImportPipelineContext."""

    def __init__(self, **data):
        self.data = dict(data)
        self.manager = _Manager()

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class VolumeSyncRollbackTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"

        # The scratch copy lands under Path.cwd(); keep it out of the repo.
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self.addCleanup(lambda: os.chdir(self._cwd))

        self.paths = {
            "root": self.root,
            "deploy": self.root / "deploy",
            "files": self.root / "files",
            "scripts": self.root / "scripts",
            "configs": self.root / "osgi" / "configs",
            "modules": self.root / "osgi" / "modules",
            "cx": self.root / "osgi" / "client-extensions",
            "ce_dir": self.root / "client-extensions",
        }
        self.stage = VolumeSyncStage()

    def _write(self, path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _context(self, **extra):
        return _Context(paths=self.paths, **extra)


class TestRestoreOnFailure(VolumeSyncRollbackTestCase):
    def test_a_file_the_import_overwrote_is_restored(self):
        """The case a delete-only rollback could never fix."""
        original = self.paths["deploy"] / "app.jar"
        self._write(original, "THE USER'S ORIGINAL")

        context = self._context()
        self.stage._snapshot_targets(context, self.paths)

        original.write_text("IMPORTED, OVERWRITING", encoding="utf-8")
        self.stage.rollback(context)

        self.assertEqual(original.read_text(encoding="utf-8"), "THE USER'S ORIGINAL")

    def test_a_file_the_import_added_is_removed(self):
        original = self.paths["deploy"] / "app.jar"
        self._write(original, "original")

        context = self._context()
        self.stage._snapshot_targets(context, self.paths)

        self._write(self.paths["deploy"] / "added-by-import.jar", "new")
        self.stage.rollback(context)

        self.assertFalse((self.paths["deploy"] / "added-by-import.jar").exists())
        self.assertTrue(original.exists(), "the pre-existing file was destroyed")

    def test_a_directory_that_did_not_exist_before_is_removed(self):
        self._write(self.paths["deploy"] / "app.jar", "original")
        self.assertFalse(self.paths["ce_dir"].exists())

        context = self._context()
        self.stage._snapshot_targets(context, self.paths)

        self._write(self.paths["ce_dir"] / "ext.zip", "imported")
        self.stage.rollback(context)

        self.assertFalse(
            self.paths["ce_dir"].exists(),
            "a directory the import brought into existence was left behind",
        )

    def test_services_are_covered(self):
        """`root/services` is written by the cloud branch and is not in `paths`."""
        services = self.root / "services"
        self._write(services / "svc" / "LCP.json", "original")

        context = self._context()
        self.stage._snapshot_targets(context, self.paths)

        self._write(services / "added" / "LCP.json", "imported")
        self.stage.rollback(context)

        self.assertFalse((services / "added").exists())
        self.assertTrue((services / "svc" / "LCP.json").exists())

    def test_nested_content_is_restored_not_just_the_top_level(self):
        deep = self.paths["configs"] / "a" / "b" / "conf.cfg"
        self._write(deep, "deep original")

        context = self._context()
        self.stage._snapshot_targets(context, self.paths)

        shutil.rmtree(self.paths["configs"])
        self.stage.rollback(context)

        self.assertEqual(deep.read_text(encoding="utf-8"), "deep original")


class TestWhenItDoesNothing(VolumeSyncRollbackTestCase):
    def test_a_brand_new_project_is_not_snapshotted(self):
        """ProjectSetupStage.rollback deletes the whole directory; this would be pure cost."""
        self._write(self.paths["deploy"] / "app.jar", "original")

        context = self._context(is_brand_new=True)
        self.stage._snapshot_targets(context, self.paths)

        self.assertIsNone(
            context.get("volume_sync_saved"),
            "a brand-new project paid for a snapshot it does not need",
        )

    def test_a_committed_import_is_never_undone(self):
        """Mirrors ProjectSetupStage: past the commit point nothing may undo it."""
        original = self.paths["deploy"] / "app.jar"
        self._write(original, "original")

        context = self._context()
        self.stage._snapshot_targets(context, self.paths)
        original.write_text("imported", encoding="utf-8")

        context.set("import_committed", True)
        self.stage.rollback(context)

        self.assertEqual(
            original.read_text(encoding="utf-8"),
            "imported",
            "rollback undid a committed import (LDM-#1630)",
        )

    def test_rollback_without_a_snapshot_is_a_no_op(self):
        """Rollback can be reached for a stage that returned early (an .ldmp import)."""
        original = self.paths["deploy"] / "app.jar"
        self._write(original, "original")

        self.stage.rollback(self._context())

        self.assertEqual(original.read_text(encoding="utf-8"), "original")


class TestTheSnapshotIsDiscardedOnSuccess(VolumeSyncRollbackTestCase):
    def test_the_scratch_copy_is_registered_for_cleanup(self):
        """FinalizationStage removes `temp_dirs`, so a success leaves nothing behind."""
        self._write(self.paths["deploy"] / "app.jar", "original")

        context = self._context()
        self.stage._snapshot_targets(context, self.paths)

        temp_dirs = context.get("temp_dirs", [])
        self.assertTrue(temp_dirs, "the scratch copy would outlive a successful import")
        self.assertTrue(any(d.exists() for d in temp_dirs))

    def test_the_document_library_is_not_copied(self):
        """`root/data` holds document-library content and must never be snapshotted."""
        self._write(self.root / "data" / "document_library" / "big.bin", "x" * 1000)

        context = self._context()
        self.stage._snapshot_targets(context, self.paths)

        saved = context.get("volume_sync_saved") or {}
        for backup in saved.values():
            if backup is None:
                continue
            self.assertFalse(
                (backup / "document_library").exists(),
                "root/data was snapshotted; every import would copy the document library",
            )


if __name__ == "__main__":
    unittest.main()
