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

LDM-#1588 added the second half of the file. Everything above it calls the
verifier directly, which leaves the path that reaches it -- download, checksum,
outer extraction, hydration -- unexercised: stop calling the verifier from
_import_ldm_package and all of those tests still pass. That path cannot be
asserted from scripts/verify_e2e_refactor.{sh,ps1} either, because the only
input routing to it is a GitHub repo URL whose latest release carries a .ldmp
and the API host is hardcoded, so it is driven here with requests.get mocked
and nothing else.
"""

import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.snapshot.archive import (
    CLIENT_EXTENSION_SOURCES,
    OSGI_MODULE_SOURCES,
    _scan_client_extension_archives,
    scan_member_names,
)
from ldm_core.workspace.importer import (
    _import_ldm_package,
    _parse_github_repo,
    _verify_ldm_package_manifest,
    cmd_import,
)

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


# ---------------------------------------------------------------------------
# LDM-#1588: the same recovery, driven through the path that reaches it.
# ---------------------------------------------------------------------------


def _build_ldmp(root, manifest_extra, payload_members):
    """A real .ldmp: the package dir above, tarred as a release asset is.

    Deliberately built from _build_package so both layers of the fixture agree
    -- the unit tests and the import test cannot drift into asserting against
    two different package shapes.
    """
    extract, _pkg = _build_package(root / "src", manifest_extra, payload_members)
    ldmp = root / f"{REPO}.ldmp"
    with tarfile.open(ldmp, "w:gz") as tar:
        for item in sorted(extract.iterdir()):
            tar.add(item, arcname=item.name)
    return ldmp


class _Response:
    """Just enough of requests.Response for _download_ldm_package_assets."""

    def __init__(self, body=b"", text=""):
        self._body = body
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]


class _ImportManager(_FakeManager):
    """The manager surface _import_ldm_package touches, and nothing else.

    read_meta/write_meta are the real implementations against a temp dir, so
    the project metadata this asserts on is genuinely written and read back.
    Everything that would reach Docker, git or the network is a MagicMock --
    cmd_restore in particular, which is the boundary this test stops at.
    """

    def __init__(self, project_path):
        self.project_path = Path(project_path)
        self.non_interactive = True
        self.args = MagicMock()
        # MagicMock attributes are truthy, and the hydrator reads several of
        # these with `or` fallbacks. Pin them to the real "absent" value.
        self.args.tag = None
        self.args.command = "import"
        self.args.host_name = None
        self.args.ssl = None
        self.args.port = None
        self.snapshot = MagicMock()
        self.runtime = MagicMock()
        self.verify_runtime_environment = MagicMock()
        self.check_uncommitted_changes = MagicMock()

    def detect_project_path(self, project_name, for_init=False):
        return self.project_path

    def setup_paths(self, root):
        root = Path(root)
        return {"root": root, "cx": root / "osgi" / "client-extensions"}

    def write_meta(self, path, meta):
        from ldm_core.utils import resolve_meta_file_path, write_meta

        write_meta(resolve_meta_file_path(path), meta)


class _ImportHost:
    def __init__(self, manager):
        self.manager = manager
        self._ensure_stopped = MagicMock()


class _ImportOutcome:
    """What an import did, whether or not it survived."""

    def __init__(self, project_path, manager):
        self.project_path = project_path
        self.manager = manager
        self.returned = None
        self.aborted = False
        self.warnings: list = []

    @property
    def restore_calls(self):
        return self.manager.snapshot.cmd_restore.call_args_list

    @property
    def project_meta(self):
        meta_file = self.project_path / "meta"
        if not meta_file.exists():
            return None
        return json.loads(meta_file.read_text(encoding="utf-8"))


def _import_package(root, manifest_extra, payload_members):
    """Run _import_ldm_package over a crafted release asset.

    Only `requests.get` is mocked: the checksum, the outer extraction, the
    manifest verification and the hydration are the real code.
    `_import_ldm_package` scratches under `Path.cwd()`, so this runs from a
    temp directory. SystemExit is caught rather than propagated so a refusal
    can be asserted alongside what the refusal did NOT do.
    """
    ldmp = _build_ldmp(root, manifest_extra, payload_members)
    body = ldmp.read_bytes()

    from ldm_core.utils import calculate_sha256

    checksum = calculate_sha256(ldmp)

    asset = {"name": ldmp.name, "url": "https://api.github.com/assets/1"}
    sha_asset = {
        "name": f"{ldmp.name}.sha256",
        "url": "https://api.github.com/assets/2",
    }

    def fake_get(url, **kwargs):
        return _Response(body=body) if url == asset["url"] else _Response(text=checksum)

    manager = _ImportManager(root / "projects" / REPO)
    outcome = _ImportOutcome(root / "projects" / REPO, manager)

    cwd = Path.cwd()
    os.chdir(root)
    try:
        with (
            # LDM_HOME so nothing can reach the developer's real ~/.ldm;
            # LDM_DRY_RUN cleared because write_meta diverts to an in-memory
            # VFS when it is set, and the project metadata below is read back
            # from disk.
            patch.dict(
                os.environ,
                {"LDM_HOME": str(root / "ldm-home"), "LDM_DRY_RUN": ""},
            ),
            patch("requests.get", side_effect=fake_get),
            patch("ldm_core.ui.UI.warning", side_effect=outcome.warnings.append),
        ):
            try:
                outcome.returned = _import_ldm_package(
                    _ImportHost(manager),
                    asset,
                    sha_asset,
                    OWNER,
                    REPO,
                    None,
                    None,
                    True,
                )
            except SystemExit:
                outcome.aborted = True
    finally:
        os.chdir(cwd)

    return outcome


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


class TestRecoveryDrivenThroughAnImport(unittest.TestCase):
    """LDM-#1588: the classes above call the verifier; this reaches it.

    Every test so far calls `_verify_ldm_package_manifest` directly, so all of
    them keep passing if the importer stops calling it, calls it with the wrong
    directory, or unpacks the payload somewhere `files.tar.gz` is no longer
    beside the manifest -- and `ldm quickstart aica` dies again with nothing
    red. The user-visible outcome of #1579 is not "the function returns a
    corrected dict", it is "the import completes", so that is what these
    assert: download, checksum, outer extraction, verification and hydration
    are the real code, with only `requests.get` standing in for the release
    asset.

    #1588 records why this cannot be an assertion in
    `scripts/verify_e2e_refactor.{sh,ps1}`: the only route to this code is a
    GitHub repo URL whose latest release carries a `.ldmp`, and the API host is
    hardcoded (`api.github.com`, importer.py `_check_github_release_for_package`),
    so exercising it end to end means publishing a deliberately malformed
    package to a real release. See TestOnlyOneInputReachesThisCode below.
    """

    def test_a_contradictory_package_imports_instead_of_dying(self):
        with tempfile.TemporaryDirectory() as d:
            outcome = _import_package(
                Path(d),
                {"includes_client_extensions": "true", "client_extensions": ""},
                [
                    "osgi/client-extensions/ai-commerce-accelerator-batch.zip",
                    "osgi/client-extensions/site-initializer.zip",
                    "data/dump.sql",
                ],
            )

            self.assertFalse(
                outcome.aborted, "the import refused a recoverable package"
            )
            self.assertEqual(outcome.returned, REPO)
            # Recovery is only worth anything if the import carries on to the
            # restore. Asserting the warning alone would pass on a package that
            # was corrected and then abandoned.
            self.assertEqual(len(outcome.restore_calls), 1)
            restore_call = outcome.restore_calls[0]
            self.assertEqual(restore_call.args[0], REPO)
            self.assertTrue(
                Path(restore_call.kwargs["backup_dir"]).name.startswith("extract_")
            )
            self.assertEqual(outcome.project_meta["restored_from_package"], "true")
            self.assertIn(
                "Recovered 2 from the package contents",
                " ".join(outcome.warnings),
            )

    def test_a_package_that_ships_nothing_still_aborts_the_import(self):
        """The #1568 gate must survive being reached through the real path."""
        with tempfile.TemporaryDirectory() as d:
            outcome = _import_package(
                Path(d),
                {"includes_client_extensions": "true", "client_extensions": ""},
                ["data/dump.sql"],
            )

            self.assertTrue(outcome.aborted, "a package shipping nothing was accepted")
            self.assertEqual(outcome.restore_calls, [])
            self.assertIsNone(
                outcome.project_meta, "a refused package still wrote project metadata"
            )

    def test_an_honest_package_needs_no_recovery_warning(self):
        with tempfile.TemporaryDirectory() as d:
            outcome = _import_package(
                Path(d),
                {
                    "includes_client_extensions": "true",
                    "client_extensions": "declared.zip",
                },
                ["osgi/client-extensions/declared.zip"],
            )

            self.assertFalse(outcome.aborted)
            self.assertEqual(len(outcome.restore_calls), 1)
            self.assertEqual(
                [w for w in outcome.warnings if "Recovered" in w],
                [],
                "an honest manifest was reported as recovered",
            )


