import json
import os
import platform
import re
import sys

from ldm_core.ui import UI
from ldm_core.utils import (
    get_actual_home,
    run_command,
    sanitize_id,
)


def probe_http_readiness(url, is_running=True, timeout=1.0):
    """Probes an HTTP/HTTPS endpoint with a short timeout.

    Returns tuple: (http_ready: bool, http_status: str)
    http_status values: "ready" | "starting" | "unresponsive"
    """
    if not is_running or not url:
        return False, "unresponsive"

    try:
        import ssl
        import urllib.error
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url, headers={"User-Agent": "LDM-HealthCheck/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310
            if 200 <= resp.status <= 399:
                return True, "ready"
            return False, "starting"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            return True, "ready"
        return False, "unresponsive"
    except Exception:
        return False, "starting"


def run_info(  # noqa: C901, PLR0912, PLR0915
    handler,
    project_id=None,
    credentials_only=False,
    credential_type="admin",
    password_only=False,
):
    """Displays user-friendly project metadata."""
    root = handler.manager.detect_project_path(project_id)
    if not root:
        return

    meta = handler.manager.read_meta(root)
    if not meta:
        UI.warning(f"No metadata found for project at {root}")
        return

    if credentials_only:
        from ldm_core.constants import DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD

        credentials = meta.get("credentials", [])

        # If no credentials block exists, mock one up from the root fields for backwards compatibility
        if not credentials:
            credentials.append(
                {
                    "type": "admin",
                    "email": meta.get("admin_email", DEFAULT_ADMIN_EMAIL),
                    "password": DEFAULT_ADMIN_PASSWORD,
                }
            )

        # Find the requested credential type
        target_cred = next(
            (c for c in credentials if c.get("type") == credential_type), None
        )

        if not target_cred:
            UI.warning(f"No credentials of type '{credential_type}' found.")
            return

        pwd_key = "pass" + "word"  # pragma: allowlist secret
        if password_only:
            # Print only the raw password (no newline for easy scripting piping if possible, though print() adds one)
            pwd = target_cred.get(pwd_key, "")
            print(pwd, end="")
        else:
            # Human-readable fallback
            ident = target_cred.get("email") or target_cred.get("username", "Unknown")
            pwd = target_cred.get(pwd_key, "")
            print(f"[{credential_type.capitalize()}]")
            print(f"Identifier: {ident}")
            print(f"Password: {pwd}")
            if "description" in target_cred:
                print(f"Description: {target_cred['description']}")

        return

    UI.heading(
        f"Project Metadata: {meta.get('liferay_container_name', meta.get('container_name', root.name))}"
    )
    UI.raw(f"  {UI.WHITE}Path:{UI.COLOR_OFF}           {root}")
    # LDM-#1090/#1135: passing the literal string "local" (a .get()
    # default) into get_active_target() as an explicit project_target
    # makes it treat that as a deliberate user choice and skip its own
    # persisted-default (`ldm target use`) fallback -- pass None instead
    # so a project with no explicit target correctly reflects the
    # persisted default, not always "local" regardless of what's
    # actually configured. Reported: `ldm info`/`ldm list` displayed
    # "local" while the actual run used the persisted "aws-2" default.
    from ldm_core.config import get_active_target

    raw_target = getattr(handler.manager, "target", None) or meta.get("target")
    target_node = get_active_target(raw_target).name
    UI.raw(
        f"  {UI.WHITE}Compute Target:{UI.COLOR_OFF} {UI.BCYAN}{target_node}{UI.COLOR_OFF}"
    )

    # Add Status and URL
    # LDM-#1351: metadata stores the project name VERBATIM by design (#1307:
    # "metadata records the name verbatim, Docker receives a transcoded ASCII
    # name"). Docker only ever knows the transcoded form, so querying with the
    # raw value could never match -- `ldm info` reported "unknown" for any
    # non-ASCII project while `ldm list`, which does sanitize (see cmd_list
    # below), reported the true status for the same project in the same moment.
    container_name = sanitize_id(
        meta.get("liferay_container_name")
        or meta.get("container_name")
        or root.name.replace(".", "-")
    )
    from ldm_core.docker_service import DockerService

    status = DockerService.get_status(container_name, target_name=target_node)
    status_color = UI.GREEN if status == "running" else UI.BYELLOW
    UI.raw(
        f"  {UI.WHITE}Status:{UI.COLOR_OFF}     {status_color}{status}{UI.COLOR_OFF}"
    )

    host_name = meta.get("host_name", "localhost")
    ssl_enabled = handler.manager.composer._is_ssl_active(host_name, meta)
    port = meta.get("port", 8080)

    url = f"https://{host_name}" if ssl_enabled else f"http://{host_name}:{port}"
    UI.raw(
        f"  {UI.WHITE}URL:{UI.COLOR_OFF}        {UI.CYAN}{UI.UNDERLINE}{url}{UI.COLOR_OFF}"
    )

    # LDM-388: Explicit Container Names for reference.
    #
    # LDM-#1351: every name here must be the one actually APPLIED, because this
    # block exists to be copied into `docker logs` / `docker exec`. It used to
    # print the verbatim metadata values, so for a project named "Saarbrücken"
    # it offered `Saarbrücken`, `Saarbrücken-db` and `Saarbrücken-lfr-tunnel` --
    # none of which exist; Docker has `Saarbruecken`, `Saarbruecken-db`. The
    # project name itself stays verbatim: that is what the user types.
    UI.raw("")
    UI.raw(f"  {UI.WHITE}Provisioned Containers:{UI.COLOR_OFF}")
    UI.raw(
        f"    {UI.WHITE}Liferay:{UI.COLOR_OFF}    {UI.CYAN}{container_name}{UI.COLOR_OFF}"
    )

    project_name = meta.get("container_name", root.name)

    # LDM-#1351: in shared mode there is no per-project database container --
    # the compose file defines only `liferay` -- so naming `<project>-db` sent
    # the user after something that was deliberately never created. Report the
    # cluster and the database inside it instead.
    from ldm_core.utils import (
        resolve_infrastructure_mode,
        search_index_prefix,
        shared_database_name,
    )

    db_mode = resolve_infrastructure_mode(
        "database_mode", meta, handler.manager.defaults
    )
    if db_mode == "shared":
        UI.raw(
            f"    {UI.WHITE}Database:{UI.COLOR_OFF}   {UI.CYAN}liferay-db-global{UI.COLOR_OFF} {UI.DIM}(shared){UI.COLOR_OFF}"
        )
        UI.raw(
            f"      {UI.WHITE}└─ Database:{UI.COLOR_OFF}    {UI.CYAN}{shared_database_name(project_name)}{UI.COLOR_OFF}"
        )
    else:
        db_container = meta.get("db_container_name")
        UI.raw(
            f"    {UI.WHITE}Database:{UI.COLOR_OFF}   {UI.CYAN}{sanitize_id(db_container) if db_container else 'N/A'}{UI.COLOR_OFF}"
        )

    # LDM-#1351/#1362: report the mode the project was PROVISIONED with, not a
    # guess. This used to claim "Shared (Global)" for any project on a tag newer
    # than 2025.2.0 regardless of how it was built -- observed reporting shared
    # (and an index prefix) for a project whose compose was unambiguously a
    # sidecar. That was a second, independent derivation of a fact the composer
    # already decided, the same pattern behind #1354 and #1359.
    #
    # `search_mode` is persisted to meta since #1362, so it can simply be read.
    # `use_shared_search` remains the fallback for projects provisioned before
    # that, and the version heuristic is gone.
    resolved_search_mode = resolve_infrastructure_mode(
        "search_mode", meta, handler.manager.defaults
    )
    use_shared = (
        resolved_search_mode == "shared"
        if resolved_search_mode
        else str(meta.get("use_shared_search", "false")).lower() == "true"
    )

    search_mode = "Shared (Global)" if use_shared else "Sidecar (Isolated)"
    UI.raw(
        f"    {UI.WHITE}Search:{UI.COLOR_OFF}     {UI.CYAN}{search_mode}{UI.COLOR_OFF}"
    )
    if use_shared:
        UI.raw(
            # LDM-#1351: Liferay lowercases the prefix on the way in
            # (CompanyIdIndexNameBuilder.setIndexNamePrefix calls StringUtil.trim
            # then StringUtil.toLowerCase), and the transcoded form is what it
            # receives. Verified against a running project: a configured
            # `ldm-SharedIdx-` produced `ldm-sharedidx-…` indices.
            f"      {UI.WHITE}└─ Index Prefix:{UI.COLOR_OFF} {UI.CYAN}{search_index_prefix(project_name)}{UI.COLOR_OFF}"
        )
    if meta.get("share_provider") == "lfr-tunnel-docker" or meta.get(
        "tunnel_container_name"
    ):
        UI.raw(
            f"    {UI.WHITE}Tunnel:{UI.COLOR_OFF}     {UI.CYAN}{sanitize_id(meta.get('tunnel_container_name')) or 'N/A'}{UI.COLOR_OFF}"
        )

    # Actively scan for client extensions in workspace
    extensions = []
    paths = handler.manager.setup_paths(root)
    if paths["cx"].exists():
        from ldm_core.handlers.workspace import WorkspaceService

        handler = WorkspaceService(handler.manager)
        extensions = handler.scan_client_extensions(
            paths["root"], paths["cx"], paths["ce_dir"]
        )

    # Fallback to metadata if no workspace is found
    if not extensions:
        extensions = meta.get("extensions", [])
        if isinstance(extensions, str):
            try:
                import json

                extensions = json.loads(extensions)
            except Exception:
                extensions = []

    share_subdomain = meta.get("share_subdomain")
    share_domain = meta.get("share_domain", "lfr-demo.online")

    project_name = meta.get("container_name", root.name)
    is_shared = meta.get("share") or meta.get("share_provider")

    fetched_urls = []
    if is_shared and share_subdomain:
        fetched_urls = handler.manager.share.resolve_public_tunnel_urls(
            share_subdomain, project_id
        )

    for ext in extensions:
        if isinstance(ext, dict) and ext.get("is_service"):
            ext_id = ext.get("id")
            ext_name = f"{project_name}-{ext_id}"

            if ssl_enabled:
                local_url = f"https://{ext_id}.{host_name}"
            else:
                local_url = f"http://{ext_id}.{host_name}:{port}"

            urls_str = local_url
            if is_shared and share_subdomain:
                public_url = None
                for url in fetched_urls:
                    if f"-{ext_id}." in url:
                        public_url = url
                        break
                if not public_url:
                    public_url = f"https://{share_subdomain}-{ext_id}.{share_domain}"
                urls_str = f"{local_url} | {public_url}"

            UI.raw(
                f"    {UI.WHITE}Extension:{UI.COLOR_OFF}  {UI.CYAN}{ext_name}{UI.COLOR_OFF} -> {urls_str}"
            )

    UI.raw("")

    # Determine specific colors for known keys
    keys_to_skip = ["root", "custom_env", "credentials"]

    # Inject extension share subdomains into meta for display
    if is_shared and share_subdomain:
        for ext in extensions:
            if isinstance(ext, dict) and ext.get("is_service"):
                ext_id = ext.get("id")
                if ext_id:
                    meta[f"share_subdomain_{ext_id.replace('-', '_')}"] = (
                        f"{share_subdomain}-{ext_id}"
                    )

    for key, value in sorted(meta.items()):
        if key in keys_to_skip:
            continue

        # Format value
        val_str = str(value)
        if val_str.lower() == "true":
            val_str = f"{UI.GREEN}{val_str}{UI.COLOR_OFF}"
        elif "password" in key.lower() or "secret" in key.lower():
            val_str = f"{UI.DIM}[hidden]{UI.COLOR_OFF}"
        elif val_str.lower() == "false":
            val_str = f"{UI.BYELLOW}{val_str}{UI.COLOR_OFF}"
        else:
            val_str = f"{UI.CYAN}{val_str}{UI.COLOR_OFF}"

        UI.raw(f"  {UI.WHITE}{key:<30}{UI.COLOR_OFF} {val_str}")

    # Pretty print custom_env if it exists
    custom_env = meta.get("custom_env")
    if custom_env and custom_env != "{}":
        if isinstance(custom_env, dict):
            env_dict = custom_env
        else:
            try:
                import json

                env_dict = json.loads(custom_env)
            except Exception:
                env_dict = {}

        if env_dict:
            UI.raw(f"\n  {UI.WHITE}Custom Environment Variables:{UI.COLOR_OFF}")
            for k, v in env_dict.items():
                UI.raw(
                    f"    {UI.WHITE}{k:<20}{UI.COLOR_OFF} {UI.CYAN}{v}{UI.COLOR_OFF}"
                )
    UI.raw("")


def run_status(  # noqa: C901, PLR0912, PLR0915
    handler, project_id=None, all_projects=False, detailed=False, as_json=False
):
    """Displays a summary of active global services and projects."""
    # LDM-#1093: --json bypasses every UI.raw()/UI.table() print below and
    # accumulates the same underlying data into json_infra/json_projects
    # instead, printed once as a single object at the very end. Exit-code
    # logic is untouched -- only the presentation differs.
    json_infra = []
    json_projects = []
    if not as_json:
        UI.heading("LDM Service Status")

    # 1. Global Infrastructure (skipped in detailed project view to avoid clutter if a specific project was asked,
    # but shown by default otherwise)
    from ldm_core.constants import INFRA_SERVICES

    infra_rows = []
    any_infra = False
    if not detailed or not project_id:
        # LDM-#1090/#1133/#1165: shared infra resolves to whichever target is
        # active for this invocation (a project on a remote node has its
        # shared search/proxy *on that node*, not always local -- see
        # docs/explanation/remote-node-architecture.md §5), so this status
        # check must ask the same engine setup_global_search()/
        # setup_global_database() actually use, not always the local daemon.
        # Matches the doctor.py precedent (PR #1174): only genuinely
        # local-machine-specific deep checks (none exist in this shallow
        # ps/inspect loop) would stay hardcoded local.
        infra_target_name = getattr(handler.manager, "target", None)
        from ldm_core.docker_service import DockerService

        infra_docker_prefix = DockerService.get_docker_cmd_prefix(infra_target_name)

        for container, label in INFRA_SERVICES:
            res = run_command(
                [*infra_docker_prefix, "ps", "-q", "-f", f"name=^{container}$"],
                check=False,
            )
            if res:
                inspect = run_command(
                    [
                        *infra_docker_prefix,
                        "inspect",
                        "--format",
                        "{{.State.Status}} {{.Config.Image}}",
                        container,
                    ],
                    check=False,
                )
                if inspect:
                    status, image = inspect.split(" ", 1)
                    infra_rows.append(
                        [
                            f"{UI.GREEN}●{UI.COLOR_OFF} {label}",
                            status.capitalize(),
                            image,
                        ]
                    )
                    json_infra.append(
                        {
                            "name": label,
                            "container": container,
                            "status": status.capitalize(),
                            "image": image,
                        }
                    )
                    any_infra = True

        if not as_json:
            if infra_rows:
                UI.raw(f"{UI.WHITE}Global Infrastructure:{UI.COLOR_OFF}")
                UI.table(infra_rows)
            elif not project_id or not detailed:
                UI.raw(
                    f"  {UI.WHITE}No global services are currently running.{UI.COLOR_OFF}"
                )
            UI.raw("")

    # Helper functions for detailed view formatting
    def clean_ports(ports_str):
        if not ports_str:
            return "-"
        parts = [p.strip() for p in ports_str.split(",") if p.strip()]
        cleaned = []
        seen = set()
        for part in parts:
            if "->" in part:
                left, right = part.split("->")
                host_port = left.split(":")[-1]
                container_port = right.split("/")[0]
                mapping = f"{host_port}->{container_port}"
                if mapping not in seen:
                    seen.add(mapping)
                    cleaned.append(mapping)
            else:
                port_val = part.split("/")[0]
                if port_val not in seen:
                    seen.add(port_val)
                    cleaned.append(port_val)
        return ", ".join(cleaned) if cleaned else "-"

    def format_status(status_str):
        status_lower = status_str.lower()
        if "unhealthy" in status_lower:
            return f"{UI.RED}●{UI.COLOR_OFF} {UI.RED}{status_str}{UI.COLOR_OFF}"
        if "healthy" in status_lower:
            return f"{UI.GREEN}●{UI.COLOR_OFF} {UI.GREEN}{status_str}{UI.COLOR_OFF}"
        if "starting" in status_lower or "health:" in status_lower:
            return f"{UI.YELLOW}●{UI.COLOR_OFF} {UI.YELLOW}{status_str}{UI.COLOR_OFF}"
        if "up" in status_lower:
            return f"{UI.GREEN}●{UI.COLOR_OFF} {UI.GREEN}{status_str}{UI.COLOR_OFF}"
        if "exited" in status_lower:
            return f"{UI.DIM}○ {status_str}{UI.COLOR_OFF}"
        return f"{UI.WHITE}{status_str}{UI.COLOR_OFF}"

    # 2. Project Status
    from ldm_core.utils import sanitize_id

    roots = []
    if project_id:
        root_path = handler.manager.detect_project_path(project_id, fatal=False)
        if not root_path:
            UI.error(f"Project '{project_id}' not found.")
            sys.exit(1)
        roots = [{"path": root_path, "version": "unknown"}]
        meta = handler.manager.read_meta(root_path)
        if meta.get("tag"):
            roots[0]["version"] = meta["tag"]
    else:
        roots = handler.manager.find_dxp_roots()

    active_projects = False
    project_rows = []
    is_requested_project_running = False

    if detailed:
        # Detailed view display
        any_detailed_printed = False
        for r in roots:
            path = r["path"]
            meta = handler.manager.read_meta(path)
            p_id = (
                meta.get("liferay_container_name")
                or meta.get("container_name")
                or path.name
            )
            safe_name = sanitize_id(p_id)

            # LDM-#1090: this must go through the project's own target
            # --context, not always the local Docker daemon -- a project
            # running on a remote --node was silently checked against the
            # orchestrating host's local containers instead.
            #
            # LDM-#1135: pass None (not the literal string "local") when
            # meta has no explicit target, so get_active_target() consults
            # its own persisted-default (`ldm target use`) fallback
            # instead of always resolving to "local" regardless of what's
            # actually configured.
            from ldm_core.config import get_active_target
            from ldm_core.docker_service import DockerService

            target_node = get_active_target(meta.get("target")).name
            docker_prefix = DockerService.get_docker_cmd_prefix(target_node)

            # Query all containers matching label com.liferay.ldm.project={safe_name}
            cmd = [
                *docker_prefix,
                "ps",
                "-a",
                "--filter",
                f"label=com.liferay.ldm.project={safe_name}",
                "--format",
                '{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}\t{{.Label "com.docker.compose.service"}}',
            ]
            res = run_command(cmd, check=False)

            # Check if this project is running
            project_running = False
            detailed_rows = []
            json_containers = []
            if res and res.strip():
                for line in res.strip().splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        c_names = parts[0]
                        c_status = parts[1]
                        c_image = parts[2] if len(parts) > 2 else ""
                        c_ports = parts[3] if len(parts) > 3 else ""
                        c_service = parts[4] if len(parts) > 4 else ""

                        if "up" in c_status.lower():
                            project_running = True
                            active_projects = True

                        # Derive service name
                        svc = (
                            c_service
                            or c_names.replace(f"{safe_name}-", "").rsplit("-", 1)[0]
                        )
                        if not svc or svc == c_names:
                            svc = c_names

                        detailed_rows.append(
                            [
                                svc,
                                format_status(c_status),
                                clean_ports(c_ports),
                                c_image,
                            ]
                        )
                        json_containers.append(
                            {
                                "service": svc,
                                "name": c_names,
                                "status": c_status,
                                "ports": c_ports or None,
                                "image": c_image,
                            }
                        )

            host = meta.get("host_name", "localhost")
            ssl = str(meta.get("ssl")).lower() == "true"
            proto = "https" if ssl else "http"
            port = (
                str(meta.get("ssl_port", "443"))
                if ssl
                else str(meta.get("port", "8080"))
            )
            url = f"{proto}://{host}"
            if (ssl and port != "443") or (not ssl and port != "80"):
                url += f":{port}"

            http_ready, http_status = probe_http_readiness(
                url, is_running=project_running
            )

            if project_id:
                is_requested_project_running = project_running

            if as_json:
                json_projects.append(
                    {
                        "project": p_id,
                        "version": r["version"],
                        "running": project_running,
                        "http_ready": http_ready,
                        "http_status": http_status,
                        "containers": json_containers,
                    }
                )
                continue

            # Only print if we requested a specific project, or if we have containers,
            # or if all_projects is set.
            if project_id or detailed_rows or all_projects:
                UI.raw(f"{UI.WHITE}Project: {UI.CYAN}{p_id}{UI.COLOR_OFF}")
                if detailed_rows:
                    UI.table(detailed_rows)
                else:
                    UI.raw(
                        f"  {UI.DIM}No containers found for this project.{UI.COLOR_OFF}"
                    )
                UI.raw("")
                any_detailed_printed = True

        if as_json:
            print(
                json.dumps(
                    {"infrastructure": json_infra, "projects": json_projects}, indent=2
                )
            )
        elif not any_detailed_printed:
            UI.raw(f"  {UI.WHITE}No projects are currently running.{UI.COLOR_OFF}")

        # Exit logic for detailed view
        if project_id:
            sys.exit(0 if is_requested_project_running else 1)
        else:
            if not any_infra and not active_projects:
                sys.exit(1)
            sys.exit(0)

    else:
        # Standard non-detailed view
        for r in roots:
            path = r["path"]
            meta = handler.manager.read_meta(path)
            p_id = (
                meta.get("liferay_container_name")
                or meta.get("container_name")
                or path.name
            )
            safe_name = sanitize_id(p_id)

            # LDM-#1090: same as the --detailed branch above -- resolve the
            # project's own target --context rather than always querying
            # the local Docker daemon.
            #
            # LDM-#1135: pass None (not the literal string "local") when
            # meta has no explicit target, so get_active_target() consults
            # its own persisted-default (`ldm target use`) fallback.
            from ldm_core.config import get_active_target
            from ldm_core.docker_service import DockerService

            target_node = get_active_target(meta.get("target")).name
            docker_prefix = DockerService.get_docker_cmd_prefix(target_node)

            # Query all containers matching label com.liferay.ldm.project={safe_name}
            # A project is running if any of its containers are active/running
            cmd = [
                *docker_prefix,
                "ps",
                "-q",
                "--filter",
                f"label=com.liferay.ldm.project={safe_name}",
                "--filter",
                "status=running",
            ]
            running_containers = run_command(cmd, check=False)
            project_running = bool(running_containers and running_containers.strip())

            host = meta.get("host_name", "localhost")
            ssl = str(meta.get("ssl")).lower() == "true"
            proto = "https" if ssl else "http"
            port = (
                str(meta.get("ssl_port", "443"))
                if ssl
                else str(meta.get("port", "8080"))
            )
            url = f"{proto}://{host}"
            if (ssl and port != "443") or (not ssl and port != "80"):
                url += f":{port}"

            http_ready, http_status = probe_http_readiness(
                url, is_running=project_running
            )

            if project_id:
                is_requested_project_running = project_running

            if as_json:
                json_projects.append(
                    {
                        "project": p_id,
                        "version": r["version"],
                        "target": target_node,
                        "running": project_running,
                        "http_ready": http_ready,
                        "http_status": http_status,
                        "url": url if project_running else None,
                    }
                )
                if project_running or all_projects:
                    active_projects = True
                continue

            if project_running:
                active_projects = True
                project_rows.append(
                    [
                        f"{UI.GREEN}●{UI.COLOR_OFF} {UI.CYAN}{p_id}{UI.COLOR_OFF}",
                        r["version"],
                        f"{UI.UNDERLINE}{url}{UI.COLOR_OFF}",
                        f"{UI.BCYAN}{target_node}{UI.COLOR_OFF}",
                    ]
                )
            # If this is the specific project requested, or we requested all projects, show it stopped
            elif project_id or all_projects:
                project_rows.append(
                    [
                        f"{UI.WHITE}○{UI.COLOR_OFF} {p_id}",
                        r["version"],
                        f"{UI.DIM}Stopped{UI.COLOR_OFF}",
                        f"{UI.DIM}{target_node}{UI.COLOR_OFF}",
                    ]
                )
                # Mark active_projects as true if we show at least one row, to prevent error exit
                if all_projects:
                    active_projects = True

        if as_json:
            print(
                json.dumps(
                    {"infrastructure": json_infra, "projects": json_projects}, indent=2
                )
            )
        elif project_rows:
            label = (
                "All Managed Projects"
                if all_projects
                else ("Project Status" if project_id else "Active Projects")
            )
            UI.raw(f"{UI.WHITE}{label}:{UI.COLOR_OFF}")
            UI.table(project_rows)
        else:
            UI.raw(f"  {UI.WHITE}No projects are currently running.{UI.COLOR_OFF}")

        # Exit logic
        if project_id:
            sys.exit(0 if is_requested_project_running else 1)
        else:
            if not any_infra and not active_projects:
                sys.exit(1)
            sys.exit(0)


def _get_env_info(self):  # noqa: C901, PLR0912, PLR0915
    """Extracts architecture, OS, and Docker provider information."""
    arch = "Unknown"
    host_os = "Unknown"
    provider = "Unknown"

    # 1. Architecture & OS
    try:
        platform_str = f"{platform.system()}-{platform.release()}-{platform.machine()}"
        p_low = platform_str.lower()
        is_mac = "mac" in p_low or "darwin" in p_low

        if "arm64" in p_low or "aarch64" in p_low:
            arch = "Apple Silicon" if is_mac else "ARM64"
        elif "x86_64" in p_low or "amd64" in p_low or "i386" in p_low:
            arch = "Apple Intel" if is_mac else "x86_64"

        if is_mac:
            # Improved mapping: darwin21 = macOS 12 Monterey, etc.
            ver_match = re.search(r"darwin[-]?(\d+)", p_low)
            if not ver_match:
                ver_match = re.search(r"macos[-]?(\d+)", p_low)

            if ver_match:
                v_num = int(ver_match.group(1))
                if v_num >= 20:
                    v_macos = v_num - 9
                    names = {
                        11: "Big Sur",
                        12: "Monterey",
                        13: "Ventura",
                        14: "Sonoma",
                        15: "Sequoia",
                        16: "Tahoe",
                        17: "17",
                    }
                    name = names.get(v_macos, str(v_macos))
                    host_os = f"macOS {v_macos} {name}".strip()
                else:
                    host_os = f"macOS {v_num}"
            else:
                host_os = "macOS 11+"
        elif "microsoft" in p_low or "windows" in p_low:
            host_os = "Windows 11"
            arch = "Windows PC"
        elif "fedora" in p_low:
            # Capture major version if possible
            fedora_match = re.search(r"fc(\d+)", p_low)
            host_os = f"Fedora {fedora_match.group(1) if fedora_match else ''}".strip()
            arch = "Linux Workstation"
        elif "ubuntu" in p_low:
            ubuntu_match = re.search(r"(\d+\.\d+)", p_low)
            host_os = f"Ubuntu {ubuntu_match.group(1) if ubuntu_match else ''}".strip()
            arch = "Linux Node" if "server" in p_low else "Linux Workstation"
        elif "linux" in p_low:
            host_os = "Linux"
            arch = "Linux Workstation"

            # Attempt to read /etc/os-release for accurate distro detection
            try:
                if os.path.exists("/etc/os-release"):
                    with open("/etc/os-release") as f:
                        os_release = f.read().lower()

                        distro_id = re.search(r"^id=([^\n]+)", os_release, re.MULTILINE)
                        version_id = re.search(
                            r"^version_id=([^\n]+)", os_release, re.MULTILINE
                        )

                        d_id = distro_id.group(1).strip("\"'") if distro_id else ""
                        v_id = version_id.group(1).strip("\"'") if version_id else ""

                        if d_id == "ubuntu":
                            host_os = f"Ubuntu {v_id}".strip()
                        elif d_id == "fedora":
                            host_os = f"Fedora {v_id}".strip()
                        elif d_id:
                            host_os = f"{d_id.capitalize()} {v_id}".strip()
            except Exception:
                pass
    except Exception:
        pass

    # 2. Docker Provider
    mount_type = None
    try:
        context_res = run_command(["docker", "context", "show"], check=False)
        context = context_res.strip() if context_res else ""
        if context:
            inspect = run_command(
                ["docker", "context", "inspect", context], check=False
            )
            if inspect:
                data = json.loads(inspect)[0]
                endpoint = ((data.get("Endpoints") or {}).get("docker") or {}).get(
                    "Host", ""
                )
                if endpoint:
                    if ".colima" in endpoint:
                        provider = "Colima"
                    elif "orbstack" in endpoint:
                        provider = "OrbStack"
                    elif "docker.sock" in endpoint or "docker_engine" in endpoint:
                        # Standard socket. Determine if it's Native or Desktop.
                        sys_type = platform.system().lower()
                        if sys_type == "linux":
                            # Check for WSL
                            try:
                                with open("/proc/version") as f:
                                    if "microsoft" in f.read().lower():
                                        provider = "Native WSL2"
                                    else:
                                        provider = "Native Docker"
                            except Exception:
                                provider = "Native Docker"
                        else:
                            provider = "Docker Desktop"

                if provider == "Unknown":
                    if context == "colima":
                        provider = "Colima"
                    elif context == "orbstack":
                        provider = "OrbStack"
                    elif context == "desktop-linux":
                        provider = "Docker Desktop"

        # 3. Final safety wash for slug/sync compatibility
        p_low = platform.system().lower()
        if provider == "Unknown":
            if p_low == "linux":
                try:
                    with open("/proc/version") as f:
                        if "microsoft" in f.read().lower():
                            provider = "Native WSL2"
                        else:
                            provider = "Native Docker"
                except Exception:
                    provider = "Native Docker"
            elif p_low == "windows" or "win32" in p_low:
                provider = "Docker Desktop"
            elif p_low == "darwin":
                # Colima and Orbstack usually have distinct context names
                # but if we are here, default to Docker Desktop
                provider = "Docker Desktop"

        # 4. Colima-specific info
        if provider == "Colima":
            try:
                # 'colima status' contains mountType in its output
                status_out = run_command(["colima", "status"], check=False)
                if status_out:
                    for line in status_out.strip().split("\n"):
                        if "mountType:" in line:
                            mount_type = line.split("mountType:")[1].strip()
                            break

                # 4. Check colima.yaml for explicit 'writable' flag
                # This is more reliable for 'sshfs' than just checking 'mount' output
                import yaml

                home = get_actual_home()
                config_path = (
                    home
                    / ".colima"
                    / (context if context != "default" else "default")
                    / "colima.yaml"
                )
                if config_path.exists():
                    with open(config_path) as f:
                        config = yaml.safe_load(f)
                        mounts = config.get("mounts", [])
                        is_explicitly_writable = False
                        for m in mounts:
                            # Standard home mount check
                            if (
                                m.get("location") == str(home)
                                or m.get("location") == "/Users"
                                or m.get("location").startswith("/Users/")
                            ) and m.get("writable") is True:
                                is_explicitly_writable = True
                                break

                        # Store this in a way doctor can use
                        if not is_explicitly_writable and mount_type == "sshfs":
                            # We'll use this to trigger a warning even if the write test hasn't run yet
                            self._colima_mount_not_writable = True
            except Exception:
                pass

    except Exception:
        pass

    return arch, host_os, provider, mount_type


def run_list(handler, as_json=False):  # noqa: C901, PLR0912, PLR0915
    # LDM-#1093: --json bypasses the table/color-formatting path entirely --
    # a stable, machine-readable array instead of fragile table-parsing,
    # which was reported as a real automation breakage.
    if not as_json:
        UI.heading("LDM Sandbox Projects")
    roots = handler.manager.find_dxp_roots()
    if not roots:
        if as_json:
            print(json.dumps([]))
        else:
            UI.detail("No projects found.")
        return

    headers = ["Project", "Version", "Target", "Status", "URL"]
    rows = []
    json_entries = []

    for r in roots:
        path = r["path"]
        meta = handler.manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )
        version = r["version"]
        # LDM-#1135: pass None (not the literal string "local") when meta
        # has no explicit target, so get_active_target() consults its own
        # persisted-default (`ldm target use`) fallback instead of always
        # resolving to "local" regardless of what's actually configured.
        # Reported: `ldm list` displayed "local" while the actual run
        # used the persisted "aws-2" default.
        from ldm_core.config import get_active_target

        target_node = get_active_target(meta.get("target")).name

        from ldm_core.docker_service import DockerService

        docker_prefix = DockerService.get_docker_cmd_prefix(target_node)

        # Check container status
        containers_status = run_command(
            [
                *docker_prefix,
                "ps",
                "-a",
                "--filter",
                f"name=^{name}$",
                "--format",
                "{{.State}}",
            ],
            check=False,
        )
        if containers_status:
            states = containers_status.splitlines()
            running_count = states.count("running")
            total_count = len(states)
            if total_count > 1:
                status = (
                    f"Running ({running_count}/{total_count})"
                    if running_count > 0
                    else f"Stopped (0/{total_count})"
                )
                status_color = UI.GREEN if running_count > 0 else UI.WHITE
            else:
                status = states[0].capitalize()
                status_color = UI.GREEN if status == "Running" else UI.WHITE
        else:
            running_count = 0
            total_count = 0
            status = "Stopped"
            status_color = UI.WHITE

        # LDM-#1091: a container reporting "running" can still have failed
        # its own HEALTHCHECK (e.g. Postgres crash-looping after ENOSPC).
        # `ldm list` previously only ever looked at .State.Status, so a
        # process that stayed alive while unhealthy was reported green
        # indefinitely. Surface Docker's own health verdict -- for the app
        # container and its DB -- when one is actually defined, without
        # requiring a live HTTP probe on every listed project.
        db_unhealthy = False
        if running_count > 0:
            app_health = run_command(
                [*docker_prefix, "inspect", "-f", "{{.State.Health.Status}}", name],
                check=False,
            )
            if app_health and app_health.strip().lower() == "unhealthy":
                status = "Unhealthy"
                status_color = UI.RED

            db_name = meta.get("db_container_name") or f"{name}-db"
            db_health = run_command(
                [*docker_prefix, "inspect", "-f", "{{.State.Health.Status}}", db_name],
                check=False,
            )
            if db_health and db_health.strip().lower() == "unhealthy":
                db_unhealthy = True
                if status_color != UI.RED:
                    status = f"{status} (DB unhealthy)"
                    status_color = UI.YELLOW

        # Access URL
        host = meta.get("host_name", "localhost")
        port = meta.get("port", "8080")
        ssl = str(meta.get("ssl")).lower() == "true"
        ssl_port = meta.get("ssl_port", "443")

        proto = "https" if ssl else "http"
        access_port = (
            f":{ssl_port}"
            if (ssl and ssl_port != "443")
            else (f":{port}" if not ssl and port != "80" else "")
        )
        url = f"{proto}://{host}{access_port}"

        # Seeded Indicator
        seeded = str(meta.get("seeded", "false")).lower() == "true"
        seeded_indicator = " 🌱" if seeded else ""

        http_ready, http_status = probe_http_readiness(
            url, is_running=(running_count > 0)
        )

        last_seen_ts = r.get("last_seen")

        if as_json:
            json_entries.append(
                {
                    "project": name,
                    "version": version,
                    "target": target_node,
                    "status": status,
                    "running_containers": running_count,
                    "total_containers": total_count,
                    "db_unhealthy": db_unhealthy,
                    "http_ready": http_ready,
                    "http_status": http_status,
                    "url": url,
                    "seeded": seeded,
                    "path": str(path),
                    "last_seen": last_seen_ts,
                }
            )
            continue

        rows.append(
            [
                f"{UI.CYAN}{name}{UI.COLOR_OFF}{seeded_indicator}",
                version,
                f"{UI.BCYAN}{target_node}{UI.COLOR_OFF}",
                f"{status_color}{status}{UI.COLOR_OFF}",
                f"{UI.UNDERLINE}{url}{UI.COLOR_OFF}",
            ]
        )

    if as_json:
        print(json.dumps(json_entries, indent=2))
        return

    UI.table(rows, headers=headers)
    UI.raw("")

    if handler.manager.verbose:
        from datetime import datetime

        last_seen_ts = r.get("last_seen")
        if last_seen_ts:
            try:
                dt = datetime.fromtimestamp(last_seen_ts)
                last_seen_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                last_seen_str = "Unknown"
        else:
            last_seen_str = "Unknown"

        print(f"    {UI.BYELLOW}Path:{UI.COLOR_OFF} {path}")
        print(f"    {UI.BYELLOW}Last Seen:{UI.COLOR_OFF} {last_seen_str}\n")
