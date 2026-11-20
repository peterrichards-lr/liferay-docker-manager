"""`ldm package` leaves out what belongs to the publisher (LDM-#2443, LDM-#2446).

A `.ldmp` is the snapshot directory tarred up, and the snapshot's
files.tar.gz carried `deploy/`, the whole `osgi/` tree, `data/` and `routes/`.
So every package of a licensed project shipped its DXP and Marketplace keys,
and every package with a client extension shipped the OAuth client credentials
and DXP host its Liferay wrote into `routes/`.

Only the package is filtered. A local snapshot stays complete, so a licensed
project restored on the machine that made it comes back licensed.

Everything here runs on real tar files: the real `cmd_package` packs a real
snapshot, and the assertions read the package that comes out.
"""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.snapshot.archive import find_publisher_material
from ldm_core.tests.test_snapshot import MockSnapshotManager
from ldm_core.utils import calculate_sha256, safe_extract
from ldm_core.workspace.importer import warn_publisher_material

LICENCE = b"<?xml version='1.0'?><license><owner>Publisher Inc</owner></license>"

PROJECT = {
    "files/portal-ext.properties": b"company.default.web.id=demo\n",
    "deploy/activation-key-dxp.xml": LICENCE,
    "deploy/not-a-licence.xml": b"<beans/>",
    "deploy/digital-sales-room.lpkg": b"lpkg",
    "deploy/my-cx.zip": b"zip",
    "osgi/modules/activation-key-app.xml": LICENCE,
    "osgi/modules/my-module.jar": b"jar",
    "osgi/marketplace/Liferay Commerce.lpkg": b"lpkg",
    "data/license/registered.li": b"registered",
    "data/document_library/0/doc.bin": b"doc",
    "routes/default/my-cx/my-cx.client-extension-config.properties": b"secret",
    "routes/default/dxp/com.liferay.lxc.dxp.domains": b"publisher.example",
    # files/ is copied over /opt/liferay at container start, so each of these
    # delivers its material exactly as its top-level counterpart does.
    "files/deploy/my-dxp-key.xml": LICENCE,
    "files/deploy/not-a-licence.xml": b"<beans/>",
    "files/deploy/Liferay Search.lpkg": b"lpkg",
    "files/osgi/modules/app-key.xml": LICENCE,
    "files/osgi/marketplace/Digital Sales Room.lpkg": b"lpkg",
    "files/data/license/registered.li": b"registered",
    "scripts/setup.sh": b"echo hello\n",
    "scripts/install-key.sh": b"cp /keys/license.xml /opt/liferay/deploy/\n",
}

PUBLISHER_ONLY = {
    "deploy/activation-key-dxp.xml",
    "deploy/digital-sales-room.lpkg",
    "osgi/modules/activation-key-app.xml",
    "osgi/marketplace/Liferay Commerce.lpkg",
    "data/license/registered.li",
    "routes/default/my-cx/my-cx.client-extension-config.properties",
    "routes/default/dxp/com.liferay.lxc.dxp.domains",
}

PUBLISHER_ONLY_UNDER_FILES = {
    "files/deploy/my-dxp-key.xml",
    "files/deploy/Liferay Search.lpkg",
    "files/osgi/modules/app-key.xml",
    "files/osgi/marketplace/Digital Sales Room.lpkg",
    "files/data/license/registered.li",
}
PUBLISHER_ONLY |= PUBLISHER_ONLY_UNDER_FILES


def _write_payload(path, files):
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _names(tar_path):
    with tarfile.open(tar_path, "r:gz") as tar:
        return {m.name for m in tar.getmembers() if m.isfile()}


