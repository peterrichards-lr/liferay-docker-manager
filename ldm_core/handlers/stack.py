import os
import re
import json
import time
import shutil
import platform
import subprocess
import hashlib
import sys
import math
from datetime import datetime, timezone
from pathlib import Path
from ldm_core.ui import UI
from ldm_core.constants import (
    PROJECT_META_FILE,
    META_VERSION,
    ELASTICSEARCH_VERSION,
    ELASTICSEARCH7_VERSION,
    TRAEFIK_VERSION,
    SOCAT_IMAGE,
)
from ldm_core.utils import (
    run_command,
    get_actual_home,
    dict_to_yaml,
    get_docker_socket_path,
    get_compose_cmd,
    open_browser,
)


class StackHandler:
    """Mixin for stack management commands (run, stop, restart, down, sync)."""

    def setup_ssl(self, cert_dir, host_name):
        """Generates certificates and Traefik config for a project."""
        if not host_name:
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

            # Smarter Host Selection
            hosts = [host_name]
            if host_name == "localhost":
                hosts.append("127.0.0.1")
            else:
                hosts.append(f"*.{host_name}")

            res = run_command(
                [
                    mkcert_bin,
                    "-cert-file",
                    str(cert_file),
                    "-key-file",
                    str(key_file),
                ]
                + hosts,
                check=False,
            )
            if not cert_file.exists():
                UI.error(f"Failed to generate SSL certificates: {res}")
                return False

        # 2. Generate Traefik Dynamic Config
        UI.info(f"Generating Traefik dynamic config for {host_name}...")
        config_path = cert_dir / f"traefik-{host_name}.yml"
        traefik_conf = (
            "tls:\n"
            "  certificates:\n"
            f"    - certFile: /etc/traefik/certs/{host_name}.pem\n"
            f"      keyFile: /etc/traefik/certs/{host_name}-key.pem\n"
        )
        try:
            config_path.write_text(traefik_conf)
            os.chmod(config_path, 0o644)
        except Exception as e:
            if self.verbose:
                UI.warning(f"Could not update Traefik config permissions: {e}")

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

            # Use explicit reference from meta if possible
            cert_base = ssl_cert.replace(".pem", "") if ssl_cert else host_name

            # Surgical deletion to force mkcert to regenerate
            files = [
                cert_dir / f"{cert_base}.pem",
                cert_dir / f"{cert_base}-key.pem",
                cert_dir / f"traefik-{host_name}.yml",
            ]
            for f in files:
                if f.exists():
                    f.unlink()

            # Trigger regeneration
            self.setup_ssl(cert_dir, host_name)

        UI.success(
            "SSL renewal complete. Changes will be detected by Traefik automatically."
        )

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
                UI.error("\nFATAL: INFRASTRUCTURE VOLUME MOUNTING IS BROKEN")
                if platform.system().lower() == "darwin":
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
            # 1. Dynamic discovery
            socket_path = Path(get_docker_socket_path()).resolve()

            # 2. Existence check (including stopped)
            existing_bridge = run_command(
                ["docker", "ps", "-a", "-q", "-f", f"name=^{api_proxy}$"], check=False
            )

            if existing_bridge:
                inspect_info = run_command(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.Config.Env}} {{.State.Running}}",
                        api_proxy,
                    ],
                    check=False,
                )
                is_outdated = "DOCKER_API_VERSION" not in (inspect_info or "")
                is_running = "true" in (inspect_info or "").lower()

                if is_outdated:
                    UI.info("Docker bridge is outdated. Recreating...")
                    run_command(["docker", "rm", "-f", api_proxy], check=False)
                    existing_bridge = None
                elif not is_running:
                    UI.info("Docker bridge is not running. Recreating...")
                    run_command(["docker", "rm", "-f", api_proxy], check=False)
                    existing_bridge = None

            if not existing_bridge:
                UI.info(
                    f"Starting Docker Socket Proxy bridge for macOS ({socket_path})..."
                )

                # Build the run command
                bridge_cmd = [
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
                    SOCAT_IMAGE,
                    "TCP-LISTEN:2375,fork,reuseaddr",
                    "UNIX-CONNECT:/var/run/docker.sock",
                ]

                # Attempt to run, with a fallback if the socket path is rejected by the daemon
                # (Common on some Colima versions with VirtioFS)
                res = subprocess.run(
                    bridge_cmd, capture_output=True, text=True, check=False
                )
                if res.returncode != 0 and "operation not supported" in res.stderr:
                    UI.info(
                        "Dynamic socket mount failed. Falling back to standard /var/run/docker.sock..."
                    )

                    # Robust Fix: Instead of hardcoded index, find and replace the mapping element
                    fallback_mapping = "/var/run/docker.sock:/var/run/docker.sock:ro"
                    for i, arg in enumerate(bridge_cmd):
                        if ":/var/run/docker.sock:ro" in arg:
                            bridge_cmd[i] = fallback_mapping
                            break

                    # Cleanup: Remove the container created by the failed attempt before retrying
                    run_command(["docker", "rm", "-f", api_proxy], check=False)
                    run_command(bridge_cmd)
                elif res.returncode != 0:
                    UI.die(f"Failed to start socket bridge: {res.stderr}")
            else:
                if not run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{api_proxy}$"], check=False
                ):
                    UI.info("Starting existing Docker Socket Proxy bridge...")
                    start_res = subprocess.run(
                        ["docker", "start", api_proxy],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    if (
                        start_res.returncode != 0
                        and "operation not supported" in start_res.stderr
                    ):
                        UI.info("Existing bridge has invalid mount path. Recreating...")
                        run_command(["docker", "rm", "-f", api_proxy], check=False)
                        # Re-run this method to trigger the creation logic with fallback
                        return self.setup_infrastructure(resolved_ip, ssl_port, use_ssl)
                    elif start_res.returncode != 0:
                        UI.die(f"Failed to start socket bridge: {start_res.stderr}")

                # Critical Fix: Ensure the bridge is actually connected to our network
                # If it was created by a different tool or run, it might be isolated.
                res = run_command(
                    [
                        "docker",
                        "network",
                        "inspect",
                        "liferay-net",
                        "-f",
                        "{{range .Containers}}{{.Name}} {{end}}",
                    ],
                    check=False,
                )
                if api_proxy not in (res or ""):
                    UI.info(f"Connecting {api_proxy} to liferay-net...")
                    run_command(
                        ["docker", "network", "connect", "liferay-net", api_proxy],
                        check=False,
                    )
            needs_bridge = True

        proxy_name = "liferay-proxy-global"
        # 1. Existence check (including stopped)
        existing_proxy = run_command(
            ["docker", "ps", "-a", "-q", "-f", f"name=^{proxy_name}$"], check=False
        )

        if existing_proxy:
            inspect_info = run_command(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.Config.Env}} {{.Config.Image}} {{.State.Running}}",
                    proxy_name,
                ],
                check=False,
            )
            # Detect if missing the CORRECT DOCKER_API_VERSION key or if using an older traefik image
            is_outdated = "DOCKER_API_VERSION" not in (
                inspect_info or ""
            ) or f"traefik:{TRAEFIK_VERSION}" not in (inspect_info or "")
            is_running = "true" in (inspect_info or "").lower()

            if is_outdated:
                UI.info(
                    f"Global proxy is outdated. Recreating with {TRAEFIK_VERSION} API fixes..."
                )
                run_command(["docker", "rm", "-f", proxy_name], check=False)
                existing_proxy = None  # Force re-creation below
            elif not is_running:
                UI.info(
                    "Global proxy is not running. Recreating to ensure latest config..."
                )
                run_command(["docker", "rm", "-f", proxy_name], check=False)
                existing_proxy = None
            else:
                # Already running and up-to-date
                return True

        if not existing_proxy:
            from ldm_core.utils import check_port

            if check_port(80):
                UI.die(
                    "Port 80 is already in use by another process. Cannot start Global Proxy."
                )
            if check_port(443):
                UI.die(
                    "Port 443 is already in use by another process. Cannot start Global Proxy."
                )

            UI.info(f"Initializing Global SSL Proxy (Traefik {TRAEFIK_VERSION})...")

            # Use the dynamic socket path for the endpoint on non-macOS platforms
            socket_path = get_docker_socket_path()
            endpoint = (
                f"tcp://{api_proxy}:2375" if needs_bridge else f"unix://{socket_path}"
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
            ]

            # Critical Fix: On Linux, we MUST mount the socket for the provider to work.
            # On macOS, we use the TCP bridge (needs_bridge=True) so no mount is needed.
            if not needs_bridge:
                traefik_cmd.extend(["-v", f"{socket_path}:/var/run/docker.sock:ro"])

            traefik_cmd.extend(
                [
                    "-e",
                    "DOCKER_API_VERSION=1.44",
                    f"traefik:{TRAEFIK_VERSION}",
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
                    f"--log.level={'DEBUG' if self.verbose else 'INFO'}",
                ]
            )
            run_command(traefik_cmd)
        return True

    def setup_global_search(self):
        """Starts a shared Elasticsearch container (v8 by default, v7 if --es7 used)."""
        self._ensure_network()
        search_name = "liferay-search-global"

        use_es7 = getattr(self.args, "es7", False)
        target_version = ELASTICSEARCH7_VERSION if use_es7 else ELASTICSEARCH_VERSION
        version_label = "ES7" if use_es7 else "ES8"

        # 1. Existence check (including stopped containers)
        existing = run_command(
            ["docker", "ps", "-a", "-q", "-f", f"name=^{search_name}$"], check=False
        )

        if existing:
            # Check if running version matches requested version
            inspect_res = run_command(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.Config.Image}} {{.State.Running}}",
                    search_name,
                ],
                check=False,
            )
            is_running = "true" in (inspect_res or "").lower()
            is_correct_version = f":{target_version}" in (inspect_res or "")

            if not is_correct_version:
                UI.warning(
                    f"A different Global Search version is already present. Requested: {version_label}"
                )
                if (
                    self.non_interactive
                    or UI.ask(f"Stop and recreate as {version_label}?", "N").upper()
                    == "Y"
                ):
                    run_command(["docker", "rm", "-f", search_name], check=False)
                else:
                    return True
            elif not is_running:
                # Same version but stopped, easiest to just recreate to ensure fresh state/network
                UI.info(
                    f"Existing {version_label} container is not running. Recreating..."
                )
                run_command(["docker", "rm", "-f", search_name], check=False)
            else:
                # Correct version and already running
                return True

        from ldm_core.utils import check_port

        if check_port(9200):
            UI.die(
                "Port 9200 is already in use by another process. Cannot start Global Search."
            )

        UI.info(f"Initializing Global Search ({version_label}) container...")
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
                "-p",
                "127.0.0.1:9200:9200",
                "-e",
                "discovery.type=single-node",
                "-e",
                "xpack.security.enabled=false",
                "-e",
                "indices.query.bool.max_clause_count=10000",
                "-e",
                "ES_JAVA_OPTS=-Xms512m -Xmx512m",
                "-v",
                f"{search_backup_dir.as_posix()}:/usr/share/elasticsearch/backup",
                f"docker.elastic.co/elasticsearch/elasticsearch:{target_version}",
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
                "-X",
                "PUT",
                "localhost:9200/_snapshot/backup",
                "-H",
                "Content-Type: application/json",
                "-d",
                '{"type": "fs", "settings": {"location": "backup"}}',
            ]
        )

        # 4. Plugin Setup (Required for Liferay Index Analyzers)
        required_plugins = [
            "analysis-icu",
            "analysis-kuromoji",
            "analysis-smartcn",
            "analysis-stempel",
        ]
        installed_plugins = (
            run_command(
                ["docker", "exec", search_name, "bin/elasticsearch-plugin", "list"],
                check=False,
            )
            or ""
        )
        missing_plugins = [p for p in required_plugins if p not in installed_plugins]

        if missing_plugins:
            UI.info(
                f"Installing missing Liferay analyzers in Global Search: {', '.join(missing_plugins)}..."
            )
            for plugin in missing_plugins:
                run_command(
                    [
                        "docker",
                        "exec",
                        search_name,
                        "bin/elasticsearch-plugin",
                        "install",
                        plugin,
                        "-b",
                    ],
                    check=False,
                )

            UI.info("Restarting Global Search to activate plugins...")
            run_command(["docker", "restart", search_name])

            # Wait for it to come back up
            for i in range(12):
                res = run_command(
                    ["docker", "exec", search_name, "curl", "-s", "localhost:9200"],
                    check=False,
                )
                if res and "cluster_name" in res:
                    break
                time.sleep(5)

        return True

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
        mount_logs, gogo_port, jvm_args = (
            config.get("mount_logs", False),
            config.get("gogo_port"),
            config.get("jvm_args"),
        )
        no_vol_cache = str(config.get("no_vol_cache", "false")).lower() == "true"
        no_jvm_verify = str(config.get("no_jvm_verify", "false")).lower() == "true"
        no_tld_skip = str(config.get("no_tld_skip", "false")).lower() == "true"

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

        vol_suffix = ""
        if not no_vol_cache and platform.system().lower() in ["darwin", "windows"]:
            vol_suffix = ":cached"

        liferay_volumes = [
            f"{paths['files'].as_posix()}:/mnt/liferay/files{vol_suffix}",
            f"{paths['scripts'].as_posix()}:/mnt/liferay/scripts{vol_suffix}",
            f"{paths['configs'].as_posix()}:/opt/liferay/osgi/configs{vol_suffix}",
            f"{paths['modules'].as_posix()}:/opt/liferay/osgi/modules{vol_suffix}",
            f"{paths['marketplace'].as_posix()}:/opt/liferay/osgi/marketplace{vol_suffix}",
            f"{paths['cx'].as_posix()}:/opt/liferay/osgi/client-extensions{vol_suffix}",
            f"{paths['routes'].as_posix()}:/opt/liferay/routes{vol_suffix}",
            f"{paths['log4j'].as_posix()}:/opt/liferay/osgi/log4j{vol_suffix}",
            f"{paths['portal_log4j'].as_posix()}/portal-log4j-ext.xml:/opt/liferay/tomcat/webapps/ROOT/WEB-INF/classes/META-INF/portal-log4j-ext.xml{vol_suffix}",
            f"{paths['data'].as_posix()}:/opt/liferay/data",
            f"{paths['deploy'].as_posix()}:/opt/liferay/deploy",
            f"{paths['files'].as_posix()}/portal-ext.properties:/opt/liferay/portal-ext.properties{vol_suffix}",
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

        # Extract tag from image_tag for version-aware logic
        tag = image_tag.split(":")[-1] if ":" in image_tag else "latest"

        # Version-Aware Env Var Formatting:
        # Newer versions (2025.Q1+ / 7.4.13-u100+) struggle with double-underscore decoding.
        # Older versions often require it for reliable property mapping.
        is_modern = self.parse_version(tag) >= (2025, 1, 0) or tag >= "7.4.13-u100"
        sep = "_" if is_modern else "__"

        # 5. Liferay Configuration (Properties)
        # We write critical infrastructure settings directly to portal-ext.properties
        # to avoid the unreliable environment variable decoding in newer DXP versions.
        portal_ext_updates = {}

        # Optimized JVM Options for high-velocity startup
        # - XX:TieredStopAtLevel=1: Speeds up JIT
        # - XX:-BytecodeVerificationLocal: Skips verification for local dev classes
        # - tomcat.util.scan.StandardJarScanFilter.jarsToSkip: Skips expensive TLD scans
        jvm_opts_list = [
            f"-Dorg.apache.catalina.SESSION_COOKIE_NAME=LFR_SESSION_ID_{host_name.replace('.', '_')}",
            "-XX:+UnlockDiagnosticVMOptions",
            "-XX:TieredStopAtLevel=1",
        ]
        if not no_jvm_verify:
            jvm_opts_list.append("-XX:-BytecodeVerificationLocal")
        if not no_tld_skip:
            jvm_opts_list.append(
                "-Dtomcat.util.scan.StandardJarScanFilter.jarsToSkip=*.jar"
            )

        jvm_opts = " ".join(jvm_opts_list)
        if jvm_args:
            jvm_opts += f" {jvm_args}"

        liferay_env_dict = {
            f"LIFERAY_WORKSPACE{sep}HOME{sep}DIR": "/opt/liferay",
            "LDM_CONFIG_SIGNATURE": config_sig,
            "OSGI_CONSOLE": gogo_env,
            "LIFERAY_JVM_OPTS": jvm_opts.replace(" ", "\\ "),
        }

        if not is_modern:
            # For older versions, we still provide these as env vars for compatibility
            liferay_env_dict.update(
                {
                    f"LIFERAY_LXC{sep}DXP{sep}MAIN{sep}DOMAIN": host_name,
                    f"LIFERAY_LXC{sep}DXP{sep}DOMAINS": host_name,
                    f"LIFERAY_VIRTUAL{sep}HOSTS{sep}VALID{sep}HOSTS": f"127.0.0.1,[::1],{host_name},localhost",
                }
            )
        if is_scaled:
            portal_ext_updates.update(
                {
                    "cluster.link.enabled": "true",
                    "lucene.replicate.write": "true",
                }
            )

        if use_shared_search:
            # Configure Liferay to use the global ES8 container
            # We must explicitly disable the sidecar and point to the shared service
            liferay_env_dict.update(
                {
                    "LIFERAY_ELASTICSEARCH_SIDECAR_ENABLED": "false",
                    "LIFERAY_ELASTICSEARCH_CONNECTION_URL": "http://liferay-search-global:9200",
                    "LIFERAY_ELASTICSEARCH_INDEX_NAME_PREFIX": f"ldm-{container_name}-",
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
            portal_ext_updates.update(
                {
                    "jdbc.default.url": f"jdbc:{db_type}://db:{db_port}/{db_name}"
                    + (
                        "?useUnicode=true&characterEncoding=UTF-8"
                        if db_type == "mysql"
                        else ""
                    ),
                    "jdbc.default.username": db_user,
                    "jdbc.default.password": db_pass,
                    "jdbc.default.driverClassName": "org.postgresql.Driver"
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

        # 5. Liferay Configuration (Properties)
        # We write critical infrastructure settings directly to portal-ext.properties
        # to avoid the unreliable environment variable decoding in newer DXP versions.
        if host_name != "localhost":
            portal_ext_updates["virtual.hosts.valid.hosts"] = (
                f"localhost,127.0.0.1,127.0.0.2,[::1],[0:0:0:0:0:0:0:1],{host_name},*.{host_name}"
            )

        if use_ssl:
            portal_ext_updates.update(
                {
                    "web.server.protocol": "https",
                    "web.server.https.port": str(ssl_port),
                    "web.server.host": host_name,
                }
            )
            compose["services"]["liferay"]["labels"].extend(
                [
                    "traefik.enable=true",
                    "traefik.docker.network=liferay-net",
                    f"traefik.http.routers.{container_name}-main.rule=Host(`{host_name}`)",
                    f"traefik.http.routers.{container_name}-main.entrypoints=websecure",
                    f"traefik.http.routers.{container_name}-main.tls=true",
                    f"traefik.http.routers.{container_name}-main.tls.domains[0].main={host_name}",
                    f"traefik.http.routers.{container_name}-main.tls.domains[0].sans=*.{host_name}",
                    f"traefik.http.services.{container_name}-main-svc.loadbalancer.server.port=8080",
                ]
            )

        if use_shared_search:
            # Configure Liferay to use the global ES8 container
            # 1. We set the operation mode via env var to prevent sidecar startup during boot
            # 2. Connection and indexing details are handled by OSGi .config files
            liferay_env_dict.update({"LIFERAY_ELASTICSEARCH_OPERATION_MODE": "REMOTE"})
        if portal_ext_updates:
            self.update_portal_ext(
                paths["files"] / "portal-ext.properties", portal_ext_updates
            )

        # Environment Variables (User and Host passthrough)
        user_env_dict = {}

        def merge_into_user_env(source):
            if not source:
                return
            if isinstance(source, list):
                for item in source:
                    if "=" in item:
                        k, v = item.split("=", 1)
                        user_env_dict[k] = v
            elif isinstance(source, dict):
                user_env_dict.update(source)

        merge_into_user_env(env_args)
        merge_into_user_env(self.get_host_passthrough_env(paths, "liferay"))

        # SCRUB USER ENV: Ensure no infrastructure settings leaked from user/host
        # This prevents Liferay from throwing 'Unable to decode' warnings for conflicting settings
        infra_prefixes = [
            "LIFERAY_WEB_SERVER",
            "LIFERAY_ELASTICSEARCH",
            "LIFERAY_VIRTUAL_HOSTS",
            "LIFERAY_CLUSTER",
            "LIFERAY_LUCENE",
            "LIFERAY_JDBC",
            "LIFERAY_LXC",
            "LIFERAY_WORKSPACE",
            "LIFERAY_CONTAINER",
        ]
        for k in list(user_env_dict.keys()):
            if any(k.startswith(p) for p in infra_prefixes):
                # We only remove it if it's NOT already in liferay_env_dict (which means LDM set it)
                if k not in liferay_env_dict:
                    user_env_dict.pop(k)

        # Final merge: User settings (scrubbed) win over LDM defaults if they collide
        # but LDM settings are protected from the generic prefix scrub.
        liferay_env_dict.update(user_env_dict)

        compose["services"]["liferay"]["environment"] = [
            f"{k}={v}" for k, v in liferay_env_dict.items()
        ]

        # 6. Client Extensions
        extensions = self.scan_client_extensions(
            paths["root"], paths["cx"], paths["ce_dir"]
        )
        for ext in extensions:
            if not ext.get("is_service"):
                continue

            ext_id = ext["id"]
            ext_name = f"{container_name}-{ext_id}"
            ext_port = 80
            if ext.get("loadBalancer") and ext["loadBalancer"].get("targetPort"):
                ext_port = ext["loadBalancer"]["targetPort"]
            elif ext.get("ports"):
                ext_port = ext["ports"][0].get("port", 80)

            ext_env_dict = {
                "LIFERAY_LXC_DXP_MAIN_DOMAIN": host_name,
                "LIFERAY_LXC_DXP_DOMAINS": host_name,
            }

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
                    "traefik.docker.network=liferay-net",
                    f"traefik.http.routers.{ext_name}.rule=Host(`{ext_host}`)",
                    f"traefik.http.routers.{ext_name}.entrypoints=websecure",
                    f"traefik.http.routers.{ext_name}.tls=true",
                    f"traefik.http.routers.{ext_name}.tls.domains[0].main={host_name}",
                    f"traefik.http.routers.{ext_name}.tls.domains[0].sans=*.{host_name}",
                    f"traefik.http.services.{ext_name}-svc.loadbalancer.server.port={ext_port}",
                ]

        standalone = self.scan_standalone_services(paths["scripts"])
        for s in standalone:
            compose["services"][f"{container_name}-{s['name']}"] = s["config"]

        yaml_content = "# Generated by Liferay Docker Manager\n" + dict_to_yaml(compose)

        has_changed = (
            not paths["compose"].exists()
            or paths["compose"].read_text() != yaml_content
        )
        if has_changed:
            from ldm_core.utils import safe_write_text

            safe_write_text(paths["compose"], yaml_content)

        return compose["services"], has_changed

    def _map_probe_to_healthcheck(self, probe, target_port):
        """Converts an LCP JSON probe definition to a Docker healthcheck."""
        # Defaults
        interval = probe.get("interval", 30)
        timeout = probe.get("timeout", 10)
        retries = probe.get("retries", 3)
        start_period = probe.get("initialDelay", 60)

        # Build command based on probe type
        test_cmd = ["CMD", "curl", "-f", f"http://localhost:{target_port}/"]

        if probe.get("httpGet"):
            path = probe["httpGet"].get("path", "/")
            port = probe["httpGet"].get("port", target_port)
            test_cmd = ["CMD", "curl", "-f", f"http://localhost:{port}{path}"]
        elif probe.get("tcpSocket"):
            port = probe["tcpSocket"].get("port", target_port)
            test_cmd = ["CMD-SHELL", f"nc -z localhost {port}"]
        elif probe.get("exec"):
            test_cmd = ["CMD"] + probe["exec"].get("command", [])

        return {
            "test": test_cmd,
            "interval": f"{interval}s",
            "timeout": f"{timeout}s",
            "retries": retries,
            "start_period": f"{start_period}s",
        }

    def cmd_infra_setup(self):
        """Standalone command to initialize global infrastructure services."""
        if not self.check_docker():
            UI.die("Docker is not reachable.")

        # Infrastructure always binds to localhost/127.0.0.1 by default
        # EXCEPT on macOS where 0.0.0.0 is required for multi-IP loopback
        resolved_ip = (
            "0.0.0.0"  # nosec B104
            if platform.system().lower() == "darwin"
            else "127.0.0.1"
        )
        ssl_port = 443

        UI.heading("Initializing Global Infrastructure")
        self.setup_infrastructure(resolved_ip, ssl_port, use_ssl=True)

        if getattr(self.args, "search", False):
            self.setup_global_search()

        UI.success("Global infrastructure services are ready.")

    def scrub_legacy_meta(self, project_id):
        """Removes legacy or broken metadata keys from previous versions."""
        root = self.detect_project_path(project_id)
        if not root:
            return
        meta_file = root / PROJECT_META_FILE
        if not meta_file.exists():
            return

        meta = self.read_meta(meta_file)
        env_args = meta.get("env_args", [])
        custom_env = json.loads(meta.get("custom_env", "{}"))

        # Keys that should NO LONGER be in environment variables (now handled by properties)
        blacklisted_prefixes = [
            "LIFERAY_ELASTICSEARCH",
            "LIFERAY_WEB_SERVER",
            "LIFERAY_VIRTUAL_HOSTS",
            "LIFERAY_CLUSTER",
            "LIFERAY_LUCENE",
            "LIFERAY_JDBC",
            "LIFERAY_LXC",
            "LIFERAY_WORKSPACE",
            "LIFERAY_CONTAINER",
            "COM_LIFERAY_LXC_DXP",
        ]

        changes_made = False

        if isinstance(env_args, list):
            new_env = []
            for arg in env_args:
                should_keep = True
                for prefix in blacklisted_prefixes:
                    if arg.startswith(prefix):
                        should_keep = False
                        break
                if should_keep:
                    new_env.append(arg)

            if len(new_env) != len(env_args):
                meta["env_args"] = new_env
                changes_made = True

        if isinstance(custom_env, dict):
            new_custom = {
                k: v
                for k, v in custom_env.items()
                if not any(k.startswith(p) for p in blacklisted_prefixes)
            }
            if len(new_custom) != len(custom_env):
                meta["custom_env"] = json.dumps(new_custom)
                changes_made = True

        if changes_made:
            UI.info("Scrubbed legacy infrastructure variables from project metadata.")
            self.write_meta(meta_file, meta)

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
        self._ensure_network()
        from ldm_core.utils import sanitize_id

        p_id = sanitize_id(project_meta.get("container_name") or paths["root"].name)
        self.scrub_legacy_meta(p_id)
        self.migrate_layout(paths)
        tag, host_name = (
            project_meta.get("tag"),
            project_meta.get("host_name", "localhost"),
        )
        resolved_ip, port = (
            self.get_resolved_ip(host_name) or "127.0.0.1",
            int(project_meta.get("port") or 8080),
        )
        use_ssl, ssl_port = (
            str(project_meta.get("ssl")).lower() == "true",
            int(project_meta.get("ssl_port") or 443),
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

        # License Verification
        lic_status, lic_ok, lic_details = self.check_license_health(
            {"common": paths.get("common"), **paths}, image_tag=tag
        )
        if not lic_ok or lic_ok == "warn":
            UI.warning(f"Project License: {lic_status}")
            if lic_details:
                for detail in lic_details:
                    print(f"  {UI.CYAN}ℹ{UI.COLOR_OFF} {detail}")
            print(
                f"  {UI.WHITE}Tip: Copy your .xml license to the 'common/' folder or the project 'deploy/' folder.{UI.COLOR_OFF}"
            )

        self.sync_logging(paths)

        # 7. Project Properties Validation
        pe_file = paths["files"] / "portal-ext.properties"
        if pe_file.exists():
            prop_status, prop_ok, prop_details = self.validate_properties_file(pe_file)
            if not prop_ok or prop_ok == "warn":
                UI.warning(f"Project Properties: {prop_status}")
                if prop_details:
                    for detail in prop_details:
                        print(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {detail}")

                if not self.non_interactive:
                    if (
                        UI.ask(
                            "Structure issues detected. Continue anyway?", "Y"
                        ).upper()
                        != "Y"
                    ):
                        UI.die("Aborted by user.")

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
                "db_name": project_meta.get("db_name") or "lportal",
                "db_user": project_meta.get("db_user") or "liferay",
                "db_pass": project_meta.get("db_pass") or "liferay",
                **project_meta,
            },
        )

        from ldm_core.utils import check_port

        for svc_name, svc_def in all_services.items():
            for port_mapping in svc_def.get("ports", []):
                parts = port_mapping.split(":")
                if len(parts) >= 2:
                    host_port = parts[-2]
                    if host_port.isdigit() and check_port(host_port):
                        UI.die(
                            f"Port {host_port} (required by '{svc_name}') is already in use by another process.\n"
                            f"Please resolve the conflict or choose a different port."
                        )

        UI.info("Orchestrating project stack...")
        if use_ssl:
            self.setup_infrastructure(resolved_ip, ssl_port, use_ssl=True)

            # Project-specific SSL Setup
            if host_name != "localhost":
                actual_home = get_actual_home()
                cert_dir = actual_home / "liferay-docker-certs"
                self.setup_ssl(cert_dir, host_name)

            # Give Traefik a few seconds to initialize and read dynamic certificates
            time.sleep(5)

        if no_up:
            return

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

        # Use UTC timestamp for the initial waiting message
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        UI.info(
            f"[{timestamp}] Waiting for Liferay to start... (Monitor progress with: {UI.CYAN}ldm logs -f {p_id}{UI.COLOR_OFF})"
        )

        max_timeout, start_time, is_ready, last_reminder = (
            int(project_meta.get("timeout", 900)),
            time.time(),
            False,
            time.time(),
        )
        while time.time() - start_time < max_timeout:
            if time.time() - last_reminder > 60:
                # Use UTC to match Liferay logs
                timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
                UI.info(
                    f"[{timestamp}] Still waiting... Tip: Open a new terminal and run {UI.CYAN}ldm logs -f {p_id}{UI.COLOR_OFF} to see internal progress."
                )
                last_reminder = time.time()
            try:
                # Use a more specific path for readiness to avoid root-level redirects or 404s
                check_url = f"{access_url}/c/portal/layout"
                res = run_command(["curl", "-k", "-I", check_url], check=False)

                if self.verbose:
                    UI.debug(
                        f"Health check: {check_url} -> {res.splitlines()[0] if res else 'No Response'}"
                    )

                if res and ("200" in res or "302" in res or "301" in res):
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
                    status_suffix = f" {UI.RED}(Unresolved){UI.COLOR_OFF}"
                    unresolved.append(ext_domain)
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
            f"  Open Browser:   {UI.CYAN}ldm browser {p_id}{UI.COLOR_OFF}\n  View Logs:      {UI.CYAN}ldm logs -f {p_id}{UI.COLOR_OFF}\n  Stop Project:   {UI.CYAN}ldm stop {p_id}{UI.COLOR_OFF}\n  Container Shell:{UI.CYAN}ldm shell {p_id}{UI.COLOR_OFF}\n  Hot Deploy:     {UI.CYAN}ldm deploy {p_id}{UI.COLOR_OFF}"
        )
        if (
            is_ready
            and str(project_meta.get("browser_launch", "true")).lower() == "true"
        ):
            from ldm_core.utils import open_browser

            UI.info(f"Launching browser: {access_url}/web/guest/home")
            open_browser(f"{access_url}/web/guest/home")
            print(
                f"\n{UI.BYELLOW}💡 Tip:{UI.COLOR_OFF} If you see SSL or connection errors, try a {UI.WHITE}full browser restart{UI.COLOR_OFF} to clear caches.\n"
            )

    def get_default_jvm_args(self):
        """Calculates recommended JVM arguments based on available Docker RAM."""
        try:
            # 1. Get Docker total memory
            docker_info_raw = run_command(
                ["docker", "info", "--format", "{{json .}}"], check=False
            )
            if not docker_info_raw:
                # Absolute fallback if Docker isn't responding
                return "-Xms4g -Xmx12g -XX:MaxMetadataSize=768m -XX:MetaspaceSize=768m"

            info = json.loads(docker_info_raw)
            mem_bytes = info.get("MemTotal", 0)
            if mem_bytes <= 0:
                return "-Xms4g -Xmx12g -XX:MaxMetadataSize=768m -XX:MetaspaceSize=768m"

            mem_gb = mem_bytes / (1024**3)

            # 2. Calculation Logic
            # Goal: Default to 4g/12g if resources allow (requires ~16GB Docker RAM)
            # Otherwise, scale to 25% min / 75% max.
            max_heap_gb = max(4, math.floor(mem_gb * 0.75))
            min_heap_gb = max(2, math.floor(mem_gb * 0.25))

            # Apply upper ceilings (Liferay rarely needs >32GB heap in dev)
            max_heap_gb = min(max_heap_gb, 12 if mem_gb < 24 else 32)
            min_heap_gb = min(min_heap_gb, 4)

            # Metaspace Tuning
            metaspace = "768m"
            if mem_gb > 16:
                metaspace = "1024m"

            # NewSize Tuning (Generally 1/3 to 1/2 of heap)
            # We'll use 1536m as a floor (your snippet) or 25% of max heap
            new_size_mb = max(1536, math.floor((max_heap_gb * 1024) * 0.33))

            return (
                f"-Xms{min_heap_gb * 1024}m -Xmx{max_heap_gb * 1024}m "
                f"-XX:MaxMetaspaceSize={metaspace} -XX:MetaspaceSize={metaspace} "
                f"-XX:NewSize={new_size_mb}m -XX:MaxNewSize={new_size_mb}m"
            )
        except Exception:
            return (
                "-Xms4096m -Xmx12288m -XX:MaxMetaspaceSize=768m -XX:MetaspaceSize=768m"
            )

    def cmd_run(self, project_id=None, is_restart=False):
        # Prioritize the passed project_id (from cmd_restart) over CLI args
        project_id = (
            project_id or self.args.project or getattr(self.args, "project_flag", None)
        )
        if getattr(self.args, "select", False) and not project_id:
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

        # Synchronize project_id with the resolved root name
        project_id = root.name

        is_new_project = not (root / PROJECT_META_FILE).exists()
        project_meta = self.read_meta(root / PROJECT_META_FILE)

        # 1. Resolve Core Config
        tag, host_name = (
            self.args.tag or project_meta.get("tag"),
            self.args.host_name or project_meta.get("host_name") or "localhost",
        )
        db_type = getattr(self.args, "db", None) or project_meta.get("db_type")
        jvm_args = getattr(self.args, "jvm_args", None) or project_meta.get("jvm_args")

        # Performance Overrides (Default to enabled/False unless specified)
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

        # If no JVM args provided or saved, calculate smart defaults
        if not jvm_args:
            jvm_args = self.get_default_jvm_args()
            if self.verbose:
                UI.info(f"Using auto-calculated JVM arguments: {jvm_args}")

        # --- Samples Streamlining ---
        is_samples = getattr(self.args, "samples", False)
        if is_samples:
            # 1. Mandate Custom Hostname
            if host_name == "localhost":
                if self.non_interactive:
                    UI.die(
                        "The --samples project requires a custom hostname (not localhost). "
                        "Please provide one with --host-name."
                    )
                else:
                    UI.warning(
                        "The samples project requires a custom hostname to demonstrate LDM routing."
                    )
                    host_name = UI.ask(
                        "Enter project Virtual Hostname", "samples.local"
                    )
                    if host_name == "localhost":
                        UI.die("Localhost is not supported for the samples project.")

            # 2. Auto-Detect Version (avoid prompt)
            if not tag:
                samples_tag = self.get_samples_tag()
                if samples_tag:
                    UI.info(
                        f"Using Liferay version defined in samples metadata: {samples_tag}"
                    )
                    tag = samples_tag

            # 3. Auto-Detect DB Type
            if not db_type:
                db_type = self.get_samples_db_type()

        if not tag and self.non_interactive:
            UI.die(
                "No Liferay tag specified. In non-interactive mode, use: ldm run <pid> --tag <tag>"
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

            # If user entered something other than a standard release type, treat it as a prefix
            prefix_filter = getattr(self.args, "tag_prefix", None)
            if not prefix_filter and release_type not in ["any", "u", "lts", "qr"]:
                prefix_filter = release_type
                release_type = "any"

            latest_tag = discover_latest_tag(
                api_url,
                release_type=release_type,
                prefix_filter=prefix_filter,
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
            if not db_type:
                db_type = snap_meta.get("db_type")

        if host_name != "localhost" and not self.check_hostname(host_name):
            sys.exit(1)

        # Search Logic: Determine if we should use shared search sidecar
        # Priority: CLI Flag > Saved Meta > Default (True for modern versions)
        sidecar_flag = getattr(self.args, "sidecar", False)
        if sidecar_flag:
            use_shared_search = False
        else:
            meta_search = project_meta.get("use_shared_search")
            if meta_search is not None:
                use_shared_search = str(meta_search).lower() == "true"
            else:
                # Default to True for modern versions (2025.Q1+ or 7.4.13-u100+)
                use_shared_search = (
                    self.parse_version(tag) >= (2025, 1, 0) or tag >= "7.4.13-u100"
                )

        paths = self.setup_paths(project_id)
        self.verify_runtime_environment(paths)

        # Seeding Logic (v2.1.0+)
        # If this is a new project and not explicitly disabled, try to find a seed.
        if (
            is_new_project
            and not getattr(self.args, "no_seed", False)
            and not external_snapshot
            and not getattr(self.args, "samples", False)
        ):
            from ldm_core.utils import download_file, get_seed_url

            # Build config strings for seed lookup
            db_val = db_type or "hypersonic"
            search_val = "shared" if use_shared_search else "sidecar"

            seed_url = get_seed_url(tag, db=db_val, search=search_val)
            if seed_url:
                if (
                    UI.ask(
                        f"Found a pre-initialized 'seed' state for {tag} ({db_val}/{search_val}). Apply it to start faster?",
                        "Y",
                    ).upper()
                    == "Y"
                ):
                    UI.info(f"Downloading seeded state for {tag}...")
                    seed_path = (
                        paths["backups"]
                        / "seeds"
                        / f"seeded-{tag}-{db_val}-{search_val}.tar.gz"
                    )
                    seed_path.parent.mkdir(parents=True, exist_ok=True)

                    if download_file(seed_url, seed_path):
                        UI.info("Applying seeded state...")
                        # We need a meta file for cmd_restore to work
                        seed_snap_dir = seed_path.parent / f"seed-{tag}"
                        seed_snap_dir.mkdir(parents=True, exist_ok=True)
                        shutil.move(seed_path, seed_snap_dir / "files.tar.gz")

                        self.write_meta(
                            seed_snap_dir / "meta",
                            {
                                "meta_version": META_VERSION,
                                "name": f"Seeded State {tag}",
                                "tag": tag,
                                "container": project_id,
                                "search_snapshot": f"seeded-{tag}",
                                "db_type": db_val,
                                "search_mode": search_val,
                            },
                        )

                        # Restore using the standard snapshot logic
                        self.cmd_restore(project_id, backup_dir=str(seed_snap_dir))

                        # Re-read meta after restore to ensure we have the seeded base
                        project_meta = self.read_meta(root / PROJECT_META_FILE)
                        project_meta["seeded"] = "true"
                        project_meta["seed_version"] = tag
                        project_meta["seed_config"] = f"{db_val}/{search_val}"
                        self.write_meta(root / PROJECT_META_FILE, project_meta)

        if getattr(self.args, "samples", False):
            self.sync_samples(paths)

        # 6. Finalize Meta
        ssl_arg = getattr(self.args, "ssl", None)
        if ssl_arg is not None:
            # User used --ssl or --no-ssl explicitly
            ssl_val = ssl_arg
        else:
            # Default logic: Use saved meta, or determine based on hostname for new projects.
            meta_ssl = project_meta.get("ssl")
            if meta_ssl is not None:
                ssl_val = str(meta_ssl).lower() == "true"
            else:
                # Default to SSL ONLY if a custom host_name is provided
                ssl_val = host_name != "localhost"

        project_meta.update(
            {
                "tag": tag,
                "host_name": host_name,
                "container_name": project_id,
                "ssl": str(ssl_val).lower(),
                "ssl_port": str(project_meta.get("ssl_port", 443)),
                "use_shared_search": str(use_shared_search).lower(),
                "db_type": db_type,
                "jvm_args": jvm_args,
                "no_vol_cache": str(no_vol_cache).lower(),
                "no_jvm_verify": str(no_jvm_verify).lower(),
                "no_tld_skip": str(no_tld_skip).lower(),
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
            self.sync_stack(paths, meta, rebuild=getattr(self.args, "rebuild", False))

    def cmd_stop(self, project_id=None, service=None, all_projects=False):
        if all_projects:
            roots = self.get_running_projects()
            if not roots:
                UI.info("No running projects found.")
                return
            UI.heading("Stopping All Running Projects")
            for r in roots:
                self.cmd_stop(project_id=r["path"].name, service=service)
            return

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

    def cmd_restart(self, project_id=None, service=None, all_projects=False):
        if all_projects:
            roots = self.get_running_projects()
            if not roots:
                UI.info("No running projects found.")
                return
            UI.heading("Restarting All Running Projects")
            for r in roots:
                self.cmd_restart(project_id=r["path"].name, service=service)
            return

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
                # Pass the resolved root.name to cmd_run to avoid double-selection
                # or NoneType errors if it was initially None.
                self.cmd_run(root.name)

    def cmd_migrate_search(self, project_id=None):
        """Migrates a project from Sidecar to Global Elasticsearch."""
        root = self.detect_project_path(project_id)
        if not root:
            return

        from ldm_core.utils import sanitize_id

        p_id = sanitize_id(root.name)
        paths = self.setup_paths(p_id)

        # 1. Ensure Liferay is NOT running
        is_running = run_command(
            ["docker", "ps", "-q", "-f", f"name=^{p_id}$"], check=False
        )
        if is_running:
            UI.die(
                f"Project '{p_id}' is currently running. Please stop it first with: ldm stop {p_id}"
            )

        UI.heading(f"Migrating '{p_id}' to Global Search")

        # 2. Check if Global Search is running
        search_running = run_command(
            ["docker", "ps", "-q", "-f", "name=^liferay-search-global$"], check=False
        )
        if not search_running:
            if (
                UI.ask(
                    "Global Search container is not running. Start it now?", "Y"
                ).upper()
                == "Y"
            ):
                self.setup_global_search()
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
        project_meta = self.read_meta(root / PROJECT_META_FILE)
        project_meta["use_shared_search"] = "true"
        self.write_meta(root / PROJECT_META_FILE, project_meta)

        # sync_common_assets will now find the global search running and copy the configs
        self.sync_common_assets(paths)

        UI.success(
            f"Migration complete! Project '{p_id}' is now configured for Global Search."
        )

        if not self.non_interactive:
            if UI.ask("Restart project now?", "Y").upper() == "Y":
                self.cmd_run()

    def cmd_down(self, project_id=None, service=None, all_projects=False):
        if all_projects:
            roots = self.get_running_projects()
            if not roots:
                UI.info("No running projects found.")
                return
            UI.heading("Removing All Running Projects")
            for r in roots:
                self.cmd_down(project_id=r["path"].name, service=service)
            return

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

    def cmd_reseed(self, project_id=None):
        """Resets a project and re-applies its original Liferay seed."""
        root = self.detect_project_path(project_id)
        if not root:
            return

        p_id = root.name
        project_meta = self.read_meta(root / PROJECT_META_FILE)
        tag = project_meta.get("tag")

        if not tag:
            UI.die(f"Project '{p_id}' has no Liferay tag recorded. Cannot re-seed.")

        # Ensure project is STOPPED
        is_running = run_command(
            ["docker", "ps", "-q", "-f", f"name=^{p_id}$"], check=False
        )
        if is_running:
            UI.info(f"Stopping project '{p_id}'...")
            self.cmd_stop(p_id)

        if not self.non_interactive:
            if (
                UI.ask(
                    f"This will wipe ALL data for '{p_id}' and restore the vanilla seed for {tag}. Proceed?",
                    "N",
                ).upper()
                != "Y"
            ):
                UI.die("Operation cancelled.")

        # 1. Surgical Reset
        self.cmd_reset(p_id, target="all")

        # 2. Re-apply Seed logic (stolen from cmd_run)
        from ldm_core.utils import get_seed_url, download_file

        seed_url = get_seed_url(tag)
        if not seed_url:
            UI.die(f"No seed found for version {tag}. Manual reset complete.")

        UI.info(f"Downloading seeded state for {tag}...")
        paths = self.setup_paths(root)
        seed_path = paths["backups"] / "seeds" / f"seeded-{tag}.tar.gz"
        seed_path.parent.mkdir(parents=True, exist_ok=True)

        if download_file(seed_url, seed_path):
            UI.info("Re-applying seeded state...")
            seed_snap_dir = seed_path.parent / f"seed-{tag}"
            seed_snap_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(seed_path, seed_snap_dir / "files.tar.gz")

            self.write_meta(
                seed_snap_dir / "meta",
                {
                    "meta_version": META_VERSION,
                    "name": f"Seeded State {tag}",
                    "tag": tag,
                    "container": p_id,
                    "search_snapshot": f"seeded-{tag}",
                },
            )

            # Restore using the standard snapshot logic
            self.cmd_restore(p_id, backup_dir=str(seed_snap_dir))

            # Update meta
            project_meta = self.read_meta(root / PROJECT_META_FILE)
            project_meta["seeded"] = "true"
            project_meta["seed_version"] = tag
            self.write_meta(root / PROJECT_META_FILE, project_meta)
            UI.success(f"Project '{p_id}' successfully re-seeded to {tag}.")
        else:
            UI.error("Failed to download seed. Manual reset was successful.")

    def cmd_reset(self, project_id=None, target="state"):
        """Surgically resets project data folders (state, search, db)."""
        root = self.detect_project_path(project_id)
        if not root:
            return

        p_id = root.name
        paths = self.setup_paths(p_id)

        # 1. Ensure project is STOPPED
        is_running = run_command(
            ["docker", "ps", "-q", "-f", f"name=^{p_id}$"], check=False
        )
        if is_running:
            UI.die(f"Project '{p_id}' is currently running. Please stop it first.")

        targets = [t.strip().lower() for t in target.split(",")]
        if "all" in targets:
            targets = ["state", "search", "db", "global-search"]

        UI.heading(f"Resetting Data for '{p_id}'")
        cleared = []

        # Target: OSGi State
        if "state" in targets:
            state_dir = paths["state"]
            if state_dir.exists():
                UI.info(f"Clearing OSGi state: {state_dir}")
                shutil.rmtree(state_dir)
                state_dir.mkdir(parents=True, exist_ok=True)
                cleared.append("state")

        # Target: Internal Search (Sidecar)
        if "search" in targets:
            indices_found = False
            for es_dir in ["elasticsearch7", "elasticsearch8"]:
                target_dir = paths["data"] / es_dir
                if target_dir.exists():
                    UI.info(f"Removing internal sidecar indices: {target_dir}")
                    shutil.rmtree(target_dir)
                    indices_found = True
            if indices_found:
                cleared.append("search")

        # Target: Database (Hypersonic and Standard DB Data)
        if "db" in targets:
            # 1. Hypersonic
            db_dir = paths["data"] / "hypersonic"
            if db_dir.exists():
                UI.info(f"Removing Hypersonic database: {db_dir}")
                shutil.rmtree(db_dir)
                cleared.append("db (hypersonic)")

            # 2. Standard DB (PostgreSQL / MySQL)
            db_data = paths["data"] / "db"
            if db_data.exists():
                UI.info(f"Removing Database data volume: {db_data}")
                self.safe_rmtree(db_data)
                db_data.mkdir(parents=True, exist_ok=True)
                cleared.append("db (data volume)")

        # Target: Global Search Indices
        if "global-search" in targets:
            search_name = "liferay-search-global"
            if run_command(["docker", "ps", "-q", "-f", f"name={search_name}"]):
                # We use the standard index prefix ldm-[project-name]-*
                UI.info(f"Removing project indices from global search ({p_id})...")
                run_command(
                    [
                        "docker",
                        "exec",
                        search_name,
                        "curl",
                        "-s",
                        "-X",
                        "DELETE",
                        f"localhost:9200/ldm-{p_id}-*",
                    ],
                    check=False,
                )
                cleared.append("global-search")

        if not cleared:
            UI.info("No data found to reset for requested targets.")
        else:
            UI.success(f"Successfully reset: {', '.join(cleared)}")

    def cmd_infra_down(self):
        """Stops and removes global infrastructure containers."""
        UI.info("Stopping global infrastructure services...")
        for c in [
            "liferay-proxy-global",
            "docker-socket-proxy",
            "liferay-search-global",
        ]:
            if run_command(
                ["docker", "ps", "-a", "-q", "-f", f"name=^{c}$"], check=False
            ):
                run_command(["docker", "rm", "-f", c], check=False)
        UI.success("Infrastructure cleanup complete.")

    def cmd_infra_restart(self):
        """Restarts all global infrastructure services."""
        UI.heading("Restarting Global Infrastructure")

        # Ensure we keep the search flag if it was used
        search_active = run_command(
            ["docker", "ps", "-a", "-q", "-f", "name=^liferay-search-global$"],
            check=False,
        )
        if search_active:
            setattr(self.args, "search", True)

        self.cmd_infra_down()
        self.cmd_infra_setup()

    def cmd_logs(self, project_id=None, service=None, all_projects=False, infra=False):
        if infra:
            infra_map = {
                "proxy": "liferay-proxy-global",
                "traefik": "liferay-proxy-global",
                "es": "liferay-search-global",
                "search": "liferay-search-global",
                "bridge": "docker-socket-proxy",
                "socket": "docker-socket-proxy",
            }

            targets = []
            if service:
                # If service is a list (from CLI), check each item
                services = service if isinstance(service, list) else [service]
                for s in services:
                    s_lower = s.lower()
                    if s_lower in infra_map:
                        targets.append(infra_map[s_lower])
                    else:
                        # Direct container name fallback
                        targets.append(s)
            else:
                # Show all infra logs
                targets = list(set(infra_map.values()))

            if not targets:
                UI.info("No infrastructure services found matching your request.")
                return

            follow = getattr(self.args, "follow", False)
            UI.heading(f"Infrastructure Logs {'(Following)' if follow else ''}")

            # For global infra, we use direct docker logs since they aren't in a single compose file
            for t in targets:
                # Check if running
                if not run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{t}$"], check=False
                ):
                    UI.warning(f"Service '{t}' is not running.")
                    continue

                cmd = ["docker", "logs"]
                if follow:
                    cmd.append("-f")
                cmd.append(t)

                try:
                    UI.info(f"Showing logs for: {t}")
                    run_command(cmd, capture_output=False)
                except KeyboardInterrupt:
                    break
            return

        if all_projects:
            roots = self.get_running_projects()
            if not roots:
                UI.info("No running projects found.")
                return
            if getattr(self.args, "follow", False):
                UI.warning(
                    "Ignoring '--follow' for bulk logs to prevent interleaved stream."
                )

            UI.heading("Logs for All Running Projects")
            for r in roots:
                print(f"\n{UI.WHITE}=== Project: {r['path'].name} ==={UI.COLOR_OFF}")
                self.cmd_logs(project_id=r["path"].name, service=service)
            return

        root = self.detect_project_path(project_id)
        if not root:
            return
        compose_base = get_compose_cmd()
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )
        cmd = compose_base + ["logs"]
        if getattr(self.args, "follow", False):
            cmd.append("-f")
        if service:
            if isinstance(service, list):
                cmd.extend(service)
            else:
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
                subprocess.run(["telnet", "localhost", str(port)])
            except KeyboardInterrupt:
                pass
        else:
            UI.info(
                f"Telnet not found. You can connect manually with: {UI.CYAN}telnet localhost {port}{UI.COLOR_OFF}"
            )

    def cmd_browser(self, project_id=None):
        """Launches the project URL in the system browser."""
        root = None
        if project_id:
            root = self.detect_project_path(project_id)
        else:
            # Identify running projects for selection
            running_roots = self.get_running_projects()
            if not running_roots:
                UI.info("No projects are currently running.")
                # Fall back to any initialized projects if none are running
                selection = self.select_project_interactively(
                    heading="Select Project to Open"
                )
            else:
                selection = self.select_project_interactively(
                    roots=running_roots, heading="Running Projects"
                )

            if selection:
                root = selection["path"]

        if not root:
            return

        meta = self.read_meta(root / PROJECT_META_FILE)
        host_name = meta.get("host_name", "localhost")
        ssl = str(meta.get("ssl")).lower() == "true"
        ssl_port = meta.get("ssl_port", 443)
        port = meta.get("port", 8080)

        proto = "https" if ssl else "http"
        access_url = f"{proto}://{host_name}"
        if (ssl and int(ssl_port) != 443) or (not ssl and int(port) != 80):
            access_url += f":{ssl_port if ssl else port}"

        UI.info(f"Launching browser: {access_url}")
        open_browser(access_url)

    def _ensure_network(self, network_name="liferay-net"):
        """Ensures that the target Docker network exists."""
        if not run_command(["docker", "network", "inspect", network_name], check=False):
            UI.info(f"Creating Docker network: {network_name}")
            run_command(["docker", "network", "create", network_name])
