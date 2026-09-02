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
        self.target: str | None = None

        from typing import Any, cast

        self.manager = cast(Any, self)

        self.defaults = MagicMock()
        self.defaults.get.return_value = "isolated"
        self.database: Any = None

    def get_resource_path(self, name):
        return Path(f"/tmp/{name}")


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
        # Isolate from whatever persisted default target a *real* ~/.ldmrc
        # on the machine running the tests happens to have --
        # DockerService.get_docker_cmd_prefix() now always consults
        # get_active_target(), even with no explicit target_name, so an
        # unmocked call here would otherwise pick up e.g. a tester's own
        # persisted "aws-2" default instead of "local".
        from ldm_core.config import TargetNode

        self.target_patcher = patch(
            "ldm_core.docker_service.get_active_target",
            return_value=TargetNode(name="local", host="localhost", is_default=True),
        )
        self.target_patcher.start()
        self.addCleanup(self.target_patcher.stop)

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

    @patch("ldm_core.ui.UI.die")
    @patch("ldm_core.ui.UI.success")
    @patch("subprocess.run")
    def test_cmd_reset_admin_success(self, mock_run, mock_success, mock_die):
        """Test resetting the admin password builds the correct SQL and calls subprocess run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            # Create dummy docker-compose.yml so the existence check passes
            (project_path / "docker-compose.yml").touch()

            mock_meta = {
                "db_type": "postgresql",
                "database": "lportal",
                "database_user": "liferay",
                "container_name": "db-container-123",
            }

            # Mock the ps check so it looks like the DB is running
            def mock_run_command(cmd, env_vars=None, **kwargs):
                if "ps" in cmd:
                    return "lportal-db"
                return ""

            with (
                patch.object(
                    self.manager, "detect_project_path", return_value=project_path
                ),
                patch.object(self.manager, "read_meta", return_value=mock_meta),
                patch.object(self.manager, "run_command", side_effect=mock_run_command),
            ):
                # Mock successful subprocess execution for the SQL execution
                mock_res = MagicMock()
                mock_res.returncode = 0
                mock_run.return_value = mock_res

                self.manager.database.cmd_reset_admin("test")

            mock_die.assert_not_called()
            mock_success.assert_called_with(
                "Successfully reset 'test@liferay.com' and unlocked the account!"
            )

            # Assert subprocess.run was called with correct args
            mock_run.assert_called_once()
            called_args, called_kwargs = mock_run.call_args

            # Validate target exec command
            exec_cmd = called_args[0]
            self.assertEqual(
                exec_cmd[:4], ["docker", "exec", "-i", "db-container-123-db"]
            )
            self.assertIn("psql", exec_cmd)
            self.assertIn("lportal", exec_cmd)

            # Validate SQL input
            sql_input = called_kwargs["input"].decode("utf-8")
            self.assertIn("UPDATE User_", sql_input)
            self.assertIn("{PBKDF2WITHHMACSHA1}", sql_input)
            self.assertIn("test@liferay.com", sql_input)

    def test_cmd_query_remote_target(self) -> None:
        """Test cmd_query passes target context prefix to database exec command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            with (
                patch.object(
                    self.manager, "detect_project_path", return_value=project_path
                ),
                patch.object(
                    self.manager,
                    "read_meta",
                    return_value={"db_type": "postgresql", "target": "aws-1"},
                ),
                patch.object(
                    self.manager, "run_command", return_value="container-id"
                ) as mock_mgr_run,
                patch("subprocess.run") as mock_run,
                patch("ldm_core.docker_service.get_active_target") as mock_target,
            ):
                from ldm_core.config import TargetNode

                mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
                mock_res = MagicMock()
                mock_res.returncode = 0
                mock_res.stdout = b"companyid\n10154\n"
                mock_res.stderr = b""
                mock_run.return_value = mock_res

                self.manager.database.cmd_query(
                    project_id="test",
                    sql="SELECT companyid FROM company;",
                    output_format="table",
                    allow_query=True,
                )
                mock_mgr_run.assert_called()
                called_cmds = [
                    call[0][0]
                    for call in mock_mgr_run.call_args_list
                    if isinstance(call[0][0], list)
                ]
                has_context = any(
                    "--context" in cmd and "aws-1" in cmd
                    for cmd in called_cmds
                    if isinstance(cmd, list)
                )
                self.assertTrue(has_context)


