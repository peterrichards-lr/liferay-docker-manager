"""A virtualhost UPDATE that failed must not read as a successful one (LDM-#2141).

After a restore, LDM rewrites the `virtualhost` table so the project answers on
this machine's hostname rather than the one the snapshot came from. Both the
PostgreSQL and the MySQL/MariaDB branch ran that UPDATE with `check=False` and
discarded the result.

The failure was quiet in the worst way. `UI.detail` announces
`- Synchronizing Virtual Host entries to: <host>` *before* the statement runs,
so the line appears whether or not it lands -- and `+ Database restored
successfully.` is printed immediately above it. A restore that left the source
machine's hostname in the database was indistinguishable in the output from one
that worked.

It is also the last statement in `_execute_orchestrated_db_restore`, and the
caller reads nothing back, so there was no later opportunity to notice.

A warning rather than a refusal: the data is restored and usable, and this is a
post-restore adjustment. But the symptom -- a site that will not resolve, or
OAuth redirect URIs registered against the wrong host -- surfaces far from the
cause, so the operator has to be told.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.snapshot.database import DatabaseSnapshotService

_UPDATE = "virtualhost SET hostname"


class _Facade:
    def __init__(self, manager):
        self.manager = manager


class TheVirtualHostSyncReportsWhetherItLanded(unittest.TestCase):
    def _restore(self, *, update_fails: bool, db_type: str = "postgresql"):
        """Drives a successful restore whose virtualhost UPDATE fails or not.

        Returns (update_attempted, warnings_text, success_reported).
        """
        manager = MagicMock()
        manager.target = None
        manager.defaults = {}
        attempted = []

        def fake_run_command(cmd, *a, **kw):
            if isinstance(cmd, (list, tuple)) and any(
                isinstance(p, str) and _UPDATE in p for p in cmd
            ):
                attempted.append(cmd)
                return None if update_fails else ""
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
                patch("subprocess.run", return_value=MagicMock(returncode=0)),
                patch("time.sleep"),
                patch("ldm_core.ui.UI.warning") as warning,
                patch("ldm_core.ui.UI.success") as success,
            ):
                try:
                    svc._execute_orchestrated_db_restore(
                        "proj-db",
                        db_type,
                        str(sql_file),
                        paths,
                        {"host_name": "demo.lfr.test"},
                    )
                except SystemExit:
                    pass

        said = " ".join(str(c) for c in warning.call_args_list)
        reported = " ".join(str(c) for c in success.call_args_list)
        return attempted, said, reported

    def test_the_update_is_actually_attempted(self):
        """Guards the harness: if this never ran, the rest proves nothing."""
        attempted, _said, _ok = self._restore(update_fails=False)
        self.assertTrue(
            attempted,
            "The harness must reach the virtualhost UPDATE, or every other "
            "assertion in this file is vacuous.",
        )

    def test_a_failed_update_warns(self):
        """The defect: the result was computed by run_command and discarded."""
        _attempted, said, _ok = self._restore(update_fails=True)
        self.assertIn(
            "virtual host",
            said.lower(),
            "A virtualhost UPDATE that failed must warn. Otherwise the project "
            "keeps answering on the hostname it was snapshotted from and "
            f"nothing says so (LDM-#2141). Warnings seen: {said}",
        )

    def test_the_warning_names_the_hostname_that_was_wanted(self):
        """'It failed' is not actionable; 'it failed to become X' is."""
        _attempted, said, _ok = self._restore(update_fails=True)
        self.assertIn(
            "demo.lfr.test",
            said,
            f"The warning must name the hostname it could not set. Got: {said}",
        )

    def test_a_failed_update_still_reports_the_restore_succeeded(self):
        """The data IS restored. This is a post-restore adjustment, not a
        reason to claim the restore failed."""
        _attempted, _said, reported = self._restore(update_fails=True)
        self.assertIn(
            "restored successfully",
            reported.lower(),
            "The restore itself succeeded and must still say so -- the "
            f"virtualhost sync is an adjustment after it. Got: {reported}",
        )

    def test_a_successful_update_does_not_warn(self):
        _attempted, said, _ok = self._restore(update_fails=False)
        self.assertNotIn(
            "virtual host",
            said.lower(),
            f"A successful sync must stay silent. Got: {said}",
        )

    def test_mysql_is_covered_too(self):
        """Both branches had the identical defect; both need the guard."""
        _attempted, said, _ok = self._restore(update_fails=True, db_type="mysql")
        self.assertIn(
            "virtual host",
            said.lower(),
            f"The MySQL branch needs the same guard. Got: {said}",
        )

    def test_mariadb_is_covered_too(self):
        _attempted, said, _ok = self._restore(update_fails=True, db_type="mariadb")
        self.assertIn(
            "virtual host",
            said.lower(),
            f"The MariaDB branch needs the same guard. Got: {said}",
        )


if __name__ == "__main__":
    unittest.main()
