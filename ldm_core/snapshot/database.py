import time
from pathlib import Path

from ldm_core.ui import UI


class DatabaseSnapshotService:
    def __init__(self, facade):
        self.facade = facade
        self.manager = facade.manager
        self.args = facade.manager.args

    def _snapshot_database(self, project_meta, container_name, snap_dir, paths):  # noqa: C901, PLR0912
        db_type = project_meta.get("db_type", "hypersonic")
        db_snapshot_file = None
        if db_type in ["mysql", "postgresql", "mariadb"]:
            db_container = project_meta.get("db_container_name")
            from ldm_core.utils import resolve_infrastructure_mode

            db_mode = resolve_infrastructure_mode(
                "database_mode", project_meta or {}, self.manager.defaults
            )
            if db_mode == "shared":
                db_container = "liferay-db-global"

            if not db_container:
                for suffix in ["-db", "-db-1"]:
                    candidate = f"{container_name}{suffix}"
                    if self.manager.run_command(
                        ["docker", "ps", "-q", "-f", f"name=^{candidate}$"]
                    ):
                        db_container = candidate
                        break

            if db_container and self.manager.run_command(
                ["docker", "ps", "-q", "-f", f"name=^{db_container}$"]
            ):
                db_snapshot_file = snap_dir / "database.sql"
                UI.detail(f"Triggering orchestrated database snapshot ({db_type})...")

                db_name = "lportal"
                if db_mode == "shared":
                    from ldm_core.utils import sanitize_id

                    db_name = (
                        f"lportal_{sanitize_id(paths['root'].name).replace('-', '_')}"
                    )

                if db_type in ["mysql", "mariadb"]:
                    dump_cmd = [
                        "docker",
                        "exec",
                        db_container,
                        "mysqldump",
                        "-u",
                        "lportal",
                        "-ptest",
                        "--opt",
                        "--add-drop-table",
                        db_name,
                    ]
                else:
                    dump_cmd = [
                        "docker",
                        "exec",
                        db_container,
                        "pg_dump",
                        "-U",
                        "lportal",
                        "--clean",
                        "--if-exists",
                        db_name,
                    ]

                try:
                    with open(db_snapshot_file, "wb") as db_f:
                        self.manager.run_command(dump_cmd, stdout_file=db_f)
                    if db_snapshot_file.stat().st_size > 0:
                        UI.success("Database dump completed.")
                    else:
                        if db_snapshot_file and db_snapshot_file.exists():
                            import time

                            for _ in range(5):
                                try:
                                    db_snapshot_file.unlink()
                                    break
                                except OSError:
                                    time.sleep(0.2)
                        UI.die("Database dump returned no content.", exit_code=3)
                except Exception as e:
                    if db_snapshot_file and db_snapshot_file.exists():
                        import time

                        for _ in range(5):
                            try:
                                db_snapshot_file.unlink()
                                break
                            except OSError:
                                time.sleep(0.2)
                    UI.die(f"Database dump failed: {e}", exit_code=3)
        return db_snapshot_file

    def _restore_database(self, paths, choice_path, project_meta, container_name):  # noqa: C901, PLR0912, PLR0915
        sql_file = choice_path / "database.sql"
        db_gz = choice_path / "database.gz"

        if db_gz.exists() and not sql_file.exists():
            UI.detail("  + Decompressing cloud database dump...")
            import gzip
            import shutil

            try:
                with gzip.open(str(db_gz), "rb") as f_in:
                    with open(str(sql_file), "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                UI.success("Cloud database dump decompressed.")
            except Exception as e:
                UI.warning(f"Failed to decompress {db_gz.name}: {e}")

        db_type = project_meta.get("db_type", "hypersonic")
        if db_type == "hypersonic":
            UI.success("  + Hypersonic database restored successfully (file-based).")
        elif sql_file.exists():
            try:
                import tempfile

                scrubbed = False
                with tempfile.NamedTemporaryFile(
                    dir=str(sql_file.parent), delete=False, mode="w", encoding="utf-8"
                ) as temp_file:
                    temp_sql = Path(temp_file.name)

                    with open(str(sql_file), encoding="utf-8", errors="ignore") as f_in:
                        first_chunk = f_in.read(4096)
                        if "\\restrict" in first_chunk:
                            UI.detail(
                                "  + Scrubbing Cloud-specific meta-commands from SQL dump..."
                            )
                            f_in.seek(0)
                            for line in f_in:
                                if line.startswith("\\restrict") or line.startswith(
                                    "\\unrestrict"
                                ):
                                    continue
                                temp_file.write(line)
                            scrubbed = True

                if scrubbed:
                    from ldm_core.utils import safe_move

                    safe_move(str(temp_sql), str(sql_file))
                elif temp_sql.exists():
                    temp_sql.unlink(missing_ok=True)
            except Exception as e:
                UI.debug(f"SQL scrub failed: {e}")
                if "temp_sql" in locals() and temp_sql.exists():
                    temp_sql.unlink(missing_ok=True)

            UI.detail(f"Triggering orchestrated database restore ({db_type})...")

            self.manager.runtime.cmd_stop(paths["root"].name, service="liferay")

            from ldm_core.utils import resolve_infrastructure_mode

            db_mode = resolve_infrastructure_mode(
                "database_mode", project_meta, self.manager.defaults
            )

            db_container = project_meta.get("db_container_name")
            if db_mode == "shared":
                db_container = (
                    "liferay-db-mysql-global"
                    if db_type in ["mysql", "mariadb"]
                    else "liferay-db-global"
                )

            if not db_container:
                for suffix in ["-db", "-db-1"]:
                    candidate = f"{container_name}{suffix}"
                    if self.manager.run_command(
                        ["docker", "ps", "-q", "-f", f"name=^{candidate}$"]
                    ):
                        db_container = candidate
                        break

            if not db_container or not self.manager.run_command(
                ["docker", "ps", "-q", "-f", f"name=^{db_container}$"]
            ):
                UI.detail("  + Starting database container for restore...")
                if db_mode == "shared":
                    self.manager.infra.setup_global_database()
                else:
                    from ldm_core.utils import get_compose_cmd

                    compose_base = get_compose_cmd()
                    if compose_base:
                        self.manager.run_command(
                            [*compose_base, "up", "-d", "db"], cwd=str(paths["root"])
                        )

                        for _i in range(10):
                            time.sleep(2)
                            for suffix in ["-db", "-db-1"]:
                                candidate = f"{container_name}{suffix}"
                                if self.manager.run_command(
                                    ["docker", "ps", "-q", "-f", f"name=^{candidate}$"]
                                ):
                                    db_container = candidate
                                    break
                            if db_container:
                                break

            if db_mode == "shared" and db_container:
                UI.detail(
                    f"Waiting for shared database ({UI.CYAN}{db_container}{UI.COLOR_OFF}) to be ready..."
                )
                start_wait = time.time()
                while time.time() - start_wait < 60:
                    status = self.manager.get_container_status(db_container)
                    if status in {"healthy", "running"}:
                        time.sleep(2)
                        break
                    if status == "exited":
                        UI.error(
                            f"Global database container '{db_container}' exited unexpectedly."
                        )
                        return
                    time.sleep(2)

            if db_container:
                self._execute_orchestrated_db_restore(
                    db_container, db_type, sql_file, paths, project_meta
                )
            else:
                UI.error("  ! Could not find database container for restore.")

    def _execute_orchestrated_db_restore(  # noqa: C901, PLR0912, PLR0915
        self, db_container, db_type, sql_file, paths, project_meta
    ):
        """Internal helper to execute a robust SQL import into a running DB container."""
        import subprocess

        from ldm_core.utils import resolve_infrastructure_mode

        db_mode = resolve_infrastructure_mode(
            "database_mode", project_meta or {}, self.manager.defaults
        )
        db_name = "lportal"
        if db_mode == "shared":
            from ldm_core.utils import sanitize_id

            db_name = f"lportal_{sanitize_id(paths['root'].name).replace('-', '_')}"

        def _wipe_db():
            # 1. Clean Slate (LDM-410)
            # Cloud dumps often lack DROP TABLE commands. We must wipe the target DB first.
            if db_type == "postgresql":
                UI.detail("  - Wiping existing PostgreSQL database schema...")
                # LDM-416: Liferay's official image sets POSTGRES_USER=lportal, meaning the
                # default 'postgres' user DOES NOT EXIST. We must use lportal (which is granted superuser).
                # We use a comprehensive DO block to drop all objects in the public schema
                # to guarantee a clean slate without needing to drop the database itself.
                wipe_script = """
                DO $$ DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                        EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
                    END LOOP;
                    FOR r IN (SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'S') LOOP
                        EXECUTE 'DROP SEQUENCE IF EXISTS public.' || quote_ident(r.relname) || ' CASCADE';
                    END LOOP;
                    FOR r IN (SELECT viewname FROM pg_views WHERE schemaname = 'public') LOOP
                        EXECUTE 'DROP VIEW IF EXISTS public.' || quote_ident(r.viewname) || ' CASCADE';
                    END LOOP;

                    -- LDM-416: Clear Large Objects to prevent pg_largeobject_metadata_oid_index collisions
                    -- 'lo_unlink' is insufficient for Liferay's usage pattern. We must directly delete.
                    DELETE FROM pg_largeobject_metadata;
                    DELETE FROM pg_largeobject;

                    -- Mock cloudsqlsuperuser to prevent ON_ERROR_STOP=1 from aborting LCP imports
                    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'cloudsqlsuperuser') THEN
                        CREATE ROLE cloudsqlsuperuser;
                    END IF;
                END $$;
                """
                # LDM-418: Execute via stdin instead of -c to prevent multi-line parsing failures across docker exec
                # We wrap this in a retry loop because a freshly created Postgres container
                # will initialize and then restart itself, causing temporary connection refusals.
                wipe_success = False
                for _wipe_attempt in range(6):  # Up to ~30 seconds wait
                    try:
                        subprocess.run(
                            [
                                "docker",
                                "exec",
                                "-i",
                                db_container,
                                "psql",
                                "-U",
                                "lportal",
                                "-d",
                                db_name,
                            ],
                            input=wipe_script.encode("utf-8"),
                            check=True,
                            capture_output=True,
                        )
                        wipe_success = True
                        break
                    except subprocess.CalledProcessError as e:
                        # LDM-423: Capture both stdout and stderr for robust error parsing
                        # psql sometimes sends connection errors to stdout
                        raw_err = (e.stderr or b"").decode(errors="ignore")
                        raw_out = (e.stdout or b"").decode(errors="ignore")
                        err_out = f"{raw_err} {raw_out}".lower()

                        if (
                            "shutting down" in err_out
                            or "starting up" in err_out
                            or "does not exist" in err_out
                            or "no such file or directory" in err_out
                        ):
                            UI.debug(f"DB initializing, waiting... ({err_out.strip()})")
                            time.sleep(5)
                        else:
                            UI.warning(f"  ! Non-fatal wipe error: {err_out.strip()}")
                            break  # Other SQL error, stop retrying
                    except Exception as e:
                        UI.warning(f"  ! Wipe encountered an error: {e}")
                        break

                if not wipe_success:
                    UI.warning(
                        "  ! Could not confirm successful schema wipe. Restore may fail."
                    )

            elif db_type in ["mysql", "mariadb"]:
                UI.detail("  - Wiping existing MySQL database...")
                self.manager.run_command(
                    [
                        "docker",
                        "exec",
                        db_container,
                        "mysql",
                        "-u",
                        "lportal",
                        "-ptest",
                        "-e",
                        f"DROP DATABASE IF EXISTS {db_name}; CREATE DATABASE {db_name};",
                    ],
                    check=False,
                )

        # 2. Build Import Command as a list
        import_cmd = []
        if db_type == "postgresql":
            # LDM-410: Use standard user and enforce error stopping for reliability
            import_cmd = [
                "docker",
                "exec",
                "-i",
                db_container,
                "psql",
                "-U",
                "lportal",
                "-d",
                db_name,
                "-v",
                "ON_ERROR_STOP=1",
            ]
        elif db_type in ["mysql", "mariadb"]:
            import_cmd = [
                "docker",
                "exec",
                "-i",
                db_container,
                "mysql",
                "-u",
                "lportal",
                "-ptest",
                db_name,
            ]

        # 3. Execute with Retry
        if import_cmd:
            success = False
            baseline_file = Path(sql_file).parent / ".restore_baseline.sql"
            baseline_dump_cmd = []
            if db_type in ["mysql", "mariadb"]:
                baseline_dump_cmd = [
                    "docker",
                    "exec",
                    db_container,
                    "mysqldump",
                    "-u",
                    "lportal",
                    "-ptest",
                    "--opt",
                    "--add-drop-table",
                    db_name,
                ]
            elif db_type == "postgresql":
                baseline_dump_cmd = [
                    "docker",
                    "exec",
                    db_container,
                    "pg_dump",
                    "-U",
                    "lportal",
                    "--clean",
                    "--if-exists",
                    db_name,
                ]

            has_baseline = False
            if baseline_dump_cmd:
                try:
                    with open(baseline_file, "wb") as bf:
                        subprocess.run(
                            baseline_dump_cmd,
                            stdout=bf,
                            stderr=subprocess.PIPE,
                            check=False,
                        )
                    if baseline_file.exists() and baseline_file.stat().st_size > 0:
                        has_baseline = True
                except Exception as e:
                    UI.debug(f"Could not create pre-restore baseline (non-fatal): {e}")

            for i in range(3):  # Retry up to 3 times for flaky Docker IO
                # LDM-422: We MUST wipe the DB at the start of EVERY retry attempt.
                # If attempt 1 fails halfway through, the tables are created. Attempt 2 will instantly
                # fail with "relation already exists" unless the schema is dropped again.
                _wipe_db()
                try:
                    # Pipe the file stream directly via stdin to prevent memory buffering and command injection
                    with open(sql_file, "rb") as sql_f:
                        subprocess.run(
                            import_cmd,
                            stdin=sql_f,
                            check=True,
                            capture_output=True,
                        )
                    success = True
                    break
                except subprocess.CalledProcessError as e:
                    # LDM-423: Capture both for better debugging
                    raw_err = (e.stderr or b"").decode(errors="ignore")
                    raw_out = (e.stdout or b"").decode(errors="ignore")
                    err_out = f"{raw_err} {raw_out}".strip()

                    if i < 2:
                        UI.warning(f"  ! Restore attempt {i + 1} failed, retrying...")
                        UI.debug(f"  ! Error: {err_out}")
                        time.sleep(5)
                    else:
                        UI.error(
                            f"  ! Database restore failed after 3 attempts: {err_out}"
                        )
                except Exception as e:
                    if i < 2:
                        UI.warning(f"  ! Restore attempt {i + 1} failed, retrying...")
                        UI.debug(f"  ! Error: {e}")
                        time.sleep(5)
                    else:
                        UI.error(f"  ! Database restore failed after 3 attempts: {e}")

            if not success:
                if has_baseline and baseline_file.exists():
                    UI.detail("Restoring database to the pre-restore baseline...")
                    _wipe_db()
                    try:
                        with open(baseline_file, "rb") as bf_read:
                            subprocess.run(
                                import_cmd,
                                stdin=bf_read,
                                check=True,
                                capture_output=True,
                            )
                        UI.detail(
                            "Original database data has been successfully restored."
                        )
                    except Exception as e:
                        UI.error(f"Failed to restore original database baseline: {e}")

                if baseline_file.exists():
                    try:
                        baseline_file.unlink()
                    except OSError:
                        pass

                UI.die(
                    "Database restore failed after all retries. Original data has been preserved.",
                    exit_code=3,
                )

            if baseline_file.exists():
                try:
                    baseline_file.unlink()
                except OSError:
                    pass

            if success:
                UI.success("  + Database restored successfully.")
                if hasattr(self.manager, "config") and hasattr(
                    self.manager.config, "track_roi"
                ):
                    self.manager.config.track_roi(300, "database restore")

                # LDM-410: Auto-update virtualhost to match local hostname

                host_name = project_meta.get("host_name", "localhost")
                from ldm_core.utils import resolve_infrastructure_mode

                db_mode = resolve_infrastructure_mode(
                    "database_mode", project_meta or {}, self.manager.defaults
                )
                db_name = "lportal"
                if db_mode == "shared":
                    from ldm_core.utils import sanitize_id

                    db_name = (
                        f"lportal_{sanitize_id(paths['root'].name).replace('-', '_')}"
                    )

                if db_type == "postgresql":
                    UI.detail(f"  - Synchronizing Virtual Host entries to: {host_name}")
                    self.manager.run_command(
                        [
                            "docker",
                            "exec",
                            db_container,
                            "psql",
                            "-U",
                            "lportal",
                            "-d",
                            db_name,
                            "-c",
                            f"UPDATE virtualhost SET hostname = '{host_name}';",  # nosec B608
                        ],
                        check=False,
                    )
                elif db_type in ["mysql", "mariadb"]:
                    UI.detail(f"  - Synchronizing Virtual Host entries to: {host_name}")
                    self.manager.run_command(
                        [
                            "docker",
                            "exec",
                            db_container,
                            "mysql",
                            "-u",
                            "lportal",
                            "-ptest",
                            "-e",
                            f"UPDATE {db_name}.virtualhost SET hostname = '{host_name}';",  # nosec B608
                        ],
                        check=False,
                    )
