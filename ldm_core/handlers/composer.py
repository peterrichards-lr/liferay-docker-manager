import json
import math
import platform
import re
from ldm_core.handlers.base import BaseHandler
from ldm_core.utils import dict_to_yaml, resolve_dependency_version


class ComposerHandler(BaseHandler):
    """Specialized handler for Stack Composition and Metadata translation."""

    def __init__(self, args=None):
        super().__init__(args)

    def get_default_jvm_args(self):
        """Calculates recommended JVM arguments based on available Docker RAM."""
        try:
            # We use self.run_command from the base mixin
            docker_info_raw = self.run_command(
                ["docker", "info", "--format", "{{json .}}"], check=False
            )
            if not docker_info_raw:
                return "-Xms4g -Xmx12g -XX:MaxMetadataSize=768m -XX:MetaspaceSize=768m"

            info = json.loads(docker_info_raw)
            mem_bytes = info.get("MemTotal", 0)
            if mem_bytes <= 0:
                return "-Xms4g -Xmx12g -XX:MaxMetadataSize=768m -XX:MetaspaceSize=768m"

            mem_gb = mem_bytes / (1024**3)
            max_heap_gb = max(4, math.floor(mem_gb * 0.75))
            min_heap_gb = max(2, math.floor(mem_gb * 0.25))
            max_heap_gb = min(max_heap_gb, 12 if mem_gb < 24 else 32)
            min_heap_gb = min(min_heap_gb, 4)

            metaspace = "768m" if mem_gb <= 16 else "1024m"
            new_size_mb = max(1536, math.floor((max_heap_gb * 1024) * 0.33))

            jvm_base = ""
            os_name = platform.system().lower()
            if os_name in ["darwin", "windows"]:
                # Optimization for local dev environments (macOS/Windows Docker VM)
                # TieredStopAtLevel=1 speeds up bundle resolution significantly.
                jvm_base += " -XX:TieredStopAtLevel=1 -Xverify:none"

            return (
                f"-Xms{min_heap_gb * 1024}m -Xmx{max_heap_gb * 1024}m "
                f"-XX:MaxMetaspaceSize={metaspace} -XX:MetaspaceSize={metaspace} "
                f"-XX:NewSize={new_size_mb}m -XX:MaxNewSize={new_size_mb}m"
                f"{jvm_base}"
            )
        except Exception:
            return "-Xms4096m -Xmx12288m -XX:MaxMetaspaceSize=768m -XX:MetaspaceSize=768m -XX:TieredStopAtLevel=1"

    def _is_ssl_active(self, host_name, meta):
        """Determines if SSL/Proxy routing should be enabled for a project."""
        is_literal_localhost = host_name == "localhost"

        # Priority: 1. CLI Arg, 2. Meta 'ssl', 3. Meta 'use_ssl', 4. Default (True for custom)
        ssl_arg = getattr(self.args, "ssl", None)
        meta_ssl = meta.get("ssl", meta.get("use_ssl"))

        if ssl_arg is not None:
            active = ssl_arg
        elif meta_ssl is not None:
            active = str(meta_ssl).lower() == "true"
        else:
            active = not is_literal_localhost

        if is_literal_localhost:
            return False

        return active

    def write_docker_compose(self, paths, meta, liferay_env=None):
        """Generates the docker-compose.yml file for the project."""
        # Ensure paths is a dictionary for subscripting
        if not isinstance(paths, dict):
            paths = self.setup_paths(paths)

        tag = str(meta.get("tag") or "latest")
        db_type = meta.get("db_type", "hypersonic")
        use_shared_search = str(meta.get("use_shared_search", "true")).lower() == "true"
        host_name = meta.get("host_name", "localhost")
        project_name = meta.get("container_name") or paths["root"].name

        ssl_enabled = self._is_ssl_active(host_name, meta)
        scale = int(meta.get("scale_liferay", 1))

        # Base Liferay Service
        jvm_opts = str(meta.get("jvm_args", ""))

        if "-Dfile.encoding" not in jvm_opts:
            jvm_opts += " -Dfile.encoding=UTF8"
        if "-Duser.timezone" not in jvm_opts:
            jvm_opts += " -Duser.timezone=GMT"

        # JDK 17+ Mandatory Module Exports
        mandatory_opens = [
            "java.base/java.lang=ALL-UNNAMED",
            "java.base/java.lang.reflect=ALL-UNNAMED",
            "java.base/java.net=ALL-UNNAMED",
            "java.base/java.util=ALL-UNNAMED",
            "java.base/java.util.concurrent=ALL-UNNAMED",
            "java.base/java.text=ALL-UNNAMED",
            "java.base/java.time=ALL-UNNAMED",
            "java.base/sun.net.www.protocol.http=ALL-UNNAMED",
            "java.base/sun.net.www.protocol.https=ALL-UNNAMED",
            "java.base/sun.nio.ch=ALL-UNNAMED",
            "java.base/sun.security.action=ALL-UNNAMED",
            "java.base/sun.security.ssl=ALL-UNNAMED",
            "java.base/sun.security.util=ALL-UNNAMED",
            "java.base/sun.security.x509=ALL-UNNAMED",
            "java.base/sun.util.calendar=ALL-UNNAMED",
            "java.security.sasl/conf=ALL-UNNAMED",
            "java.management/sun.management=ALL-UNNAMED",
            "jdk.management/com.sun.management.internal=ALL-UNNAMED",
        ]
        for opt in mandatory_opens:
            flag = f"--add-opens={opt}"
            if flag not in jvm_opts:
                jvm_opts += f" {flag}"

        if "-Xms" in jvm_opts and "-XX:TieredStopAtLevel=1" not in jvm_opts:
            jvm_opts += " -XX:TieredStopAtLevel=1"

        jvm_opts = jvm_opts.strip()

        image = meta.get("image_tag")
        if not image:
            # Smart Image Mapping
            # 1. If it contains 'u', it's always Portal
            if "u" in tag:
                image = f"liferay/portal:{tag}"
            # 2. If it has a known suffix, use it as is (DXP)
            elif any(s in tag for s in ["-lts", "-qr", "-ga"]):
                image = f"liferay/dxp:{tag}"
            # 3. Modern Quarterly Tags (e.g. 2026.q1.4) without suffix default to LTS
            elif re.match(r"^\d{4}\.q[1-4]\.\d+$", tag):
                image = f"liferay/dxp:{tag}-lts"
            # 4. Default Fallback
            else:
                image = f"liferay/dxp:{tag}"

        port = meta.get("port", 8080)

        if liferay_env is None:
            liferay_env = ["LIFERAY_HOME=/opt/liferay"]

        liferay_env.append(f"LIFERAY_JVM_OPTS={jvm_opts}")

        if use_shared_search:
            liferay_env.extend(
                [
                    "LIFERAY_ELASTICSEARCH_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=true",
                    "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=false",
                    "LIFERAY_ELASTICSEARCH_PERIOD_CONNECTION_PERIOD_URL=http://liferay-search-global:9200",
                    f"LIFERAY_ELASTICSEARCH_PERIOD_INDEX_PERIOD_NAME_PERIOD_PREFIX=ldm-{project_name}-",
                ]
            )
        else:
            liferay_env.extend(
                [
                    "LIFERAY_ELASTICSEARCH_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=false",
                    "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=true",
                ]
            )
        # Add custom environment variables from metadata
        custom_env_str = meta.get("custom_env", "{}")
        try:
            custom_env_dict = json.loads(custom_env_str)
        except Exception:
            # Fallback for old comma-separated format
            custom_env_dict = {}
            if custom_env_str:
                for pair in custom_env_str.split(","):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        custom_env_dict[k] = v

        has_jdbc_env = False
        for k, v in custom_env_dict.items():
            env_line = f"{k}={v}"
            liferay_env.append(env_line)
            if k.startswith("LIFERAY_JDBC_PERIOD_"):
                has_jdbc_env = True

        if db_type in ["mysql", "mariadb"]:
            driver = "org.mariadb.jdbc.Driver"
            url = (
                "jdbc:mariadb://db:3306/lportal?"
                "characterEncoding=UTF-8"
                "&dontTrackOpenResources=true"
                "&holdResultsOpenOverStatementClose=true"
                "&serverTimezone=GMT"
                "&useFastDateParsing=false"
                "&useUnicode=true"
                "&useSSL=false"
                "&allowPublicKeyRetrieval=true"
                "&rewriteBatchedStatements=true"
                "&prepStmtCacheSize=1000"
                "&prepStmtCacheSqlLimit=2048"
                "&useLocalSessionState=true"
                "&useLocalTransactionState=true"
                "&permitMysqlScheme=true"
            )
            dialect = "org.hibernate.dialect.MariaDB103Dialect"

            if not has_jdbc_env:
                self.update_portal_ext(
                    paths,
                    {
                        "jdbc.default.enabled": "true",
                        "jdbc.default.driverClassName": driver,
                        "jdbc.default.url": url,
                        "jdbc.default.username": "lportal",
                        "jdbc.default.password": "test",
                        "hibernate.dialect": dialect,
                    },
                )

            liferay_env.append("LIFERAY_HSQL_PERIOD_ENABLED=false")

        elif db_type == "postgresql":
            driver = "org.postgresql.Driver"
            url = "jdbc:postgresql://db:5432/lportal"
            dialect = "org.hibernate.dialect.PostgreSQL10Dialect"

            if not has_jdbc_env:
                self.update_portal_ext(
                    paths,
                    {
                        "jdbc.default.enabled": "true",
                        "jdbc.default.driverClassName": driver,
                        "jdbc.default.url": url,
                        "jdbc.default.username": "lportal",
                        "jdbc.default.password": "test",
                        "hibernate.dialect": dialect,
                    },
                )
            liferay_env.append("LIFERAY_HSQL_PERIOD_ENABLED=false")

        # Port Binding
        resolved_ip = self.get_resolved_ip(host_name)
        ssl_active = self._is_ssl_active(host_name, meta)

        port_list = []
        if host_name == "localhost" or not ssl_active:
            bind_ip = resolved_ip or "127.0.0.1"
            port_list.append(f"{bind_ip}:{port}:8080")
        elif ssl_active:
            # Inject mandatory web server properties for SSL alignment
            self.update_portal_ext(
                paths,
                {
                    "web.server.host": host_name,
                    "web.server.https.port": "443",
                    "web.server.protocol": "https",
                },
            )

        liferay_service = {
            "image": image,
            "ports": port_list,
            "environment": liferay_env,
            "labels": [f"com.liferay.ldm.project={project_name}"],
            "volumes": [
                f"{paths['deploy']}:/mnt/liferay/deploy",
                f"{paths['files']}:/mnt/liferay/files",
                f"{paths['data']}:/storage/liferay/data",
                f"{paths['configs']}:/opt/liferay/osgi/configs",
            ],
            "networks": ["liferay-net"],
        }

        # Resource Limits
        cpu_limit = meta.get("cpu_limit")
        mem_limit = meta.get("mem_limit")
        if cpu_limit or mem_limit:
            liferay_service["deploy"] = {"resources": {"limits": {}}}
            if cpu_limit:
                liferay_service["deploy"]["resources"]["limits"]["cpus"] = str(
                    cpu_limit
                )
            if mem_limit:
                liferay_service["deploy"]["resources"]["limits"]["memory"] = (
                    str(mem_limit) + "M"
                )

        if scale == 1:
            liferay_service["container_name"] = project_name
            liferay_service["volumes"].append(
                f"{paths['state']}:/opt/liferay/osgi/state"
            )
            liferay_service["volumes"].append(f"{paths['logs']}:/opt/liferay/logs")
        else:
            liferay_env.extend(
                [
                    "LIFERAY_CLUSTER_PERIOD_LINK_PERIOD_ENABLED=true",
                    "LIFERAY_LUCENE_PERIOD_REPLICATE_PERIOD_WRITE=true",
                ]
            )

        # SSL Labels
        if ssl_enabled:
            traefik_id = f"{project_name}-main"
            liferay_service["labels"].extend(
                [
                    "traefik.enable=true",
                    "traefik.docker.network=liferay-net",
                    f"traefik.http.routers.{traefik_id}.rule=Host(`{host_name}`)",
                    f"traefik.http.routers.{traefik_id}.tls=true",
                    f"traefik.http.routers.{traefik_id}.entrypoints=websecure",
                    f"traefik.http.routers.{traefik_id}.tls.domains[0].main={host_name}",
                    f"traefik.http.routers.{traefik_id}.tls.domains[0].sans=*.{host_name}",
                    f"traefik.http.services.{traefik_id}.loadbalancer.server.port=8080",
                ]
            )

        services = {"liferay": liferay_service}

        # Client Extensions / Microservices
        if hasattr(self, "scan_client_extensions"):
            extensions = self.scan_client_extensions(
                paths["root"], paths["cx"], paths["ce_dir"]
            )
        else:
            from ldm_core.handlers.workspace import WorkspaceHandler

            cx_handler = WorkspaceHandler(self.args)
            extensions = cx_handler.scan_client_extensions(
                paths["root"], paths["cx"], paths["ce_dir"]
            )

        for ext in extensions:
            if ext.get("deploy") and ext.get("is_service"):
                svc_id = f"{project_name}-{ext['id']}"
                ms_port = ext.get("loadBalancer", {}).get("targetPort", 8080)
                services[svc_id] = {
                    "image": f"{svc_id}:latest",
                    "build": {"context": str(ext["path"])},
                    "networks": ["liferay-net"],
                    "labels": ["traefik.enable=true"],
                }
                if ssl_enabled:
                    traefik_svc_id = f"{svc_id}-svc"
                    services[svc_id]["labels"].extend(
                        [
                            "traefik.docker.network=liferay-net",
                            f"traefik.http.routers.{traefik_svc_id}.rule=Host(`{ext['id']}.{host_name}`)",
                            f"traefik.http.routers.{traefik_svc_id}.tls=true",
                            f"traefik.http.services.{traefik_svc_id}.loadbalancer.server.port={ms_port}",
                        ]
                    )

        if not use_shared_search:
            es_ver = resolve_dependency_version(tag, "elasticsearch") or "7.17.10"
            services["search"] = {
                "image": f"elasticsearch:{es_ver}",
                "environment": ["discovery.type=single-node"],
                "networks": ["liferay-net"],
            }

        if db_type == "postgresql":
            pg_ver = resolve_dependency_version(tag, "postgresql") or "13"
            services["db"] = {
                "image": f"postgres:{pg_ver}",
                "environment": {
                    "POSTGRES_PASSWORD": "test",
                    "POSTGRES_USER": "lportal",
                    "POSTGRES_DB": "lportal",
                },
                "networks": ["liferay-net"],
            }
        elif db_type in ["mysql", "mariadb"]:
            is_modern = False
            try:
                major_ver = int(tag.split(".")[0])
                if major_ver >= 2024:
                    is_modern = True
            except (ValueError, IndexError):
                pass

            auth_flags = []
            if db_type == "mysql":
                if is_modern:
                    auth_flags = ["--mysql-native-password=ON"]
                else:
                    auth_flags = [
                        "--default-authentication-plugin=mysql_native_password"
                    ]

            services["db"] = {
                "image": (
                    resolve_dependency_version(tag, "mysql")
                    or ("mysql:8.4" if is_modern else "mysql:5.7")
                )
                if db_type == "mysql"
                else (resolve_dependency_version(tag, "mariadb") or "mariadb:10.6"),
                "command": [
                    "mysqld",
                    "--character-set-server=utf8mb4",
                    "--collation-server=utf8mb4_unicode_ci",
                    "--character-set-filesystem=utf8mb4",
                    "--lower_case_table_names=1",
                    "--bind-address=0.0.0.0",
                    "--skip-name-resolve",
                ]
                + auth_flags,
                "environment": {
                    "MYSQL_ROOT_PASSWORD": "test",
                    "MYSQL_USER": "lportal",
                    "MYSQL_PASSWORD": "test",
                    "MYSQL_DATABASE": "lportal",
                    "MYSQL_TCP_PORT": "3306",
                },
                "healthcheck": {
                    "test": [
                        "CMD",
                        "mysqladmin",
                        "ping",
                        "-h",
                        "127.0.0.1",
                        "-uroot",
                        "-ptest",
                    ],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 10,
                    "start_period": "60s",
                },
                "networks": ["liferay-net"],
            }
            liferay_service["depends_on"] = {"db": {"condition": "service_healthy"}}

        compose = {
            "services": services,
            "networks": {"liferay-net": {"external": True}},
        }
        paths["compose"].write_text(dict_to_yaml(compose))
