"""Rootless host names, SSL defaults and lazy mkcert (LDM-#2513, #2514, #2515).

Each class drives a real call site -- the composer, the run and import stages,
the hosts-file flow, the SSL setup, doctor -- rather than only the classifier
in `ldm_core.hostnames`, so a call site reverted to the old `== "localhost"`
comparison fails here even while the helper stays correct.
"""

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerService, resolve_access_url
from ldm_core.handlers.infra import InfraService
from ldm_core.hostnames import (
    is_loopback_name,
    localhost_name_for,
    normalize_host_name,
    routes_http_via_proxy,
)
from ldm_core.tests.tmproot import TEST_TMP_ROOT


class TheClassifier(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(normalize_host_name("localhost"), "localhost")
        self.assertEqual(normalize_host_name("myproj"), "myproj.localhost")
        self.assertEqual(normalize_host_name("MyProj"), "myproj.localhost")
        self.assertEqual(normalize_host_name("x.localhost"), "x.localhost")
        self.assertEqual(normalize_host_name("acme.demo"), "acme.demo")
        self.assertIsNone(normalize_host_name(None))
        with self.assertRaises(ValueError):
            normalize_host_name("bad_name!")

    def test_kinds(self):
        self.assertTrue(is_loopback_name("localhost"))
        self.assertTrue(is_loopback_name("p.localhost"))
        self.assertTrue(is_loopback_name("cx.p.localhost"))
        self.assertFalse(is_loopback_name("p.local"))
        self.assertFalse(is_loopback_name("notlocalhost"))
        self.assertTrue(routes_http_via_proxy("p.localhost", False))
        self.assertFalse(routes_http_via_proxy("p.localhost", True))
        self.assertFalse(routes_http_via_proxy("localhost", False))
        self.assertFalse(routes_http_via_proxy("acme.demo", False))

    def test_project_label(self):
        self.assertEqual(localhost_name_for("My_Proj"), "my-proj.localhost")


def _manager(ssl=None):
    manager = MagicMock()
    manager.args = Namespace(
        database_mode=None,
        search_mode=None,
        ssl=ssl,
        lean=False,
        tunnel_managed_cors=False,
    )
    manager.verbose = False
    manager.non_interactive = True
    manager.config.get_global_config.return_value = {}
    manager.share.get_known_tunnel_base_domains.return_value = []
    manager.get_resolved_ip.return_value = "127.0.0.1"
    manager.parse_version.return_value = (2024, 1, 0)
    return manager


class TheSslDefault(unittest.TestCase):
    """LDM-#2514: auto-SSL for custom domains only."""

    def test_localhost_names_stay_http_unless_asked(self):
        composer = ComposerService(_manager())
        self.assertFalse(composer._is_ssl_active("p.localhost", {}))
        self.assertFalse(composer._is_ssl_active("localhost", {}))
        self.assertTrue(composer._is_ssl_active("acme.demo", {}))

    def test_flag_and_manifest_still_decide(self):
        self.assertTrue(
            ComposerService(_manager(ssl=True))._is_ssl_active("p.localhost", {})
        )
        self.assertFalse(
            ComposerService(_manager(ssl=False))._is_ssl_active("acme.demo", {})
        )
        composer = ComposerService(_manager())
        self.assertTrue(composer._is_ssl_active("p.localhost", {"ssl": "true"}))


class TheComposeRouting(unittest.TestCase):
    """LDM-#2514: the plain-HTTP router exists for `*.localhost` only."""

    def setUp(self):
        from ldm_core.config import TargetNode

        p = patch(
            "ldm_core.config.get_active_target",
            return_value=TargetNode(name="local", host="localhost", is_default=True),
        )
        p.start()
        self.addCleanup(p.stop)

    def _liferay(self, host_name, ssl=None, meta_ssl=None):
        composer = ComposerService(_manager(ssl=ssl))
        root = Path(TEST_TMP_ROOT) / "rootless-proj"
        paths = {
            key: root / key
            for key in [
                "deploy",
                "files",
                "data",
                "configs",
                "modules",
                "cx",
                "scripts",
                "state",
                "logs",
                "portal_log4j",
            ]
        }
        paths["root"] = root
        paths["compose"] = root / "docker-compose.yml"
        meta = {"container_name": "proj", "host_name": host_name, "tag": "2026.q1.4"}
        if meta_ssl is not None:
            meta["ssl"] = meta_ssl
        captured: dict = {}
        with (
            patch(
                "ldm_core.handlers.composer.dict_to_yaml",
                side_effect=lambda d: captured.setdefault("compose", d) and "yaml",
            ),
            patch("ldm_core.utils.safe_write_text"),
            patch.object(composer, "_build_db_service", return_value=None),
            patch.object(composer, "_build_search_service", return_value=None),
            patch.object(composer, "_build_extensions_services", return_value={}),
            patch.object(composer, "_scaffold_routes_tree"),
        ):
            composer.write_docker_compose(paths, meta)
        return captured["compose"]["services"]["liferay"]

    def test_localhost_name_gets_an_http_router_and_keeps_its_port(self):
        service = self._liferay("p.localhost")
        labels = service["labels"]
        self.assertIn("traefik.http.routers.proj-main.entrypoints=web", labels)
        self.assertIn("traefik.http.routers.proj-main.rule=Host(`p.localhost`)", labels)
        self.assertFalse(any("tls=true" in label for label in labels), labels)
        self.assertTrue(service["ports"], "the direct port is the fallback")

    def test_localhost_name_with_ssl_uses_websecure(self):
        labels = self._liferay("p.localhost", ssl=True)["labels"]
        self.assertIn("traefik.http.routers.proj-main.entrypoints=websecure", labels)
        self.assertNotIn("traefik.http.routers.proj-main.entrypoints=web", labels)

    def test_custom_domain_without_ssl_gets_no_router(self):
        labels = self._liferay("acme.demo", meta_ssl="false")["labels"]
        self.assertFalse(any(lbl.startswith("traefik.") for lbl in labels), labels)

    def test_basic_localhost_gets_no_router(self):
        labels = self._liferay("localhost")["labels"]
        self.assertFalse(any(lbl.startswith("traefik.") for lbl in labels), labels)


class TheAccessUrl(unittest.TestCase):
    """LDM-#2514: port-free only when the proxy was observed on port 80."""

    META: ClassVar[dict] = {"port": 8080, "http_proxy_port": 80}

    def test_port_free_when_the_proxy_holds_80(self):
        self.assertEqual(
            resolve_access_url("p.localhost", self.META, False), "http://p.localhost"
        )

    def test_falls_back_when_the_proxy_is_elsewhere(self):
        self.assertEqual(
            resolve_access_url(
                "p.localhost", self.META, False, proxy_ports={"http": 8081}
            ),
            "http://p.localhost:8080",
        )
        self.assertEqual(
            resolve_access_url("p.localhost", {"port": 8080}, False),
            "http://p.localhost:8080",
        )

    def test_an_override_address_takes_the_direct_port(self):
        self.assertEqual(
            resolve_access_url(
                "p.localhost", self.META, False, host_override="10.0.0.5"
            ),
            "http://10.0.0.5:8080",
        )

    def test_custom_domain_and_localhost_are_unchanged(self):
        self.assertEqual(
            resolve_access_url("acme.demo", self.META, False), "http://acme.demo:8080"
        )
        self.assertEqual(
            resolve_access_url("localhost", self.META, False), "http://localhost:8080"
        )


class _HostsManager(BaseHandler):
    """A BaseHandler with only what the hosts-file flow touches."""

    def __init__(self, root):
        self.root = root
        self.non_interactive = False
        self.verbose = False
        self.args = MagicMock()

    def setup_paths(self, _root):
        return {"root": self.root, "cx": self.root / "cx", "ce_dir": self.root / "ce"}

    def detect_project_path(self, *_a, **_k):
        return self.root

    def read_meta(self, *_a, **_k):
        return {"host_name": "p.localhost"}


class NoHostsPromptForLocalhostNames(unittest.TestCase):
    """LDM-#2513: only custom domains reach the sudo prompt."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.handler = _HostsManager(self.root)
        unresolvable = patch("socket.gethostbyname", side_effect=OSError)
        unresolvable.start()
        self.addCleanup(unresolvable.stop)

    def test_localhost_name_never_asks(self):
        with (
            patch("ldm_core.handlers.base.UI.confirm") as confirm,
            patch.object(type(self.handler), "_apply_hosts_fix") as fix,
        ):
            self.assertTrue(
                self.handler.ensure_hostnames_resolve(self.root, "p.localhost")
            )
        confirm.assert_not_called()
        fix.assert_not_called()

    def test_custom_domain_still_asks(self):
        import socket

        with (
            patch("socket.gethostbyname", side_effect=socket.gaierror),
            patch("ldm_core.handlers.base.UI.confirm", return_value=True) as confirm,
            patch.object(
                type(self.handler), "_apply_hosts_fix", return_value=True
            ) as fix,
        ):
            self.handler.ensure_hostnames_resolve(self.root, "acme.demo")
        confirm.assert_called_once()
        fix.assert_called_once()

    def test_fix_hosts_writes_nothing_for_localhost_names(self):
        with patch.object(type(self.handler), "run_command") as run:
            self.assertTrue(
                self.handler._apply_hosts_fix(["p.localhost", "cx.p.localhost"])
            )
        run.assert_not_called()

    def test_clean_hosts_never_elevates_for_localhost_names(self):
        with (
            patch("ldm_core.handlers.base.subprocess.run") as run,
            patch.object(type(self.handler), "run_command") as run_command,
        ):
            self.assertTrue(
                self.handler._remove_hosts_entries(hostnames=["p.localhost"])
            )
        run.assert_not_called()
        run_command.assert_not_called()

    def test_project_dns_is_clean(self):
        self.assertEqual(self.handler.validate_project_dns("p"), (True, [], []))

    def test_resolution_is_answered_without_the_os(self):
        self.assertEqual(self.handler.get_resolved_ip("cx.p.localhost"), "127.0.0.1")
        self.assertTrue(self.handler.check_hostname("p.localhost", silent=True))


class TheRunStageProvisionsTheProxy(unittest.TestCase):
    """LDM-#2514: `ldm run` starts the proxy for a non-SSL `*.localhost`."""

    def _run(self, host_name, ssl, proxy_ports):
        from ldm_core.pipelines.run import ComposerStage, RunPipelineContext

        context = RunPipelineContext(MagicMock())
        manager = context.manager
        manager.non_interactive = True
        manager.args.no_up = False
        manager.args.search = False
        manager.infra.get_proxy_ports.return_value = proxy_ports
        context.set("project_id", "test-project")
        context.set("is_new_project", False)
        context.set("dry_run", True)
        context.set("host_name", host_name)
        meta = {"container_name": "test-project", "ssl": ssl, "port": 8080}
        context.set("project_meta", meta)
        root_mock = MagicMock()
        root_mock.__truediv__.return_value.exists.return_value = False
        context.set("paths", {"root": root_mock, "configs": MagicMock()})
        context.set("infra_ports", {})
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("ldm_core.utils.resolve_database_mode", return_value="isolated"),
            patch("ldm_core.pipelines.run.UI.warning") as warning,
        ):
            ComposerStage().execute(context)
        return manager, context.get("project_meta"), warning

    def test_localhost_name_starts_the_proxy_and_records_port_80(self):
        manager, meta, warning = self._run(
            "p.localhost", "false", {"http": 80, "https": 443, "admin": 18080}
        )
        manager.infra.setup_infrastructure.assert_called_once()
        self.assertIs(
            manager.infra.setup_infrastructure.call_args.kwargs.get("use_proxy"), True
        )
        self.assertEqual(meta.get("http_proxy_port"), 80)
        self.assertEqual(
            [c for c in warning.call_args_list if "p.localhost" in str(c)], []
        )

    def test_port_80_taken_is_reported(self):
        _manager_, meta, warning = self._run(
            "p.localhost", "false", {"http": 8081, "https": 443, "admin": 18080}
        )
        self.assertEqual(meta.get("http_proxy_port"), 8081)
        said = " ".join(str(c.args[0]) for c in warning.call_args_list)
        self.assertIn("http://p.localhost:8080", said)

    def test_custom_domain_without_ssl_does_not_start_it(self):
        manager, meta, _w = self._run("acme.demo", "false", None)
        manager.infra.setup_infrastructure.assert_not_called()
        self.assertNotIn("http_proxy_port", meta)


