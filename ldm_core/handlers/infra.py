import json
import os
import shutil
import time

from ldm_core.ui import UI
from ldm_core.utils import (
    get_actual_home,
    get_docker_socket_path,
)

# LDM-#1413: these run on paths a user waits on -- `ldm run` and `ldm restore`
# both provision global services through here -- and every one can contact a
# daemon that may never answer. Unbounded, a stalled socket is indistinguishable
# from LDM being slow; that ambiguity cost 84 minutes three times over in
# LDM-#1410 before anyone realised the restore was not merely taking a while.
#
# Sized to the operation rather than uniformly: creating a service may pull a
# multi-gigabyte image, while a readiness probe that has not answered in 30
# seconds is not going to.
_INFRA_CREATE_TIMEOUT = 600  # `docker run` for a global service -- may pull
_INFRA_PROBE_TIMEOUT = 30  # readiness probes and inspection
_INFRA_LIFECYCLE_TIMEOUT = 120  # start/stop/rm/network operations


class InfraService:
    """Service for global infrastructure management (Traefik, Global Search)."""

    def __init__(self, manager=None):
        self.manager = manager
        self.target: str | None = None

    def cmd_infra_setup(self):
        """Sets up the global infrastructure (Traefik, Search)."""
        import sys

        if not self.manager.check_docker():
            UI.die("Docker is not running.")
        resolved_ip = (
            "0.0.0.0"  # nosec B104
            if sys.platform == "darwin"
            else self.manager.get_resolved_ip("localhost")
        )

        project_path = self.manager.detect_project_path(None)
        meta = self.manager.read_meta(project_path) or {} if project_path else {}

        from ldm_core.utils import resolve_infrastructure_mode

        db_mode = resolve_infrastructure_mode(
            "database_mode",
            meta,
            self.manager.defaults,
            getattr(self.manager.args, "database_mode", None),
        )

        use_shared_db = db_mode == "shared"

        ssl_port = getattr(self.manager.args, "ssl_port", None)
        if ssl_port is None:
            ssl_port = int(os.getenv("LDM_SSL_PORT", "443"))

        force_recreate = getattr(self.manager.args, "force_recreate", False)

        self.setup_infrastructure(
            resolved_ip,
            ssl_port,
            use_ssl=True,
            use_shared_db=use_shared_db,
            force_recreate=force_recreate,
            db_type=meta.get("db_type") or getattr(self.manager.args, "db", None),
        )
        UI.success("Infrastructure setup complete.")

    def get_proxy_ports(self, target_name: str | None = None):
        """Returns the active mapped host ports for liferay-proxy-global."""
        ports = {"http": 80, "https": 443, "admin": 18080}
        target = target_name or getattr(self.manager, "target", None)
        try:
            # Inspect the running proxy container
            from ldm_core.docker_service import DockerService

            inspect_raw = DockerService.inspect(
                "liferay-proxy-global", target_name=target
            )
            if inspect_raw:
                settings = (
                    inspect_raw.get("NetworkSettings", {}).get("Ports", {})
                    if isinstance(inspect_raw, dict)
                    else json.loads(inspect_raw)
                )
                # settings is a dict like: {"443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "443"}], ...}
                if isinstance(settings, dict):
                    if settings.get("80/tcp"):
                        ports["http"] = int(settings["80/tcp"][0]["HostPort"])
                    if settings.get("443/tcp"):
                        ports["https"] = int(settings["443/tcp"][0]["HostPort"])
                    if settings.get("8080/tcp"):
                        ports["admin"] = int(settings["8080/tcp"][0]["HostPort"])
        except Exception:
            pass
        return ports

    def setup_infrastructure(  # noqa: C901, PLR0912, PLR0915
        self,
        resolved_ip,
        ssl_port,
        use_ssl=True,
        quiet=False,
        use_shared_search=True,
        use_shared_db=False,
        force_recreate=False,
        db_type=None,
    ):
        """Initializes global Traefik proxy and search services.

        LDM-#1361: `db_type` selects which engine's global database
        container to provision. `None` means PostgreSQL, preserving the
        pre-#1361 behaviour for callers that do not know the engine.
        """
        self._ensure_network(self.target)
        # Orchestrated Global Search (ES8)
        if getattr(self.manager.args, "search", False) and use_shared_search:
            self.setup_global_search()

        if use_shared_db:
            self.setup_global_database(db_type=db_type)

        if not use_ssl:
            return 443

        # Docker bridge proxy check (Traefik needs to talk to Docker socket securely)
        self._ensure_docker_proxy()

        if not quiet:
            UI.detail("Checking infrastructure stack (Traefik SSL Proxy)...")
        infra_compose = self.manager.get_resource_path("infra-compose.yml")
        if not infra_compose:
            UI.die(
                "Infrastructure compose file 'infra-compose.yml' not found in resources."
            )

        from ldm_core.docker_service import DockerService

        target_name = getattr(self.manager, "target", None)
        is_proxy_running = DockerService.is_running(
            "liferay-proxy-global", target_name=target_name
        )

        http_port = int(os.getenv("LDM_HTTP_PORT", "80"))
        ssl_port = int(ssl_port)
        admin_port = int(os.getenv("LDM_ADMIN_PORT", "18080"))

        # Safety Check: Warn or abort if recreate/reconfigure would disrupt active running projects
        if is_proxy_running and force_recreate:
            # NOTE: Best-effort check. There is a small TOCTOU window between this scan and stopping the proxy.
            running_projects = []
            try:
                roots = self.manager.find_dxp_roots()
                for r in roots:
                    path = r["path"]
                    meta = self.manager.read_meta(path)
                    name = (
                        meta.get("liferay_container_name")
                        or meta.get("container_name")
                        or path.name.replace(".", "-")
                    )
                    target_node = meta.get("target", "local")
                    docker_prefix = DockerService.get_docker_cmd_prefix(target_node)

                    from ldm_core.utils import run_command

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
                    if containers_status and "running" in containers_status:
                        running_projects.append((name, target_node))
            except Exception as e:
                import sys

                print(
                    f"\n{UI.BRED}[!] ERROR: Could not verify active running projects: {e}{UI.COLOR_OFF}",
                    file=sys.stderr,
                )
                if not getattr(self.manager.args, "force", False):
                    UI.die(
                        "Aborted due to verification failure. Use --force to proceed anyway."
                    )

            if running_projects:
                print(
                    f"\n{UI.BRED}[!] WARNING: Active LDM projects are currently running on this machine:{UI.COLOR_OFF}"
                )
                for p_name, node in running_projects:
                    print(f"  - {UI.CYAN}{p_name}{UI.COLOR_OFF} (on node: {node})")
                print(
                    f"\n{UI.YELLOW}Recreating or reconfiguring the global SSL proxy will disrupt connectivity for these projects.{UI.COLOR_OFF}"
                )
                if not getattr(self.manager.args, "force", False):
                    UI.die("Aborted. Use --force to proceed anyway.")

            # Stop and remove existing Traefik to release port bindings cleanly before checks
            UI.detail("Stopping existing Traefik SSL proxy to release port bindings...")
            DockerService.stop("liferay-proxy-global", target_name=target_name)
            DockerService.rm(
                "liferay-proxy-global", force=True, target_name=target_name
            )
            is_proxy_running = False

        if is_proxy_running and not force_recreate:
            # Use the currently running ports to keep compose state identical
            ports = self.get_proxy_ports()
            http_port = ports["http"]
            ssl_port = ports["https"]
            admin_port = ports["admin"]
        else:
            allocated_ports = []

            # Check HTTP port
            if not self.manager.check_port("0.0.0.0", http_port):  # nosec B104
                orig_http = http_port
                http_port = self.manager.find_available_port("0.0.0.0", http_port)  # nosec B104
                UI.warning(
                    f"Port conflict detected! Global HTTP proxy port {orig_http} is in use on the host. Using {http_port} instead."
                )
            allocated_ports.append(http_port)

            # Check HTTPS port
            if ssl_port in allocated_ports or not self.manager.check_port(
                "0.0.0.0",
                ssl_port,  # nosec B104
            ):
                orig_ssl = ssl_port
                ssl_port = self.manager.find_available_port(
                    "0.0.0.0",
                    ssl_port,
                    exclude=allocated_ports,  # nosec B104
                )
                UI.warning(
                    f"Port conflict detected! Global HTTPS proxy port {orig_ssl} is in use on the host. Using {ssl_port} instead."
                )
            allocated_ports.append(ssl_port)

            # Check Admin port
            if admin_port in allocated_ports or not self.manager.check_port(
                "0.0.0.0",
                admin_port,  # nosec B104
            ):
                orig_admin = admin_port
                admin_port = self.manager.find_available_port(
                    "0.0.0.0",
                    admin_port,
                    exclude=allocated_ports,  # nosec B104
                )
                UI.warning(
                    f"Port conflict detected! Global Admin proxy port {orig_admin} is in use on the host. Using {admin_port} instead."
                )

        # Start infrastructure
        env = self._get_infra_env(resolved_ip, ssl_port, http_port, admin_port)

        from ldm_core.docker_service import DockerService

        target_name = getattr(self.manager, "target", None)
        compose_prefix = DockerService.get_compose_cmd_prefix(target_name)

        cmd = [
            *compose_prefix,
            "-f",
            str(infra_compose),
            "up",
            "-d",
            "--remove-orphans",
        ]
        if force_recreate:
            cmd.append("--force-recreate")

        self.manager.run_command(
            cmd,
            env=env,
            capture_output=True,
        )
        return ssl_port

    def _get_infra_env(
        self, resolved_ip="127.0.0.1", ssl_port=443, http_port=80, admin_port=18080
    ):
        """Generates the standard environment variables for the infrastructure stack."""
        actual_home = get_actual_home()
        cert_dir = (actual_home / "liferay-docker-certs").resolve()

        env = os.environ.copy()
        env["LDM_CERTS_DIR"] = str(cert_dir)
        env["LDM_SSL_PORT"] = str(ssl_port)
        env["LDM_HTTP_PORT"] = str(http_port)
        env["LDM_ADMIN_PORT"] = str(admin_port)
        env["LDM_RESOLVED_IP"] = resolved_ip
        return env

    def _fix_cert_permissions(self, path):
        """Attempts to fix directory permissions using sudo if authorized by the user."""
        if UI.confirm(f"Fix permissions for {path}? (Requires sudo)", "Y"):
            try:
                # Get current user and group
                import os

                uid = os.getuid()
                gid = os.getgid()
                UI.detail(f"Requesting permission to reclaim ownership of {path}...")
                self.manager.run_command(
                    ["sudo", "chown", "-R", f"{uid}:{gid}", str(path)]
                )
                return True
            except Exception as e:
                UI.error(f"Failed to reclaim ownership: {e}")
        return False

    def setup_ssl(self, cert_dir, host_name):  # noqa: PLR0911, PLR0912
        """Ensures valid locally-trusted wildcard certificates exist for the host."""
        if not shutil.which("mkcert"):
            UI.error("LDM Requirement Missing: mkcert")
            UI.detail(
                "Local SSL requires 'mkcert'. Please install it to continue:\n"
                "  - macOS: brew install mkcert nss\n"
                "  - Windows: scoop install mkcert\n"
                "  - Linux: sudo apt install mkcert libnss3-tools\n"
            )
            UI.detail(
                f"After installation, run: {UI.WHITE}mkcert -install{UI.COLOR_OFF}"
            )
            UI.warning("SSL proxy will use default self-signed certs for now.")
            return False

        try:
            cert_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            UI.error(f"Permission Denied: Cannot create directory {cert_dir}")
            if self._fix_cert_permissions(cert_dir.parent):
                # Retry
                try:
                    cert_dir.mkdir(parents=True, exist_ok=True)
                except Exception:
                    return False
            else:
                return False

        cert_file = cert_dir / f"{host_name}.pem"
        key_file = cert_dir / f"{host_name}-key.pem"

        new_files_written = False

        if not cert_file.exists():
            UI.detail(
                f"Generating SSL certificates for {UI.CYAN}{host_name}{UI.COLOR_OFF}..."
            )
            try:
                # We use check=False to handle errors manually with better feedback
                res = self.manager.run_command(
                    [
                        "mkcert",
                        "-cert-file",
                        str(cert_file),
                        "-key-file",
                        str(key_file),
                        host_name,
                        f"*.{host_name}",
                    ],
                    cwd=str(cert_dir),
                    check=False,
                    capture_output=True,
                )

                if res is None:
                    # Check if it was a permission issue
                    if not os.access(cert_dir, os.W_OK):
                        UI.error(
                            f"Permission Denied: mkcert cannot write to {cert_dir}"
                        )
                        if self._fix_cert_permissions(cert_dir):
                            # Retry the mkcert command once
                            return self.setup_ssl(cert_dir, host_name)
                    else:
                        UI.error("mkcert failed to generate certificates.")
                        UI.detail(
                            "Ensure mkcert is correctly installed and initialized ('mkcert -install')."
                        )
                    return False
                new_files_written = True
            except Exception as e:
                UI.error(f"mkcert unexpected error: {e}")
                return False

        config_file = cert_dir / f"traefik-{host_name}.yml"
        if not config_file.exists():
            new_files_written = True

        # Generate Traefik Dynamic Config for this host
        try:
            config_content = f"""
tls:
  certificates:
    - certFile: /etc/traefik/certs/{host_name}.pem
      keyFile: /etc/traefik/certs/{host_name}-key.pem
"""
            from ldm_core.utils import safe_write_text

            safe_write_text(config_file, config_content)
        except Exception as e:
            UI.error(f"Failed to write Traefik configuration: {e}")
            return False

        if new_files_written:
            import platform

            is_mac = platform.system().lower() == "darwin"
            is_win = platform.system().lower() == "windows"
            is_wsl = "microsoft" in platform.uname().release.lower()
            if is_mac or is_win or is_wsl:
                UI.detail("Waiting for host certificates to sync with Docker VM...")
                time.sleep(2)

            # Restart Traefik to ensure it picks up the new files, as VirtioFS/SSHFS
            # file-watching (inotify) is often unreliable on VM-based Docker providers.
            from ldm_core.docker_service import DockerService

            if DockerService.exists(
                "liferay-proxy-global"
            ) and DockerService.is_running("liferay-proxy-global"):
                UI.detail(
                    "Restarting Traefik proxy to ensure new certificates are detected..."
                )
                DockerService.restart("liferay-proxy-global")

        return True

    def cmd_infra_down(self):
        """Tears down the global infrastructure (Traefik, Proxy)."""
        from ldm_core.utils import has_shared_projects

        if not getattr(self.manager, "non_interactive", False) and has_shared_projects(
            self.manager
        ):
            if not UI.confirm(
                "Are you sure you want to tear down global infrastructure? This will disrupt all active shared workspaces.",
                default="N",
            ):
                UI.detail("Infra teardown aborted.")
                return False

        UI.warning("Tearing down global infrastructure (Traefik)...")
        infra_compose = self.manager.get_resource_path("infra-compose.yml")
        if not infra_compose:
            UI.die("Infrastructure compose file 'infra-compose.yml' not found.")

        # Down requires the same env as UP to resolve volume paths correctly
        from ldm_core.docker_service import DockerService

        target_name = getattr(self.manager, "target", None)
        compose_prefix = DockerService.get_compose_cmd_prefix(target_name)

        env = self._get_infra_env()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        self.manager.run_command(
            [*compose_prefix, "-f", str(infra_compose), "down", "-v"],
            env=env,
            capture_output=capture,
        )

        # Also stop the docker socket proxy and global search
        from ldm_core.constants import INFRA_SERVICES
        from ldm_core.docker_service import DockerService

        for container, _ in INFRA_SERVICES:
            if container == "liferay-proxy-global":
                continue  # Handled by compose down above
            DockerService.stop(container)
            DockerService.rm(container)
        UI.success("Infrastructure teardown complete.")
        return True

    def cmd_infra_restart(self):
        """Restarts the global infrastructure services."""
        UI.detail("Restarting Global Infrastructure...")
        self.cmd_infra_down()
        self.cmd_infra_setup()

    def cmd_restart_proxy(self):
        """Restarts only the Traefik proxy container."""
        from ldm_core.docker_service import DockerService

        container_name = "liferay-proxy-global"
        UI.detail(f"Restarting proxy container: {container_name}...")

        if DockerService.exists(container_name):
            DockerService.restart(container_name)
            UI.success("Proxy container restarted successfully.")
        else:
            UI.error(
                f"Container '{container_name}' does not exist. Is the infrastructure running?"
            )

    def _ensure_network(self, target_name: str | None = None):
        """Ensures the standard 'liferay-net' Docker network exists."""
        from ldm_core.docker_service import DockerService

        docker_prefix = DockerService.get_docker_cmd_prefix(target_name)
        networks = self.manager.run_command(
            [*docker_prefix, "network", "ls", "--format", "{{.Name}}"],
            timeout=_INFRA_PROBE_TIMEOUT,
        )
        if "liferay-net" not in (networks or ""):
            UI.detail("Creating Docker network: liferay-net")
            self.manager.run_command(
                [*docker_prefix, "network", "create", "liferay-net"],
                timeout=_INFRA_LIFECYCLE_TIMEOUT,
            )

    def _ensure_docker_proxy(self):
        """Ensures a safe Docker socket proxy is running for Traefik."""
        from ldm_core.docker_service import DockerService

        container_name = "liferay-docker-proxy"
        # Check if it exists at all (running or stopped)
        exists = DockerService.exists(container_name)

        if not exists:
            UI.detail("Starting Docker socket bridge...")
            socket_path = get_docker_socket_path()

            # Hardening for VM-based providers (Colima, Lima, OrbStack):
            if any(
                p in str(socket_path).lower() for p in ["colima", ".lima", "orbstack"]
            ):
                UI.debug(
                    f"Provider VM detected ({socket_path}). Using standard internal socket path."
                )
                socket_path = "/var/run/docker.sock"

            self.manager.run_command(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "--network",
                    "liferay-net",
                    "-v",
                    f"{socket_path}:/var/run/docker.sock:ro",
                    "tecnativa/docker-socket-proxy",
                ]
            )
        else:
            # If it exists, make sure it is running
            running = DockerService.is_running(container_name)
            if not running:
                UI.detail("Starting existing Docker socket bridge...")
                DockerService.start(container_name)

    def setup_global_database(self, force=False, db_type=None):
        """Ensures the global database service for `db_type` is running.

        "Shared" means shared among projects resolving to the same target,
        not a single global instance for every target -- see
        DatabaseService.cmd_start's docstring and
        docs/explanation/remote-node-architecture.md §5. Resolves
        self.manager.target exactly like any other Docker operation.

        LDM-#1361: there is one global container per engine, resolved via
        `shared_database_container`. `db_type=None` resolves to PostgreSQL,
        which is what every pre-#1361 caller got.

        A mixed fleet runs both globals. They are provisioned lazily -- on
        the first `ldm run` that needs each -- rather than eagerly together,
        because eager provisioning would cost an all-PostgreSQL fleet (the
        overwhelming majority) a permanently idle MySQL container for
        nothing. Two globals still beats a container per project, which is
        the comparison shared mode is actually against.
        """
        from ldm_core.docker_service import DockerService
        from ldm_core.utils import (
            resolve_dependency_version,
            shared_database_container,
            shared_database_volume,
        )

        target_name = getattr(self.manager, "target", None)
        docker_prefix = DockerService.get_docker_cmd_prefix(target_name)
        is_mysql = str(db_type or "").lower() in ("mysql", "mariadb")
        engine_label = "MySQL" if is_mysql else "PostgreSQL"
        db_name = shared_database_container(db_type)
        data_volume = shared_database_volume(db_type)
        exists = DockerService.exists(db_name, target_name=target_name)
        running = DockerService.is_running(db_name, target_name=target_name)

        if exists and not running:
            UI.detail(
                f"Starting existing Global Database ({engine_label}) container..."
            )
            DockerService.start(db_name, target_name=target_name)
            # LDM-#1545: DockerService.start runs with check=False and its result
            # was discarded, and the readiness probe below lives inside the
            # `if not exists:` branch -- so a global that already existed but
            # failed to restart (port taken, corrupt volume, OOM) was never
            # probed at all. LDM then configured Liferay against a dead
            # database and reported success.
            if not DockerService.is_running(db_name, target_name=target_name):
                UI.die(
                    f"Global database container '{db_name}' exists but could not be started.",
                    tip=f"Inspect it with 'docker logs {db_name}'.",
                    exit_code=3,
                )

        if not exists:
            UI.detail(f"Initializing Global Database ({engine_label}) container...")
            tag = "latest"

            if is_mysql:
                # A single MySQL-protocol container serves both `--db mysql`
                # and `--db mariadb`, which is why they share one container
                # name. That is not a shortcut: `_inject_liferay_db_env`
                # already emits an identical `jdbc:mariadb://` URL and
                # MariaDB103Dialect for both engines, so Liferay cannot tell
                # them apart from the connection down. Giving `mariadb` its
                # own global would add a third idle container to a mixed
                # fleet for no behavioural difference -- and, worse, if one
                # name mapped to two images then whichever project ran first
                # would silently decide the engine for the second.
                mysql_ver = resolve_dependency_version(tag, "mysql") or "8.4"
                self.manager.run_command(
                    [
                        *docker_prefix,
                        "run",
                        "-d",
                        "--name",
                        db_name,
                        "--network",
                        "liferay-net",
                        "-e",
                        "MYSQL_ROOT_PASSWORD=test",  # nosec B105
                        "-e",
                        "MYSQL_USER=lportal",
                        "-e",
                        "MYSQL_PASSWORD=test",  # nosec B105
                        "-e",
                        "MYSQL_DATABASE=lportal",
                        "-p",
                        "3307:3306",
                        "-v",
                        f"{data_volume}:/var/lib/mysql",
                        f"mysql:{mysql_ver}",
                        "mysqld",
                        "--character-set-server=utf8mb4",
                        "--collation-server=utf8mb4_unicode_ci",
                        "--character-set-filesystem=utf8mb4",
                        # Mirrors the isolated MySQL service in
                        # handlers/composer.py. It also makes the lowercase
                        # `shared_database_name` contract hold on Linux,
                        # where MySQL is otherwise case-sensitive.
                        "--lower_case_table_names=1",
                        "--bind-address=0.0.0.0",
                        "--skip-name-resolve",
                        "--mysql-native-password=ON",
                    ],
                    timeout=_INFRA_CREATE_TIMEOUT,
                )
            else:
                pg_ver = resolve_dependency_version(tag, "postgresql") or "16"
                self.manager.run_command(
                    [
                        *docker_prefix,
                        "run",
                        "-d",
                        "--name",
                        db_name,
                        "--network",
                        "liferay-net",
                        "-e",
                        "POSTGRES_PASSWORD=test",  # nosec B105
                        "-e",
                        "POSTGRES_USER=lportal",
                        "-e",
                        "POSTGRES_DB=lportal",
                        "-p",
                        "5433:5432",
                        "-v",
                        f"{data_volume}:/var/lib/postgresql/data",
                        f"postgres:{pg_ver}",
                    ],
                    timeout=_INFRA_CREATE_TIMEOUT,
                )

            UI.detail("Waiting for Global Database to become ready...")
            import time

            if is_mysql:
                ready_cmd = [
                    *docker_prefix,
                    "exec",
                    db_name,
                    "mysqladmin",
                    "ping",
                    "-h",
                    "127.0.0.1",
                    "-uroot",
                    "-ptest",
                ]
            else:
                ready_cmd = [
                    *docker_prefix,
                    "exec",
                    db_name,
                    "pg_isready",
                    "-U",
                    "lportal",
                ]

            # LDM-#1545: this loop had no failure path. It broke on success,
            # broke after UI.error (which prints and RETURNS -- only UI.die
            # exits), or exhausted 60 attempts and fell through silently. So a
            # shared database that never started was indistinguishable from one
            # that came up, and `ldm run` reported success with no container.
            #
            # Observed in CI: `ldm run --db mysql --database-mode shared` exited
            # 0 while `docker ps -a` showed the global had never been created.
            # The E2E caught it only because LDM-#1494 checks the container
            # directly; nothing in LDM itself objected.
            #
            # Exit code 3 is the contract's Infrastructure/Data Error
            # (.agents/skills/ldm-architecture/SKILL.md). Liferay cannot run
            # without its database, so continuing here only defers the failure
            # to something less legible -- a readiness timeout, or a stack that
            # boots and cannot serve.
            ready = False
            for _ in range(60):
                status = self.manager.get_container_status(
                    db_name, target_name=target_name
                )
                if status == "exited":
                    UI.die(
                        f"Global database container '{db_name}' exited unexpectedly.",
                        tip=f"Inspect it with 'docker logs {db_name}'.",
                        exit_code=3,
                    )
                res = self.manager.run_command(
                    ready_cmd,
                    check=False,
                    capture_output=True,
                    timeout=_INFRA_PROBE_TIMEOUT,
                )
                if res is not None:
                    UI.success("Global database is ready.")
                    ready = True
                    break
                time.sleep(2)

            if not ready:
                UI.die(
                    f"Global database ({engine_label}) did not become ready in time.",
                    details=f"Container: {db_name}",
                    tip=(
                        f"Check 'docker logs {db_name}'. If the host is slow or "
                        "loaded, the container may still be initialising."
                    ),
                    exit_code=3,
                )

    def setup_global_search(self, force=False, _depth=0):  # noqa: C901, PLR0912, PLR0915
        """Ensures the global ES8 search service is running.

        "Shared" means shared among projects resolving to the same target
        -- see setup_global_database's docstring. Starting/checking an
        *existing* container resolves self.manager.target like any other
        Docker operation. First-time creation on a remote target is not
        yet supported: unlike the DB's Docker-managed named volume, this
        container's data/backup dirs are host bind-mounts (built from a
        *local* path) -- redirecting container creation without also
        resolving and pre-creating the equivalent remote path (the same
        problem LDM-#1134 solved for project bind mounts) would silently
        create empty, wrong directories on the remote engine.
        """
        # LDM-369: Sidecar Protection. If the current project metadata explicitly
        # disables shared search, we MUST NOT touch the global search infrastructure.
        project_meta = getattr(self.manager, "meta", {})
        if project_meta:
            use_shared = (
                str(project_meta.get("use_shared_search", "true")).lower() == "true"
            )
            if not use_shared and not force:
                UI.debug("Skipping global search setup (Sidecar mode active)")
                return None

        from ldm_core.docker_service import DockerService

        target_name = getattr(self.manager, "target", None)
        docker_prefix = DockerService.get_docker_cmd_prefix(target_name)
        search_name = "liferay-search-global"
        exists = DockerService.exists(search_name, target_name=target_name)
        running = DockerService.is_running(search_name, target_name=target_name)

        if exists and not running:
            UI.detail("Starting existing Global Search (ES8) container...")
            DockerService.start(search_name, target_name=target_name)

        if not exists:
            if target_name and target_name != "local":
                from ldm_core.config import get_active_target

                active_target = get_active_target(target_name)
                if active_target.name != "local" and active_target.host not in (
                    "localhost",
                    "127.0.0.1",
                    "",
                ):
                    UI.die(
                        f"Global Search isn't provisioned yet on '{active_target.name}', "
                        "and first-time remote provisioning isn't supported "
                        "(its data/backup volumes are host bind-mounts, not "
                        "portable across engines automatically). Provision it "
                        "once by running a shared-search-enabled project against "
                        f"'{active_target.name}' with this fixed, or set up the "
                        "container manually on that node first."
                    )
            UI.detail("Initializing Global Search (ES8) container...")
            home = get_actual_home()
            es_data = (home / ".ldm" / "infra" / "search" / "data").resolve()
            es_backup = (home / ".ldm" / "infra" / "search" / "backup").resolve()
            es_data.mkdir(parents=True, exist_ok=True)
            es_backup.mkdir(parents=True, exist_ok=True)

            # Fix permissions for Linux/CI (ES runs as UID 1000, we ensure world-writable or chowned)
            # Reclamation via Docker container ensures it works even if files are owned by root
            from ldm_core.utils import reclaim_volume_permissions

            reclaim_volume_permissions(es_data, chmod_val="777")
            reclaim_volume_permissions(es_backup, chmod_val="777")

            from ldm_core.constants import ELASTICSEARCH_VERSION

            es_heap = "512m"
            if hasattr(self.manager, "defaults") and self.manager.defaults is not None:
                es_heap = self.manager.defaults.get("elasticsearch_heap_size", "512m")

            self.manager.run_command(
                [
                    *docker_prefix,
                    "run",
                    "-d",
                    "--name",
                    search_name,
                    "--network",
                    "liferay-net",
                    "-e",
                    "discovery.type=single-node",
                    "-e",
                    "xpack.security.enabled=false",
                    "-e",
                    "path.repo=/usr/share/elasticsearch/backup",
                    "-e",
                    "cluster.name=liferay-cluster",
                    "-e",
                    f"ES_JAVA_OPTS=-Xms{es_heap} -Xmx{es_heap}",
                    "-e",
                    "processors=1",
                    "-e",
                    "indices.query.bool.max_clause_count=10000",
                    "-v",
                    f"{es_data}:/usr/share/elasticsearch/data",
                    "-v",
                    f"{es_backup}:/usr/share/elasticsearch/backup",
                    f"elasticsearch:{ELASTICSEARCH_VERSION}",
                ],
                timeout=_INFRA_CREATE_TIMEOUT,
            )
            UI.detail("Waiting for Elasticsearch to become ready...")

            # Robust health check loop
            ready = False
            for _ in range(60):  # 5 minute timeout (60 * 5s)
                # Fail fast if container exited
                status = self.manager.get_container_status(
                    search_name, target_name=target_name
                )
                if status == "exited":
                    UI.error("Elasticsearch container exited unexpectedly.")
                    break

                res = self.manager.run_command(
                    [
                        *docker_prefix,
                        "exec",
                        search_name,
                        "curl",
                        "-s",
                        "localhost:9200",
                    ],
                    check=False,
                    capture_output=True,
                    timeout=_INFRA_PROBE_TIMEOUT,
                )
                if res and '"cluster_name"' in res:
                    ready = True
                    break
                time.sleep(5)

            if not ready:
                if _depth >= 2:
                    UI.die(
                        "Elasticsearch failed to start after 2 restart attempts. Check: ldm logs",
                        exit_code=3,
                    )
                UI.error("Elasticsearch failed to become ready in time.")
                # AUTO-REPAIR: If ES fails to start, it's often due to corrupted data in the volume.
                # Wiping and restarting usually fixes mapping/plugin-mismatch issues.
                UI.warning("Attempting automatic search volume repair...")
                self.manager.run_command(
                    [*docker_prefix, "rm", "-f", search_name],
                    check=False,
                    timeout=_INFRA_LIFECYCLE_TIMEOUT,
                )
                if es_data.exists():
                    import shutil

                    shutil.rmtree(es_data)
                    es_data.mkdir(parents=True, exist_ok=True)
                    from ldm_core.utils import reclaim_volume_permissions

                    reclaim_volume_permissions(es_data, chmod_val="777")

                UI.detail("Restarting Global Search with clean slate...")
                return self.setup_global_search(force=force, _depth=_depth + 1)

            # Register backup repository (required for snapshots)
            self.manager.run_command(
                [
                    *docker_prefix,
                    "exec",
                    search_name,
                    "curl",
                    "-s",
                    "-X",
                    "PUT",
                    "localhost:9200/_snapshot/liferay_backup",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    json.dumps(
                        {
                            "type": "fs",
                            "settings": {"location": "/usr/share/elasticsearch/backup"},
                        }
                    ),
                ],
                timeout=_INFRA_PROBE_TIMEOUT,
            )

            # Proactive analyzer installation
            UI.detail("Installing missing Liferay analyzers in Global Search...")

            # Tests expect a 'plugin list' call first
            self.manager.run_command(
                [
                    *docker_prefix,
                    "exec",
                    search_name,
                    "bin/elasticsearch-plugin",
                    "list",
                ],
                timeout=_INFRA_PROBE_TIMEOUT,
            )

            analyzers = [
                "analysis-icu",
                "analysis-kuromoji",
                "analysis-smartcn",
                "analysis-stempel",
            ]
            for plugin in analyzers:
                self.manager.run_command(
                    [
                        *docker_prefix,
                        "exec",
                        search_name,
                        "bin/elasticsearch-plugin",
                        "install",
                        "-b",
                        plugin,
                    ],
                    check=False,
                    # Downloads the plugin from the internet, so this is the one
                    # call here most likely to stall on something outside the
                    # daemon entirely.
                    timeout=_INFRA_CREATE_TIMEOUT,
                )

            UI.detail("Restarting Global Search to activate plugins...")
            self.manager.run_command(
                [*docker_prefix, "restart", search_name],
                timeout=_INFRA_LIFECYCLE_TIMEOUT,
            )

            # Wait for it to come back up
            UI.detail("Waiting for Global Search to be ready after restart...")
            ready = False
            for _ in range(30):
                # Fail fast if container exited
                status = self.manager.get_container_status(
                    search_name, target_name=target_name
                )
                if status == "exited":
                    UI.error(
                        "Elasticsearch container exited unexpectedly after restart."
                    )
                    break

                res = self.manager.run_command(
                    [
                        *docker_prefix,
                        "exec",
                        search_name,
                        "curl",
                        "-s",
                        "localhost:9200",
                    ],
                    check=False,
                    capture_output=True,
                    timeout=_INFRA_PROBE_TIMEOUT,
                )
                if res and '"cluster_name"' in res:
                    ready = True
                    break
                time.sleep(5)
            if not ready:
                UI.warning(
                    "Global Search restart timed out. Snapshots may fail initially."
                )
        else:
            # Check if it is running
            running = DockerService.is_running(search_name, target_name=target_name)
            if not running:
                UI.detail(f"Starting existing {search_name} container...")
                DockerService.start(search_name, target_name=target_name)

            # Always ensure backup repository is registered if service is running
            UI.debug("Ensuring Global Search backup repository is registered...")
            self.manager.run_command(
                [
                    *docker_prefix,
                    "exec",
                    search_name,
                    "curl",
                    "-s",
                    "-X",
                    "PUT",
                    "localhost:9200/_snapshot/liferay_backup",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    json.dumps(
                        {
                            "type": "fs",
                            "settings": {"location": "/usr/share/elasticsearch/backup"},
                        }
                    ),
                ],
                check=False,
                timeout=_INFRA_PROBE_TIMEOUT,
            )
        return None

    def cmd_system(self, subcommand):
        """Routing for system-level management commands."""
        if subcommand == "relocate":
            self.cmd_system_relocate(self.manager.args.target)
        else:
            UI.die(f"Unknown system subcommand: {subcommand}")

    def cmd_system_relocate(self, target_path):  # noqa: C901, PLR0912
        """Safely moves LDM and Docker data to an external drive via symbolic links."""
        from pathlib import Path

        UI.heading(f"System Relocation: {target_path}")

        target = Path(target_path).resolve()
        if not target.exists() or not target.is_dir():
            UI.die(f"Target path does not exist or is not a directory: {target}")

        # Ensure target is not in the home directory to avoid circular links
        home = get_actual_home()
        if str(home) in str(target):
            UI.die("Target path must be outside of your home directory.")

        paths_to_move = [
            (".colima", "Docker Engine (Colima)"),
            (".ldm", "LDM Configuration & Search Data"),
            ("liferay-docker-certs", "Global SSL Certificates"),
        ]

        # 1. Safety Checks
        try:
            context = (
                self.manager.run_command(["docker", "context", "show"], check=False)
                or ""
            ).strip()
            if "colima" in context.lower() or context == "default":
                UI.detail("Stopping Colima to ensure data integrity...")
                self.manager.run_command(["colima", "stop"], check=False)
        except Exception:
            pass

        for folder, label in paths_to_move:
            source = home / folder
            dest = target / folder

            if not source.exists() and not source.is_symlink():
                UI.debug(f"Skipping {label}: Source does not exist.")
                continue

            if source.is_symlink():
                target_link = source.readlink()
                UI.detail(
                    f"{label} is already a link to: {UI.CYAN}{target_link}{UI.COLOR_OFF}"
                )
                continue

            UI.detail(f"Relocating {label}...")

            # 2. Move data if requested
            if not getattr(self.manager.args, "no_move", False):
                if dest.exists():
                    if not UI.confirm(
                        f"Destination {dest} already exists. Merge/Overwrite?", "N"
                    ):
                        UI.warning(f"Skipping {label}")
                        continue

                # Perform the move
                try:
                    # We use shutil.move which handles cross-device moves by copy+delete
                    UI.detail(f"  -> Moving data to {dest} (this may take a while)...")
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.move(str(source), str(dest))
                except Exception as e:
                    UI.error(f"Failed to move {label}: {e}")
                    continue
            # If no-move, we assume user already moved it or wants a fresh start
            elif source.exists():
                UI.detail(f"  -> Deleting local {source} (no-move flag active)...")
                if source.is_dir():
                    shutil.rmtree(source)
                else:
                    source.unlink()

            # 3. Create Symlink
            try:
                # Ensure the destination directory exists if it was a fresh start
                if not dest.exists():
                    dest.mkdir(parents=True, exist_ok=True)

                source.symlink_to(dest)
                UI.success(f"{label} is now linked to external drive.")
            except Exception as e:
                UI.error(f"Failed to create link for {label}: {e}")

        UI.success("Relocation complete. You can now restart Colima.")
        UI.detail(f"Run: {UI.WHITE}colima start{UI.COLOR_OFF}")

    def thaw_elasticsearch(self, quiet=False):
        """Attempts to lift disk watermarks on the global search container."""
        from ldm_core.docker_service import DockerService

        search_name = "liferay-search-global"
        if not quiet:
            UI.detail("Checking for blocked search indices (Disk Watermark)...")

        try:
            # First, lift the watermarks to 99%
            lift_res = DockerService.exec(
                search_name,
                [
                    "curl",
                    "-s",
                    "-X",
                    "PUT",
                    "localhost:9200/_cluster/settings",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    json.dumps(
                        {
                            "persistent": {
                                "cluster.routing.allocation.disk.watermark.low": "95%",
                                "cluster.routing.allocation.disk.watermark.high": "98%",
                                "cluster.routing.allocation.disk.watermark.flood_stage": "99%",
                            }
                        }
                    ),
                ],
                check=False,
            )

            if lift_res and '"acknowledged":true' in lift_res:
                if not quiet:
                    UI.success("Elasticsearch disk watermarks lifted.")

                # Now explicitly lift the read-only block from all indices
                DockerService.exec(
                    search_name,
                    [
                        "curl",
                        "-s",
                        "-X",
                        "PUT",
                        "localhost:9200/_all/_settings",
                        "-H",
                        "Content-Type: application/json",
                        "-d",
                        json.dumps({"index.blocks.read_only_allow_delete": None}),
                    ],
                    check=False,
                )
                return True
        except Exception as e:
            if not quiet:
                UI.debug(f"Thaw failed: {e}")

        return False
