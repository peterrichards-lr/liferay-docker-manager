"""Where a stateless `.ldmp` puts client extensions (LDM-#2493 follow-up).

Three defects in the first stateless packages, raised by their first consumer:

1. Every client extension shipped twice -- the zip in `osgi/client-extensions/`
   and the tree `ldm link` expands into `client-extensions/<stem>/` -- so a
   site initializer's images doubled the package. Only a container needs the
   expanded tree; for anything else LDM reads only the descriptors.
2. A site initializer shipped in `osgi/client-extensions/`. A stateless pack is
   booted against an empty database, and nothing on the `--samples` path (this
   LDM, or v2.26.x copying the payload over the project) held it back, so it
   was present at first boot: the LDM-#1779 silent failure.
3. A site initializer `ldm link` had parked in `.ldm/deferred-client-extensions/`
   (deployed only once `ldm wait` succeeds) was not in the package at all.

Driven through the real `cmd_package` and, for the import, the real
`cmd_restore`; the samples path is the copy `sync_samples` performs, read back
by the same `pending()` that `_wait_for_ready` deploys from.
"""

import io
import json
import shutil
import tarfile
import zipfile
from unittest.mock import MagicMock, patch

from ldm_core.tests.test_stateless_package import (
    _payload_names,
    _RestoreHarness,
    _write_tree,
)
from ldm_core.utils import safe_extract
from ldm_core.workspace import site_initializers

SI = "demo-site-initializer"
FRONTEND = "demo-frontend"
SVC = "demo-svc"
IMAGE = b"\x89PNG" + b"x" * 4096

SI_FILES = {
    "WEB-INF/liferay-plugin-package.properties": (
        b"name=Demo\n"
        + site_initializers.SITE_INITIALIZER_HEADER.encode()
        + b"=demo-site-initializer\n"
    ),
    "site-initializer/images/hero.png": IMAGE,
    # A real site initializer ships a Dockerfile AND `kind: Job`: not a
    # container LDM runs.
    "Dockerfile": b"FROM liferay/job\n",
    "LCP.json": b'{"id": "demositeinit", "kind": "Job"}',
    f"{SI}.client-extension-config.json": b'{"demo-si": {"type": "siteInitializer"}}',
}
FRONTEND_FILES = {
    f"{FRONTEND}.client-extension-config.json": (
        b'{"demo-frontend": {"type": "customElement"}}'
    ),
    "static/js/main.js": b"console.log('x')" * 256,
}


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _expanded(stem, files):
    return {f"client-extensions/{stem}/{name}": data for name, data in files.items()}


class _Layout(_RestoreHarness):
    """A project as `ldm link` leaves it: each zip in `osgi/client-extensions/`
    and expanded beside it in `client-extensions/<stem>/`."""

    def add_linked_cx(self, stem, files, parked=False):
        zip_dir = (
            site_initializers.deferred_dir(self.root)
            if parked
            else self.root / "osgi" / "client-extensions"
        )
        zip_dir.mkdir(parents=True, exist_ok=True)
        (zip_dir / f"{stem}.zip").write_bytes(_zip_bytes(files))
        _write_tree(self.root, _expanded(stem, files))


class ASiteInitializerIsShippedHeldBack(_Layout):
    """Claim 2: never in `osgi/client-extensions/` of a stateless payload."""

    def setUp(self):
        super().setUp()
        self.add_linked_cx(SI, SI_FILES)
        self.add_linked_cx(FRONTEND, FRONTEND_FILES)
        self.pkg = self.package(stateless=True)
        self.shipped = _payload_names(self.pkg)

    def test_it_ships_in_the_deferral_directory(self):
        self.assertIn(f".ldm/deferred-client-extensions/{SI}.zip", self.shipped)
        self.assertNotIn(f"osgi/client-extensions/{SI}.zip", self.shipped)

    def test_another_extension_stays_where_it_was(self):
        self.assertIn(f"osgi/client-extensions/{FRONTEND}.zip", self.shipped)
        self.assertFalse([n for n in self.shipped if FRONTEND in n and ".ldm/" in n])

    def test_the_publisher_project_is_not_changed(self):
        self.assertTrue((self.root / f"osgi/client-extensions/{SI}.zip").is_file())
        self.assertEqual(site_initializers.pending(self.root), [])

    def test_the_samples_copy_holds_it_back_until_ready(self):
        """`sync_samples` (and v2.26.x `--samples`) copy the extracted payload
        over the project; `_wait_for_ready` deploys what `pending()` lists."""
        extracted = self.base / "samples"
        with tarfile.open(self.pkg / "files.tar.gz", "r:gz") as tar:
            safe_extract(tar, extracted)
        project = self.base / "samples-project"
        project.mkdir()
        shutil.copytree(extracted, project, dirs_exist_ok=True)

        cx = project / "osgi" / "client-extensions"
        self.assertFalse((cx / f"{SI}.zip").exists(), "present at first boot")
        self.assertEqual(
            [p.name for p in site_initializers.pending(project)], [f"{SI}.zip"]
        )

        deployed = site_initializers.deploy_pending(
            MagicMock(), project, {"cx": cx}, {}
        )
        self.assertEqual(deployed, [f"{SI}.zip"])
        self.assertTrue((cx / f"{SI}.zip").is_file())

    def test_a_stateless_import_holds_it_back_until_ready(self):
        target = self.restore(self.pkg)
        self.assertFalse((target / f"osgi/client-extensions/{SI}.zip").exists())
        self.assertEqual(
            [p.name for p in site_initializers.pending(target)], [f"{SI}.zip"]
        )
        self.assertTrue((target / f"osgi/client-extensions/{FRONTEND}.zip").is_file())