class TheHostNameReachesTheRun(unittest.TestCase):
    """LDM-#2513: `--host-name myproj` runs as `myproj.localhost`."""

    def _execute(self, flag):
        from ldm_core.pipelines.run import ConfigResolutionStage, RunPipelineContext

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            root.mkdir()
            manager = MagicMock()
            manager.non_interactive = True
            manager.verbose = False
            manager.args.sidecar = False
            manager.args.database_mode = None
            manager.args.search_mode = None
            manager.args.reindex = False
            manager.args.container_hostname = None
            manager.args.host_name = flag
            manager.defaults.get.side_effect = lambda _k, default=None: default
            context = RunPipelineContext(
                manager, project_id="proj", paths={"root": root}
            )
            context.set(
                "project_meta", {"container_name": "proj", "tag": "2026.q1.12-lts"}
            )
            ConfigResolutionStage().execute(context)
            return context.get("host_name")

    def test_bare_name_is_normalised(self):
        self.assertEqual(self._execute("myproj"), "myproj.localhost")

    def test_custom_domain_is_used_as_given(self):
        self.assertEqual(self._execute("acme.demo"), "acme.demo")


class TheImportDefaults(unittest.TestCase):
    """LDM-#2513/#2514 at the import call site."""

    def _import(self, host_name):
        from ldm_core.pipelines.import_pipeline import (
            ImportPipelineContext,
            ProjectSetupStage,
        )

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            manager = MagicMock()
            manager.setup_paths.return_value = {"root": tmp_path / "project_root"}
            manager.read_meta.return_value = {}
            manager.args = MagicMock()
            manager.args.project = "test_project"
            manager.args.host_name = host_name
            manager.args.ssl = None
            manager.args.port = None
            manager.non_interactive = True
            context = ImportPipelineContext(manager=manager, source_path=str(tmp_path))
            context.set("source_resolved", tmp_path)
            context.set("backup_dir", None)
            (tmp_path / ".ldmrc").write_text(json.dumps({"defaults": {}}))
            ProjectSetupStage().execute(context)
            return manager.write_meta.call_args[0][1]

    def test_localhost_name_is_http(self):
        meta = self._import("myproj")
        self.assertEqual(meta["host_name"], "myproj.localhost")
        self.assertEqual(meta["ssl"], "false")

    def test_custom_domain_keeps_auto_ssl(self):
        meta = self._import("acme.demo")
        self.assertEqual(meta["host_name"], "acme.demo")
        self.assertEqual(meta["ssl"], "true")


