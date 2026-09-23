"""A full Docker host must name LDM's own reclamation command (LDM-#1906).

A user's EC2 node filled up. LDM surfaced Docker's raw "no space left on
device" and nothing else, and an experienced operator concluded in public that
LDM could not reclaim the space. It can -- `ldm system prune --images` runs
`docker image prune -af` AND `docker builder prune -af` (LDM-#1086), and build
cache was the largest single item in the reported case.

Asserted through `run_command(check=True)` rather than against the classifier
directly. The bug being guarded is "the classifier exists and nothing attaches
it", which a test of the pure function cannot see.
"""

import io
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from ldm_core.utils import run_command

ENOSPC = (
    "write /var/lib/docker/overlay2/9f3a/diff/opt/liferay/x: no space left on device"
)


def _run(cmd, stderr_text):
    """Returns (stderr, stdout) from a failing `run_command(check=True)`."""
    err = subprocess.CalledProcessError(1, cmd, output=b"", stderr=stderr_text.encode())
    err_buf, out_buf = io.StringIO(), io.StringIO()
    with patch("subprocess.run", side_effect=err):
        with redirect_stderr(err_buf), redirect_stdout(out_buf):
            try:
                run_command(cmd, check=True)
            except SystemExit:
                pass
    return err_buf.getvalue(), out_buf.getvalue()


class TestTheRemedyIsNamed(unittest.TestCase):
    def test_a_full_docker_host_names_ldms_own_reclamation_command(self):
        err, _ = _run(["docker", "compose", "up", "-d"], ENOSPC)
        self.assertIn("ldm system prune --images", err)

    def test_a_remote_failure_carries_the_node_so_the_command_is_copy_pasteable(self):
        """Without the node the user has to work out which host filled up."""
        err, _ = _run(["docker", "--context", "aws-1", "compose", "up", "-d"], ENOSPC)
        self.assertIn("ldm system prune --images --node aws-1", err)
        self.assertIn("aws-1", err)

    def test_a_shell_string_command_is_handled_as_well_as_a_list(self):
        err, _ = _run("docker --context aws-1 compose up -d", ENOSPC)
        self.assertIn("--node aws-1", err)


class TestItDoesNotFireOnEverythingElse(unittest.TestCase):
    """A classifier that always fires makes the tests above vacuous."""

    def test_an_ordinary_docker_failure_gets_no_disk_tip(self):
        err, _ = _run(
            ["docker", "compose", "up", "-d"],
            "service 'liferay' has neither an image nor a build context",
        )
        self.assertNotIn("system prune", err)

    def test_a_non_docker_enospc_is_not_sent_to_docker_prune(self):
        """`ldm system prune` reclaims Docker's storage. A gzip ENOSPC is a
        different disk and the command cannot help."""
        err, _ = _run(["gzip", "dump.sql"], ENOSPC)
        self.assertNotIn("system prune", err)

    def test_an_empty_stderr_is_not_treated_as_a_disk_failure(self):
        err, _ = _run(["docker", "compose", "up", "-d"], "")
        self.assertNotIn("system prune", err)


class TestTheUnderlyingErrorSurvives(unittest.TestCase):
    """The difference from the LDM-#1345 diagnosis, which suppresses stderr.

    Here the stderr names the path that filled, which is the half worth
    keeping -- a later 'tidy-up' that demotes it would lose the only clue
    about WHICH volume is full.
    """

    def test_the_docker_error_is_still_shown_to_the_user(self):
        err, out = _run(["docker", "compose", "up", "-d"], ENOSPC)
        self.assertIn("no space left on device", err + out)


if __name__ == "__main__":
    unittest.main()
