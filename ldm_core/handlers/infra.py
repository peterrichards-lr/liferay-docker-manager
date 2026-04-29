import os
import time
import json
import shutil
from ldm_core.ui import UI
from ldm_core.handlers.base import BaseHandler
from ldm_core.utils import (
    get_actual_home,
    get_compose_cmd,
    get_docker_socket_path,
)


class InfraHandler(BaseHandler):
    """Mixin for global infrastructure management (Traefik, Global Search)."""

    def __init__(self, args=None):
        super().__init__(args)

    def cmd_infra_setup(self):
        """Sets up the global infrastructure (Traefik, Search)."""
        import sys

        if not self.check_docker():
            UI.die("Docker is not running.")
        resolved_ip = (
            "0.0.0.0"  # nosec B104
            if sys.platform == "darwin"
            else self.get_resolved_ip("localhost")
        )
        self.setup_infrastructure(resolved_ip, 443, use_ssl=True)
        UI.success("Infrastructure setup complete.")

    def setup_infrastructure(self, resolved_ip, ssl_port, use_ssl=True):
        """Initializes global Traefik proxy and search services."""
        self._ensure_network()
        if not use_ssl:
            return True

        # Docker bridge proxy check (Traefik needs to talk to Docker socket securely)
        self._ensure_docker_proxy()

        # Orchestrated Global Search (ES8)
        if getattr(self.args, "search", False):
            self.setup_global_search()

        UI.info("Checking infrastructure stack (Traefik SSL Proxy)...")
        infra_compose = self.get_resource_path("infra-compose.yml")
        if not infra_compose:
            UI.die(
                "Infrastructure compose file 'infra-compose.yml' not found in resources."
            )

        # Start infrastructure
        env = self._get_infra_env(resolved_ip, ssl_port)

        self.run_command(
            get_compose_cmd()
            + ["-f", str(infra_compose), "up", "-d", "--remove-orphans"],
            env=env,
            capture_output=False,
        )
        return True

    def _get_infra_env(self, resolved_ip="127.0.0.1", ssl_port=443):
        """Generates the standard environment variables for the infrastructure stack."""
        actual_home = get_actual_home()
        cert_dir = actual_home / "liferay-docker-certs"

        env = os.environ.copy()
        env["LDM_CERTS_DIR"] = str(cert_dir)
        env["LDM_SSL_PORT"] = str(ssl_port)
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
                self.run_command(["sudo", "chown", "-R", f"{uid}:{gid}", str(path)])
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

        if not cert_file.exists():
            UI.info(
                f"Generating SSL certificates for {UI.CYAN}{host_name}{UI.COLOR_OFF}..."
            )
            try:
                # We use check=False to handle errors manually with better feedback
                res = self.run_command(
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
            except Exception as e:
                UI.error(f"mkcert unexpected error: {e}")
                return False

        # Generate Traefik Dynamic Config for this host
        try:
            config_content = f"""
tls:
  certificates:
    - certFile: /etc/traefik/certs/{host_name}.pem
      keyFile: /etc/traefik/certs/{host_name}-key.pem
"""
            config_file = cert_dir / f"traefik-{host_name}.yml"
            config_file.write_text(config_content)
        except Exception as e:
            UI.error(f"Failed to write Traefik configuration: {e}")
            return False

        return True

    def cmd_infra_down(self):
        """Tears down the global infrastructure (Traefik, Proxy)."""
        UI.warning("Tearing down global infrastructure (Traefik)...")
        infra_compose = self.get_resource_path("infra-compose.yml")
        if not infra_compose:
            UI.die("Infrastructure compose file 'infra-compose.yml' not found.")

        # Down requires the same env as UP to resolve volume paths correctly
        env = self._get_infra_env()
        self.run_command(
            get_compose_cmd() + ["-f", str(infra_compose), "down", "-v"],
            env=env,
            capture_output=False,
        )

        # Also stop the docker socket proxy and global search
        for container in ["liferay-docker-proxy", "liferay-search-global"]:
            self.run_command(
                ["docker", "stop", container], check=False, capture_output=True
            )
            self.run_command(
                ["docker", "rm", container], check=False, capture_output=True
            )
        UI.success("Infrastructure teardown complete.")

    def cmd_infra_restart(self):
        """Restarts the global infrastructure services."""
        UI.info("Restarting Global Infrastructure...")
        self.cmd_infra_down()
        self.cmd_infra_setup()

    def _ensure_network(self):
        """Ensures the standard 'liferay-net' Docker network exists."""
        networks = self.run_command(
            ["docker", "network", "ls", "--format", "{{.Name}}"]
        )
        if "liferay-net" not in (networks or ""):
            UI.info("Creating Docker network: liferay-net")
            self.run_command(["docker", "network", "create", "liferay-net"])

    def _ensure_docker_proxy(self):
        """Ensures a safe Docker socket proxy is running for Traefik."""
        container_name = "liferay-docker-proxy"
        # Check if it exists at all (running or stopped)
        exists = self.run_command(
            ["docker", "ps", "-a", "-q", "-f", f"name={container_name}"]
        )

        if not exists:
            UI.info("Starting Docker socket bridge...")
            socket_path = get_docker_socket_path()

            # Hardening for Colima/Lima:
            if (
                "colima" in str(socket_path).lower()
                or ".lima" in str(socket_path).lower()
            ):
                UI.debug("Colima/Lima detected. Using standard internal socket path.")
                socket_path = "/var/run/docker.sock"

            self.run_command(
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
            running = self.run_command(
                ["docker", "ps", "-q", "-f", f"name={container_name}"]
            )
            if not running:
                UI.info("Starting existing Docker socket bridge...")
                self.run_command(["docker", "start", container_name])

    def setup_global_search(self):
        """Ensures the global ES8 search service is running."""
        search_name = "liferay-search-global"
        exists = self.run_command(
            ["docker", "ps", "-a", "-q", "-f", f"name={search_name}"]
        )

        if not exists:
            UI.info("Initializing Global Search (ES8) container...")
            home = get_actual_home()
            es_data = home / ".ldm" / "infra" / "search" / "data"
            es_backup = home / ".ldm" / "infra" / "search" / "backup"
            es_data.mkdir(parents=True, exist_ok=True)
            es_backup.mkdir(parents=True, exist_ok=True)

            # Fix permissions for Linux/CI (ES runs as UID 1000, we ensure world-writable or chowned)
            # Reclamation via Docker container ensures it works even if files are owned by root
            self._reclaim_permissions(es_data)
            self._reclaim_permissions(es_backup)

            # Persistent ES8 instance matching Liferay requirements
            from ldm_core.constants import ELASTICSEARCH_VERSION

            self.run_command(
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
                    "ES_JAVA_OPTS=-Xms1g -Xmx1g",
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
                res = self.run_command(
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
                self.run_command(["docker", "rm", "-f", search_name], check=False)
                if es_data.exists():
                    import shutil

                    shutil.rmtree(es_data)
                    es_data.mkdir(parents=True, exist_ok=True)
                    self._reclaim_permissions(es_data)

                UI.info("Restarting Global Search with clean slate...")
                return self.setup_global_search()

            # Register backup repository (required for snapshots)
            self.run_command(
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
            self.run_command(
                ["docker", "exec", search_name, "bin/elasticsearch-plugin", "list"]
            )

            analyzers = [
                "analysis-icu",
                "analysis-kuromoji",
                "analysis-smartcn",
                "analysis-stempel",
            ]
            for plugin in analyzers:
                self.run_command(
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
            self.run_command(["docker", "restart", search_name])

            # Wait for it to come back up
            UI.info("Waiting for Global Search to be ready after restart...")
            ready = False
            for _ in range(30):
                res = self.run_command(
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
            running = self.run_command(
                ["docker", "ps", "-q", "-f", f"name={search_name}"]
            )
            if not running:
                UI.info(f"Starting existing {search_name} container...")
                self.run_command(["docker", "start", search_name])

            # Always ensure backup repository is registered if service is running
            UI.debug("Ensuring Global Search backup repository is registered...")
            self.run_command(
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
