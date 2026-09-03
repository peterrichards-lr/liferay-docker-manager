import csv
import datetime
import io
import json
import re
import subprocess
import sys

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import (
    SHARED_DB_CONTAINERS,
    shared_database_container,
    shared_database_name,
)


def _shared_db_engines():
    """The engines whose global containers `ldm db start`/`stop` act on.

    LDM-#1400 introduced the global database container, created by
    `InfraService.setup_global_database` with a bare `docker run`; LDM-#1361
    then gave each *engine* its own -- `liferay-db-global` for PostgreSQL,
    `liferay-db-mysql-global` for MySQL, with `mariadb` deliberately sharing
    the MySQL one.

    Iterating `SHARED_DB_CONTAINERS` by engine would therefore act on
    `liferay-db-mysql-global` twice, so this keeps one representative engine
    per *distinct container*. Deriving it from that map rather than restating
    the engine list here means a fourth engine added there is picked up
    without a second edit -- the kind of drift LDM-#1361 existed to end.
    """
    representatives: dict[str, str] = {}
    for engine in SHARED_DB_CONTAINERS:
        representatives.setdefault(shared_database_container(engine), engine)
    return list(representatives.values())


class DatabaseService(BaseHandler):
    """Orchestration handler for project database querying operations."""

    def __init__(self, manager=None):
        super().__init__(manager.args if manager else None)
        self.manager = manager

    @staticmethod
    def is_query_safe(sql: str) -> tuple[bool, str | None]:
        """Statically validates a SQL query string to ensure it is SELECT-only.

        Comments (both single-line and block comments) are stripped.
        Every separate statement (demarcated by semicolons) must start with
        SELECT, WITH, EXPLAIN, or SHOW. No statement is allowed to contain
        forbidden mutating keywords.

        Returns:
            tuple[bool, str | None]: (True, None) if safe, (False, error_reason) if unsafe.
        """
        # 1. Strip comments
        # Strip single line comments
        clean_sql = re.sub(r"--.*?\n", "\n", sql)
        # Strip block comments
        clean_sql = re.sub(r"/\*.*?\*/", "", clean_sql, flags=re.DOTALL)

        clean_sql = clean_sql.strip()
        if not clean_sql:
            return False, "Query is empty."

        # 2. Split statements by semicolon and check each
        statements = [s.strip() for s in clean_sql.split(";") if s.strip()]
        if not statements:
            return False, "No SQL statements found."

        forbidden_keywords = {
            "INSERT",
            "UPDATE",
            "DELETE",
            "DROP",
            "TRUNCATE",
            "ALTER",
            "CREATE",
            "REPLACE",
            "GRANT",
            "REVOKE",
            "COPY",
            "MERGE",
            "EXEC",
            "EXECUTE",
            "INTO",
        }

        for stmt in statements:
            # Tokenize statement into alpha-numeric words
            tokens = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", stmt.upper())
            if not tokens:
                return False, "Statement is empty or malformed."

            # Enforce that it starts with an allowed query prefix
            first_token = tokens[0]
            allowed_starts = {"SELECT", "WITH", "EXPLAIN", "SHOW"}
            if first_token not in allowed_starts:
                return False, (
                    f"Forbidden or unsupported SQL statement: must start with "
                    f"SELECT, WITH, EXPLAIN, or SHOW. Got: {first_token}"
                )

            # Check for forbidden keywords anywhere in the token stream
            found_forbidden = forbidden_keywords.intersection(tokens)
            if found_forbidden:
                return (
                    False,
                    f"Forbidden SQL keyword(s) detected: {', '.join(found_forbidden)}",
                )

        return True, None

    def cmd_start(self):
        """Starts the shared global databases.

        "Shared" means shared among projects resolving to the *same*
        target -- not a single global instance for every target. A
        project running on aws-1 sharing a DB with other aws-1 projects
        makes sense (same host, no cross-network DB latency, and the
        orchestrating laptop that isn't even network-reachable from a
        remote VPC never needs to be in the loop); a single shared
        instance pinned to one node that every other node's projects
        reach across the network would not. So this resolves --node/
        --target exactly like any other command -- there's nothing
        special about shared infra here, it's "which target is active for
        this invocation," same as everything else. See
        docs/explanation/remote-node-architecture.md §5.

        LDM-#1547: *which engine* is a separate question from which target,
        and this command used to answer it with a hardcoded
        `liferay-db-global`. On a MySQL fleet that inspected -- and
        provisioned -- a PostgreSQL container nobody uses, while the MySQL
        global the projects actually run against stayed down.

        There is no project here to read `db_type` from, and the `db start`
        subparser takes no `--db`, so the engine is not guessed: the command
        acts on whichever globals *exist* on the target. That is an observed
        fact rather than an assumption, and it is what the command has always
        advertised -- "the shared global databases (e.g. Postgres, MySQL)",
        plural, in its help text, `docs/reference/advanced_cli.md` and the man
        page. Only when none exists is there nothing to observe, and
        provisioning falls back to the configured `db_type` default
        (`~/.ldmrc`, PostgreSQL by convention) -- the same default `ldm run`
        gives a project that names no engine.
        """
        # LDM-#1400: this used to run
        # `docker compose -f infra-compose.yml start db`, but that file defines
        # only `traefik` -- there is no `db` service, and the global database is
        # not a compose service at all. `setup_global_database` creates it with a
        # bare `docker run`. The command could therefore never succeed, and
        # `cmd_reset_admin` points users straight at it.
        from ldm_core.docker_service import DockerService

        target_name = getattr(self.manager, "target", None)
        engines = [
            engine
            for engine in _shared_db_engines()
            if DockerService.exists(shared_database_container(engine), target_name)
        ]

        if not engines:
            # Not created yet -- provision it through the same path `ldm run`
            # uses, rather than reporting a missing container the user cannot
            # create. `setup_global_database` owns the post-provision health
            # check (LDM-#1545), so repeating it here would be unreachable.
            db_type = self.manager.defaults.get("db_type", "postgresql")
            UI.detail("Global shared database not found; provisioning it...")
            self.manager.infra.setup_global_database(db_type=db_type)
            return

        for engine in engines:
            db_name = shared_database_container(engine)

            if DockerService.is_running(db_name, target_name):
                # UI.success, not UI.detail: the latter is gated behind
                # --info/--verbose (LDM-#1036), so the user would see nothing at
                # all and could not tell success from a no-op.
                UI.success(f"Global shared database '{db_name}' is already running.")
                continue

            UI.detail(f"Starting global shared database '{db_name}'...")
            DockerService.start(db_name, target_name)

            # LDM-#1547: `DockerService.start` runs `run_command(..., check=False)`
            # and returns None on failure, and that result was discarded -- so a
            # container that never came up (port clash, corrupt volume, missing
            # image) printed a green success line and exited 0. `UI.error` only
            # prints; only `UI.die` exits. Exit code 3 is Infrastructure/Data
            # Error per .agents/skills/ldm-architecture/SKILL.md.
            if not DockerService.is_running(db_name, target_name):
                UI.die(
                    f"Global shared database '{db_name}' did not come up.",
                    tip=f"Inspect it with `docker logs {db_name}`.",
                    exit_code=3,
                )

            UI.success(f"Global shared database '{db_name}' started.")

    def cmd_stop(self):
        """Stops the shared global databases. See cmd_start's docstring for
        why this resolves --node/--target like any other command, and why the
        engine is read off the containers that exist rather than guessed.

        The engine resolution has to match cmd_start's or the pair is
        incoherent: a fleet whose `ldm db start` brings up
        `liferay-db-mysql-global` cannot have an `ldm db stop` that reports
        `liferay-db-global` missing (LDM-#1547).
        """
        # LDM-#1400: see cmd_start -- there is no `db` compose service to stop.
        from ldm_core.docker_service import DockerService

        target_name = getattr(self.manager, "target", None)
        engines = [
            engine
            for engine in _shared_db_engines()
            if DockerService.exists(shared_database_container(engine), target_name)
        ]

        if not engines:
            UI.warning("No global shared database exists.")
            return

        for engine in engines:
            db_name = shared_database_container(engine)

            if not DockerService.is_running(db_name, target_name):
                UI.success(f"Global shared database '{db_name}' is already stopped.")
                continue

            UI.detail(f"Stopping global shared database '{db_name}'...")
            DockerService.stop(db_name, target_name)

            # LDM-#1547: `DockerService.stop` discards failure exactly as
            # `start` did. Reporting a stop that did not happen is the same
            # defect on the same pair of commands, so it gets the same guard.
            if DockerService.is_running(db_name, target_name):
                UI.die(
                    f"Global shared database '{db_name}' is still running.",
                    tip=f"Inspect it with `docker inspect {db_name}`.",
                    exit_code=3,
                )

            UI.success(f"Global shared database '{db_name}' stopped.")

    def cmd_query(  # noqa: C901, PLR0911, PLR0912, PLR0915
        self, project_id=None, sql=None, output_format="table", allow_query=False
    ):
        """Execute a safe SELECT SQL query against the project database."""
        # 1. Resolve project path and read metadata
        project_path = self.manager.detect_project_path(project_id)
        if not project_path:
            UI.die("Project path not resolved.")
            return

        project_meta = self.manager.read_meta(project_path)
        from ldm_core.utils import (
            ldm_manages_database_container,
            resolve_database_config,
        )

        db_type, db_mode = resolve_database_config(
            project_meta or {}, self.manager.defaults
        )

        if db_type not in ["postgresql", "mysql", "mariadb"]:
            UI.warning(
                f"Query execution is not supported for database type '{db_type}'. "
                "Only PostgreSQL and MySQL/MariaDB variants are supported."
            )
            return

        # LDM-#1511: the engine may now be known for a database LDM does not
        # run. `ldm db query` execs into a container, so it needs a container
        # LDM owns -- which is a question about the MODE. Before #1511 the
        # engine check above happened to cover it, because choosing "external"
        # discarded the engine.
        if not ldm_manages_database_container(db_mode):
            UI.warning(
                f"Query execution is not supported in '{db_mode}' database mode: "
                "LDM does not run this database and has no container to execute in."
            )
            return

        # 2. Resolve database container and name
        container_name = project_meta.get("liferay_container_name") or project_meta.get(
            "container_name"
        )
        if not container_name:
            container_name = project_path.name

        db_name = "lportal"
        if db_mode == "shared":
            from ldm_core.utils import shared_database_container

            db_container = shared_database_container(db_type)
            db_name = shared_database_name(project_path.name)
        else:
            db_container = project_meta.get("db_container_name")
            if not db_container:
                for suffix in ["-db", "-db-1"]:
                    candidate = f"{container_name}{suffix}"
                    # check container exist
                    if self.manager.run_command(
                        ["docker", "ps", "-q", "-f", f"name=^{candidate}$"]
                    ):
                        db_container = candidate
                        break
            if not db_container:
                db_container = f"{container_name}-db"

        # Verify DB container is running
        target_name = getattr(self.manager, "target", None) or project_meta.get(
            "target"
        )
        from ldm_core.docker_service import DockerService

        docker_prefix = DockerService.get_docker_cmd_prefix(target_name)
        is_running = self.manager.run_command(
            [*docker_prefix, "ps", "-q", "-f", f"name=^{db_container}$"]
        )
        if not is_running:
            UI.warning(f"Database container '{db_container}' is not running.")
            return

        # 3. Read SQL query from stdin if not provided as argument
        if not sql:
            if sys.stdin.isatty():
                # Interactive stdin prompt
                UI.detail("Enter SQL query (SELECT-only, end with semicolon ';'):")
                sql = sys.stdin.read()
            else:
                # Piped stdin
                sql = sys.stdin.read()

        sql = sql.strip()
        if not sql:
            UI.warning("No SQL query provided.")
            return

        # 4. Enforce query safety rules
        is_safe, error_reason = self.is_query_safe(sql)
        if not is_safe:
            UI.warning(f"SQL Safety Violation: {error_reason}")
            return

        # 5. Security confirmation prompt (if not pre-approved)
        if not allow_query:
            # Check if allowed in project meta
            allow_query_meta = (
                str(project_meta.get("allow_db_query", "false")).lower() == "true"
            )
            if not allow_query_meta:
                if self.non_interactive:
                    UI.die(
                        "Database query execution requires explicit opt-in. "
                        "Run with --allow-db-query or enable 'allow_db_query' in project meta."
                    )
                    return
                if not UI.confirm(
                    f"Are you sure you want to run query against database '{db_name}'?",
                    "N",
                ):
                    return

        # 6. Audit logging
        log_dir = project_path / ".liferay-docker"
        try:
            log_dir.mkdir(exist_ok=True)
            log_file = log_dir / "query.log"
            timestamp = datetime.datetime.now().isoformat()
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] SQL: {sql.replace(chr(10), ' ')}\n")
        except Exception:
            pass

        # 7. Construct docker exec CLI args
        if db_type == "postgresql":
            cmd_args = [
                *docker_prefix,
                "exec",
                "-i",
                db_container,
                "psql",
                "-U",
                "lportal",
                "-d",
                db_name,
                "--csv",
                "--pset=footer=off",
            ]
            delimiter = ","
        else:  # mysql / mariadb
            cmd_args = [
                *docker_prefix,
                "exec",
                "-i",
                db_container,
                "mysql",
                "-u",
                "lportal",
                "-ptest",
                "-D",
                db_name,
                "--batch",
                "--html=false",
                "--xml=false",
            ]
            delimiter = "\t"

        # 8. Execute query in container
        res = subprocess.run(
            cmd_args,
            input=sql.encode("utf-8"),
            capture_output=True,
            check=False,
        )

        if res.returncode != 0:
            err_msg = (res.stderr or b"").decode(errors="ignore").strip()
            UI.warning(f"Database query failed: {err_msg}")
            return

        raw_out = (res.stdout or b"").decode(errors="ignore").strip()
        if not raw_out:
            UI.detail("No rows returned.")
            return

        # 9. Format outputs
        f_io = io.StringIO(raw_out)
        reader = csv.reader(f_io, delimiter=delimiter)
        try:
            headers = next(reader)
        except StopIteration:
            UI.detail("No rows returned.")
            return

        rows = list(reader)

        if output_format == "csv":
            writer = csv.writer(sys.stdout)
            writer.writerow(headers)
            writer.writerows(rows)
        elif output_format == "json":
            results = [dict(zip(headers, row, strict=False)) for row in rows]
            print(json.dumps(results, indent=2))
        else:
            UI.table(rows, headers=headers)

    def cmd_reset_admin(self, project_id=None):
        """Force reset the test@liferay.com admin password to 'test' and unlock the account."""
        project_path = self.manager.detect_project_path(project_id)
        if not project_path:
            UI.die("Project path not resolved.")
            return

        project_meta = self.manager.read_meta(project_path)
        from ldm_core.utils import (
            ldm_manages_database_container,
            resolve_database_config,
        )

        db_type, db_mode = resolve_database_config(
            project_meta or {}, self.manager.defaults
        )

        if db_type not in ["postgresql", "mysql", "mariadb"]:
            UI.die(
                f"Password reset is not supported for database type '{db_type}'. "
                "Only PostgreSQL and MySQL/MariaDB variants are supported."
            )
            return

        # LDM-#1511: see cmd_query -- an engine LDM knows is not the same as a
        # container LDM runs.
        if not ldm_manages_database_container(db_mode):
            UI.die(
                f"Password reset is not supported in '{db_mode}' database mode: "
                "LDM does not run this database and has no container to execute in."
            )
            return

        # PBKDF2 hash for the word 'test'
        test_hash = "{PBKDF2WITHHMACSHA1}AAAAoAAT1iBt8tGXvh0pOQAAAAAAAAAAp2Q/Gh3D7VjWFUgM+4aG54uaQjw="
        target_email = "test@liferay.com"

        update_sql = f"UPDATE User_ SET password_ = '{test_hash}', passwordEncrypted = 1, passwordReset = 0, status = 0, lockDate = NULL, failedLoginAttempts = 0 WHERE emailAddress = '{target_email}';"  # nosec B608

        container_name = project_meta.get("liferay_container_name") or project_meta.get(
            "container_name"
        )
        if not container_name:
            container_name = project_path.name

        db_name = "lportal"
        if db_mode == "shared":
            from ldm_core.utils import shared_database_container

            db_container = shared_database_container(db_type)
            db_name = shared_database_name(project_path.name)
        else:
            db_container = project_meta.get("db_container_name", f"{container_name}-db")

        # Verify DB container is running
        target_name = getattr(self.manager, "target", None) or project_meta.get(
            "target"
        )
        from ldm_core.docker_service import DockerService

        docker_prefix = DockerService.get_docker_cmd_prefix(target_name)
        is_running = self.manager.run_command(
            [*docker_prefix, "ps", "-q", "-f", f"name=^{db_container}$"]
        )
        if not is_running:
            UI.die(
                f"The database container '{db_container}' is not running. "
                "Please run `ldm start` (or `ldm db start` for shared DBs) first."
            )
            return

        UI.info(f"Resetting {target_email} password to 'test'...")

        # We execute the query directly by piggybacking on cmd_query's container execution
        # But cmd_query enforces SELECT only. We must run our own exec.

        # MySQL/MariaDB credentials
        if db_type in ["mysql", "mariadb"]:
            db_user = "root"
            db_pass = project_meta.get("database_root_password", "my-secret-pw")
            exec_cmd = [
                *docker_prefix,
                "exec",
                "-i",
                db_container,
                "mysql",
                f"--user={db_user}",
                f"--password={db_pass}",
                db_name,
            ]
        elif db_type == "postgresql":
            db_user = project_meta.get("database_user", "lportal")
            exec_cmd = [
                *docker_prefix,
                "exec",
                "-i",
                db_container,
                "psql",
                "-U",
                db_user,
                "-d",
                db_name,
            ]

        # Use subprocess to pass the SQL string via stdin
        import subprocess

        try:
            subprocess.run(
                exec_cmd,
                input=update_sql.encode("utf-8"),
                capture_output=True,
                check=True,
            )
            UI.success(f"Successfully reset '{target_email}' and unlocked the account!")
            UI.info("You can now log in using:")
            UI.info(f"  Email:    {target_email}")
            UI.info("  Password: test")
        except subprocess.CalledProcessError as e:
            UI.die(
                f"Failed to reset password:\n{e.stderr.decode('utf-8', errors='ignore')}"
            )
