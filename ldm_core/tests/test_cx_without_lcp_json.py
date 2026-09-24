"""LDM-#1959: a client extension that declares no `LCP.json`.

`is_service` requires a **Dockerfile**, not an `LCP.json`, so an extension
carrying only `client-extension.yaml` and a `Dockerfile` is a valid shape --
and it is exactly the shape the E2E fixture uses. Two defects made it fatal:

1. `_scan_extension_metadata` initialises `id` to `None` and only
   `_parse_lcp_json` ever sets it. Both scan sites wrote
   `"id": ext_info.get("id") or item.name` and then unpacked `**ext_info`
   **after** it, so the fallback was overwritten with `None` again. That
   `None` reached `get_service_targeted_env(ext_id, ...)`, which does
   `target_id.upper()`.

2. `_inject_liferay_extensions_routes` read
   `ext.get("loadBalancer", {}).get(...)`. The key EXISTS with value `None`,
   and `dict.get`'s default only applies when the key is ABSENT.

Either one raised inside `write_docker_compose`. `PipelineStage` catches it,
rolls back, and the caller exits **0** -- so `ldm run` reported success having
written no compose file at all, and every client extension silently vanished.

These tests drive the REAL scan against a real directory. Every pre-existing
test in this area hand-builds an `ext` dict carrying an `id`, which is why
none of them could see it.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetNode
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.workspace import WorkspaceService

HOST = "example.test"


def _project(with_lcp_id=None):
    """A project whose client extension declares no LCP.json unless asked."""
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    ext = root / "client-extensions" / "synthetic-svc"
    ext.mkdir(parents=True)
    (ext / "client-extension.yaml").write_text(
        "synthetic-svc:\n"
        "    .serviceAddress: synthetic-svc:8080\n"
        "    name: Synthetic CX Service\n"
        "    type: microservice\n"
    )
    # The Dockerfile is what makes it a service. Note there is deliberately
    # no LCP.json unless the test asks for one.
    (ext / "Dockerfile").write_text('FROM alpine\nCMD ["sleep", "3600"]\n')
    if with_lcp_id is not None:
        (ext / "LCP.json").write_text(json.dumps({"id": with_lcp_id}))
    (root / "osgi" / "client-extensions").mkdir(parents=True)
    return tmp, root


def _scan(root):
    mgr = MagicMock()
    mgr.read_meta.return_value = {}
    # `_parse_lcp_json` unpacks `validate_lcp_json` into three values and
    # swallows any exception, so a bare MagicMock silently discards the whole
    # LCP.json -- including the declared `id`. Give it a real 3-tuple, or the
    # "a declared id still wins" control below passes for the wrong reason.
    mgr.diagnostics.validate_lcp_json.return_value = ("ok", True, [])
    ws = WorkspaceService(mgr)
    mgr.workspace = ws
    return mgr, ws.scan_client_extensions(
        root, root / "osgi" / "client-extensions", None, host_name=HOST
    )


class TestAnExtensionWithoutLcpJsonGetsAnId(unittest.TestCase):
    def test_the_id_falls_back_to_the_directory_name(self):
        tmp, root = _project()
        with tmp:
            _, exts = _scan(root)
            self.assertTrue(exts, "the extension was not discovered at all")
            self.assertEqual(
                "synthetic-svc",
                exts[0]["id"],
                "id was clobbered back to None by the `**ext_info` unpack "
                "(LDM-#1959) -- `svc_id` would then be 'proj-None' and "
                "get_service_targeted_env would raise on target_id.upper()",
            )

    def test_a_declared_id_still_wins(self):
        """The control. The fallback must not override a real declaration."""
        tmp, root = _project(with_lcp_id="declared-id")
        with tmp:
            _, exts = _scan(root)
            self.assertEqual("declared-id", exts[0]["id"])


class TestComposeIsGeneratedForIt(unittest.TestCase):
    """The user-visible half: `ldm run` wrote no compose file and exited 0."""

    def _services(self, root, mgr, exts):
        mgr.workspace.scan_client_extensions = MagicMock(return_value=exts)
        paths = {
            "root": root,
            "cx": root / "osgi" / "client-extensions",
            "ce_dir": root / "client-extensions",
        }
        node = TargetNode(name="local", host="localhost", is_default=True)
        with patch("ldm_core.config.get_active_target", return_value=node):
            return ComposerService(mgr)._build_extensions_services(
                paths, {}, HOST, "proj", False
            )

    def test_a_service_is_built_and_is_not_named_none(self):
        tmp, root = _project()
        with tmp:
            mgr, exts = _scan(root)
            services = self._services(root, mgr, exts)
            self.assertIn("proj-synthetic-svc", services)
            self.assertFalse(
                [k for k in services if "None" in k],
                f"a service was named after a None id: {list(services)}",
            )


class TestTheLiferayRoutesEnvDoesNotCrash(unittest.TestCase):
    """LDM-#1959's second defect, in `_inject_liferay_extensions_routes`.

    `ext.get("loadBalancer", {})` returns None, because the key exists with
    value None -- `dict.get`'s default only applies when the key is ABSENT.
    """

    def test_a_none_load_balancer_is_tolerated(self):
        liferay_env: list[str] = []
        ext = {
            "id": "synthetic-svc",
            "deploy": True,
            "is_service": True,
            "ports": [],
            "loadBalancer": None,
        }
        mgr = MagicMock()
        mgr.workspace.scan_client_extensions = MagicMock(return_value=[ext])
        ComposerService(mgr)._inject_liferay_extensions_routes(
            {"root": Path("/tmp/p")}, {}, "proj", liferay_env
        )
        self.assertTrue(
            [
                e
                for e in liferay_env
                if e.startswith("LIFERAY_ROUTES_CLIENT_EXTENSION_")
            ],
            "the routes env var was not emitted",
        )
        self.assertIn("8080", liferay_env[0], "the default targetPort was not used")


class TestTheTargetedEnvLookupToleratesNoId(unittest.TestCase):
    """The defensive half. Nothing can be addressed to a service with no id,
    so there is nothing to deliver -- and nothing to fail on."""

    def test_no_target_id_returns_nothing(self):
        from ldm_core.workspace.metadata import get_service_targeted_env

        self.assertEqual({}, get_service_targeted_env(MagicMock(), None))
        self.assertEqual({}, get_service_targeted_env(MagicMock(), ""))


if __name__ == "__main__":
    unittest.main()
