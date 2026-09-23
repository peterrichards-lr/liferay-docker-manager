"""`osgi/marketplace` must be permission-reclaimed before it is archived (LDM-#1941).

LDM-#1918 (`ac1210db`) restored the `osgi/marketplace` bind mount, which had
been dropped in the `stack.py` -> `composer.py` refactor. The mount was added
to `handlers/composer.py` but not to the reclaim list in
`snapshot/archive.py`, and the two had never needed to agree before: until it
was mounted, nothing containerised wrote to `marketplace`, so there was never
anything the host user could not read back.

Once mounted, Liferay creates `osgi/marketplace/override` as uid 1000. The
archive adds the whole `osgi` tree as a single entry, so that one unreadable
subdirectory failed the entry outright and LDM-#1429's incomplete-archive guard
correctly refused the snapshot -- leaving native Linux users unable to back up
any project that had booted Liferay:

    Skipping osgi due to permission error: [Errno 13] Permission denied:
        '.../osgi/marketplace/override'
    Snapshot failed: content could not be added to the archive.

macOS and Windows are unaffected: Docker Desktop's virtiofs/gRPC-FUSE bind
mounts present files as the host user regardless of mode. That is the same
blind spot that let LDM-#599 ship, and it is why this reached a tag with four
of five distros green.

Observed against the unfixed code before these were written: all three fail.
The ordering test fails there too -- it asserts the reclaim happened at all
before asserting when, so it does not pass vacuously on an empty list.
"""

import tarfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.snapshot.archive import ArchiveSnapshotService


class TestMarketplaceIsReclaimedBeforeArchiving(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.snap_dir = self.root / "snap"

        # The real tree shape: `marketplace` lives inside `osgi`, and `osgi` is
        # what the archive adds as one entry.
        (self.root / "osgi" / "marketplace").mkdir(parents=True)
        (self.root / "osgi" / "marketplace" / "a.lpkg").write_text("x")
        (self.root / "deploy").mkdir()

        self.paths = {
            "root": self.root,
            "state": None,
            "deploy": self.root / "deploy",
            "marketplace": self.root / "osgi" / "marketplace",
        }

        facade = MagicMock()
        facade.manager.args = MagicMock()
        self.svc = ArchiveSnapshotService(facade)
        self.svc.manager.verify_runtime_environment = MagicMock()

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self):
        """Drive _create_archive, recording every reclaim call and tar entry."""
        reclaimed = []
        order = []

        def fake_reclaim(path, uid=None, gid=None, chmod_val="750", **kw):
            reclaimed.append((Path(path), uid, chmod_val))
            order.append(("reclaim", Path(path)))
            return True

        real_add = tarfile.TarFile.add

        def fake_add(self_tar, name, arcname=None, recursive=True, **kw):
            # tarfile recurses through this same method, passing `recursive`
            # positionally -- the signature has to accept it or the nested
            # call raises TypeError mid-archive.
            order.append(("add", arcname))
            return real_add(self_tar, name, arcname=arcname, recursive=recursive, **kw)

        with (
            patch("ldm_core.utils.reclaim_volume_permissions", fake_reclaim),
            patch.object(tarfile.TarFile, "add", fake_add),
        ):
            self.svc._create_archive(self.paths, self.snap_dir, None)

        return reclaimed, order

    def test_marketplace_is_reclaimed(self):
        """The regression guard: an unreclaimed marketplace fails the osgi entry."""
        reclaimed, _ = self._run()
        paths = [p for p, _uid, _mode in reclaimed]

        self.assertIn(
            self.paths["marketplace"],
            paths,
            "osgi/marketplace is bind-mounted (LDM-#1918) but never "
            "permission-reclaimed, so Liferay's root-owned override/ "
            "subdirectory fails the whole osgi archive entry and "
            "'ldm snapshot' produces no backup on native Linux (LDM-#1941). "
            f"Reclaimed: {paths}",
        )

    def test_marketplace_gets_the_container_uid_treatment(self):
        """uid 1000 / 777, not the host-uid 755 given to data and state.

        Liferay's Tomcat/Equinox runs as uid 1000 and must still be able to
        write the directory after the snapshot; the host user has to read every
        file back to add it to the tarball. `777` is what admits both. Narrowing
        it reproduces LDM-#599, which broke native Linux for a release.
        """
        reclaimed, _ = self._run()
        entry = [
            (uid, mode) for p, uid, mode in reclaimed if p == self.paths["marketplace"]
        ]
        self.assertTrue(entry, "marketplace was not reclaimed at all")

        uid, mode = entry[0]
        self.assertEqual(
            uid, "1000", "marketplace is written by the uid-1000 Liferay process"
        )
        self.assertEqual(
            mode,
            "777",
            "anything narrower locks out either the container or the host "
            "user reading it back into the tarball (LDM-#599 / LDM-#645)",
        )

    def test_reclaim_happens_before_the_osgi_entry_is_added(self):
        """Reclaiming after the tarball is written would fix nothing."""
        _, order = self._run()

        reclaim_idx = [
            i
            for i, (kind, val) in enumerate(order)
            if kind == "reclaim" and val == self.paths["marketplace"]
        ]
        add_idx = [
            i for i, (kind, val) in enumerate(order) if kind == "add" and val == "osgi"
        ]

        self.assertTrue(reclaim_idx, "marketplace was never reclaimed")
        self.assertTrue(add_idx, "the osgi tree was never added to the archive")
        self.assertLess(
            reclaim_idx[0],
            add_idx[0],
            "marketplace must be reclaimed before osgi is read into the "
            "tarball; reclaiming afterwards leaves the archive incomplete.",
        )


if __name__ == "__main__":
    unittest.main()
