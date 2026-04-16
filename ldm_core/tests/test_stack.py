import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.stack import StackHandler
from ldm_core.handlers.workspace import WorkspaceHandler
from ldm_core.handlers.license import LicenseHandler


class MockManager(StackHandler, WorkspaceHandler, LicenseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

    # Mocking methods that StackHandler might use
    def get_host_passthrough_env(self, *args, **kwargs):
        return []

    def scan_client_extensions(self, *args, **kwargs):
        return []

    def scan_standalone_services(self, *args, **kwargs):
        return []

    def setup_ssl(self, *args, **kwargs):
        pass

    def setup_infrastructure(self, *args, **kwargs):
        pass

    def setup_global_search(self, *args, **kwargs):
        pass

    def check_docker(self, *args, **kwargs):
        return True

    def update_portal_ext(self, *args, **kwargs):
        pass

    def sync_common_assets(self, *args, **kwargs):
        pass

    def sync_logging(self, *args, **kwargs):
        pass

    def migrate_layout(self, *args, **kwargs):
        pass

    def get_default_jvm_args(self, *args, **kwargs):
        return "-Xms4g -Xmx12g"

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

    def get_resolved_ip(self, host_name):
        return "127.0.0.1"

    def setup_paths(self, root_path):
        return {
            "root": Path(root_path),
            "files": Path(root_path) / "files",
            "scripts": Path(root_path) / "scripts",
            "state": Path(root_path) / "osgi" / "state",
            "configs": Path(root_path) / "osgi" / "configs",
            "modules": Path(root_path) / "osgi" / "modules",
            "marketplace": Path(root_path) / "osgi" / "marketplace",
            "data": Path(root_path) / "data",
            "deploy": Path(root_path) / "deploy",
            "cx": Path(root_path) / "osgi" / "client-extensions",
            "routes": Path(root_path) / "osgi" / "routes",
            "log4j": Path(root_path) / "osgi" / "log4j",
            "portal_log4j": Path(root_path) / "osgi" / "portal-log4j",
            "compose": Path(root_path) / "docker-compose.yml",
            "ce_dir": Path(root_path) / "client-extensions",
            "logs": Path(root_path) / "logs",
        }


class TestStackInfrastructure(unittest.TestCase):
    def setUp(self):
        self.manager = StackHandler()
        self.manager.args = MagicMock()
        self.manager.verbose = False
        self.manager.non_interactive = True

    @patch("ldm_core.utils.check_port", return_value=False)
    @patch("ldm_core.handlers.stack.run_command")
    @patch("ldm_core.handlers.stack.get_actual_home")
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

    @patch("ldm_core.handlers.stack.run_command")
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
    @patch("ldm_core.handlers.stack.dict_to_yaml")
    @patch("ldm_core.handlers.stack.get_docker_socket_path")
    def test_generate_compose_with_scale(self, mock_socket, mock_yaml):
        mock_socket.return_value = "/var/run/docker.sock"
        mock_yaml.side_effect = lambda x, indent=0: str(x)

        manager = MockManager()
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
            patch.object(Path, "write_text"),
            patch("os.replace"),
            patch.object(manager, "scan_client_extensions", return_value=[]),
        ):
            manager.write_docker_compose(paths, config)
            # Verify container_name is set
            compose_call = mock_yaml.call_args[0][0]
            self.assertEqual(
                compose_call["services"]["liferay"]["container_name"], "test-project"
            )
            # Verify state volume is mounted
            volumes = compose_call["services"]["liferay"]["volumes"]
            self.assertTrue(any("/opt/liferay/osgi/state" in v for v in volumes))

        # Scale = 2
        config["scale_liferay"] = 2
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value=""),
            patch.object(Path, "write_text"),
            patch("os.replace"),
            patch.object(manager, "scan_client_extensions", return_value=[]),
            patch.object(manager, "update_portal_ext") as mock_update,
        ):
            manager.write_docker_compose(paths, config)
            compose_call = mock_yaml.call_args[0][0]
            # Verify container_name is NOT set
            self.assertNotIn("container_name", compose_call["services"]["liferay"])
            # Verify state volume is NOT mounted
            volumes = compose_call["services"]["liferay"]["volumes"]
            self.assertFalse(any("/opt/liferay/osgi/state" in v for v in volumes))
            # Verify clustering properties are applied
            update_call = mock_update.call_args[0][1]
            self.assertEqual(update_call["cluster.link.enabled"], "true")
            self.assertEqual(update_call["lucene.replicate.write"], "true")


