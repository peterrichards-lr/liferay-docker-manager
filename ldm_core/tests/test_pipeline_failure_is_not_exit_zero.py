"""A pipeline that failed must not exit 0 (LDM-#2155).

`Pipeline.run` returns `False` when a stage raised an unexpected exception:
it rolls back and leaves the verdict to the caller (`pipelines/base.py:98`).
Every caller threw it away. The dispatcher ignores bools on purpose
(`cli.py`, pinned by `test_silent_failure_exit_codes.py`), `cmd_quickstart`
did not return what `cmd_run` answered, and the import paths returned a
project name regardless -- so a stack that never came up reported success to
whatever ran it.

Exit 4 is Orchestration/Deployment Error per
`.agents/skills/ldm-architecture/SKILL.md`.

Every assertion here is on the outcome -- the SystemExit code, or the value
the handler returned -- never on the text of any source file. A `UI.die`
raised inside a stage must still carry its own code, so the last class guards
the LDM-#1630/#1636 behaviour this change sits next to.
"""

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.cli import _build_command_map, _execute_command, _exit_code_for_pipeline
from ldm_core.workspace.importer import _die_if_the_pipeline_failed

ORCHESTRATION = 4


def _dispatch(handler):
    """Run `_execute_command` with one registered handler.

    `system upgrade` suppresses the background update-check thread, so this
    touches no network -- the same harness `test_silent_failure_exit_codes`
    uses.
    """
    _execute_command(
        argparse.Namespace(command="system", subcommand="upgrade"),
        ("system", "upgrade"),
        {("system", "upgrade"): handler},
    )


class TestTheConversionItself(unittest.TestCase):
    def test_false_becomes_the_orchestration_exit_code(self):
        self.assertEqual(_exit_code_for_pipeline(False), ORCHESTRATION)

    def test_a_failed_pipeline_exits_four_through_the_dispatcher(self):
        with self.assertRaises(SystemExit) as ctx:
            _dispatch(lambda: _exit_code_for_pipeline(False))
        self.assertEqual(ctx.exception.code, ORCHESTRATION)

    def test_a_successful_pipeline_is_still_exit_zero(self):
        self.assertIs(_exit_code_for_pipeline(True), True)
        _dispatch(lambda: _exit_code_for_pipeline(True))

    def test_a_handler_that_said_nothing_is_untouched(self):
        self.assertIsNone(_exit_code_for_pipeline(None))
        _dispatch(lambda: _exit_code_for_pipeline(None))

    def test_a_handler_that_returned_its_own_code_keeps_it(self):
        # `0` must not be mistaken for `False`: both are falsy, and only one
        # of them means a stage rolled back.
        self.assertEqual(_exit_code_for_pipeline(0), 0)
        _dispatch(lambda: _exit_code_for_pipeline(0))

        with self.assertRaises(SystemExit) as ctx:
            _dispatch(lambda: _exit_code_for_pipeline(3))
        self.assertEqual(ctx.exception.code, 3)


class TestTheCommandsThatOwnAPipeline(unittest.TestCase):
    """The real dispatch entries, not a stand-in for them."""

    def _cmds(self, *, run_result):
        manager = MagicMock()
        manager.runtime.cmd_run = MagicMock(return_value=run_result)
        args = argparse.Namespace(
            command="run", project="demo", open=False, template="aica"
        )
        args.share = False
        args.share_subdomain = None
        return _build_command_map(args, manager), manager

    def test_run_reports_a_failed_boot(self):
        cmds, _ = self._cmds(run_result=False)
        self.assertEqual(cmds[("run", None)](), ORCHESTRATION)

    def test_up_reports_a_failed_boot(self):
        cmds, _ = self._cmds(run_result=False)
        self.assertEqual(cmds[("up", None)](), ORCHESTRATION)

    def test_a_boot_that_worked_still_exits_zero(self):
        cmds, _ = self._cmds(run_result=True)
        self.assertIs(cmds[("run", None)](), True)
        _dispatch(lambda: True)

    def test_quickstart_reports_what_it_was_told(self):
        manager = MagicMock()
        manager.workspace.cmd_quickstart = MagicMock(return_value=False)
        args = argparse.Namespace(
            command="quickstart", template="aica", share=False, share_subdomain=None
        )
        cmds = _build_command_map(args, manager)
        self.assertEqual(cmds[("quickstart", None)](), ORCHESTRATION)

        manager.workspace.cmd_quickstart = MagicMock(return_value=True)
        cmds = _build_command_map(args, manager)
        self.assertIs(cmds[("quickstart", None)](), True)


