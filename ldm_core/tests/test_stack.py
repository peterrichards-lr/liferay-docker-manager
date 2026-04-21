import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.stack import StackHandler
from ldm_core.handlers.workspace import WorkspaceHandler
from ldm_core.handlers.license import LicenseHandler
from ldm_core.handlers.snapshot import SnapshotHandler
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.infra import InfraHandler


class MockManager(
    StackHandler,
    WorkspaceHandler,
    LicenseHandler,
    SnapshotHandler,
    InfraHandler,
    BaseHandler,
):
    def __init__(self):
        self.args = MagicMock()
        self.args.search = False
        self.verbose = False
        self.non_interactive = True

    # Mocking external dependency methods that are NOT in the handlers above
    def get_host_passthrough_env(self, *args, **kwargs):
        return []

    def scan_standalone_services(self, *args, **kwargs):
        return []

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
        # Default to a modern version for tests
        return (2025, 1, 0)

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
        }

    def verify_runtime_environment(self, *args, **kwargs):
        pass

    def check_hostname(self, *args, **kwargs):
        return True


class TestStackInfrastructure(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.manager.args = MagicMock()
        self.manager.verbose = False
        self.manager.non_interactive = True
        self.manager.update_portal_ext = MagicMock()

    @patch("ldm_core.utils.check_port", return_value=False)
    @patch("ldm_core.utils.run_command")
    @patch("ldm_core.utils.get_actual_home")
    @patch("time.sleep")
    def test_setup_global_search_installs_plugins(
        self, mock_sleep, mock_home, mock_run, mock_check_port
    ):
        mock_home.return_value = Path("/tmp/home")

        # 1. Existence check: No container
        # 2. Inspect: (skipped since no container)
        # 3. docker run: (starts ES)
        # 4. elasticsearch ready check: returns "cluster_name"
        # 5. snapshot PUT
        # 6. elasticsearch-plugin list: returns empty string (no plugins)
        # 7. elasticsearch-plugin install (4 calls)
        # 8. docker restart
        # 9. wait for ready (returns "cluster_name")

        mock_run.side_effect = [
            None,  # existence check
            "container_id",  # docker run
            '{"cluster_name": "test"}',  # ready check
            "snapshot_ok",  # snapshot PUT
            "",  # plugin list (missing all)
            "install_icu",
            "install_kuro",
            "install_smart",
            "install_stempel",
            "restart_ok",
            '{"cluster_name": "test"}',  # ready check after restart
        ]

        with patch.object(self.manager, "_ensure_network"):
            self.manager.setup_global_search()

            # Verify plugin installation was attempted
            install_calls = [
                c
                for c in mock_run.call_args_list
                if "elasticsearch-plugin" in str(c) and "install" in str(c)
            ]
            self.assertEqual(len(install_calls), 4)

            # Verify optimized config was in the 'docker run' call
            run_call = [
                c for c in mock_run.call_args_list if "run" in str(c) and "-d" in str(c)
            ][0]
            cmd_args = run_call[0][0]
            self.assertIn("indices.query.bool.max_clause_count=10000", cmd_args)

    @patch("ldm_core.utils.run_command")
    @patch("shutil.which")
    def test_setup_ssl_generates_config(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/mkcert"

        # We'll use a temp directory for certs
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            cert_dir = Path(tmp_dir)
            host_name = "test.local"

            # Mock mkcert creating the files
            def mock_mkcert(*args, **kwargs):
                (cert_dir / f"{host_name}.pem").write_text("CERT")
                (cert_dir / f"{host_name}-key.pem").write_text("KEY")
                return "OK"

            mock_run.side_effect = mock_mkcert

            res = self.manager.setup_ssl(cert_dir, host_name)

            self.assertTrue(res)
            # Verify Traefik config was created
            config_file = cert_dir / f"traefik-{host_name}.yml"
            self.assertTrue(config_file.exists())
            self.assertIn(
                f"certFile: /etc/traefik/certs/{host_name}.pem", config_file.read_text()
            )


class TestStackScaling(unittest.TestCase):
    @patch("ldm_core.handlers.infra.get_docker_socket_path")
    def test_generate_compose_with_scale(self, mock_socket):
        mock_socket.return_value = "/var/run/docker.sock"

        manager = MockManager()
        manager.update_portal_ext = MagicMock()
        paths = manager.setup_paths("/tmp/test-project")

        # Scale = 1 (default)
        config = {
            "container_name": "test-project",
            "image_tag": "liferay/dxp:2025.q1.0",
            "port": 8080,
            "scale_liferay": 1,
        }

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value=""),
            patch.object(Path, "write_text") as mock_write,
            patch("os.replace"),
            patch.object(manager, "scan_client_extensions", return_value=[]),
        ):
            manager.write_docker_compose(paths, config)
            # Verify container_name is set
            import yaml

            compose_data = yaml.safe_load(mock_write.call_args[0][0])
            self.assertEqual(
                compose_data["services"]["liferay"]["container_name"], "test-project"
            )
            # Verify state volume is mounted
            volumes = compose_data["services"]["liferay"]["volumes"]
            self.assertTrue(any("/opt/liferay/osgi/state" in v for v in volumes))

        # Scale = 2
        config["scale_liferay"] = 2
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value=""),
            patch.object(Path, "write_text") as mock_write,
            patch("os.replace"),
            patch.object(manager, "scan_client_extensions", return_value=[]),
        ):
            manager.write_docker_compose(paths, config)
            compose_data = yaml.safe_load(mock_write.call_args[0][0])
            # In scaling mode, container_name should be ABSENT to let Docker handle indexing
            self.assertNotIn("container_name", compose_data["services"]["liferay"])
            # In scaling mode, state volume should be DISABLED
            volumes = compose_data["services"]["liferay"]["volumes"]
            self.assertFalse(any("/opt/liferay/osgi/state" in v for v in volumes))
            # Verify clustering properties are applied
            update_call = manager.update_portal_ext.call_args[0][1]
            self.assertEqual(update_call["cluster.link.enabled"], "true")
            self.assertEqual(update_call["lucene.replicate.write"], "true")


