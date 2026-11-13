"""LDM-#2356: `ldm reset <project> all` must remove what Liferay wrote.

Liferay runs as uid 1000 in the container, so what it writes into the
bind-mounted `logs/` is owned by uid 1000 on the host. On native Linux a host
user with any other uid -- the GitHub Ubuntu runner is 1001 -- cannot remove
it, and `cmd_reset` died with:

    PermissionError: [Errno 13] Permission denied: '.../logs/companies/...'

because it cleaned each directory with a plain `shutil.rmtree`.
`utils.safe_rmtree` already handles this: on a `PermissionError` it calls
`reclaim_volume_permissions` and retries.

The denial is injected rather than produced with `chmod`, because a test run
as root (as some CI containers are) is never denied and would pass against the
unfixed code. Reclamation itself needs Docker, so it is replaced by a recorder
that reports success; what is asserted is that `cmd_reset` reaches it and the
retry then empties the directory.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.tests.test_orchestration import MockRuntime

PROJECT = "devcon2026"


class ResetReclaimsContainerOwnedDirectories(unittest.TestCase):
    def setUp(self):
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir_obj.name).resolve() / PROJECT
        self.logs = self.root / "logs"
        # The exact shape from the failing Ubuntu job.
        (self.logs / "companies" / "72565249891551").mkdir(parents=True)
        (self.logs / "companies" / "72565249891551" / "liferay.log").write_text("x")
        self.handler = MockRuntime()
        self.handler.detect_project_path = MagicMock(return_value=self.root)  # type: ignore[method-assign]

    def tearDown(self):
        self.tmp_dir_obj.cleanup()

    def test_a_permission_denied_logs_dir_is_reclaimed_and_removed(self):
        real_rmtree = shutil.rmtree
        denied: list[object] = []

        def rmtree_denied_once(path, *args, **kwargs):
            # The first attempt on the container-owned tree is refused, as it
            # is for a host uid that is not 1000.
            if Path(path) == self.logs and not denied:
                denied.append(path)
                raise PermissionError(13, "Permission denied", str(path))
            return real_rmtree(path, *args, **kwargs)

        reclaim = MagicMock(return_value=True)
        env = {k: v for k, v in os.environ.items() if k != "LDM_DRY_RUN"}

        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(
                self.handler.manager,
                "read_meta",
                return_value={"container_name": PROJECT},
            ),
            patch.object(self.handler, "setup_paths", return_value={"logs": self.logs}),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=False
            ),
            patch.object(self.handler, "run_command", return_value=""),
            patch("shutil.rmtree", side_effect=rmtree_denied_once),
            patch("ldm_core.utils.platform.system", return_value="Linux"),
            patch("ldm_core.utils.reclaim_volume_permissions", reclaim),
        ):
            self.handler.handler.orchestration.cmd_reset(PROJECT, target="logs")

        self.assertTrue(denied, "the injected denial never fired")
        reclaim.assert_called_once_with(self.logs)
        # The directory is recreated, empty, for the next boot.
        self.assertTrue(self.logs.is_dir())
        self.assertEqual(list(self.logs.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
