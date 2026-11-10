"""`ldm init` must accept `--tag-prefix`, like every other creating command (LDM-#2168).

It was rejected with `unrecognized arguments: --tag-prefix 2026.q3`, so the
command a new user reaches for first could not scope tag discovery to a release
line. `init` runs the same pipeline as `run` (`cmd_init` calls `cmd_run`), so
the fix is the parser; this checks the parsed value reaches discovery, not only
that the parser takes it.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core.cli import get_parser
from ldm_core.pipelines.run import ConfigResolutionStage


class InitTakesATagPrefix(unittest.TestCase):
    def _parse(self, *argv):
        parser, _ = get_parser()
        return parser.parse_args(["init", "demo", *argv])

    def test_the_parser_accepts_it(self):
        self.assertEqual(self._parse("--tag-prefix", "2026.q3").tag_prefix, "2026.q3")

    @patch("ldm_core.utils.discover_latest_tag", return_value="2026.q3.7")
    def test_the_prefix_reaches_tag_discovery(self, discover):
        manager = MagicMock()
        manager.non_interactive = True
        manager.verbose = False
        manager.args = self._parse("--tag-prefix", "2026.q3")
        manager.defaults.get.return_value = None

        tag, _ = ConfigResolutionStage()._resolve_tag(
            manager, {}, is_samples=False, is_portal=False
        )

        self.assertEqual(tag, "2026.q3.7")
        self.assertEqual(discover.call_args.kwargs.get("prefix_filter"), "2026.q3")


if __name__ == "__main__":
    unittest.main()
