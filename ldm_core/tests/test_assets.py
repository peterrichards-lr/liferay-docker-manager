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
            mock_res.iter_content = MagicMock(return_value=[b"ZIP"])
            mock_get.return_value = mock_res

            # Mock ZipFile to avoid BadZipFile
            with patch("zipfile.ZipFile") as mock_zip:
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

            # Mock ZipFile
            with patch("zipfile.ZipFile") as mock_zip:
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

    @patch("ldm_core.handlers.assets.get_actual_home")
    @patch("requests.head")
    @patch("requests.get")
    def test_download_seed_success(self, mock_get, mock_head, mock_home):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_home.return_value = tmp_path

            # Mock head failure to trigger GET fallback
            mock_head_res = MagicMock()
            mock_head_res.status_code = 404
            mock_head.return_value = mock_head_res

            # Mock GET release API
            mock_api_res = MagicMock()
            mock_api_res.status_code = 200
            mock_api_res.json.return_value = {
                "tag_name": "seeded-states",
                "assets": [
                    {
                        "name": f"seeded-7.4-mysql-local-v{SEED_VERSION}.tar.gz",
                        "browser_download_url": "http://example.com",
                    }
                ],
            }

            # Mock GET download stream
            mock_stream_res = MagicMock()
            mock_stream_res.status_code = 200
            mock_stream_res.headers = {"content-length": "100"}
            mock_stream_res.iter_content.return_value = [b"data"]

            mock_get.side_effect = [mock_api_res, mock_stream_res]

            paths = {"root": tmp_path}
            with patch("ldm_core.ui.UI.confirm", return_value=True):
                res = self.assets._fetch_seed("7.4", "mysql", "local", paths)

            self.assertTrue(res)


if __name__ == "__main__":
    unittest.main()
