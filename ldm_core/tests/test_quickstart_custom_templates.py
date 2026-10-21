"""Custom quickstart templates must survive the parser (LDM-#2270).

The parser declared `template` with `choices=["aica"]`, so argparse exited 2
("invalid choice") before `cmd_quickstart` ever read `~/.ldm_templates.json`.
The override feature was dead from the day it shipped: `choices` came first
(#99) and the overrides later (#102), which did not touch it. Its only test
called `cmd_quickstart` directly, skipping the parser -- so it passed.

These start at the parser, with a real overrides file in a temporary home,
and follow the parsed name into the lookup.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ldm_core.tests.test_workspace import MockWorkspaceManager


class ACustomTemplateIsUsable(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.home = Path(self._td.name)
        (self.home / ".ldm_templates.json").write_text(
            json.dumps(
                {
                    "custom_aica": {
                        "repo": "https://github.com/example/custom-aica.git",
                        "default_name": "custom-aica",
                    }
                }
            )
        )

    def tearDown(self):
        self._td.cleanup()

    def _parse_and_run(self, argv):
        from ldm_core.cli import get_parser

        args = get_parser()[0].parse_args(argv)
        manager = MockWorkspaceManager()
        manager.args.no_seed = False
        manager.args.replace = False
        manager.args.name = None
        project_dir = self.home / "proj"
        project_dir.mkdir(exist_ok=True)
        with (
            patch("ldm_core.utils.get_actual_home", return_value=self.home),
            patch(
                "ldm_core.handlers.workspace.WorkspaceService.cmd_import"
            ) as imported,
            patch.object(
                MockWorkspaceManager, "detect_project_path", return_value=project_dir
            ),
            patch(
                "ldm_core.handlers.base.BaseHandler.read_meta",
                return_value={"tag": "2026.q3.0", "db_type": "postgresql"},
            ),
            patch.object(MockWorkspaceManager, "setup_paths", return_value={}),
            patch(
                "ldm_core.handlers.assets.AssetService._fetch_seed", return_value=True
            ),
            patch(
                "ldm_core.handlers.runtime.RuntimeService.cmd_run", return_value=True
            ),
        ):
            manager.workspace.cmd_quickstart(args.template)
        return imported

    def test_a_template_from_the_overrides_file_is_imported(self):
        imported = self._parse_and_run(["quickstart", "custom_aica"])
        self.assertEqual(
            imported.call_args.args[0], "https://github.com/example/custom-aica.git"
        )
        self.assertEqual(imported.call_args.kwargs["project_id"], "custom-aica")

    def test_the_built_in_name_is_case_insensitive(self):
        imported = self._parse_and_run(["quickstart", "AICA"])
        self.assertIn("liferay-ai-commerce-accelerator", imported.call_args.args[0])

    def test_an_unknown_name_is_refused_by_quickstart_listing_what_exists(self):
        """Control: dropping `choices` must not make anything acceptable."""
        errors: list[str] = []
        with (
            patch(
                "ldm_core.ui.UI.error",
                side_effect=lambda msg, *_a, **_k: errors.append(f"{msg} {_a} {_k}"),
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            self._parse_and_run(["quickstart", "nope"])
        self.assertEqual(ctx.exception.code, 1)
        said = " ".join(errors)
        self.assertIn("aica", said)
        self.assertIn("custom_aica", said)


if __name__ == "__main__":
    unittest.main()
