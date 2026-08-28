"""A snapshot that skipped its payload must not present as a snapshot (LDM-#1429).

On a full Docker disk `ldm snapshot` printed five warnings naming the wrong
cause -- "Skipping osgi due to permission error: [Errno 28] No space left on
device" -- and carried on. `PermissionError` is a subclass of `OSError`, so the
combined handler asserted "permission error" for every OS-level failure, and
sent the user to chmod when the fix was disk space.

Worse, it warned and continued. Every payload directory was skipped while
"Database dump completed." had already been reported as success. Had the final
write survived, the user would hold a .tar.gz they believe is a backup and which
cannot restore anything -- a failure that only surfaces at restore time, which
is the worst moment to discover it.

Observed against the unfixed code before these were written: all four fail.
"""

import errno
import tarfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.snapshot.archive import ArchiveSnapshotService


def _service(tmp):
    facade = MagicMock()
    facade.manager.args = MagicMock()
    svc = ArchiveSnapshotService(facade)
    svc.manager.verify_runtime_environment = MagicMock()
    return svc


class TestSnapshotArchiveDiskFailures(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.snap_dir = self.root / "snap"
        # One payload directory with content in it, so tar.add has something
        # real to attempt.
        (self.root / "osgi").mkdir(parents=True)
        (self.root / "osgi" / "a.jar").write_text("x")
        self.paths = {"root": self.root, "state": None}
        self.svc = _service(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_with_add_error(self, exc):
        """Drive _create_archive with tar.add raising `exc` for payload dirs."""
        real_add = tarfile.TarFile.add

        def fake_add(self_tar, name, arcname=None, **kw):
            if arcname == "osgi":
                raise exc
            return real_add(self_tar, name, arcname=arcname, **kw)

        with (
            patch.object(tarfile.TarFile, "add", fake_add),
            patch("ldm_core.utils.reclaim_volume_permissions", return_value=True),
        ):
            return self.svc._create_archive(self.paths, self.snap_dir, None)

    def test_enospc_is_not_reported_as_a_permission_error(self):
        """The cause must be named correctly -- chmod does not fix a full disk."""
        enospc = OSError(errno.ENOSPC, "No space left on device")
        with patch("ldm_core.snapshot.archive.UI") as ui:
            ui.die.side_effect = SystemExit(3)
            with self.assertRaises(SystemExit):
                self._run_with_add_error(enospc)

        warned = " ".join(str(c) for c in ui.warning.call_args_list)
        self.assertNotIn(
            "permission error",
            warned,
            "ENOSPC was reported as a permission error, which sends the user to "
            "chmod when the fix is disk space (LDM-#1429).",
        )
        died = " ".join(str(c) for c in ui.die.call_args_list).lower()
        self.assertIn("space", died, "the fatal message must name disk space")

    def test_enospc_exits_with_the_infrastructure_data_code(self):
        """Exit 3 per the contract: Infrastructure/Data Error, not generic 1."""
        enospc = OSError(errno.ENOSPC, "No space left on device")
        with patch("ldm_core.snapshot.archive.UI") as ui:
            ui.die.side_effect = SystemExit(3)
            with self.assertRaises(SystemExit):
                self._run_with_add_error(enospc)
        self.assertEqual(
            3,
            ui.die.call_args.kwargs.get("exit_code"),
            "a full disk during a backup is an Infrastructure/Data error (3).",
        )

    def test_a_skipped_payload_directory_is_fatal(self):
        """A snapshot missing its payload cannot restore -- it must not succeed."""
        with patch("ldm_core.snapshot.archive.UI") as ui:
            ui.die.side_effect = SystemExit(3)
            with self.assertRaises(SystemExit):
                self._run_with_add_error(PermissionError("denied"))
        self.assertTrue(
            ui.die.called,
            "content was skipped and _create_archive still returned normally; "
            "the caller would report a successful snapshot (LDM-#1429).",
        )

    def test_the_incomplete_archive_is_removed(self):
        """A truncated .tar.gz left on disk looks exactly like a usable one."""
        enospc = OSError(errno.ENOSPC, "No space left on device")
        with patch("ldm_core.snapshot.archive.UI") as ui:
            ui.die.side_effect = SystemExit(3)
            with self.assertRaises(SystemExit):
                self._run_with_add_error(enospc)
        self.assertFalse(
            (self.snap_dir / "files.tar.gz").exists(),
            "the partial archive survived, indistinguishable from a real one.",
        )


if __name__ == "__main__":
    unittest.main()