# The two global database containers, spelled out rather than resolved through
# `shared_database_container`. LDM-#1547 is a bug about *which name* the command
# acts on, so a test that derived the name from the same helper the code uses
# would agree with the code no matter which container it drove.
PG_GLOBAL = "liferay-db-global"
MYSQL_GLOBAL = "liferay-db-mysql-global"


class FakeDocker:
    """A DockerService double whose container state actually changes.

    `DockerService.start` runs `run_command(..., check=False)` and returns None
    on failure -- so a MagicMock that records a call but reports nothing about
    the container cannot tell a start that worked from one that did not, which
    is exactly the blind spot LDM-#1547 shipped. Here `start` really flips a
    container to running, unless the test asks it to fail, and `is_running`
    answers from that state.
    """

    def __init__(self, containers=None, fails_to_start=(), fails_to_stop=()):
        # {container_name: is_running}. Absent means the container
        # does not exist at all.
        self.containers = dict(containers or {})
        self.fails_to_start = set(fails_to_start)
        self.fails_to_stop = set(fails_to_stop)
        self.started: list[tuple[str, str | None]] = []
        self.stopped: list[tuple[str, str | None]] = []
        self.existence_checks: list[tuple[str, str | None]] = []

    def exists(self, container_name, target_name=None):
        self.existence_checks.append((container_name, target_name))
        return container_name in self.containers

    def is_running(self, container_name, target_name=None):
        return bool(self.containers.get(container_name))

    def start(self, container_name, target_name=None):
        self.started.append((container_name, target_name))
        if container_name not in self.fails_to_start:
            self.containers[container_name] = True

    def stop(self, container_name, target_name=None):
        self.stopped.append((container_name, target_name))
        if container_name not in self.fails_to_stop:
            self.containers[container_name] = False


