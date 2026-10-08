"""A site-initializer client extension must not be present at first boot (LDM-#1779).

Measured on a live DXP ``2026.q3.0``: a site-initializer client extension
sitting in ``osgi/client-extensions/`` when the portal starts against an empty
database kills the extender's ``addingBundle`` with

    java.lang.NullPointerException: Cannot invoke
    "com.liferay.portal.kernel.model.Layout.getGroupId()" because "layout" is null
        at com.liferay.portal.util.PortalImpl.getCanonicalURL(PortalImpl.java:1556)

...and then logs ``STARTED`` anyway. No site, no fragments, no page. The same
artifact dropped into the running portal initialises in ~100 ms.

``ldm import`` used to put it there. It now stages it under
``.ldm/deferred-client-extensions/`` and ``_wait_for_ready`` deploys it once
the portal is healthy.

Nothing here is mocked in the hydration path: the workspace is real, the zips
are real zips, and every assertion is about which file exists at which path
with which bytes. The header the detector keys on was read out of a real built
artifact rather than guessed --
``ldm-cx-samples/client-extensions/ecopulse-site-initializer/dist/*.zip``
carries ``Liferay-Client-Extension-Site-Initializer=site-initializer/`` in
``WEB-INF/liferay-plugin-package.properties``, and that file is the *only*
place the built zip records it (there is no ``client-extension.yaml`` inside
it; the ``type: siteInitializer`` declaration is a source-side descriptor that
the build consumes).
"""

import ast
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.workspace import WorkspaceService
from ldm_core.workspace.site_initializers import (
    PLUGIN_PACKAGE_PROPERTIES,
    SITE_INITIALIZER_HEADER,
    deferred_dir,
    deploy_pending,
    is_site_initializer_zip,
    pending,
)

SAMPLES = Path("/Volumes/SanDisk/repos/ldm-cx-samples/client-extensions")


