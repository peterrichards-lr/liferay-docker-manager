import os
import re
import json
import time
import shutil
import platform
import subprocess
import socket
import hashlib
import webbrowser
import sys
from pathlib import Path
from datetime import datetime
from ldm_core.ui import UI
from ldm_core.constants import (
    PROJECT_META_FILE,
    SCRIPT_DIR,
    ELASTICSEARCH_VERSION,
    TAG_PATTERN,
    API_BASE_PORTAL,
    API_BASE_DXP,
)
from ldm_core.utils import (
    run_command,
    get_actual_home,
    get_docker_socket_path,
    dict_to_yaml,
    discover_latest_tag,
)


class StackHandler:
    """Mixin for stack management commands (run, stop, restart, down, sync)."""

    def check_mkcert(self):
        mkcert_bin = shutil.which("mkcert")
        if not mkcert_bin:
            UI.error("mkcert is not installed. Fast-failing SSL setup.")
            UI.info(
                "Installation Guide: https://github.com/FiloSottile/mkcert#installation"
            )
            UI.die("Please install mkcert and try again.")

        try:
            subprocess.run([mkcert_bin, "-version"], capture_output=True, check=True)  # nosec B603
        except Exception:
            UI.die("Failed to execute mkcert. Check your installation.")

        try:
            ca_root = subprocess.run(  # nosec B603
                [mkcert_bin, "-CAROOT"], capture_output=True, text=True, check=True
            ).stdout.strip()

            if not ca_root or not os.path.exists(ca_root) or not os.listdir(ca_root):
                raise ValueError("Root CA not found")
        except Exception:
            UI.error("mkcert Root CA is not installed on this host.")
            print(
                f"\n{UI.BYELLOW}ACTION REQUIRED:{UI.COLOR_OFF} Please run the following command to trust mkcert:"
            )
            print(f"{UI.CYAN}mkcert -install{UI.COLOR_OFF}\n")
            UI.die("Root CA trust is required for automated SSL.")

    def setup_ssl(self, paths, host_name):
        if not host_name or host_name == "localhost":
            return False

        actual_home = get_actual_home()
        cert_dir = actual_home / ".liferay_docker_certs"
        cert_dir.mkdir(parents=True, exist_ok=True)

        cert_file = cert_dir / f"{host_name}.pem"
        key_file = cert_dir / f"{host_name}-key.pem"
        wildcard_host = f"*.{host_name}"

        needs_gen = not cert_file.exists() or getattr(self.args, "force_ssl", False)

        if not needs_gen:
            openssl_bin = shutil.which("openssl")
            if openssl_bin:
                try:
                    res = subprocess.run(  # nosec B603
                        [openssl_bin, "x509", "-in", str(cert_file), "-text", "-noout"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )

                    if f"DNS:{wildcard_host}" not in res.stdout:
                        if self.verbose:
                            UI.info(
                                f"Certificate for {host_name} missing wildcard SAN. Regenerating..."
                            )
                        needs_gen = True
                except Exception:
                    needs_gen = True

        if needs_gen:
            if self.verbose:
                UI.info(f"Generating SSL wildcard certificate for {host_name}...")
            if cert_file.exists():
                os.remove(cert_file)
            if key_file.exists():
                os.remove(key_file)

            real_user = os.environ.get("SUDO_USER") or os.environ.get("USER")
            cmd = [
                "sudo",
                "-u",
                real_user,
                "mkcert",
                "-cert-file",
                str(cert_file),
                "-key-file",
                str(key_file),
                host_name,
                wildcard_host,
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                UI.error(f"mkcert failed: {e.stderr.decode().strip()}")
                return False

        os.chmod(cert_file, 0o644)
        os.chmod(key_file, 0o644)
        config_path = cert_dir / f"traefik-{host_name}.yml"
        config_path.write_text(
            f"tls:\n  certificates:\n    - certFile: /etc/traefik/certs/{host_name}.pem\n"
            f"      keyFile: /etc/traefik/certs/{host_name}-key.pem\n"
        )
        os.chmod(config_path, 0o644)
        return True

    def get_docker_socket_params(self):
        system = sys.platform
        if system == "darwin":
            real_socket = get_actual_home() / ".docker/run/docker.sock"
            path = str(real_socket) if real_socket.exists() else "/var/run/docker.sock"
            return (
                ["-v", f"{path}:/var/run/docker.sock:ro"],
                "--providers.docker.endpoint=unix:///var/run/docker.sock",
            )
        elif system == "win32":
            return (
                ["-v", "//./pipe/docker_engine://./pipe/docker_engine"],
                "--providers.docker.endpoint=npipe:////./pipe/docker_engine",
            )
        else:
            return (
                ["-v", "/var/run/docker.sock:/var/run/docker.sock:ro"],
                "--providers.docker.endpoint=unix:///var/run/docker.sock",
            )

    def setup_global_search(self):
        search_name = "liferay-search-global"
        custom_image_name = f"liferay-search-global:{ELASTICSEARCH_VERSION}"
        target_image_base = (
            f"docker.elastic.co/elasticsearch/elasticsearch:{ELASTICSEARCH_VERSION}"
        )

        actual_home = get_actual_home()
        search_backup_dir = actual_home / ".liferay_docker_search_backups"
        search_backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Bandit: B103 (chmod 777) is needed here as Elasticsearch container
            # runs with a different UID and needs write access to the host volume.
            os.chmod(search_backup_dir, 0o777)  # nosec B103
        except Exception:
            pass

        inspect = run_command(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}} {{.Config.Image}} {{.HostConfig.PortBindings}} {{.HostConfig.Memory}} {{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}",
                search_name,
            ],
            check=False,
        )

        if inspect:
            parts = inspect.split(" ", 4)
            if (
                parts[0] == "true"
                and parts[1] == custom_image_name
                and "9200/tcp" in parts[2]
                and parts[3] == "536870912"
                and f"{search_backup_dir.as_posix()}:/usr/share/elasticsearch/backup"
                in (parts[4] if len(parts) > 4 else "")
            ):
                return True
            if self.verbose:
                UI.info("Updating Global Search service...")
            run_command(["docker", "rm", "-f", search_name])

        if not run_command(["docker", "images", "-q", custom_image_name], check=False):
            search_build_dir = SCRIPT_DIR / "temp" / "search"
            search_build_dir.mkdir(parents=True, exist_ok=True)
            (search_build_dir / "Dockerfile").write_text(
                f"FROM {target_image_base}\n"
                "RUN bin/elasticsearch-plugin install --batch analysis-icu analysis-kuromoji analysis-smartcn analysis-stempel\n"
            )
            run_command(
                ["docker", "build", "-t", custom_image_name, "."],
                cwd=str(search_build_dir),
                capture_output=False,
            )

        publish_args = ["-p", "9200:9200"] if self.is_port_available(9200) else []
        run_command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                search_name,
                "--network",
                "liferay-net",
                "-v",
                f"{search_backup_dir.as_posix()}:/usr/share/elasticsearch/backup",
            ]
            + publish_args
            + [
                "-e",
                "discovery.type=single-node",
                "-e",
                "xpack.security.enabled=false",
                "-e",
                "ES_JAVA_OPTS=-Xms512m -Xmx512m",
                "-e",
                "indices.query.bool.max_clause_count=4096",
                "-e",
                "path.repo=/usr/share/elasticsearch/backup",
                custom_image_name,
            ]
        )
        return True

    def setup_infrastructure(self, resolved_ip, ssl_port, paths, use_ssl=True):
        run_command(["docker", "network", "create", "liferay-net"], check=False)
        if not use_ssl:
            return True

        actual_home = get_actual_home()
        global_cert_dir = actual_home / ".liferay_docker_certs"
        global_cert_dir.mkdir(parents=True, exist_ok=True)

        # 3. Start the Socket Proxy ONLY if the standard socket is missing (Docker Desktop Mac isolation)
        api_proxy = "docker-socket-proxy"
        standard_socket = Path("/var/run/docker.sock")
        is_darwin = platform.system() == "darwin"

        # If standard socket exists and we can talk to it, we don't need the bridge
        needs_bridge = is_darwin and not standard_socket.exists()

        if needs_bridge:
            if not run_command(["docker", "ps", "-q", "-f", f"name=^{api_proxy}$"]):
                run_command(["docker", "rm", "-f", api_proxy], check=False)
                real_socket_path = f"{actual_home}/.docker/run/docker.sock"
                if self.verbose:
                    UI.info("Starting Docker Socket Proxy bridge for macOS...")
                run_command(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        api_proxy,
                        "--network",
                        "liferay-net",
                        "-v",
                        f"{real_socket_path}:/var/run/docker.sock:ro",
                        "alpine/socat",
                        "TCP-LISTEN:2375,fork,reuseaddr",
                        "UNIX-CONNECT:/var/run/docker.sock",
                    ]
                )

        # 4. Start Traefik if not running
        proxy_name = "liferay-proxy-global"
        if not run_command(["docker", "ps", "-q", "-f", f"name=^{proxy_name}$"]):
            run_command(["docker", "rm", "-f", proxy_name], check=False)

            # Determine Traefik's Docker endpoint
            if needs_bridge:
                endpoint = f"tcp://{api_proxy}:2375"
            else:
                endpoint = "unix:///var/run/docker.sock"

            traefik_cmd = [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                proxy_name,
                "--network",
                "liferay-net",
                "-p",
                f"{resolved_ip}:{ssl_port}:443",
                "-e",
                "DOCKER_API_VERSION=1.44",
                "-v",
                f"{global_cert_dir.as_posix()}:/etc/traefik/certs:ro",
            ]

            # Mount the real socket if not using the bridge
            if not needs_bridge:
                traefik_cmd.extend(
                    ["-v", "/var/run/docker.sock:/var/run/docker.sock:ro"]
                )

            traefik_cmd.extend(
                [
                    "traefik:v3.6.1",
                    "--providers.docker=true",
                    f"--providers.docker.endpoint={endpoint}",
                    "--providers.docker.exposedbydefault=false",
                    "--entrypoints.websecure.address=:443",
                    "--providers.file.directory=/etc/traefik/certs",
                    "--providers.file.watch=true",
                ]
            )
            run_command(traefik_cmd)
        return True

    def check_hostname(self, host_name, silent=False, expected_ip=None):
        if not host_name or host_name == "localhost":
            return True
        ip = self.get_resolved_ip(host_name)
        if not ip:
            UI.error(f"Hostname '{host_name}' could not be resolved.")
            target_ip = expected_ip or "127.0.0.1"
            print(
                f"\n{UI.BRED}IMPORTANT:{UI.COLOR_OFF} Map '{host_name}' to {target_ip} in your hosts file."
            )
            if self.non_interactive:
                sys.exit(1)
            return UI.ask("Continue anyway? (y/n/q)", "N").upper() == "Y"
        if not (ip.startswith("127.") or ip in ["::1", "0:0:0:0:0:0:0:1"]):
            UI.error(f"Hostname '{host_name}' resolves to {ip} (not loopback).")
            if self.non_interactive:
                sys.exit(1)
            return UI.ask("Continue anyway? (y/n/q)", "N").upper() == "Y"
        if not self.is_bindable(ip):
            UI.error(f"IP {ip} not available for binding.")
            self.print_macos_alias_advice(ip)
            if self.non_interactive:
                sys.exit(1)
            if not UI.ask("Continue anyway? (y/n/q)", "N").upper() == "Y":
                return False
        return True

    def print_macos_alias_advice(self, ip):
        if platform.system() == "darwin":
            print(
                f"\n{UI.BRED}OSX DETECTED:{UI.COLOR_OFF} Alias this IP to loopback: {UI.CYAN}sudo ifconfig lo0 alias {ip} up{UI.COLOR_OFF}\n"
            )

    def wait_for_container_stop(self, container_name, timeout=30):
        start_time = time.time()
        while time.time() - start_time < timeout:
            inspect_raw = run_command(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Status}} {{.State.Running}}",
                    container_name,
                ],
                check=False,
            )
            if not inspect_raw:
                return True
            parts = inspect_raw.split()
            if parts[0] == "exited" and parts[1].lower() == "false":
                return True
            time.sleep(1)
        return False

    def _map_probe_to_healthcheck(self, probe, default_port=8080):
        if not probe or "httpGet" not in probe:
            return None
        http = probe["httpGet"]
        return {
            "test": [
                "CMD",
                "curl",
                "-f",
                f"http://localhost:{http.get('port', default_port)}{http.get('path', '/')}",
            ],
            "interval": f"{probe.get('periodSeconds', 10)}s",
            "timeout": f"{probe.get('timeoutSeconds', 5)}s",
            "retries": probe.get("failureThreshold", 3),
            "start_period": f"{probe.get('initialDelaySeconds', 0)}s"
            if probe.get("initialDelaySeconds")
            else None,
        }

    def write_docker_compose(self, paths, config):
        container_name, image_tag, port = (
            config["container_name"],
            config["image_tag"],
            config["port"],
        )
        resolved_ip, use_ssl, ssl_port, host_name = (
            config.get("resolved_ip", "127.0.0.1"),
            config.get("use_ssl", False),
            config.get("ssl_port", 443),
            config.get("host_name", "localhost"),
        )
        env_args, custom_env, use_shared_search = (
            config.get("env_args", []),
            config.get("custom_env", {}),
            config.get("use_shared_search", False),
        )
        mount_logs, gogo_port = config.get("mount_logs", False), config.get("gogo_port")
        db_type, db_name, db_user, db_pass = (
            config.get("db_type"),
            config.get("db_name"),
            config.get("db_user"),
            config.get("db_pass"),
        )
        scale_map = {
            k.replace("scale_", ""): int(v)
            for k, v in config.items()
            if k.startswith("scale_") and str(v).isdigit()
        }

        socket_path = get_docker_socket_path()
        liferay_volumes = [
            f"{paths['files'].as_posix()}:/mnt/liferay/files",
            f"{paths['scripts'].as_posix()}:/mnt/liferay/scripts",
            f"{paths['configs'].as_posix()}:/opt/liferay/osgi/configs",
            f"{paths['modules'].as_posix()}:/opt/liferay/osgi/modules",
            f"{paths['marketplace'].as_posix()}:/opt/liferay/osgi/marketplace",
            f"{paths['data'].as_posix()}:/opt/liferay/data",
            f"{paths['deploy'].as_posix()}:/mnt/liferay/deploy",
            f"{paths['cx'].as_posix()}:/opt/liferay/osgi/client-extensions",
            f"{paths['routes'].as_posix()}:/opt/liferay/routes",
            f"{paths['log4j'].as_posix()}:/opt/liferay/osgi/log4j",
            f"{paths['portal_log4j'].as_posix()}/portal-log4j-ext.xml:/opt/liferay/tomcat/webapps/ROOT/WEB-INF/classes/META-INF/portal-log4j-ext.xml",
        ]
        if scale_map.get("liferay", 1) <= 1:
            liferay_volumes.append(
                f"{paths['state'].as_posix()}:/opt/liferay/osgi/state"
            )
            if mount_logs:
                liferay_volumes.append(f"{paths['logs'].as_posix()}:/opt/liferay/logs")

        gogo_env = (
            "0.0.0.0:11311"
            if gogo_port and str(gogo_port).isdigit()
            else "localhost:11311"
        )
        # Use SHA256 for configuration fingerprinting (resolves Bandit B324)
        config_sig = hashlib.sha256(
            json.dumps(custom_env, sort_keys=True).encode()
        ).hexdigest()[:12]

        liferay_env = [
            "LIFERAY_WORKSPACE__HOME__DIR=/opt/liferay",
            f"LIFERAY_LXC__DXP__MAIN__DOMAIN={host_name}",
            f"LIFERAY_LXC__DXP__DOMAINS={host_name}",
            f"LDM_CONFIG_SIGNATURE={config_sig}",
            f"LIFERAY_MODULE__FRAMEWORK__PROPERTIES__OSGI__CONSOLE={gogo_env}",
            f"OSGI_CONSOLE={gogo_env}",
        ]
        if scale_map.get("liferay", 1) > 1:
            liferay_env.extend(
                [
                    "LIFERAY_CLUSTER__LINK__ENABLED=true",
                    "LIFERAY_LUCENE__REPLICATE__WRITE=true",
                ]
            )

        compose = {
            "services": {
                "liferay": {
                    "image": image_tag,
                    "ports": [],
                    "environment": liferay_env,
                    "volumes": liferay_volumes,
                    "networks": ["liferay-net"],
                    "extra_hosts": [f"{host_name}:host-gateway"],
                    "stop_grace_period": "60s",
                    "labels": [
                        "com.liferay.ldm.managed=true",
                        f"com.liferay.ldm.project={container_name}",
                    ],
                }
            },
            "networks": {"liferay-net": {"external": True}},
        }

        if scale_map.get("liferay", 1) <= 1:
            compose["services"]["liferay"]["container_name"] = container_name

        if db_type in ["postgresql", "mysql"]:
            db_img, db_port = (
                ("postgres:16", "5432")
                if db_type == "postgresql"
                else ("mysql:5.7", "3306")
            )
            compose["services"]["db"] = {
                "image": db_img,
                "container_name": f"{container_name}-db",
                "networks": ["liferay-net"],
                "environment": [
                    f"POSTGRES_DB={db_name}",
                    f"POSTGRES_USER={db_user}",
                    f"POSTGRES_PASSWORD={db_pass}",
                ]
                if db_type == "postgresql"
                else [
                    f"MYSQL_DATABASE={db_name}",
                    f"MYSQL_USER={db_user}",
                    f"MYSQL_PASSWORD={db_pass}",
                    f"MYSQL_ROOT_PASSWORD={db_pass}",
                ],
                "volumes": [
                    f"{paths['root'].as_posix()}/data/db:/var/lib/postgresql/data"
                    if db_type == "postgresql"
                    else f"{paths['root'].as_posix()}/data/db:/var/lib/mysql"
                ],
                "healthcheck": {
                    "test": ["CMD-SHELL", f"pg_isready -U $$POSTGRES_USER -d {db_name}"]
                    if db_type == "postgresql"
                    else [
                        "CMD",
                        "mysqladmin",
                        "ping",
                        "-h",
                        "localhost",
                        f"-u{db_user}",
                        f"-p{db_pass}",
                    ],
                    "interval": "5s",
                    "retries": 5,
                },
                "labels": [
                    "com.liferay.ldm.managed=true",
                    f"com.liferay.ldm.project={container_name}",
                ],
            }
            compose["services"]["liferay"]["depends_on"] = {
                "db": {"condition": "service_healthy"}
            }
            compose["services"]["liferay"]["environment"].extend(
                [
                    f"LIFERAY_JDBC__DEFAULT__URL=jdbc:{db_type}://db:{db_port}/{db_name}"
                    + (
                        "?useUnicode=true&characterEncoding=UTF-8"
                        if db_type == "mysql"
                        else ""
                    ),
                    f"LIFERAY_JDBC__DEFAULT__USERNAME={db_user}",
                    f"LIFERAY_JDBC__DEFAULT__PASSWORD={db_pass}",
                    f"LIFERAY_JDBC__DEFAULT__DRIVER__CLASS__NAME={'org.postgresql.Driver' if db_type == 'postgresql' else 'com.mysql.cj.jdbc.Driver'}",
                ]
            )

        if port and str(port).isdigit() and not use_ssl:
            compose["services"]["liferay"]["ports"].append(f"{resolved_ip}:{port}:8080")
        if gogo_port and str(gogo_port).isdigit():
            compose["services"]["liferay"]["ports"].append(
                f"{resolved_ip}:{gogo_port}:11311"
            )
        if not use_shared_search:
            compose["services"]["liferay"]["ports"].append(f"{resolved_ip}:9201:9201")
        compose["services"]["liferay"]["environment"].extend(env_args)

        if use_ssl:
            compose["services"]["liferay"]["environment"].extend(
                [
                    "LIFERAY_WEB__SERVER__PROTOCOL=https",
                    f"LIFERAY_WEB__SERVER__HTTPS__PORT={ssl_port}",
                ]
            )
            compose["services"]["liferay"]["labels"] = [
                "traefik.enable=true",
                f"traefik.http.routers.{container_name}-main.rule=Host(`{host_name}`)",
                f"traefik.http.routers.{container_name}-main.entrypoints=websecure",
                f"traefik.http.routers.{container_name}-main.tls=true",
                f"traefik.http.routers.{container_name}-main.tls.domains[0].main={host_name}",
                f"traefik.http.routers.{container_name}-main.tls.domains[0].sans=*.{host_name}",
                f"traefik.http.services.{container_name}-main-svc.loadbalancer.server.port=8080",
            ]
            if (
                platform.system() == "darwin"
                and not Path("/var/run/docker.sock").exists()
            ):
                compose["services"]["docker-socket-bridge"] = {
                    "image": "alpine/socat",
                    "container_name": f"{container_name}-socket-bridge",
                    "networks": ["liferay-net"],
                    "command": "TCP-LISTEN:2375,fork,reuseaddr UNIX-CONNECT:/var/run/docker.sock",
                    "volumes": [f"{socket_path}:/var/run/docker.sock:ro"],
                    "environment": ["DOCKER_API_VERSION=1.44"],
                }

        extensions = self.scan_client_extensions(
            paths["root"], paths["cx"], paths["ce_dir"]
        )
        standalone_services = self.scan_standalone_services(paths["root"])
        all_services = extensions + standalone_services
        compose["services"]["liferay"]["environment"].extend(
            self.get_host_passthrough_env(paths, "liferay")
        )

        for ext in all_services:
            if "path" not in ext or ext.get("kind", "Deployment") != "Deployment":
                continue
            ext_id, ext_name, ext_port = ext["id"], ext["name"], ext.get("port", 8080)
            ext_env = [
                f"COM_LIFERAY_LXC_DXP_DOMAINS={host_name}",
                f"COM_LIFERAY_LXC_DXP_MAIN_DOMAIN={host_name}",
                f"LDM_CONFIG_SIGNATURE={config_sig}",
                f"LIFERAY_ROUTES_CLIENT_EXTENSION=/opt/liferay/routes/default/{ext_id}",
                "LIFERAY_ROUTES_DXP=/opt/liferay/routes/default/dxp",
            ]
            ext_env.extend(self.get_host_passthrough_env(paths, ext_id))
            compose["services"][ext_name] = {
                "build": {"context": ext["path"].as_posix()},
                "networks": ["liferay-net"],
                "environment": ext_env,
                "volumes": [f"{paths['routes'].as_posix()}:/opt/liferay/routes"],
                "extra_hosts": [f"{host_name}:host-gateway"],
                "depends_on": ["liferay"],
                "restart": "on-failure",
                "labels": [
                    "com.liferay.ldm.managed=true",
                    f"com.liferay.ldm.project={container_name}",
                ],
            }
            if scale_map.get(ext_name, 1) <= 1:
                compose["services"][ext_name]["container_name"] = (
                    f"{container_name}-{ext_name}"
                )

            probe = ext.get("readinessProbe") or ext.get("livenessProbe")
            if probe:
                compose["services"][ext_name]["healthcheck"] = (
                    self._map_probe_to_healthcheck(probe, ext_port)
                )
            if ext.get("memory"):
                compose["services"][ext_name]["deploy"] = {
                    "resources": {"limits": {"memory": f"{ext['memory']}m"}}
                }
            if use_ssl and host_name != "localhost" and ext.get("has_load_balancer"):
                ext_host = f"{ext_id}.{host_name}"
                compose["services"][ext_name]["labels"] = [
                    "traefik.enable=true",
                    f"traefik.http.routers.{container_name}-{ext_name}.rule=Host(`{ext_host}`)",
                    f"traefik.http.routers.{container_name}-{ext_name}.entrypoints=websecure",
                    f"traefik.http.routers.{container_name}-{ext_name}.tls=true",
                    f"traefik.http.routers.{container_name}-{ext_name}.tls.domains[0].main={host_name}",
                    f"traefik.http.services.{container_name}-{ext_name}.loadbalancer.server.port={ext_port}",
                ]
                ext["url"] = f"https://{ext_host}"
            else:
                ext["url"] = f"http://{ext_name}:{ext_port} (Internal)"

        yaml_content = "# Generated by Liferay Docker Manager\n" + dict_to_yaml(compose)
        has_changed = (
            not paths["compose"].exists()
            or paths["compose"].read_text() != yaml_content
        )
        if has_changed:
            paths["compose"].write_text(yaml_content)
            UI.success(f"Generated {paths['compose'].name}")
        return all_services, has_changed

    def cmd_scale(self, project_id, scale_args):
        project_path = self.detect_project_path(project_id)
        if not project_path:
            UI.die("Project not found.")

        meta = self.read_meta(project_path / PROJECT_META_FILE)

        for arg in scale_args:
            if "=" not in arg:
                UI.error(f"Invalid scale argument: {arg}. Expected service=number")
                continue
            service, count = arg.split("=", 1)
            if not count.isdigit():
                UI.error(f"Invalid scale count for {service}: {count}")
                continue
            meta[f"scale_{service}"] = count

        self.write_meta(project_path / PROJECT_META_FILE, meta)
        UI.success(f"Updated scale factors for project {project_path.name}")

        # Trigger regeneration and restart
        self.cmd_run(project_id)

    def sync_stack(
        self,
        paths,
        project_meta,
        follow=False,
        rebuild=False,
        show_summary=True,
        no_up=False,
    ):
        self.migrate_layout(paths)
        tag, host_name = (
            project_meta.get("tag"),
            project_meta.get("host_name", "localhost"),
        )
        resolved_ip, port = (
            self.get_resolved_ip(host_name) or "127.0.0.1",
            int(project_meta.get("port", 8080)),
        )
        use_ssl, ssl_port = (
            str(project_meta.get("ssl")).lower() == "true",
            int(project_meta.get("ssl_port", 443)),
        )
        container_name, image_type = (
            project_meta.get("container_name"),
            project_meta.get("image_type", "dxp"),
        )
        image_tag = project_meta.get("image_tag") or f"liferay/{image_type}:{tag}"
        if str(project_meta.get("mount_logs")).lower() == "true":
            paths["logs"].mkdir(parents=True, exist_ok=True)

        gradle_props = paths["root"] / "gradle.properties"
        if gradle_props.exists():
            content = gradle_props.read_text()
            ws_home = f"liferay.workspace.home.dir={paths['root'].resolve()}"
            pattern = re.compile(
                r"^\s*liferay\.workspace\.home\.dir\s*=.*$", re.MULTILINE
            )
            content = (
                pattern.sub(ws_home, content)
                if pattern.search(content)
                else content + f"\n{ws_home}\n"
            )
            gradle_props.write_text(content)

        jvm_opts = []
        if str(project_meta.get("disable_zip64")).lower() == "true":
            jvm_opts.append("-Djdk.util.zip.disableZip64ExtraFieldValidation=true")
        valid_hosts = ",".join(
            sorted(list({"localhost", "127.0.0.1", "[::1]", host_name, resolved_ip}))
        )
        if host_name != "localhost":
            jvm_opts.append(
                f"-Dorg.apache.catalina.SESSION_COOKIE_NAME=LFR_SESSION_ID_{host_name.replace('.', '_').replace('-', '_')}"
            )
        env_args = [f"LIFERAY_VIRTUAL__HOSTS__VALID__HOSTS={valid_hosts}"]
        if jvm_opts:
            env_args.append(f"LIFERAY_JVM_OPTS={' '.join(jvm_opts)}")

        use_shared_search = self.parse_version(tag) >= (2025, 1, 0) and not getattr(
            self.args, "sidecar", False
        )
        if use_shared_search:
            search_url = "http://liferay-search-global:9200"
            configs = [
                (
                    "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConfiguration.config",
                    f'operationMode="REMOTE"\nremoteClusterConnectionId="REMOTE"\nindexNamePrefix="{container_name}-"\n',
                ),
                (
                    "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConnectionConfiguration.config",
                    f'active=B"true"\nconnectionId="REMOTE"\nnetworkHostAddresses=["{search_url}"]\n',
                ),
                (
                    "com.liferay.portal.bundle.blacklist.internal.configuration.BundleBlacklistConfiguration.config",
                    'blacklistBundleSymbolicNames=[\\\n "com.liferay.portal.search.opensearch.api",\\\n "com.liferay.portal.search.opensearch.impl"\\\n]\n',
                ),
            ]
            for cfg, content in configs:
                (paths["configs"] / cfg).write_text(content)

        self.sync_common_assets(
            paths, host_updates={"web.server.protocol": "https" if use_ssl else "http"}
        )
        self.sync_logging(paths)
        if use_ssl:
            self.setup_ssl(paths, host_name)
        self.setup_infrastructure(resolved_ip, ssl_port, paths, use_ssl=use_ssl)
        if use_shared_search:
            self.setup_global_search()

        config_payload = {
            "container_name": container_name,
            "image_tag": image_tag,
            "port": port,
            "resolved_ip": resolved_ip,
            "use_ssl": use_ssl,
            "ssl_port": ssl_port,
            "host_name": host_name,
            "env_args": env_args,
            "custom_env": json.loads(project_meta.get("custom_env", "{}")),
            "use_shared_search": use_shared_search,
            "mount_logs": str(project_meta.get("mount_logs")).lower() == "true",
            "gogo_port": getattr(self.args, "gogo_port", None)
            or project_meta.get("gogo_port"),
            "db_type": project_meta.get("db_type"),
            "db_name": project_meta.get("db_name", "lportal"),
            "db_user": project_meta.get("db_user", "liferay"),
            "db_pass": project_meta.get("db_pass", "liferay"),
        }
        for k, v in project_meta.items():
            if k.startswith("scale_"):
                config_payload[k] = v

        extensions, has_changed = self.write_docker_compose(paths, config_payload)

        project_meta.update({"last_run": datetime.now().isoformat()})
        self.write_meta(paths["root"] / PROJECT_META_FILE, project_meta)

        if (
            not rebuild
            and not has_changed
            and run_command(["docker", "ps", "-q", "-f", f"name=^{container_name}$"])
        ):
            UI.success("Hot-deployed changes.")
            return
        if no_up:
            return

        scale_args = []
        for k, v in project_meta.items():
            if k.startswith("scale_") and str(v).isdigit():
                service = k.replace("scale_", "")
                scale_args.extend(["--scale", f"{service}={v}"])

        cmd = ["docker", "compose", "up", "-d"] + scale_args
        if rebuild:
            cmd.append("--build")
        run_command(cmd, capture_output=False, cwd=str(paths["root"]))
        if follow:
            run_command(
                ["docker", "compose", "logs", "-f"],
                capture_output=False,
                cwd=str(paths["root"]),
            )
        else:
            self.print_success_summary(
                paths,
                project_meta,
                extensions,
                show_summary=show_summary,
                no_wait=no_up or getattr(self.args, "no_wait", False),
            )

    def print_success_summary(
        self, paths, project_meta, extensions, show_summary=True, no_wait=False
    ):
        host_name, port = (
            project_meta.get("host_name", "localhost"),
            project_meta.get("port", "8080"),
        )
        ssl, ssl_port = (
            str(project_meta.get("ssl")).lower() == "true",
            project_meta.get("ssl_port", "443"),
        )
        access_url = f"{'https' if ssl else 'http'}://{host_name}{f':{ssl_port}' if ssl and ssl_port != '443' else (f':{port}' if not ssl else '')}"

        if not no_wait:
            UI.info("Waiting for Liferay to start...")
            start_time = time.time()
            is_ready = False
            while time.time() - start_time < int(project_meta.get("timeout", 600)):
                try:
                    res = run_command(["curl", "-k", "-I", access_url], check=False)
                    if res and ("200" in res or "302" in res):
                        is_ready = True
                        break
                except Exception:
                    pass
                sys.stdout.write(".")
                sys.stdout.flush()
                time.sleep(10)
            print("\n")
            if is_ready:
                UI.success(f"Stack {project_meta.get('container_name')} is READY!")
            else:
                UI.error("Startup timed out.")
        else:
            UI.info("Skipping startup verification.")

        print(f"  {UI.WHITE}🌐 Liferay:        {UI.CYAN}{access_url}{UI.COLOR_OFF}")
        for e in [e for e in extensions if "url" in e]:
            print(f"     - {UI.WHITE}{e['id']:<14} {UI.CYAN}{e['url']}")

        if not show_summary:
            return

        launch_url, found_prop = None, False
        if (paths["files"] / "portal-ext.properties").exists():
            for line in (
                (paths["files"] / "portal-ext.properties").read_text().splitlines()
            ):
                if line.strip().startswith("browser.launcher.url"):
                    found_prop = True
                    if "=" in line:
                        val = line.split("=", 1)[1].strip()
                        if val:
                            launch_url = re.sub(r"https?://[^/]+", access_url, val)
                    break
        if not found_prop:
            launch_url = f"{access_url}/web/guest/home"
        if launch_url:
            UI.info(f"Launching browser: {launch_url}")
            webbrowser.open(launch_url)

    def cmd_run(self, is_restart=False):
        if getattr(self.args, "select", False):
            roots = self.find_dxp_roots()
            UI.heading("Available Projects")
            for i, r in enumerate(roots):
                print(
                    f"[{i + 1}] {r['path'].name} [{UI.CYAN}{r['version']}{UI.COLOR_OFF}]"
                )
            choice = UI.ask("Select index", "1")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(roots):
                    self.args.project, self.args.tag = (
                        str(roots[idx]["path"]),
                        roots[idx]["version"],
                    )
            except Exception:
                UI.die("Invalid selection.")

        project_id = self.args.project or self.detect_project_path()
        project_meta = (
            self.read_meta(Path(project_id) / PROJECT_META_FILE)
            if project_id and Path(project_id).exists()
            else {}
        )

        # Handle Samples Switch
        is_sample_run = getattr(self.args, "samples", False)
        sample_meta = {}
        if is_sample_run:
            sample_meta_file = SCRIPT_DIR / "samples" / "metadata.json"
            if sample_meta_file.exists():
                sample_meta = json.loads(sample_meta_file.read_text())

        host_name = self.args.host_name or project_meta.get("host_name") or "localhost"
        if host_name != "localhost" and not self.check_hostname(host_name):
            sys.exit(1)

        port = self.args.port or project_meta.get("port") or 8080
        use_ssl = getattr(self.args, "ssl", None)
        if use_ssl is None:
            use_ssl = str(project_meta.get("ssl")).lower() == "true" or (
                host_name != "localhost"
            )
        if use_ssl:
            self.check_mkcert()

        tag = self.args.tag or project_meta.get("tag")
        if not tag:
            ans = (
                self.args.release_type
                or (sample_meta.get("reference_tag") if is_sample_run else None)
                or UI.ask("Release type (any|u|lts|qr) or prefix", "any")
            )
            api = API_BASE_PORTAL if self.args.portal else API_BASE_DXP
            tag = (
                ans
                if re.match(TAG_PATTERN, ans)
                else discover_latest_tag(
                    api,
                    ans,
                    datetime.now().strftime("%Y")
                    if ans not in ["u", "lts", "any", "qr"]
                    else None,
                )
            )
            if not tag:
                UI.die(f"Could not discover tag for '{ans}'.")
            if not self.non_interactive:
                default_tag = (
                    sample_meta.get("reference_tag", tag) if is_sample_run else tag
                )
                tag = UI.ask("Enter Liferay Tag", default_tag)

        # Version Check for Samples
        if is_sample_run and sample_meta.get("reference_tag"):
            ref_v = self.parse_version(sample_meta["reference_tag"])
            user_v = self.parse_version(tag)
            if user_v < ref_v:
                UI.warning(
                    f"Samples require Liferay {sample_meta['reference_tag']} or later. Skipping."
                )
                is_sample_run = False
            elif user_v > ref_v:
                UI.warning(
                    "Applying samples to newer Liferay. Database upgrade will be attempted."
                )

        project_id = self.args.project or UI.ask(
            "Project Path", project_id or f"./{tag}"
        )
        paths = self.setup_paths(project_id)

        if is_sample_run:
            self.args.db = "postgresql"
            self.sync_samples(paths)
            if user_v > ref_v:
                self.update_portal_ext(
                    paths["files"] / "portal-ext.properties",
                    {
                        "upgrade.database.auto.run": "true",
                        "upgrade.report.enabled": "true",
                    },
                )

        container_name = (
            self.args.container
            or project_meta.get("container_name")
            or paths["root"].name.replace(".", "-")
        )

        if not (paths["root"] / PROJECT_META_FILE).exists() or (
            not self.non_interactive
            and not is_restart
            and UI.ask(f"Project '{container_name}' exists. Re-init?", "N").upper()
            == "Y"
        ):
            for p in [
                v for v in paths.values() if isinstance(v, Path) and not v.suffix
            ]:
                p.mkdir(parents=True, exist_ok=True)
            (paths["routes"] / "default" / "dxp").mkdir(parents=True, exist_ok=True)

        project_meta.update(
            {
                "project_name": container_name,
                "tag": tag,
                "host_name": host_name,
                "port": str(port),
                "ssl": str(use_ssl),
                "container_name": container_name,
                "db_type": self.args.db or project_meta.get("db_type"),
                "mount_logs": str(
                    getattr(self.args, "mount_logs", False)
                    or project_meta.get("mount_logs", "False")
                ).lower(),
            }
        )

        # Trigger Auto-Restore for Samples
        if is_sample_run:
            # We must ensure DB/ES are up before restore
            self.sync_stack(paths, project_meta, no_up=True, show_summary=False)
            UI.info("Preparing sample data...")
            run_command(["docker", "compose", "up", "-d", "db"], cwd=str(paths["root"]))
            # Give DB a moment
            time.sleep(5)
            # Restore Sample Gold
            self.cmd_restore(project_id, auto_index=1)

        self.sync_stack(
            paths,
            project_meta,
            follow=getattr(self.args, "follow", False),
            no_up=getattr(self.args, "no_up", False),
        )

    def cmd_deploy(self, project_id=None, service=None):
        root = self.detect_project_path(project_id)
        if not root:
            return
        paths = self.setup_paths(root)
        meta = self.read_meta(paths["root"] / PROJECT_META_FILE)

        if service:
            UI.heading(f"Deploying service '{service}' to {meta.get('container_name')}")
            cmd = ["docker", "compose", "up", "-d"]
            if getattr(self.args, "rebuild", False):
                cmd.append("--build")
            cmd.append(service)
            run_command(cmd, capture_output=False, cwd=str(root))
        else:
            UI.heading(f"Deploying stack to {meta.get('container_name')}")
            self.sync_stack(
                paths,
                meta,
                rebuild=getattr(self.args, "rebuild", False),
                show_summary=False,
            )

    def cmd_restart(self, project_id=None, service=None):
        root = self.detect_project_path(project_id)
        if not root:
            return
        if service:
            UI.info(f"Restarting service '{service}' in {root.name}...")
            run_command(
                ["docker", "compose", "restart", service],
                capture_output=False,
                cwd=str(root),
            )
        else:
            self.cmd_stop(str(root))
            self.args.project = str(root)
            self.cmd_run(is_restart=True)

    def cmd_stop(self, project_id=None, service=None):
        root = self.detect_project_path(project_id)
        if not root:
            return
        cmd = ["docker", "compose", "stop", "-t", "60"]
        if service:
            UI.info(f"Stopping service '{service}' in {root.name}...")
            cmd.append(service)
        run_command(
            cmd,
            capture_output=False,
            cwd=str(root),
        )

    def cmd_down(self, project_id=None, service=None):
        root = self.detect_project_path(project_id)
        if not root:
            return

        if service:
            UI.info(f"Removing service '{service}' in {root.name}...")
            cmd = ["docker", "compose", "rm", "-fs"]
            if getattr(self.args, "volumes", False):
                cmd.append("-v")
            cmd.append(service)
            run_command(cmd, capture_output=False, cwd=str(root))
        else:
            self.cmd_stop(str(root))
            cmd = ["docker", "compose", "down"]
            if getattr(self.args, "volumes", False):
                cmd.append("-v")
            run_command(cmd, capture_output=False, cwd=str(root))
            if getattr(self.args, "delete", False):
                self.safe_rmtree(root)

    def cmd_infra_down(self):
        for c in [
            "liferay-proxy-global",
            "docker-socket-proxy",
            "liferay-search-global",
        ]:
            if run_command(
                ["docker", "ps", "-a", "-q", "-f", f"name=^{c}$"], check=False
            ):
                run_command(["docker", "rm", "-f", c], check=False)
        run_command(["docker", "network", "rm", "liferay-net"], check=False)
        UI.success("Infrastructure cleanup complete.")

    def cmd_logs(self, project_id=None, service=None):
        root = self.detect_project_path(project_id)
        if not root:
            return
        cmd = ["docker", "compose", "logs", "-f"]
        if service:
            cmd.append(service)
        try:
            run_command(cmd, capture_output=False, cwd=str(root))
        except KeyboardInterrupt:
            pass

    def cmd_shell(self, project_id=None, service="liferay"):
        root = self.detect_project_path(project_id)
        if not root:
            return
        # Default to 'liferay' if service is None (CLI nargs="?" can pass None)
        service_name = service or "liferay"

        # Determine container name from compose or pattern
        project_meta = self.read_meta(root / PROJECT_META_FILE)
        container_prefix = project_meta.get("container_name")

        target_container = f"{container_prefix}-{service_name}"
        if service_name == "liferay":
            target_container = container_prefix

        UI.info(f"Entering container: {target_container}")
        docker_bin = shutil.which("docker")
        if not docker_bin:
            UI.die("docker command not found.")
        try:
            # We use subprocess.run directly for interactive TTY
            subprocess.run(  # nosec B603
                [docker_bin, "exec", "-it", target_container, "/bin/bash"]
            )
        except KeyboardInterrupt:
            pass

    def cmd_gogo(self, project_id=None):
        root = self.detect_project_path(project_id)
        if not root:
            return
        project_meta = self.read_meta(root / PROJECT_META_FILE)
        port = project_meta.get("gogo_port")

        if not port or port == "None":
            UI.die(
                "Gogo shell is not exposed for this project. Run 'ldm run --gogo-port <port>' to enable it."
            )

        UI.info(f"Connecting to Gogo shell on localhost:{port}...")
        UI.info("Hint: Type 'help' for commands, 'exit' to disconnect.")

        # Try telnet, fallback to connection instruction
        telnet_bin = shutil.which("telnet")
        if telnet_bin:
            try:
                subprocess.run([telnet_bin, "localhost", str(port)])  # nosec B603
            except KeyboardInterrupt:
                pass
        else:
            UI.error("telnet binary not found on host.")
            print(f"Run: {UI.CYAN}telnet localhost {port}{UI.COLOR_OFF}")

    def is_port_available(self, port, ip="127.0.0.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.bind((ip, int(port)))
            return True
        except Exception:
            return False

    def is_bindable(self, ip):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((ip, 0))
            return True
        except Exception:
            return False