class SharedDatabaseCommandTestCase(unittest.TestCase):
    """Shared setup for the `ldm db start` / `ldm db stop` tests."""

    def setUp(self):
        self.manager = MockManager()
        self.manager.database = DatabaseService(self.manager)
        self.mock_run = MagicMock(return_value="")
        self.manager.run_command = self.mock_run  # type: ignore[method-assign]
        self.infra = MagicMock()
        self.manager.infra = self.infra  # type: ignore[attr-defined]
        # DefaultsManager.get(key, fallback) semantics: nothing configured, so
        # every lookup falls through to the caller's fallback.
        self.manager.defaults.get.return_value = None
        self.manager.defaults.get.side_effect = lambda _key, fallback=None: fallback
        self.exists_patcher = patch.object(Path, "exists", return_value=True)
        self.exists_patcher.start()
        self.addCleanup(self.exists_patcher.stop)

    def install_docker(self, **kwargs):
        """Swaps DockerService's container primitives for a stateful fake."""
        fake = FakeDocker(**kwargs)
        for name in ("exists", "is_running", "start", "stop"):
            patcher = patch(
                f"ldm_core.docker_service.DockerService.{name}", getattr(fake, name)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        return fake

    def started_names(self, fake):
        return [name for name, _target in fake.started]

    def stopped_names(self, fake):
        return [name for name, _target in fake.stopped]


class TestSharedDatabaseCommandsResolvePerTarget(SharedDatabaseCommandTestCase):
    """Shared infra (cmd_start/cmd_stop) resolves --node/--target exactly
    like any other command -- "shared" means shared among projects on the
    *same* target, not a single global instance for every target. See
    docs/explanation/remote-node-architecture.md §5."""

    # LDM-#1400 changed the mechanism, not the intent. These used to assert on
    # a `docker compose -f infra-compose.yml start db` command line, but that
    # compose file defines only `traefik` -- there is no `db` service, so the
    # command could never succeed. The commands now drive the container that
    # `setup_global_database` actually creates, so the target is asserted where
    # it is now passed: into DockerService.
    def test_cmd_start_passes_the_explicit_target_through(self):
        fake = self.install_docker(containers={PG_GLOBAL: False})
        self.manager.target = "aws-1"

        self.manager.database.cmd_start()

        self.assertIn((PG_GLOBAL, "aws-1"), fake.existence_checks)
        self.assertEqual([(PG_GLOBAL, "aws-1")], fake.started)

    def test_cmd_start_stays_local_without_explicit_target(self):
        fake = self.install_docker(containers={PG_GLOBAL: False})
        self.manager.target = None

        self.manager.database.cmd_start()

        self.assertIn((PG_GLOBAL, None), fake.existence_checks)
        self.assertEqual([(PG_GLOBAL, None)], fake.started)

    def test_cmd_stop_passes_the_explicit_target_through(self):
        fake = self.install_docker(containers={PG_GLOBAL: True})
        self.manager.target = "aws-2"

        self.manager.database.cmd_stop()

        self.assertEqual([(PG_GLOBAL, "aws-2")], fake.stopped)

    def test_cmd_start_provisions_when_the_container_does_not_exist(self):
        """Previously the user was told to run a command that could not create
        it. Now the missing case provisions through the same path `ldm run`
        uses."""
        self.install_docker(containers={})

        self.manager.database.cmd_start()

        self.infra.setup_global_database.assert_called_once()

    def test_cmd_stop_does_nothing_when_there_is_no_container(self):
        fake = self.install_docker(containers={})

        self.manager.database.cmd_stop()

        self.assertEqual([], fake.stopped)


class TestSharedDatabaseCommandsResolveTheEngine(SharedDatabaseCommandTestCase):
    """LDM-#1547: `ldm db start` hardcoded `liferay-db-global`, so on a MySQL
    fleet it inspected and provisioned the PostgreSQL global while the MySQL
    one the projects actually use stayed down. LDM-#1361 gave each engine its
    own container; these two call sites were missed."""

    def test_cmd_start_starts_the_mysql_global_on_a_mysql_fleet(self):
        fake = self.install_docker(containers={MYSQL_GLOBAL: False})

        self.manager.database.cmd_start()

        self.assertEqual([(MYSQL_GLOBAL, None)], fake.started)
        self.assertTrue(fake.containers[MYSQL_GLOBAL])

    def test_cmd_start_does_not_provision_postgresql_on_a_mysql_fleet(self):
        """The damaging half of the bug: an unwanted PostgreSQL container gets
        created on a fleet that has no PostgreSQL project."""
        fake = self.install_docker(containers={MYSQL_GLOBAL: False})

        self.manager.database.cmd_start()

        self.infra.setup_global_database.assert_not_called()
        self.assertNotIn(PG_GLOBAL, self.started_names(fake))
        self.assertNotIn(PG_GLOBAL, fake.containers)

    def test_cmd_start_starts_both_globals_on_a_mixed_fleet(self):
        fake = self.install_docker(
            containers={PG_GLOBAL: False, MYSQL_GLOBAL: False},
        )

        self.manager.database.cmd_start()

        self.assertEqual(
            {PG_GLOBAL, MYSQL_GLOBAL},
            set(self.started_names(fake)),
        )

    def test_cmd_start_reports_the_mysql_global_once(self):
        """`mysql` and `mariadb` share one container by design. Iterating the
        engine map by key rather than by container visits it twice, and the
        second visit reports the container it just started as "already
        running" -- one start, two contradictory-looking success lines."""
        self.install_docker(containers={MYSQL_GLOBAL: False})

        with patch("ldm_core.ui.UI.success") as mock_success:
            self.manager.database.cmd_start()

        mentions = [
            call.args[0]
            for call in mock_success.call_args_list
            if MYSQL_GLOBAL in call.args[0]
        ]
        self.assertEqual(1, len(mentions), mentions)

    def test_cmd_start_provisions_the_configured_engine_when_nothing_exists(self):
        """Nothing exists, so there is nothing to observe -- the engine comes
        from the configured `db_type` default rather than a guess."""
        self.install_docker(containers={})
        self.manager.defaults.get.side_effect = lambda key, fallback=None: (
            "mysql" if key == "db_type" else fallback
        )

        self.manager.database.cmd_start()

        self.assertEqual(
            "mysql",
            self.infra.setup_global_database.call_args.kwargs["db_type"],
        )

    def test_cmd_start_provisions_postgresql_when_no_default_is_configured(self):
        """PostgreSQL is LDM's convention default, and what every pre-#1361
        caller got."""
        self.install_docker(containers={})

        self.manager.database.cmd_start()

        self.assertEqual(
            "postgresql",
            self.infra.setup_global_database.call_args.kwargs["db_type"],
        )

    def test_cmd_stop_stops_the_mysql_global_on_a_mysql_fleet(self):
        """`stop` has to resolve the engine the same way `start` does, or the
        pair is incoherent."""
        fake = self.install_docker(containers={MYSQL_GLOBAL: True})

        self.manager.database.cmd_stop()

        self.assertEqual([(MYSQL_GLOBAL, None)], fake.stopped)
        self.assertFalse(fake.containers[MYSQL_GLOBAL])


class TestSharedDatabaseStartVerifiesTheOutcome(SharedDatabaseCommandTestCase):
    """LDM-#1547: `DockerService.start` returns None on failure and the result
    was discarded, so a container that never came up printed a green success
    line and exited 0. Exit code 3 is Infrastructure/Data Error."""

    def test_cmd_start_exits_3_when_the_container_does_not_come_up(self):
        self.install_docker(containers={PG_GLOBAL: False}, fails_to_start=[PG_GLOBAL])

        with patch("ldm_core.ui.UI.error"), self.assertRaises(SystemExit) as caught:
            self.manager.database.cmd_start()

        self.assertEqual(3, caught.exception.code)

    def test_cmd_start_does_not_claim_success_when_the_container_fails(self):
        self.install_docker(containers={PG_GLOBAL: False}, fails_to_start=[PG_GLOBAL])

        with (
            patch("ldm_core.ui.UI.error"),
            patch("ldm_core.ui.UI.success") as mock_success,
            self.assertRaises(SystemExit),
        ):
            self.manager.database.cmd_start()

        self.assertEqual([], mock_success.call_args_list)

    def test_cmd_start_names_the_container_that_failed(self):
        """A mixed fleet has two globals; the message has to say which one."""
        self.install_docker(
            containers={PG_GLOBAL: True, MYSQL_GLOBAL: False},
            fails_to_start=[MYSQL_GLOBAL],
        )

        with patch("ldm_core.ui.UI.error") as mock_error, self.assertRaises(SystemExit):
            self.manager.database.cmd_start()

        self.assertIn(MYSQL_GLOBAL, mock_error.call_args[0][0])

    def test_cmd_start_reports_success_when_the_container_comes_up(self):
        self.install_docker(containers={PG_GLOBAL: False})

        with patch("ldm_core.ui.UI.success") as mock_success:
            self.manager.database.cmd_start()

        self.assertIn(
            f"Global shared database '{PG_GLOBAL}' started.",
            [call.args[0] for call in mock_success.call_args_list],
        )

    def test_cmd_stop_exits_3_when_the_container_is_still_running(self):
        self.install_docker(containers={PG_GLOBAL: True}, fails_to_stop=[PG_GLOBAL])

        with patch("ldm_core.ui.UI.error"), self.assertRaises(SystemExit) as caught:
            self.manager.database.cmd_stop()

        self.assertEqual(3, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
