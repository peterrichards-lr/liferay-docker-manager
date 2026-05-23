import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.config import ConfigService


class MockConfigManager:
    def __init__(self):
        class Args:
            def __init__(self):
                self.vars = []
                self.remove = False
                self.import_env = False
                self.project = None
                self.global_level = False

        self.args = Args()
        self.verbose = False
        self.non_interactive = True

        from ldm_core.defaults import DefaultsManager

        self.defaults = DefaultsManager()

    def update_portal_ext(self, *args, **kwargs):
        pass

    def detect_project_path(self, *args, **kwargs):
        pass

    def read_meta(self, *args, **kwargs):
        pass

    def write_meta(self, *args, **kwargs):
        pass

    def sync_stack(self, *args, **kwargs):
        pass

    def setup_paths(self, root):
        return {"root": Path(root)}

    def get_host_passthrough_env(self, *args, **kwargs):
        return []


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

    def test_sync_common_assets_fast_login(self):
        """Verify fast-login properties are set."""
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
            project_meta = {"fast_login": "true", "db_type": "mysql"}
            host_updates: dict[str, str] = {}

            with patch("ldm_core.ui.UI.warning") as mock_warning:
                self.config.sync_common_assets(
                    paths, project_meta=project_meta, host_updates=host_updates
                )
                for call in mock_warning.call_args_list:
                    self.assertNotIn("Hypersonic", call[0][0])

            self.assertEqual(
                host_updates["passwords.default.policy.change.required"], "false"
            )
            self.assertEqual(host_updates["setup.wizard.enabled"], "false")
            self.assertEqual(host_updates["terms.of.use.required"], "false")

    def test_sync_common_assets_fast_login_hypersonic_warning(self):
        """Verify fast-login warns if used with Hypersonic database."""
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
            project_meta = {"fast_login": "true", "db_type": "hypersonic"}
            host_updates: dict[str, str] = {}

            with patch("ldm_core.ui.UI.warning") as mock_warning:
                self.config.sync_common_assets(
                    paths, project_meta=project_meta, host_updates=host_updates
                )
                hypersonic_warnings = [
                    call
                    for call in mock_warning.call_args_list
                    if "Hypersonic" in call[0][0]
                ]
                self.assertEqual(len(hypersonic_warnings), 1)

    def test_sync_common_assets_feature_flags(self):
        """Verify feature flags are correctly injected into portal properties."""
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
            project_meta = {"features": "LPS-122920, dev, beta"}
            host_updates: dict[str, str] = {}

            self.config.sync_common_assets(
                paths, project_meta=project_meta, host_updates=host_updates
            )

            self.assertEqual(host_updates["feature.flag.LPS-122920"], "true")
            self.assertEqual(host_updates["feature.flag.ui.visible[dev]"], "true")
            self.assertEqual(host_updates["feature.flag.ui.visible[beta]"], "true")

    def test_sync_common_assets_global_features(self):
        """Verify global feature defaults are merged with project features."""
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
            project_meta = {"features": "LPS-1, dev"}
            host_updates: dict[str, str] = {}

            # Mock get_global_config to return some global features
            with patch.object(self.config, "get_global_config") as mock_global:
                mock_global.return_value = {"features": "LPS-2, beta"}

                self.config.sync_common_assets(
                    paths, project_meta=project_meta, host_updates=host_updates
                )

            # Should have both global and project features
            self.assertEqual(host_updates["feature.flag.LPS-1"], "true")
            self.assertEqual(host_updates["feature.flag.LPS-2"], "true")
            self.assertEqual(host_updates["feature.flag.ui.visible[dev]"], "true")
            self.assertEqual(host_updates["feature.flag.ui.visible[beta]"], "true")

    def test_cmd_feature(self):
        """Verify cmd_feature manages and lists feature flags."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "files").mkdir()

            # Setup paths for MockConfigManager
            self.manager.detect_project_path = MagicMock(return_value=tmp_path)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(  # type: ignore[method-assign]
                return_value={"features": "dev,LPS-122920"}
            )
            self.manager.write_meta = MagicMock()  # type: ignore[method-assign]

            # Test 1: Listing (no enable/disable)
            with patch("ldm_core.ui.UI.raw") as mock_raw:
                self.config.cmd_feature("project")
                # Verify dev and LPS were listed
                raw_outputs = [str(call[0][0]) for call in mock_raw.call_args_list]
                self.assertTrue(any("dev" in out for out in raw_outputs))
                self.assertTrue(any("LPS-122920" in out for out in raw_outputs))

            # Test 2: Enable
            self.config.cmd_feature("project", enable=["beta", "LPS-178642"])
            self.manager.write_meta.assert_called()
            updated_meta = self.manager.write_meta.call_args[0][1]
            features = updated_meta["features"].split(",")
            self.assertIn("dev", features)
            self.assertIn("LPS-122920", features)
            self.assertIn("beta", features)
            self.assertIn("LPS-178642", features)

            # Test 3: Disable
            self.manager.read_meta.return_value = {"features": "dev,beta,LPS-122920"}  # type: ignore[method-assign]
            self.config.cmd_feature("project", disable=["LPS-122920"])
            updated_meta = self.manager.write_meta.call_args[0][1]
            features = updated_meta["features"].split(",")
            self.assertIn("dev", features)
            self.assertIn("beta", features)
            self.assertNotIn("LPS-122920", features)

    def test_cmd_env_parsing_robustness(self):
        """Verify that cmd_env handles various custom_env formats safely."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "files").mkdir()

            self.manager.detect_project_path = MagicMock(return_value=tmp_path)  # type: ignore[method-assign]
            self.manager.write_meta = MagicMock()  # type: ignore[method-assign]
            self.manager.sync_stack = MagicMock()  # type: ignore[method-assign]

            # Case 1: Empty string + Batch Update
            self.manager.non_interactive = True
            self.manager.args.vars = ["KEY=VAL"]
            self.manager.read_meta = MagicMock(return_value={"custom_env": ""})  # type: ignore[method-assign]

            self.config.cmd_env("project")

            self.manager.write_meta.assert_called()
            updated_meta = self.manager.write_meta.call_args[0][1]
            print(f"DEBUG Case 1: {updated_meta}")
            self.assertEqual(json.loads(updated_meta["custom_env"]), {"KEY": "VAL"})

            # Case 2: Legacy comma-separated format + Interactive Update
            self.manager.read_meta = MagicMock(  # type: ignore[method-assign]
                return_value={"custom_env": "KEY1=VAL1,KEY2=VAL2"}
            )
            self.manager.non_interactive = False
            self.manager.args.vars = []

            # We mock the interactive UI.ask sequence:
            # 1. First UI.ask returns 'KEY1'
            # 2. Second UI.ask returns 'VAL3'
            with patch("ldm_core.ui.UI.ask", side_effect=["KEY1", "VAL3"]):
                self.config.cmd_env("project")

                updated_meta = self.manager.write_meta.call_args[0][1]
                updated_env = json.loads(updated_meta["custom_env"])
                self.assertEqual(updated_env["KEY1"], "VAL3")
                self.assertEqual(updated_env["KEY2"], "VAL2")

            # Case 3: Proper JSON + Batch Update
            self.manager.non_interactive = True
            self.manager.args.vars = ["KEY3=VAL3"]
            self.manager.read_meta = MagicMock(  # type: ignore[method-assign]
                return_value={"custom_env": '{"KEY1": "VAL1"}'}
            )
            self.config.cmd_env("project")

            updated_meta = self.manager.write_meta.call_args[0][1]
            updated_env = json.loads(updated_meta["custom_env"])
            self.assertEqual(updated_env["KEY1"], "VAL1")
            self.assertEqual(updated_env["KEY3"], "VAL3")

    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.raw")
    def test_cmd_defaults(self, mock_raw, mock_success, mock_info):
        self.manager.args.global_level = False
        self.manager.args.remove = False

        # Test viewing defaults
        self.config.cmd_defaults()
        mock_info.assert_any_call("======================")

        # Test setting a default
        self.config.cmd_defaults("tag", "2024.q1.4-lts")
        mock_success.assert_called_with("Set user default 'tag' to '2024.q1.4-lts'.")

        # Test removing a default
        self.manager.args.remove = True
        self.config.cmd_defaults("tag")
        mock_success.assert_called_with("Removed user default 'tag'.")


if __name__ == "__main__":
    unittest.main()  # type: ignore[misc]
