"""`ldm run --dry-run` must preview, not probe (LDM-#1704).

Two defects, and the second only became reachable once the first was fixed.

**The reported one.** `verify_runtime_environment` writes a sentinel with
`safe_write_text` and has a container compare it. Under `LDM_DRY_RUN` the write
goes to `_DRY_RUN_VFS` and never reaches disk (`utils.py:1477`), and
`run_command` announces the container instead of running it. The verdict is then
judged as though both had happened, so `"OK" not in verify_res` is true and the
user is told `FATAL: VOLUME MOUNTING IS BROKEN` on a host where mounting works
-- followed by macOS advice to `colima stop` and restart with specific mount
flags, a disruptive change for a problem they do not have.

**The one it was hiding.** That refusal aborted every dry run three phases
early, so execution had never reached `_wait_for_ready`. With the mount check
skipped, a dry run reached it for the first time and polled for a container that
was never created -- the full `--timeout`, 900s by default. Fixing only the
reported defect would have turned a wrong message into a fifteen-minute hang.

This is the LDM-#1262 shape: an assertion fixed, and the next block found broken
because nothing had ever executed it. Measured end to end -- before, the command
hung past 90s; after, it exits 0 in ~45s with no false diagnosis.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from ldm_core.handlers.base import BaseHandler


class _Handler(BaseHandler):
    """Only the surface `verify_runtime_environment` touches."""

    def __init__(self, root, dry_run):
        self.args = SimpleNamespace(node=None)
        self.verbose = False
        self.dry_run = dry_run
        self.target = None
        self.root = root
        self.commands: list = []
        self.manager = cast(Any, self)

    def run_command(self, cmd, **kw):  # type: ignore[override]
        self.commands.append(list(cmd))
        # "OK" is what a healthy probe returns; anything else sends
        # verify_runtime_environment down the FATAL branch, which is not what
        # these tests are about.
        return "OK"


class TheMountProbeUnderDryRun(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()

        self._saved = os.environ.get("LDM_DRY_RUN")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._saved is None:
            os.environ.pop("LDM_DRY_RUN", None)
        else:
            os.environ["LDM_DRY_RUN"] = self._saved

    def test_no_container_is_run(self):
        """The probe cannot answer under dry run, so it must not be asked."""
        os.environ["LDM_DRY_RUN"] = "true"
        handler = _Handler(self.root, dry_run=True)

        with patch("ldm_core.handlers.base.UI"):
            with patch(
                "ldm_core.handlers.base.shutil.which", return_value="/bin/docker"
            ):
                handler.verify_runtime_environment(self.root)

        self.assertEqual(
            handler.commands,
            [],
            "a dry run invoked the docker probe -- it can only answer FAIL",
        )

    def test_it_does_not_refuse(self):
        """`FATAL: VOLUME MOUNTING IS BROKEN` used to end every dry run."""
        os.environ["LDM_DRY_RUN"] = "true"
        handler = _Handler(self.root, dry_run=True)

        with patch("ldm_core.handlers.base.UI") as ui:
            ui.die.side_effect = SystemExit(1)
            with patch(
                "ldm_core.handlers.base.shutil.which", return_value="/bin/docker"
            ):
                handler.verify_runtime_environment(self.root)

        ui.die.assert_not_called()
        reported = " ".join(str(c) for c in ui.error.call_args_list)
        self.assertNotIn("VOLUME MOUNTING IS BROKEN", reported)

    def test_it_says_what_it_would_have_done(self):
        os.environ["LDM_DRY_RUN"] = "true"
        handler = _Handler(self.root, dry_run=True)

        with patch("ldm_core.handlers.base.UI") as ui:
            with patch(
                "ldm_core.handlers.base.shutil.which", return_value="/bin/docker"
            ):
                handler.verify_runtime_environment(self.root)

        announced = " ".join(str(c) for c in ui.warning.call_args_list)
        self.assertIn("Dry Run", announced)
        self.assertIn("mount", announced.lower())

    def test_a_real_run_still_probes(self):
        """The guard must not disarm the check for everybody else."""
        os.environ.pop("LDM_DRY_RUN", None)
        handler = _Handler(self.root, dry_run=False)

        with patch("ldm_core.handlers.base.UI"):
            with patch(
                "ldm_core.handlers.base.shutil.which", return_value="/bin/docker"
            ):
                handler.verify_runtime_environment(self.root)

        self.assertTrue(
            any("docker" in c[0] for c in handler.commands),
            "a real run stopped probing the mount -- the guard is too broad",
        )


class TheReadinessWaitUnderDryRun(unittest.TestCase):
    """Structural, because reaching the line needs a booted stack.

    The objection recorded in `test_fragment_override_module.py` is to comparing
    source positions of two *different* functions, which says nothing about
    execution order. Both statements here are straight-line code in one function
    body, where statement order IS execution order.
    """

    def _execute_body(self):
        import ast
        import inspect
        import textwrap

        from ldm_core.pipelines.run import ExecutionStage

        src = textwrap.dedent(inspect.getsource(ExecutionStage.execute))
        function = ast.parse(src).body[0]
        assert isinstance(function, ast.FunctionDef)
        return function

    def test_the_dry_run_guard_precedes_the_readiness_poll(self):
        import ast

        function = self._execute_body()
        guard_line = None
        poll_line = None

        for node in ast.walk(function):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "Would wait for Liferay" in node.value and guard_line is None:
                    guard_line = node.lineno
            if isinstance(node, ast.Call):
                if getattr(node.func, "attr", None) == "_wait_for_ready":
                    poll_line = node.lineno

        self.assertIsNotNone(
            guard_line, "the dry-run short-circuit before _wait_for_ready is gone"
        )
        self.assertIsNotNone(poll_line, "_wait_for_ready is no longer called here")
        self.assertLess(
            cast(int, guard_line),
            cast(int, poll_line),
            "the dry-run guard must come BEFORE the readiness poll, or a dry run "
            "waits the full --timeout for a container that was never started",
        )


if __name__ == "__main__":
    unittest.main()
