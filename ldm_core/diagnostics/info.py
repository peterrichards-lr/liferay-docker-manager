import json
import os
import platform
import re
import sys

from ldm_core.ui import UI
from ldm_core.utils import (
    get_actual_home,
    run_command,
)


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
    UI.raw(f"  {UI.WHITE}Path:{UI.COLOR_OFF}       {root}")

    # Add Status and URL
    container_name = (
        meta.get("liferay_container_name")
        or meta.get("container_name")
        or root.name.replace(".", "-")
    )
    from ldm_core.docker_service import DockerService

    status = DockerService.get_status(container_name)
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

    # LDM-388: Explicit Container Names for reference
    UI.raw("")
    UI.raw(f"  {UI.WHITE}Provisioned Containers:{UI.COLOR_OFF}")
    UI.raw(
        f"    {UI.WHITE}Liferay:{UI.COLOR_OFF}    {UI.CYAN}{meta.get('liferay_container_name', 'N/A')}{UI.COLOR_OFF}"
    )
    UI.raw(
        f"    {UI.WHITE}Database:{UI.COLOR_OFF}   {UI.CYAN}{meta.get('db_container_name', 'N/A')}{UI.COLOR_OFF}"
    )
    project_name = meta.get("container_name", root.name)

    default_shared = (
        "true" if handler.manager.defaults.get("search_mode") == "shared" else "false"
    )
    use_shared = str(meta.get("use_shared_search", default_shared)).lower() == "true"
    if not use_shared and handler.manager.parse_version(meta.get("tag", "")) >= (
        2025,
        2,
        0,
    ):
        use_shared = True

    search_mode = "Shared (Global)" if use_shared else "Sidecar (Isolated)"
    UI.raw(
        f"    {UI.WHITE}Search:{UI.COLOR_OFF}     {UI.CYAN}{search_mode}{UI.COLOR_OFF}"
    )
    if use_shared:
        UI.raw(
            f"      {UI.WHITE}└─ Index Prefix:{UI.COLOR_OFF} {UI.CYAN}ldm-{project_name}-{UI.COLOR_OFF}"
        )
    if meta.get("share_provider") == "lfr-tunnel-docker" or meta.get(
        "tunnel_container_name"
    ):
        UI.raw(
            f"    {UI.WHITE}Tunnel:{UI.COLOR_OFF}     {UI.CYAN}{meta.get('tunnel_container_name', 'N/A')}{UI.COLOR_OFF}"
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
    keys_to_skip = ["root", "custom_env"]

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
        elif val_str.lower() == "false":
            val_str = f"{UI.BYELLOW}{val_str}{UI.COLOR_OFF}"
        else:
            val_str = f"{UI.CYAN}{val_str}{UI.COLOR_OFF}"

        UI.raw(f"  {UI.WHITE}{key:<30}{UI.COLOR_OFF} {val_str}")

    # Pretty print custom_env if it exists
    custom_env = meta.get("custom_env")
    if custom_env and custom_env != "{}":
        try:
            import json

            env_dict = json.loads(custom_env)
            UI.raw(f"\n  {UI.WHITE}Custom Environment Variables:{UI.COLOR_OFF}")
            for k, v in env_dict.items():
                UI.raw(
                    f"    {UI.WHITE}{k:<20}{UI.COLOR_OFF} {UI.CYAN}{v}{UI.COLOR_OFF}"
                )
        except Exception:
            pass
    UI.raw("")


def run_status(handler, project_id=None, all_projects=False, detailed=False):  # noqa: C901, PLR0912, PLR0915
    """Displays a summary of active global services and projects."""
    UI.heading("LDM Service Status")

    # 1. Global Infrastructure (skipped in detailed project view to avoid clutter if a specific project was asked,
    # but shown by default otherwise)
    from ldm_core.constants import INFRA_SERVICES

    infra_rows = []
    any_infra = False
    if not detailed or not project_id:
        for container, label in INFRA_SERVICES:
            res = run_command(
                ["docker", "ps", "-q", "-f", f"name=^{container}$"], check=False
            )
            if res:
                inspect = run_command(
                    [
                        "docker",
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
                    any_infra = True

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

            # Query all containers matching label com.liferay.ldm.project={safe_name}
            cmd = [
                "docker",
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

            if project_id:
                is_requested_project_running = project_running

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

        if not any_detailed_printed:
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

            # Query all containers matching label com.liferay.ldm.project={safe_name}
            # A project is running if any of its containers are active/running
            cmd = [
                "docker",
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

            if project_id:
                is_requested_project_running = project_running

            if project_running:
                active_projects = True
                project_rows.append(
                    [
                        f"{UI.GREEN}●{UI.COLOR_OFF} {UI.CYAN}{p_id}{UI.COLOR_OFF}",
                        r["version"],
                        f"{UI.UNDERLINE}{url}{UI.COLOR_OFF}",
                    ]
                )
            # If this is the specific project requested, or we requested all projects, show it stopped
            elif project_id or all_projects:
                project_rows.append(
                    [
                        f"{UI.WHITE}○{UI.COLOR_OFF} {p_id}",
                        r["version"],
                        f"{UI.DIM}Stopped{UI.COLOR_OFF}",
                    ]
                )
                # Mark active_projects as true if we show at least one row, to prevent error exit
                if all_projects:
                    active_projects = True

        if project_rows:
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


def run_list(handler):
    UI.heading("LDM Sandbox Projects")
    roots = handler.manager.find_dxp_roots()
    if not roots:
        UI.detail("No projects found.")
        return

    headers = ["Project", "Version", "Status", "URL"]
    rows = []

    for r in roots:
        path = r["path"]
        meta = handler.manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )
        version = r["version"]

        # Check container status
        containers_status = run_command(
            [
                "docker",
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
            status = "Stopped"
            status_color = UI.WHITE

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

        rows.append(
            [
                f"{UI.CYAN}{name}{UI.COLOR_OFF}{seeded_indicator}",
                version,
                f"{status_color}{status}{UI.COLOR_OFF}",
                f"{UI.UNDERLINE}{url}{UI.COLOR_OFF}",
            ]
        )

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
