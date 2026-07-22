import csv
import datetime
import io
import json
import re
import subprocess
import sys

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import get_compose_cmd, resolve_infrastructure_mode, sanitize_id


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
        """Starts the shared global database."""
        infra_compose = self.manager.get_resource_path("infra-compose.yml")
        if not infra_compose or not infra_compose.exists():
            UI.die(
                "Infrastructure compose file 'infra-compose.yml' not found in resources."
            )
            return

        cmd = [*get_compose_cmd(), "-f", str(infra_compose)]
        cmd.extend(["start", "db"])

        UI.detail("Starting global shared database (db)...")
        self.manager.run_command(cmd, capture_output=False)

    def cmd_stop(self):
        """Stops the shared global database."""
        infra_compose = self.manager.get_resource_path("infra-compose.yml")
        if not infra_compose or not infra_compose.exists():
            UI.die(
                "Infrastructure compose file 'infra-compose.yml' not found in resources."
            )
            return

        cmd = [*get_compose_cmd(), "-f", str(infra_compose)]
        cmd.extend(["stop", "db"])

        UI.detail("Stopping global shared database (db)...")
        self.manager.run_command(cmd, capture_output=False)

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
        db_type = project_meta.get("db_type", "postgresql")

        if db_type not in ["postgresql", "mysql", "mariadb"]:
            UI.warning(
                f"Query execution is not supported for database type '{db_type}'. "
                "Only PostgreSQL and MySQL/MariaDB variants are supported."
            )
            return

        # 2. Resolve database container and name
        container_name = project_meta.get("liferay_container_name") or project_meta.get(
            "container_name"
        )
        if not container_name:
            container_name = project_path.name

        db_mode = resolve_infrastructure_mode(
            "database_mode", project_meta or {}, self.manager.defaults
        )
        db_name = "lportal"
        if db_mode == "shared":
            db_container = "liferay-db-global"
            db_name = f"lportal_{sanitize_id(project_path.name).replace('-', '_')}"
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
        is_running = self.manager.run_command(
            ["docker", "ps", "-q", "-f", f"name=^{db_container}$"]
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
                "docker",
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
                "docker",
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
