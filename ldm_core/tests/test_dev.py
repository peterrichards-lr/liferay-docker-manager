import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.dev import DevHandler


class TestDevHandler(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp_dir.name)

        # Mock args
        self.args = MagicMock()
        self.args.yes = True
        self.handler = DevHandler(self.args)

        # Create mock project structure
        (self.base / ".git").mkdir()
        (self.base / "pyproject.toml").write_text('version = "1.0.0"')

        # Override constants.VERSION for testing
        self.version_patch = patch("ldm_core.handlers.dev.VERSION", "2.4.26-beta.4")
        self.version_patch.start()

    def tearDown(self):
        self.version_patch.stop()
        self.tmp_dir.cleanup()

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_bump_beta(self, mock_cwd):
        mock_cwd.return_value = self.base

        with patch("ldm_core.ui.UI.confirm", return_value=True):
            # Test 2.4.26-beta.4 -> 2.4.26-beta.5
            with patch.object(self.handler, "_apply_version_update") as mock_apply:
                self.handler.cmd_version(bump_type="beta")
                mock_apply.assert_called_with("2.4.26-beta.5", None)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_promote_stable(self, mock_cwd):
        mock_cwd.return_value = self.base

        with patch("ldm_core.ui.UI.confirm", return_value=True):
            # Test 2.4.26-beta.4 -> 2.4.26
            with patch.object(self.handler, "_apply_version_update") as mock_apply:
                self.handler.cmd_version(promote=True)
                mock_apply.assert_called_with("2.4.26", None)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_ensure_dev_env_blocks(self, mock_cwd):
        # Create a directory without .git
        with tempfile.TemporaryDirectory() as empty_dir:
            mock_cwd.return_value = Path(empty_dir)
            with self.assertRaises(SystemExit):
                with patch("ldm_core.ui.UI.die") as mock_die:
                    mock_die.side_effect = SystemExit
                    self.handler._ensure_dev_env()

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_bump_major_minor(self, mock_cwd):
        mock_cwd.return_value = self.base
        with patch("ldm_core.ui.UI.confirm", return_value=True):
            with patch.object(self.handler, "_apply_version_update") as mock_apply:
                self.handler.cmd_version(bump_type="major")
                mock_apply.assert_called_with("3.0.0", None)

                self.handler.cmd_version(bump_type="minor")
                mock_apply.assert_called_with("2.5.0", None)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_apply_version_update_writes_files(self, mock_cwd):
        mock_cwd.return_value = self.base

        # Setup files
        constants_path = self.base / "ldm_core" / "constants.py"
        constants_path.parent.mkdir(parents=True, exist_ok=True)
        constants_path.write_text(
            'VERSION = "2.4.26-beta.4"\nELASTICSEARCH_VERSION = "8.19.1"\n# LDM_MAGIC_VERSION: 2.4.26-beta.4'
        )

        pyproject_path = self.base / "pyproject.toml"
        pyproject_path.write_text('version = "2.4.26-beta.4"')

        self.handler._apply_version_update("2.4.26")

        content = constants_path.read_text()
        self.assertIn('VERSION = "2.4.26"', content)
        self.assertIn('ELASTICSEARCH_VERSION = "8.19.1"', content)  # UNCHANGED
        self.assertIn("LDM_MAGIC_VERSION: 2.4.26", content)
        self.assertIn('version = "2.4.26"', pyproject_path.read_text())

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_promote_blocks_stable(self, mock_cwd):
        mock_cwd.return_value = self.base
        # Setup stable version
        with patch("ldm_core.handlers.dev.VERSION", "2.4.26"):
            with self.assertRaises(SystemExit):
                with patch("ldm_core.ui.UI.die") as mock_die:
                    mock_die.side_effect = SystemExit
                    self.handler.cmd_version(promote=True)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_version_set_and_build_info(self, mock_cwd):
        mock_cwd.return_value = self.base
        # Setup files
        constants_path = self.base / "ldm_core" / "constants.py"
        constants_path.parent.mkdir(parents=True, exist_ok=True)
        constants_path.write_text(
            'VERSION = "1.0.0"\nBUILD_INFO = None\n# LDM_MAGIC_VERSION: 1.0.0'
        )

        self.handler.cmd_version(set_version="2.0.0", build_info="CI-Build-123")

        content = constants_path.read_text()
        self.assertIn('VERSION = "2.0.0"', content)
        self.assertIn('BUILD_INFO = "CI-Build-123"', content)
        self.assertIn("LDM_MAGIC_VERSION: 2.0.0", content)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_version_check_sync(self, mock_cwd):
        mock_cwd.return_value = self.base
        pyproject_path = self.base / "pyproject.toml"

        # 1. Matching
        pyproject_path.write_text('version = "2.4.26-beta.4"')
        self.handler.cmd_version(check=True)  # Should not raise

        # 2. Mismatch
        pyproject_path.write_text('version = "mismatch"')
        with self.assertRaises(SystemExit):
            with patch("ldm_core.ui.UI.die") as mock_die:
                mock_die.side_effect = SystemExit
                self.handler.cmd_version(check=True)

    def test_version_print(self):
        with patch("builtins.print") as mock_print:
            self.handler.cmd_version(print_only=True)
            mock_print.assert_called_with("2.4.26-beta.4")
