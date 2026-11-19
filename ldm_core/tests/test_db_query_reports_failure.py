"""`ldm db query` tells a failed query from an empty result (LDM-#2431).

Both printed nothing and exited 0. psql reading SQL on stdin exits 0 after a
failed statement unless `ON_ERROR_STOP` is set, and the empty-result message
was `UI.detail`, hidden at default verbosity. A query naming a column absent on
the running DXP looked exactly like "no match".

`ldm db reset-admin` pipes its UPDATE to psql the same way, so a failed reset
reported success.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetNode
from ldm_core.handlers.database import DatabaseService
from ldm_core.tests.test_database import MockManager


class _Harness(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.manager.database = DatabaseService(self.manager)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "docker-compose.yml").touch()
        # isolate from a persisted default target in the real ~/.ldmrc
        patcher = patch(
            "ldm_core.docker_service.get_active_target",
            return_value=TargetNode(name="local", host="localhost", is_default=True),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def query(self, db_type, *, returncode=0, stdout=b"", stderr=b"", fmt="table"):
        """Runs the real cmd_query against a simulated database client.
        Returns (exit code or None, the client argv, what was printed)."""
        self.manager.detect_project_path = MagicMock(return_value=self.root)  # type: ignore[method-assign]
        self.manager.read_meta = MagicMock(return_value={"db_type": db_type})  # type: ignore[method-assign]
        self.manager.run_command = MagicMock(return_value="container-id")  # type: ignore[method-assign]
        res = MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
        with (
            patch("subprocess.run", return_value=res) as run,
            patch("builtins.print") as printed,
            patch("sys.stdout.write") as written,
            patch("ldm_core.ui.UI.die", side_effect=SystemExit) as died,
        ):
            try:
                self.manager.database.cmd_query(
                    project_id="test",
                    sql="SELECT nosuchcolumn FROM fragmententrylink;",
                    output_format=fmt,
                    allow_query=True,
                )
            except SystemExit:
                pass
        code = died.call_args.kwargs.get("exit_code", 1) if died.called else None
        out = [c.args[0] for c in printed.call_args_list] + [
            c.args[0] for c in written.call_args_list
        ]
        self.died = died
        return code, run.call_args.args[0], out


class AFailedQueryFails(_Harness):
    def test_psql_stops_on_the_first_error(self):
        """Without it psql exits 0 after the failed statement: the whole bug."""
        _, argv, _ = self.query("postgresql")
        self.assertIn("ON_ERROR_STOP=1", argv)
        self.assertEqual(argv[argv.index("ON_ERROR_STOP=1") - 1], "-v")

    def test_a_client_error_exits_1_with_the_database_message(self):
        for db_type in ("postgresql", "mysql"):
            with self.subTest(db_type=db_type):
                code, _, out = self.query(
                    db_type,
                    returncode=3,
                    stderr=b'ERROR:  column "nosuchcolumn" does not exist',
                )
                self.assertEqual(code, 1)
                self.assertIn(
                    'column "nosuchcolumn" does not exist', self.died.call_args.args[0]
                )
                self.assertEqual(out, [], "a failed query printed a result")


class AnEmptyResultSaysSo(_Harness):
    def test_table_prints_zero_rows(self):
        for stdout in (b"", b"companyid,webid\n"):
            with self.subTest(stdout=stdout):
                code, _, out = self.query("postgresql", stdout=stdout)
                self.assertIsNone(code)
                self.assertEqual(out, ["(0 rows)"])

    def test_json_prints_an_empty_list(self):
        _, _, out = self.query("mysql", stdout=b"", fmt="json")
        self.assertEqual(json.loads(out[0]), [])

    def test_csv_keeps_the_header_psql_sends(self):
        _, _, out = self.query("postgresql", stdout=b"companyid,webid\n", fmt="csv")
        self.assertEqual("".join(out).strip(), "companyid,webid")

    def test_a_row_is_still_a_table(self):
        """A guard that fires on the happy path gets disabled within a week."""
        with patch("ldm_core.ui.UI.table") as table:
            code, _, out = self.query("postgresql", stdout=b"companyid\n10154\n")
        self.assertIsNone(code)
        table.assert_called_once_with([["10154"]], headers=["companyid"])
        self.assertNotIn("(0 rows)", out)


class ResetAdminStopsOnError(_Harness):
    def test_reset_admin_psql_stops_on_the_first_error(self):
        self.manager.detect_project_path = MagicMock(return_value=self.root)  # type: ignore[method-assign]
        self.manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={"db_type": "postgresql", "container_name": "proj"}
        )
        self.manager.run_command = MagicMock(return_value="lportal-db")  # type: ignore[method-assign]
        with (
            patch("subprocess.run") as run,
            patch("ldm_core.ui.UI.success"),
            patch("ldm_core.ui.UI.info"),
        ):
            self.manager.database.cmd_reset_admin("test")
        argv = run.call_args.args[0]
        self.assertIn("ON_ERROR_STOP=1", argv)


if __name__ == "__main__":
    unittest.main()