class TheLazyLocalCa(unittest.TestCase):
    """LDM-#2515: mkcert is set up when a certificate is first needed."""

    def _infra(self, non_interactive):
        manager = MagicMock()
        manager.non_interactive = non_interactive
        return InfraService(manager)

    def test_non_interactive_missing_mkcert_is_one_privileged_step(self):
        infra = self._infra(non_interactive=True)
        with (
            patch("ldm_core.handlers.infra.shutil.which", return_value=None),
            patch.object(InfraService, "_privileged_step_required") as step,
            patch("ldm_core.handlers.infra.UI.confirm") as confirm,
        ):
            self.assertFalse(infra.ensure_local_ca())
        confirm.assert_not_called()
        infra.manager.run_command.assert_not_called()
        step.assert_called_once()
        self.assertIn("mkcert -install", step.call_args.args[0])

    def test_interactive_missing_ca_offers_mkcert_install(self):
        infra = self._infra(non_interactive=False)
        ready = iter([False, True])
        with (
            patch("ldm_core.handlers.infra.shutil.which", return_value="/x/mkcert"),
            patch.object(
                InfraService, "_mkcert_ca_ready", side_effect=lambda: next(ready)
            ),
            patch("ldm_core.handlers.infra.UI.confirm", return_value=True),
        ):
            self.assertTrue(infra.ensure_local_ca())
        infra.manager.run_command.assert_called_once_with(
            ["mkcert", "-install"], check=False, capture_output=False
        )

    def test_setup_ssl_asks_only_when_a_certificate_is_needed(self):
        infra = self._infra(non_interactive=True)
        with tempfile.TemporaryDirectory() as td:
            cert_dir = Path(td)
            with patch.object(
                InfraService, "ensure_local_ca", return_value=False
            ) as ensure:
                self.assertFalse(infra.setup_ssl(cert_dir, "p.localhost"))
            ensure.assert_called_once()

            (cert_dir / "p.localhost.pem").write_text("CERT")
            with (
                patch.object(InfraService, "ensure_local_ca") as ensure,
                patch(
                    "ldm_core.docker_service.DockerService.exists", return_value=False
                ),
                patch("time.sleep"),
            ):
                infra.setup_ssl(cert_dir, "p.localhost")
            ensure.assert_not_called()


