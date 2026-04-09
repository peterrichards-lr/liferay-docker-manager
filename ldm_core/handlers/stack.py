import os
import re
import json
import time
import shutil
import platform
import subprocess
import socket
import hashlib
import sys
from pathlib import Path
from ldm_core.ui import UI
from ldm_core.constants import (
    PROJECT_META_FILE,
    ELASTICSEARCH_VERSION,
)
from ldm_core.utils import (
    run_command,
    get_actual_home,
    dict_to_yaml,
    get_docker_socket_path,
    get_compose_cmd,
)


class StackHandler:
    """Mixin for stack management commands (run, stop, restart, down, sync)."""

    def setup_ssl(self, cert_dir, host_name):
        """Generates certificates and Traefik config for a project."""
        if not host_name or host_name == "localhost":
            return False

        # Ensure directory exists
        cert_dir.mkdir(parents=True, exist_ok=True)

        # 1. Generate Certificates via mkcert
        cert_file = cert_dir / f"{host_name}.pem"
        key_file = cert_dir / f"{host_name}-key.pem"

        if not cert_file.exists() or not key_file.exists():
            UI.info(f"Generating SSL certificates for {host_name}...")
            mkcert_bin = shutil.which("mkcert")
            if not mkcert_bin:
                UI.die("mkcert binary not found. Please install it to use SSL.")

            run_command(
                [
                    mkcert_bin,
                    "-cert-file",
                    str(cert_file),
                    "-key-file",
                    str(key_file),
                    host_name,
                    f"*.{host_name}",
                ],
                check=True,
            )

        # 2. Generate Traefik Dynamic Config
        config_path = cert_dir / f"traefik-{host_name}.yml"
        traefik_conf = (
            "tls:\n"
            "  certificates:\n"
            f"    - certFile: /etc/traefik/certs/{host_name}.pem\n"
            f"      keyFile: /etc/traefik/certs/{host_name}-key.pem\n"
        )
        config_path.write_text(traefik_conf)
        os.chmod(config_path, 0o644)
        return True

    def cmd_renew_ssl(self, project_id=None):
        """Renew SSL certificates for one or all projects."""
        targets = []
        if getattr(self.args, "all", False):
            roots = self.find_dxp_roots()
            for r in roots:
                meta = self.read_meta(r["path"] / PROJECT_META_FILE)
                if str(meta.get("ssl", "false")).lower() == "true":
                    targets.append({"path": r["path"], "meta": meta})
        else:
            root = self.detect_project_path(project_id)
            if root:
                meta = self.read_meta(root / PROJECT_META_FILE)
                if str(meta.get("ssl", "false")).lower() == "true":
                    targets.append({"path": root, "meta": meta})
                else:
                    UI.die(f"Project '{root.name}' is not configured for SSL.")

        if not targets:
            UI.info("No projects found requiring SSL renewal.")
            return

        UI.heading(f"Renewing SSL for {len(targets)} project(s)")

        actual_home = get_actual_home()
        cert_dir = actual_home / "liferay-docker-certs"

        for t in targets:
            host_name = t["meta"].get("host_name")
            ssl_cert = t["meta"].get("ssl_cert")

            if not host_name:
                continue

            UI.info(f"  + Renewing: {host_name}...")
            cert_base = ssl_cert.replace(".pem", "") if ssl_cert else host_name

            for f in [
                cert_dir / f"{cert_base}.pem",
                cert_dir / f"{cert_base}-key.pem",
                cert_dir / f"traefik-{host_name}.yml",
            ]:
                if f.exists():
                    f.unlink()

            self.setup_ssl(cert_dir, host_name)

        UI.success(
            "SSL renewal complete. Changes will be detected by Traefik automatically."
        )

    def get_docker_socket_params(self):
        path = get_docker_socket_path()
        system = platform.system().lower()
        if system in ["windows", "win32"]:
            return ["-v", f"{path}://./pipe/docker_engine"]
        return ["-v", f"{path}:/var/run/docker.sock:ro"]

    def _ensure_network(self):
        """Idempotent creation of the shared liferay-net network."""
        run_command(["docker", "network", "create", "liferay-net"], check=False)

    def setup_infrastructure(self, resolved_ip, ssl_port, use_ssl=True):
        """Initializes global Traefik proxy and search services."""
        self._ensure_network()
        if not use_ssl:
            return True

        actual_home = get_actual_home()
        global_cert_dir = actual_home / "liferay-docker-certs"
        global_cert_dir.mkdir(parents=True, exist_ok=True)

        token_val = f"LDM_INFRA_VERIFY_{hashlib.sha256(str(actual_home).encode()).hexdigest()[:8]}"
        check_file = ".ldm_infra_mount_check"
        (global_cert_dir / check_file).write_text(token_val)

        try:
            verify_res = run_command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{global_cert_dir.as_posix()}:/certs",
                    "alpine",
                    "sh",
                    "-c",
                    f'if [ "$(cat /certs/{check_file} 2>/dev/null)" = "{token_val}" ]; then chown -R 1000:1000 /certs 2>/dev/null || true; chmod -R 775 /certs 2>/dev/null || true; echo "OK"; else echo "FAIL"; fi',
                ]
            )
            if "OK" not in (verify_res or ""):
                UI.error("\n❌ FATAL: INFRASTRUCTURE VOLUME MOUNTING IS BROKEN")
                if platform.system() == "darwin":
                    UI.info(
                        "\nThis often happens on macOS when Colima/OrbStack is not configured to share your home directory."
                    )
                    UI.info(f"\n{UI.CYAN}To fix this, run:{UI.COLOR_OFF}")
                    UI.info("colima stop\ncolima start --mount /Users/$(whoami):w")
                sys.exit(1)
        except Exception:
            pass

        needs_bridge = False
        api_proxy = "docker-socket-proxy"
        if platform.system().lower() == "darwin":
            socket_path = Path("/var/run/docker.sock")
            if not run_command(
                ["docker", "ps", "-a", "-q", "-f", f"name=^{api_proxy}$"]
            ):
                UI.info(
                    f"Starting Docker Socket Proxy bridge for macOS ({socket_path})..."
                )
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
                        f"{socket_path}:/var/run/docker.sock:ro",
                        "-e",
                        "DOCKER_API_VERSION=1.44",
                        "alpine/socat",
                        "TCP-LISTEN:2375,fork,reuseaddr",
                        "UNIX-CONNECT:/var/run/docker.sock",
                    ]
                )
            else:
                if not run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{api_proxy}$"], check=False
                ):
                    UI.info("Starting existing Docker Socket Proxy bridge...")
                    run_command(["docker", "start", api_proxy])
            needs_bridge = True

        proxy_name = "liferay-proxy-global"
        if not run_command(["docker", "ps", "-q", "-f", f"name=^{proxy_name}$"]):
            UI.info("Initializing Global SSL Proxy (Traefik v3)...")
            endpoint = (
                f"tcp://{api_proxy}:2375"
                if needs_bridge
                else "unix:///var/run/docker.sock"
            )
            traefik_cmd = [
                "docker",
                "run",
                "-d",
                "--name",
                proxy_name,
                "--network",
                "liferay-net",
                "-p",
                f"{resolved_ip}:80:80",
                "-p",
                f"{resolved_ip}:443:443",
                "-v",
                f"{global_cert_dir.as_posix()}:/etc/traefik/certs:ro",
                "traefik:v3.0",
                "--providers.docker=true",
                f"--providers.docker.endpoint={endpoint}",
                "--providers.docker.exposedbydefault=false",
                "--providers.docker.network=liferay-net",
                "--providers.file.directory=/etc/traefik/certs",
                "--providers.file.watch=true",
                "--entrypoints.web.address=:80",
                "--entrypoints.websecure.address=:443",
                "--entrypoints.web.http.redirections.entryPoint.to=websecure",
                "--entrypoints.web.http.redirections.entryPoint.scheme=https",
                "--log.level=ERROR",
            ]
            run_command(traefik_cmd)
        return True

    def setup_global_search(self):
        """Starts a shared Elasticsearch 8.x container."""
        self._ensure_network()
        search_name = "liferay-search-global"
        if run_command(["docker", "ps", "-q", "-f", f"name={search_name}"]):
            return True

        UI.info("Initializing Global Search (ES8) container...")
        actual_home = get_actual_home()
        search_backup_dir = actual_home / ".liferay_docker_search_backups"
        search_backup_dir.mkdir(parents=True, exist_ok=True)

        run_command(
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
                "ES_JAVA_OPTS=-Xms512m -Xmx512m",
                "-v",
                f"{search_backup_dir.as_posix()}:/usr/share/elasticsearch/backup",
                f"docker.elastic.co/elasticsearch/elasticsearch:{ELASTICSEARCH_VERSION}",
            ]
        )
        # Wait for Elasticsearch to be ready (up to 60s)
        UI.info("Waiting for Elasticsearch to become ready...")
        es_ready = False
        for i in range(12):
            res = run_command(
                ["docker", "exec", search_name, "curl", "-s", "localhost:9200"],
                check=False,
            )
            if res and "cluster_name" in res:
                es_ready = True
                break
            time.sleep(5)

        if not es_ready:
            UI.die("Elasticsearch failed to start within 60 seconds.")

        run_command(
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
                '{"type": "fs", "settings": {"location": "backup"}}',
            ]
        )
        return True

    def cmd_infra_setup(self):
        """Manual trigger for global infrastructure setup."""
        resolved_ip = (
            "0.0.0.0"  # nosec B104
            if platform.system().lower() == "darwin"
            else "127.0.0.1"
        )
        UI.heading("Initializing Global Infrastructure")
        self.setup_infrastructure(resolved_ip, 443, use_ssl=True)
        if getattr(self.args, "search", False):
            self.setup_global_search()
        UI.success("Global infrastructure services are ready.")

    def check_hostname(self, host_name, silent=False, expected_ip=None):
        if not host_name or host_name == "localhost":
            return True
        ip = self.get_resolved_ip(host_name)
        if not ip:
            UI.error(f"Hostname '{host_name}' could not be resolved.")
            target_ip = expected_ip or "127.0.0.1"
            print(
                f"\n   {UI.WHITE}To fix this, add the following to your hosts file:{UI.COLOR_OFF}"
            )
            print(f"   {UI.CYAN}{target_ip} {host_name}{UI.COLOR_OFF}\n")
            return False
        return True

    def get_resolved_ip(self, host_name):
        try:
            return socket.gethostbyname(host_name)
        except Exception:
            return None

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

        liferay_volumes = [
            f"{paths['files'].as_posix()}:/mnt/liferay/files",
            f"{paths['scripts'].as_posix()}:/mnt/liferay/scripts",
            f"{paths['configs'].as_posix()}:/opt/liferay/osgi/configs",
            f"{paths['modules'].as_posix()}:/opt/liferay/osgi/modules",
            f"{paths['marketplace'].as_posix()}:/opt/liferay/osgi/marketplace",
            f"{paths['cx'].as_posix()}:/opt/liferay/osgi/client-extensions",
            f"{paths['routes'].as_posix()}:/opt/liferay/routes",
            f"{paths['log4j'].as_posix()}:/opt/liferay/osgi/log4j",
            f"{paths['portal_log4j'].as_posix()}/portal-log4j-ext.xml:/opt/liferay/tomcat/webapps/ROOT/WEB-INF/classes/META-INF/portal-log4j-ext.xml",
            f"{paths['data'].as_posix()}:/opt/liferay/data",
            f"{paths['deploy'].as_posix()}:/opt/liferay/deploy",
            f"{paths['files'].as_posix()}/portal-ext.properties:/opt/liferay/portal-ext.properties",
        ]
        is_scaled = scale_map.get("liferay", 1) > 1
        if not is_scaled:
            liferay_volumes.append(
                f"{paths['state'].as_posix()}:/opt/liferay/osgi/state"
            )
        if mount_logs and not is_scaled:
            liferay_volumes.append(f"{paths['logs'].as_posix()}:/opt/liferay/logs")

        gogo_env = (
            "0.0.0.0:11311"  # nosec B104
            if gogo_port and str(gogo_port).isdigit()
            else "localhost:11311"
        )
        config_sig = hashlib.sha256(
            json.dumps(custom_env, sort_keys=True).encode()
        ).hexdigest()[:12]

        liferay_env_dict = {
            "LIFERAY_WORKSPACE__HOME__DIR": "/opt/liferay",
            "LIFERAY_LXC__DXP__MAIN__DOMAIN": host_name,
            "LIFERAY_LXC__DXP__DOMAINS": host_name,
            "LDM_CONFIG_SIGNATURE": config_sig,
            "LIFERAY_MODULE__FRAMEWORK__PROPERTIES__OSGI__CONSOLE": gogo_env,
            "OSGI_CONSOLE": gogo_env,
            "LIFERAY_VIRTUAL__HOSTS__VALID__HOSTS": f"127.0.0.1,[::1],{host_name},localhost",
            "LIFERAY_JVM_OPTS": f"-Dorg.apache.catalina.SESSION_COOKIE_NAME=LFR_SESSION_ID_{host_name.replace('.', '_')}",
        }
        if is_scaled:
            liferay_env_dict.update(
                {
                    "LIFERAY_CLUSTER__LINK__ENABLED": "true",
                    "LIFERAY_LUCENE__REPLICATE__WRITE": "true",
                }
            )

        compose = {
            "services": {
                "liferay": {
                    "image": image_tag,
                    "ports": [],
                    "volumes": liferay_volumes,
                    "networks": ["liferay-net"],
                    "extra_hosts": [f"{host_name}:host-gateway"],
                    "stop_grace_period": "60s",
                    "healthcheck": {
                        "test": [
                            "CMD",
                            "curl",
                            "-f",
                            "http://localhost:8080/c/portal/layout",
                        ],
                        "interval": "30s",
                        "timeout": "10s",
                        "retries": 15,
                        "start_period": "120s",
                    },
                    "labels": [
                        "com.liferay.ldm.managed=true",
                        f"com.liferay.ldm.project={container_name}",
                    ],
                }
            },
            "networks": {"liferay-net": {"external": True}},
        }
        if not is_scaled:
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
            liferay_env_dict.update(
                {
                    "LIFERAY_JDBC__DEFAULT__URL": f"jdbc:{db_type}://db:{db_port}/{db_name}"
                    + (
                        "?useUnicode=true&characterEncoding=UTF-8"
                        if db_type == "mysql"
                        else ""
                    ),
                    "LIFERAY_JDBC__DEFAULT__USERNAME": db_user,
                    "LIFERAY_JDBC__DEFAULT__PASSWORD": db_pass,
                    "LIFERAY_JDBC__DEFAULT__DRIVER__CLASS__NAME": "org.postgresql.Driver"
                    if db_type == "postgresql"
                    else "com.mysql.cj.jdbc.Driver",
                }
            )

        # 4. Port Mapping
        if port and str(port).isdigit() and not use_ssl:
            compose["services"]["liferay"]["ports"].append(f"{resolved_ip}:{port}:8080")
        if gogo_port and str(gogo_port).isdigit():
            compose["services"]["liferay"]["ports"].append(
                f"{resolved_ip}:{gogo_port}:11311"
            )
        if not use_shared_search:
            compose["services"]["liferay"]["ports"].append(f"{resolved_ip}:9201:9201")

        # 5. SSL & Security
        if use_ssl:
            liferay_env_dict.update(
                {
                    "LIFERAY_WEB__SERVER__PROTOCOL": "https",
                    "LIFERAY_WEB__SERVER__HTTPS__PORT": str(ssl_port),
                    "LIFERAY_WEB__SERVER__HOST": host_name,
                    "LIFERAY_VIRTUAL__HOSTS__VALID__HOSTS": f"localhost,127.0.0.1,127.0.0.2,[::1],[0:0:0:0:0:0:0:1],{host_name},*.{host_name}",
                }
            )
            self.update_portal_ext(
                paths["files"] / "portal-ext.properties",
                {
                    "virtual.hosts.valid.hosts": liferay_env_dict[
                        "LIFERAY_VIRTUAL__HOSTS__VALID__HOSTS"
                    ]
                },
            )
            compose["services"]["liferay"]["labels"].extend(
                [
                    "traefik.enable=true",
                    f"traefik.http.routers.{container_name}-main.rule=Host(`{host_name}`)",
                    f"traefik.http.routers.{container_name}-main.entrypoints=websecure",
                    f"traefik.http.routers.{container_name}-main.tls=true",
                    f"traefik.http.routers.{container_name}-main.tls.domains[0].main={host_name}",
                    f"traefik.http.routers.{container_name}-main.tls.domains[0].sans=*.{host_name}",
                    f"traefik.http.services.{container_name}-main-svc.loadbalancer.server.port=8080",
                ]
            )

        def merge_into_env(source):
            if not source:
                return
            if isinstance(source, list):
                for item in source:
                    if "=" in item:
                        k, v = item.split("=", 1)
                        liferay_env_dict[k] = v
            elif isinstance(source, dict):
                liferay_env_dict.update(source)

        merge_into_env(env_args)
        merge_into_env(self.get_host_passthrough_env(paths, "liferay"))
        compose["services"]["liferay"]["environment"] = [
            f"{k}={v}" for k, v in liferay_env_dict.items()
        ]

        extensions = self.scan_client_extensions(
            paths["root"], paths["cx"], paths["ce_dir"]
        )
        standalone_services = self.scan_standalone_services(paths["root"])
        all_services = extensions + standalone_services
        current_ssce_port = 8081

        for ext in all_services:
            if (
                "path" not in ext
                or ext.get("kind", "Deployment") != "Deployment"
                or not ext.get("deploy", True)
            ):
                continue
            ext_port = 80
            if ext.get("loadBalancer") and ext["loadBalancer"].get("targetPort"):
                ext_port = ext["loadBalancer"]["targetPort"]
            elif ext.get("ports"):
                ext_port = ext["ports"][0].get("port", 80)

            ext_id, ext_name = ext["id"], ext["name"]
            ext_env_dict = {
                "COM_LIFERAY_LXC_DXP_DOMAINS": host_name,
                "COM_LIFERAY_LXC_DXP_MAIN_DOMAIN": host_name,
                "LDM_CONFIG_SIGNATURE": config_sig,
                "LIFERAY_ROUTES_CLIENT_EXTENSION": f"/opt/liferay/routes/default/{ext_id}",
                "LIFERAY_ROUTES_DXP": "/opt/liferay/routes/default/dxp",
            }
            if not use_ssl:
                ext_url = f"http://localhost:{current_ssce_port}"
                ext_env_dict["COM_LIFERAY_LXC_CLIENT_EXTENSION_URL"] = ext_url
                ext["url"] = ext_url
                current_ssce_port += 1

            def merge_ext_env(source):
                if not source:
                    return
                if isinstance(source, list):
                    for item in source:
                        if "=" in item:
                            k, v = item.split("=", 1)
                            ext_env_dict[k] = v
                elif isinstance(source, dict):
                    ext_env_dict.update(source)

            merge_ext_env(self.get_host_passthrough_env(paths, ext_id))
            compose["services"][ext_name] = {
                "build": {"context": ext["path"].as_posix()},
                "networks": ["liferay-net"],
                "environment": [f"{k}={v}" for k, v in ext_env_dict.items()],
                "volumes": [f"{paths['routes'].as_posix()}:/opt/liferay/routes"],
                "extra_hosts": [f"{host_name}:host-gateway"],
            }
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
                    f"traefik.http.routers.{container_name}-{ext_name}.tls.domains[0].sans=*.{host_name}",
                    f"traefik.http.services.{container_name}-{ext_name}.loadbalancer.server.port={ext_port}",
                ]
                ext["url"] = f"https://{ext_host}"
            elif not use_ssl and "url" in ext:
                pass
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
                continue
            service, count = arg.split("=", 1)
            if not count.isdigit():
                continue
            meta[f"scale_{service}"] = count
        self.write_meta(project_path / PROJECT_META_FILE, meta)
        UI.success(f"Updated scale factors for project {project_path.name}")
        self.cmd_run(project_id)

    def sync_stack(
        self,
        paths,
        project_meta,
        follow=False,
        rebuild=False,
        show_summary=True,
        no_up=False,
        no_wait=False,
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
            if pattern.search(content):
                content = pattern.sub(ws_home, content)
            else:
                content += f"\n{ws_home}\n"
            gradle_props.write_text(content)

        if str(project_meta.get("use_shared_search", "true")).lower() == "true":
            self.setup_global_search()
        self.sync_common_assets(paths)
        self.sync_logging(paths)

        all_services, has_changed = self.write_docker_compose(
            paths,
            {
                "container_name": container_name,
                "image_tag": image_tag,
                "port": port,
                "resolved_ip": resolved_ip,
                "use_ssl": use_ssl,
                "ssl_port": ssl_port,
                "host_name": host_name,
                "env_args": project_meta.get("env_args", []),
                "custom_env": project_meta.get("custom_env", {}),
                "use_shared_search": str(
                    project_meta.get("use_shared_search", "true")
                ).lower()
                == "true",
                "mount_logs": str(project_meta.get("mount_logs")).lower() == "true",
                "gogo_port": project_meta.get("gogo_port"),
                "db_type": project_meta.get("db_type"),
                "db_name": project_meta.get("db_name"),
                "db_user": project_meta.get("db_user"),
                "db_pass": project_meta.get("db_pass"),
                **project_meta,
            },
        )

        if no_up:
            return
        UI.info("Orchestrating project stack...")
        if use_ssl:
            self.setup_infrastructure(resolved_ip, ssl_port, use_ssl=True)

        scale_args = []
        for k, v in project_meta.items():
            if k.startswith("scale_") and str(v).isdigit():
                service = k.replace("scale_", "")
                scale_args.extend(["--scale", f"{service}={v}"])

        compose_base = get_compose_cmd()
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )

        cmd = compose_base + ["up", "-d"] + scale_args
        if rebuild:
            cmd.append("--build")
        try:
            run_command(cmd, capture_output=False, cwd=str(paths["root"]))
        except Exception as e:
            UI.die("Failed to start project stack.", e)

        if not show_summary or no_wait:
            return
        p_id = project_meta.get("container_name") or paths["root"].name
        access_url = f"{'https' if use_ssl else 'http'}://{host_name}{f':{ssl_port}' if (use_ssl and ssl_port != 443) else (f':{port}' if not use_ssl else '')}"
        UI.info(
            f"Waiting for Liferay to start... (Monitor progress with: {UI.CYAN}ldm logs -f {p_id}{UI.COLOR_OFF})"
        )

        max_timeout, start_time, is_ready, last_reminder = (
            int(project_meta.get("timeout", 900)),
            time.time(),
            False,
            time.time(),
        )
        while time.time() - start_time < max_timeout:
            if time.time() - last_reminder > 60:
                UI.info(
                    f"Still waiting... Tip: Open a new terminal and run {UI.CYAN}ldm logs -f {p_id}{UI.COLOR_OFF} to see internal progress."
                )
                last_reminder = time.time()
            try:
                res = run_command(["curl", "-k", "-I", access_url], check=False)
                if res and ("200" in res or "302" in res):
                    is_ready = True
                    break
            except Exception:
                pass
            time.sleep(10)

        if is_ready:
            UI.success("Liferay is up and running!")
        else:
            UI.error("Startup timed out.")

        if follow:
            run_command(
                compose_base + ["logs", "-f"],
                capture_output=False,
                cwd=str(paths["root"]),
            )
        else:
            self.print_success_summary(
                paths, project_meta, all_services, show_summary, is_ready
            )

    def print_success_summary(
        self, paths, project_meta, extensions, show_summary=True, is_ready=False
    ):
        if not show_summary:
            return
        host_name, port, ssl, ssl_port = (
            project_meta.get("host_name", "localhost"),
            int(project_meta.get("port", 8080)),
            str(project_meta.get("ssl")).lower() == "true",
            int(project_meta.get("ssl_port", 443)),
        )
        access_url = f"{'https' if ssl else 'http'}://{host_name}{f':{ssl_port}' if (ssl and ssl_port != 443) else (f':{port}' if not ssl else '')}"
        UI.heading("Liferay Stack Ready")
        UI._print(f"Liferay:        {UI.CYAN}{access_url}{UI.COLOR_OFF}", icon="🌐")
        unresolved = []
        for e in [e for e in extensions if "url" in e]:
            status_suffix = ""
            if "https" in e["url"] and host_name != "localhost":
                ext_domain = re.sub(r"https?://([^:/]+).*", r"\1", e["url"])
                ip = self.get_resolved_ip(ext_domain)
                if not ip or not (
                    ip.startswith("127.") or ip in ["::1", "0:0:0:0:0:0:0:1"]
                ):
                    status_suffix, unresolved = (
                        f" {UI.RED}(Unresolved){UI.COLOR_OFF}",
                        unresolved + [ext_domain],
                    )
                else:
                    status_suffix = f" {UI.GREEN}[OK]{UI.COLOR_OFF}"
            UI._print(f"  - {UI.WHITE}{e['id']:<14} {UI.CYAN}{e['url']}{status_suffix}")
        if unresolved:
            target_ip = self.get_resolved_ip(host_name) or "127.0.0.1"
            UI.warning("Some subdomains are not resolving to your machine.")
            print(
                f"   Please add them to your {UI.WHITE}/etc/hosts{UI.COLOR_OFF} file:\n   {UI.CYAN}{target_ip} {' '.join(unresolved)}{UI.COLOR_OFF}\n"
            )
        UI.heading("Helpful Commands")
        p_id = project_meta.get("container_name") or paths["root"].name
        print(
            f"  View Logs:      {UI.CYAN}ldm logs -f {p_id}{UI.COLOR_OFF}\n  Stop Project:   {UI.CYAN}ldm stop {p_id}{UI.COLOR_OFF}\n  Container Shell:{UI.CYAN}ldm shell {p_id}{UI.COLOR_OFF}\n  Hot Deploy:     {UI.CYAN}ldm deploy {p_id}{UI.COLOR_OFF}"
        )
        if (
            is_ready
            and str(project_meta.get("browser_launch", "true")).lower() == "true"
        ):
            from ldm_core.utils import open_browser

            UI.info(f"Launching browser: {access_url}/web/guest/home")
            open_browser(f"{access_url}/web/guest/home")

    def cmd_run(self, is_restart=False):
        project_id = self.args.project or getattr(self.args, "project_flag", None)
        if getattr(self.args, "select", False):
            if self.non_interactive:
                UI.die(
                    "Project selection is not supported in non-interactive mode. Please specify a project ID."
                )
            selection = self.select_project_interactively(heading="Available Projects")
            if not selection:
                return
            project_id = selection["path"].name
        root = self.detect_project_path(project_id, for_init=True)
        if not root:
            if self.non_interactive:
                UI.die(
                    "Project not found and no name provided to initialize. Specify a project ID in non-interactive mode."
                )
            UI.die("Project not found and no name provided to initialize.")
        project_meta = self.read_meta(root / PROJECT_META_FILE)
        tag, host_name = (
            self.args.tag or project_meta.get("tag"),
            self.args.host_name or project_meta.get("host_name") or "localhost",
        )

        # 2. Tag Discovery (Restored)
        if not tag:
            if self.non_interactive:
                UI.die(
                    "No Liferay tag specified. In non-interactive mode, use: ldm run <pid> --tag <tag>"
                )

            release_type = self.args.release_type or UI.ask(
                "Release type (any|u|lts|qr) or prefix", "any"
            )
            from ldm_core.constants import API_BASE_DXP, API_BASE_PORTAL

            api_url = (
                API_BASE_PORTAL if getattr(self.args, "portal", False) else API_BASE_DXP
            )

            from ldm_core.utils import discover_latest_tag

            latest_tag = discover_latest_tag(
                api_url,
                release_type=release_type,
                verbose=True,
                refresh=getattr(self.args, "refresh", False),
            )
            if not latest_tag:
                UI.die("Could not discover any tags. Please specify one with --tag")

            tag = UI.ask("Enter Liferay Tag", latest_tag)

        # 3. Handle External Snapshot Initialization
        external_snapshot = getattr(self.args, "snapshot", None)
        if external_snapshot:
            snap_path = Path(external_snapshot).resolve()
            if not snap_path.exists():
                UI.die(f"Snapshot path not found: {snap_path}")
            snap_meta = self.read_meta(snap_path / "meta")
            if not tag:
                tag = snap_meta.get("tag")
            if host_name == "localhost":
                host_name = snap_meta.get("host_name") or "localhost"
        if host_name != "localhost" and not self.check_hostname(host_name):
            sys.exit(1)
        paths = self.setup_paths(project_id)
        self.verify_runtime_environment(paths)
        if getattr(self.args, "samples", False):
            self.sync_samples(paths)

        # 6. Finalize Meta
        ssl_arg = getattr(self.args, "ssl", None)
        if ssl_arg is not None:
            # User used --ssl or --no-ssl explicitly
            ssl_val = ssl_arg
        else:
            # Default logic: Use saved meta, or True (SSL) if new.
            meta_ssl = project_meta.get("ssl")
            if meta_ssl is not None:
                ssl_val = str(meta_ssl).lower() == "true"
            else:
                # Default to SSL for new projects
                ssl_val = True

        project_meta.update(
            {
                "tag": tag,
                "host_name": host_name,
                "container_name": project_id,
                "ssl": str(ssl_val).lower(),
            }
        )
        self.write_meta(root / PROJECT_META_FILE, project_meta)
        if getattr(self.args, "samples", False) or external_snapshot:
            self.sync_stack(paths, project_meta, no_up=True, show_summary=False)
            UI.info("Preparing data restoration...")
            run_command(get_compose_cmd() + ["up", "-d", "db"], cwd=str(paths["root"]))
            time.sleep(5)
            self.cmd_restore(
                project_id,
                auto_index=1 if getattr(self.args, "samples", False) else None,
                backup_dir=external_snapshot
                if not getattr(self.args, "samples", False)
                else None,
            )

        # 8. Start Stack
        self.sync_stack(
            paths,
            project_meta,
            follow=getattr(self.args, "follow", False),
            rebuild=getattr(self.args, "rebuild", False),
            no_up=getattr(self.args, "no_up", False),
            no_wait=getattr(self.args, "no_wait", False),
        )

    def cmd_deploy(self, project_id=None, service=None):
        root = self.detect_project_path(project_id)
        if not root:
            return
        paths, meta, compose_base = (
            self.setup_paths(root),
            self.read_meta(root / PROJECT_META_FILE),
            get_compose_cmd(),
        )
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )
        if service:
            UI.heading(f"Deploying service '{service}' to {meta.get('container_name')}")
            cmd = compose_base + ["up", "-d"]
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
        compose_base = get_compose_cmd()
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )
        if service:
            UI.info(f"Restarting service '{service}' in {root.name}...")
            if not self.require_compose(root):
                return
            run_command(
                compose_base + ["restart", service], capture_output=False, cwd=str(root)
            )
        else:
            if self.require_compose(root, silent=True):
                self.cmd_stop(str(root))
            self.args.project = str(root)
            self.cmd_run(is_restart=True)

    def cmd_stop(self, project_id=None, service=None):
        root = self.detect_project_path(project_id)
        if not root or not self.require_compose(root, silent=True):
            return
        compose_base = get_compose_cmd()
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )
        cmd = compose_base + ["stop", "-t", "60"]
        if service:
            UI.info(f"Stopping service '{service}' in {root.name}...")
            cmd.append(service)
        run_command(cmd, capture_output=False, cwd=str(root))

    def cmd_down(self, project_id=None, service=None):
        root = self.detect_project_path(project_id)
        if not root:
            if getattr(self.args, "infra", False):
                self.cmd_infra_down()
            return
        compose_base = get_compose_cmd()
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )
        if service:
            UI.info(f"Removing service '{service}' in {root.name}...")
            if not self.require_compose(root):
                return
            cmd = compose_base + ["rm", "-fs"]
            if getattr(self.args, "volumes", False):
                cmd.append("-v")
            cmd.append(service)
            run_command(cmd, capture_output=False, cwd=str(root))
        else:
            if self.require_compose(root, silent=True):
                self.cmd_stop(str(root))
                cmd = compose_base + ["down"]
                if getattr(self.args, "volumes", False):
                    cmd.append("-v")
                run_command(cmd, capture_output=False, cwd=str(root))
            if getattr(self.args, "infra", False):
                self.cmd_infra_down()
            meta, host_name = (
                self.read_meta(root / PROJECT_META_FILE),
                self.read_meta(root / PROJECT_META_FILE).get("host_name"),
            )
            if host_name and host_name != "localhost":
                cert_dir = get_actual_home() / "liferay-docker-certs"
                cert_base = meta.get("ssl_cert", host_name).replace(".pem", "")
                for art in [
                    cert_dir / f"{cert_base}.pem",
                    cert_dir / f"{cert_base}-key.pem",
                    cert_dir / f"traefik-{host_name}.yml",
                ]:
                    if art.exists():
                        art.unlink()
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
        compose_base = get_compose_cmd()
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )
        cmd = compose_base + ["logs", "-f"]
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
        service_name, project_meta = (
            service or "liferay",
            self.read_meta(root / PROJECT_META_FILE),
        )
        container_prefix = project_meta.get("container_name")
        target_container = (
            f"{container_prefix}-{service_name}"
            if service_name != "liferay"
            else container_prefix
        )
        UI.info(f"Entering container: {target_container}")
        docker_bin = shutil.which("docker")
        if not docker_bin:
            UI.die("docker command not found.")
        try:
            subprocess.run([docker_bin, "exec", "-it", target_container, "/bin/bash"])
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
        telnet_bin = shutil.which("telnet")
        if telnet_bin:
            try:
                subprocess.run([telnet_bin, "localhost", str(port)])
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
