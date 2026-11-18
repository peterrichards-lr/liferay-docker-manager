"""The background update check must not write once the command exits (LDM-#2432).

`_execute_command` runs `check_for_updates` on a daemon thread. A command that
ended through `UI.die` skipped the thread's join entirely, and a thread still
writing when the interpreter finalized could hold stdout's buffer lock, so
CPython aborted with "_enter_buffered_busy ... possibly due to daemon threads"
(exit 134) instead of exiting 1.

The real abort reproduces every time against the unfixed code, but each one
makes macOS raise a crash dialog and write a diagnostic report, so this test
asserts the mechanism instead and leaves the process with `os._exit`, which
skips finalization: the update thread must write nothing after `UI.die` has
raised. Against the unfixed code the thread keeps printing and the test fails
without crashing anything.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]

# Runs in a child so that the daemon thread, the SystemExit and the module-level
# shutdown flag cannot leak into the pytest process.
_CHILD = r"""
import argparse, os, sys, threading, time
from unittest.mock import patch

from ldm_core import cli
from ldm_core.ui import UI

started = threading.Event()

def blocking_update_check(_version, **_kwargs):
    # Stands in for check_for_updates: writes continuously through UI, the
    # path its own UI.debug/UI.warning and the logging bridge take.
    started.set()
    n = 0
    while True:
        n += 1
        UI._print(f"TICK {n} " + "x" * 2000, file=sys.stdout)

def refusing_handler():
    started.wait(5)
    UI.die("refused")

args = argparse.Namespace(command="config", subcommand="env")
code = None
with patch.object(cli, "check_for_updates", blocking_update_check):
    try:
        cli._execute_command(args, ("config", "env"), {("config", "env"): refusing_handler})
    except SystemExit as e:
        code = e.code

print("EXIT-BEGIN", flush=True)
# Give a still-writing thread ample time to show itself.
time.sleep(0.3)
sys.stdout.flush()
os._exit(code if isinstance(code, int) else 99)
"""


class TestUpdateThreadStopsWritingAtExit(unittest.TestCase):
    def test_ui_die_exit_stops_update_thread_writes(self):
        env = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS")}
        with tempfile.TemporaryDirectory() as home:
            env["LDM_HOME"] = home
            env["PYTHONPATH"] = str(REPO_ROOT)
            proc = subprocess.run(
                [sys.executable, "-c", _CHILD],
                check=False,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr[-2000:])
        self.assertNotIn("Fatal Python error", proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertIn("EXIT-BEGIN", lines)
        marker = lines.index("EXIT-BEGIN")
        before = [ln for ln in lines[:marker] if ln.startswith("TICK")]
        after = [ln for ln in lines[marker + 1 :] if ln.startswith("TICK")]
        # The stub must genuinely have been writing, or "nothing after" is vacuous.
        self.assertTrue(before, "update-check stub never wrote")
        self.assertEqual(len(after), 0, "update thread wrote after exit began")


class TestUpdateCheckSkippedInCI(unittest.TestCase):
    def _run(self, env_overrides):
        from ldm_core.cli import _execute_command

        clean = {
            k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS")
        }
        clean.update(env_overrides)
        args = type("Args", (), {"command": "config", "subcommand": "env"})()
        with (
            patch.dict(os.environ, clean, clear=True),
            patch("ldm_core.cli.check_for_updates", return_value=(None, None)) as check,
        ):
            _execute_command(args, ("config", "env"), {("config", "env"): lambda: None})
            # The thread is joined for only 50 ms; allow a slow scheduler.
            deadline = time.monotonic() + 2
            while not check.called and time.monotonic() < deadline:
                time.sleep(0.01)
        return check

    def test_skipped_when_ci_is_set(self):
        self.assertFalse(self._run({"CI": "true"}).called)

    def test_skipped_when_github_actions_is_set(self):
        self.assertFalse(self._run({"GITHUB_ACTIONS": "true"}).called)

    def test_still_runs_outside_ci(self):
        self.assertTrue(self._run({}).called)


if __name__ == "__main__":
    unittest.main()
