"""Unit tests for standalone target node power management script (scripts/manage_target_nodes.py).

Last Updated: 2026-08-20 | Last Reviewed: 2026-08-20
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import manage_target_nodes
from manage_target_nodes import (
    is_in_shutdown_window,
    parse_duration,
)


class TestManageTargetNodes(unittest.TestCase):
    def test_parse_duration(self) -> None:
        """Test parse_duration converts strings like '2h', '30m', '1d'."""
        self.assertEqual(parse_duration("2h"), timedelta(hours=2))
        self.assertEqual(parse_duration("30m"), timedelta(minutes=30))
        self.assertEqual(parse_duration("1d"), timedelta(days=1))
        # Fallback default
        self.assertEqual(parse_duration("invalid"), timedelta(hours=2))

    def test_is_in_shutdown_window_overnight(self) -> None:
        """Test overnight shutdown window detection (19:00 - 07:00)."""
        # Tuesday 20:00 (Overnight)
        dt_night = datetime(2026, 8, 18, 20, 0, 0)
        self.assertTrue(is_in_shutdown_window(dt_night, "overnight"))
        self.assertTrue(is_in_shutdown_window(dt_night, "auto"))

        # Tuesday 12:00 (Daytime)
        dt_day = datetime(2026, 8, 18, 12, 0, 0)
        self.assertFalse(is_in_shutdown_window(dt_day, "overnight"))
        self.assertFalse(is_in_shutdown_window(dt_day, "auto"))

    def test_is_in_shutdown_window_weekend(self) -> None:
        """Test weekend shutdown window detection (Fri 19:00 - Mon 07:00)."""
        # Saturday 14:00 (Weekend)
        dt_sat = datetime(2026, 8, 22, 14, 0, 0)
        self.assertTrue(is_in_shutdown_window(dt_sat, "weekend"))
        self.assertTrue(is_in_shutdown_window(dt_sat, "auto"))

        # Wednesday 14:00 (Weekday Daytime)
        dt_wed = datetime(2026, 8, 19, 14, 0, 0)
        self.assertFalse(is_in_shutdown_window(dt_wed, "weekend"))

    def test_schedule_off(self) -> None:
        """Test schedule 'off' disables shutdown window enforcement."""
        dt_sat = datetime(2026, 8, 22, 14, 0, 0)
        self.assertFalse(is_in_shutdown_window(dt_sat, "off"))


if __name__ == "__main__":
    unittest.main()


class TestDockerContextRefresh(unittest.TestCase):
    """LDM-#1346: an IP refresh that skips the Docker context fixes nothing.

    `docker --context <node>` is what every remote LDM command dials, and its
    endpoint lives in Docker's own store -- so rewriting `~/.ldmrc` alone left
    the context on the dead address on the very path that had just resolved the
    live one.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ldmrc = Path(self.temp_dir.name) / ".ldmrc"
        self.ldmrc.write_text(
            json.dumps(
                {
                    "targets": {
                        "aws-1": {
                            "host": "51.20.52.201",
                            "user": "ec2-user",
                            "key_path": "~/.ssh/aws-key.pem",
                        }
                    }
                }
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _docker_calls(self, runs):
        """Extracts the docker argv lists from recorded subprocess.run calls."""
        return [
            c.args[0]
            for c in runs.call_args_list
            if c.args and isinstance(c.args[0], list) and c.args[0][:1] == ["docker"]
        ]

    def test_the_context_is_recreated_at_the_new_address(self):
        with patch("manage_target_nodes.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            ok = manage_target_nodes.update_docker_context(
                "aws-1", "13.49.210.78", "ec2-user"
            )

        self.assertTrue(ok)
        calls = self._docker_calls(mock_run)
        self.assertIn(["docker", "context", "rm", "aws-1"], calls)
        self.assertIn(
            [
                "docker",
                "context",
                "create",
                "aws-1",
                "--docker",
                "host=ssh://ec2-user@13.49.210.78",
            ],
            calls,
        )

    def test_the_ssh_user_comes_from_ldmrc_not_the_power_config(self):
        """The power config names the automation account, which cannot reach the daemon."""
        with patch.object(manage_target_nodes, "LDMRC_FILE", self.ldmrc):
            self.assertEqual(
                "ec2-user", manage_target_nodes.get_docker_context_user("aws-1")
            )
            self.assertEqual(
                "", manage_target_nodes.get_docker_context_user("no-such-node")
            )

    def test_the_user_is_resolved_when_not_supplied(self):
        with (
            patch.object(manage_target_nodes, "LDMRC_FILE", self.ldmrc),
            patch("manage_target_nodes.subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            manage_target_nodes.update_docker_context("aws-1", "13.49.210.78")

        create = next(c for c in self._docker_calls(mock_run) if "create" in c)
        self.assertIn("host=ssh://ec2-user@13.49.210.78", create)

    def test_an_unknown_user_still_produces_a_usable_endpoint(self):
        """Better a context Docker can complete from ~/.ssh/config than none."""
        with (
            patch.object(manage_target_nodes, "LDMRC_FILE", Path("/nonexistent")),
            patch("manage_target_nodes.subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            manage_target_nodes.update_docker_context("aws-1", "13.49.210.78")

        create = next(c for c in self._docker_calls(mock_run) if "create" in c)
        self.assertIn("host=ssh://13.49.210.78", create)

    def test_a_docker_failure_is_reported_but_not_fatal(self):
        """The config files are already correct; losing the context is not worth aborting."""
        with patch("manage_target_nodes.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "docker: command not found"
            ok = manage_target_nodes.update_docker_context(
                "aws-1", "13.49.210.78", "ec2-user"
            )
        self.assertFalse(ok)

    def test_an_empty_ip_touches_nothing(self):
        with patch("manage_target_nodes.subprocess.run") as mock_run:
            self.assertFalse(manage_target_nodes.update_docker_context("aws-1", ""))
        mock_run.assert_not_called()

    def test_update_node_host_ip_refreshes_ldmrc_and_the_context_together(self):
        """The regression under test: the two must not be able to drift apart."""
        with (
            patch.object(manage_target_nodes, "LDMRC_FILE", self.ldmrc),
            patch.object(
                manage_target_nodes, "CONFIG_FILE", Path("/nonexistent-config.json")
            ),
            patch("manage_target_nodes.subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            manage_target_nodes.update_node_host_ip("aws-1", "13.49.210.78")

        stored = json.loads(self.ldmrc.read_text())["targets"]["aws-1"]["host"]
        self.assertEqual("13.49.210.78", stored)

        create = next(c for c in self._docker_calls(mock_run) if "create" in c)
        self.assertIn("host=ssh://ec2-user@13.49.210.78", create)
