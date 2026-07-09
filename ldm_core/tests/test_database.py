import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.database import DatabaseService


class MockManager(BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.args.command = "db"
        self.args.subcommand = "query"
        self.non_interactive = True
        self.dry_run = False
        self.verbose = False

        from typing import Any, cast

        self.manager = cast(Any, self)

        self.defaults = MagicMock()
        self.defaults.get.return_value = "isolated"
        self.database: Any = None


class TestDatabaseQuerySafety(unittest.TestCase):
    """Unit tests for SQL query safety validator (is_query_safe)."""

    def test_safe_queries(self):
        """Valid SELECT, WITH, EXPLAIN, SHOW queries must pass."""
        safe_cases = [
            "SELECT * FROM company;",
            "select companyid, webid from company where companyid = 10154;",
            "WITH roles AS (SELECT roleid FROM role_) SELECT * FROM roles;",
            "EXPLAIN SELECT * FROM User_;",
            "SHOW TABLES;",
            "  -- This is a comment\nSELECT * FROM role_;",
            "/* block comment */ SELECT name FROM role_ /* nested comment */;",
            "SELECT roleid FROM role_; SELECT userid FROM user_;",
        ]
        for query in safe_cases:
            is_safe, reason = DatabaseService.is_query_safe(query)
            self.assertTrue(is_safe, f"Expected safe: {query}. Reason: {reason}")

    def test_unsafe_dml_keyword_injection(self):
        """Queries containing forbidden mutating keywords must be rejected."""
        unsafe_cases = [
            "INSERT INTO role_ (name) VALUES ('Guest');",
            "UPDATE User_ SET emailAddress='hacker@liferay.com';",
            "DELETE FROM Group_ WHERE groupid = 20124;",
            "DROP TABLE company;",
            "TRUNCATE TABLE role_;",
            "ALTER TABLE User_ ADD COLUMN hacker VARCHAR(255);",
            "CREATE TABLE dummy (id INT);",
            "SELECT * FROM role_; DROP TABLE user_;",
            "SELECT * FROM role_ INTO TEMP dummy;",
            "EXEC my_procedure;",
            "EXECUTE my_procedure;",
        ]
        for query in unsafe_cases:
            is_safe, reason = DatabaseService.is_query_safe(query)
            self.assertFalse(is_safe, f"Expected unsafe: {query}")
            self.assertIn("Forbidden", reason or "")

    def test_invalid_start_prefix(self):
        """Queries starting with unsupported prefixes must be rejected."""
        invalid_cases = [
            "DESCRIBE User_;",
            "GRANT ALL PRIVILEGES ON lportal TO hacker;",
            "REVOKE SELECT ON User_ FROM Guest;",
        ]
        for query in invalid_cases:
            is_safe, reason = DatabaseService.is_query_safe(query)
            self.assertFalse(is_safe, f"Expected unsafe: {query}")
            self.assertIn(
                "must start with SELECT, WITH, EXPLAIN, or SHOW", reason or ""
            )

    def test_empty_query(self):
        """Empty queries must be rejected."""
        is_safe, reason = DatabaseService.is_query_safe("   \n  ")
        self.assertFalse(is_safe)
        self.assertIn("empty", reason or "")


class TestDatabaseQueryCommand(unittest.TestCase):
    """Integration-level unit tests for cmd_query."""

    def setUp(self):
        self.manager = MockManager()
        self.manager.database = DatabaseService(self.manager)

    @patch("ldm_core.ui.UI.die")
    @patch("ldm_core.ui.UI.warning")
    def test_cmd_query_unsupported_db(self, mock_warn, mock_die):
        """Query command must reject unsupported database types (e.g. hypersonic)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            self.manager.detect_project_path = MagicMock(return_value=project_path)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(return_value={"db_type": "hypersonic"})  # type: ignore[method-assign]

            self.manager.database.cmd_query(
                project_id="test", sql="SELECT * FROM company;"
            )
            mock_warn.assert_called_once()
            self.assertIn(
                "not supported for database type 'hypersonic'",
                mock_warn.call_args[0][0],
            )
            mock_die.assert_not_called()

    @patch("ldm_core.ui.UI.die")
    @patch("ldm_core.ui.UI.warning")
    def test_cmd_query_container_not_running(self, mock_warn, mock_die):
        """Query command must reject execution if database container is not running."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            self.manager.detect_project_path = MagicMock(return_value=project_path)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(return_value={"db_type": "postgresql"})  # type: ignore[method-assign]
            # Mock container not running (run_command returns empty string or false)
            self.manager.run_command = MagicMock(return_value="")  # type: ignore[method-assign]

            self.manager.database.cmd_query(
                project_id="test", sql="SELECT * FROM company;"
            )
            mock_warn.assert_called_once()
            self.assertIn("is not running", mock_warn.call_args[0][0])

    @patch("ldm_core.ui.UI.die")
    @patch("ldm_core.ui.UI.warning")
    @patch("subprocess.run")
    def test_cmd_query_non_interactive_no_opt_in(self, mock_run, mock_warn, mock_die):
        """Non-interactive query must fail if --allow-db-query / opt-in is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            self.manager.detect_project_path = MagicMock(return_value=project_path)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(  # type: ignore[method-assign]
                return_value={"db_type": "postgresql", "allow_db_query": "false"}
            )
            self.manager.run_command = MagicMock(return_value="container-id")  # type: ignore[method-assign]

            self.manager.database.cmd_query(
                project_id="test", sql="SELECT * FROM company;", allow_query=False
            )
            mock_die.assert_called_once()
            self.assertIn("requires explicit opt-in", mock_die.call_args[0][0])
            mock_run.assert_not_called()

    @patch("ldm_core.ui.UI.die")
    @patch("ldm_core.ui.UI.warning")
    @patch("subprocess.run")
    @patch("sys.stdout.write")
    def test_cmd_query_json_format(
        self, mock_stdout_write, mock_run, mock_warn, mock_die
    ):
        """Query execution with json format must parse and print valid JSON list of dicts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            self.manager.detect_project_path = MagicMock(return_value=project_path)  # type: ignore[method-assign]
            self.manager.read_meta = MagicMock(return_value={"db_type": "postgresql"})  # type: ignore[method-assign]
            self.manager.run_command = MagicMock(return_value="container-id")  # type: ignore[method-assign]

            # Mock subprocess run query output (CSV headers + row)
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = b"companyid,webid\n10154,liferay.com\n"
            mock_res.stderr = b""
            mock_run.return_value = mock_res

            with patch("builtins.print") as mock_print:
                self.manager.database.cmd_query(
                    project_id="test",
                    sql="SELECT companyid, webid FROM company;",
                    output_format="json",
                    allow_query=True,
                )
                mock_print.assert_called_once()
                printed_json = json.loads(mock_print.call_args[0][0])
                self.assertEqual(len(printed_json), 1)
                self.assertEqual(printed_json[0]["companyid"], "10154")
                self.assertEqual(printed_json[0]["webid"], "liferay.com")

            mock_die.assert_not_called()
            mock_warn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
