import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from ldm_core.handlers.assets import AssetService
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.config import ConfigService
from ldm_core.handlers.diagnostics import DiagnosticsService
from ldm_core.handlers.infra import InfraService
from ldm_core.handlers.license import LicenseService
from ldm_core.handlers.runtime import RuntimeService
from ldm_core.handlers.snapshot import SnapshotService
from ldm_core.handlers.workspace import WorkspaceService
from ldm_core.tests.tmproot import TEST_TMP_ROOT


class MockManager(
    BaseHandler,
):
    def __init__(self):
        from argparse import Namespace

        class DefaultNamespace(Namespace):
            def __getattr__(self, name):
                if name in [
                    "tag_latest",
                    "search",
                    "lean",
                    "tunnel_managed_cors",
                    "portal",
                    "share",
                    "expose",
                    "share_inspector",
                    "sidecar",
                    "persist_osgi",
                    "no_captcha",
                    "fast_login",
                    "force_downgrade",
                    "no_up",
                ]:
                    return False
                return None

        self.args = DefaultNamespace(
            database_mode=None,
            search_mode=None,
            search=False,
            ssl=None,
            lean=False,
            tunnel_managed_cors=False,
        )
        self.verbose = False
        self.non_interactive = True
        self.license = LicenseService(self)
        self.assets = AssetService(self)
        self.config = ConfigService(self)
        self.infra = InfraService(self)
        self.diagnostics = DiagnosticsService(self)
        self.snapshot = SnapshotService(self)
        self.workspace = WorkspaceService(self)
        self.composer = ComposerService(self)
        self.runtime = RuntimeService(self)
        self.share = MagicMock()
        # LDM-#1077: _build_liferay_service() always consults known tunnel
        # base domains for the CORS/virtual-hosts allowlist, even when
        # sharing isn't under test here -- give it a real-shaped default.
        self.share.get_known_tunnel_base_domains.return_value = [
            "lfr-demo.online",
            "lfr-demo.se",
        ]
        self.defaults = MagicMock()

        def mock_defaults_get(key, default=None):
            if key == "port":
                return 8080
            return "isolated"

        self.defaults.get = MagicMock(side_effect=mock_defaults_get)
        self.parse_version = MagicMock(return_value=(2024, 1, 0))  # type: ignore[method-assign]

        self.run_command = MagicMock()  # type: ignore[method-assign]
        self.write_docker_compose = MagicMock()  # type: ignore[method-assign]
        self.cmd_browser = MagicMock(  # type: ignore[method-assign]
            side_effect=self.runtime.cmd_browser
        )
        self.cmd_reset = MagicMock(  # type: ignore[method-assign]
            side_effect=self.runtime.cmd_reset
        )
        self.get_container_status = MagicMock()  # type: ignore[method-assign]
        self.update_portal_ext = MagicMock()  # type: ignore[method-assign]

    def _wait_for_ready(self, *args, **kwargs):
        return self.runtime._wait_for_ready(*args, **kwargs)

    def cmd_run(self, *args, **kwargs):
        return self.runtime.cmd_run(*args, **kwargs)

    def cmd_stop(self, *args, **kwargs):
        return self.runtime.cmd_stop(*args, **kwargs)

    def cmd_restart(self, *args, **kwargs):
        return self.runtime.cmd_restart(*args, **kwargs)

    def cmd_down(self, *args, **kwargs):
        return self.runtime.cmd_down(*args, **kwargs)

    def get_host_passthrough_env(self, *args, **kwargs):
        return []

    def scan_standalone_services(self, *args, **kwargs):
        return []

    def setup_infrastructure(self, *args, **kwargs):
        return True

    def check_docker(self, *args, **kwargs):
        return True

    def sync_common_assets(self, *args, **kwargs):
        pass

    def sync_logging(self, *args, **kwargs):
        pass

    def migrate_layout(self, *args, **kwargs):
        pass

    def detect_project_path(self, *args, **kwargs):
        return Path("/tmp/test-project")

    def scrub_legacy_meta(self, *args, **kwargs):
        pass

    def write_meta(self, *args, **kwargs):
        pass

    def read_meta(self, *args, **kwargs):
        return {}

    def parse_version(self, tag):
        return (2025, 1, 0)

    @staticmethod
    def exists_fn(path):
        return os.path.exists(path)

    def get_resource_path(self, *args, **kwargs):
        return Path("/tmp/res")

    def check_hostname(self, *args, **kwargs):
        return True

    def get_resolved_ip(self, host_name):
        return "127.0.0.1"

    def setup_paths(self, root_path):
        root = Path(root_path)
        return {
            "root": root,
            "files": root / "files",
            "scripts": root / "scripts",
            "state": root / "osgi" / "state",
            "configs": root / "osgi" / "configs",
            "modules": root / "osgi" / "modules",
            "marketplace": root / "osgi" / "marketplace",
            "data": root / "data",
            "deploy": root / "deploy",
            "cx": root / "osgi" / "client-extensions",
            "routes": root / "osgi" / "routes",
            "log4j": root / "osgi" / "log4j",
            "portal_log4j": root / "osgi" / "portal-log4j",
            "compose": root / "docker-compose.yml",
            "ce_dir": root / "client-extensions",
            "logs": root / "logs",
            "backups": root / "backups",
            "snapshots": root / "snapshots",
            "history": root / ".ldm_history",
        }

    def verify_runtime_environment(self, *args, **kwargs):
        pass


