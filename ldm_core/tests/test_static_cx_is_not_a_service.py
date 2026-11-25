"""A static client extension is not a service (LDM-#2534).

Liferay's CX build writes a Dockerfile and an LCP.json into EVERY client
extension, static ones included. LDM's test for "is this a service" was "has a
Dockerfile and LCP `kind` is not Job", copied into three places, so every
theme, favicon, spritemap and custom element got a sidecar container of its
own, and a stateless package shipped each one's expanded folder beside its zip.

The fixtures are the members of `ldm-cx-samples`' built zips, as
`gradlew build` leaves them in `client-extensions/<name>/dist/<name>.zip`
(trimmed to the files LDM reads plus one asset):

* `ecopulse-theme` (themeCSS) and `ecopulse-grid-map` (customElement):
  `FROM liferay/caddy`, `COPY static/ /public_html/`, LCP.json
  `loadBalancer.cdn: true` and no `ports`. The config json's `baseURL` is
  `${portalURL}/o/<projectName>`: the portal serves it from the zip.
* `ecopulse-object-action`: a node runtime, LCP.json `ports` 3003, types
  `objectAction` + `oAuthApplicationHeadlessServer`. A real service.

None of the built zips carries `client-extension.yaml`; the declared types
come from `<name>.client-extension-config.json`.

Each call site is driven separately, because the defect was three copies of
one rule and a test of the shared predicate alone would bracket them without
crossing any of them.
"""

import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.snapshot import archive
from ldm_core.workspace import metadata

CADDY_DOCKERFILE = "FROM liferay/caddy:latest\n\nCOPY static/ /public_html/\n"


def _static_lcp(ext_id):
    return json.dumps(
        {
            "cpu": 0.05,
            "env": {
                "LIFERAY_ROUTES_CLIENT_EXTENSION": "/etc/liferay/lxc/ext-init-metadata",
                "LIFERAY_ROUTES_DXP": "/etc/liferay/lxc/dxp-metadata",
            },
            "environments": {
                "dev": {"loadBalancer": {"cdn": False, "targetPort": 80}},
                "infra": {"deploy": False},
            },
            "id": ext_id,
            "kind": "Deployment",
            "loadBalancer": {"cdn": True, "targetPort": 80},
            "memory": 50,
            "scale": 1,
        }
    )


def _cet_config(name, cx_type):
    return json.dumps(
        {
            f"com.liferay.client.extension.type.configuration.CETConfiguration~{name}": {
                "baseURL": f"${{portalURL}}/o/{name}",
                "projectId": name.replace("-", ""),
                "projectName": name,
                "type": cx_type,
            }
        }
    )


THEME = "ecopulse-theme"
THEME_FILES = {
    "Dockerfile": CADDY_DOCKERFILE,
    "LCP.json": _static_lcp("ecopulsetheme"),
    f"{THEME}.client-extension-config.json": _cet_config(THEME, "themeCSS"),
    "static/css/main.css": "body{}" * 512,
}

ELEMENT = "ecopulse-grid-map"
ELEMENT_FILES = {
    "Dockerfile": CADDY_DOCKERFILE,
    "LCP.json": _static_lcp("ecopulsegridmap"),
    f"{ELEMENT}.client-extension-config.json": _cet_config(ELEMENT, "customElement"),
    "static/js/main.js": "console.log(1);" * 512,
}

