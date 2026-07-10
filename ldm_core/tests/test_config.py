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
                self.reset = False
                self.no_restart = False

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

    def verify_runtime_environment(self, *args, **kwargs):
        pass

    def cmd_stop(self, *args, **kwargs):
        pass

    def cmd_run(self, *args, **kwargs):
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

    def test_get_properties_escaped_backslash(self):
        # Even number of backslashes = escaped, not a continuation.
        content = "key1=val1\\\\\nkey2=val2"
        props = self.config._get_properties(content)
        self.assertEqual(props["key1"], "val1\\\\")
        self.assertEqual(props["key2"], "val2")

        # Odd number of backslashes = active continuation.
        content = "key1=val1\\\\\\\n    continued\nkey2=val2"
        props = self.config._get_properties(content)
        self.assertEqual(props["key1"], "val1\\\\\\\n    continued")
        self.assertEqual(props["key2"], "val2")

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

    @patch("ldm_core.handlers.config.safe_write_text")
    def test_update_portal_ext_multiline_whitespace(self, mock_write):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            pe_path = Path(tmp_dir) / "portal-ext.properties"
            # Note the trailing space after the first backslash
            pe_path.write_text("multiline=val1\\ \n    val2\\\n    val3\nkey2=val2")

            self.config.update_portal_ext(pe_path, {"multiline": "new"})
            self.assertTrue(mock_write.called)
            content = mock_write.call_args[0][1]
            self.assertIn("multiline=new", content)
            self.assertIn("key2=val2", content)
            # Verify continuation lines were correctly skipped and not duplicated
            lines = content.splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], "multiline=new")
            self.assertEqual(lines[1], "key2=val2")

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

    def test_sync_common_assets_preferred_admin(self):
        """Verify global admin preferences are merged into host_updates."""
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
            host_updates: dict[str, str] = {}

            with patch.object(self.config, "get_global_config") as mock_global:
                mock_global.return_value = {
                    "admin_password": "secretpassword",  # pragma: allowlist secret
                    "admin_first_name": "John",
                    "admin_last_name": "Doe",
                }

                self.config.sync_common_assets(paths, host_updates=host_updates)

            self.assertEqual(host_updates["default.admin.password"], "secretpassword")
            self.assertEqual(host_updates["default.admin.first.name"], "John")
            self.assertEqual(host_updates["default.admin.last.name"], "Doe")
            self.assertNotIn("default.admin.middle.name", host_updates)

    def test_sync_common_assets_smart_merge(self):
        """Verify global common portal properties override vanilla defaults but not project overrides."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            files_dir = tmp_path / "files"
            files_dir.mkdir(parents=True)
            common_dir = tmp_path / "common"
            common_dir.mkdir(parents=True)

            project_pe = files_dir / "portal-ext.properties"
            common_pe = common_dir / "portal-ext.properties"

            paths = {
                "root": tmp_path,
                "configs": tmp_path / "osgi" / "configs",
                "files": files_dir,
                "common": common_dir,
                "deploy": tmp_path / "deploy",
            }
            self.manager.update_portal_ext = self.config.update_portal_ext  # type: ignore[method-assign]

            # 1. Scenario: Overwrite vanilla default value
            project_pe.write_text("default.admin.password=test\n")
            common_pe.write_text("default.admin.password=D3mo!\n")

            self.config.sync_common_assets(paths)

            # Since the project password matched the baseline default "test", it should be overridden by "D3mo!"
            project_props = self.config._get_properties(project_pe.read_text())
            self.assertEqual(project_props["default.admin.password"], "D3mo!")

            # 2. Scenario: Do not overwrite custom local override
            project_pe.write_text("default.admin.password=myprojpass\n")
            common_pe.write_text("default.admin.password=D3mo!\n")

            self.config.sync_common_assets(paths)

            # Since the project password was "myprojpass" (different from baseline default "test"), it should not be overridden
            project_props = self.config._get_properties(project_pe.read_text())
            self.assertEqual(project_props["default.admin.password"], "myprojpass")

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

    @patch("os.environ.get")
    @patch("ldm_core.handlers.config.ConfigService.get_global_config")
    def test_get_ngrok_auth_token_env(self, mock_get_global, mock_env_get):
        mock_env_get.return_value = "env-token"
        token = self.config.get_ngrok_auth_token()
        self.assertEqual(token, "env-token")
        mock_env_get.assert_called_with("NGROK_AUTHTOKEN")

    @patch("os.environ.get")
    @patch("ldm_core.handlers.config.ConfigService.get_global_config")
    def test_get_ngrok_auth_token_config(self, mock_get_global, mock_env_get):
        mock_env_get.return_value = None
        mock_get_global.return_value = {"ngrok_authtoken": "config-token"}
        token = self.config.get_ngrok_auth_token()
        self.assertEqual(token, "config-token")

    @patch("ldm_core.handlers.config.ConfigService.get_global_config")
    @patch("ldm_core.utils.save_global_config_safe")
    def test_set_ngrok_auth_token(self, mock_save_config, mock_get_global):
        mock_get_global.return_value = {}
        self.config.set_ngrok_auth_token("new-token")
        self.assertTrue(mock_save_config.called)
        written_data = mock_save_config.call_args[0][1]
        self.assertEqual(written_data["ngrok_authtoken"], "new-token")

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.detail")
    @patch("ldm_core.handlers.config.ConfigService.get_global_config")
    @patch("ldm_core.handlers.config.ConfigService.set_global_config")
    def test_track_roi(self, mock_set, mock_get, mock_detail, mock_success):
        mock_get.return_value = {"roi_seconds_saved": 100}

        run_sec, cumulative = self.config.track_roi(200, "test-action")
        self.assertEqual(run_sec, 200)
        self.assertEqual(cumulative, 300)
        mock_set.assert_called_with("roi_seconds_saved", 300)
        mock_success.assert_called_once()
        mock_detail.assert_called_once()

    @patch("ldm_core.ui.UI.heading")
    @patch("ldm_core.ui.UI.raw")
    @patch("ldm_core.handlers.config.ConfigService.get_global_config")
    def test_cmd_roi(self, mock_get, mock_raw, mock_heading):
        mock_get.return_value = {"roi_seconds_saved": 3600}
        self.manager.args.reset = False

        self.config.cmd_roi()
        mock_heading.assert_called_once_with("LDM Developer Productivity ROI")
        mock_raw.assert_any_call(
            "  ● \x1b[0;37mCumulative Time Saved: \x1b[0;32m\x1b[1m1h 0m 0s\x1b[0m"
        )

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.handlers.config.ConfigService.set_global_config")
    def test_cmd_roi_reset(self, mock_set, mock_success):
        self.manager.args.reset = True

        self.config.cmd_roi()
        mock_set.assert_called_with("roi_seconds_saved", 0)
        mock_success.assert_called_with("ROI metrics reset successfully.")

    def test_sync_common_assets_cascade_and_important(self):
        """Verify the 5-layer properties merge cascade and !important override precedence."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            files_dir = tmp_path / "files"
            files_dir.mkdir(parents=True)

            paths = {
                "root": tmp_path,
                "configs": tmp_path / "osgi" / "configs",
                "files": files_dir,
                "common_dirs": [],
                "deploy": tmp_path / "deploy",
            }

            global_common = tmp_path / "global_common"
            global_common.mkdir()
            local_common = tmp_path / "local_common"
            local_common.mkdir()

            paths["common_dirs"] = [global_common, local_common]
            target_pe = files_dir / "portal-ext.properties"

            self.manager.update_portal_ext = self.config.update_portal_ext  # type: ignore[method-assign]

            global_pe_content = "default.admin.password=global_val\n"
            local_pe_content = "default.admin.password=local_val\n"

            (global_common / "portal-ext.properties").write_text(global_pe_content)
            (local_common / "portal-ext.properties").write_text(local_pe_content)

            self.config.sync_common_assets(paths)

            project_props, _ = self.config._get_properties_with_metadata(
                target_pe.read_text()
            )
            self.assertEqual(project_props["default.admin.password"], "local_val")

            global_pe_content = (
                "# !important\ndefault.admin.password=global_important_val\n"
            )
            (global_common / "portal-ext.properties").write_text(global_pe_content)

            self.config.sync_common_assets(paths)

            project_props, project_imp = self.config._get_properties_with_metadata(
                target_pe.read_text()
            )
            self.assertEqual(
                project_props["default.admin.password"], "global_important_val"
            )
            self.assertIn("default.admin.password", project_imp)

    def test_cmd_rebuild_revert_reset_properties(self):
        """Verify CLI subcommand handlers: rebuild, revert, and reset properties."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            files_dir = tmp_path / "files"
            files_dir.mkdir(parents=True)

            paths = {
                "root": tmp_path,
                "configs": tmp_path / "osgi" / "configs",
                "files": files_dir,
                "common_dirs": [],
                "deploy": tmp_path / "deploy",
            }

            self.manager.detect_project_path = MagicMock(return_value=tmp_path)  # type: ignore[method-assign]
            self.manager.setup_paths = MagicMock(return_value=paths)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(return_value={})  # type: ignore[method-assign]
            self.manager.verify_runtime_environment = MagicMock()  # type: ignore[method-assign]

            ldm_dir = tmp_path / ".liferay-docker"
            ldm_dir.mkdir()
            orig_pe = ldm_dir / "original-portal-ext.properties"
            target_pe = files_dir / "portal-ext.properties"

            orig_pe.write_text("my.prop=original\n")
            target_pe.write_text("my.prop=customized\n")

            self.config.cmd_revert_properties("project")
            self.assertEqual(target_pe.read_text().strip(), "my.prop=original")

    def test_validate_properties_success(self):
        """Test properties validation with correct settings."""
        paths = {
            "root": Path("/tmp"),
            "deploy": MagicMock(exists=MagicMock(return_value=True)),
            "files": MagicMock(exists=MagicMock(return_value=True)),
        }
        props = {
            "jdbc.default.driverClassName": "org.postgresql.Driver",
            "jdbc.default.url": "jdbc:postgresql://localhost:5432/lportal",
            "normal.prop": "value",
            "quoted.prop": '"valid quoted value"',
        }
        meta = {"db_type": "postgresql"}
        # Should not raise any exception
        self.config.validate_properties(paths, props, meta, is_dry_run=True)

    def test_validate_properties_unclosed_quotes(self):
        """Test that unclosed quotes trigger validation error."""
        paths = {
            "root": Path("/tmp"),
        }
        props = {
            "my.prop": '"unclosed val',
        }
        with self.assertRaises(SystemExit):
            self.config.validate_properties(paths, props, {}, is_dry_run=True)

    def test_validate_properties_malformed_url(self):
        """Test that malformed JDBC URLs trigger validation error."""
        paths = {
            "root": Path("/tmp"),
        }
        props = {
            "jdbc.default.url": "postgresql://localhost:5432/lportal",  # missing jdbc: prefix
        }
        with self.assertRaises(SystemExit):
            self.config.validate_properties(paths, props, {}, is_dry_run=True)

        props2 = {
            "jdbc.default.url": "jdbc:postgresql:localhost:5432/lportal",  # missing //
        }
        with self.assertRaises(SystemExit):
            self.config.validate_properties(paths, props2, {}, is_dry_run=True)

        props3 = {
            "jdbc.default.url": "jdbc:postgresql://localhost:5432/lportal[param",  # mismatched brackets
        }
        with self.assertRaises(SystemExit):
            self.config.validate_properties(paths, props3, {}, is_dry_run=True)

    def test_validate_properties_conflicting_db(self):
        """Test that conflicting database types trigger validation error."""
        paths = {
            "root": Path("/tmp"),
        }
        # project is hypersonic, but properties define postgresql driver
        props = {
            "jdbc.default.driverClassName": "org.postgresql.Driver",
            "jdbc.default.url": "jdbc:postgresql://localhost:5432/lportal",
        }
        meta = {"db_type": "hypersonic"}
        with self.assertRaises(SystemExit):
            self.config.validate_properties(paths, props, meta, is_dry_run=True)

        # project is postgresql, but properties define hypersonic driver
        props2 = {
            "jdbc.default.driverClassName": "org.hsqldb.jdbc.JDBCDriver",
            "jdbc.default.url": "jdbc:hsqldb:mem:lportal",
        }
        meta2 = {"db_type": "postgresql"}
        with self.assertRaises(SystemExit):
            self.config.validate_properties(paths, props2, meta2, is_dry_run=True)

        # Mismatch within properties (PostgreSQL driver with HSQLDB URL)
        props3 = {
            "jdbc.default.driverClassName": "org.postgresql.Driver",
            "jdbc.default.url": "jdbc:hsqldb:mem:lportal",
        }
        with self.assertRaises(SystemExit):
            self.config.validate_properties(paths, props3, {}, is_dry_run=True)

    def test_validate_properties_missing_mounts_dry_run(self):
        """Test that missing mount paths are reported as warnings in dry run."""
        paths = {
            "root": Path("/tmp"),
            "deploy": MagicMock(exists=MagicMock(return_value=False)),
            "files": MagicMock(exists=MagicMock(return_value=False)),
        }
        with patch("ldm_core.ui.UI.warning") as mock_warn:
            self.config.validate_properties(paths, {}, {}, is_dry_run=True)
            self.assertTrue(mock_warn.called)

    def test_validate_properties_missing_mounts_creation(self):
        """Test that missing mount paths are created in normal run."""
        mock_deploy = MagicMock()
        mock_deploy.exists.return_value = False
        paths = {
            "root": Path("/tmp"),
            "deploy": mock_deploy,
        }
        with patch("ldm_core.ui.UI.info") as mock_info:
            self.config.validate_properties(paths, {}, {}, is_dry_run=False)
            mock_deploy.mkdir.assert_called_once_with(parents=True, exist_ok=True)
            mock_info.assert_called_with(
                f"Created missing mount directory: {mock_deploy}"
            )

    @patch("builtins.input")
    def test_cmd_edit_tui(self, mock_input):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            files_dir = tmp_path / "files"
            files_dir.mkdir(parents=True)

            paths = {
                "root": tmp_path,
                "configs": tmp_path / "osgi" / "configs",
                "files": files_dir,
                "common_dirs": [],
                "deploy": tmp_path / "deploy",
            }

            self.manager.detect_project_path = MagicMock(return_value=tmp_path)  # type: ignore[method-assign]
            self.manager.setup_paths = MagicMock(return_value=paths)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(return_value={})  # type: ignore[method-assign]

            # Setup input sequence
            mock_input.side_effect = [
                "a",
                "prop.one",
                "val.one",
                "y",
                "e",
                "1",
                "val.one.edited",
                "n",
                "t",
                "1",
                "d",
                "1",
                "y",
                "q",
            ]

            # Invoke cmd_edit in TUI mode
            self.config.cmd_edit("project", target="properties", tui=True)

            target_pe = files_dir / "portal-ext.properties"
            self.assertTrue(target_pe.exists())
            self.assertEqual(target_pe.read_text().strip(), "")

    @patch("subprocess.run")
    def test_cmd_ssl_mode_hosts(self, mock_run):
        """Verify cmd_ssl_mode configures project for hosts-based SSL and syncs .env files."""
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock

        # Mock subprocess.run to return Running=true
        mock_inspect = MagicMock()
        mock_inspect.stdout = "true\n"
        mock_run.return_value = mock_inspect

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            files_dir = tmp_path / "files"
            files_dir.mkdir(parents=True)

            # Create a mock env file
            env_file = tmp_path / "client-extensions" / "my-cx" / ".env"
            env_file.parent.mkdir(parents=True)
            env_file.write_text(
                "LIFERAY_URL=http://localhost:8080\nAICA_LIFERAY_URL=http://localhost:8080\n"
            )

            paths = {
                "root": tmp_path,
                "configs": tmp_path / "osgi" / "configs",
                "files": files_dir,
                "common_dirs": [],
                "deploy": tmp_path / "deploy",
            }

            self.manager.detect_project_path = MagicMock(return_value=tmp_path)  # type: ignore[method-assign]
            self.manager.setup_paths = MagicMock(return_value=paths)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(  # type: ignore[method-assign]
                return_value={"container_name": "test-c"}
            )
            self.manager.write_meta = MagicMock()  # type: ignore[method-assign]
            self.manager.cmd_stop = MagicMock()  # type: ignore[method-assign]
            self.manager.cmd_run = MagicMock()  # type: ignore[method-assign]
            self.config.cmd_rebuild_properties = MagicMock()  # type: ignore[method-assign]
            self.manager.args.no_restart = False

            # Run ssl-mode hosts
            self.config.cmd_ssl_mode("hosts", project_id="test-p")

            # Check meta write
            self.manager.write_meta.assert_called_once()
            meta_arg = self.manager.write_meta.call_args[0][1]
            self.assertEqual(meta_arg["ssl"], "true")
            self.assertEqual(meta_arg["host_name"], f"{tmp_path.name}.local")

            # Check .env sync
            env_content = env_file.read_text()
            self.assertIn(f"LIFERAY_URL=https://{tmp_path.name}.local", env_content)
            self.assertIn(
                f"AICA_LIFERAY_URL=https://{tmp_path.name}.local", env_content
            )

            # Check stop/start and rebuild
            self.manager.cmd_stop.assert_called_once_with(project_id=tmp_path.name)
            self.manager.cmd_run.assert_called_once_with(project_id=tmp_path.name)
            self.config.cmd_rebuild_properties.assert_called_once_with(tmp_path.name)

    @patch("subprocess.run")
    def test_cmd_ssl_mode_share(self, mock_run):
        """Verify cmd_ssl_mode configures project for share tunnel routing."""
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock

        # Mock subprocess.run to return Running=true
        mock_inspect = MagicMock()
        mock_inspect.stdout = "true\n"
        mock_run.return_value = mock_inspect

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            files_dir = tmp_path / "files"
            files_dir.mkdir(parents=True)

            # Create a mock env file
            env_file = tmp_path / ".env"
            env_file.write_text("LIFERAY_PORTAL_URL=http://localhost:8080\n")

            paths = {
                "root": tmp_path,
                "configs": tmp_path / "osgi" / "configs",
                "files": files_dir,
                "common_dirs": [],
                "deploy": tmp_path / "deploy",
            }

            self.manager.detect_project_path = MagicMock(return_value=tmp_path)  # type: ignore[method-assign]
            self.manager.setup_paths = MagicMock(return_value=paths)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(return_value={})  # type: ignore[method-assign]
            self.manager.write_meta = MagicMock()  # type: ignore[method-assign]
            self.manager.cmd_stop = MagicMock()  # type: ignore[method-assign]
            self.manager.cmd_run = MagicMock()  # type: ignore[method-assign]
            self.config.cmd_rebuild_properties = MagicMock()  # type: ignore[method-assign]
            self.manager.args.no_restart = False

            # Run ssl-mode share
            self.config.cmd_ssl_mode(
                "share",
                project_id="test-p",
                subdomain="pjrsub",
                domain="lfr-demo.online",
            )

            # Check meta write
            self.manager.write_meta.assert_called_once()
            meta_arg = self.manager.write_meta.call_args[0][1]
            self.assertEqual(meta_arg["ssl"], "false")
            self.assertEqual(meta_arg["host_name"], "localhost")
            self.assertEqual(meta_arg["share_subdomain"], "pjrsub")
            self.assertEqual(meta_arg["share_domain"], "lfr-demo.online")

            # Check .env sync
            env_content = env_file.read_text()
            self.assertIn(
                "LIFERAY_PORTAL_URL=https://pjrsub.lfr-demo.online", env_content
            )

            # Check stop/start and rebuild
            self.manager.cmd_stop.assert_called_once_with(project_id=tmp_path.name)
            self.manager.cmd_run.assert_called_once_with(project_id=tmp_path.name)
            self.config.cmd_rebuild_properties.assert_called_once_with(tmp_path.name)

    @patch("subprocess.run")
    def test_cmd_ssl_mode_no_restart(self, mock_run):
        """Verify cmd_ssl_mode bypasses container restarts when --no-restart is set."""
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock

        # Mock subprocess.run to return Running=true
        mock_inspect = MagicMock()
        mock_inspect.stdout = "true\n"
        mock_run.return_value = mock_inspect

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            files_dir = tmp_path / "files"
            files_dir.mkdir(parents=True)

            paths = {
                "root": tmp_path,
                "configs": tmp_path / "osgi" / "configs",
                "files": files_dir,
                "common_dirs": [],
                "deploy": tmp_path / "deploy",
            }

            self.manager.detect_project_path = MagicMock(return_value=tmp_path)  # type: ignore[method-assign]
            self.manager.setup_paths = MagicMock(return_value=paths)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(return_value={})  # type: ignore[method-assign]
            self.manager.write_meta = MagicMock()  # type: ignore[method-assign]
            self.manager.cmd_stop = MagicMock()  # type: ignore[method-assign]
            self.manager.cmd_run = MagicMock()  # type: ignore[method-assign]
            self.config.cmd_rebuild_properties = MagicMock()  # type: ignore[method-assign]

            # Force no_restart on args
            self.manager.args.no_restart = True

            # Run ssl-mode hosts
            self.config.cmd_ssl_mode("hosts", project_id="test-p")

            # Check stop/start were NOT called
            self.manager.cmd_stop.assert_not_called()
            self.manager.cmd_run.assert_not_called()
            self.config.cmd_rebuild_properties.assert_called_once_with(tmp_path.name)

    def test_cmd_database_mode(self):
        """Verify cmd_database_mode views and configures database profile."""
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            self.manager.detect_project_path = MagicMock(return_value=tmp_path)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(return_value={})  # type: ignore[method-assign]
            self.manager.write_meta = MagicMock()  # type: ignore[method-assign]

            mock_defaults = MagicMock()
            mock_defaults.get.return_value = "isolated"
            mock_defaults.user_defaults = {}
            mock_defaults.global_defaults = {}
            self.manager.defaults = mock_defaults

            # 1. View defaults fallback
            self.config.cmd_database_mode()
            mock_defaults.get.assert_called_with("database_mode", "isolated")

            # 2. Set database mode locally
            self.manager.args.global_level = False
            self.config.cmd_database_mode("shared")
            self.manager.write_meta.assert_called_once()
            meta_arg = self.manager.write_meta.call_args[0][1]
            self.assertEqual(meta_arg["database_mode"], "shared")

            # 3. Set database mode globally
            self.manager.args.global_level = True
            self.config.cmd_database_mode("isolated")
            self.manager.defaults.set_user_default.assert_called_with(
                "database_mode", "isolated"
            )

    def test_sync_common_assets_checksum_caching(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            root_dir = tmp / "root"
            files_dir = tmp / "files"
            common_dir = tmp / "common"
            deploy_dir = tmp / "deploy"
            configs_dir = tmp / "configs"

            for d in [root_dir, files_dir, common_dir, deploy_dir, configs_dir]:
                d.mkdir(parents=True)

            paths = {
                "root": root_dir,
                "files": files_dir,
                "common": common_dir,
                "deploy": deploy_dir,
                "configs": configs_dir,
            }

            target_ext = files_dir / "portal-ext.properties"
            ldm_dir = root_dir / ".liferay-docker"
            manifest = ldm_dir / "properties_manifest.json"

            # Run 1: Should build target_ext and manifest
            self.handler.sync_common_assets(paths, host_updates={"test.prop": "1"})

            self.assertTrue(target_ext.exists())
            self.assertTrue(manifest.exists())

            content_1 = target_ext.read_text()
            self.assertIn("test.prop=1", content_1)

            # Run 2: Hash matches, should bypass parser completely
            with patch.object(
                self.handler, "_get_properties_with_metadata"
            ) as mock_parse:
                self.handler.sync_common_assets(paths, host_updates={"test.prop": "1"})
                mock_parse.assert_not_called()

            # Run 3: We modify a layer, it should trigger parser again
            with patch.object(
                self.handler,
                "_get_properties_with_metadata",
                wraps=self.handler._get_properties_with_metadata,
            ) as mock_parse_2:
                self.handler.sync_common_assets(paths, host_updates={"test.prop": "2"})
                mock_parse_2.assert_called()
                content_2 = target_ext.read_text()
                self.assertIn("test.prop=2", content_2)


if __name__ == "__main__":
    unittest.main()  # type: ignore[misc]
