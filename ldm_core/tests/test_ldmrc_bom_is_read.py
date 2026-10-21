"""A config file saved with a UTF-8 byte-order mark is read (LDM-#2281).

Windows PowerShell 5.1 (`Out-File -Encoding utf8`) and older Notepad save
UTF-8 with a BOM. LDM read `~/.ldmrc` as plain utf-8, which keeps the BOM, and
`json.loads` rejected it: the `ldm config` listing printed nothing, and every
other reader fell back to the built-in defaults. Found by the E2E suite's
LDM-#2174 check on PS 5.1.

The fixture's `database_mode` is `shared`, deliberately NOT the built-in
default (`isolated`): the E2E check passed half of itself because the value it
expected was the one a failed read falls back to.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

BOM = b"\xef\xbb\xbf"
CONFIG = {
    "defaults": {"database_mode": "shared"},
    "database_mode": "isolated",
}


class _BomHome(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        (self.home / ".ldmrc").write_bytes(BOM + json.dumps(CONFIG).encode())
        self._env = patch.dict(os.environ, {"LDM_HOME": str(self.home)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._td.cleanup()


class TheConfigLoaderReadsABomFile(_BomHome):
    def test_load_global_config_safe(self):
        from ldm_core.utils import load_global_config_safe

        self.assertEqual(load_global_config_safe(self.home / ".ldmrc"), CONFIG)

    def test_the_defaults_cascade_sees_the_value(self):
        """The resolver every project-creation decision reads."""
        from ldm_core.defaults import DefaultsManager

        self.assertEqual(DefaultsManager().get("database_mode"), "shared")


class TheListingShowsABomFile(_BomHome):
    def test_the_listing_marks_the_shadowed_key(self):
        """The exact assertion the E2E LDM-#2174 check makes."""
        from ldm_core.handlers.config import ConfigService
        from ldm_core.tests.test_workspace import MockWorkspaceManager

        out = io.StringIO()
        with redirect_stdout(out):
            ConfigService(MockWorkspaceManager()).cmd_config()
        self.assertIn("shadowed", out.getvalue())


class QuickstartTemplatesWithABom(unittest.TestCase):
    def test_a_bom_templates_file_is_used(self):
        from ldm_core.tests.test_workspace import MockWorkspaceManager

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".ldm_templates.json").write_bytes(
                BOM
                + json.dumps(
                    {
                        "mine": {
                            "repo": "https://github.com/x/y.git",
                            "default_name": "y",
                        }
                    }
                ).encode()
            )
            manager = MockWorkspaceManager()
            manager.args.name = None
            manager.args.replace = False
            manager.args.no_seed = True
            project = home / "proj"
            project.mkdir()
            with (
                patch("ldm_core.utils.get_actual_home", return_value=home),
                patch(
                    "ldm_core.handlers.workspace.WorkspaceService.cmd_import"
                ) as imported,
                patch.object(
                    MockWorkspaceManager, "detect_project_path", return_value=project
                ),
                patch(
                    "ldm_core.handlers.base.BaseHandler.read_meta",
                    return_value={"tag": "2026.q3.0"},
                ),
                patch(
                    "ldm_core.handlers.runtime.RuntimeService.cmd_run",
                    return_value=True,
                ),
            ):
                manager.workspace.cmd_quickstart("mine")
        self.assertEqual(imported.call_args.args[0], "https://github.com/x/y.git")


if __name__ == "__main__":
    unittest.main()