class TestStackNetwork(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.paths = self.manager.setup_paths("/tmp/test-project")

    @patch("ldm_core.handlers.stack.get_compose_cmd")
    @patch("ldm_core.handlers.stack.run_command")
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
            patch.object(
                self.manager, "write_docker_compose", return_value=({}, False)
            ),
        ):
            # Verify network is ensured regardless of configuration
            self.manager.sync_stack(self.paths, project_meta, no_up=True)
            mock_ensure.assert_called_once_with()

    @patch("ldm_core.handlers.stack.get_compose_cmd")
    @patch("ldm_core.handlers.stack.run_command")
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
            patch.object(
                self.manager, "write_docker_compose", return_value=({}, False)
            ),
        ):
            # This should NOT raise TypeError: int() argument must be... not 'NoneType'
            try:
                self.manager.sync_stack(self.paths, project_meta, no_up=True)
            except TypeError as e:
                self.fail(f"sync_stack raised TypeError with None meta values: {e}")


class TestStackOrchestration(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.paths = self.manager.setup_paths("/tmp/test-project")

    @patch("ldm_core.handlers.stack.dict_to_yaml")
    def test_write_docker_compose_ssl_labels(self, mock_yaml):
        mock_yaml.side_effect = lambda x, indent=0: str(x)

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
            patch.object(Path, "write_text"),
            patch("os.replace"),
            patch.object(self.manager, "scan_client_extensions", return_value=[]),
        ):
            self.manager.write_docker_compose(self.paths, config)
            compose_call = mock_yaml.call_args[0][0]
            labels = compose_call["services"]["liferay"]["labels"]

            if not any("tls.domains[0].main=forge.demo" in label for label in labels):
                print(f"\nDEBUG: Generated Labels: {labels}")

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

    @patch("ldm_core.handlers.stack.dict_to_yaml")
    def test_microservice_port_resolution(self, mock_yaml):
        mock_yaml.side_effect = lambda x, indent=0: str(x)

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
            patch.object(Path, "write_text"),
            patch("os.replace"),
            patch.object(
                self.manager, "scan_client_extensions", return_value=mock_exts
            ),
        ):
            self.manager.write_docker_compose(self.paths, config)
            compose_call = mock_yaml.call_args[0][0]

            # Verify Traefik service label uses the targetPort
            ms_labels = compose_call["services"]["test-my-ms"]["labels"]
            self.assertIn(
                "traefik.http.services.test-my-ms-svc.loadbalancer.server.port=3001",
                ms_labels,
            )

    @patch("ldm_core.handlers.stack.dict_to_yaml")
    def test_jvm_args_override(self, mock_yaml):
        mock_yaml.side_effect = lambda x, indent=0: str(x)

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
            patch.object(Path, "write_text"),
            patch("os.replace"),
            patch.object(self.manager, "scan_client_extensions", return_value=[]),
        ):
            self.manager.write_docker_compose(self.paths, config)
            compose_call = mock_yaml.call_args[0][0]

            # Verify JVM args are present in environment
            liferay_env = compose_call["services"]["liferay"]["environment"]
            jvm_opts_env = next(
                (e for e in liferay_env if e.startswith("LIFERAY_JVM_OPTS=")), None
            )
            self.assertIsNotNone(jvm_opts_env)
            self.assertIn("-Xms4g -Xmx4g", jvm_opts_env)
            self.assertIn("-XX:TieredStopAtLevel=1", jvm_opts_env)

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

    @patch("ldm_core.handlers.stack.run_command")
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

    @patch("ldm_core.handlers.stack.open_browser")
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
