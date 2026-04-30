import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from ldm_core.handlers.assets import AssetHandler
from ldm_core.handlers.base import BaseHandler


class MockAssets(AssetHandler, BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

    def verify_runtime_environment(self, *args, **kwargs):
        pass

    def setup_paths(self, root):
        return {"root": root}


class TestAssets(unittest.TestCase):
    def setUp(self):
        self.assets = MockAssets()
        self.tmp_dir = Path("/tmp/assets-test")

    @patch("ldm_core.handlers.assets.requests.head")
    def test_fetch_seed_api_fallback(self, mock_head):
        # Scenario: Direct download URL returns 404, fallback to GitHub API
        res_404 = MagicMock()
        res_404.status_code = 404
        mock_head.return_value = res_404

        with (
            patch("ldm_core.handlers.assets.requests.get") as mock_get,
            patch(
                "ldm_core.handlers.assets.get_actual_home", return_value=self.tmp_dir
            ),
            patch("os.path.exists", return_value=False),
        ):
            # Mock API response to find the asset
            res_api = MagicMock()
            res_api.status_code = 200
            res_api.json.return_value = {
                "tag_name": "seeded-states",
                "assets": [
                    {
                        "name": "seeded-tag-db-search-v2.tar.gz",
                        "browser_download_url": "http://fallback",
                    }
                ],
            }
            mock_get.side_effect = [res_api, Exception("Stop after URL resolved")]

            # The tool should attempt the API call after the HEAD 404
            self.assets._fetch_seed("tag", "db", "search", {"root": self.tmp_dir})
            self.assertGreater(mock_get.call_count, 0)

    @patch("ldm_core.handlers.assets.zipfile.ZipFile")
    @patch("ldm_core.handlers.assets.shutil.rmtree")
    @patch("ldm_core.handlers.assets.safe_move")
    def test_download_samples_extraction_logic(self, mock_move, mock_rmtree, mock_zip):
        with (
            patch("ldm_core.handlers.assets.requests.get"),
            patch(
                "ldm_core.handlers.assets.os.path.exists", return_value=True
            ),  # Cached
            patch.object(Path, "mkdir"),
        ):
            # Mock ZipFile context manager
            mock_zip.return_value.__enter__.return_value
            # Mock iterdir to simulate inner 'samples' folder
            with patch.object(Path, "iterdir", return_value=[Path("inner-file")]):
                res = self.assets.download_samples("2.3.0", self.tmp_dir)
                self.assertTrue(res)
                # Verify moves were attempted
                self.assertTrue(mock_move.called)

    @patch("ldm_core.handlers.assets.requests.head")
    def test_fetch_seed_offline_fallback(self, mock_head):
        """Verifies that the tool falls back to vanilla if discovery fails (offline)."""
        # Scenario: Network timeout during HEAD request
        mock_head.side_effect = requests.exceptions.Timeout("Connection timed out")

        with (
            patch(
                "ldm_core.handlers.assets.get_actual_home", return_value=self.tmp_dir
            ),
            patch("os.path.exists", return_value=False),
            patch("ldm_core.ui.UI.warning") as mock_warn,
        ):
            # Should NOT raise, but should return False and log warning
            res = self.assets._fetch_seed("tag", "db", "search", {"root": self.tmp_dir})
            self.assertFalse(res)
            # Verify user was informed about the offline state
            warn_calls = [call[0][0] for call in mock_warn.call_args_list]
            self.assertTrue(any("offline" in msg.lower() for msg in warn_calls))


if __name__ == "__main__":
    unittest.main()
