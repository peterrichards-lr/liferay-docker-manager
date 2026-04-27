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
                mock_apply.assert_called_with("2.4.26-beta.5")

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_promote_stable(self, mock_cwd):
        mock_cwd.return_value = self.base

        with patch("ldm_core.ui.UI.confirm", return_value=True):
            # Test 2.4.26-beta.4 -> 2.4.26
            with patch.object(self.handler, "_apply_version_update") as mock_apply:
                self.handler.cmd_version(promote=True)
                mock_apply.assert_called_with("2.4.26")

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_bump_patch_from_beta(self, mock_cwd):
        mock_cwd.return_value = self.base

        with patch("ldm_core.ui.UI.confirm", return_value=True):
            # Test 2.4.26-beta.4 -> 2.4.26
            # Note: cmd_version logic for patch currently does major.minor.patch+1
            # based on base_version. So 2.4.26-beta.4 -> 2.4.27
            with patch.object(self.handler, "_apply_version_update") as mock_apply:
                self.handler.cmd_version(bump_type="patch")
                mock_apply.assert_called_with("2.4.27")
