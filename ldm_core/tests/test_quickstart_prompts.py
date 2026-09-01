"""Quickstart should ask only the question that has consequences (LDM-#1535).

A real `ldm quickstart aica` asked three:

    Release type (lts|u|qr|nightly|master|latest), prefix, or specific tag [...]
    Project seed not found in cache. Download pre-warmed ... seed? [Y/n]
    Add host entries? (Requires sudo) [Y/n]

The tag is in the package manifest (#1514) and the seed answer is derivable,
so two of the three are asked despite the answer being known. The third stays:
it shells out to sudo and mutates /etc/hosts, the only step reaching outside
LDM's own directories.

What these assert is behaviour -- whether the prompt fires, and whether the
gate runs before the expensive work -- not the wording of any message.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.cli import get_parser
from ldm_core.handlers.assets import AssetService


class MockAssetManager:
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True
        self.snapshot = MagicMock()
        self.parse_version = MagicMock(return_value=(2026, 1, 0))

    def verify_runtime_environment(self, paths):
        pass


class TestDeclaredSeedAnswer(unittest.TestCase):
    def setUp(self):
        self.assets = AssetService(MockAssetManager())

    @patch("ldm_core.handlers.assets.get_actual_home")
    @patch("ldm_core.handlers.assets.UI.confirm")
    @patch("requests.get")
    @patch("requests.head")
    def test_assume_yes_does_not_prompt(
        self, mock_head, mock_get, mock_confirm, mock_home
    ):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            mock_home.return_value = Path(d)
            mock_head.return_value = MagicMock(status_code=200)
            mock_get.side_effect = OSError("stop after the decision point")

            self.assets._fetch_seed(
                "2026.q1.7-lts", "postgresql", "shared", {}, assume_yes=True
            )

        mock_confirm.assert_not_called()

    @patch("ldm_core.handlers.assets.get_actual_home")
    @patch("ldm_core.handlers.assets.UI.confirm")
    @patch("requests.get")
    @patch("requests.head")
    def test_without_assume_yes_the_prompt_still_fires(
        self, mock_head, mock_get, mock_confirm, mock_home
    ):
        # The default must be unchanged for run/import/init-from/link/clone.
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            mock_home.return_value = Path(d)
            mock_head.return_value = MagicMock(status_code=200)
            mock_confirm.return_value = False

            self.assets._fetch_seed("2026.q1.7-lts", "postgresql", "shared", {})

        mock_confirm.assert_called_once()


class TestNoSeedFlagIsTheReusedName(unittest.TestCase):
    def test_quickstart_accepts_no_seed(self):
        parser, _ = get_parser()
        args = parser.parse_args(["quickstart", "aica", "--no-seed"])
        self.assertTrue(args.no_seed)

    def test_quickstart_defaults_to_seeding(self):
        parser, _ = get_parser()
        args = parser.parse_args(["quickstart", "aica"])
        self.assertFalse(args.no_seed)

    def test_the_flag_matches_the_other_commands(self):
        # Reuse, not a new noun: the same spelling must parse on the commands
        # that already had it, so muscle memory transfers.
        parser, _ = get_parser()
        for argv in (
            ["quickstart", "aica", "--no-seed"],
            ["import", "some-source", "--no-seed"],
        ):
            with self.subTest(argv=argv):
                self.assertTrue(parser.parse_args(argv).no_seed)


if __name__ == "__main__":
    unittest.main()
