import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.stack import StackHandler


class MockManager(StackHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

    # Mocking BaseHandler methods that StackHandler might use
    def scan_client_extensions(self, *args, **kwargs):
        return []

    def scan_standalone_services(self, *args, **kwargs):
        return []

    def get_host_passthrough_env(self, *args, **kwargs):
        return []

    def setup_ssl(self, *args, **kwargs):
        pass

    def setup_infrastructure(self, *args, **kwargs):
        pass

    def setup_global_search(self, *args, **kwargs):
        pass

    def sync_common_assets(self, *args, **kwargs):
        pass

    def sync_logging(self, *args, **kwargs):
        pass

    def migrate_layout(self, *args, **kwargs):
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


class TestStackScaling(unittest.TestCase):
    @patch("ldm_core.handlers.stack.dict_to_yaml")
    @patch("ldm_core.handlers.stack.get_docker_socket_path")
    def test_generate_compose_with_scale(self, mock_socket, mock_yaml):
        mock_socket.return_value = "/var/run/docker.sock"
        mock_yaml.side_effect = lambda x: str(x)

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
        ):
            manager.write_docker_compose(paths, config)
            compose_call = mock_yaml.call_args[0][0]
            # Verify container_name is NOT set
            self.assertNotIn("container_name", compose_call["services"]["liferay"])
            # Verify state volume is NOT mounted
            volumes = compose_call["services"]["liferay"]["volumes"]
            self.assertFalse(any("/opt/liferay/osgi/state" in v for v in volumes))
            # Verify clustering env vars
            env = compose_call["services"]["liferay"]["environment"]
            self.assertIn("LIFERAY_CLUSTER__LINK__ENABLED=true", env)


if __name__ == "__main__":
    unittest.main()