class TestOnlyOneInputReachesThisCode(unittest.TestCase):
    """Why LDM-#1588 exists, measured rather than asserted in prose.

    The recovery lives behind `_import_ldm_package`, and cmd_import routes to
    that from exactly one kind of input. This drives cmd_import with all four
    destinations stubbed and records which one it actually picked, so the claim
    in #1588 is checked by running the router instead of by reading it.

    If a future change routes a local `.ldmp` through package verification --
    the option #1588 lists under "what would change this", and worth doing on
    its own merits since a local package currently skips the origin and db_type
    checks too -- this test fails, and the E2E assertion becomes possible.
    """

    def _route(self, source_path):
        taken = []

        def record(name, ret=None):
            def _stub(*args, **kwargs):
                taken.append(name)
                return ret

            return _stub

        host = MagicMock()
        host.manager.non_interactive = True
        host.manager.args.project = None
        host.manager.args.project_flag = None
        host.manager.args.clone_only = False
        host._parse_github_repo = lambda url: _parse_github_repo(host, url)

        with (
            # cmd_import short-circuits to _handle_dry_run when this is set.
            patch.dict(os.environ, {"LDM_DRY_RUN": ""}),
            patch(
                "ldm_core.workspace.importer._download_remote_archive",
                side_effect=record("_download_remote_archive"),
            ),
            patch(
                "ldm_core.workspace.importer._check_github_release_for_package",
                side_effect=record(
                    "_check_github_release_for_package", (False, None, None, None)
                ),
            ),
            patch(
                "ldm_core.workspace.importer._clone_remote_repository",
                side_effect=record("_clone_remote_repository"),
            ),
            patch(
                "ldm_core.pipelines.import_pipeline.ImportPipeline.run",
                side_effect=record("ImportPipeline.run"),
            ),
        ):
            cmd_import(host, source_path, no_run=True)

        return taken

    def test_a_local_ldmp_never_reaches_package_verification(self):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / f"{REPO}.ldmp"
            local.write_bytes(b"PK\x03\x04")
            self.assertEqual(self._route(str(local)), ["ImportPipeline.run"])

    def test_an_ldmp_url_never_reaches_package_verification(self):
        self.assertEqual(
            self._route("https://example.com/downloads/widget.ldmp"),
            ["_download_remote_archive"],
        )

    def test_a_repo_url_is_the_only_route_to_the_release_asset_path(self):
        self.assertEqual(
            self._route(f"https://github.com/{OWNER}/{REPO}"),
            ["_check_github_release_for_package", "_clone_remote_repository"],
        )


if __name__ == "__main__":
    unittest.main()
