"""A boot that never became healthy must not exit 0 (LDM-#1790).

Split out of LDM-#1782, where this turned a fifteen-minute boot failure into a
twenty-five-minute one in a different subsystem.

`ldm run` printed:

```
❌  Timed out waiting for Liferay to become healthy.
```

and exited **0**. The calling script ran under `set -e`, carried on, fell
through to a hardcoded `http://localhost:8080`, and spent five more minutes
failing to seed a Liferay that was never up. `fetch failed` is what it
reported.

## Why the False was lost

`ExecutionStage.execute` ended with `return ready`. But `PipelineStage.execute`
is declared `-> None`, and the runner discards the value:

```python
stage.execute(context)          # pipelines/base.py
self.executed_stages.append(stage)
```

So there was no path from a readiness timeout to a non-zero exit. The same
condition reached by the other route already exits non-zero: `ldm wait` calls
`UI.die`. Identical state, two commands, two exit codes.

## Why exit 3

The configuration was accepted and the environment did not deliver, which is
the reading `_verify_pinned_mac` already takes for the MAC check. 1 would say
the user got their input wrong.

## Why the stack is left running

It is the evidence. Rollback does run on the way out -- `UI.die` raises
`SystemExit`, which the pipeline catches, rolls back and re-raises -- and it is
a no-op here: `ProjectInitializationStage` deletes only a project it created
*and* whose init did not succeed, and `init_success` is set by
`EnvironmentSetupStage`, two stages earlier. `TheProjectSurvives` asserts that
rather than trusting it, because a fix that deleted the user's new project on a
slow boot would be far worse than the bug it replaced.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core.pipelines.run import (
    ExecutionStage,
    ProjectInitializationStage,
    RunPipelineContext,
)


class ReadinessFailureHarness(unittest.TestCase):
    """Drives the real `ExecutionStage` to its readiness decision."""

    def setUp(self):
        self.context = RunPipelineContext(MagicMock())
        self.context.set("project_id", "test-project")
        self.context.set("is_new_project", False)
        self.context.set("dry_run", False)
        self.context.set("no_up", False)
        self.context.set("paths", {"root": MagicMock()})
        self.context.set("project_meta", {"container_name": "test-project"})
        self.context.manager.non_interactive = True
        # Every one of these must be a real False. `manager` is a MagicMock, so
        # an unset attribute is a Mock -- which is truthy, and silently sends
        # the stage down the dry-run branch instead of to the readiness wait.
        for flag in (
            "no_wait",
            "follow",
            "force_recreate",
            "rebuild",
            "quiet",
            "dry_run",
            "no_up",
        ):
            setattr(self.context.manager.args, flag, False)
        self.context.manager.dry_run = False
        self.context.manager.args.timeout = 900
        # Dependencies report healthy so the two 60-second readiness polls exit
        # on their first iteration. Left as a MagicMock they never match
        # {"healthy", "running"}, and each loop spins for a real minute --
        # `time.sleep` is patched but `time.time` is not. That is three minutes
        # of wall clock for five assertions about something else entirely.
        self.context.manager.get_container_status.return_value = "healthy"

    def run_stage(self, ready):
        """Runs the stage with readiness forced to `ready`. Returns the exit
        code it died with, or None if it did not die."""
        self.context.manager.runtime._wait_for_ready.return_value = ready
        with (
            patch("ldm_core.config.sync_project_to_target"),
            patch("ldm_core.pipelines.run.offer_shared_database_tip"),
            # The dependency-readiness loops poll for up to 60s each. Nothing
            # here is about them, and a real sleep would make this suite a
            # minute slower for no assertion.
            patch("ldm_core.pipelines.run.time.sleep"),
            patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit) as died,
        ):
            try:
                ExecutionStage().execute(self.context)
            except SystemExit:
                pass
        if not died.called:
            return None
        return died.call_args.kwargs.get("exit_code")


class AFailedBootIsReported(ReadinessFailureHarness):
    def test_a_readiness_failure_does_not_exit_zero(self):
        """The whole defect. `return ready` reached nothing."""
        self.assertIsNotNone(
            self.run_stage(ready=False),
            "a boot that never became healthy exited without an error",
        )

    def test_it_uses_the_infrastructure_exit_code(self):
        """3, not 1: the configuration was accepted and the environment did not
        deliver. Automation branches on this."""
        self.assertEqual(self.run_stage(ready=False), 3)

    def test_a_successful_boot_is_untouched(self):
        """A guard that fires on the happy path gets disabled within a week."""
        self.assertIsNone(self.run_stage(ready=True))


class TheProjectSurvives(unittest.TestCase):
    """`UI.die` triggers rollback. On a readiness timeout that must do nothing.

    A fix that deleted a user's brand-new project because its first boot was
    slow would be worse than the bug it replaced.
    """

    def rollback_with(self, *, is_new_project, init_success):
        context = RunPipelineContext(MagicMock())
        context.set("project_id", "test-project")
        context.set("is_new_project", is_new_project)
        context.set("init_success", init_success)
        context.set("root_existed", False)
        root = MagicMock()
        root.exists.return_value = True
        context.set("root", root)

        ProjectInitializationStage().rollback(context)
        return context.manager

    def test_a_timed_out_boot_deletes_nothing(self):
        """`init_success` is set by `EnvironmentSetupStage`, which runs before
        `ExecutionStage` -- so by the time readiness fails it is True."""
        manager = self.rollback_with(is_new_project=True, init_success=True)

        manager.safe_rmtree.assert_not_called()
        manager.unregister_project.assert_not_called()

    def test_a_genuinely_failed_init_still_cleans_up(self):
        """The rollback must keep doing its own job (LDM-#1630)."""
        manager = self.rollback_with(is_new_project=True, init_success=False)

        manager.safe_rmtree.assert_called_once()
        manager.unregister_project.assert_called_once()


if __name__ == "__main__":
    unittest.main()