SERVICE = "ecopulse-object-action"
SERVICE_FILES = {
    "Dockerfile": (
        "FROM node:22-slim\n\nWORKDIR /app\n\nCOPY package*.json ./\n\n"
        "RUN npm install --production\n\nCOPY . .\n\nEXPOSE 3003\n\n"
        'CMD ["npm", "start"]\n'
    ),
    "LCP.json": json.dumps(
        {
            "cpu": 0.2,
            "id": "ecopulseobjectaction",
            "kind": "Deployment",
            "loadBalancer": {"targetPort": 3003},
            "ports": [{"external": True, "port": 3003}],
        }
    ),
    f"{SERVICE}.client-extension-config.json": json.dumps(
        {
            "com.liferay.oauth2.provider.configuration."
            f"OAuth2ProviderApplicationHeadlessServerConfiguration~{SERVICE}-oauth": {
                "projectId": "ecopulseobjectaction",
                "projectName": SERVICE,
                "type": "oAuthApplicationHeadlessServer",
            },
            "com.liferay.object.internal.configuration."
            f"FunctionObjectActionExecutorFactoryConfiguration~{SERVICE}": {
                "projectId": "ecopulseobjectaction",
                "projectName": SERVICE,
                "type": "objectAction",
            },
        }
    ),
    "package.json": '{"name": "ecopulse-object-action"}',
    "server.js": "require('http').createServer().listen(3003);",
}

ALL = {THEME: THEME_FILES, ELEMENT: ELEMENT_FILES, SERVICE: SERVICE_FILES}


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _write_folder(folder, files):
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)


def _handler():
    handler = MagicMock()
    handler.manager.diagnostics.validate_lcp_json.return_value = ("valid", True, [])
    return handler


