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
    """Both containers see the same host directory -- at the path each expects.

    Liferay WRITES its config trees to /opt/liferay/routes (measured, with a
    deployed oAuthApplicationHeadlessServer extension). A client extension
    reads them from the same host tree, but mounted at
    /etc/liferay/lxc/dxp-metadata, which is what its own image declares.

    This class originally asserted the extension mounted Liferay's path. That
    was wrong and it shipped to master -- see
    TestTheMetadataMountDoesNotShadowTheApplication. What is shared is the host
    DIRECTORY, not the container path.
    """

    def test_the_extension_reads_the_tree_liferay_writes(self):
        """Same host directory, so what Liferay writes is what the extension
        reads -- which is the whole point of the shared volume."""
        cx = next(v for v in _services()["volumes"] if "dxp-metadata" in v)
        liferay = next(
            v for v in _liferay_service()["volumes"] if ":/opt/liferay/routes" in v
        )
        self.assertTrue(
            cx.split(":")[0].startswith(liferay.split(":")[0]),
            f"the extension reads a different host tree from the one Liferay "
            f"writes: {cx} vs {liferay}",
        )

    def test_it_is_not_mounted_at_workspace_routes(self):
        """The original regression: /workspace/routes is a path nothing writes
        to, so the tree was always empty (LDM-#1911)."""
        self.assertFalse(
            [v for v in _services()["volumes"] if "/workspace/routes" in v]
        )


class TestTheExtensionWaitsForLiferayToBeServing(unittest.TestCase):
    """LDM-#1955: this had no pytest coverage at all, only E2E.

    `depends_on: {liferay: {condition: service_healthy}}` is a literal dict in
    `_build_extensions_services`. The custom-container counterpart of exactly
    this behaviour IS unit-tested (`test_shared_routes_space.py`), but the
    client-extension side was asserted only by
    `scripts/verify_e2e_refactor.{sh,ps1}` -- which run in `release-e2e.yml`
    and `scheduled-verification.yml`, NOT on every PR. A refactor dropping the
    key would pass the whole suite and surface only on the next scheduled run,
    by which point it can already be inside a pre-release.

    `service_healthy` rather than `service_started` is the substance:
    the liferay/dxp image's own HEALTHCHECK curls /c/portal/layout, so it means
    "serving pages", not "process started". The tree the extension mounts is
    written by Liferay at boot, so starting early means reading an empty
    directory (LDM-#1928).
    """

    def test_the_extension_declares_a_dependency_on_liferay(self):
        self.assertIn(
            "liferay",
            _services().get("depends_on") or {},
            "the extension starts concurrently with a boot that takes minutes "
            "and reads an empty config tree (LDM-#1928)",
        )

    def test_it_waits_for_healthy_not_merely_started(self):
        cond = (_services()["depends_on"]["liferay"] or {}).get("condition")
        self.assertEqual(
            "service_healthy",
            cond,
            "'started' is not 'serving' -- the config tree is written during "
            "boot, so service_started still reads an empty directory",
        )


class TestTheMetadataMountDoesNotShadowTheApplication(unittest.TestCase):
    """LDM-#1911: the two containers do NOT share a filesystem convention.

    `/opt/liferay/routes` is Liferay's config tree -- it writes there, measured.
    But a client extension built on `liferay/node-runner` does
    `COPY . /opt/liferay`, so for IT that path is the application's own route
    handlers. A live deployment had 16 `.cjs` files there.

    LDM-#1918 restored the pre-refactor mount on both sides without asking
    whether it had ever been right for this one. Mounting over it shadows the
    app and the container does not start -- a silent no-op became a broken
    container. Caught by the consumer, before release.
    """

    def test_the_extension_does_not_mount_over_opt_liferay_routes(self):
        for v in _services()["volumes"]:
            self.assertFalse(
                v.endswith(":/opt/liferay/routes") or ":/opt/liferay/routes:" in v,
                f"this shadows the extension's own application code: {v}",
            )

    def test_the_metadata_lands_where_the_image_declares(self):
        """The image sets LIFERAY_ROUTES_DXP=/etc/liferay/lxc/dxp-metadata, so
        the consumer needs no change to find it."""
        got = _services()["volumes"]
        self.assertTrue(
            [v for v in got if ":/etc/liferay/lxc/dxp-metadata" in v],
            f"the extension cannot resolve its config trees: {got}",
        )

    def test_it_is_the_dxp_subtree_not_the_whole_routes_directory(self):
        """Mounting all of `routes/` would expose other environments' trees."""
        mount = next(v for v in _services()["volumes"] if "dxp-metadata" in v)
        self.assertIn("routes/default/dxp:", mount)

    def test_liferay_still_mounts_the_tree_it_writes_to(self):
        """The other half must not regress while fixing this one."""
        got = _liferay_service()["volumes"]
        self.assertTrue([v for v in got if ":/opt/liferay/routes" in v], got)


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
    """Unmounted, anything dropped in these directories is silently ignored --
    and the directory's existence, where it exists, is what makes that silence
    misleading.

    LDM-#1917: this docstring used to say "both directories are created in
    every project by `setup_paths`". `setup_paths` builds a dict of paths and
    creates nothing, and `marketplace` in particular has had no Python creator
    since `64c75e9f` unwired `migrate_layout`. The mount asserted below is
    therefore not guaranteed a host directory -- which is a second defect, not
    a reason to drop this assertion."""

    def test_marketplace_is_mounted(self):
        got = _liferay_service()["volumes"]
        self.assertTrue(
            # `in`, not `endswith`: Linux appends an SELinux `:z` label, so the
            # mount reads `...:/opt/liferay/osgi/marketplace:z` there and
            # `endswith` passes on macOS and fails in CI. test_composer.py
            # already carries a comment about exactly this and I wrote the
            # fragile form anyway.
            [v for v in got if ":/opt/liferay/osgi/marketplace" in v],
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
