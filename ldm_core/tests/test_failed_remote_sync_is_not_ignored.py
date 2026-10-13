"""A failed sync to a compute node must not pass as a successful one (LDM-#2139).

`sync_project_to_target()` has always computed the right answer -- `check=False`
makes `run_command` return `None` for any non-zero exit, so `res is not None` is
`False` on an rsync that failed. Every one of its four callers discarded that
answer, calling it as a bare statement:

    ldm_core/runtime/orchestration.py   `ldm run` against a remote target
    ldm_core/pipelines/run.py (x2)      the remote and resolved-target branches
    ldm_core/handlers/config.py         project migration between nodes

So the boolean was correct and unread. Reported by the AICA team from a node
trace: rsync exited 23 every run on a `routes/default` their SSH user could not
traverse, which also made rsync abandon `--delete` for the whole transfer, so
the node accumulated stale files indefinitely with nothing said anywhere.

The fix reports the failure where it is produced rather than at four call sites,
so a fifth caller cannot reintroduce the defect. Migration additionally refuses,
because it is the one path that records the project as living on a node that may
never have received it.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TheSyncReportsItsOwnFailure(unittest.TestCase):
    """The function knows the transfer failed. It must say so."""

    def _target(self, name="aws"):
        from ldm_core.config import TargetNode

        return TargetNode(name=name, host="34.1.1.1", user="ec2-user")

    def _run_sync(self, run_command_result):
        """Drives sync_project_to_target with rsync present and a given outcome."""
        from ldm_core import config

        with (
            patch.object(config, "get_active_target", return_value=self._target()),
            patch.object(config, "is_local_host", return_value=False),
            patch.object(
                config, "remote_project_dir", return_value="~/.liferay-docker/p"
            ),
            patch("shutil.which", return_value="/usr/bin/rsync"),
            patch.object(
                config, "run_command", return_value=run_command_result
            ) as mock_run,
            patch("ldm_core.ui.UI.warning") as mock_warning,
        ):
            ok = config.sync_project_to_target(Path("/tmp/proj"), target_name="aws")
        return ok, mock_warning, mock_run

    def test_a_failed_rsync_is_reported_as_failure(self):
        """The contract that already held, locked in: None means False."""
        ok, _warning, _run = self._run_sync(None)
        self.assertFalse(
            ok,
            "sync_project_to_target must return False when run_command reports "
            "a non-zero exit by returning None.",
        )

    def test_a_failed_rsync_warns_the_operator(self):
        """The defect: the failure was computed and never surfaced."""
        _ok, warning, _run = self._run_sync(None)
        self.assertTrue(
            warning.called,
            "A failed sync to a compute node must warn. Without it the node "
            "silently diverges from the project and nothing anywhere says so "
            "(LDM-#2139).",
        )

    def test_the_warning_names_the_node(self):
        """A warning that does not say WHERE is not actionable."""
        _ok, warning, _run = self._run_sync(None)
        said = " ".join(str(c) for c in warning.call_args_list)
        self.assertIn(
            "aws",
            said,
            f"The warning must name the target it failed to reach. Got: {said}",
        )

    def test_a_successful_rsync_does_not_warn(self):
        """A warning on every healthy run would train everyone to ignore it."""
        ok, warning, _run = self._run_sync("")
        self.assertTrue(ok, "An rsync that exits 0 must report success.")
        self.assertFalse(
            warning.called,
            f"A successful sync must stay silent. Got: {warning.call_args_list}",
        )

    def test_the_tar_fallback_reports_failure_too(self):
        """The fallback below rsync has the same shape and the same defect."""
        from ldm_core import config

        with (
            patch.object(config, "get_active_target", return_value=self._target()),
            patch.object(config, "is_local_host", return_value=False),
            patch.object(
                config, "remote_project_dir", return_value="~/.liferay-docker/p"
            ),
            patch("shutil.which", return_value=None),
            patch.object(config, "run_command", return_value=None),
            patch("ldm_core.ui.UI.warning") as warning,
        ):
            ok = config.sync_project_to_target(Path("/tmp/proj"), target_name="aws")

        self.assertFalse(ok, "The tar fallback must report a failed transfer.")
        self.assertTrue(
            warning.called,
            "The tar fallback must warn on failure exactly as the rsync path "
            "does -- it is reached on hosts without rsync, which are the least "
            "likely to be watching closely.",
        )

    def test_a_local_target_is_not_a_failure(self):
        """Local needs no sync; that is success, not a silent skip."""
        from ldm_core import config

        with (
            patch.object(
                config, "get_active_target", return_value=self._target("local")
            ),
            patch("ldm_core.ui.UI.warning") as warning,
        ):
            ok = config.sync_project_to_target(Path("/tmp/proj"), target_name="local")

        self.assertTrue(ok, "A local target must report success.")
        self.assertFalse(warning.called, "A local target must not warn.")


class MigrationRefusesToReassignAProjectItCouldNotSend(unittest.TestCase):
    """`cmd_target_migrate` is step 3/5; step 4/5 rewrites meta['target'].

    A sync that failed outright still left the project recorded as living on the
    destination node, after which step 5 starts it there. The source node still
    has the files and step 1 took a snapshot, so refusing is safe -- and is the
    only way the operator finds out.
    """

    def _service(self, tmp):
        from ldm_core.handlers.config import ConfigService

        manager = MagicMock()
        manager.detect_project_path.return_value = tmp
        manager.read_meta.return_value = {"target": "local"}
        svc = ConfigService(manager)
        svc.manager = manager
        return svc, manager

    def _migrate(self, tmp, sync_result):
        """Runs a migration whose sync returns `sync_result`. Returns exit code."""
        svc, manager = self._service(tmp)
        code = None
        with (
            patch("ldm_core.config.sync_project_to_target", return_value=sync_result),
            patch(
                "ldm_core.config.load_targets",
                return_value={"local": MagicMock(), "aws": MagicMock()},
            ),
        ):
            try:
                svc.cmd_target_migrate("local", "aws")
            except SystemExit as e:
                code = e.code
        return manager, code

    def test_a_failed_sync_does_not_reassign_the_target(self):
        """The serious one: the project must not be recorded on a node it
        never reached."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            manager, _code = self._migrate(Path(td), False)

        reassigned = [
            c
            for c in manager.write_meta.call_args_list
            if isinstance(c.args[-1], dict) and c.args[-1].get("target") == "aws"
        ]
        self.assertEqual(
            reassigned,
            [],
            "A migration whose sync failed must not write target='aws' into the "
            "project metadata. Doing so records the project as living on a node "
            "that may be missing its files (LDM-#2139).",
        )

    def test_a_failed_sync_does_not_start_the_stack_on_the_destination(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            manager, _code = self._migrate(Path(td), False)

        self.assertFalse(
            manager.runtime.cmd_run.called,
            "A migration whose sync failed must not start the stack on the "
            "destination node.",
        )

    def test_a_failed_sync_exits_three(self):
        """Infrastructure/Data error, matching the restore precedent."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _manager, code = self._migrate(Path(td), False)

        self.assertEqual(
            code,
            3,
            "A failed transfer is an Infrastructure/Data error (exit 3), the "
            "same code the database restore uses when it preserves the "
            "original data.",
        )

    def test_a_successful_sync_still_completes_the_migration(self):
        """The fix must not break the path that works."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            manager, code = self._migrate(Path(td), True)

        self.assertIsNone(code, "A successful migration must not exit early.")
        self.assertTrue(
            manager.runtime.cmd_run.called,
            "A successful migration must still start the stack on the destination.",
        )
        reassigned = [
            c
            for c in manager.write_meta.call_args_list
            if isinstance(c.args[-1], dict) and c.args[-1].get("target") == "aws"
        ]
        self.assertTrue(
            reassigned,
            "A successful migration must still record the new target.",
        )


if __name__ == "__main__":
    unittest.main()
