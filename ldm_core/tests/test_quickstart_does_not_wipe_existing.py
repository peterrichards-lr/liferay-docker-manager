"""Quickstart must not replace an existing project without being told to (LDM-#2269).

It used to print "already exists" and carry on. The package restore behind
`cmd_import` then runs `cmd_reset(target="all")` on any project with a compose
file -- `down -v` and the data directories -- so re-running quickstart, or
`--name` pointing at an unrelated project, deleted its database and volumes
after a single warning.

The refusal has to land before `cmd_import`, which is where the reset lives,
and before `_ensure_stopped`, so a running project is not stopped only to be
refused. Both are asserted. The project directory and its meta file are real
files, because "is this a project?" is decided by looking at the disk.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ldm_core.tests.test_workspace import MockWorkspaceManager


class AnExistingProjectIsNotReplacedSilently(unittest.TestCase):
    def setUp(self):
        self.manager = MockWorkspaceManager()
        self.manager.args.no_seed = False
        self.manager.args.replace = False
        self._td = TemporaryDirectory()
        self.project_dir = Path(self._td.name) / "liferay-ai-commerce-accelerator"
        self.project_dir.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def _make_project(self):
        (self.project_dir / "meta").write_text("tag=2026.q3.0\n")

    def _quickstart(self, confirm_answer=None):
        self.calls: list[str] = []
        self.errors: list[str] = []
        with (
            patch(
                "ldm_core.handlers.workspace.WorkspaceService.cmd_import",
                side_effect=lambda *_a, **_k: self.calls.append("import"),
            ),
            patch(
                "ldm_core.handlers.workspace.WorkspaceService._ensure_stopped",
                side_effect=lambda *_a, **_k: self.calls.append("stop"),
            ),
            patch.object(
                MockWorkspaceManager,
                "detect_project_path",
                return_value=self.project_dir,
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
            patch("ldm_core.ui.UI.confirm", return_value=confirm_answer) as confirm,
            patch(
                "ldm_core.ui.UI.error",
                side_effect=lambda msg, *_a, **_k: self.errors.append(msg),
            ),
        ):
            self.confirm = confirm
            return self.manager.workspace.cmd_quickstart("aica")

    def test_non_interactive_refuses_and_names_the_flag(self):
        self._make_project()
        self.manager.non_interactive = True
        with self.assertRaises(SystemExit) as ctx:
            self._quickstart()
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(
            self.calls,
            [],
            "quickstart reached the import (where the reset lives) or stopped "
            "the project before refusing.",
        )
        message = " ".join(self.errors)
        self.assertIn("--replace", message)
        self.assertIn("database and volumes", message)

    def test_replace_proceeds(self):
        self._make_project()
        self.manager.non_interactive = True
        self.manager.args.replace = True
        self._quickstart()
        self.assertIn("import", self.calls)

    def test_interactive_decline_changes_nothing(self):
        self._make_project()
        self.manager.non_interactive = False
        result = self._quickstart(confirm_answer=False)
        self.confirm.assert_called_once()
        self.assertEqual(self.calls, [])
        self.assertIsNone(result)

    def test_interactive_accept_proceeds(self):
        self._make_project()
        self.manager.non_interactive = False
        self._quickstart(confirm_answer=True)
        self.assertIn("import", self.calls)

    def test_a_directory_that_is_not_a_project_is_not_questioned(self):
        """Control: an empty directory has nothing to wipe, so no prompt and
        no refusal -- otherwise this would refuse every quickstart whose
        target directory merely exists."""
        self.manager.non_interactive = True
        self._quickstart()
        self.confirm.assert_not_called()
        self.assertIn("import", self.calls)


if __name__ == "__main__":
    unittest.main()
