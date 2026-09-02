"""A shared database that never starts must fail the run (LDM-#1545).

`setup_global_database`'s readiness loop had no failure path. It broke on
success, broke after `UI.error` -- which prints and RETURNS, since only
`UI.die` calls sys.exit -- or exhausted 60 attempts and fell through silently.

So "the global database never came up" could not produce a non-zero exit. In
CI, `ldm run --db mysql --database-mode shared` exited 0 while `docker ps -a`
showed the container had never been created. Nothing in LDM objected; the E2E
caught it only because LDM-#1494 inspects the container directly.

Exit code 3 is the contract's Infrastructure/Data Error
(.agents/skills/ldm-architecture/SKILL.md).
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core.handlers.infra import InfraService


class _Manager:
    def __init__(self, status, probe_result):
        self.args = MagicMock()
        self.target = None
        self._status = status
        self._probe = probe_result
        self.run_command = MagicMock(side_effect=self._run)

    def get_container_status(self, name, target_name=None):
        return self._status

    def _run(self, cmd, **kwargs):
        # The readiness probe is the only call whose result is inspected.
        if any("mysqladmin" in str(c) or "pg_isready" in str(c) for c in cmd):
            return self._probe
        return ""


def _setup(status, probe_result, *, exists=True, running=True):
    svc = InfraService(_Manager(status, probe_result))
    with (
        patch("ldm_core.docker_service.DockerService.exists", return_value=exists),
        patch("ldm_core.docker_service.DockerService.is_running", return_value=running),
        patch("ldm_core.docker_service.DockerService.start"),
        patch(
            "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
            return_value=["docker"],
        ),
        patch("time.sleep"),
    ):
        svc.setup_global_database(db_type="mysql")


class TestGlobalDatabaseReadinessFails(unittest.TestCase):
    def test_a_container_that_never_becomes_ready_exits_3(self):
        # exists=False forces the create-then-wait path; the probe never succeeds.
        with self.assertRaises(SystemExit) as ctx:
            _setup("running", None, exists=False)
        self.assertEqual(
            ctx.exception.code,
            3,
            "a database that never came up must be an Infrastructure error, "
            "not a silent success (LDM-#1545)",
        )

    def test_a_container_that_exits_is_reported_as_3(self):
        with self.assertRaises(SystemExit) as ctx:
            _setup("exited", None, exists=False)
        self.assertEqual(ctx.exception.code, 3)

    def test_a_ready_database_does_not_raise(self):
        # The guard must not turn a healthy provision into a failure.
        _setup("running", "mysqld is alive", exists=False)


if __name__ == "__main__":
    unittest.main()


class TestExistingButStoppedGlobal(unittest.TestCase):
    """The other half of the same gap (LDM-#1545).

    DockerService.start runs with check=False and its result was discarded,
    while the readiness probe sat inside `if not exists:` -- so a global that
    already existed and failed to restart was never probed at all. LDM then
    configured Liferay against a dead database and reported success.
    """

    def _start_existing(self, running_after):
        svc = InfraService(_Manager("running", "alive"))
        with (
            patch("ldm_core.docker_service.DockerService.exists", return_value=True),
            patch(
                "ldm_core.docker_service.DockerService.is_running",
                side_effect=[False, running_after],
            ),
            patch("ldm_core.docker_service.DockerService.start"),
            patch(
                "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
                return_value=["docker"],
            ),
            patch("time.sleep"),
        ):
            svc.setup_global_database(db_type="mysql")

    def test_a_failed_restart_exits_3(self):
        with self.assertRaises(SystemExit) as ctx:
            self._start_existing(running_after=False)
        self.assertEqual(ctx.exception.code, 3)

    def test_a_successful_restart_proceeds(self):
        self._start_existing(running_after=True)
