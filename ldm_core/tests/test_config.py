import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.config import ConfigHandler
from ldm_core.handlers.diagnostics import DiagnosticsHandler
from ldm_core.handlers.base import BaseHandler


class MockConfigManager(ConfigHandler, DiagnosticsHandler, BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False

    def parse_version(self, tag):
        return (7, 4, 13)

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
            "common": Path("/tmp/work/common"),
            "deploy": Path("/tmp/test-project/deploy"),
            "configs": Path("/tmp/test-project/osgi/configs"),
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
            patch("ldm_core.utils.safe_write_text") as mock_write,
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

    @patch("ldm_core.handlers.config.shutil.copy")
    @patch("pathlib.Path.mkdir")
    @patch("ldm_core.utils.safe_write_text")
    def test_sync_common_assets_dir_creation(self, mock_write, mock_mkdir, mock_copy):

        # Setup: Target files/ dir does NOT exist, common/ DOES exist
        common_dir = Path("/tmp/common")
        self.paths["common"] = common_dir

        def exists_side_effect(self_obj):
            path_str = str(self_obj)
            if "/tmp/common" in path_str:
                return True
            return False

        with patch.object(
            Path, "exists", autospec=True, side_effect=exists_side_effect
        ):
            with patch.object(self.manager, "get_common_dir", return_value=common_dir):
                self.manager.sync_common_assets(self.paths)

                # Verify that mkdir was called for the parent of target portal-ext
                self.assertTrue(mock_mkdir.called)
                self.assertTrue(mock_copy.called)

    @patch("shutil.copy")
    @patch("ldm_core.handlers.config.run_command")
    def test_sync_common_assets_es_substitution(self, mock_run, mock_copy):
        """Verifies that ES .config files are dynamically namespaced during sync."""
        common_dir = Path("/tmp/work/common")
        es_config_name = "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConfiguration.config"
        es_config_path = common_dir / es_config_name
        target_dir = self.paths["root"] / "osgi" / "configs"

        self.paths["configs"] = target_dir
        self.paths["deploy"] = self.paths["root"] / "deploy"
        self.paths["common"] = common_dir

        mock_config_content = 'operationMode="REMOTE"\nproductionModeEnabled=B"true"'
        # Mock that ES8 is running
        mock_run.return_value = "elasticsearch:8.11.1"

        with patch.object(self.manager, "get_common_dir", return_value=common_dir):
            # 1. Satisfy 'if common_dir and common_dir.exists()'
            with patch.object(Path, "exists", return_value=True):
                # 2. Robust mock for read_text that returns content based on caller or index
                def mock_read_text_logic(*args, **kwargs):
                    return mock_config_content

                with patch.object(Path, "read_text", side_effect=mock_read_text_logic):
                    # 3. Satisfy globbing and file writing
                    with patch.object(Path, "glob", return_value=[es_config_path]):
                        with patch("ldm_core.utils.safe_write_text") as mock_write:
                            # Trigger sync
                            self.manager.sync_common_assets(self.paths)

                            # If substitution was applied, it should be in one of the write_text calls
                            found_substitution = False
                            for call in mock_write.call_args_list:
                                if 'indexNamePrefix="ldm-test-project-"' in call[0][1]:
                                    found_substitution = True
                                    break

                                self.assertTrue(
                                    found_substitution,
                                    "Elasticsearch substitution was not applied to the config file.",
                                )

    @patch("ldm_core.handlers.config.get_actual_home")
    def test_cmd_config_get_set(self, mock_home):
        mock_home.return_value = Path("/tmp/home")
        self.manager.args.remove = False

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value='{"verbose": "true"}'),
            patch.object(Path, "write_text") as mock_write,
            patch("os.replace"),
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