class TheDoctorMkcertLine(unittest.TestCase):
    """LDM-#2515: neutral "not needed" while no project uses SSL."""

    def _mkcert_results(self, project_meta):
        from ldm_core.diagnostics.doctor import DoctorRunner
        from ldm_core.tests.test_diagnostics import MockDiagManager

        manager = MockDiagManager()
        manager.read_meta = lambda *_a, **_k: dict(project_meta)  # type: ignore[method-assign]
        runner = DoctorRunner(manager.diagnostics)
        runner.docker_version = None
        runner.project_paths = []
        with (
            patch(
                "ldm_core.diagnostics.doctor.check_mkcert",
                return_value=("Not installed", "warn", None),
            ),
            patch("ldm_core.diagnostics.doctor.run_command", return_value=""),
            patch("ldm_core.diagnostics.doctor.shutil.which", return_value=None),
        ):
            runner._check_global_config_and_network()
        return [r for r in runner.results if "mkcert" in r[0]]

    def test_not_needed_is_a_pass(self):
        results = self._mkcert_results({"ssl": "false"})
        self.assertTrue(results)
        for name, status, ok in results:
            self.assertIs(ok, True, (name, status))
            self.assertIn("not needed", status.lower())

    def test_an_ssl_project_keeps_the_warning(self):
        results = self._mkcert_results({"ssl": "true"})
        self.assertTrue(any(ok is not True for _n, _s, ok in results), results)