class _Project(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "project"
        self.ce_dir = self.root / "client-extensions"
        self.osgi_cx = self.root / "osgi" / "client-extensions"
        for d in (self.ce_dir, self.osgi_cx):
            d.mkdir(parents=True)
        self.ports_resolved: list[str] = []

        def _record_port(_self, _info, ext_id, _meta, _root):
            self.ports_resolved.append(ext_id)
            return "40000"

        port_patch = patch.object(
            metadata, "_resolve_and_persist_cx_port", side_effect=_record_port
        )
        port_patch.start()
        self.addCleanup(port_patch.stop)

    def by_name(self, extensions):
        return {e["name"]: e for e in extensions}


class TheBuiltZipScan(_Project):
    """`_process_built_cx_zips`: what `ldm run` sees in a workspace build."""

    def setUp(self):
        super().setUp()
        build = self.base / "build"
        build.mkdir()
        for name, files in ALL.items():
            (build / f"{name}.zip").write_bytes(_zip_bytes(files))
        self.extensions: list[dict] = []
        metadata._process_built_cx_zips(
            _handler(),
            build,
            self.ce_dir,
            self.osgi_cx,
            self.root,
            None,
            {},
            self.extensions,
        )
        self.found = self.by_name(self.extensions)

    def test_a_theme_is_not_a_service(self):
        self.assertFalse(self.found[THEME]["is_service"])

    def test_a_custom_element_is_not_a_service(self):
        self.assertFalse(self.found[ELEMENT]["is_service"])

    def test_a_static_extension_is_not_expanded_as_a_build_context(self):
        self.assertFalse((self.ce_dir / THEME).exists())
        self.assertFalse((self.ce_dir / ELEMENT).exists())

    def test_a_static_extension_gets_no_host_port(self):
        self.assertNotIn("ecopulsetheme", self.ports_resolved)
        self.assertNotIn("ecopulsegridmap", self.ports_resolved)

    def test_its_zip_is_still_deployed(self):
        for name in (THEME, ELEMENT):
            self.assertTrue((self.osgi_cx / f"{name}.zip").is_file())

    def test_a_real_service_still_is_one(self):
        self.assertTrue(self.found[SERVICE]["is_service"])
        self.assertTrue((self.ce_dir / SERVICE / "Dockerfile").is_file())
        self.assertEqual(self.ports_resolved, ["ecopulseobjectaction"])


class TheExpandedFolderScan(_Project):
    """`_process_existing_cx_folders`: what `ldm link` leaves expanded."""

    def setUp(self):
        super().setUp()
        for name, files in ALL.items():
            _write_folder(self.ce_dir / name, files)
        self.extensions: list[dict] = []
        metadata._process_existing_cx_folders(
            _handler(),
            self.ce_dir,
            self.osgi_cx,
            self.root,
            None,
            {},
            self.extensions,
            set(),
        )
        self.found = self.by_name(self.extensions)

    def test_a_theme_is_not_a_service(self):
        self.assertFalse(self.found[THEME]["is_service"])

    def test_a_custom_element_is_not_a_service(self):
        self.assertFalse(self.found[ELEMENT]["is_service"])

    def test_a_static_extension_gets_no_host_port(self):
        self.assertNotIn("host_port", self.found[THEME])
        self.assertEqual(self.ports_resolved, ["ecopulseobjectaction"])

    def test_a_real_service_still_is_one(self):
        self.assertTrue(self.found[SERVICE]["is_service"])
        self.assertEqual(self.found[SERVICE]["host_port"], "40000")


class TheStatelessPackage(_Project):
    """`_is_container_cx` via `write_stateless_payload`: what a package ships."""

    def setUp(self):
        super().setUp()
        for name, files in ALL.items():
            (self.osgi_cx / f"{name}.zip").write_bytes(_zip_bytes(files))
            _write_folder(self.ce_dir / name, files)
        dst = self.base / "files.tar.gz"
        archive.write_stateless_payload(self.root, dst)
        with tarfile.open(dst, "r:gz") as tar:
            self.shipped = {m.name for m in tar.getmembers() if m.isfile()}

    def test_a_static_extension_ships_its_descriptors_not_its_assets(self):
        self.assertIn(f"client-extensions/{THEME}/LCP.json", self.shipped)
        self.assertIn(
            f"client-extensions/{THEME}/{THEME}.client-extension-config.json",
            self.shipped,
        )
        self.assertNotIn(f"client-extensions/{THEME}/static/css/main.css", self.shipped)
        self.assertNotIn(f"client-extensions/{ELEMENT}/static/js/main.js", self.shipped)

    def test_a_slimmed_extension_carries_no_dockerfile(self):
        """An LDM that predates this fix keys on the Dockerfile alone, and
        `ldm run --samples` in v2.26.x copies the payload straight over a
        project. A Dockerfile left beside a folder that lost its `static/`
        would have it build `COPY static/ /public_html/` from nothing."""
        self.assertNotIn(f"client-extensions/{THEME}/Dockerfile", self.shipped)
        self.assertNotIn(f"client-extensions/{ELEMENT}/Dockerfile", self.shipped)

    def test_a_real_service_ships_its_whole_build_context(self):
        for name in ("Dockerfile", "LCP.json", "server.js", "package.json"):
            self.assertIn(f"client-extensions/{SERVICE}/{name}", self.shipped)


class TheDevMonitor(unittest.TestCase):
    """`ldm dev`: a rebuilt zip redeploys its own compose service only when
    there is one. A static extension has none any more, so naming it would be
    `docker compose up -d <a service that does not exist>`."""

    def _deploy_calls_for(self, name, files):
        from ldm_core.workspace import monitor

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        source = base / "workspace"
        (source / "client-extensions").mkdir(parents=True)
        paths = {"root": base, "deploy": base / "deploy", "ce_dir": base / "ce"}
        _write_folder(paths["ce_dir"] / name, files)

        handlers = []

        class _Observer:
            def __init__(self, *_a, **_k):
                pass

            def schedule(self, handler, _path, recursive=False):
                handlers.append(handler)

            def start(self):
                pass

            def stop(self):
                pass

            def join(self, *_a, **_k):
                pass

        manager_self = MagicMock()
        manager_self.manager.args.delay = 0.1
        manager_self.manager.verbose = False
        manager_self.manager.setup_paths.return_value = paths
        manager_self.manager.read_meta.return_value = {}
        fake_time = MagicMock()
        fake_time.sleep.side_effect = KeyboardInterrupt()
        with (
            patch("watchdog.observers.Observer", _Observer),
            patch("watchdog.observers.polling.PollingObserverVFS", _Observer),
            patch.object(monitor, "UI", MagicMock()),
            patch.object(monitor, "time", fake_time),
            patch.object(monitor, "platform") as fake_platform,
        ):
            fake_platform.system.return_value = "Linux"
            monitor.cmd_monitor(manager_self, source_path=str(source), project_id="p")
            handler = handlers[0]
            handler.pending_files = {
                source / "client-extensions" / name / "dist" / f"{name}.zip"
            }
            handler._process_pending()
        # The handler is built with the workspace handler itself as `manager`.
        return manager_self.runtime.cmd_deploy.call_args_list

    def test_a_rebuilt_theme_does_not_target_a_service(self):
        calls = self._deploy_calls_for(THEME, THEME_FILES)
        self.assertEqual([c.kwargs for c in calls], [{}])

    def test_a_rebuilt_service_targets_its_own(self):
        calls = self._deploy_calls_for(SERVICE, SERVICE_FILES)
        self.assertEqual([c.kwargs for c in calls], [{"service": SERVICE}])


class TheMissingIdRefusal(unittest.TestCase):
    """`cx_service_missing_lcp_id` (LDM-#1962) asks the same question first:
    only a container needs an LCP.json id."""

    def _check(self, files):
        from ldm_core.workspace.site_initializers import cx_service_missing_lcp_id

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "cx.zip"
        path.write_bytes(_zip_bytes(files))
        return cx_service_missing_lcp_id(path)

    def _without_id(self, files):
        lcp = json.loads(files["LCP.json"])
        del lcp["id"]
        return dict(files, **{"LCP.json": json.dumps(lcp)})

    def test_a_static_extension_without_an_id_is_not_refused(self):
        self.assertFalse(self._check(self._without_id(THEME_FILES)))

    def test_a_service_without_an_id_still_is(self):
        self.assertTrue(self._check(self._without_id(SERVICE_FILES)))


class ThePredicate(unittest.TestCase):
    """The cases the corpus does not reach by itself."""

    def members(self, files):
        return {k: (lambda v=v: v) for k, v in files.items()}

    def test_a_declared_port_keeps_a_frontend_type_a_service(self):
        lcp = json.loads(ELEMENT_FILES["LCP.json"])
        lcp["ports"] = [{"external": True, "port": 3005}]
        del lcp["loadBalancer"]["cdn"]
        files = dict(ELEMENT_FILES, **{"LCP.json": json.dumps(lcp)})
        self.assertTrue(metadata.cx_is_service(self.members(files)))

    def test_a_static_type_without_the_cdn_flag_is_static(self):
        lcp = json.loads(THEME_FILES["LCP.json"])
        del lcp["loadBalancer"]["cdn"]
        files = dict(THEME_FILES, **{"LCP.json": json.dumps(lcp)})
        self.assertFalse(metadata.cx_is_service(self.members(files)))

    def test_a_static_type_in_client_extension_yaml_is_static(self):
        files = {
            "Dockerfile": CADDY_DOCKERFILE,
            "LCP.json": json.dumps({"id": "ecobrand", "kind": "Deployment"}),
            "client-extension.yaml": "eco-brand:\n  type: themeCSS\n",
        }
        self.assertFalse(metadata.cx_is_service(self.members(files)))

    def test_a_job_is_not_a_service(self):
        files = {"Dockerfile": "FROM liferay/batch\n", "LCP.json": '{"kind": "job"}'}
        self.assertFalse(metadata.cx_is_service(self.members(files)))

    def test_an_unreadable_lcp_json_is_a_service(self):
        files = {"Dockerfile": "FROM alpine\n", "LCP.json": "{not json"}
        self.assertTrue(metadata.cx_is_service(self.members(files)))

    def test_no_dockerfile_is_never_a_service(self):
        files = dict(SERVICE_FILES)
        del files["Dockerfile"]
        self.assertFalse(metadata.cx_is_service(self.members(files)))


if __name__ == "__main__":
    unittest.main()
