import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.constants import SEED_VERSION
from ldm_core.handlers.assets import AssetService


class MockAssetManager:
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True
        self.snapshot = MagicMock()

    def verify_runtime_environment(self, paths):
        pass


class TestAssetService(unittest.TestCase):
    def setUp(self):
        self.manager = MockAssetManager()
        self.assets = AssetService(self.manager)

    @patch("ldm_core.handlers.assets.get_actual_home")
    @patch("requests.get")
    def test_download_samples_success(self, mock_get, mock_home):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_home.return_value = tmp_path

            # Mock response
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.iter_content = MagicMock(return_value=[b"TAR"])
            mock_get.return_value = mock_res

            def mock_extractall(path, **kwargs):
                # Ensure the expected files_tar exists after first extraction
                files_tar = Path(path) / "files.tar.gz"
                if "temp_extract_samples" in str(path):
                    files_tar.parent.mkdir(parents=True, exist_ok=True)
                    files_tar.touch()

            # Mock tarfile to avoid ReadError
            with patch("tarfile.open") as mock_tar:
                mock_tar.return_value.__enter__.return_value.extractall.side_effect = (
                    mock_extractall
                )
                res = self.assets.download_samples("2.5.0", tmp_path / "dest")
                self.assertTrue(res)
                self.assertTrue(mock_get.called)

    @patch("ldm_core.handlers.assets.get_actual_home")
    @patch("os.path.exists", return_value=True)
    def test_download_samples_cached(self, mock_exists, mock_home):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_home.return_value = tmp_path

            def mock_extractall(path, **kwargs):
                files_tar = Path(path) / "files.tar.gz"
                if "temp_extract_samples" in str(path):
                    files_tar.parent.mkdir(parents=True, exist_ok=True)
                    files_tar.touch()

            # Mock tarfile
            with patch("tarfile.open") as mock_tar:
                mock_tar.return_value.__enter__.return_value.extractall.side_effect = (
                    mock_extractall
                )
                res = self.assets.download_samples("2.5.0", tmp_path / "dest")
                self.assertTrue(res)

    @patch("ldm_core.handlers.assets.get_actual_home")
    @patch("requests.head")
    def test_fetch_seed_cached(self, mock_head, mock_home):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_home.return_value = tmp_path

            seed_file = (
                tmp_path
                / ".ldm"
                / "seeds"
                / f"seeded-7.4-mysql-local-v{SEED_VERSION}.tar.gz"
            )
            seed_file.parent.mkdir(parents=True)
            seed_file.touch()

            paths = {"root": tmp_path}
            # We must mock os.path.exists specifically for the seed file path
            with patch(
                "os.path.exists",
                side_effect=lambda x: str(x) == str(seed_file) or Path(x).exists(),
            ):
                res = self.assets._fetch_seed("7.4", "mysql", "local", paths)
                self.assertTrue(res)

    @patch("ldm_core.ui.UI.ask")
    @patch("ldm_core.utils.discover_latest_tag")
    def test_prompt_for_tag(self, mock_discover, mock_ask):
        # Case 1: user accepts the default resolved tag
        mock_discover.return_value = "2026.q1.4-lts"
        mock_ask.return_value = "2026.q1.4-lts"
        tag = self.assets.prompt_for_tag()
        self.assertEqual(tag, "2026.q1.4-lts")

        # Case 2: user types a specific release type like 'u'
        mock_ask.return_value = "u"
        mock_discover.side_effect = ["2026.q1.4-lts", "2026.q2.1-u"]
        tag = self.assets.prompt_for_tag()
        self.assertEqual(tag, "2026.q2.1-u")

        # Case 3: user typed a manual tag
        mock_discover.side_effect = None
        mock_discover.return_value = "2026.q1.4-lts"
        mock_ask.return_value = "7.4.13-dxp-4"
        tag = self.assets.prompt_for_tag()
        self.assertEqual(tag, "7.4.13-dxp-4")


if __name__ == "__main__":
    unittest.main()
