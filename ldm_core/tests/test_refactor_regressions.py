"""Behaviours lost in the stack.py -> composer.py refactor (LDM-#1918).

Seven working features were dropped and shipped broken for ~25 releases. None
had a test. Worse, the commit that half-restored the routes mount at the wrong
path (`5857d14f`) also wrote the test that pinned it there -- a test written
from the implementation rather than the requirement, green every run, actively
defending the regression.

So each test here states WHY the value must be what it is, not merely that it
is. Someone changing one has to argue with the reason, not just update a
string.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetNode
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.workspace import WorkspaceService
from ldm_core.tests.test_composer import MockComposerManager

EXT_ID = "probe-svc"
HOST = "myproj.test"


def _services(ext_overrides=None, env=None, meta=None):
    ext = {
        "id": EXT_ID,
        "deploy": True,
        "is_service": True,
        "path": "/tmp/ms",
        "env": {"OWN": "declared"},
    }
    ext.update(ext_overrides or {})
    with patch.dict(os.environ, env or {}, clear=True):
        mgr = MagicMock()
        mgr.workspace = WorkspaceService(mgr)
        mgr.workspace.scan_client_extensions = MagicMock(return_value=[ext])
        paths = {
            "root": Path("/tmp/p"),
            "cx": Path("/tmp/p/cx"),
            "ce_dir": Path("/tmp/p/ce"),
            "routes": Path("/tmp/p/routes"),
            "marketplace": Path("/tmp/p/osgi/marketplace"),
        }
        node = TargetNode(name="local", host="localhost", is_default=True)
        with patch("ldm_core.config.get_active_target", return_value=node):
            return ComposerService(mgr)._build_extensions_services(
                paths, meta or {}, HOST, "proj", False
            )[f"proj-{EXT_ID}"]


class TestTheRoutesVolumeIsShared(unittest.TestCase):
    """Liferay WRITES its config trees to /opt/liferay/routes -- measured, with
    a deployed oAuthApplicationHeadlessServer extension. A client extension
    reads them from the same host directory. Mount either side anywhere else
    and the channel silently carries nothing."""

    def test_the_extension_mounts_the_path_liferay_writes_to(self):
        got = _services()["volumes"]
        self.assertIn("/tmp/p/routes:/opt/liferay/routes", got)

    def test_it_is_not_mounted_at_workspace_routes(self):
        """The regression: /workspace/routes is a path Liferay never touches,
        so the tree was always empty (LDM-#1911)."""
        self.assertFalse(
            [v for v in _services()["volumes"] if "/workspace/routes" in v]
        )


class TestTheExtensionIsToldWhereLiferayIs(unittest.TestCase):
    """`lxcConfig.dxpMainDomain()` in the Liferay client-extension SDKs resolves
    `com.liferay.lxc.dxp.main.domain`. Without these an extension cannot find
    Liferay at all, which is what an external team reported (LDM-#1903)."""

    def test_the_main_domain_is_the_project_host(self):
        self.assertIn(f"LIFERAY_LXC_DXP_MAIN_DOMAIN={HOST}", _services()["environment"])

    def test_the_domains_list_is_set_too(self):
        self.assertIn(f"LIFERAY_LXC_DXP_DOMAINS={HOST}", _services()["environment"])

    def test_the_extension_can_override_them(self):
        """Its own descriptor wins -- LDM supplies a default, not a mandate."""
        got = _services({"env": {"LIFERAY_LXC_DXP_MAIN_DOMAIN": "chosen.test"}})
        self.assertIn("LIFERAY_LXC_DXP_MAIN_DOMAIN=chosen.test", got["environment"])


class TestTheExtensionCanResolveTheProjectHost(unittest.TestCase):
    def test_extra_hosts_maps_the_project_host_to_the_docker_host(self):
        """Without it an extension cannot reach Liferay when the project is
        served under a custom host name."""
        self.assertEqual([f"{HOST}:host-gateway"], _services()["extra_hosts"])


class TestADeclaredProbeBecomesAHealthcheck(unittest.TestCase):
    """`readinessProbe`/`livenessProbe` have never stopped being parsed into the
    extension's info. The consumer was lost twice -- `7711cff0` is titled
    "restore missing healthcheck mapper" -- so a declared probe was silently
    discarded."""

    def test_an_http_probe_becomes_a_curl_healthcheck(self):
        got = _services(
            {"readinessProbe": {"httpGet": {"path": "/health", "port": 3000}}}
        )
        self.assertEqual(
            ["CMD", "curl", "-f", "http://localhost:3000/health"],
            got["healthcheck"]["test"],
        )

    def test_probe_timings_are_carried_through(self):
        got = _services({"readinessProbe": {"interval": 15, "retries": 5}})
        self.assertEqual("15s", got["healthcheck"]["interval"])
        self.assertEqual(5, got["healthcheck"]["retries"])

    def test_a_liveness_probe_is_used_when_there_is_no_readiness_probe(self):
        got = _services({"livenessProbe": {"tcpSocket": {"port": 9000}}})
        self.assertEqual(
            ["CMD-SHELL", "nc -z localhost 9000"], got["healthcheck"]["test"]
        )

    def test_no_probe_means_no_healthcheck(self):
        self.assertNotIn("healthcheck", _services())


class TestTheExtensionsResourceLimitIsHonoured(unittest.TestCase):
    def test_declared_memory_becomes_a_deploy_limit(self):
        got = _services({"memory": 512})
        self.assertEqual("512m", got["deploy"]["resources"]["limits"]["memory"])

    def test_memory_and_replicas_coexist(self):
        """Both write to `deploy`; an earlier shape had one clobber the other."""
        got = _services({"memory": 256}, meta={f"scale_{EXT_ID}": "3"})
        self.assertEqual(3, got["deploy"]["replicas"])
        self.assertEqual("256m", got["deploy"]["resources"]["limits"]["memory"])


def _liferay_service():
    """The Liferay service as the composer actually builds it."""
    # MockComposerManager, not a bare MagicMock: the builder compares the
    # Liferay tag against a version tuple, and every MagicMock attribute is a
    # mock, so the comparison raises before any volume is built.
    mgr = MockComposerManager()
    mgr.workspace.scan_client_extensions = MagicMock(return_value=[])
    base = Path("/tmp/p")
    paths = {
        "root": base,
        "deploy": base / "deploy",
        "files": base / "files",
        "data": base / "data",
        "configs": base / "osgi/configs",
        "modules": base / "osgi/modules",
        "marketplace": base / "osgi/marketplace",
        "cx": base / "osgi/client-extensions",
        "scripts": base / "scripts",
        "state": base / "osgi/state",
        "logs": base / "logs",
        "routes": base / "routes",
        "log4j": base / "osgi/log4j",
        "portal_log4j": base / "osgi/log4j",
    }
    node = TargetNode(name="local", host="localhost", is_default=True)
    with patch("ldm_core.config.get_active_target", return_value=node):
        return ComposerService(mgr)._build_liferay_service(
            paths,
            {"tag": "2026.q1.7-lts", "container_name": "proj"},
            "localhost",
            "proj",
            False,
            None,
        )


class TestTheLiferayServiceMountsWhatTheProjectOffers(unittest.TestCase):
    """Both directories are created in every project by `setup_paths`. Unmounted,
    anything dropped in them is silently ignored -- and the directory's very
    existence is what makes that silence misleading."""

    def test_marketplace_is_mounted(self):
        got = _liferay_service()["volumes"]
        self.assertTrue(
            [v for v in got if v.endswith(":/opt/liferay/osgi/marketplace")],
            f"osgi/marketplace is not mounted; an .lpkg there goes nowhere. Got: {got}",
        )

    def test_routes_is_mounted_where_liferay_writes(self):
        """The Liferay half of the shared volume -- it WRITES here."""
        got = _liferay_service()["volumes"]
        self.assertTrue(
            [v for v in got if ":/opt/liferay/routes" in v],
            f"routes is not mounted at the path Liferay writes to. Got: {got}",
        )


if __name__ == "__main__":
    unittest.main()
