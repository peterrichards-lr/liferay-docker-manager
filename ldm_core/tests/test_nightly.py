import unittest
from unittest.mock import MagicMock, patch

from ldm_core.cli import get_parser
from ldm_core.defaults import DefaultsManager
from ldm_core.handlers.assets import AssetService
from ldm_core.utils import discover_latest_tag


class TestNightlyBuilds(unittest.TestCase):
    def setUp(self):
        self.parser, _ = get_parser()

    def test_cli_nightly_and_master_flags_parsing(self):
        """Verify -n, --nightly, --master, and --pull flags parse correctly."""
        # Test: ldm run my-app --nightly --pull
        args = self.parser.parse_args(["run", "my-app", "--nightly", "--pull"])
        self.assertTrue(getattr(args, "nightly", False))
        self.assertTrue(getattr(args, "pull", False))

        # Test: ldm run my-app -n
        args = self.parser.parse_args(["run", "my-app", "-n"])
        self.assertTrue(getattr(args, "nightly", False))

        # Test: ldm run my-app --master
        args = self.parser.parse_args(["run", "my-app", "--master"])
        self.assertTrue(getattr(args, "master", False))

        # Test: ldm start my-app --pull
        args = self.parser.parse_args(["start", "my-app", "--pull"])
        self.assertTrue(getattr(args, "pull", False))

    @patch("ldm_core.utils.get_actual_home")
    @patch("ldm_core.utils.get_raw")
    def test_discover_latest_tag_nightly(self, mock_get_raw, mock_home):
        """Verify discover_latest_tag handles release_type='nightly'."""
        mock_home.return_value.exists.return_value = False
        mock_get_raw.side_effect = [
            # CDN response (empty or non-matching)
            '{"entry": {"liferayDockerImage": "liferay/dxp:2026.q1.4-lts"}}',
            # Docker Hub API response
            '{"results": [{"name": "7.4.13.nightly"}, {"name": "2026.q1.4-lts"}]}',
        ]

        tag = discover_latest_tag(
            "https://hub.docker.com/v2/repositories/liferay/dxp/tags",
            release_type="nightly",
            refresh=True,
        )
        self.assertEqual(tag, "7.4.13.nightly")

    @patch("ldm_core.utils.discover_latest_tag")
    @patch("ldm_core.ui.UI.ask")
    def test_prompt_for_tag_nightly_selection(self, mock_ask, mock_discover):
        """Verify prompt_for_tag resolves 'nightly' input."""
        mock_discover.return_value = "7.4.13.nightly"
        mock_ask.return_value = "nightly"

        mock_manager = MagicMock()
        mock_manager.verbose = False
        asset_service = AssetService(mock_manager)

        selected_tag = asset_service.prompt_for_tag()
        self.assertEqual(selected_tag, "7.4.13.nightly")

    def test_defaults_manager_auto_pull_nightly(self):
        """Verify auto_pull_nightly default configuration value."""
        defaults = DefaultsManager()
        self.assertEqual(defaults.get("auto_pull_nightly"), "prompt")


if __name__ == "__main__":
    unittest.main()
