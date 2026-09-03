"""A package cannot claim extensions it does not list (LDM-#1568).

`ldm snapshot` derived the two manifest fields from different directory sets:

    has_cx  <- cx/, deploy/, ce_dir/          # includes_client_extensions
    cx_list <- ce_dir/ only                    # client_extensions

so a project whose extensions live in cx/ or deploy/ produced a manifest
saying `includes_client_extensions: "true"` with `client_extensions: ""`.

Observed in the published AICA package: six archives under
osgi/client-extensions/, zero listed. Importing it produced a project that
looked installed and was not -- the site-level setup had nothing to act on.

This file covers the generator half only: it can no longer emit the
contradiction. The importer half -- recovering a listing from the payload, and
refusing only when the package ships nothing -- is in
test_package_listing_recovery.py (LDM-#1579).
"""

import tempfile
import unittest
from pathlib import Path

from ldm_core.snapshot.archive import _scan_client_extension_archives


def _paths(root, **dirs):
    made = {"root": root}
    for name, files in dirs.items():
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        made[name] = d
        for f in files:
            target = d / f
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"PK\x03\x04")
    return made


class TestTheTwoFieldsCannotDisagree(unittest.TestCase):
    def test_extensions_in_cx_are_listed(self):
        """The exact AICA shape: archives outside the build dir."""
        with tempfile.TemporaryDirectory() as d:
            paths = _paths(Path(d), cx=["a.zip", "b.zip"])
            found = _scan_client_extension_archives(paths)
            self.assertEqual(found, ["a.zip", "b.zip"])
            self.assertTrue(
                bool(found), "has_cx would have been true; the list must not be empty"
            )

    def test_extensions_in_deploy_are_listed(self):
        with tempfile.TemporaryDirectory() as d:
            paths = _paths(Path(d), deploy=["c.zip"])
            self.assertEqual(_scan_client_extension_archives(paths), ["c.zip"])

    def test_build_dir_dist_layout_is_listed(self):
        with tempfile.TemporaryDirectory() as d:
            paths = _paths(Path(d), ce_dir=["ext/dist/d.zip"])
            self.assertEqual(_scan_client_extension_archives(paths), ["d.zip"])

    def test_all_locations_combine(self):
        with tempfile.TemporaryDirectory() as d:
            paths = _paths(Path(d), cx=["a.zip"], deploy=["b.zip"], ce_dir=["c.zip"])
            self.assertEqual(
                _scan_client_extension_archives(paths), ["a.zip", "b.zip", "c.zip"]
            )

    def test_duplicates_collapse(self):
        """The same archive can be both built and staged for deploy."""
        with tempfile.TemporaryDirectory() as d:
            paths = _paths(Path(d), ce_dir=["same.zip"], deploy=["same.zip"])
            self.assertEqual(_scan_client_extension_archives(paths), ["same.zip"])

    def test_no_extensions_is_an_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            paths = _paths(Path(d), cx=[], deploy=[])
            found = _scan_client_extension_archives(paths)
            self.assertEqual(found, [])
            self.assertFalse(bool(found), "has_cx must be false when nothing is found")

    def test_order_is_stable(self):
        """A manifest must not churn between snapshots of an unchanged project."""
        with tempfile.TemporaryDirectory() as d:
            paths = _paths(Path(d), cx=["z.zip", "a.zip", "m.zip"])
            self.assertEqual(
                _scan_client_extension_archives(paths),
                _scan_client_extension_archives(paths),
            )
            self.assertEqual(
                _scan_client_extension_archives(paths), ["a.zip", "m.zip", "z.zip"]
            )

    def test_a_missing_directory_does_not_lose_the_others(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            paths = _paths(root, cx=["a.zip"])
            paths["ce_dir"] = root / "does-not-exist"
            self.assertEqual(_scan_client_extension_archives(paths), ["a.zip"])


if __name__ == "__main__":
    unittest.main()