class TestStackNetwork(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.paths = self.manager.setup_paths("/tmp/test-project")

    @patch("ldm_core.utils.get_compose_cmd")
    @patch("ldm_core.utils.run_command")
    def test_sync_stack_ensures_network(self, mock_run, mock_compose):
        mock_compose.return_value = ["docker", "compose"]

        # Minimal project meta
        project_meta = {
            "tag": "2025.q1.0",
            "host_name": "localhost",
            "ssl": "false",
            "use_shared_search": "false",
        }

        with (
            patch.object(self.manager, "_ensure_network") as mock_ensure,
            patch.object(self.manager, "write_docker_compose"),
        ):
            # Verify network is ensured regardless of configuration
            self.manager.sync_stack(self.paths, project_meta, no_up=True)
            mock_ensure.assert_called_once_with()

    @patch("ldm_core.utils.get_compose_cmd")
    @patch("ldm_core.utils.run_command")
    def test_sync_stack_with_missing_meta_values(self, mock_run, mock_compose):
        mock_compose.return_value = ["docker", "compose"]

        # Scenario: meta has None values (the cause of the reported crash)
        project_meta = {
            "tag": "2025.q1.0",
            "host_name": "localhost",
            "port": None,
            "ssl_port": None,
            "ssl": "false",
            "use_shared_search": "false",
        }

        with (
            patch.object(self.manager, "_ensure_network"),
            patch.object(self.manager, "write_docker_compose"),
        ):
            # This should NOT raise TypeError
            try:
                self.manager.sync_stack(self.paths, project_meta, no_up=True)
            except TypeError as e:
                self.fail(f"sync_stack raised TypeError with None meta values: {e}")


class TestStackOrchestration(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.manager.update_portal_ext = MagicMock()
        self.paths = self.manager.setup_paths("/tmp/test-project")

    def test_write_docker_compose_ssl_labels(self):
        import yaml

        # Scenario: SSL enabled, custom host
        config = {
            "container_name": "test",
            "image_tag": "liferay/dxp:latest",
            "port": 8080,
            "use_ssl": True,
            "host_name": "forge.demo",
            "ssl_port": 443,
        }

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value=""),
            patch.object(Path, "write_text") as mock_write,
            patch("os.replace"),
            patch.object(self.manager, "scan_client_extensions", return_value=[]),
        ):
            self.manager.write_docker_compose(self.paths, config)
            compose_data = yaml.safe_load(mock_write.call_args[0][0])
            labels = compose_data["services"]["liferay"]["labels"]

            # Tests expect the main router to be '{project_name}-main'
            self.assertIn("traefik.http.routers.test-main.tls=true", labels)
            self.assertIn(
                "traefik.http.routers.test-main.entrypoints=websecure", labels
            )
            # Verify we have explicit domain hints for SNI matching
            self.assertTrue(
                any("tls.domains[0].main=forge.demo" in label for label in labels)
            )
            self.assertTrue(
                any("tls.domains[0].sans=*.forge.demo" in label for label in labels)
            )

    def test_microservice_port_resolution(self):
        import yaml

        # Mock a microservice with a custom targetPort
        mock_exts = [
            {
                "id": "my-ms",
                "name": "my-ms",
                "kind": "Deployment",
                "deploy": True,
                "is_service": True,
                "has_load_balancer": True,
                "loadBalancer": {"targetPort": 3001},
                "path": Path("/tmp/ms"),
            }
        ]

        config = {
            "container_name": "test",
            "image_tag": "liferay/dxp:latest",
            "port": 8080,
            "use_ssl": True,
            "host_name": "forge.demo",
        }

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value=""),
            patch.object(Path, "write_text") as mock_write,
            patch("os.replace"),
            patch.object(
                self.manager, "scan_client_extensions", return_value=mock_exts
            ),
        ):
            self.manager.write_docker_compose(self.paths, config)
            compose_data = yaml.safe_load(mock_write.call_args[0][0])

            # Verify Traefik service label uses the targetPort
            # Service key is container_name-id (test-my-ms)
            ms_labels = compose_data["services"]["test-my-ms"]["labels"]
            self.assertIn(
                "traefik.http.services.test-my-ms-svc.loadbalancer.server.port=3001",
                ms_labels,
            )

    def test_jvm_args_override(self):
        import yaml

        config = {
            "container_name": "test",
            "image_tag": "liferay/dxp:latest",
            "port": 8080,
            "host_name": "localhost",
            "jvm_args": "-Xms4g -Xmx4g",
        }

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value=""),
            patch.object(Path, "write_text") as mock_write,
            patch("os.replace"),
            patch.object(self.manager, "scan_client_extensions", return_value=[]),
        ):
            self.manager.write_docker_compose(self.paths, config)
            compose_data = yaml.safe_load(mock_write.call_args[0][0])

            # Verify JVM args are present in environment
            liferay_env = compose_data["services"]["liferay"]["environment"]

            # Check LIFERAY_JVM_OPTS
            jvm_opts_env = next(
                (e for e in liferay_env if e.startswith("LIFERAY_JVM_OPTS=")), None
            )
            self.assertIsNotNone(jvm_opts_env)
            # The spaces should be standard spaces for shell expansion
            self.assertIn("-Xms4g -Xmx4g", jvm_opts_env)
            self.assertIn("-XX:TieredStopAtLevel=1", jvm_opts_env)

    def test_generate_compose_with_mysql(self):
        import yaml

        config = {
            "container_name": "test",
            "image_tag": "liferay/dxp:latest",
            "port": 8080,
            "host_name": "localhost",
            "db_type": "mysql",
        }

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value=""),
            patch.object(Path, "write_text") as mock_write,
            patch("os.replace"),
            patch.object(self.manager, "scan_client_extensions", return_value=[]),
        ):
            self.manager.write_docker_compose(self.paths, config)
            compose_data = yaml.safe_load(mock_write.call_args[0][0])

            # Verify Liferay JVM Flags
            liferay_env = compose_data["services"]["liferay"]["environment"][0]
            self.assertIn("-Dfile.encoding=UTF8", liferay_env)
            self.assertIn("-Duser.timezone=GMT", liferay_env)

            # Verify MySQL service hardening
            db_service = compose_data["services"]["db"]
            self.assertEqual(db_service["image"], "mysql:5.7")
            self.assertIn("mysqld", db_service["command"])
            self.assertIn("--character-set-server=utf8mb4", db_service["command"])
            self.assertIn(
                "--collation-server=utf8mb4_unicode_ci", db_service["command"]
            )
            self.assertIn("--lower_case_table_names=1", db_service["command"])
            self.assertIn("--skip-name-resolve", db_service["command"])
            self.assertIn(
                "--default-authentication-plugin=mysql_native_password",
                db_service["command"],
            )
            self.assertEqual(db_service["environment"]["MYSQL_DATABASE"], "lportal")
            self.assertEqual(db_service["healthcheck"]["start_period"], "60s")

            # Verify MariaDB dialect is used even for legacy MySQL (Standardized on MariaDB driver)
            update_call = self.manager.update_portal_ext.call_args[0][1]
            self.assertEqual(
                update_call["hibernate.dialect"],
                "org.hibernate.dialect.MariaDB103Dialect",
            )

    def test_generate_compose_with_mysql_modern(self):
        import yaml

        config = {
            "container_name": "test",
            "tag": "2026.q1.4",
            "port": 8080,
            "host_name": "localhost",
            "db_type": "mysql",
        }

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value=""),
            patch.object(Path, "write_text") as mock_write,
            patch("os.replace"),
            patch.object(self.manager, "scan_client_extensions", return_value=[]),
        ):
            self.manager.write_docker_compose(self.paths, config)
            compose_data = yaml.safe_load(mock_write.call_args[0][0])

            # Verify MySQL 8.4 is selected for 2026 version
            db_service = compose_data["services"]["db"]
            self.assertEqual(db_service["image"], "mysql:8.4")
            self.assertIn("--mysql-native-password=ON", db_service["command"])
            self.assertIn("--skip-name-resolve", db_service["command"])
            self.assertNotIn(
                "--default-authentication-plugin=mysql_native_password",
                db_service["command"],
            )

            # Verify Cloud-aligned MariaDB dialect is used
            update_call = self.manager.update_portal_ext.call_args[0][1]
            self.assertEqual(
                update_call["hibernate.dialect"],
                "org.hibernate.dialect.MariaDB103Dialect",
            )
            self.assertEqual(db_service["healthcheck"]["start_period"], "60s")

    def test_generate_compose_with_custom_env(self):
        import yaml

        config = {
            "container_name": "test",
            "tag": "2026.q1.4",
            "port": 8080,
            "host_name": "localhost",
            "db_type": "mysql",
            "custom_env": "LIFERAY_JDBC_PERIOD_URL=jdbc:mysql://remote:3306/lportal,LIFERAY_CUSTOM_VAR=val",
        }

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value=""),
            patch.object(Path, "write_text") as mock_write,
            patch("os.replace"),
            patch.object(self.manager, "scan_client_extensions", return_value=[]),
        ):
            self.manager.write_docker_compose(self.paths, config)
            compose_data = yaml.safe_load(mock_write.call_args[0][0])

            # Verify Custom Env Vars are present in environment
            liferay_env = compose_data["services"]["liferay"]["environment"]
            self.assertIn(
                "LIFERAY_JDBC_PERIOD_URL=jdbc:mysql://remote:3306/lportal", liferay_env
            )
            self.assertIn("LIFERAY_CUSTOM_VAR=val", liferay_env)

            # Verify update_portal_ext was NOT called for JDBC (because we have JDBC env vars)
            # The only calls should be for clustering if scale > 1 (not here)
            # Actually, MockManager's write_docker_compose calls self.update_portal_ext
            self.manager.update_portal_ext.assert_not_called()

    @patch("ldm_core.utils.run_command")
    def test_cmd_logs_infra(self, mock_run):
        # Mock docker ps -q returning a container ID (service is running)
        mock_run.return_value = "abc123"

        # Test showing all infra logs
        self.manager.cmd_logs(infra=True)

        # Verify it checks if containers are running
        checked_containers = [
            call[0][0][4] for call in mock_run.call_args_list if "ps" in call[0][0]
        ]
        self.assertIn("name=^liferay-proxy-global$", checked_containers)
        self.assertIn("name=^liferay-search-global$", checked_containers)

        # Test filtering for a specific infra service
        mock_run.reset_mock()
        self.manager.cmd_logs(infra=True, service="es")

        # Verify it specifically targeted the search container
        checked_containers = [
            call[0][0][4] for call in mock_run.call_args_list if "ps" in call[0][0]
        ]
        self.assertEqual(len(checked_containers), 1)
        self.assertIn("name=^liferay-search-global$", checked_containers)

    @patch("ldm_core.utils.run_command")
    def test_cmd_logs_passes_tail(self, mock_run):
        # Mock container existing
        mock_run.return_value = "abc123"

        # 1. Test infra logs with tail
        self.manager.cmd_logs(infra=True, tail="50")
        infra_logs_call = [
            call[0][0] for call in mock_run.call_args_list if "logs" in call[0][0]
        ][0]
        self.assertIn("--tail", infra_logs_call)
        self.assertIn("50", infra_logs_call)

        # 2. Test project logs with tail
        mock_run.reset_mock()
        with patch.object(
            self.manager, "detect_project_path", return_value=Path("/tmp/proj")
        ):
            self.manager.cmd_logs(project_id="forge", tail="200")
            project_logs_call = [
                call[0][0] for call in mock_run.call_args_list if "logs" in call[0][0]
            ][0]
            self.assertIn("--tail", project_logs_call)
            self.assertIn("200", project_logs_call)

    @patch("ldm_core.utils.run_command")
    @patch("requests.get")
    @patch("requests.head")
    @patch("shutil.move")
    @patch("ldm_core.ui.UI.ask", return_value="Y")
    def test_cmd_reseed(self, mock_ask, mock_move, mock_head, mock_get, mock_run):
        mock_head.return_value.status_code = 200
        mock_get.return_value.__enter__.return_value.iter_content.return_value = [
            b"data"
        ]
        mock_run.return_value = ""  # Project not running

        with patch.object(
            self.manager, "detect_project_path", return_value=Path("/tmp/proj")
        ):
            with patch.object(
                self.manager, "read_meta", return_value={"tag": "2025.q1.0"}
            ):
                with patch.object(self.manager, "cmd_reset") as mock_reset:
                    with patch.object(self.manager, "write_meta"):
                        with patch.object(Path, "mkdir"):
                            with patch.object(
                                self.manager, "_extract_snapshot_archive"
                            ):
                                self.manager.cmd_reseed("proj")

                                # Verify sequence
                                mock_reset.assert_called_once_with("proj", target="all")
                                mock_head.assert_called_once()

    @patch("requests.head")
    @patch("requests.get")
    def test_fetch_seed_url_construction(self, mock_get, mock_head):
        from ldm_core.constants import SEED_VERSION

        mock_head.return_value.status_code = 404  # Stop after construction check

        manager = MockManager()
        paths = manager.setup_paths("/tmp/proj")

        # Test URL construction
        tag = "2025.q1.0"
        db_type = "mysql"
        search_mode = "shared"

        manager._fetch_seed(tag, db_type, search_mode, paths)

        # Verify the constructed URL in the head request
        call_url = mock_head.call_args[0][0]
        self.assertIn("seeded-states", call_url)
        self.assertIn(
            f"seeded-{tag}-{db_type}-{search_mode}-v{SEED_VERSION}.tar.gz", call_url
        )

    def test_cmd_infra_setup(self):
        with patch.object(self.manager, "check_docker", return_value=True):
            with patch.object(
                self.manager, "get_resolved_ip", return_value="127.0.0.1"
            ):
                with patch.object(self.manager, "setup_infrastructure") as mock_setup:
                    self.manager.cmd_infra_setup()

                    # Verify setup_infrastructure was called with default local settings
                    mock_setup.assert_called_once()
                    args, kwargs = mock_setup.call_args

                    import platform

                    expected_ip = (
                        "0.0.0.0"
                        if platform.system().lower() == "darwin"
                        else "127.0.0.1"
                    )
                    self.assertEqual(args[0], expected_ip)  # resolved_ip
                    self.assertEqual(args[1], 443)  # ssl_port (integer)
                    self.assertTrue(kwargs.get("use_ssl"))

    @patch("ldm_core.utils.run_command")
    @patch("shutil.rmtree")
    def test_cmd_reset_state(self, mock_rmtree, mock_run):
        import os

        manager = MockManager()
        # Mock project is NOT running
        mock_run.return_value = None

        with patch.object(Path, "exists", return_value=True):
            manager.cmd_reset("test", target="state")
            # Verify rmtree was called for osgi/state
            self.assertTrue(mock_rmtree.called)
            args = mock_rmtree.call_args[0][0]
            # Normalize path for comparison
            expected_part = os.path.join("osgi", "state")
            self.assertTrue(expected_part in str(args))

    @patch("ldm_core.utils.open_browser")
    def test_cmd_browser_launches_url(self, mock_open):
        manager = MockManager()
        with (
            patch.object(
                manager,
                "read_meta",
                return_value={
                    "host_name": "test.local",
                    "ssl": "true",
                    "ssl_port": 443,
                },
            ),
            patch.object(
                manager, "detect_project_path", return_value=Path("/tmp/test")
            ),
        ):
            manager.cmd_browser("test")
            mock_open.assert_called_with("https://test.local")


if __name__ == "__main__":
    unittest.main()
