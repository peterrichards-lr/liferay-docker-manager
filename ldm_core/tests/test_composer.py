import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.composer import ComposerHandler
from ldm_core.handlers.base import BaseHandler


from ldm_core.handlers.config import ConfigHandler


class MockComposer(ComposerHandler, ConfigHandler, BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

    def get_resolved_ip(self, host_name):
        return "127.0.0.1"

    def scan_client_extensions(self, *args, **kwargs):
        return []


class TestComposer(unittest.TestCase):
    def setUp(self):
        self.composer = MockComposer()
        self.tmp_dir = Path("/tmp/composer-test")
        self.paths = {
            "root": self.tmp_dir,
            "deploy": self.tmp_dir / "deploy",
            "files": self.tmp_dir / "files",
            "data": self.tmp_dir / "data",
            "configs": self.tmp_dir / "osgi" / "configs",
            "state": self.tmp_dir / "osgi" / "state",
            "logs": self.tmp_dir / "logs",
            "compose": self.tmp_dir / "docker-compose.yml",
            "ce_dir": self.tmp_dir / "client-extensions",
            "cx": self.tmp_dir / "osgi" / "client-extensions",
        }

    @patch("ldm_core.handlers.composer.json.loads")
    def test_get_default_jvm_args_ram_scaling(self, mock_json):
        # Test 16GB RAM detection
        mock_json.return_value = {"MemTotal": 16 * 1024 * 1024 * 1024}
        with patch.object(
            self.composer, "run_command", return_value='{"MemTotal": 17179869184}'
        ):
            args = self.composer.get_default_jvm_args()
            self.assertIn("-Xmx12288m", args)
            self.assertIn("-XX:MaxMetaspaceSize=768m", args)

        # Test 32GB RAM detection (Capped at 24GB or 32GB depending on logic, verify actual output)
        mock_json.return_value = {"MemTotal": 32 * 1024 * 1024 * 1024}
        with patch.object(
            self.composer, "run_command", return_value='{"MemTotal": 34359738368}'
        ):
            args = self.composer.get_default_jvm_args()
            # The current logic caps at min(floor(32*0.75), 32) = 24GB
            self.assertIn("-Xmx24576m", args)
            self.assertIn("-XX:MaxMetaspaceSize=1024m", args)

    def test_is_ssl_active_logic(self):
        meta = {}
        # Localhost should always be False
        self.assertFalse(self.composer._is_ssl_active("localhost", meta))

        # Custom domain should default to True
        self.assertTrue(self.composer._is_ssl_active("test.local", meta))

        # CLI Override
        self.composer.args.ssl = False
        self.assertFalse(self.composer._is_ssl_active("test.local", meta))

        # Meta Override
        self.composer.args.ssl = None
        meta["ssl"] = "true"
        self.assertTrue(self.composer._is_ssl_active("test.local", meta))

    @patch("ldm_core.handlers.composer.dict_to_yaml")
    def test_write_docker_compose_structure(self, mock_yaml):
        mock_yaml.return_value = "services: {}"
        meta = {
            "tag": "2025.q1.0",
            "db_type": "postgresql",
            "host_name": "localhost",
        }

        with (
            patch.object(Path, "write_text"),
            patch.object(self.composer, "update_portal_ext"),
        ):
            self.composer.write_docker_compose(self.paths, meta)

            # Verify update_portal_ext was called for PostgreSQL
            self.assertTrue(self.composer.update_portal_ext.called)

            # Verify YAML generation
            compose_call_data = mock_yaml.call_args[0][0]
            self.assertIn("liferay", compose_call_data["services"])
            self.assertIn("db", compose_call_data["services"])

    def test_jvm_opts_mandatory_opens(self):
        meta = {"jvm_args": "-Xmx4g"}
        with (
            patch.object(Path, "write_text"),
            patch.object(self.composer, "update_portal_ext"),
        ):
            # Capture the environment passed to the service
            with patch("ldm_core.handlers.composer.dict_to_yaml") as mock_yaml:
                self.composer.write_docker_compose(self.paths, meta)
                compose_data = mock_yaml.call_args[0][0]
                liferay_env = compose_data["services"]["liferay"]["environment"]

                jvm_opts = next(
                    e for e in liferay_env if e.startswith("LIFERAY_JVM_OPTS=")
                )
                self.assertIn("--add-opens=java.base/java.lang=ALL-UNNAMED", jvm_opts)
                self.assertIn("-Dfile.encoding=UTF8", jvm_opts)


if __name__ == "__main__":
    unittest.main()
