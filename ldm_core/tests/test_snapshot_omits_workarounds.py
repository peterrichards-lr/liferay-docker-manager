"""A snapshot must not carry LDM's own portal-workaround bundle (LDM-#2359).

`sync_workarounds` places the bundle in the project's `osgi/modules` on every
`ldm run`. The archive adds the whole `osgi` tree as one entry, and
`ldm package` tars the snapshot directory, so the bundle shipped inside every
`.ldmp` -- pinned to the build's Liferay line, and refused by AICA's release
gate as "shipped but not built". The importing `ldm run` installs the right
variant for its own tag, so the snapshot has no reason to hold one.

Observed against the unfixed code before these were written: both fail.
"""

import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.runtime.portal_workarounds import (
    RETIRED_PREFIXES,
    WORKAROUNDS,
    is_ldm_bundle,
)
from ldm_core.snapshot.archive import ArchiveSnapshotService

USER_JAR = "com.example.user.module-1.0.0.jar"


class TestSnapshotOmitsWorkaroundBundles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.snap_dir = self.root / "snap"

        self.modules = modules = self.root / "osgi" / "modules"
        modules.mkdir(parents=True)
        # Every jar name LDM can leave there: each variant, and a copy an
        # older LDM may have left under a versioned name.
        self.ldm_jars = sorted(
            {v.jar for w in WORKAROUNDS for v in w.variants}
            | {f"{w.prefix}-1.0.0.jar" for w in WORKAROUNDS}
            | {f"{p}.jar" for p in RETIRED_PREFIXES}
        )
        for name in [*self.ldm_jars, USER_JAR]:
            (modules / name).write_bytes(b"PK")

        self.paths = {
            "root": self.root,
            "state": None,
            "deploy": self.root / "deploy",
            "marketplace": self.root / "osgi" / "marketplace",
            "files": self.root / "files",
            "logs": self.root / "logs",
            "configs": self.root / "osgi" / "configs",
            "modules": modules,
            "data": self.root / "data",
        }

        facade = MagicMock()
        facade.manager.args = MagicMock()
        facade.manager.run_command.return_value = ""
        self.svc = ArchiveSnapshotService(facade)

    def tearDown(self):
        self._tmp.cleanup()

    def test_fixture_holds_the_bundle(self):
        """Guards the fixture: without an LDM jar the other tests pass vacuously."""
        self.assertTrue(self.ldm_jars)
        self.assertTrue(all(is_ldm_bundle(n) for n in self.ldm_jars))
        self.assertFalse(is_ldm_bundle(USER_JAR))

    def test_archive_omits_workaround_jar_and_keeps_user_jar(self):
        with patch("ldm_core.utils.reclaim_volume_permissions"):
            files_tar = self.svc._create_archive(self.paths, self.snap_dir, None)

        with tarfile.open(files_tar) as tar:
            names = set(tar.getnames())

        self.assertIn(f"osgi/modules/{USER_JAR}", names)
        for jar in self.ldm_jars:
            self.assertNotIn(f"osgi/modules/{jar}", names)

    def test_manifest_does_not_list_workaround_jar(self):
        self.svc._generate_snapshot_metadata(
            "name", "ts", {}, self.root, self.paths, self.snap_dir, None, None
        )
        meta = self.svc.manager.write_meta.call_args.args[1]

        self.assertEqual(meta["osgi_modules"], USER_JAR)
        self.assertEqual(meta["includes_osgi_modules"], "true")

    def test_manifest_with_only_the_bundle_claims_no_modules(self):
        (self.modules / USER_JAR).unlink()
        self.svc._generate_snapshot_metadata(
            "name", "ts", {}, self.root, self.paths, self.snap_dir, None, None
        )
        meta = self.svc.manager.write_meta.call_args.args[1]

        self.assertEqual(meta["osgi_modules"], "")
        self.assertEqual(meta["includes_osgi_modules"], "false")


if __name__ == "__main__":
    unittest.main()
