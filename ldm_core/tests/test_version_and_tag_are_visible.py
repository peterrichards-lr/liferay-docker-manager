"""Three facts the log must carry, and did not (LDM-#1790).

From LDM-#1782, where a boot regression took a binary-hash comparison, two
local scaffolds and three Liferay boots to attribute, because none of the
following appeared anywhere in the output:

1. the LDM version that ran;
2. which Liferay tag was booted;
3. where that tag came from -- a workspace pin, an explicit `-t`, or discovery.

The third is the one that mattered. LDM-#1693 changed the accelerator's E2E
from the tag discovery returned (`2026.q1.12-lts`) to the one its workspace
pins (`2026.q1.7-lts`), and said so **only** through `UI.detail` -- which prints
under `--info`/`--verbose` and nowhere else (LDM-#1036). A default
non-interactive CI run never showed it.

## Why a pin deserves a visible line

A pin records the version that has been **tested**. A package or workspace
legitimately pins an older line because a later one was tried and failed, or
because nobody has had time to qualify one. So a consumer may be running an
older line entirely deliberately, and anything that changes which line boots is
a decision they need to see -- not a detail they must opt into.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.constants import VERSION


class TheBannerNamesTheVersion(unittest.TestCase):
    def render(self):
        printed = []

        def capture(*args, **_kwargs):
            printed.append(" ".join(str(x) for x in args))

        with patch("builtins.print", side_effect=capture):
            from ldm_core.ui import UI

            UI.print_banner()
        return "\n".join(printed)

    def test_the_version_is_in_the_banner(self):
        """Reconstructing it from release timestamps is not a diagnosis."""
        self.assertIn(VERSION, self.render())

    def test_the_banner_still_names_the_tool(self):
        self.assertIn("Liferay Docker Manager", self.render())


class TheTagProvenanceIsVisible(unittest.TestCase):
    """Drives the real `_apply_workspace_product` against a real workspace on
    disk. `UI.info`, not `UI.detail` -- that distinction is the whole defect.
    """

    def apply_pin(self, product="dxp-2026.q1.7-lts", explicit_tag=None):
        """Returns (what was said at each level, the resulting project meta)."""
        from ldm_core.pipelines.import_pipeline import ProjectSetupStage

        said: dict[str, list[str]] = {"info": [], "detail": []}
        meta: dict[str, str] = {}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gradle.properties").write_text(
                f"liferay.workspace.product={product}\n", encoding="utf-8"
            )

            def record_info(message, *_args, **_kwargs):
                said["info"].append(str(message))

            def record_detail(message, *_args, **_kwargs):
                said["detail"].append(str(message))

            context = MagicMock()
            context.manager.args.tag = explicit_tag

            def lookup(key, *_default):
                return (
                    root if key in ("workspace_root", "source_path", "root") else None
                )

            context.get.side_effect = lookup

            with (
                patch("ldm_core.ui.UI.info", side_effect=record_info),
                patch("ldm_core.ui.UI.detail", side_effect=record_detail),
            ):
                ProjectSetupStage._apply_workspace_product(context, meta)

        return said, meta

    def test_the_pin_is_announced_at_default_verbosity(self):
        """The defect: this was `UI.detail`, which prints only under
        `--info`/`--verbose`, so CI never saw which line it was booting."""
        said, meta = self.apply_pin()

        self.assertTrue(
            said["info"],
            "the tag changed and nothing was said at default verbosity",
        )
        self.assertNotIn(
            "2026.q1.7-lts",
            " ".join(said["detail"]),
            "the pin announcement is still hidden behind --info",
        )

    def test_it_names_the_tag_and_where_it_came_from(self):
        """ "Using tag X" alone does not say that a pin overrode discovery,
        which is the fact that explains a changed boot."""
        said, _ = self.apply_pin()
        line = " ".join(said["info"])

        self.assertIn("2026.q1.7-lts", line)
        self.assertIn("pin", line.lower())

    def test_an_explicit_tag_wins_and_says_nothing_about_a_pin(self):
        """`-t` is a decision; the pin does not apply and must not be
        announced as though it did (LDM-#1693's own precedence rule)."""
        said, meta = self.apply_pin(explicit_tag="2026.q3.0")

        self.assertEqual(said["info"], [])
        self.assertNotIn("tag", meta)


if __name__ == "__main__":
    unittest.main()
