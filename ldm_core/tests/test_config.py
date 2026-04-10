import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.config import ConfigHandler
from ldm_core.handlers.diagnostics import DiagnosticsHandler


class MockConfigManager(ConfigHandler, DiagnosticsHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False

    def get_common_dir(self, project_path=None):
        return Path("/tmp/work/common")

    def read_meta(self, *args, **kwargs):
        return {}

    def write_meta(self, *args, **kwargs):
        pass


class TestConfigManagement(unittest.TestCase):
    def setUp(self):
        self.manager = MockConfigManager()
        self.paths = {
            "root": Path("/tmp/test-project"),
            "files": Path("/tmp/test-project/files"),
        }

    @patch("pathlib.Path.cwd")
    @patch("importlib.resources.files")
    def test_cmd_init_common(self, mock_pkg_files, mock_cwd):
        mock_cwd.return_value = Path("/tmp/work")

        # Mock the resource files
        mock_resource = MagicMock()
        mock_resource.read_text.return_value = "baseline content"
        mock_pkg_files.return_value.__truediv__.return_value.__truediv__.return_value = mock_resource

        with (
            patch.object(Path, "mkdir"),
            patch.object(Path, "write_text") as mock_write,
        ):
            with patch.object(Path, "exists", return_value=False):
                self.manager.cmd_init_common()

                # Verify it attempted to write at least one baseline file
                self.assertTrue(mock_write.called)
                # Verify it used the CWD for the common folder
                args, _ = mock_write.call_args_list[0]
                self.assertTrue(any("baseline content" in str(arg) for arg in args))

    def test_sync_common_assets_mandatory_updates(self):
        target_ext = self.paths["files"] / "portal-ext.properties"
        host_updates = {"web.server.host": "forge.demo"}

        with patch.object(Path, "exists", return_value=False):
            with patch.object(self.manager, "update_portal_ext") as mock_update:
                self.manager.sync_common_assets(self.paths, host_updates=host_updates)

                # Even if common/ doesn't exist, host_updates should be applied
                mock_update.assert_called_with(target_ext, host_updates)

    @patch("ldm_core.handlers.config.get_actual_home")
    def test_cmd_config_get_set(self, mock_home):
        mock_home.return_value = Path("/tmp/home")
        self.manager.args.remove = False

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value='{"verbose": "true"}'),
            patch.object(Path, "write_text") as mock_write,
        ):
            # 1. Test Get
            with patch("builtins.print") as mock_print:
                self.manager.cmd_config("verbose")
                mock_print.assert_called_with("true")

            # 2. Test Set
            self.manager.cmd_config("verbose", "false")
            self.assertTrue(mock_write.called)
            write_call = mock_write.call_args[0][0]
            self.assertIn('"verbose": "false"', write_call)

    @patch("ldm_core.handlers.config.get_actual_home")
    @patch("os.remove")
    def test_cmd_cache_clear(self, mock_remove, mock_home):
        mock_home.return_value = Path("/tmp/home")

        with patch.object(Path, "exists", return_value=True):
            self.manager.cmd_cache(target="tags")
            # Verify tag cache removal
            self.assertTrue(mock_remove.called)


if __name__ == "__main__":
    unittest.main()
