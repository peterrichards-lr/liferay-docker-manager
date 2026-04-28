import time
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from ldm_core.ui import UI
from ldm_core.handlers.base import BaseHandler
from ldm_core.utils import get_actual_home, get_compose_cmd, open_browser


class RuntimeHandler(BaseHandler):
    """Specialized handler for container lifecycle and orchestration."""

    def __init__(self, args=None):
        super().__init__(args)

    def cmd_run(self, project_id=None, is_restart=False):
        """Main entry point for starting or updating a project stack."""
        total_start = time.time()
        project_id = (
            project_id or self.args.project or getattr(self.args, "project_flag", None)
        )
        if getattr(self.args, "select", False) and not project_id:
            if self.non_interactive:
                UI.die("Project selection is not supported in non-interactive mode.")
            selection = self.select_project_interactively(heading="Available Projects")
            if not selection:
                return
            project_id = selection["path"].name

        root = self.detect_project_path(project_id, for_init=True)
        if not root:
            UI.die("Project not found and no name provided to initialize.")

        project_id = root.name
        is_new_project = not any(
            (root / f).exists() for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]
        )
        paths = self.setup_paths(root)
        project_meta = self.read_meta(paths["root"])

        tag = self.args.tag or project_meta.get("tag")
        host_name = self.args.host_name or project_meta.get("host_name") or "localhost"
        db_type = getattr(self.args, "db", None) or project_meta.get("db_type")
        jvm_args = getattr(self.args, "jvm_args", None) or project_meta.get("jvm_args")
        port_val = project_meta.get("port", 8080)
        port = int(port_val) if port_val is not None else 8080

        # FAIL FAST: Pre-flight checks before expensive operations
        project_meta["root"] = str(root.resolve())
        project_meta["project_name"] = project_id

        paths = self.setup_paths(root)
        ssl_val = self._is_ssl_active(host_name, project_meta)

        if not getattr(self.args, "no_up", False):
            port = self._pre_flight_checks(
                host_name, port, ssl_enabled=ssl_val, meta=project_meta
            )

        project_meta["port"] = port

        # Performance Overrides
        no_vol_cache = (
            getattr(self.args, "no_vol_cache", False)
            or str(project_meta.get("no_vol_cache", "false")).lower() == "true"
        )
        no_jvm_verify = (
            getattr(self.args, "no_jvm_verify", False)
            or str(project_meta.get("no_jvm_verify", "false")).lower() == "true"
        )
        no_tld_skip = (
            getattr(self.args, "no_tld_skip", False)
            or str(project_meta.get("no_tld_skip", "false")).lower() == "true"
        )

        env_type = getattr(self.args, "env_type", None) or project_meta.get(
            "env_type", "dev"
        )
        cpu_limit = getattr(self.args, "cpu_limit", None) or project_meta.get(
            "cpu_limit"
        )
        mem_limit = getattr(self.args, "mem_limit", None) or project_meta.get(
            "mem_limit"
        )

        if not jvm_args:
            jvm_args = self.get_default_jvm_args()

        is_samples = getattr(self.args, "samples", False)
        if is_samples:
            from ldm_core.handlers.config import ConfigHandler

            config_handler = ConfigHandler(self.args)
            if host_name == "localhost":
                if self.non_interactive:
                    UI.die("--samples requires a custom hostname.")
                host_name = UI.ask("Enter project Virtual Hostname", "samples.local")
            if not tag:
                tag = config_handler.get_samples_tag()
            if not db_type:
                db_type = config_handler.get_samples_db_type()

        if not tag:
            tag_latest = getattr(self.args, "tag_latest", False)
            if self.non_interactive and not tag_latest:
                UI.die("No Liferay tag specified.")

            from ldm_core.constants import API_BASE_DXP
            from ldm_core.utils import discover_latest_tag

            rt = getattr(self.args, "release_type", None) or "lts"
            prefix = getattr(self.args, "tag_prefix", None)

            if not tag_latest:
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
                if self.verbose:
                    UI.info("Automatically discovering latest Liferay tag...")
                tag = discover_latest_tag(
                    API_BASE_DXP,
                    release_type=rt,
                    prefix_filter=prefix,
                    verbose=self.verbose,
                )
                if not tag:
                    UI.die(
                        "Failed to discover latest Liferay tag. Please specify one explicitly with -t."
                    )
                if self.verbose:
                    UI.success(f"Using tag: {tag}")

        external_snapshot = getattr(self.args, "snapshot", None)
        if external_snapshot:
            snap_path = Path(external_snapshot).resolve()
            snap_meta = self.read_meta(snap_path)
            tag = tag or snap_meta.get("tag")
            db_type = db_type or snap_meta.get("db_type")

        if is_new_project:
            if self._ensure_seeded(tag, db_type, paths):
                from ldm_core.constants import SEED_VERSION

                project_meta = self.read_meta(paths["root"])
                project_meta["seeded"] = "true"
                project_meta["seed_version"] = str(SEED_VERSION)
                self.write_meta(paths["root"], project_meta)
                is_new_project = False

        use_shared_search = (
            str(project_meta.get("use_shared_search", "false")).lower() == "true"
        )
        if not getattr(self.args, "sidecar", False) and not use_shared_search:
            use_shared_search = self.parse_version(tag) >= (2025, 1, 0)

        self.verify_runtime_environment(paths)

        if is_samples:
            from ldm_core.handlers.config import ConfigHandler

            ConfigHandler(self.args).sync_samples(paths)

        no_captcha = (
            getattr(self.args, "no_captcha", False)
            or str(project_meta.get("no_captcha", "false")).lower() == "true"
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
                "env_type": env_type,
                "cpu_limit": cpu_limit,
                "mem_limit": mem_limit,
            }
        )
        self.write_meta(paths["root"], project_meta)
        self.register_project(project_id, paths["root"], host_name=host_name)

        if is_samples or external_snapshot:
            from ldm_core.handlers.snapshot import SnapshotHandler

            self.sync_stack(paths, project_meta, no_up=True)
            self.run_command(
                get_compose_cmd() + ["up", "-d", "db"], cwd=str(paths["root"])
            )
            time.sleep(5)
            SnapshotHandler(self.args).cmd_restore(
                project_id,
                auto_index=1 if is_samples else None,
                backup_dir=external_snapshot if not is_samples else None,
            )

        self.sync_stack(
            paths,
            project_meta,
            follow=getattr(self.args, "follow", False),
            rebuild=getattr(self.args, "rebuild", False),
            no_up=getattr(self.args, "no_up", False),
            no_wait=getattr(self.args, "no_wait", False),
            total_start=total_start,
        )

    def cmd_reseed(self, project_id=None):
        """Triggers a re-bootstrap of the project from a fresh seed."""
        root = self.detect_project_path(project_id)
        if not root:
            return
        project_meta = self.read_meta(root)
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
            paths = self.setup_paths(root)
            if self._fetch_seed(tag, db_type, search_mode, paths):
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
                UI.info(
                    f"[{timestamp}] Still waiting for Liferay to become healthy... ({duration_str})"
                )

                # Proactive Log Monitoring: Look for ERRORS
                try:
                    logs = self.run_command(
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
                            UI.info(
                                f"Check full logs: {UI.WHITE}ldm logs -f {container_name}{UI.COLOR_OFF}"
                            )
                except Exception:
                    pass

                last_notified_time = elapsed

            status = self.run_command(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", container_name],
                check=False,
            )
            if status == "healthy":
                total_duration = (
                    time.time() - total_start
                    if total_start
                    else time.time() - start_time
                )
                duration_str = UI.format_duration(total_duration)
                UI.success(f"Liferay is ready! (Total time: {duration_str})")
                access_url = (
                    f"https://{host_name}"
                    if host_name != "localhost"
                    else "http://localhost:8080"
                )
                UI.info(
                    f"Access your instance at: {UI.CYAN}{UI.BOLD}{access_url}{UI.COLOR_OFF}"
                )

                UI.heading("Useful Commands")
                print(
                    f"  {UI.CYAN}ldm logs -f {container_name}{UI.COLOR_OFF}  Tail logs"
                )
                print(
                    f"  {UI.CYAN}ldm shell {container_name}{UI.COLOR_OFF}    Enter bash"
                )
                print(
                    f"  {UI.CYAN}ldm status {container_name}{UI.COLOR_OFF}   Check health"
                )
                print(
                    f"  {UI.CYAN}ldm stop {container_name}{UI.COLOR_OFF}     Stop stack"
                )
                print()

                if getattr(self.args, "browser", False):
                    UI.info(f"Launching browser: {access_url}/web/guest/home")
                    open_browser(f"{access_url}/web/guest/home")
                return True

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
            paths = self.setup_paths(paths)

        compose_base = get_compose_cmd()
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )

        liferay_env = ["LIFERAY_HOME=/opt/liferay"]
        project_id = project_meta.get("container_name")
        host_name = project_meta.get("host_name", "localhost")

        ssl_enabled = self._is_ssl_active(host_name, project_meta)
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

        self._ensure_network()
        if ssl_enabled or getattr(self.args, "search", False):
            if self.verbose:
                UI.info("Checking infrastructure stack (Traefik SSL Proxy)...")

            infra_start = time.time()
            resolved_ip = self.get_resolved_ip(host_name) or "127.0.0.1"
            self.setup_infrastructure(resolved_ip, ssl_port, use_ssl=ssl_enabled)

            if self.verbose:
                duration_str = UI.format_duration(time.time() - infra_start)
                UI.debug(f"Infrastructure setup took: {duration_str}")

            if ssl_enabled and not no_up:
                ssl_start = time.time()
                actual_home = get_actual_home()
                cert_dir = actual_home / "liferay-docker-certs"
                self.setup_ssl(cert_dir, host_name)
                if self.verbose:
                    duration_str = UI.format_duration(time.time() - ssl_start)
                    UI.debug(f"SSL certificate generation took: {duration_str}")

        from ldm_core.handlers.config import ConfigHandler

        config_handler = ConfigHandler(self.args)
        config_handler.sync_common_assets(
            paths, version=project_meta.get("tag"), project_meta=project_meta
        )
        config_handler.sync_logging(paths)

        self.write_docker_compose(paths, project_meta, liferay_env=liferay_env)

        UI.debug("Validating generated docker-compose.yml syntax...")
        self.run_command(
            get_compose_cmd() + ["config", "--quiet"],
            cwd=str(paths["root"]),
            check=True,
        )

        cmd = compose_base + ["up", "-d", "--remove-orphans"]
        if rebuild:
            cmd.append("--build")

        if show_summary:
            UI.heading(f"Stack Orchestration: {project_id}")
            UI.info(f"  + Liferay: {UI.CYAN}{project_meta.get('tag')}{UI.COLOR_OFF}")
            UI.info(
                f"  + DB Type: {UI.CYAN}{project_meta.get('db_type', 'hypersonic')}{UI.COLOR_OFF}"
            )

            search_mode = (
                "Shared (ES8)"
                if str(project_meta.get("use_shared_search", "true")).lower() == "true"
                else "Sidecar (Internal)"
            )
            UI.info(f"  + Search:  {UI.CYAN}{search_mode}{UI.COLOR_OFF}")

            UI.info(f"  + Host:    {UI.BOLD}{host_name}{UI.COLOR_OFF}")
            if ssl_enabled:
                UI.info(
                    f"  + SSL:     {UI.GREEN}Active (Port {ssl_port}){UI.COLOR_OFF}"
                )
                UI.info(
                    f"  + Port:    {UI.YELLOW}Disabled (SSL Proxy Active){UI.COLOR_OFF}"
                )
            else:
                UI.info(
                    f"  + Port:    {UI.CYAN}8080 -> {project_meta.get('port', 8080)}{UI.COLOR_OFF}"
                )

        if not no_up:
            if self.verbose and total_start:
                duration_str = UI.format_duration(time.time() - total_start)
                UI.debug(f"Time to orchestration start: {duration_str}")

            db_type = project_meta.get("db_type", "hypersonic")
            deps = []
            if db_type != "hypersonic":
                deps.append("db")
            if not use_shared_search:
                deps.append("search")

            if deps:
                UI.info(
                    f"Starting dependencies: {UI.CYAN}{', '.join(deps)}{UI.COLOR_OFF}..."
                )
                self.run_command(
                    compose_base + ["up", "-d"] + deps,
                    cwd=str(paths["root"]),
                    check=True,
                )

                for dep in deps:
                    UI.info(f"Waiting for {UI.CYAN}{dep}{UI.COLOR_OFF} to be ready...")
                    start_wait = time.time()
                    while time.time() - start_wait < 60:
                        status = self.get_container_status(f"{project_id}-{dep}-1")
                        if status == "healthy" or status == "running":
                            time.sleep(2)
                            break
                        time.sleep(2)

            UI.info(f"Starting {UI.BOLD}{project_id}{UI.COLOR_OFF} stack...")
            self.run_command(cmd, cwd=str(paths["root"]), capture_output=not follow)

            if follow:
                self.run_command(compose_base + ["logs", "-f"], cwd=str(paths["root"]))
                return True
            elif not no_wait:
                return self._wait_for_ready(project_meta, host_name, total_start)

        if no_wait:
            UI.success(f"Project '{project_id}' started in background.")
            return True

        return True

    def cmd_stop(self, project_id=None, service=None, all_projects=False):
        """Stops project containers."""
        targets = []
        if all_projects:
            targets = [r["path"] for r in self.find_dxp_roots()]
        else:
            root = self.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets:
            UI.info("No projects found to stop.")
            return

        compose_base = get_compose_cmd()
        for root in targets:
            UI.info(f"Stopping project: {root.name}...")
            cmd = compose_base + ["stop"]
            if service:
                cmd.append(service)
            self.run_command(cmd, capture_output=False, cwd=str(root))

    def cmd_restart(self, project_id=None, service=None, all_projects=False):
        """Restarts project containers."""
        targets = []
        if all_projects:
            targets = [r["path"] for r in self.find_dxp_roots()]
        else:
            root = self.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets:
            UI.info("No projects found to restart.")
            return

        compose_base = get_compose_cmd()
        for root in targets:
            UI.info(f"Restarting project: {root.name}...")
            cmd = compose_base + ["restart"]
            if service:
                cmd.append(service)
            self.run_command(cmd, capture_output=False, cwd=str(root))

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
            self.cmd_infra_down()

        targets = []
        if all_projects:
            targets = [r["path"] for r in self.find_dxp_roots()]
        else:
            root = self.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets and not infra:
            UI.info("No projects found to tear down.")
            return

        compose_base = get_compose_cmd()
        for root in targets:
            UI.warning(f"Tearing down stack: {root.name}")

            # DNS Cleanup (if requested)
            if clean_hosts:
                meta = self.read_meta(root)
                host = meta.get("host_name")
                if host and host != "localhost":
                    # Collect subdomains as well (from extensions)
                    unresolved, non_local = self.validate_project_dns(root)[1:]
                    # We remove the primary host and any unresolved subdomains
                    to_clean = [host] + unresolved
                    self._remove_hosts_entries(hostnames=to_clean)

            # DOWN always tears down the whole project to ensure networks and orphans are handled.
            # If the user wants to stop a specific service, they should use 'ldm stop [svc]'.
            cmd = compose_base + ["down", "-v", "--remove-orphans"]

            if (root / "docker-compose.yml").exists():
                self.run_command(cmd, capture_output=False, cwd=str(root))
            else:
                UI.debug(
                    f"No docker-compose.yml found in {root}. Skipping docker-compose down."
                )

            if delete:
                UI.warning(f"Permanently deleting project directory: {root.name}")
                self.unregister_project(root.name)
                self.safe_rmtree(root)

    def cmd_browser(self, project_id=None):
        """Opens the project's URL in the default browser."""
        from ldm_core.utils import open_browser

        root = self.detect_project_path(project_id)
        if not root:
            return
        meta = self.read_meta(root)
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
                {"path": r["path"], "meta": self.read_meta(r["path"])}
                for r in self.find_dxp_roots()
            ]
        else:
            root = self.detect_project_path(project_id)
            if root:
                meta = self.read_meta(root)
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
                self.setup_ssl(cert_dir, host_name)

        UI.success(
            "SSL renewal complete. Changes will be detected by Traefik automatically."
        )

    def cmd_reset(self, project_id=None, target="all"):
        """Wipes local state (data, logs, osgi/state) for a project."""
        root = self.detect_project_path(project_id)
        if not root:
            return

        UI.warning(f"Resetting {UI.BOLD}{root.name}{UI.COLOR_OFF} ({target})...")

        # 1. Stop containers if running
        meta = self.read_meta(root)
        c_name = meta.get("container_name") or root.name
        is_running = self.run_command(["docker", "ps", "-q", "-f", f"name=^{c_name}$"])
        if is_running:
            self.cmd_stop(root.name)

        # 2. Wipe directories
        paths = self.setup_paths(root)
        targets = []
        if target == "all":
            targets = ["data", "logs", "state"]
        else:
            targets = [target]

        for t in targets:
            path = paths.get(t)
            if path and path.exists():
                UI.info(f"  - Cleaning {t}...")
                shutil.rmtree(path)
                path.mkdir(parents=True, exist_ok=True)

        UI.success(f"Project {root.name} reset successful.")

    def cmd_gogo(self, project_id=None):
        """Connects to the OSGi Gogo shell."""
        root = self.detect_project_path(project_id)
        if not root:
            return
        meta = self.read_meta(root)
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
                self.run_command(["docker", "ps", "-q", "-f", f"name=^{container}$"])

            infra_compose = self.get_resource_path("infra-compose.yml")
            if not infra_compose:
                UI.die("Infrastructure compose file 'infra-compose.yml' not found.")

            cmd = get_compose_cmd() + [
                "-f",
                str(infra_compose),
                "logs",
            ]
            if follow:
                cmd.append("-f")

            if tail:
                cmd.extend(["--tail", str(tail)])

            env = self._get_infra_env()
            self.run_command(cmd, env=env, capture_output=not follow)
        else:
            targets = []
            if all_projects:
                targets = [r["path"] for r in self.find_dxp_roots()]
            else:
                root = self.detect_project_path(project_id)
                if root:
                    targets = [root]

            if not targets:
                UI.info("No running projects found to show logs.")
                return

            for root in targets:
                if self.verbose:
                    UI.debug(f"Processing logs for project: {root.name} in {root}")

                meta = self.read_meta(root)
                c_name = meta.get("container_name") or root.name

                res = self.run_command(
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

                        if self.run_command(
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

                cmd = get_compose_cmd() + ["logs"]
                if follow:
                    cmd.append("-f")

                if tail:
                    cmd.extend(["--tail", str(tail)])

                if service:
                    if isinstance(service, list):
                        cmd.extend(service)
                    else:
                        cmd.append(service)
                self.run_command(cmd, capture_output=not follow, cwd=str(root))
