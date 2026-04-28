import unittest
import yaml
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.composer import ComposerHandler
from ldm_core.handlers.runtime import RuntimeHandler
from ldm_core.handlers.assets import AssetHandler
from ldm_core.handlers.infra import InfraHandler
from ldm_core.handlers.workspace import WorkspaceHandler
from ldm_core.handlers.license import LicenseHandler
from ldm_core.handlers.snapshot import SnapshotHandler
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.config import ConfigHandler
from ldm_core.handlers.diagnostics import DiagnosticsHandler


class MockManager(
    ComposerHandler,
    RuntimeHandler,
    AssetHandler,
    InfraHandler,
    WorkspaceHandler,
    LicenseHandler,
    SnapshotHandler,
    DiagnosticsHandler,
    ConfigHandler,
    BaseHandler,
):
    def __init__(self):
        self.args = MagicMock()
        self.args.search = False
        self.verbose = False
        self.non_interactive = True

        # Wrap methods for patching while keeping logic
        self.run_command = MagicMock()
        self.write_docker_compose = MagicMock(
            side_effect=ComposerHandler.write_docker_compose.__get__(self, MockManager)
        )
        self.setup_ssl = MagicMock(
            side_effect=InfraHandler.setup_ssl.__get__(self, MockManager)
        )
        self.cmd_browser = MagicMock(
            side_effect=RuntimeHandler.cmd_browser.__get__(self, MockManager)
        )
        self.cmd_reset = MagicMock(
            side_effect=RuntimeHandler.cmd_reset.__get__(self, MockManager)
        )
        self.update_portal_ext = MagicMock()

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

    def cmd_down(self, *args, **kwargs):
        pass

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

    @patch("ldm_core.utils.check_port", return_value=False)
    @patch("ldm_core.handlers.infra.shutil.which")
    def test_setup_global_search_installs_plugins(self, mock_which, mock_check_port):
        mock_which.return_value = "/usr/bin/elasticsearch-plugin"

        with (
            patch(
                "ldm_core.handlers.infra.get_actual_home",
                return_value=Path("/tmp/home"),
            ),
            patch("time.sleep"),
            patch.object(self.manager, "run_command") as mock_run,
        ):
            # Existence check: No container
            mock_run.side_effect = [
                "",  # 1. ps -a name=liferay-search-global
                "",  # 2. docker run ...
                '{"cluster_name": "liferay-cluster"}',  # 3. health check (readiness)
                "OK",  # 4. PUT snapshot
                "",  # 5. elasticsearch-plugin list
                "OK",  # 6. install 1
                "OK",  # 7. install 2
                "OK",  # 8. install 3
                "OK",  # 9. install 4
                "OK",  # 10. docker restart
            ]

            self.manager.setup_global_search()

            # Verify plugin installation was attempted
            install_calls = [
                c
                for c in mock_run.call_args_list
                if "elasticsearch-plugin" in str(c) and "install" in str(c)
            ]
            self.assertEqual(len(install_calls), 4)

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
            ):

                def mock_mkcert(*args, **kwargs):
                    (cert_dir / f"{host_name}.pem").write_text("CERT")
                    (cert_dir / f"{host_name}-key.pem").write_text("KEY")
                    return "OK"

                mock_run.side_effect = mock_mkcert

                res = self.manager.setup_ssl(cert_dir, host_name)

                self.assertTrue(res)
                config_file = cert_dir / f"traefik-{host_name}.yml"
                self.assertTrue(config_file.exists())


class TestStackScaling(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.paths = self.manager.setup_paths("/tmp/proj")

    @patch("ldm_core.handlers.infra.get_docker_socket_path")
    def test_generate_compose_with_scale(self, mock_socket):
        meta = {
            "tag": "2025.q1.0",
            "scale_liferay": 2,
            "host_name": "localhost",
            "container_name": "scaled-test",
        }

        with patch.object(Path, "write_text") as mock_write:
            self.manager.write_docker_compose(self.paths, meta)

            compose_data = yaml.safe_load(mock_write.call_args[0][0])
            liferay_service = compose_data["services"]["liferay"]

            # SCALE MANDATE: Scale > 1 must NOT have container_name
            self.assertNotIn("container_name", liferay_service)

            # SCALE MANDATE: Host-mapped logs and state must be disabled
            volumes = liferay_service.get("volumes", [])
            self.assertFalse(any("/opt/liferay/logs" in v for v in volumes))
            self.assertFalse(any("/opt/liferay/osgi/state" in v for v in volumes))

            # SCALE MANDATE: Clustering env vars must be present
            env = liferay_service.get("environment", [])
            # 2025.Q1+ uses single underscore
            self.assertTrue(any("LIFERAY_CLUSTER_LINK_ENABLED=true" in e for e in env))


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
    @patch("ldm_core.utils.get_compose_cmd")
    def test_sync_stack_readiness_timeout(self, mock_compose, mock_sleep, mock_time):
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

            self.t = 1000

            def mock_time_inc():
                self.t += 20
                return self.t

            mock_time.side_effect = mock_time_inc

            with (
                patch.object(
                    self.manager, "get_container_status", return_value="starting"
                ),
                patch.object(self.manager, "run_command"),
            ):
                self.manager.sync_stack(paths, meta, no_up=False, no_wait=True)
                self.assertGreater(self.manager.get_container_status.call_count, 1)

    def test_generate_compose_with_mysql(self):
        config = {
            "container_name": "test",
            "tag": "7.4.13-u100",
            "port": 8080,
            "host_name": "localhost",
            "db_type": "mysql",
        }

        with patch.object(Path, "write_text") as mock_write:
            self.manager.write_docker_compose(self.paths, config)
            compose_data = yaml.safe_load(mock_write.call_args[0][0])
            liferay_service = compose_data["services"]["liferay"]
            db_service = compose_data["services"]["db"]

            self.assertEqual(liferay_service["image"], "liferay/portal:7.4.13-u100")
            self.assertEqual(db_service["image"], "mysql:5.7")
            self.assertIn(
                "--default-authentication-plugin=mysql_native_password",
                db_service["command"],
            )

    def test_fetch_seed_url_construction(self):
        from ldm_core.constants import SEED_VERSION

        tag = "2025.q1.0"
        db_type = "mysql"
        search_mode = "shared"

        with (
            patch(
                "ldm_core.handlers.assets.get_actual_home",
                return_value=Path("/tmp/home"),
            ),
            patch("requests.head") as mock_head,
            patch("os.path.exists", return_value=False),
        ):
            mock_head.return_value.status_code = 404  # Skip download
            self.manager._fetch_seed(tag, db_type, search_mode, self.paths)
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
                result = self.manager._fetch_seed("tag", "db", "search", self.paths)
                self.assertFalse(result)
                mock_confirm.assert_called()

                # 2. User says YES
                mock_confirm.reset_mock()
                mock_confirm.return_value = True
                mock_get.side_effect = Exception("Stop")
                result = self.manager._fetch_seed("tag", "db", "search", self.paths)
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
            self.manager.cmd_browser("test")
            self.assertTrue(mock_open.called)

    def test_cmd_reset_state(self):
        with (
            patch.object(self.manager, "run_command", return_value=None),
            patch.object(Path, "exists", return_value=True),
            patch("ldm_core.handlers.runtime.shutil.rmtree") as mock_rmtree,
        ):
            self.manager.cmd_reset("test", target="state")
            self.assertTrue(mock_rmtree.called)


if __name__ == "__main__":
    unittest.main()