class TestStackInfrastructure(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()

    def _provision_search(self, plugin_list_output):
        """Drive setup_global_search for a container that does not exist yet,
        answering each Docker call by what it is rather than by position."""
        import tempfile

        def respond(cmd, *_a, **_k):
            text = " ".join(str(part) for part in cmd)
            if "_cluster/health" in text:
                return '{"status": "green"}'
            if "localhost:9200" in text:
                return '{"cluster_name": "liferay-cluster"}'
            if "elasticsearch-plugin list" in text:
                return plugin_list_output
            if "ps -a" in text or "ps -q" in text:
                return ""
            if "inspect" in text:
                return "running"
            return "OK"

        with (
            tempfile.TemporaryDirectory() as home,
            patch(
                "ldm_core.handlers.infra.get_actual_home",
                return_value=Path(home),
            ),
            patch("time.sleep"),
            patch.object(self.manager, "run_command", side_effect=respond) as run,
        ):
            self.manager.infra.setup_global_search()
            return [" ".join(str(p) for p in c.args[0]) for c in run.call_args_list]

    @patch("ldm_core.utils.check_port", return_value=False)
    def test_plugins_are_installed_before_the_data_is_opened(self, _port):
        """LDM-#2284: a one-shot container installs every analyzer into the
        mounted plugins directory BEFORE Elasticsearch starts on the data."""
        calls = self._provision_search("")
        pre = [c for c in calls if " run --rm " in f" {c} " and "install" in c]
        self.assertEqual(len(pre), 1, calls)
        for plugin in (
            "analysis-icu",
            "analysis-kuromoji",
            "analysis-smartcn",
            "analysis-stempel",
        ):
            self.assertIn(plugin, pre[0])
        es_run = [
            c for c in calls if " run -d " in f" {c} " and "liferay-search-global" in c
        ]
        self.assertEqual(len(es_run), 1, calls)
        self.assertIn(":/usr/share/elasticsearch/plugins", es_run[0])
        self.assertLess(calls.index(pre[0]), calls.index(es_run[0]))

    @patch("ldm_core.utils.check_port", return_value=False)
    def test_post_start_installs_only_what_the_list_lacks(self, _port):
        calls = self._provision_search("analysis-icu\nanalysis-kuromoji\n")
        post = [c for c in calls if " exec " in f" {c} " and "plugin install" in c]
        self.assertEqual(
            sorted(c.split()[-1] for c in post),
            ["analysis-smartcn", "analysis-stempel"],
        )

    @patch("ldm_core.utils.check_port", return_value=False)
    def test_nothing_is_reinstalled_when_all_are_present(self, _port):
        calls = self._provision_search(
            "analysis-icu\nanalysis-kuromoji\nanalysis-smartcn\nanalysis-stempel\n"
        )
        self.assertEqual(
            [c for c in calls if " exec " in f" {c} " and "plugin install" in c], []
        )

    def test_setup_ssl_generates_config(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            cert_dir = Path(tmp_dir)
            host_name = "test.local"

            with (
                patch(
                    "ldm_core.handlers.infra.shutil.which",
                    return_value="/usr/bin/mkcert",
                ),
                patch.object(self.manager, "run_command") as mock_run,
                patch("time.sleep") as mock_sleep,
            ):

                def mock_mkcert(*args, **kwargs):
                    (cert_dir / f"{host_name}.pem").write_text("CERT")
                    (cert_dir / f"{host_name}-key.pem").write_text("KEY")
                    return "OK"

                mock_run.side_effect = mock_mkcert

                res = self.manager.infra.setup_ssl(cert_dir, host_name)

                self.assertTrue(res)
                config_file = cert_dir / f"traefik-{host_name}.yml"
                self.assertTrue(config_file.exists())
                # On test platforms (Darwin/macOS), the sleep should have been triggered
                import platform

                if platform.system().lower() in ["darwin", "windows"]:
                    mock_sleep.assert_called_once_with(2)


class TestStackScaling(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.paths = self.manager.setup_paths(f"{TEST_TMP_ROOT}/proj")

    @patch("ldm_core.handlers.infra.get_docker_socket_path")
    @patch("ldm_core.handlers.config.ConfigService.update_portal_ext")
    def test_generate_compose_with_scale(self, mock_update, mock_socket):
        meta = {
            "tag": "2025.q1.0",
            "scale_liferay": 2,
            "host_name": "localhost",
            "container_name": "scaled-test",
        }

        with patch("ldm_core.utils.safe_write_text") as mock_write:
            self.manager.composer.write_docker_compose(self.paths, meta)

            compose_data = yaml.safe_load(mock_write.call_args[0][1])
            liferay_service = compose_data["services"]["liferay"]

            # SCALE MANDATE: Scale > 1 must NOT have container_name
            self.assertNotIn("container_name", liferay_service)

            # SCALE MANDATE: Host-mapped logs and state must be disabled
            volumes = liferay_service.get("volumes", [])
            self.assertFalse(any("/opt/liferay/logs" in v for v in volumes))
            self.assertFalse(any("/opt/liferay/osgi/state" in v for v in volumes))

            # SCALE MANDATE: Clustering env vars must be present
            env = liferay_service.get("environment", [])
            # Liferay correctly decodes using _PERIOD_
            self.assertTrue(
                any("LIFERAY_CLUSTER_PERIOD_LINK_PERIOD_ENABLED=true" in e for e in env)
            )


class TestStackOrchestration(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp_dir.name)
        # Create full structure
        (self.base / "files").mkdir(parents=True)
        (self.base / "osgi" / "configs").mkdir(parents=True)
        (self.base / "osgi" / "client-extensions").mkdir(parents=True)
        (self.base / "osgi" / "portal-log4j").mkdir(parents=True)
        (self.base / "data").mkdir(parents=True)
        (self.base / "deploy").mkdir(parents=True)
        (self.base / "logs").mkdir(parents=True)
        (self.base / "client-extensions").mkdir(parents=True)

        self.manager = MockManager()
        self.paths = self.manager.setup_paths(self.base)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("time.time")
    @patch("time.sleep")
    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
    def test_cmd_run_readiness_timeout(self, mock_compose, mock_sleep, mock_time):
        """Verifies that the Service Readiness Gate correctly times out if a dependency hangs."""
        import tempfile
        from pathlib import Path

        mock_compose.return_value = ["docker", "compose"]

        with tempfile.TemporaryDirectory() as base_tmp:
            base = Path(base_tmp)
            # Create required structure
            (base / "files").mkdir()
            (base / "osgi" / "configs").mkdir(parents=True)
            (base / "osgi" / "client-extensions").mkdir(parents=True)
            (base / "osgi" / "portal-log4j").mkdir(parents=True)
            (base / "client-extensions").mkdir()
            (base / "data").mkdir()
            (base / "deploy").mkdir()
            (base / "logs").mkdir()

            paths = self.manager.setup_paths(base)
            meta = {
                "container_name": "timeout-test",
                "tag": "2025.q1.0",
                "db_type": "postgresql",
                "host_name": "localhost",
                "use_shared_search": "true",
            }

            # LDM-#1979: the clock advances once per POLL, not once per
            # `time.time()` call.
            #
            # It used to advance 20s on every call, which made the iteration
            # count a function of how many times the code under test happens to
            # consult the clock -- an implementation detail. Add a `time.time()`
            # anywhere in the readiness path and the budget is spent sooner, so
            # `assertGreater(call_count, 1)` below starts failing for a reason
            # that has nothing to do with readiness. It flaked on
            # `lint-and-test (3.14)` while the other three Python versions
            # passed in the same run.
            #
            # Driving the clock from the status probe makes the loop count a
            # pure function of the timeout budget and the 20s step, so the
            # assertion measures the gate rather than the call graph.
            self.t = 1000
            self.polls = 0

            mock_time.side_effect = lambda: self.t

            def mock_status(*_args, **_kwargs):
                self.polls += 1
                self.t += 20
                return "starting"

            with (
                patch.object(
                    self.manager, "get_container_status", side_effect=mock_status
                ),
                patch.object(self.manager, "check_port", return_value=True),
                patch.object(self.manager, "run_command"),
                # LDM-#1363 made shared-search provisioning actually run for a
                # shared-search project, which this meta declares. Without this
                # patch the test reaches the real Docker daemon and tries to
                # start Elasticsearch: it passed on a developer machine that
                # already had the image and container, and died with exit 3 in
                # CI, which has neither. The test is about the readiness gate,
                # not about provisioning.
                patch.object(self.manager.infra, "setup_global_search"),
                patch.object(self.manager.infra, "setup_global_database"),
                # LDM-#1409: the same shape as the note above, and it bit in
                # the same way. On Linux -- so on CI, never on a macOS dev
                # machine -- pipelines/run.py takes its
                # `elif platform.system() == "linux"` branch and calls
                # reclaim_volume_permissions, which runs
                # `docker run --rm -v <path> alpine chown -R ...`.
                patch("ldm_core.utils.reclaim_volume_permissions"),
            ):
                self.manager.runtime.cmd_run(
                    project_id="timeout-test",
                    no_up=False,
                    no_wait=True,
                    paths=paths,
                    project_meta=meta,
                )
                # The gate must POLL and then GIVE UP, not return on the
                # first look and not hang. Asserted on the poll counter this
                # test controls, rather than on a mock's call count, which also
                # counts calls made anywhere else.
                self.assertGreater(
                    self.polls,
                    1,
                    "the readiness gate did not poll more than once, so it is "
                    "not waiting for the container at all (LDM-#1979)",
                )

    def test_generate_compose_with_mysql(self):
        config = {
            "container_name": "test",
            "tag": "2026.q1.4-lts",
            "port": 8080,
            "host_name": "localhost",
            "db_type": "mysql",
        }

        with patch("ldm_core.utils.safe_write_text") as mock_write:
            self.manager.composer.write_docker_compose(self.paths, config)
            compose_data = yaml.safe_load(mock_write.call_args[0][1])
            liferay_service = compose_data["services"]["liferay"]
            db_service = compose_data["services"]["test-db"]

            self.assertEqual(liferay_service["image"], "liferay/dxp:2026.q1.4-lts")
            self.assertEqual(db_service["image"], "mysql:8.4.0")

            # MySQL 8.4 uses --mysql-native-password=ON instead of --default-authentication-plugin
            self.assertIn(
                "--mysql-native-password=ON",
                db_service["command"],
            )

    def test_wait_for_ready_logs_monitoring(self):
        """Verifies that the health check loop proactively scans logs for ERRORs."""

        self.t = 0

        def mock_time():
            self.t += 31  # Trigger notification blocks
            return self.t

        with (
            patch("time.time", side_effect=mock_time),
            patch("time.sleep"),
            patch.object(self.manager, "run_command") as mock_run,
            patch("ldm_core.ui.UI.warning") as mock_warn,
        ):
            # Dynamic response logic to avoid StopIteration and correctly trigger both paths
            def run_cmd_side_effect(cmd, *args, **kwargs):
                cmd_str = str(cmd)
                if "logs" in cmd_str and "--tail" in cmd_str:
                    if self.t > 100:  # Succeed in later loops
                        return "org.apache.catalina.startup.Catalina.start Server startup in 123 ms"
                    return "ERROR: Database connection failed"
                if "inspect" in cmd_str:
                    if self.t > 200:
                        return "healthy"
                    return ""
                return ""

            mock_run.side_effect = run_cmd_side_effect

            with patch("ldm_core.runtime.readiness.datetime") as mock_dt:
                mock_dt.now().strftime.return_value = "12:00:00"
                self.manager._wait_for_ready(
                    {"container_name": "test-container"}, "test-host", 0
                )

            # Verify that we hit the warning logic
            mock_warn.assert_any_call("LDM detected 1 new error(s) in the logs.")

    def test_fetch_seed_url_construction(self):
        from ldm_core.constants import SEED_VERSION

        tag = "2025.q1.0"
        db_type = "mysql"
        search_mode = "shared"
        self.manager.non_interactive = True

        with (
            patch("ldm_core.ui.UI.NON_INTERACTIVE", True),
            patch(
                "ldm_core.handlers.assets.get_actual_home",
                return_value=Path("/tmp/home"),
            ),
            patch("requests.head") as mock_head,
            patch("os.path.exists", return_value=False),
        ):
            mock_head.return_value.status_code = 404  # Skip download
            self.manager.assets._fetch_seed(tag, db_type, search_mode, self.paths)
            call_url = mock_head.call_args[0][0]
            self.assertIn("seeded-states", call_url)
            self.assertIn(
                f"seeded-{tag}-{db_type}-{search_mode}-v{SEED_VERSION}.tar.gz", call_url
            )

    def test_fetch_seed_interactive_confirmation(self):
        """Verifies that LDM prompts for confirmation in interactive mode."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir_name:
            tmp_home = Path(tmp_dir_name)

            # Key: Mock exists so it MUST enter the download loop
            with (
                patch(
                    "ldm_core.handlers.assets.get_actual_home", return_value=tmp_home
                ),
                patch("ldm_core.handlers.assets.requests.get") as mock_get,
                patch("ldm_core.handlers.assets.requests.head") as mock_head,
                patch("ldm_core.ui.UI.confirm", return_value=False) as mock_confirm,
                patch("ldm_core.ui.UI.NON_INTERACTIVE", False),
                patch.object(self.manager, "exists_fn", return_value=False),
            ):
                self.manager.non_interactive = False
                res_head = MagicMock()
                res_head.status_code = 200
                res_head.headers = {"content-length": "1048576"}
                mock_head.return_value = res_head

                # 1. User says NO
                mock_confirm.return_value = False
                result = self.manager.assets._fetch_seed(
                    "tag", "db", "search", self.paths
                )
                self.assertFalse(result)
                mock_confirm.assert_called()

                # 2. User says YES
                mock_confirm.reset_mock()
                mock_confirm.return_value = True
                mock_get.side_effect = Exception("Stop")
                result = self.manager.assets._fetch_seed(
                    "tag", "db", "search", self.paths
                )
                self.assertFalse(result)
                self.assertTrue(mock_get.called)

    def test_cmd_browser_launches_url(self):
        with (
            patch.object(
                self.manager,
                "read_meta",
                return_value={
                    "host_name": "test.local",
                    "ssl": "true",
                    "ssl_port": 443,
                },
            ),
            patch.object(
                self.manager, "detect_project_path", return_value=Path("/tmp/test")
            ),
            patch("ldm_core.utils.open_browser") as mock_open,
        ):
            self.manager.non_interactive = False
            self.manager.runtime.cmd_browser("test")
            self.assertTrue(mock_open.called)

    def test_cmd_reset_state(self):
        with (
            patch.object(self.manager, "run_command", return_value=None),
            patch.object(Path, "exists", return_value=True),
            patch("ldm_core.runtime.orchestration.shutil.rmtree") as mock_rmtree,
        ):
            self.manager.runtime.cmd_reset("test", target="state")
            self.assertTrue(mock_rmtree.called)


if __name__ == "__main__":
    unittest.main()
