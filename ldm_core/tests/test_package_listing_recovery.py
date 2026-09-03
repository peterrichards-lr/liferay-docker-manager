"""A manifest that under-reports is corrected from the payload (LDM-#1579).

LDM-#1568 made the importer refuse any package claiming a category and listing
nothing. The published AICA package is exactly that shape --
`includes_client_extensions: "true"` with `client_extensions: ""` and six
archives under osgi/client-extensions/ -- so that gate turned
`ldm quickstart aica` into a hard failure for every user, pending a re-publish
by someone else.

The archives are in the payload and discoverable, so the importer now recovers
the listing from the package's own contents and refuses only when the package
genuinely ships nothing.

The scan runs over member NAMES in files.tar.gz. At the point the manifest is
verified, only the outer package has been unpacked -- the project tree does not
exist on disk until cmd_restore() runs, so there is no directory to glob.
"""

import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from ldm_core.snapshot.archive import (
    CLIENT_EXTENSION_SOURCES,
    OSGI_MODULE_SOURCES,
    _scan_client_extension_archives,
    scan_member_names,
)
from ldm_core.workspace.importer import _verify_ldm_package_manifest

OWNER, REPO = "acme", "widget"


class _FakeManager:
    """Mirrors LiferayDockerManager.read_meta's directory handling."""

    def read_meta(self, path, strict=False):
        from ldm_core.utils import read_meta

        p = Path(path)
        return read_meta(p / "meta" if p.is_dir() else p, strict=strict)


class _FakeSelf:
    def __init__(self):
        self.manager = _FakeManager()


def _build_package(root, manifest_extra, payload_members):
    """A package dir as it looks after the OUTER archive is unpacked."""
    extract = root / "extract"
    extract.mkdir(parents=True, exist_ok=True)

    manifest = {"github_repository": f"{OWNER}/{REPO}"}
    manifest.update(manifest_extra)
    (extract / "meta").write_text(json.dumps(manifest), encoding="utf-8")

    staging = root / "staging"
    for member in payload_members:
        target = staging / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PK\x03\x04")
    staging.mkdir(parents=True, exist_ok=True)

    with tarfile.open(extract / "files.tar.gz", "w:gz") as tar:
        for member in payload_members:
            tar.add(staging / member, arcname=member)

    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    return extract, pkg


def _verify(extract, pkg):
    return _verify_ldm_package_manifest(_FakeSelf(), extract, pkg, OWNER, REPO)


class TestRecoveryFromThePayload(unittest.TestCase):
    def test_the_aica_shape_imports_and_is_corrected(self):
        """Six archives shipped, zero listed -- the published AICA package."""
        archives = [
            "ai-commerce-accelerator-batch.zip",
            "ai-commerce-accelerator-frontend.zip",
            "site-initializer.zip",
        ]
        with tempfile.TemporaryDirectory() as d:
            extract, pkg = _build_package(
                Path(d),
                {"includes_client_extensions": "true", "client_extensions": ""},
                [f"osgi/client-extensions/{a}" for a in archives],
            )
            manifest = _verify(extract, pkg)

        self.assertEqual(manifest["client_extensions"], ",".join(sorted(archives)))

    def test_osgi_modules_are_recovered_too(self):
        with tempfile.TemporaryDirectory() as d:
            extract, pkg = _build_package(
                Path(d),
                {"includes_osgi_modules": "true", "osgi_modules": ""},
                ["osgi/modules/com.acme.endpoint.jar", "deploy/legacy.war"],
            )
            manifest = _verify(extract, pkg)

        self.assertEqual(manifest["osgi_modules"], "com.acme.endpoint.jar,legacy.war")

    def test_the_package_is_not_discarded_when_recovery_succeeds(self):
        """Refusing deletes the scratch dirs; recovery must leave them intact."""
        with tempfile.TemporaryDirectory() as d:
            extract, pkg = _build_package(
                Path(d),
                {"includes_client_extensions": "true", "client_extensions": ""},
                ["osgi/client-extensions/a.zip"],
            )
            _verify(extract, pkg)
            self.assertTrue(extract.exists(), "payload was discarded")
            self.assertTrue(pkg.exists(), "package dir was discarded")

    def test_an_honest_manifest_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            extract, pkg = _build_package(
                Path(d),
                {
                    "includes_client_extensions": "true",
                    "client_extensions": "declared.zip",
                },
                ["osgi/client-extensions/something-else.zip"],
            )
            manifest = _verify(extract, pkg)

        self.assertEqual(manifest["client_extensions"], "declared.zip")


class TestTheRefusalStillFires(unittest.TestCase):
    def test_claims_extensions_but_ships_none(self):
        """Deliberately empty payload -- the scan must not rescue this."""
        with tempfile.TemporaryDirectory() as d:
            extract, pkg = _build_package(
                Path(d),
                {"includes_client_extensions": "true", "client_extensions": ""},
                ["data/dump.sql"],
            )
            with self.assertRaises(SystemExit):
                _verify(extract, pkg)

    def test_a_missing_payload_is_not_mistaken_for_an_empty_one(self):
        with tempfile.TemporaryDirectory() as d:
            extract, pkg = _build_package(
                Path(d),
                {"includes_client_extensions": "true", "client_extensions": ""},
                [],
            )
            (extract / "files.tar.gz").unlink()
            with self.assertRaises(SystemExit):
                _verify(extract, pkg)


class TestTheTwoScansAgree(unittest.TestCase):
    """The #1568 bug was two scans disagreeing. There are two again."""

    def test_disk_glob_and_member_scan_return_the_same_list(self):
        layout = {
            "osgi/client-extensions": ["a.zip", "b.zip"],
            "deploy": ["c.zip"],
            "client-extensions": ["ext/dist/d.zip"],
        }
        members = [f"{d}/{f}" for d, files in layout.items() for f in files]

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for member in members:
                target = root / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"PK\x03\x04")
            paths = {
                "root": root,
                "cx": root / "osgi" / "client-extensions",
                "deploy": root / "deploy",
                "ce_dir": root / "client-extensions",
            }
            from_disk = _scan_client_extension_archives(paths)

        from_members = scan_member_names(members, CLIENT_EXTENSION_SOURCES)
        self.assertEqual(from_disk, from_members)
        self.assertEqual(from_disk, ["a.zip", "b.zip", "c.zip", "d.zip"])

    def test_matching_is_anchored_at_the_package_root(self):
        """PurePosixPath.match alone is right-anchored and would accept this."""
        self.assertEqual(
            scan_member_names(["vendor/deploy/x.zip"], CLIENT_EXTENSION_SOURCES), []
        )

    def test_unrelated_payload_members_are_not_claimed(self):
        noise = [
            "data/dump.sql",
            "files/portal-ext.properties",
            "osgi/state/bundle.info",
            "client-extensions/ext/src/main.js",
        ]
        self.assertEqual(scan_member_names(noise, CLIENT_EXTENSION_SOURCES), [])
        self.assertEqual(scan_member_names(noise, OSGI_MODULE_SOURCES), [])


if __name__ == "__main__":
    unittest.main()