class AParkedSiteInitializerIsPackaged(_Layout):
    """Claim 3: `ldm link` parks it until `ldm wait` succeeds; a package built
    before then used to leave it out."""

    def setUp(self):
        super().setUp()
        self.add_linked_cx(SI, SI_FILES, parked=True)
        self.shipped = _payload_names(self.package(stateless=True))

    def test_it_is_in_the_package(self):
        self.assertIn(f".ldm/deferred-client-extensions/{SI}.zip", self.shipped)

    def test_its_descriptors_ship_with_it(self):
        self.assertIn(f"client-extensions/{SI}/LCP.json", self.shipped)


class AParkedCopyWinsOverADeployedOne(_Layout):
    def test_the_parked_zip_is_the_one_shipped(self):
        self.add_linked_cx(SI, SI_FILES)
        newer = {**SI_FILES, "site-initializer/marker.txt": b"newer"}
        parked = site_initializers.deferred_dir(self.root)
        parked.mkdir(parents=True)
        (parked / f"{SI}.zip").write_bytes(_zip_bytes(newer))

        pkg = self.package(stateless=True)
        with tarfile.open(pkg / "files.tar.gz", "r:gz") as tar:
            names = [m.name for m in tar.getmembers() if m.name.endswith(f"{SI}.zip")]
            self.assertEqual(names, [f".ldm/deferred-client-extensions/{SI}.zip"])
            member = tar.extractfile(names[0])
            assert member is not None
            with zipfile.ZipFile(io.BytesIO(member.read())) as z:
                self.assertIn("site-initializer/marker.txt", z.namelist())


class OnlyAContainerShipsItsExpandedTree(_Layout):
    """Claim 1."""

    def setUp(self):
        super().setUp()
        self.add_linked_cx(SI, SI_FILES)
        self.add_linked_cx(FRONTEND, FRONTEND_FILES)
        self.add_linked_cx(
            SVC,
            {
                "Dockerfile": b"FROM liferay/jar-runner\n",
                "LCP.json": b'{"id": "demosvc"}',
                f"{SVC}.client-extension-config.json": b'{"demo-svc": {}}',
                "app/server.jar": b"jar" * 64,
            },
        )
        self.pkg = self.package(stateless=True)
        self.shipped = _payload_names(self.pkg)

    def test_a_site_initializer_ships_its_images_once(self):
        images = [n for n in self.shipped if n.endswith("hero.png")]
        self.assertEqual(images, [], "zipped AND expanded")
        for name in ("Dockerfile", "LCP.json", f"{SI}.client-extension-config.json"):
            self.assertIn(f"client-extensions/{SI}/{name}", self.shipped)

    def test_a_frontend_extension_ships_its_descriptor_only(self):
        self.assertIn(
            f"client-extensions/{FRONTEND}/{FRONTEND}.client-extension-config.json",
            self.shipped,
        )
        self.assertNotIn(
            f"client-extensions/{FRONTEND}/static/js/main.js", self.shipped
        )

    def test_a_container_ships_its_whole_build_context(self):
        for name in ("Dockerfile", "LCP.json", "app/server.jar"):
            self.assertIn(f"client-extensions/{SVC}/{name}", self.shipped)

    def test_the_imported_project_describes_the_same_extensions(self):
        """The seam: what `ldm run` scans from the imported, slimmed tree is
        what it scanned from the publisher's full one."""
        from ldm_core.workspace import metadata

        def scan(root):
            handler = MagicMock()
            handler.manager.read_meta.return_value = {}
            handler.manager.diagnostics.validate_lcp_json.return_value = (
                "valid",
                True,
                [],
            )
            with patch.object(
                metadata, "_resolve_and_persist_cx_port", return_value=None
            ):
                found = metadata.scan_client_extensions(
                    handler,
                    root,
                    root / "osgi" / "client-extensions",
                    root / "client-extensions",
                )
            return sorted(
                json.dumps(
                    {k: v for k, v in e.items() if k != "path"},
                    sort_keys=True,
                    default=str,
                )
                for e in found
            )

        publisher = scan(self.root)
        # The ones with an LCP.json or a typed yaml: the site initializer,
        # demo-svc, and my-svc from the shared fixture.
        self.assertEqual(len(publisher), 3)
        self.assertEqual(scan(self.restore(self.pkg)), publisher)


class AnExpandedTreeWithoutItsZipShipsWhole(_Layout):
    def test_nothing_is_dropped_when_the_zip_is_not_shipped(self):
        _write_tree(self.root, _expanded(FRONTEND, FRONTEND_FILES))
        shipped = _payload_names(self.package(stateless=True))
        self.assertIn(f"client-extensions/{FRONTEND}/static/js/main.js", shipped)
