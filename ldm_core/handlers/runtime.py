import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from ldm_core.ui import UI
from ldm_core.utils import get_actual_home, get_compose_cmd, open_browser


class RuntimeService:
    """Service for container lifecycle and orchestration."""

    def __init__(self, manager=None):
        self.manager = manager

    def cmd_run(self, project_id=None, is_restart=False):
        """Main entry point for starting or updating a project stack."""
        total_start = time.time()
        project_id = (
            project_id
            or self.manager.args.project
            or getattr(self.manager.args, "project_flag", None)
        )
        if getattr(self.manager.args, "select", False) and not project_id:
            if self.manager.non_interactive:
                UI.die("Project selection is not supported in non-interactive mode.")
            selection = self.manager.select_project_interactively(
                heading="Available Projects"
            )
            if not selection:
                return
            if selection.get("new"):
                project_id = None
            else:
                project_id = selection["path"].name

        root = self.manager.detect_project_path(project_id, for_init=True)
        if not root:
            if self.manager.non_interactive:
                UI.die("Project not found and no name provided to initialize.")

            default_name = f"ldm-{int(time.time())}"
            project_id = UI.ask("Enter a new project name to initialize", default_name)
            if not project_id:
                return
            root = self.manager.detect_project_path(project_id, for_init=True)
            if not root:
                UI.die("Failed to resolve project path.")

        project_id = root.name
        is_new_project = not any(
            (root / f).exists() for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]
        )
        paths = self.manager.setup_paths(root)
        project_meta = self.manager.read_meta(paths["root"])

        tag = self.manager.args.tag or project_meta.get("tag")
        host_name = (
            self.manager.args.host_name or project_meta.get("host_name") or "localhost"
        )
        db_type = getattr(self.manager.args, "db", None) or project_meta.get("db_type")
        jvm_args = getattr(self.manager.args, "jvm_args", None) or project_meta.get(
            "jvm_args"
        )
        port_val = getattr(self.manager.args, "port", None) or project_meta.get(
            "port", 8080
        )
        port = int(port_val) if port_val is not None else 8080

        # FAIL FAST: Pre-flight checks before expensive operations
        project_meta["root"] = str(root.resolve())
        project_meta["project_name"] = project_id

        paths = self.manager.setup_paths(root)
        ssl_val = self.manager.composer._is_ssl_active(host_name, project_meta)

        if not getattr(self.manager.args, "no_up", False):
            port = self.manager._pre_flight_checks(
                host_name, port, ssl_enabled=ssl_val, meta=project_meta
            )

        project_meta["port"] = port

        # Performance Overrides
        no_vol_cache = (
            getattr(self.manager.args, "no_vol_cache", False)
            or str(project_meta.get("no_vol_cache", "false")).lower() == "true"
        )
        no_jvm_verify = (
            getattr(self.manager.args, "no_jvm_verify", False)
            or str(project_meta.get("no_jvm_verify", "false")).lower() == "true"
        )
        no_tld_skip = (
            getattr(self.manager.args, "no_tld_skip", False)
            or str(project_meta.get("no_tld_skip", "false")).lower() == "true"
        )

        env_type = getattr(self.manager.args, "env_type", None) or project_meta.get(
            "env_type", "dev"
        )
        cpu_limit = getattr(self.manager.args, "cpu_limit", None) or project_meta.get(
            "cpu_limit"
        )
        mem_limit = getattr(self.manager.args, "mem_limit", None) or project_meta.get(
            "mem_limit"
        )

        if not jvm_args:
            jvm_args = self.manager.composer.get_default_jvm_args()

        is_samples = getattr(self.manager.args, "samples", False)
        if is_samples:
            config_handler = self.manager.config
            if host_name == "localhost":
                if self.manager.non_interactive:
                    UI.die("--samples requires a custom hostname.")
                host_name = UI.ask("Enter project Virtual Hostname", "samples.local")
            if not tag:
                tag = config_handler.get_samples_tag()
            if not db_type:
                db_type = config_handler.get_samples_db_type()

        if not tag:
            tag_latest = getattr(self.manager.args, "tag_latest", False)
            prefix = getattr(self.manager.args, "tag_prefix", None)

            can_discover = tag_latest or bool(prefix)
            if self.manager.non_interactive and not can_discover:
                UI.die("No Liferay tag specified.")

            from ldm_core.constants import API_BASE_DXP
            from ldm_core.utils import discover_latest_tag

            rt = getattr(self.manager.args, "release_type", None)
            if not rt:
                rt = "any" if prefix else "lts"

            if not can_discover:
                # Interactive Tag Discovery Sequence
                # Combined prompt for release type or specific version prefix
                ans = UI.ask("Release type (any|u|lts|qr) or prefix", "lts")

                if ans in ["any", "u", "lts", "qr"]:
                    rt = ans
                else:
                    prefix = ans
                    rt = "any"

                latest_tag = discover_latest_tag(
                    API_BASE_DXP, release_type=rt, prefix_filter=prefix, verbose=True
                )
                tag = UI.ask("Enter Liferay Tag", latest_tag)
            else:
                if self.manager.verbose:
                    UI.info("Automatically discovering latest Liferay tag...")
                tag = discover_latest_tag(
                    API_BASE_DXP,
                    release_type=rt,
                    prefix_filter=prefix,
                    verbose=self.manager.verbose,
                )
                if not tag:
                    UI.die(
                        "Failed to discover latest Liferay tag. Please specify one explicitly with -t."
                    )
                if self.manager.verbose:
                    UI.success(f"Using tag: {tag}")

        external_snapshot = getattr(self.manager.args, "snapshot", None)
        if external_snapshot:
            snap_path = Path(external_snapshot).resolve()
            snap_meta = self.manager.read_meta(snap_path)
            tag = tag or snap_meta.get("tag")
            db_type = db_type or snap_meta.get("db_type")

        if is_new_project and self.manager.assets._ensure_seeded(tag, db_type, paths):
            from ldm_core.constants import SEED_VERSION

            project_meta = self.manager.read_meta(paths["root"])
            project_meta["seeded"] = "true"
            project_meta["seed_version"] = str(SEED_VERSION)
            self.manager.write_meta(paths["root"], project_meta)
            is_new_project = False

        use_shared_search = (
            str(project_meta.get("use_shared_search", "false")).lower() == "true"
        )
        if not getattr(self.manager.args, "sidecar", False) and not use_shared_search:
            use_shared_search = self.manager.parse_version(tag) >= (2025, 1, 0)

        self.manager.verify_runtime_environment(paths)

        if is_samples:
            self.manager.config.sync_samples(paths)

        no_captcha = (
            getattr(self.manager.args, "no_captcha", False)
            or str(project_meta.get("no_captcha", "false")).lower() == "true"
        )
        fast_login = (
            getattr(self.manager.args, "fast_login", False)
            or str(project_meta.get("fast_login", "false")).lower() == "true"
        )

        project_meta.update(
            {
                "project_name": project_id,
                "tag": tag,
                "host_name": host_name,
                "container_name": project_id,
                "ssl": str(ssl_val).lower(),
                "db_type": db_type or "hypersonic",
                "port": port,
                "jvm_args": jvm_args,
                "use_shared_search": str(use_shared_search).lower(),
                "no_vol_cache": str(no_vol_cache).lower(),
                "no_jvm_verify": str(no_jvm_verify).lower(),
                "no_tld_skip": str(no_tld_skip).lower(),
                "no_captcha": str(no_captcha).lower(),
                "fast_login": str(fast_login).lower(),
                "env_type": env_type,
                "cpu_limit": cpu_limit,
                "mem_limit": mem_limit,
            }
        )
        self.manager.write_meta(paths["root"], project_meta)
        self.manager.register_project(project_id, paths["root"], host_name=host_name)

        if is_samples or external_snapshot:
            self.sync_stack(paths, project_meta, no_up=True, show_summary=False)
            self.manager.run_command(
                [*get_compose_cmd(), "up", "-d", "db"], cwd=str(paths["root"])
            )
            time.sleep(5)
            self.manager.snapshot.cmd_restore(
                project_id,
                auto_index=1 if is_samples else None,
                backup_dir=external_snapshot if not is_samples else None,
            )

        self.sync_stack(
            paths,
            project_meta,
            follow=getattr(self.manager.args, "follow", False),
            rebuild=getattr(self.manager.args, "rebuild", False),
            no_up=getattr(self.manager.args, "no_up", False),
            no_wait=getattr(self.manager.args, "no_wait", False),
            total_start=total_start,
        )

    def cmd_reseed(self, project_id=None):
        """Triggers a re-bootstrap of the project from a fresh seed."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        project_meta = self.manager.read_meta(root)
        tag = project_meta.get("tag")
        db_type = project_meta.get("db_type")
        use_shared = (
            str(project_meta.get("use_shared_search", "false")).lower() == "true"
        )
        search_mode = "shared" if use_shared else "sidecar"

        if not tag:
            UI.die("Project missing tag metadata. Cannot reseed.")

        if UI.confirm(
            f"Reseed {root.name} from {tag} ({db_type}/{search_mode})? ALL LOCAL DATA WILL BE LOST.",
            "N",
        ):
            self.cmd_reset(root.name, target="all")
            paths = self.manager.setup_paths(root)
            if self.manager.assets._fetch_seed(tag, db_type, search_mode, paths):
                UI.success("Reseed complete.")
            else:
                UI.error("Reseed failed.")

    def _wait_for_ready(self, project_meta, host_name, total_start=None):
        """Wait for Liferay to become healthy and provide access information."""
        container_name = project_meta.get("container_name")
        start_time = time.time()
        UI.info(f"Waiting for Liferay to become healthy ({container_name})...")

        last_notified_time = 0
        while time.time() - start_time < 600:  # 10 minute timeout
            elapsed = time.time() - start_time
            # Notify every 30 seconds (Robust timestamp check)
            # Move notification BEFORE blocking call for guaranteed feedback
            if elapsed - last_notified_time >= 30:
                timestamp = datetime.now().strftime("%H:%M:%S")
                duration_str = UI.format_duration(elapsed)
                UI.detail(
                    f"[{timestamp}] Still waiting for Liferay to become healthy... ({duration_str})"
                )

                # Proactive Log Monitoring: Look for ERRORS
                try:
                    logs = self.manager.run_command(
                        ["docker", "logs", "--tail", "100", container_name],
                        check=False,
                        capture_output=True,
                    )
                    if logs:
                        error_lines = [
                            line.strip()
                            for line in logs.splitlines()
                            if "ERROR" in line.upper()
                            or "FATAL" in line.upper()
                            or "CRITICAL" in line.upper()
                        ]
                        if error_lines:
                            UI.warning(
                                f"LDM detected {len(error_lines)} error(s) in the logs."
                            )
                            # Display the most recent unique error
                            last_unique_error = list(dict.fromkeys(error_lines))[-1]
                            UI.info(
                                f"Recent log error: {UI.YELLOW}{last_unique_error[:120]}...{UI.COLOR_OFF}"
                            )

                            # --- Auto-Thaw & Hints Win ---
                            if (
                                "ClusterBlockException" in last_unique_error
                                or "index.blocks.read_only" in last_unique_error
                            ):
                                UI.warning(
                                    "Detected Elasticsearch disk pressure blocking Liferay startup."
                                )
                                if self.manager.infra.thaw_elasticsearch():
                                    UI.success(
                                        "Auto-Thaw successful. Liferay should now proceed."
                                    )
                                else:
                                    UI.info(
                                        f"💡 {UI.CYAN}Hint:{UI.COLOR_OFF} Your disk is likely full. Run '{UI.WHITE}ldm prune --seeds --samples{UI.COLOR_OFF}' to free space."
                                    )

                            UI.info(
                                f"Check full logs: {UI.WHITE}ldm logs -f {container_name}{UI.COLOR_OFF}"
                            )
                except Exception:
                    pass

                last_notified_time = elapsed

            status = self.manager.run_command(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", container_name],
                check=False,
            )
            if status == "healthy":
                ts = getattr(self.manager.args, "total_start", None)
                duration_total = (
                    time.time() - float(ts) if ts else time.time() - start_time
                )

                duration_str = UI.format_duration(duration_total)

                UI.success(f"Liferay is ready! (Total time: {duration_str})")
                access_url = (
                    f"https://{host_name}"
                    if host_name != "localhost"
                    else "http://localhost:8080"
                )
                UI.info(
                    f"Access your instance at: {UI.CYAN}{UI.BOLD}{access_url}{UI.COLOR_OFF}"
                )

                UI.detail("=== Useful Commands ===")
                UI.detail(
                    f"  {UI.CYAN}ldm logs -f {container_name}{UI.COLOR_OFF}  Tail logs"
                )
                UI.detail(
                    f"  {UI.CYAN}ldm shell {container_name}{UI.COLOR_OFF}    Enter bash"
                )
                UI.detail(
                    f"  {UI.CYAN}ldm status {container_name}{UI.COLOR_OFF}   Check health"
                )
                UI.detail(
                    f"  {UI.CYAN}ldm stop {container_name}{UI.COLOR_OFF}     Stop stack"
                )
                UI.detail("")

                if getattr(self.manager.args, "browser", False):
                    UI.info(f"Launching browser: {access_url}/web/guest/home")
                    open_browser(f"{access_url}/web/guest/home")
                return True

            # Fail fast if container exited
            container_state = self.manager.get_container_status(container_name)
            if container_state == "exited":
                UI.error(f"Liferay container '{container_name}' exited unexpectedly.")
                return False

            time.sleep(5)  # Shorter sleep for more responsive status checks

        UI.error("\nTimed out waiting for Liferay to become healthy.")
        return False

    def sync_stack(
        self,
        paths,
        project_meta,
        follow=False,
        rebuild=False,
        no_up=False,
        no_wait=False,
        show_summary=True,
        total_start=None,
    ):
        """Orchestrates stack configuration and startup."""
        # Ensure paths is a dictionary for subscripting
        if not isinstance(paths, dict):
            paths = self.manager.setup_paths(paths)

        compose_base = get_compose_cmd()
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )

        liferay_env = ["LIFERAY_HOME=/opt/liferay"]
        project_id = project_meta.get("container_name")
        host_name = project_meta.get("host_name", "localhost")

        ssl_enabled = self.manager.composer._is_ssl_active(host_name, project_meta)
        ssl_port_val = project_meta.get("ssl_port", 443)
        ssl_port = int(ssl_port_val) if ssl_port_val is not None else 443
        use_shared_search = (
            str(project_meta.get("use_shared_search", "true")).lower() == "true"
        )

        if host_name != "localhost":
            liferay_env.extend(
                [
                    "LIFERAY_WEB_PERIOD_SERVER_PERIOD_DISPLAY_PERIOD_NODE_PERIOD_NAME=true",
                    "LIFERAY_REDIRECT_PERIOD_URL_PERIOD_IPS_PERIOD_ALLOWED=127.0.0.1,0.0.0.0/0",
                ]
            )

        self.manager.infra._ensure_network()
        if ssl_enabled or getattr(self.manager.args, "search", False):
            infra_start = time.time()
            resolved_ip = self.manager.get_resolved_ip(host_name) or "127.0.0.1"
            self.manager.infra.setup_infrastructure(
                resolved_ip,
                ssl_port,
                use_ssl=ssl_enabled,
                quiet=not show_summary,
                use_shared_search=use_shared_search,
            )

            if self.manager.verbose:
                duration_str = UI.format_duration(time.time() - infra_start)
                UI.debug(f"Infrastructure setup took: {duration_str}")

            if ssl_enabled and not no_up:
                ssl_start = time.time()
                actual_home = get_actual_home()
                cert_dir = actual_home / "liferay-docker-certs"
                self.manager.infra.setup_ssl(cert_dir, host_name)
                if self.manager.verbose:
                    duration_str = UI.format_duration(time.time() - ssl_start)
                    UI.debug(f"SSL certificate generation took: {duration_str}")

        config_handler = self.manager.config
        config_handler.sync_common_assets(
            paths, version=project_meta.get("tag"), project_meta=project_meta
        )
        config_handler.sync_logging(paths)

        self.manager.composer.write_docker_compose(
            paths, project_meta, liferay_env=liferay_env
        )

        UI.debug("Validating generated docker-compose.yml syntax...")
        self.manager.run_command(
            [*get_compose_cmd(), "config", "--quiet"],
            cwd=str(paths["root"]),
            check=True,
        )

        cmd = [*compose_base, "up", "-d", "--remove-orphans"]
        if rebuild:
            cmd.append("--build")

        if show_summary:
            tag_val = project_meta.get("tag")
            db_val = project_meta.get("db_type", "hypersonic")
            port_val = project_meta.get("port", 8080)

            UI.info(
                f"🚀 Starting {project_id} stack ({tag_val}, {db_val}, {host_name}:{port_val})..."
            )

            UI.detail(f"=== Stack Configuration: {project_id} ===")
            UI.detail(f"  + Liferay: {UI.CYAN}{tag_val}{UI.COLOR_OFF}")
            UI.detail(f"  + DB Type: {UI.CYAN}{db_val}{UI.COLOR_OFF}")

            search_mode = (
                "Shared (ES8)"
                if str(project_meta.get("use_shared_search", "true")).lower() == "true"
                else "Sidecar (Internal)"
            )
            UI.detail(f"  + Search:  {UI.CYAN}{search_mode}{UI.COLOR_OFF}")

            UI.detail(f"  + Host:    {UI.BOLD}{host_name}{UI.COLOR_OFF}")
            if ssl_enabled:
                UI.detail(
                    f"  + SSL:     {UI.GREEN}Active (Port {ssl_port}){UI.COLOR_OFF}"
                )
                UI.detail(
                    f"  + Port:    {UI.YELLOW}Disabled (SSL Proxy Active){UI.COLOR_OFF}"
                )
            else:
                UI.detail(f"  + Port:    {UI.CYAN}8080 -> {port_val}{UI.COLOR_OFF}")

        if not no_up:
            if self.manager.verbose and total_start:
                duration_str = UI.format_duration(time.time() - total_start)
                UI.debug(f"Time to orchestration start: {duration_str}")

            db_type = project_meta.get("db_type", "hypersonic")
            deps = []
            if db_type != "hypersonic":
                deps.append("db")

            if deps:
                UI.detail(
                    f"Starting dependencies: {UI.CYAN}{', '.join(deps)}{UI.COLOR_OFF}..."
                )
                self.manager.run_command(
                    [*compose_base, "up", "-d", *deps],
                    cwd=str(paths["root"]),
                    check=True,
                )

                for dep in deps:
                    UI.detail(
                        f"Waiting for {UI.CYAN}{dep}{UI.COLOR_OFF} to be ready..."
                    )
                    start_wait = time.time()
                    while time.time() - start_wait < 60:
                        status = self.manager.get_container_status(
                            f"{project_id}-{dep}-1"
                        )
                        if status in {"healthy", "running"}:
                            time.sleep(2)
                            break
                        if status == "exited":
                            UI.error(f"Dependency '{dep}' exited unexpectedly.")
                            return False
                        time.sleep(2)

            UI.info(f"Starting {UI.BOLD}{project_id}{UI.COLOR_OFF} stack...")
            self.manager.run_command(
                cmd, cwd=str(paths["root"]), capture_output=not follow
            )

            if follow:
                self.manager.run_command(
                    [*compose_base, "logs", "-f"], cwd=str(paths["root"])
                )
                return True
            if not no_wait:
                return self._wait_for_ready(project_meta, host_name, total_start)

        if no_wait:
            UI.success(f"Project '{project_id}' started in background.")
            return True

        return True

    def cmd_stop(self, project_id=None, service=None, all_projects=False):
        """Stops project containers."""
        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets:
            UI.info("No projects found to stop.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.info(f"Stopping project: {root.name}...")
            cmd = [*compose_base, "stop"]
            if service:
                cmd.append(service)
            self.manager.run_command(cmd, capture_output=capture, cwd=str(root))

    def cmd_restart(self, project_id=None, service=None, all_projects=False):
        """Restarts project containers."""
        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets:
            UI.info("No projects found to restart.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.info(f"Restarting project: {root.name}...")
            cmd = [*compose_base, "restart"]
            if service:
                cmd.append(service)
            self.manager.run_command(cmd, capture_output=capture, cwd=str(root))

    def cmd_down(
        self,
        project_id=None,
        service=None,
        all_projects=False,
        delete=False,
        infra=False,
        clean_hosts=False,
    ):
        """Tears down project containers and volumes."""
        if infra:
            self.manager.infra.cmd_infra_down()

        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets and not infra:
            UI.info("No projects found to tear down.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.warning(f"Tearing down stack: {root.name}")

            # DNS Cleanup (if requested)
            if clean_hosts:
                meta = self.manager.read_meta(root)
                host = meta.get("host_name")
                if host and host != "localhost":
                    # Collect subdomains as well (from extensions)
                    unresolved, _non_local = self.manager.validate_project_dns(root)[1:]
                    # We remove the primary host and any unresolved subdomains
                    to_clean = [host, *unresolved]
                    self.manager._remove_hosts_entries(hostnames=to_clean)

            # DOWN always tears down the whole project to ensure networks and orphans are handled.
            # If the user wants to stop a specific service, they should use 'ldm stop [svc]'.
            cmd = [*compose_base, "down", "-v", "--remove-orphans"]

            if (root / "docker-compose.yml").exists():
                self.manager.run_command(cmd, capture_output=capture, cwd=str(root))
            else:
                UI.debug(
                    f"No docker-compose.yml found in {root}. Skipping docker-compose down."
                )

            if delete:
                UI.warning(f"Permanently deleting project directory: {root.name}")
                self.manager.unregister_project(root.name)
                self.manager.safe_rmtree(root)

    def cmd_browser(self, project_id=None):
        """Opens the project's URL in the default browser."""
        from ldm_core.utils import open_browser

        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        meta = self.manager.read_meta(root)
        host_name = meta.get("host_name", "localhost")
        ssl_enabled = str(meta.get("ssl", "false")).lower() == "true"
        port = meta.get("port", 8080)

        protocol = "https" if ssl_enabled else "http"
        url = f"{protocol}://{host_name}"
        if not ssl_enabled and port != 80:
            url += f":{port}"

        UI.info(f"Opening browser: {UI.CYAN}{url}{UI.COLOR_OFF}")
        open_browser(url)

    def cmd_renew_ssl(self, project_id=None, all_projects=False):
        """Forces renewal of SSL certificates for projects."""
        targets = []
        if all_projects:
            targets = [
                {"path": r["path"], "meta": self.manager.read_meta(r["path"])}
                for r in self.manager.find_dxp_roots()
            ]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                meta = self.manager.read_meta(root)
                targets.append({"path": root, "meta": meta})

        if not targets:
            UI.info("No projects found for SSL renewal.")
            return

        actual_home = get_actual_home()
        cert_dir = actual_home / "liferay-docker-certs"

        for target in targets:
            host_name = target["meta"].get("host_name")
            if host_name and host_name != "localhost":
                UI.info(f"Renewing SSL for {UI.CYAN}{host_name}{UI.COLOR_OFF}...")
                # Delete existing certs to force renewal
                for f in [f"{host_name}.pem", f"{host_name}-key.pem"]:
                    if (cert_dir / f).exists():
                        (cert_dir / f).unlink()
                self.manager.infra.setup_ssl(cert_dir, host_name)

        UI.success(
            "SSL renewal complete. Changes will be detected by Traefik automatically."
        )

    def cmd_reset(self, project_id=None, target="all"):
        """Wipes local state (data, logs, osgi/state) for a project."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return

        UI.warning(f"Resetting {UI.BOLD}{root.name}{UI.COLOR_OFF} ({target})...")

        # 1. Stop containers if running
        meta = self.manager.read_meta(root)
        c_name = meta.get("container_name") or root.name
        from ldm_core.docker_service import DockerService

        is_running = DockerService.is_running(c_name)
        if is_running:
            self.cmd_stop(root.name)

        # 2. Wipe directories
        paths = self.manager.setup_paths(root)
        targets = []
        targets = ["data", "logs", "state"] if target == "all" else [target]

        for t in targets:
            path = paths.get(t)
            if path and path.exists():
                UI.info(f"  - Cleaning {t}...")
                shutil.rmtree(path)
                path.mkdir(parents=True, exist_ok=True)

        UI.success(f"Project {root.name} reset successful.")

    def cmd_gogo(self, project_id=None):
        """Connects to the OSGi Gogo shell."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        meta = self.manager.read_meta(root)
        port = meta.get("gogo_port")

        if not port or port == "None":
            UI.die(
                "Gogo shell is not exposed. Run 'ldm run --gogo-port <port>' to enable it."
            )

        UI.info(f"Connecting to Gogo shell on localhost:{port}...")
        try:
            subprocess.run(["telnet", "localhost", str(port)])
        except FileNotFoundError:
            UI.error("telnet not found. Run: telnet localhost " + str(port))
        except KeyboardInterrupt:
            pass

    def cmd_logs(
        self,
        project_id=None,
        service=None,
        all_projects=False,
        infra=False,
        follow=False,
        no_wait=False,
        tail="100",
        timestamps=False,
        since=None,
        until=None,
    ):
        """Shows logs for a project or global infrastructure."""
        if infra:
            UI.info("Showing infrastructure logs...")
            containers = []
            if not service or "proxy" in service:
                containers.append("liferay-proxy-global")
            if not service or "es" in service:
                containers.append("liferay-search-global")

            for container in containers:
                self.manager.run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{container}$"]
                )

            infra_compose = self.manager.get_resource_path("infra-compose.yml")
            if not infra_compose:
                UI.die("Infrastructure compose file 'infra-compose.yml' not found.")

            cmd = [*get_compose_cmd(), "-f", str(infra_compose), "logs"]
            if follow:
                cmd.append("-f")

            if tail:
                cmd.extend(["--tail", str(tail)])

            if timestamps:
                cmd.append("-t")

            if since:
                cmd.extend(["--since", str(since)])

            if until:
                cmd.extend(["--until", str(until)])

            env = self.manager.infra._get_infra_env()
            self.manager.run_command(cmd, env=env, capture_output=False)
        else:
            targets = []
            if all_projects:
                targets = [r["path"] for r in self.manager.find_dxp_roots()]
            else:
                root = self.manager.detect_project_path(project_id)
                if root:
                    targets = [root]

            if not targets:
                UI.info("No running projects found to show logs.")
                return

            for root in targets:
                if self.manager.verbose:
                    UI.debug(f"Processing logs for project: {root.name} in {root}")

                meta = self.manager.read_meta(root)
                c_name = meta.get("container_name") or root.name

                res = self.manager.run_command(
                    ["docker", "ps", "-a", "-q", "-f", f"name=^{c_name}$"],
                    check=False,
                )

                if not res:
                    if no_wait:
                        UI.error(f"Container '{c_name}' does not exist. Skipping.")
                        continue

                    UI.info(f"Waiting for container {UI.CYAN}{c_name}{UI.COLOR_OFF}...")
                    start_wait = time.time()
                    found = False
                    while time.time() - start_wait < 60:
                        elapsed = int(time.time() - start_wait)
                        if elapsed > 0 and elapsed % 10 == 0:
                            UI.info(f"  ... still waiting for '{c_name}' ({elapsed}s)")

                        if self.manager.run_command(
                            ["docker", "ps", "-a", "-q", "-f", f"name=^{c_name}$"]
                        ):
                            found = True
                            break
                        time.sleep(2)

                    if not found:
                        UI.error(f"Container '{c_name}' did not appear within 60s.")
                        continue

                if follow:
                    log_dir = root / "logs"
                    if not log_dir.exists():
                        if no_wait:
                            UI.error(
                                f"Logs directory missing in {root.name}. Skipping."
                            )
                            continue

                        UI.info(f"Waiting for logs directory in {root.name}...")
                        start_wait = time.time()
                        while not log_dir.exists() and time.time() - start_wait < 30:
                            time.sleep(1)

                cmd = [*get_compose_cmd(), "logs"]
                if follow:
                    cmd.append("-f")

                if tail:
                    cmd.extend(["--tail", str(tail)])

                if timestamps:
                    cmd.append("-t")

                if since:
                    cmd.extend(["--since", str(since)])

                if until:
                    cmd.extend(["--until", str(until)])

                if service:
                    if isinstance(service, list):
                        cmd.extend(service)
                    else:
                        cmd.append(service)
                self.manager.run_command(cmd, capture_output=False, cwd=str(root))

    def cmd_deploy(self, project_id=None, service=None):
        """Deploys a project or specific service."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        paths, meta = self.manager.setup_paths(root), self.manager.read_meta(root)
        if service:
            UI.info(f"Deploying service '{service}'...")
            self.manager.run_command(
                [*get_compose_cmd(), "up", "-d", service],
                capture_output=False,
                cwd=str(root),
            )
        else:
            self.sync_stack(
                paths, meta, rebuild=getattr(self.manager.args, "rebuild", False)
            )

    def cmd_shell(self, project_id=None, service="liferay"):
        """Enters a project container via bash."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        service_name = service or "liferay"
        meta = self.manager.read_meta(root)
        container_prefix = meta.get("container_name")

        target_container = f"{container_prefix}-{service_name}"
        if service_name == "liferay":
            target_container = container_prefix

        UI.info(f"Entering container: {target_container}")
        try:
            subprocess.run(["docker", "exec", "-it", target_container, "/bin/bash"])
        except KeyboardInterrupt:
            pass

    def cmd_scale(self, project_id, scale_args):
        """Scales project services."""
        project_path = self.manager.detect_project_path(project_id)
        if not project_path:
            UI.die("Project not found.")

        meta = self.manager.read_meta(project_path)

        for arg in scale_args:
            if "=" not in arg:
                UI.error(f"Invalid scale argument: {arg}. Expected service=number")
                continue
            service, count = arg.split("=", 1)
            if not count.isdigit():
                UI.error(f"Invalid scale count for {service}: {count}")
                continue
            meta[f"scale_{service}"] = count

        self.manager.write_meta(project_path, meta)
        UI.success(f"Updated scale factors for project {project_path.name}")

        # Trigger regeneration and restart
        self.cmd_run(project_id)

    def cmd_migrate_search(self, project_id=None):
        """Migrates a project from Sidecar to Global Elasticsearch."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return

        p_id = root.name
        paths = self.manager.setup_paths(p_id)

        # 1. Ensure Liferay is NOT running
        is_running = self.manager.run_command(
            ["docker", "ps", "-q", "-f", f"name=^{p_id}$"], check=False
        )
        if is_running:
            UI.die(
                f"Project '{p_id}' is currently running. Please stop it first with: ldm stop {p_id}"
            )

        UI.heading(f"Migrating '{p_id}' to Global Search")

        # 2. Check if Global Search is running
        search_running = self.manager.run_command(
            ["docker", "ps", "-q", "-f", "name=^liferay-search-global$"], check=False
        )
        if not search_running:
            if (
                UI.ask(
                    "Global Search container is not running. Start it now?", "Y"
                ).upper()
                == "Y"
            ):
                self.manager.infra.setup_global_search()
            else:
                UI.die("Migration aborted. Global Search is required.")

        # 3. Clean up internal indices
        data_dir = paths["data"]
        indices_found = False
        for es_dir in ["elasticsearch7", "elasticsearch8"]:
            target = data_dir / es_dir
            if target.exists():
                UI.info(f"Removing internal index directory: {target}")
                shutil.rmtree(target)
                indices_found = True

        if not indices_found:
            UI.info("No internal sidecar indices found. (Already clean?)")

        # 4. Sync configuration
        UI.info("Applying Global Search configurations...")
        # We force use_shared_search=True in meta
        project_meta = self.manager.read_meta(root)
        project_meta["use_shared_search"] = "true"
        self.manager.write_meta(root, project_meta)

        # sync_common_assets will now find the global search running and copy the configs
        self.manager.config.sync_common_assets(paths)

        UI.success(
            f"Migration complete! Project '{p_id}' is now configured for Global Search."
        )

        if not self.manager.non_interactive:
            if UI.ask("Restart project now?", "Y").upper() == "Y":
                self.cmd_run(project_id)
