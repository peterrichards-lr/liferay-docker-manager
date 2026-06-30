import json
import os
import shutil
import time

from ldm_core.ui import UI
from ldm_core.utils import (
    get_actual_home,
    get_compose_cmd,
    get_docker_socket_path,
)


class InfraService:
    """Service for global infrastructure management (Traefik, Global Search)."""

    def __init__(self, manager=None):
        self.manager = manager

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
        self.setup_infrastructure(resolved_ip, 443, use_ssl=True)
        UI.success("Infrastructure setup complete.")

    def get_proxy_ports(self):
        """Returns the active mapped host ports for liferay-proxy-global."""
        ports = {"http": 80, "https": 443, "admin": 18080}
        try:
            # Inspect the running proxy container
            inspect_raw = self.manager.run_command(
                [
                    "docker",
                    "inspect",
                    "liferay-proxy-global",
                    "--format",
                    "{{json .NetworkSettings.Ports}}",
                ],
                check=False,
                capture_output=True,
            )
            if inspect_raw:
                settings = json.loads(inspect_raw)
                # settings is a dict like: {"443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "443"}], ...}
                if settings.get("80/tcp"):
                    ports["http"] = int(settings["80/tcp"][0]["HostPort"])
                if settings.get("443/tcp"):
                    ports["https"] = int(settings["443/tcp"][0]["HostPort"])
                if settings.get("8080/tcp"):
                    ports["admin"] = int(settings["8080/tcp"][0]["HostPort"])
        except Exception:
            pass
        return ports

    def setup_infrastructure(
        self,
        resolved_ip,
        ssl_port,
        use_ssl=True,
        quiet=False,
        use_shared_search=True,
        use_shared_db=False,
    ):
        """Initializes global Traefik proxy and search services."""
        self._ensure_network()
        if not use_ssl:
            return 443

        # Docker bridge proxy check (Traefik needs to talk to Docker socket securely)
        self._ensure_docker_proxy()

        # Orchestrated Global Search (ES8)
        if getattr(self.manager.args, "search", False) and use_shared_search:
            self.setup_global_search()

        if use_shared_db:
            self.setup_global_database()

        if not quiet:
            UI.info("Checking infrastructure stack (Traefik SSL Proxy)...")
        infra_compose = self.manager.get_resource_path("infra-compose.yml")
        if not infra_compose:
            UI.die(
                "Infrastructure compose file 'infra-compose.yml' not found in resources."
            )

        from ldm_core.docker_service import DockerService

        is_proxy_running = DockerService.is_running("liferay-proxy-global")

        http_port = int(os.getenv("LDM_HTTP_PORT", "80"))
        ssl_port = int(ssl_port)
        admin_port = int(os.getenv("LDM_ADMIN_PORT", "18080"))

        if is_proxy_running:
            # Use the currently running ports to keep compose state identical
            ports = self.get_proxy_ports()
            http_port = ports["http"]
            ssl_port = ports["https"]
            admin_port = ports["admin"]
        else:
            allocated_ports = []

            # Check HTTP port
            if not self.manager.check_port("127.0.0.1", http_port):
                orig_http = http_port
                http_port = self.manager.find_available_port("127.0.0.1", http_port)
                UI.warning(
                    f"Port conflict detected! Global HTTP proxy port {orig_http} is in use on the host. Using {http_port} instead."
                )
            allocated_ports.append(http_port)

            # Check HTTPS port
            if ssl_port in allocated_ports or not self.manager.check_port(
                "127.0.0.1", ssl_port
            ):
                orig_ssl = ssl_port
                ssl_port = self.manager.find_available_port(
                    "127.0.0.1", ssl_port, exclude=allocated_ports
                )
                UI.warning(
                    f"Port conflict detected! Global HTTPS proxy port {orig_ssl} is in use on the host. Using {ssl_port} instead."
                )
            allocated_ports.append(ssl_port)

            # Check Admin port
            if admin_port in allocated_ports or not self.manager.check_port(
                "127.0.0.1", admin_port
            ):
                orig_admin = admin_port
                admin_port = self.manager.find_available_port(
                    "127.0.0.1", admin_port, exclude=allocated_ports
                )
                UI.warning(
                    f"Port conflict detected! Global Admin proxy port {orig_admin} is in use on the host. Using {admin_port} instead."
                )

        # Start infrastructure
        env = self._get_infra_env(resolved_ip, ssl_port, http_port, admin_port)

        self.manager.run_command(
            [
                *get_compose_cmd(),
                "-f",
                str(infra_compose),
                "up",
                "-d",
                "--remove-orphans",
            ],
            env=env,
            capture_output=quiet,
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
                UI.info(f"Requesting permission to reclaim ownership of {path}...")
                self.manager.run_command(
                    ["sudo", "chown", "-R", f"{uid}:{gid}", str(path)]
                )
                return True
            except Exception as e:
                UI.error(f"Failed to reclaim ownership: {e}")
        return False

    def setup_ssl(self, cert_dir, host_name):
        """Ensures valid locally-trusted wildcard certificates exist for the host."""
        if not shutil.which("mkcert"):
            UI.error("LDM Requirement Missing: mkcert")
            UI.info(
                "Local SSL requires 'mkcert'. Please install it to continue:\n"
                "  - macOS: brew install mkcert nss\n"
                "  - Windows: scoop install mkcert\n"
                "  - Linux: sudo apt install mkcert libnss3-tools\n"
            )
            UI.info(f"After installation, run: {UI.WHITE}mkcert -install{UI.COLOR_OFF}")
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
            UI.info(
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
                        UI.info(
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
                UI.info("Waiting for host certificates to sync with Docker VM...")
                time.sleep(2)

        return True

    def cmd_infra_down(self):
        """Tears down the global infrastructure (Traefik, Proxy)."""
        UI.warning("Tearing down global infrastructure (Traefik)...")
        infra_compose = self.manager.get_resource_path("infra-compose.yml")
        if not infra_compose:
            UI.die("Infrastructure compose file 'infra-compose.yml' not found.")

        # Down requires the same env as UP to resolve volume paths correctly
        env = self._get_infra_env()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        self.manager.run_command(
            [*get_compose_cmd(), "-f", str(infra_compose), "down", "-v"],
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

    def cmd_infra_restart(self):
        """Restarts the global infrastructure services."""
        UI.info("Restarting Global Infrastructure...")
        self.cmd_infra_down()
        self.cmd_infra_setup()

    def _ensure_network(self):
        """Ensures the standard 'liferay-net' Docker network exists."""
        networks = self.manager.run_command(
            ["docker", "network", "ls", "--format", "{{.Name}}"]
        )
        if "liferay-net" not in (networks or ""):
            UI.detail("Creating Docker network: liferay-net")
            self.manager.run_command(["docker", "network", "create", "liferay-net"])

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

    def setup_global_database(self, force=False):
        """Ensures the global PostgreSQL database service is running."""
        from ldm_core.docker_service import DockerService

        db_name = "liferay-db-global"
        exists = DockerService.exists(db_name)

        if not exists:
            UI.detail("Initializing Global Database (PostgreSQL) container...")
            tag = "latest"
            from ldm_core.utils import resolve_dependency_version

            pg_ver = resolve_dependency_version(tag, "postgresql") or "16"

            self.manager.run_command(
                [
                    "docker",
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
                    f"postgres:{pg_ver}",
                ]
            )
            UI.info("Waiting for Global Database to become ready...")
            import time

            for _ in range(60):
                status = self.manager.get_container_status(db_name)
                if status == "exited":
                    UI.error("Global database container exited unexpectedly.")
                    break
                res = self.manager.run_command(
                    ["docker", "exec", db_name, "pg_isready", "-U", "lportal"],
                    check=False,
                    capture_output=True,
                )
                if res is not None:
                    UI.success("Global database is ready.")
                    break
                time.sleep(2)

    def setup_global_search(self, force=False):
        """Ensures the global ES8 search service is running."""
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

        search_name = "liferay-search-global"
        exists = DockerService.exists(search_name)

        if not exists:
            UI.detail("Initializing Global Search (ES8) container...")
            home = get_actual_home()
            es_data = (home / ".ldm" / "infra" / "search" / "data").resolve()
            es_backup = (home / ".ldm" / "infra" / "search" / "backup").resolve()
            es_data.mkdir(parents=True, exist_ok=True)
            es_backup.mkdir(parents=True, exist_ok=True)

            # Fix permissions for Linux/CI (ES runs as UID 1000, we ensure world-writable or chowned)
            # Reclamation via Docker container ensures it works even if files are owned by root
            from ldm_core.utils import reclaim_volume_permissions

            reclaim_volume_permissions(es_data)
            reclaim_volume_permissions(es_backup)

            from ldm_core.constants import ELASTICSEARCH_VERSION

            es_heap = "512m"
            if hasattr(self.manager, "defaults") and self.manager.defaults is not None:
                es_heap = self.manager.defaults.get("elasticsearch_heap_size", "512m")

            self.manager.run_command(
                [
                    "docker",
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
                ]
            )
            UI.info("Waiting for Elasticsearch to become ready...")

            # Robust health check loop
            ready = False
            for _ in range(60):  # 5 minute timeout (60 * 5s)
                # Fail fast if container exited
                status = self.manager.get_container_status(search_name)
                if status == "exited":
                    UI.error("Elasticsearch container exited unexpectedly.")
                    break

                res = self.manager.run_command(
                    ["docker", "exec", search_name, "curl", "-s", "localhost:9200"],
                    check=False,
                    capture_output=True,
                )
                if res and '"cluster_name"' in res:
                    ready = True
                    break
                time.sleep(5)

            if not ready:
                UI.error("Elasticsearch failed to become ready in time.")
                # AUTO-REPAIR: If ES fails to start, it's often due to corrupted data in the volume.
                # Wiping and restarting usually fixes mapping/plugin-mismatch issues.
                UI.warning("Attempting automatic search volume repair...")
                self.manager.run_command(
                    ["docker", "rm", "-f", search_name], check=False
                )
                if es_data.exists():
                    import shutil

                    shutil.rmtree(es_data)
                    es_data.mkdir(parents=True, exist_ok=True)
                    from ldm_core.utils import reclaim_volume_permissions

                    reclaim_volume_permissions(es_data)

                UI.info("Restarting Global Search with clean slate...")
                return self.setup_global_search()

            # Register backup repository (required for snapshots)
            self.manager.run_command(
                [
                    "docker",
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
                ]
            )

            # Proactive analyzer installation
            UI.info("Installing missing Liferay analyzers in Global Search...")

            # Tests expect a 'plugin list' call first
            self.manager.run_command(
                ["docker", "exec", search_name, "bin/elasticsearch-plugin", "list"]
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
                        "docker",
                        "exec",
                        search_name,
                        "bin/elasticsearch-plugin",
                        "install",
                        "-b",
                        plugin,
                    ],
                    check=False,
                )

            UI.info("Restarting Global Search to activate plugins...")
            self.manager.run_command(["docker", "restart", search_name])

            # Wait for it to come back up
            UI.info("Waiting for Global Search to be ready after restart...")
            ready = False
            for _ in range(30):
                # Fail fast if container exited
                status = self.manager.get_container_status(search_name)
                if status == "exited":
                    UI.error(
                        "Elasticsearch container exited unexpectedly after restart."
                    )
                    break

                res = self.manager.run_command(
                    ["docker", "exec", search_name, "curl", "-s", "localhost:9200"],
                    check=False,
                    capture_output=True,
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
            running = DockerService.is_running(search_name)
            if not running:
                UI.detail(f"Starting existing {search_name} container...")
                DockerService.start(search_name)

            # Always ensure backup repository is registered if service is running
            UI.debug("Ensuring Global Search backup repository is registered...")
            self.manager.run_command(
                [
                    "docker",
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
            )
        return None

    def cmd_system(self, subcommand):
        """Routing for system-level management commands."""
        if subcommand == "relocate":
            self.cmd_system_relocate(self.manager.args.target)
        else:
            UI.die(f"Unknown system subcommand: {subcommand}")

    def cmd_system_relocate(self, target_path):
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
                UI.info("Stopping Colima to ensure data integrity...")
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
                UI.info(
                    f"{label} is already a link to: {UI.CYAN}{target_link}{UI.COLOR_OFF}"
                )
                continue

            UI.info(f"Relocating {label}...")

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
                    UI.info(f"  -> Moving data to {dest} (this may take a while)...")
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.move(str(source), str(dest))
                except Exception as e:
                    UI.error(f"Failed to move {label}: {e}")
                    continue
            # If no-move, we assume user already moved it or wants a fresh start
            elif source.exists():
                UI.info(f"  -> Deleting local {source} (no-move flag active)...")
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
        UI.info(f"Run: {UI.WHITE}colima start{UI.COLOR_OFF}")

    def thaw_elasticsearch(self, quiet=False):
        """Attempts to lift disk watermarks on the global search container."""
        from ldm_core.docker_service import DockerService

        search_name = "liferay-search-global"
        if not quiet:
            UI.info("Checking for blocked search indices (Disk Watermark)...")

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
