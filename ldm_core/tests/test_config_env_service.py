"""`ldm config env -s <service>` refuses rather than writing to Liferay (LDM-#2384).

`-s/--service` was parsed and then dropped by the dispatch, so `cmd_env` wrote
the variable into ``custom_env`` -- which the composer applies to the Liferay
container only. These tests go through the real parser and the real dispatch
entry (``_build_command_map``) into the real ``ConfigService.cmd_env``; only the
project I/O is stubbed, so the assertions cross the parser -> handler seam the
bug lived in.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.cli import _build_command_map, get_parser, preprocess_args
from ldm_core.handlers.config import ConfigService
from ldm_core.ui import UI


class _Manager:
    """Just enough manager for cmd_env; records every metadata write."""

    def __init__(self, args, root):
        self.args = args
        self.root = root
        self.non_interactive = True
        self.runtime = MagicMock()
        self.workspace = MagicMock()
        self.workspace.get_host_passthrough_env.return_value = []
        self.writes = []
        self.config = ConfigService(self)

    def __getattr__(self, name):
        # _build_command_map binds every handler eagerly; only `config` is real.
        value = MagicMock(name=name)
        setattr(self, name, value)
        return value

    def detect_project_path(self, project_id=None):
        return self.root

    def setup_paths(self, root):
        return {"root": root}

    def read_meta(self, root):
        return {"custom_env": json.dumps({"EXISTING": "1"})}

    def write_meta(self, root, meta):
        self.writes.append(dict(meta))


class TestConfigEnvServiceFlag(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.errors: list[tuple[str, str]] = []
        # UI.die -> UI.error(msg, details, tip); keep the message and the tip.
        patcher = patch.object(
            UI,
            "error",
            side_effect=lambda msg, _details=None, tip=None: self.errors.append(
                (msg, tip or "")
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _dispatch(self, *argv):
        argv_list = preprocess_args(["ldm", "-y", *argv])
        parser, _ = get_parser()
        args = parser.parse_args(argv_list[1:])
        self.manager = _Manager(args, self.root)
        cmds = _build_command_map(args, self.manager)
        cmds[("config", "env")]()
        return self.manager

    def _refused(self, *argv):
        with self.assertRaises(SystemExit) as ctx:
            self._dispatch(*argv)
        # Not argparse's 2: the refusal must come from cmd_env, past the parser.
        self.assertEqual(ctx.exception.code, 1)
        return ctx.exception.code

    def test_another_service_is_refused_with_exit_1(self):
        code = self._refused("config", "env", "-s", "my-ext", "DEBUG=true")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("my-ext", self.errors[0][0])

    def test_refusal_names_the_exact_targeted_host_variable(self):
        self._refused("config", "env", "--service", "my-custom-ext", "DEBUG=true")
        _msg, tip = self.errors[0]
        self.assertIn("MY_CUSTOM_EXT_DEBUG=true ldm run", tip)
        self.assertIn("configuration.md#4-service-specific-targeting", tip)

    def test_refusal_writes_nothing(self):
        self._refused("env", "-s", "my-ext", "myproj", "DEBUG=true")
        manager = self.manager
        self.assertEqual(manager.writes, [])
        manager.runtime.cmd_run.assert_not_called()
        # The legacy flat form routes here too, and its project id is not a KEY.
        self.assertIn("MY_EXT_DEBUG=true ldm run", self.errors[0][1])
        self.assertNotIn("myproj", self.errors[0][1])

    def test_liferay_is_accepted_and_writes_custom_env(self):
        manager = self._dispatch("config", "env", "-s", "liferay", "DEBUG=true")
        self.assertEqual(self.errors, [])
        self.assertEqual(
            json.loads(manager.writes[-1]["custom_env"]),
            {"EXISTING": "1", "DEBUG": "true"},
        )

    def test_without_service_still_writes_custom_env(self):
        manager = self._dispatch("config", "env", "DEBUG=true")
        self.assertEqual(self.errors, [])
        self.assertEqual(
            json.loads(manager.writes[-1]["custom_env"]),
            {"EXISTING": "1", "DEBUG": "true"},
        )
        manager.runtime.cmd_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