def _make_zip(path: Path, entries: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def _site_initializer_zip(path: Path) -> Path:
    """The shape a real built site-initializer CX has on disk.

    Entry list taken from the ecopulse artifact: a Dockerfile, an LCP.json, the
    plugin-package properties carrying the header, the nested content zip, and
    the generated ``*.client-extension-config.json``.
    """
    return _make_zip(
        path,
        {
            "Dockerfile": "FROM scratch\n",
            # LDM-#1962: a real built site initializer declares an id and
            # `"kind": "Job"` -- checked against the ecopulse artifact this
            # fixture is described as being taken from. `{}` made it look like
            # a service with no identity, which LDM now refuses to deploy.
            "LCP.json": '{"id": "ldmverifysiteinitializer", "kind": "Job"}\n',
            PLUGIN_PACKAGE_PROPERTIES: (
                "#Mon Jul 20 08:57:51 BST 2026\n"
                f"Bundle-SymbolicName={path.stem.replace('-', '')}\n"
                f"{SITE_INITIALIZER_HEADER}=site-initializer/\n"
                "module-group-id=liferay\n"
                f"name={path.stem}\n"
            ),
            "site-initializer/site-initializer.json": '{"name":"LDM Verify"}\n',
            "site-initializer/site-initializer.zip": b"PK\x03\x04 not really\n",
            f"{path.stem}.client-extension-config.json": "{}\n",
        },
    )


class TheDetector(unittest.TestCase):
    """Which zips are site initializers, keyed on the extender's own header."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def test_the_header_marks_a_site_initializer(self):
        zip_path = _site_initializer_zip(self.base / "verify-site-initializer.zip")
        self.assertTrue(is_site_initializer_zip(zip_path))

    def test_a_custom_element_is_not_one(self):
        zip_path = _make_zip(
            self.base / "my-element.zip",
            {
                PLUGIN_PACKAGE_PROPERTIES: (
                    "Bundle-SymbolicName=myelement\nmodule-group-id=liferay\n"
                ),
                "my-element.client-extension-config.json": "{}\n",
            },
        )
        self.assertFalse(is_site_initializer_zip(zip_path))

    def test_a_zip_without_the_properties_file_is_not_one(self):
        zip_path = _make_zip(self.base / "bare.zip", {"README.md": "hi"})
        self.assertFalse(is_site_initializer_zip(zip_path))

    def test_the_name_is_not_the_marker(self):
        """A zip called ``*-site-initializer.zip`` with no header is not one.

        Guards against the obvious wrong implementation. Liferay's
        ``SiteInitializerClientExtension`` tracks bundles on the header alone,
        so a filename heuristic would both miss real initializers and defer
        extensions that are fine where they are.
        """
        zip_path = _make_zip(
            self.base / "looks-like-a-site-initializer.zip",
            {PLUGIN_PACKAGE_PROPERTIES: "Bundle-SymbolicName=nope\n"},
        )
        self.assertFalse(is_site_initializer_zip(zip_path))

    def test_an_empty_header_value_does_not_count(self):
        zip_path = _make_zip(
            self.base / "empty-header.zip",
            {PLUGIN_PACKAGE_PROPERTIES: f"{SITE_INITIALIZER_HEADER}=\n"},
        )
        self.assertFalse(is_site_initializer_zip(zip_path))

    def test_a_corrupt_zip_is_not_deferred(self):
        """Failing to identify an artifact must not change its deploy moment."""
        zip_path = self.base / "corrupt.zip"
        zip_path.write_bytes(b"not a zip at all")
        self.assertFalse(is_site_initializer_zip(zip_path))

    @unittest.skipUnless(
        SAMPLES.is_dir(), "ldm-cx-samples checkout not present on this machine"
    )
    def test_it_agrees_with_the_real_built_artifacts(self):
        """Run against every built sample CX, not a fixture of our own making.

        Sixteen real ``dist/*.zip`` artifacts, of which exactly two are site
        initializers (ecopulse and veridian). The other fourteen span batch,
        brand, custom element, favicon, theme, microservice, object action,
        workflow action and spritemap -- including ones that carry a
        Dockerfile, which is the other thing in that zip that looks
        distinguishing and is not.
        """
        verdicts = {
            f"{d.name}/{z.name}": is_site_initializer_zip(z)
            for d in sorted(SAMPLES.iterdir())
            if d.is_dir()
            for z in sorted(d.glob("dist/*.zip"))
        }
        self.assertTrue(verdicts, "no built sample artifacts found to check")

        flagged = sorted(k for k, v in verdicts.items() if v)
        self.assertEqual(
            flagged,
            [
                "ecopulse-site-initializer/ecopulse-site-initializer.zip",
                "veridian-site-initializer/veridian-site-initializer.zip",
            ],
            f"detector disagreed with the real artifacts: {verdicts}",
        )


class _FakeManager(BaseHandler):
    """Minimal manager: real ``read_meta``/``write_meta``, fixed project path."""

    def __init__(self, project_path, args=None):
        args = args or SimpleNamespace(
            project="proj", non_interactive=True, verbose=False
        )
        super().__init__(args)
        self._project_path = project_path
        self._non_interactive = True
        self.cloud = MagicMock()
        # Assigned by `_HydrationCase.setUp`; declared so the real manager's
        # `.workspace` attribute exists on the fake too.
        self.workspace: Any = None

        from ldm_core.defaults import DefaultsManager

        self.defaults = DefaultsManager()

    @property
    def non_interactive(self):
        return self._non_interactive

    @non_interactive.setter
    def non_interactive(self, value):
        self._non_interactive = value

    def detect_project_path(self, project_id=None, for_init=False, fatal=True):
        return self._project_path


class _HydrationCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)

        self.workspace = base / "workspace"
        self.workspace.mkdir()

        self.project = base / "project"
        self.manager = _FakeManager(self.project)
        self.service = WorkspaceService(self.manager)
        # The real service, as `manager.workspace` is on a real manager -- so
        # the OAuth rewrite exercised below is the shipped one, not a mock.
        self.manager.workspace = self.service

        self.paths = {
            "root": self.project,
            "cx": self.project / "osgi" / "client-extensions",
            "modules": self.project / "osgi" / "modules",
            "deploy": self.project / "deploy",
        }
        for directory in self.paths.values():
            directory.mkdir(parents=True, exist_ok=True)

    def hydrate(self, overwrite=True):
        return self.service._hydrate_from_workspace(
            self.workspace, self.paths, overwrite=overwrite
        )


class TheImport(_HydrationCase):
    """What ``ldm import`` leaves on disk, per artifact type."""

    def test_a_site_initializer_is_kept_out_of_osgi_client_extensions(self):
        source = _site_initializer_zip(
            self.workspace / "client-extensions" / "ldm-verify-site-initializer.zip"
        )
        original_bytes = source.read_bytes()

        self.hydrate()

        deployed = self.paths["cx"] / "ldm-verify-site-initializer.zip"
        self.assertFalse(
            deployed.exists(),
            "the site initializer reached osgi/client-extensions before the "
            "first boot -- this is the NPE in LDM-#1779",
        )

        staged = deferred_dir(self.project) / "ldm-verify-site-initializer.zip"
        self.assertTrue(staged.is_file(), "the artifact was not staged anywhere")
        self.assertEqual(
            staged.read_bytes(),
            original_bytes,
            "the staged artifact is not byte-identical to the built one",
        )

    def test_the_docker_build_context_is_still_expanded(self):
        """Deferral must not hide the extension from compose.

        ``scan_client_extensions`` describes an extension to compose from the
        expanded ``client-extensions/<stem>/`` folder, so holding the zip back
        must leave that folder exactly where it was.
        """
        _site_initializer_zip(
            self.workspace / "client-extensions" / "ldm-verify-site-initializer.zip"
        )

        self.hydrate()

        expanded = self.project / "client-extensions" / "ldm-verify-site-initializer"
        self.assertTrue(expanded.is_dir(), "the build context was not expanded")
        self.assertTrue((expanded / PLUGIN_PACKAGE_PROPERTIES).is_file())

    def test_a_non_site_initializer_still_goes_straight_to_liferay(self):
        """The regression this fix must not cause.

        Every other client-extension type is fine where it was, and moving
        their deployment to after readiness would delay every boot.
        """
        source = _make_zip(
            self.workspace / "client-extensions" / "my-ext.zip",
            {
                "client-extension.yaml": "my-ext:\n  type: customElement\n",
                PLUGIN_PACKAGE_PROPERTIES: "Bundle-SymbolicName=myext\n",
                "src/index.js": "console.log('hi');",
            },
        )
        original_bytes = source.read_bytes()

        self.hydrate()

        deployed = self.paths["cx"] / "my-ext.zip"
        self.assertTrue(deployed.is_file(), "a normal CX stopped being deployed")
        self.assertEqual(deployed.read_bytes(), original_bytes)
        self.assertEqual(pending(self.project), [], "a normal CX was deferred")

    def test_a_mixed_workspace_splits_the_two_apart(self):
        _site_initializer_zip(
            self.workspace / "client-extensions" / "ldm-verify-site-initializer.zip"
        )
        _make_zip(
            self.workspace / "client-extensions" / "my-ext.zip",
            {"client-extension.yaml": "my-ext:\n  type: customElement\n"},
        )

        self.hydrate()

        self.assertEqual(
            sorted(p.name for p in self.paths["cx"].glob("*.zip")),
            ["my-ext.zip"],
        )
        self.assertEqual(
            sorted(p.name for p in pending(self.project)),
            ["ldm-verify-site-initializer.zip"],
        )

    def test_a_site_initializer_shipped_under_fragments_is_deferred_too(self):
        """``_sync_fragments`` routes any non-fragment zip to the CX sync.

        The same pre-boot moment applies, so the same deferral must.
        """
        _site_initializer_zip(
            self.workspace / "fragments" / "ldm-verify-site-initializer.zip"
        )

        self.hydrate()

        self.assertFalse(
            (self.paths["cx"] / "ldm-verify-site-initializer.zip").exists()
        )
        self.assertEqual(
            sorted(p.name for p in pending(self.project)),
            ["ldm-verify-site-initializer.zip"],
        )


class TheDeployCommand(_HydrationCase):
    """``ldm deploy`` and the ``ldm dev`` monitor act on a RUNNING portal."""

    def test_a_direct_sync_deploys_a_site_initializer_immediately(self):
        """The default must stay "deploy now".

        Dropping the artifact into a portal that is already up is the moment
        that *works* -- ~100 ms to an initialised site, measured. Deferring
        here would break ``ldm deploy`` for the one artifact type this fix is
        about.
        """
        source = _site_initializer_zip(
            self.workspace / "ldm-verify-site-initializer.zip"
        )
        original_bytes = source.read_bytes()

        self.service._sync_cx_artifact(source, self.paths)

        deployed = self.paths["cx"] / "ldm-verify-site-initializer.zip"
        self.assertTrue(
            deployed.is_file(),
            "an explicit deploy of a site initializer was deferred",
        )
        self.assertEqual(deployed.read_bytes(), original_bytes)
        self.assertEqual(pending(self.project), [])


class TheDeferredDeployment(_HydrationCase):
    """What happens once the portal is healthy."""

    def test_it_moves_every_staged_artifact_into_osgi_client_extensions(self):
        _site_initializer_zip(
            self.workspace / "client-extensions" / "ldm-verify-site-initializer.zip"
        )
        self.hydrate()
        staged_bytes = pending(self.project)[0].read_bytes()

        deployed = deploy_pending(self.manager, self.project, self.paths, {})

        self.assertEqual(deployed, ["ldm-verify-site-initializer.zip"])
        landed = self.paths["cx"] / "ldm-verify-site-initializer.zip"
        self.assertTrue(landed.is_file())
        self.assertEqual(landed.read_bytes(), staged_bytes)
        self.assertEqual(
            pending(self.project), [], "the staging directory was not drained"
        )

    def test_it_is_idempotent(self):
        """A second boot must not re-deploy, and must not fail."""
        _site_initializer_zip(
            self.workspace / "client-extensions" / "ldm-verify-site-initializer.zip"
        )
        self.hydrate()
        deploy_pending(self.manager, self.project, self.paths, {})

        self.assertEqual(deploy_pending(self.manager, self.project, self.paths, {}), [])
        self.assertTrue(
            (self.paths["cx"] / "ldm-verify-site-initializer.zip").is_file()
        )

    def test_nothing_staged_is_a_silent_no_op(self):
        with patch("ldm_core.ui.UI.info") as announced:
            self.assertEqual(
                deploy_pending(self.manager, self.project, self.paths, {}), []
            )
        self.assertEqual(list(self.paths["cx"].glob("*.zip")), [])
        announced.assert_not_called()

    def test_the_deployment_is_announced_at_default_verbosity(self):
        """Not `UI.detail`, which is gated behind INFO_MODE/VERBOSE.

        Site initialisation takes tens of seconds after this point. A default
        `ldm run` that printed nothing would end its banner with no site in
        existence yet, which looks exactly like the silent failure this change
        removes. LDM-#1728 is the same lesson from the other direction.
        """
        _site_initializer_zip(
            self.workspace / "client-extensions" / "ldm-verify-site-initializer.zip"
        )
        self.hydrate()

        with patch("ldm_core.ui.UI.info") as announced:
            deploy_pending(self.manager, self.project, self.paths, {})

        announced.assert_called_once()
        self.assertIn("ldm-verify-site-initializer.zip", announced.call_args.args[0])

    def test_the_oauth_rewrite_reaches_the_deferred_artifact(self):
        """``scan_client_extensions`` only rewrites zips already in ``cx``.

        A deferred artifact is not there when that scan runs, so the rewrite
        has to happen on the way in. Without it a site initializer bundling an
        ``oAuthApplicationHeadlessServer`` -- which the real ecopulse one does
        -- keeps ``localhost`` URLs on a project served under a custom host.
        """
        _site_initializer_zip(
            self.workspace / "client-extensions" / "ldm-verify-site-initializer.zip"
        )
        self.hydrate()

        seen = []

        def _record(*args, **_kwargs):
            seen.append(args)

        self.manager.workspace._rewrite_oauth_urls_in_zip = _record

        deploy_pending(
            self.manager, self.project, self.paths, {"host_name": "demo.example.com"}
        )

        self.assertEqual(len(seen), 1, "the OAuth rewrite never ran")
        zip_arg, host_arg, name_arg, root_arg = seen[0]
        self.assertEqual(host_arg, "demo.example.com")
        self.assertEqual(name_arg, "ldm-verify-site-initializer")
        self.assertEqual(root_arg, self.project)
        self.assertEqual(
            zip_arg.parent,
            deferred_dir(self.project),
            "the rewrite must happen while the artifact is still staged, not "
            "after Liferay's file installer can already see it",
        )


if __name__ == "__main__":
    unittest.main()


class TheRestoreHoldsItBackToo(unittest.TestCase):
    """LDM-#2105: the deferral guarded `ldm import` and not snapshot restore.

    A restored tree arrives whole, so the site-initializer zip is already in
    `osgi/client-extensions/` when the container starts. Liferay processes
    it during startup and `SiteInitializerClientExtension` NPEs on a null
    Layout -- the portal's default layouts do not exist yet. 48 occurrences
    on the reported run, no site created, `/web/<site>` 404 afterwards.

    `handlers/snapshot.py` had zero references to the deferral: it never
    calls `_sync_cx_artifact`, so the branch LDM-#1779 added was
    unreachable from here.

    These assert the OUTCOME -- which file is where -- rather than that a
    deferral function was called. The function existed and was called all
    along, just not from this path, so a call-assertion would have passed
    against the defect.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.cx_dir = self.root / "osgi" / "client-extensions"
        self.cx_dir.mkdir(parents=True)

        from ldm_core.handlers.snapshot import SnapshotService

        self.service = SnapshotService.__new__(SnapshotService)

    def _defer(self):
        return self.service._defer_restored_site_initializers({"root": self.root})

    def _deferred(self):
        from ldm_core.workspace.site_initializers import deferred_dir

        d = deferred_dir(self.root)
        return sorted(p.name for p in d.glob("*.zip")) if d.is_dir() else []

    def test_a_restored_site_initializer_leaves_osgi_client_extensions(self):
        """The whole bug: present at boot means processed at boot."""
        _site_initializer_zip(self.cx_dir / "aica-site-initializer.zip")

        self._defer()

        self.assertFalse(
            (self.cx_dir / "aica-site-initializer.zip").exists(),
            "the zip is still where Liferay will pick it up during startup",
        )

    def test_it_is_staged_for_deployment_after_the_portal_is_ready(self):
        """Removing it is only half: it must still be deployed later, which
        is what `_wait_for_ready` picks up."""
        _site_initializer_zip(self.cx_dir / "aica-site-initializer.zip")

        self._defer()

        self.assertEqual(self._deferred(), ["aica-site-initializer.zip"])

    def test_a_plain_client_extension_is_left_alone(self):
        """The control. Only site initializers break at boot; everything
        else must deploy exactly as before -- AICA's batch extension
        deployed fine in the same boot that produced the NPEs."""
        _make_zip(
            self.cx_dir / "aica-batch.zip",
            {"Dockerfile": "FROM scratch\n", "LCP.json": "{}"},
        )

        staged = self._defer()

        self.assertTrue((self.cx_dir / "aica-batch.zip").exists())
        self.assertEqual(staged, [])

    def test_a_mixed_restore_splits_them(self):
        _site_initializer_zip(self.cx_dir / "si.zip")
        _make_zip(self.cx_dir / "plain.zip", {"LCP.json": "{}"})

        self._defer()

        self.assertFalse((self.cx_dir / "si.zip").exists())
        self.assertTrue((self.cx_dir / "plain.zip").exists())
        self.assertEqual(self._deferred(), ["si.zip"])

    def test_a_project_with_no_client_extensions_is_fine(self):
        import shutil

        shutil.rmtree(self.cx_dir)

        self.assertEqual(self._defer(), [])

    def test_an_unreadable_zip_does_not_fail_the_restore(self):
        """The data is already on disk by this point. Aborting here would
        turn a cosmetic problem into a broken restore."""
        (self.cx_dir / "corrupt.zip").write_bytes(b"not a zip")

        with patch(
            "ldm_core.workspace.site_initializers.is_site_initializer_zip",
            side_effect=OSError("boom"),
        ):
            self.assertEqual(self._defer(), [])

        self.assertTrue((self.cx_dir / "corrupt.zip").exists())


class TheRestorePathActuallyCallsIt(unittest.TestCase):
    """A wiring contract, stated as one rather than dressed up as behaviour.

    The helper being correct is worthless if `cmd_restore` never calls it --
    which is precisely the defect LDM-#2105 was: the deferral existed,
    worked, and was called from `ldm import` only.

    This is a source-level contract because driving a full `cmd_restore`
    needs a snapshot archive, a database and a container. It catches the
    call being deleted, which is the realistic regression; it does NOT
    prove the call is reached at runtime, and the behavioural assertions
    above do not prove it is wired. The two together are the coverage.
    """

    def _restore_fn(self):
        import ast
        import inspect

        from ldm_core.handlers import snapshot as snapshot_module

        tree = ast.parse(inspect.getsource(snapshot_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "cmd_restore":
                return node
        self.fail("cmd_restore not found in handlers/snapshot.py")
        return None

    def test_cmd_restore_defers_site_initializers(self):
        calls = [
            n.func.attr
            for n in ast.walk(self._restore_fn())
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]

        self.assertIn(
            "_defer_restored_site_initializers",
            calls,
            "cmd_restore no longer holds back site initializers; a restored "
            "project will deploy one before boot and create no site",
        )

    def test_it_is_deferred_after_extraction_and_before_the_stack_starts(self):
        """Ordering is the whole point, and the anchors have to be the right
        ones. `cmd_restore` calls `cmd_run` three times: twice with
        `no_up=True`, which only scaffolds, and once without, which is what
        actually starts the project. An earlier version of this test
        anchored on the first `cmd_run` it found and failed against correct
        code -- the scaffolding call precedes the extraction.

        The real contract: stage AFTER the tree is written, BEFORE anything
        starts the container.
        """
        fn = self._restore_fn()

        def lines(attr, predicate=None):
            predicate = predicate or (lambda _n: True)
            return [
                n.lineno
                for n in ast.walk(fn)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == attr
                and predicate(n)
            ]

        def starts_the_stack(node):
            return not any(
                k.arg == "no_up"
                and isinstance(k.value, ast.Constant)
                and k.value.value is True
                for k in node.keywords
            )

        defer = lines("_defer_restored_site_initializers")
        extract = lines("_extract_snapshot_archive")
        starting = lines("cmd_run", starts_the_stack)

        self.assertTrue(defer, "cmd_restore does not defer at all")
        if extract:
            self.assertGreater(
                min(defer),
                min(extract),
                "deferral runs before the tree is extracted, so there is "
                "nothing there yet to hold back",
            )
        if starting:
            self.assertLess(
                max(defer),
                min(starting),
                "the stack starts before the site initializer is held back",
            )
