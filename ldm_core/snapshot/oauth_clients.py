"""Does each client extension's OAuth client ID exist in the restored database?

LDM-#2446. Liferay writes the OAuth client ID it generated for a client
extension into `routes/default/<projectName>/`, one file per OAuth application,
named `<application ERC>.oauth2.headless.server.client.id` (or
`.oauth2.user.agent.client.id`). The extension's container reads it from there
and presents it to Liferay, which looks it up in the `OAuth2Application` table.

The two only agree when they come from the same Liferay and the same database.
A package leaves `routes/` out (LDM-#2450) so the importer's Liferay writes its
own, but nothing removes a `routes/` tree already on disk -- an import over an
existing project keeps the old one -- and nothing checked the pair. A
mismatch shows up only as 401s from the extension, far from the import.

So, after an imported package has booted, every client ID under `routes/` is
looked up in the database. A miss is a warning naming the extension and the
fix, never a failure: the import itself worked. The check says nothing when
it cannot be asked -- no client IDs on disk, a database LDM does not run, a
container that is not up, or a query that fails.
"""

from __future__ import annotations

from pathlib import Path

from ldm_core.ui import UI

#: The client-ID files Liferay writes, one per OAuth application type.
CLIENT_ID_SUFFIXES = (
    ".oauth2.headless.server.client.id",
    ".oauth2.user.agent.client.id",
)


def routed_client_ids(routes_root) -> list[tuple[str, str, str]]:
    """`(extension tree, application ERC, client ID)` for each client-ID file."""
    base = Path(routes_root) / "default"
    if not base.is_dir():
        return []
    found = []
    for ext_dir in sorted(base.iterdir()):
        if not ext_dir.is_dir():
            continue
        for entry in sorted(ext_dir.iterdir()):
            suffix = next(
                (s for s in CLIENT_ID_SUFFIXES if entry.name.endswith(s)), None
            )
            if suffix is None or not entry.is_file():
                continue
            try:
                client_id = entry.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if client_id:
                found.append((ext_dir.name, entry.name[: -len(suffix)], client_id))
    return found


def database_client_ids(manager, project_meta, project_root) -> set[str] | None:
    """Every `clientId` in the project's OAuth2Application table, or None when
    the database cannot be asked."""
    from ldm_core.docker_service import DockerService
    from ldm_core.utils import (
        ldm_manages_database_container,
        liferay_container_of,
        resolve_database_config,
        shared_database_container,
        shared_database_name,
    )

    db_type, db_mode = resolve_database_config(project_meta, manager.defaults)
    if db_type not in ("postgresql", "mysql", "mariadb"):
        return None
    if not ldm_manages_database_container(db_mode):
        return None

    target_name = getattr(manager, "target", None) or project_meta.get("target")
    docker_prefix = DockerService.get_docker_cmd_prefix(target_name)

    if db_mode == "shared":
        db_container = shared_database_container(db_type)
        db_name = shared_database_name(Path(project_root).name)
    else:
        db_container = (
            project_meta.get("db_container_name")
            or f"{liferay_container_of(project_meta)}-db"
        )
        db_name = "lportal"

    if not manager.run_command(
        [*docker_prefix, "ps", "-q", "-f", f"name=^{db_container}$"], check=False
    ):
        return None

    # Lowercase table name for both engines: LDM starts MySQL with
    # --lower_case_table_names=1, as the virtualhost sync relies on.
    sql = "SELECT clientId FROM oauth2application;"
    if db_type == "postgresql":
        client = ["psql", "-U", "lportal", "-d", db_name, "-At", "-c", sql]
    else:
        client = ["mysql", "-u", "lportal", "-ptest", "-N", "-B", "-D", db_name]
        client += ["-e", sql]
    out = manager.run_command(
        [*docker_prefix, "exec", db_container, *client], check=False
    )
    if out is None:
        return None
    return {line.strip() for line in out.splitlines() if line.strip()}


def warn_unresolved_oauth_clients(manager, project_id) -> None:
    """Warn for each routed client ID the restored database does not know.

    Best-effort and silent when the check cannot run: it follows an import
    that has already succeeded, and must not turn it into a failure. Callers
    run it only after `cmd_run` reported a ready portal: `import` and
    `quickstart` declare no `--no-wait`, so the run always waits.
    """
    try:
        project_root = manager.detect_project_path(project_id)
        if not project_root:
            return
        project_meta = manager.read_meta(project_root) or {}
        paths = manager.setup_paths(project_root)
        routes_root = paths.get("routes") or Path(project_root) / "routes"

        routed = routed_client_ids(routes_root)
        if not routed:
            return
        known = database_client_ids(manager, project_meta, project_root)
        if known is None:
            return
    except Exception as e:
        UI.debug(f"OAuth client check skipped: {e}")
        return

    for ext, erc, client_id in routed:
        if client_id in known:
            continue
        tree = Path(routes_root) / "default" / ext
        UI.warning(
            f"Client extension '{ext}': the OAuth client ID it holds for "
            f"'{erc}' matches no OAuth application in the restored database, so "
            f"Liferay will refuse it (401) (LDM-#2446). Its routes tree is "
            f"stale. Remove it and restart, so Liferay writes it again from "
            f"this database:\n"
            f"    rm -rf {tree}\n"
            f"    ldm restart {project_id}"
        )
