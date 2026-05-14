import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.config import ConfigService


class MockConfigManager:
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

    def update_portal_ext(self, *args, **kwargs):
        pass


class TestConfigService(unittest.TestCase):
    def setUp(self):
        self.manager = MockConfigManager()
        self.config = ConfigService(self.manager)

    def test_get_properties_basic(self):
        content = "key1=val1\nkey2=val2"
        props = self.config._get_properties(content)
        self.assertEqual(props["key1"], "val1")
        self.assertEqual(props["key2"], "val2")

    def test_get_properties_multiline(self):
        content = "key1=val1\\\n    continued\nkey2=val2"
        props = self.config._get_properties(content)
        self.assertIn("key1", props)

    @patch("ldm_core.handlers.config.safe_write_text")
    def test_update_portal_ext(self, mock_write):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            pe_path = Path(tmp_dir) / "portal-ext.properties"
            pe_path.write_text("key1=old")

            self.config.update_portal_ext(pe_path, {"key1": "new", "key2": "val2"})
            self.assertTrue(mock_write.called)
            # Verify the content passed to safe_write_text
            content = mock_write.call_args[0][1]
            self.assertIn("key1=new", content)
            self.assertIn("key2=val2", content)

    def test_sync_logging(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            log4j_dir = tmp_path / "log4j"
            log4j_dir.mkdir()
            target = log4j_dir / "portal-log4j-ext.xml"
            # DON'T patch safe_write_text here so it actually writes

            paths = {"root": tmp_path, "portal_log4j": log4j_dir}
            # Create logging.json
            log_json = tmp_path / "logging.json"
            log_json.write_text(json.dumps({"bundle": {"com.liferay": "DEBUG"}}))

            self.config.sync_logging(paths)

            # Check if file was written and contains the logger
            content = target.read_text()
            self.assertIn('name="com.liferay" level="DEBUG"', content)

    def test_sync_common_assets_no_captcha(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            configs_dir = tmp_path / "osgi" / "configs"
            configs_dir.mkdir(parents=True)
            files_dir = tmp_path / "files"
            files_dir.mkdir(parents=True)

            paths = {
                "root": tmp_path,
                "configs": configs_dir,
                "files": files_dir,
                "common": tmp_path / "common",
            }
            project_meta = {"no_captcha": "true"}
            host_updates: dict[str, str] = {}

            self.config.sync_common_assets(
                paths, project_meta=project_meta, host_updates=host_updates
            )

            captcha_cfg = (
                configs_dir
                / "com.liferay.captcha.configuration.CaptchaConfiguration.config"
            )
            self.assertTrue(captcha_cfg.exists())
            content = captcha_cfg.read_text()
            self.assertIn('maxChallenges=I"-1"', content)
            self.assertEqual(host_updates["captcha.enforce.disabled"], "true")

    def test_sync_common_assets_captcha_enabled(self):
        """Verify that by default (no flag), captcha is enabled."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            configs_dir = tmp_path / "osgi" / "configs"
            configs_dir.mkdir(parents=True)
            files_dir = tmp_path / "files"
            files_dir.mkdir(parents=True)

            paths = {
                "root": tmp_path,
                "configs": configs_dir,
                "files": files_dir,
                "common": tmp_path / "common",
            }
            project_meta = {"no_captcha": "false"}
            host_updates: dict[str, str] = {}

            self.config.sync_common_assets(
                paths, project_meta=project_meta, host_updates=host_updates
            )

            captcha_cfg = (
                configs_dir
                / "com.liferay.captcha.configuration.CaptchaConfiguration.config"
            )
            self.assertFalse(captcha_cfg.exists())
            self.assertEqual(host_updates["captcha.enforce.disabled"], "false")

    def test_sync_common_assets_captcha_cleanup(self):
        """Verify that running without the flag cleans up previous bypass configs."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            configs_dir = tmp_path / "osgi" / "configs"
            configs_dir.mkdir(parents=True)
            files_dir = tmp_path / "files"
            files_dir.mkdir(parents=True)

            # Pre-create the bypass file
            captcha_cfg = (
                configs_dir
                / "com.liferay.captcha.configuration.CaptchaConfiguration.config"
            )
            captcha_cfg.write_text("old-content")

            paths = {
                "root": tmp_path,
                "configs": configs_dir,
                "files": files_dir,
                "common": tmp_path / "common",
            }
            project_meta = {"no_captcha": "false"}
            host_updates: dict[str, str] = {}

            self.config.sync_common_assets(
                paths, project_meta=project_meta, host_updates=host_updates
            )

            self.assertFalse(captcha_cfg.exists())
            self.assertEqual(host_updates["captcha.enforce.disabled"], "false")


if __name__ == "__main__":
    unittest.main()