class TestQuickstartReturnsTheBootVerdict(unittest.TestCase):
    """`quickstart.py` discarded `cmd_run` and returned None regardless.

    `ldm quickstart aica` is the whole workflow of the team that reports most
    of these, and it runs in their CI.
    """

    def _run_quickstart(self, *, boot_result):
        from ldm_core.workspace.quickstart import cmd_quickstart

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "liferay-ai-commerce-accelerator"
            project.mkdir()

            manager = MagicMock()
            # Explicit, not inherited from the mock: an absent attribute on a
            # MagicMock is truthy, which would decide these branches silently.
            manager.args.name = None
            manager.args.no_seed = True
            manager.non_interactive = True
            manager.detect_project_path = MagicMock(return_value=project)
            manager.read_meta = MagicMock(
                return_value={"tag": "2026.q1.7-lts", "db_type": "postgresql"}
            )
            manager.parse_version = MagicMock(return_value=(2026, 1, 0))
            manager.runtime.cmd_run = MagicMock(return_value=boot_result)

            facade = MagicMock()
            facade.manager = manager

            with patch("ldm_core.utils.get_actual_home", return_value=Path(tmp)):
                return cmd_quickstart(facade, "aica")

    def test_a_failed_boot_is_returned(self):
        self.assertIs(self._run_quickstart(boot_result=False), False)

    def test_a_successful_boot_is_returned(self):
        self.assertIs(self._run_quickstart(boot_result=True), True)


class TestImportDoesNotNameAProjectThatWasRolledBack(unittest.TestCase):
    def test_the_guard_exits_four(self):
        with self.assertRaises(SystemExit) as ctx:
            _die_if_the_pipeline_failed(False, "Import")
        self.assertEqual(ctx.exception.code, ORCHESTRATION)

    def test_the_guard_passes_a_success_through(self):
        _die_if_the_pipeline_failed(True, "Import")
        _die_if_the_pipeline_failed(None, "Import")

    def _import_local_dir(self, pipeline_run):
        from ldm_core.workspace.importer import cmd_import

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "workspace"
            source.mkdir()

            manager = MagicMock()
            manager.non_interactive = True
            manager.args.project = None
            manager.args.project_flag = None
            manager.args.clone_only = False
            manager.args.no_run = True

            facade = MagicMock()
            facade.manager = manager

            with (
                patch.dict("os.environ", {"LDM_DRY_RUN": "false"}),
                patch(
                    "ldm_core.pipelines.import_pipeline.ImportPipeline.run",
                    pipeline_run,
                ),
            ):
                return cmd_import(facade, str(source), project_id="demo")

    def test_a_rolled_back_import_exits_four(self):
        with self.assertRaises(SystemExit) as ctx:
            self._import_local_dir(MagicMock(return_value=False))
        self.assertEqual(ctx.exception.code, ORCHESTRATION)

    def test_a_clean_import_still_returns_the_project_name(self):
        self.assertEqual(self._import_local_dir(MagicMock(return_value=True)), "demo")

    def test_a_stage_refusal_keeps_its_own_exit_code(self):
        # LDM-#1630: `UI.die` inside a stage raises SystemExit, which
        # `Pipeline.run` re-raises unchanged. It must not be flattened into 4.
        def _refuse(_self, _context):
            raise SystemExit(2)

        with self.assertRaises(SystemExit) as ctx:
            self._import_local_dir(_refuse)
        self.assertEqual(ctx.exception.code, 2)

    def test_an_idempotent_no_op_is_not_turned_into_a_failure(self):
        # LDM-#1636: exit 5 means the desired state already held.
        def _no_op(_self, _context):
            raise SystemExit(5)

        with self.assertRaises(SystemExit) as ctx:
            self._import_local_dir(_no_op)
        self.assertEqual(ctx.exception.code, 5)


if __name__ == "__main__":
    unittest.main()