class _Harness(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "demo"
        self.snap = self.root / "snapshots" / "20261119_100000"
        self.snap.mkdir(parents=True)
        self.out = Path(tmp.name) / "out"
        self.payload = self.snap / "files.tar.gz"
        _write_payload(self.payload, PROJECT)
        (self.snap / "files.tar.gz.sha256").write_text(calculate_sha256(self.payload))
        (self.snap / "meta").write_text('{"tag": "2026.q1.7-lts"}')
        self.snapshot_sha = calculate_sha256(self.payload)
        self.manager = MockSnapshotManager()

    def package(self, **kwargs):
        """Runs the real cmd_package on the snapshot. Returns the extracted
        package directory and what was printed with UI.info."""
        with (
            patch(
                "ldm_core.handlers.base.BaseHandler.detect_project_path",
                return_value=self.root,
            ),
            patch(
                "ldm_core.handlers.base.BaseHandler.setup_paths",
                return_value={"root": self.root, "backups": self.root / "snapshots"},
            ),
            patch(
                "ldm_core.snapshot.utils.UtilsSnapshotService._list_backups",
                return_value=[{"path": self.snap}],
            ),
            patch("ldm_core.ui.UI.info") as info,
            patch("ldm_core.ui.UI.success"),
        ):
            self.manager.snapshot.cmd_package(
                project_id="demo",
                output_dir=str(self.out),
                repo="owner/repo",
                use_latest=True,
                **kwargs,
            )
        unpacked = self.out / "unpacked"
        with tarfile.open(self.out / "demo.ldmp", "r:gz") as tar:
            safe_extract(tar, unpacked)
        printed = "\n".join(str(c.args[0]) for c in info.call_args_list)
        return unpacked, printed


class ThePackageLeavesItOut(_Harness):
    def test_no_licence_routes_or_marketplace_product_is_shipped(self):
        unpacked, _ = self.package()
        shipped = _names(unpacked / "files.tar.gz")
        self.assertFalse(shipped & PUBLISHER_ONLY, "publisher material shipped")

    def test_everything_else_is_still_shipped(self):
        """A filter that drops the project is worse than the leak."""
        unpacked, _ = self.package()
        self.assertEqual(
            _names(unpacked / "files.tar.gz"), set(PROJECT) - PUBLISHER_ONLY
        )

    def test_the_package_checksum_matches_its_own_payload(self):
        """Restore verifies files.tar.gz against this; the snapshot's checksum
        is for the unfiltered payload and would fail every import."""
        unpacked, _ = self.package()
        self.assertEqual(
            (unpacked / "files.tar.gz.sha256").read_text().strip(),
            calculate_sha256(unpacked / "files.tar.gz"),
        )

    def test_it_says_what_it_left_out_by_name(self):
        _, printed = self.package()
        self.assertIn("activation-key-dxp.xml", printed)
        self.assertIn("activation-key-app.xml", printed)
        self.assertIn("digital-sales-room.lpkg", printed)
        self.assertIn("routes/ (2 files)", printed)

    def test_include_marketplace_ships_the_products_only(self):
        unpacked, _ = self.package(include_marketplace=True)
        shipped = _names(unpacked / "files.tar.gz")
        self.assertIn("deploy/digital-sales-room.lpkg", shipped)
        self.assertIn("osgi/marketplace/Liferay Commerce.lpkg", shipped)
        self.assertNotIn("deploy/activation-key-dxp.xml", shipped)
        self.assertNotIn("data/license/registered.li", shipped)


class TheFilesTreeIsFilteredLikeTheTopLevel(_Harness):
    """LDM-#2443: `files/` is copied over `/opt/liferay` at container start,
    so `files/deploy/`, `files/osgi/...` and `files/data/license/` deliver
    licences and Marketplace products just as the top-level trees do."""

    def test_no_licence_or_marketplace_product_under_files_is_shipped(self):
        unpacked, _ = self.package()
        shipped = _names(unpacked / "files.tar.gz")
        self.assertFalse(shipped & PUBLISHER_ONLY_UNDER_FILES)
        self.assertIn("files/deploy/not-a-licence.xml", shipped)
        self.assertIn("files/portal-ext.properties", shipped)

    def test_it_names_what_it_left_out_of_files(self):
        _, printed = self.package()
        self.assertIn("files/deploy/my-dxp-key.xml", printed)
        self.assertIn("files/osgi/modules/app-key.xml", printed)
        self.assertIn("files/data/license/registered.li", printed)
        self.assertIn("files/osgi/marketplace/Digital Sales Room.lpkg", printed)

    def test_include_marketplace_ships_the_products_under_files(self):
        unpacked, _ = self.package(include_marketplace=True)
        shipped = _names(unpacked / "files.tar.gz")
        self.assertIn("files/deploy/Liferay Search.lpkg", shipped)
        self.assertIn("files/osgi/marketplace/Digital Sales Room.lpkg", shipped)
        self.assertNotIn("files/deploy/my-dxp-key.xml", shipped)
        self.assertNotIn("files/data/license/registered.li", shipped)

    def test_import_warns_about_an_older_package_with_files_material(self):
        found = dict(find_publisher_material(self.payload))
        self.assertEqual(found["files/deploy/my-dxp-key.xml"], "licence")
        self.assertEqual(found["files/deploy/Liferay Search.lpkg"], "marketplace")
        self.assertNotIn("files/deploy/not-a-licence.xml", found)


class ShippedScriptsAreListed(_Harness):
    """LDM-#2443: `scripts/` ships, mounted at /mnt/liferay/scripts in the
    importer's container. It is listed, and a script mentioning a licence or
    routes is flagged, but nothing is left out."""

    def package_with_warnings(self):
        with patch("ldm_core.ui.UI.warning") as warned:
            unpacked, printed = self.package()
        said = "\n".join(str(c.args[0]) for c in warned.call_args_list)
        return unpacked, printed, said

    def test_every_script_is_listed_and_still_shipped(self):
        unpacked, printed, _ = self.package_with_warnings()
        self.assertIn("scripts/setup.sh", printed)
        self.assertIn("scripts/install-key.sh", printed)
        shipped = _names(unpacked / "files.tar.gz")
        self.assertIn("scripts/install-key.sh", shipped)

    def test_a_script_mentioning_a_licence_is_flagged(self):
        _, _, said = self.package_with_warnings()
        self.assertIn("scripts/install-key.sh", said)
        self.assertNotIn("scripts/setup.sh", said)


class TheLocalSnapshotIsUntouched(_Harness):
    def test_the_snapshot_keeps_everything(self):
        """A licensed project must restore licensed on its own machine."""
        self.package()
        self.assertEqual(calculate_sha256(self.payload), self.snapshot_sha)
        self.assertEqual(_names(self.payload), set(PROJECT))


class AnOlderPackageIsReported(_Harness):
    def test_import_warns_naming_the_material(self):
        with patch("ldm_core.utils.UI.warning") as warned:
            warn_publisher_material(self.snap)
        said = "\n".join(str(c.args[0]) for c in warned.call_args_list)
        self.assertIn("activation-key-dxp.xml", said)
        self.assertIn("routes/", said)
        self.assertIn("deploy/", said)

    def test_a_clean_package_imports_quietly(self):
        unpacked, _ = self.package()
        with patch("ldm_core.utils.UI.warning") as warned:
            warn_publisher_material(unpacked)
        warned.assert_not_called()

    def test_only_licence_xml_counts_as_a_licence(self):
        found = dict(find_publisher_material(self.payload))
        self.assertNotIn("deploy/not-a-licence.xml", found)
        self.assertEqual(found["deploy/activation-key-dxp.xml"], "licence")


if __name__ == "__main__":
    unittest.main()