class TheDoctorSuggestsTheLocalhostForm(unittest.TestCase):
    """LDM-#2513: a stored bare host name is not rewritten; doctor suggests."""

    def _hints(self, host_name):
        from ldm_core.config import TargetNode
        from ldm_core.diagnostics.doctor import DoctorRunner
        from ldm_core.tests.test_diagnostics import MockDiagManager

        manager = MockDiagManager()
        manager.read_meta = lambda *_a, **_k: {  # type: ignore[method-assign]
            "tag": "2026.q1.4-lts",
            "host_name": host_name,
        }
        manager.validate_project_dns = MagicMock(  # type: ignore[method-assign]
            return_value=(False, [host_name], [])
        )
        runner = DoctorRunner(manager.diagnostics)
        runner.project_paths = [Path(TEST_TMP_ROOT) / "proj1"]
        with (
            patch(
                "ldm_core.docker_service.get_active_target",
                return_value=TargetNode(
                    name="local", host="localhost", is_default=True
                ),
            ),
            patch("ldm_core.diagnostics.doctor.run_command", return_value=""),
            patch(
                "ldm_core.diagnostics.doctor.validate_lcp_json",
                return_value=("Valid", True, []),
            ),
        ):
            runner._check_project_specific()
        return " ".join(h["text"] for h in runner.hints)

    def test_bare_name_gets_the_suggestion(self):
        self.assertIn("--host-name myproj.localhost", self._hints("myproj"))

    def test_a_domain_does_not(self):
        self.assertNotIn(".localhost", self._hints("acme.demo"))


