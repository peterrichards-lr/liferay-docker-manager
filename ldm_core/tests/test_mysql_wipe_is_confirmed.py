"""A MySQL schema wipe that failed must not pass silently into the import (LDM-#2140).

`_execute_orchestrated_db_restore` wipes the target schema before importing,
because a dump that lacks `DROP TABLE` would otherwise land on top of the
existing one. The PostgreSQL branch is careful about this: it retries for ~30
seconds, classifies the error, tracks `wipe_success`, and warns when it never
succeeded. The MySQL/MariaDB branch twenty lines below it fired one command with
`check=False` and read nothing.

The retry exists for a reason that is not specific to PostgreSQL. Its own
comment says a freshly created container "will initialize and then restart
itself, causing temporary connection refusals" -- that is a container-lifecycle
property, and a MySQL or MariaDB container does the same thing. So the MySQL
branch was exposed to precisely the race the PostgreSQL branch was built to
survive, and could not even report losing it.

These tests assert the OUTCOME -- was the wipe retried, was the operator warned
-- and count wipe attempts across either call mechanism, so they do not encode
how the fix is implemented.
"""

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.snapshot.database import DatabaseSnapshotService

_DROP = "DROP DATABASE IF EXISTS"


class _Facade:
    def __init__(self, manager):
        self.manager = manager


def _is_wipe(call) -> bool:
    """True when this call carries the wipe SQL, whatever path issued it."""
    for arg in call.args:
        if isinstance(arg, (list, tuple)) and any(
            isinstance(p, str) and _DROP in p for p in arg
        ):
            return True
    return False


class TheMySqlWipeIsConfirmedBeforeTheImport(unittest.TestCase):
    def _manager(self):
        manager = MagicMock()
        manager.target = None
        manager.defaults = {}
        manager.run_command = MagicMock(return_value=None)
        return manager

    def _restore(self, *, wipe_fails: bool, db_type: str = "mysql"):
        """Drives the real restore with a wipe that fails or succeeds.

        Returns (wipe_attempts, warnings).
        """
        manager = self._manager()
        attempts = []

        def fake_subprocess_run(cmd, *a, **kw):
            # The MySQL wipe carries its SQL in the command (`-e "DROP ..."`);
            # the PostgreSQL wipe pipes it on stdin. Detect both, so this
            # harness can drive either branch.
            piped = kw.get("input") or b""
            if isinstance(piped, bytes):
                piped = piped.decode("utf-8", errors="ignore")
            in_cmd = isinstance(cmd, (list, tuple)) and any(
                isinstance(p, str) and _DROP in p for p in cmd
            )
            in_stdin = "DROP TABLE IF EXISTS" in piped or _DROP in piped
            if in_cmd or in_stdin:
                attempts.append(cmd)
                if wipe_fails:
                    raise subprocess.CalledProcessError(
                        1, cmd, output=b"", stderr=b"ERROR 2002 (HY000): Can't connect"
                    )
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        def fake_run_command(cmd, *a, **kw):
            if isinstance(cmd, (list, tuple)) and any(
                isinstance(p, str) and _DROP in p for p in cmd
            ):
                attempts.append(cmd)
                return None if wipe_fails else ""
            return ""

        manager.run_command = MagicMock(side_effect=fake_run_command)

        with TemporaryDirectory() as tmp:
            sql_file = Path(tmp) / "database.sql"
            sql_file.write_text("SELECT 1;", encoding="utf-8")
            root = Path(tmp) / "proj"
            root.mkdir()
            paths = {"root": root}

            svc = DatabaseSnapshotService(_Facade(manager))
            with (
                patch(
                    "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
                    return_value=["docker"],
                ),
                patch("ldm_core.utils.resolve_database_mode", return_value="isolated"),
                patch("subprocess.run", side_effect=fake_subprocess_run),
                patch("time.sleep"),
                patch("ldm_core.ui.UI.warning") as warning,
            ):
                try:
                    svc._execute_orchestrated_db_restore(
                        "proj-db", db_type, str(sql_file), paths, {}
                    )
                except SystemExit:
                    pass

        said = " ".join(str(c) for c in warning.call_args_list)
        return attempts, said

    def test_a_failing_mysql_wipe_is_retried(self):
        """The PostgreSQL branch retries ~30s for the initialising container.
        MySQL containers restart themselves during init too."""
        attempts, _said = self._restore(wipe_fails=True)
        self.assertGreater(
            len(attempts),
            1,
            "A MySQL wipe that fails must be retried, exactly as the "
            "PostgreSQL branch is -- a freshly created container refuses "
            "connections while it initialises and restarts itself. Got "
            f"{len(attempts)} attempt(s).",
        )

    def test_a_failing_mysql_wipe_warns(self):
        """The defect: the import proceeded onto a schema that was never emptied."""
        _attempts, said = self._restore(wipe_fails=True)
        self.assertIn(
            "wipe",
            said.lower(),
            "A MySQL wipe that never succeeded must warn before the import "
            "runs -- otherwise the dump lands on a schema that was never "
            f"emptied and nothing says so (LDM-#2140). Warnings seen: {said}",
        )

    def test_a_successful_mysql_wipe_does_not_warn(self):
        """A warning on every healthy restore would train everyone to ignore it."""
        _attempts, said = self._restore(wipe_fails=False)
        self.assertNotIn(
            "could not confirm",
            said.lower(),
            f"A successful wipe must not warn. Got: {said}",
        )

    def test_a_successful_mysql_wipe_is_not_retried(self):
        """Retrying a wipe that worked would drop the schema the import needs."""
        attempts, _said = self._restore(wipe_fails=False)
        self.assertEqual(
            len(attempts),
            1,
            "A wipe that succeeds first time must be issued exactly once per "
            f"import attempt. Got {len(attempts)}.",
        )

    def test_mariadb_is_covered_too(self):
        """The branch is `db_type in ["mysql", "mariadb"]`; both must be guarded."""
        _attempts, said = self._restore(wipe_fails=True, db_type="mariadb")
        self.assertIn(
            "wipe",
            said.lower(),
            f"MariaDB takes the same branch and needs the same guard. Got: {said}",
        )

    def test_postgresql_still_warns_as_before(self):
        """The fix must not disturb the branch that was already correct."""
        _attempts, said = self._restore(wipe_fails=True, db_type="postgresql")
        self.assertIn(
            "wipe",
            said.lower(),
            f"The PostgreSQL warning must survive the change. Got: {said}",
        )


if __name__ == "__main__":
    unittest.main()
