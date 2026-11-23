"""A remote node's disk is measured before a run and shown in status (LDM-#2145).

A long-lived node used as an `ldm target` filled to 98MB free of 30GB. Nothing
measured it: `_check_host_disk_space` correctly skips a remote target, nothing
took its place, and `ldm run` synced and started against the node without
asking. The failure surfaced as an intermittently hanging `yarn install`.

Every probe here is mocked -- these tests start no container and dial no node.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetContext, TargetNode, save_target_node

GB = 1024**3


def _df(available_kb):
    """`df -P -k /` output as the alpine probe prints it."""
    return (
        "Filesystem           1024-blocks    Used Available Capacity Mounted on\n"
        f"overlay               31457280  31357000  {available_kb}  100% /"
    )


#: The measured node: 98MB free.
_NEARLY_FULL = _df(98 * 1024)
#: Comfortably above the 3GB threshold.
_AMPLE = _df(20 * 1024 * 1024)


def _remote_ctx():
    return TargetContext(
        target=TargetNode(name="aws-2", host="203.0.113.5"),
        is_remote=True,
        docker_prefix=["docker", "--context", "aws-2"],
        compose_prefix=["docker", "--context", "aws-2", "compose"],
    )


class ThePreRunWarning(unittest.TestCase):
    """Piece 1: warn, never refuse, and never block on a failed measurement."""

    def _warn(self, probe_output):
        from ldm_core.pipelines.run import _warn_if_remote_disk_low

        warnings: list[str] = []
        hints: list[str] = []
        with (
            patch(
                "ldm_core.diagnostics.doctor.run_command", return_value=probe_output
            ) as mock_run,
            patch("ldm_core.pipelines.run.UI.warning", side_effect=warnings.append),
            patch("ldm_core.pipelines.run.UI.hint", side_effect=hints.append),
        ):
            _warn_if_remote_disk_low(_remote_ctx())
        return warnings, hints, mock_run

    def test_a_nearly_full_node_is_named_with_its_free_space(self):
        warnings, hints, _ = self._warn(_NEARLY_FULL)
        self.assertEqual(1, len(warnings))
        self.assertIn("'aws-2'", warnings[0])
        self.assertIn("0.1GB free", warnings[0])
        self.assertEqual(1, len(hints))
        self.assertIn("ldm prune --node aws-2 --images", hints[0])

    def test_a_node_with_room_is_silent(self):
        warnings, hints, _ = self._warn(_AMPLE)
        self.assertEqual([], warnings)
        self.assertEqual([], hints)

    def test_a_failed_or_timed_out_probe_is_silent(self):
        # run_command returns None for both a non-zero exit and a timeout
        # under check=False.
        warnings, hints, _ = self._warn(None)
        self.assertEqual([], warnings)
        self.assertEqual([], hints)

    def test_the_probe_runs_on_the_node_and_is_bounded(self):
        _, _, mock_run = self._warn(_AMPLE)
        cmd = mock_run.call_args.args[0]
        self.assertEqual(["docker", "--context", "aws-2", "run"], cmd[:4])
        self.assertIsNotNone(mock_run.call_args.kwargs.get("timeout"))

    def test_a_probe_that_raises_does_not_block_the_run(self):
        from ldm_core.pipelines.run import _warn_if_remote_disk_low

        with patch(
            "ldm_core.diagnostics.doctor.run_command",
            side_effect=OSError("ssh: connection reset"),
        ):
            _warn_if_remote_disk_low(_remote_ctx())


class _StopStageError(Exception):
    """Raised from the mocked sync so the stage goes no further."""


class TheExecutionStageWiring(unittest.TestCase):
    def _execute(self, dry_run):
        from ldm_core.pipelines.run import ExecutionStage, RunPipelineContext

        context = RunPipelineContext(MagicMock())
        context.manager.dry_run = dry_run
        context.set("project_id", "proj")
        context.set("project_meta", {"container_name": "proj"})
        context.set("paths", {"root": Path("/nonexistent/proj")})
        context.set("target_context", _remote_ctx())

        order: list[str] = []

        def _sync(*_a, **_k):
            order.append("sync")
            raise _StopStageError

        with (
            patch(
                "ldm_core.pipelines.run._warn_if_remote_disk_low",
                side_effect=lambda ctx: order.append(f"disk:{ctx.target.name}"),
            ),
            patch("ldm_core.config.sync_project_to_target", side_effect=_sync),
            self.assertRaises(_StopStageError),
        ):
            ExecutionStage().execute(context)
        return order

    def test_a_remote_run_measures_the_node_before_syncing_to_it(self):
        self.assertEqual(["disk:aws-2", "sync"], self._execute(dry_run=False))

    def test_a_dry_run_does_not_start_the_probe_container(self):
        self.assertEqual(["sync"], self._execute(dry_run=True))


class TheTargetStatusColumn(unittest.TestCase):
    """Piece 2: a DISK FREE column in `ldm target status`."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / ".ldmrc"
        save_target_node(TargetNode(name="aws-2", host="203.0.113.5"), self.config_path)

        from ldm_core.handlers.config import ConfigService

        manager = MagicMock()
        manager.args = MagicMock()
        self.service = ConfigService(manager)

    def _status(self, info, df):
        calls: list = []

        def _run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return info if "info" in cmd else df

        tables: list = []
        with (
            patch("ldm_core.config._get_config_path", return_value=self.config_path),
            # LDM-#1409: cmd_target_status resolves run_command function-locally
            # from ldm_core.utils, so that is where it must be patched.
            patch("ldm_core.utils.run_command", side_effect=_run),
            patch(
                "ldm_core.docker_service.DockerService.get_context_endpoint_host",
                return_value="203.0.113.5",
            ),
            patch(
                "ldm_core.ui.UI.table",
                side_effect=lambda rows, headers: tables.append((headers, rows)),
            ),
        ):
            self.service.cmd_target_status("aws-2")
        headers, rows = tables[0]
        return dict(zip(headers, rows[0], strict=True)), calls

    def test_a_nearly_full_node_is_flagged_low(self):
        row, _ = self._status("27.0.1|8|17179869184|3", _NEARLY_FULL)
        self.assertIn("0.1 GB", row["DISK FREE"])
        self.assertIn("LOW", row["DISK FREE"])

    def test_a_node_with_room_shows_its_free_space(self):
        row, _ = self._status("27.0.1|8|17179869184|3", _AMPLE)
        self.assertEqual("20.0 GB", row["DISK FREE"])

    def test_an_unmeasurable_node_is_still_online(self):
        row, _ = self._status("27.0.1|8|17179869184|3", None)
        self.assertEqual("-", row["DISK FREE"])
        self.assertIn("ONLINE", row["STATUS"])

    def test_an_offline_node_is_not_probed_for_disk(self):
        row, calls = self._status("", _AMPLE)
        self.assertEqual("-", row["DISK FREE"])
        self.assertEqual([], [c for c, _ in calls if "run" in c])

    def test_the_disk_probe_dials_the_node_with_a_timeout(self):
        _, calls = self._status("27.0.1|8|17179869184|3", _AMPLE)
        probes = [(c, k) for c, k in calls if "run" in c]
        self.assertEqual(1, len(probes))
        cmd, kwargs = probes[0]
        self.assertIn("aws-2", cmd)
        self.assertIsNotNone(kwargs.get("timeout"))


if __name__ == "__main__":
    unittest.main()