class TheProxyStartsWithoutSsl(unittest.TestCase):
    """LDM-#2514: `use_proxy` reaches `compose up` with no certificate."""

    def _calls(self, use_proxy):
        from ldm_core.tests.test_infra_compose_project import _Manager

        manager = _Manager("ldm-infra")
        infra = InfraService(manager)
        with tempfile.TemporaryDirectory() as td:
            with (
                patch("ldm_core.docker_service.run_command", return_value=""),
                patch(
                    "ldm_core.docker_service.DockerService.get_compose_cmd_prefix",
                    return_value=["docker", "compose"],
                ),
                patch(
                    "ldm_core.docker_service.DockerService.is_running",
                    return_value=False,
                ),
                patch("ldm_core.handlers.infra.get_actual_home", return_value=Path(td)),
                patch.object(InfraService, "_ensure_docker_proxy"),
                patch.object(InfraService, "_ensure_network"),
                patch.object(InfraService, "_migrate_legacy_infra_project"),
            ):
                infra.setup_infrastructure(
                    "127.0.0.1", 443, use_ssl=False, quiet=True, use_proxy=use_proxy
                )
            certs_created = (Path(td) / "liferay-docker-certs").is_dir()
        return [c for c in manager.calls if "up" in c], certs_created

    def test_use_proxy_brings_the_proxy_up(self):
        ups, certs_created = self._calls(use_proxy=True)
        self.assertEqual(len(ups), 1, ups)
        # Otherwise Docker would create the bind-mount source, root-owned.
        self.assertTrue(certs_created)

    def test_without_ssl_or_proxy_nothing_starts(self):
        ups, _ = self._calls(use_proxy=False)
        self.assertEqual(ups, [])


class TheLdmpHydrate(unittest.TestCase):
    """The `.ldmp` hydrate is the second import call site (LDM-#2513/#2514)."""

    def _hydrate(self, host_name):
        from ldm_core.tests.test_workspace import MockWorkspaceManager
        from ldm_core.workspace.importer import _hydrate_ldm_package_to_project

        manager = MockWorkspaceManager()
        manager.args.port = None
        manager.args.host_name = host_name
        manager.args.ssl = None
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td) / "aica"
            project_dir.mkdir()
            with patch.object(MockWorkspaceManager, "verify_runtime_environment"):
                _hydrate_ldm_package_to_project(
                    manager.workspace,
                    project_dir,
                    "aica",
                    {"tag": "2026.q3.0", "db_type": "postgresql"},
                    Path(td) / "extract",
                )
            return manager.read_meta(project_dir)

    def test_localhost_name_is_http(self):
        meta = self._hydrate("aica")
        self.assertEqual(meta["host_name"], "aica.localhost")
        self.assertEqual(meta["ssl"], "false")

    def test_custom_domain_keeps_auto_ssl(self):
        meta = self._hydrate("aica.demo")
        self.assertEqual(meta["ssl"], "true")


class TheSslRenewal(unittest.TestCase):
    """LDM-#2515: renewal never makes mkcert a need for a non-SSL project."""

    def _renew(self, meta):
        from ldm_core.runtime.orchestration import OrchestrationService

        manager = MagicMock()
        manager.detect_project_path.return_value = Path("/nonexistent/p")
        manager.read_meta.return_value = meta
        service = OrchestrationService.__new__(OrchestrationService)
        service.manager = manager
        with tempfile.TemporaryDirectory() as td:
            with patch(
                "ldm_core.runtime.orchestration.get_actual_home", return_value=Path(td)
            ):
                service.cmd_renew_ssl(project_id="p")
        return manager.infra.setup_ssl

    def test_non_ssl_localhost_name_is_skipped(self):
        self._renew({"host_name": "p.localhost", "ssl": "false"}).assert_not_called()

    def test_ssl_localhost_name_is_renewed(self):
        self._renew({"host_name": "p.localhost", "ssl": "true"}).assert_called_once()


if __name__ == "__main__":
    unittest.main()
