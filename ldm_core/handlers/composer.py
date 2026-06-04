import json
import math
import os
import platform
from pathlib import Path

from ldm_core.ui import UI
from ldm_core.utils import dict_to_yaml, resolve_dependency_version


class ComposerService:
    """Service for Stack Composition and Metadata translation."""

    def __init__(self, manager=None):
        self.manager = manager

    def get_default_jvm_args(self):
        """Calculates recommended JVM arguments based on available Docker RAM."""
        # LDM-385: Support 'Lean' profile for CI or low-memory environments
        is_lean = (
            getattr(self.manager.args, "lean", False)
            or os.getenv("GITHUB_ACTIONS") == "true"
        )

        if is_lean:
            # Optimized for 7GB GitHub Runners (2GB heap)
            return (
                "-Xms1536m -Xmx2048m -XX:MaxMetaspaceSize=512m "
                "-XX:MetaspaceSize=512m -XX:TieredStopAtLevel=1"
            )

        try:
            # We use self.manager.run_command from the base mixin
            docker_info_raw = self.manager.run_command(
                ["docker", "info", "--format", "{{json .}}"], check=False
            )
            if not docker_info_raw:
                return "-Xms4g -Xmx12g -XX:MaxMetadataSize=768m -XX:MetaspaceSize=768m"

            info = json.loads(docker_info_raw)
            mem_bytes = info.get("MemTotal", 0)
            if mem_bytes <= 0:
                return "-Xms4g -Xmx12g -XX:MaxMetadataSize=768m -XX:MetaspaceSize=768m"

            mem_gb = mem_bytes / (1024**3)
            # 80/20 DESIGN: Leave room for Sidecar and OS
            max_heap_gb = max(4, math.floor(mem_gb * 0.50))
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
                jvm_base += " -XX:TieredStopAtLevel=1"

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
        ssl_arg = getattr(self.manager.args, "ssl", None)
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
        """Generates the docker-compose.yml file using the Builder Pattern."""
        # Ensure paths is a dictionary for subscripting
        if not isinstance(paths, dict):
            paths = self.manager.setup_paths(paths)

        project_name = meta.get("container_name") or paths["root"].name
        host_name = meta.get("host_name", "localhost")
        ssl_enabled = self._is_ssl_active(host_name, meta)

        services = {}

        # Build individual services
        services["liferay"] = self._build_liferay_service(
            paths, meta, host_name, project_name, ssl_enabled, liferay_env
        )

        search_service = self._build_search_service(meta)
        if search_service:
            services["search"] = search_service

        db_service = self._build_db_service(meta, project_name)
        if db_service:
            services["db"] = db_service
            # Add dependency from liferay to db
            services["liferay"]["depends_on"] = {"db": {"condition": "service_healthy"}}

        # Append Microservices/Client Extensions
        ext_services = self._build_extensions_services(
            paths, meta, host_name, project_name, ssl_enabled
        )
        services.update(ext_services)

        compose = {
            "services": services,
            "networks": {"liferay-net": {"external": True}},
        }

        # LDM-381: Ensure all services have standard LDM labels for pruning
        for _, svc_data in services.items():
            if "labels" not in svc_data:
                svc_data["labels"] = []

            # Convert to list if it's a dict (though LDM uses lists)
            if isinstance(svc_data["labels"], dict):
                svc_data["labels"] = [f"{k}={v}" for k, v in svc_data["labels"].items()]

            standard_labels = [
                f"com.liferay.ldm.project={project_name}",
                "com.liferay.ldm.managed=true",
            ]
            for label in standard_labels:
                if label not in svc_data["labels"]:
                    svc_data["labels"].append(label)

        # LDM-369: Add top-level volumes for Named Volumes (data/state)
        named_volumes: dict[str, dict] = {}
        for svc in services.values():
            for vol in svc.get("volumes", []):
                if ":" in vol:
                    host_side = vol.split(":")[0]
                    # If it doesn't look like a path, it's a named volume
                    if not (
                        host_side.startswith(".")
                        or host_side.startswith("/")
                        or "/" in host_side
                        or "\\" in host_side
                    ):
                        # LDM-424: Force explicit volume naming to prevent Docker from prefixing
                        # with the project name (which causes hydration mismatches).
                        named_volumes[host_side] = {"name": host_side}

        if named_volumes:
            compose["volumes"] = named_volumes

        # --- Extensible Stack Archetypes Merge ---
        archetype_name = meta.get("archetype")
        if archetype_name:
            from ldm_core.constants import SCRIPT_DIR

            archetype_overlay_path = (
                SCRIPT_DIR
                / "ldm_core"
                / "resources"
                / "archetypes"
                / archetype_name
                / "compose-overlay.yml"
            )
            if archetype_overlay_path.exists():
                import yaml

                def deep_merge(dict1, dict2):
                    for key, val in dict2.items():
                        if isinstance(val, dict):
                            dict1[key] = deep_merge(dict1.get(key, {}), val)
                        elif isinstance(val, list):
                            dict1[key] = dict1.get(key, []) + val
                        else:
                            dict1[key] = val
                    return dict1

                try:
                    overlay_data = (
                        yaml.safe_load(
                            archetype_overlay_path.read_text(encoding="utf-8")
                        )
                        or {}
                    )
                    compose = deep_merge(compose, overlay_data)

                    # Dynamic Clustered Image Sync
                    if (
                        "liferay2" in compose["services"]
                        and "liferay" in compose["services"]
                    ):
                        compose["services"]["liferay2"]["image"] = compose["services"][
                            "liferay"
                        ]["image"]

                except Exception as e:
                    UI.error(f"Failed to merge archetype overlay: {e}")

        from ldm_core.utils import safe_write_text

        safe_write_text(paths["compose"], dict_to_yaml(compose))

    def is_using_named_volumes(self):
        """Returns True if the current platform/configuration uses Docker Named Volumes for data/state."""
        # Current policy: Named Volumes are used on all platforms to prevent locking errors.
        return True

    def _build_liferay_service(
        self, paths, meta, host_name, project_name, ssl_enabled, base_env
    ):
        """Constructs the primary Liferay service definition."""
        tag = str(meta.get("tag") or "latest")
        scale = int(meta.get("scale_liferay", 1))
        port = meta.get("port", 8080)
        use_shared_search = str(meta.get("use_shared_search", "true")).lower() == "true"

        jvm_opts = str(meta.get("jvm_args", ""))
        if "-Dfile.encoding" not in jvm_opts:
            jvm_opts += " -Dfile.encoding=UTF8"
        if "-Duser.timezone" not in jvm_opts:
            jvm_opts += " -Duser.timezone=GMT"

        mandatory_opens = [
            "java.base/java.lang=ALL-UNNAMED",
            "java.base/java.lang.invoke=ALL-UNNAMED",
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
            "java.management/sun.management=ALL-UNNAMED",
            "java.rmi/sun.rmi.transport=ALL-UNNAMED",
            "jdk.management/com.sun.management.internal=ALL-UNNAMED",
            "jdk.zipfs/jdk.nio.zipfs=ALL-UNNAMED",
        ]
        for opt in mandatory_opens:
            flag = f"--add-opens={opt}"
            if flag not in jvm_opts:
                jvm_opts += f" {flag}"

        if "-Djdk.util.zip.disableZip64ExtraFieldValidation=true" not in jvm_opts:
            jvm_opts += " -Djdk.util.zip.disableZip64ExtraFieldValidation=true"

        # LDM-422/423: Self-Tuning JVM for Reindexing (Performance & Stability Win)
        # If a reindex is scheduled, we must scale up the compiler resources.
        reindex_active = str(meta.get("reindex_required", "false")).lower() == "true"
        if reindex_active:
            # 1. Disable TieredStopAtLevel (Enable C2 compiler for reindex performance)
            if "-XX:TieredStopAtLevel=1" in jvm_opts:
                jvm_opts = jvm_opts.replace("-XX:TieredStopAtLevel=1", "")

            # 2. Increase CodeCache (Prevent NoSuchMethodException/VirtualMachineError)
            if "-XX:ReservedCodeCacheSize" not in jvm_opts:
                jvm_opts += " -XX:ReservedCodeCacheSize=512m"
        elif "-Xms" in jvm_opts and "-XX:TieredStopAtLevel=1" not in jvm_opts:
            # ONLY apply these to Darwin/Windows VMs where bundle resolution is slow
            if platform.system().lower() in ["darwin", "windows"]:
                jvm_opts += " -XX:TieredStopAtLevel=1"

        # LDM-369: JVM argument deduplication
        # We use a dictionary-style merge where the last flag wins for any duplicated key
        opt_map = {}
        for opt in jvm_opts.split(" "):
            if not opt:
                continue
            if opt.startswith("-D"):
                key = opt.split("=", 1)[0]
                opt_map[key] = opt
            elif opt.startswith("-Xm"):
                # Use 4 chars to distinguish -Xms and -Xmx
                key = opt[:4]
                opt_map[key] = opt
            elif opt.startswith("-XX:"):
                key = opt.split("=", 1)[0]
                opt_map[key] = opt
            else:
                opt_map[opt] = opt

        liferay_env = []
        liferay_env.append(f"LIFERAY_JVM_OPTS={' '.join(opt_map.values())}")
        liferay_env.append(
            "LIFERAY_LOG4J2_CONFIGURATION_FILE=/opt/liferay/osgi/log4j/portal-log4j-ext.xml"
        )

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
            # LDM-Sidecar: We must explicitly tell Liferay which ports to use for Sidecar
            # because LDM defaults to 9201 to avoid global search collisions.
            # We use portal-ext.properties to ensure these take precedence over .config files.
            es_port = int(meta.get("es_port", 9201))
            tcp_port = es_port + 100

            def get_es_props(ver):
                base = f"module.framework.properties.com.liferay.portal.search.elasticsearch{ver}.configuration.ElasticsearchConfiguration"
                return {
                    f"{base}.operationMode": "EMBEDDED",
                    f"{base}.sidecarHttpPort": str(es_port),
                    f"{base}.sidecarTransportTcpPort": str(tcp_port),
                    f"{base}.transportTcpPort": str(tcp_port),
                    f"{base}.sidecarNetworkHost": "0.0.0.0",  # nosec B104
                }

            self.manager.config.update_portal_ext(paths, get_es_props(7))
            self.manager.config.update_portal_ext(paths, get_es_props(8))

            if "-Dliferay.auto.deploy.interval" not in jvm_opts:
                jvm_opts += " -Dliferay.auto.deploy.interval=5000"

            liferay_env.extend(
                [
                    "LIFERAY_ELASTICSEARCH_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=false",
                    "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=true",
                    "LIFERAY_ELASTICSEARCH_PERIOD_OPERATION_PERIOD_MODE=EMBEDDED",
                ]
            )

        # LDM-422: Automatic Reindex on Startup
        if str(meta.get("reindex_required", "false")).lower() == "true":
            liferay_env.append("LIFERAY_INDEX_PERIOD_ON_PERIOD_STARTUP=true")
            liferay_env.append("LIFERAY_INDEX_PERIOD_ON_PERIOD_STARTUP_PERIOD_DELAY=30")

        # LDM-424: Inject Smart Store Implementation
        dl_store = meta.get("dl_store_impl")
        if dl_store:
            liferay_env.append(f"LIFERAY_DL_PERIOD_STORE_PERIOD_IMPL={dl_store}")

        custom_env_str = meta.get("custom_env", "{}")
        try:
            custom_env_dict = json.loads(custom_env_str)
        except Exception:
            custom_env_dict = {}
            if custom_env_str:
                for pair in custom_env_str.split(","):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        custom_env_dict[k] = v

        has_jdbc_env = False
        for k, v in custom_env_dict.items():
            liferay_env.append(f"{k}={v}")
            if k.startswith("LIFERAY_JDBC_PERIOD_"):
                has_jdbc_env = True

        db_type = meta.get("db_type", "postgresql")
        if db_type == "external":
            if not has_jdbc_env:
                self.manager.config.update_portal_ext(  # type: ignore[attr-defined]
                    paths,
                    {
                        "jdbc.default.url": meta.get("jdbc_url", ""),
                        "jdbc.default.username": meta.get("jdbc_user", ""),
                        "jdbc.default.password": meta.get("jdbc_pass", ""),
                    },
                )
            liferay_env.append("LIFERAY_HSQL_PERIOD_ENABLED=false")
        elif db_type in ["mysql", "mariadb"]:
            driver = (
                resolve_dependency_version(tag, "jdbc_driver_mysql")
                or "org.mariadb.jdbc.Driver"
            )
            dialect = "org.hibernate.dialect.MariaDB103Dialect"
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
            if not has_jdbc_env:
                self.manager.config.update_portal_ext(  # type: ignore[attr-defined]
                    paths,
                    {
                        "jdbc.default.driverClassName": driver,
                        "jdbc.default.url": url,
                        "jdbc.default.username": "lportal",
                        "jdbc.default.password": "test",  # nosec B105
                        "hibernate.dialect": dialect,
                    },
                )
            liferay_env.append("LIFERAY_HSQL_PERIOD_ENABLED=false")
        elif db_type == "postgresql":
            driver = (
                resolve_dependency_version(tag, "jdbc_driver_postgresql")
                or "org.postgresql.Driver"
            )
            url = "jdbc:postgresql://db:5432/lportal"
            dialect = (
                resolve_dependency_version(tag, "jdbc_dialect_postgresql")
                or "org.hibernate.dialect.PostgreSQL10Dialect"
            )
            if not has_jdbc_env:
                self.manager.config.update_portal_ext(  # type: ignore[attr-defined]
                    paths,
                    {
                        "jdbc.default.driverClassName": driver,
                        "jdbc.default.url": url,
                        "jdbc.default.username": "lportal",
                        "jdbc.default.password": "test",  # nosec B105
                        "hibernate.dialect": dialect,
                    },
                )
            liferay_env.append("LIFERAY_HSQL_PERIOD_ENABLED=false")

        port_list = []
        if host_name == "localhost" or not ssl_enabled:
            bind_ip = self.manager.get_resolved_ip(host_name) or "127.0.0.1"
            port_list.append(f"{bind_ip}:{port}:8080")
        elif ssl_enabled:
            self.manager.config.update_portal_ext(  # type: ignore[attr-defined]
                paths,
                {
                    "web.server.host": host_name,
                    "web.server.https.port": "443",
                    "web.server.protocol": "https",
                },
            )

        # LDM-381: Determine base image and sanitized tag
        tag = str(meta.get("tag") or "latest")
        is_portal = str(meta.get("portal", "false")).lower() == "true"

        # Explicit tag prefixes take precedence and are stripped
        if tag.startswith("dxp-"):
            is_portal = False
            tag = tag[4:]
        elif tag.startswith("portal-"):
            is_portal = True
            tag = tag[7:]

        # Heuristic: Is it a legacy portal update tag? (e.g. 7.4.13-u102)
        is_legacy_portal_u_tag = (
            "u" in tag and "." in tag and tag.index("u") > tag.rindex(".")
        )

        image = meta.get("image_tag")
        if not image:
            # LDM-381: Portal is deprecated, default to DXP
            if is_portal or is_legacy_portal_u_tag:
                image = f"liferay/portal:{tag}"
            else:
                image = f"liferay/dxp:{tag}"
        elif str(image).startswith("-"):
            # It's a suffix
            suffix = str(image)
            image_base = (
                "liferay/portal"
                if (is_portal or is_legacy_portal_u_tag)
                else "liferay/dxp"
            )
            image = f"{image_base}:{tag}{suffix}"

        depends_on = []
        if db_type not in ["hypersonic", "external"]:
            depends_on.append("db")

        # 80/20 DESIGN: SELinux compatibility for Fedora/RHEL
        z_label = ":z" if platform.system().lower() == "linux" else ""

        service = {
            "image": image,
            "ports": port_list,
            "environment": liferay_env,
            "labels": [
                f"com.liferay.ldm.project={project_name}",
                "com.liferay.ldm.managed=true",
            ],
            "volumes": [
                f"{paths['deploy'].as_posix()}:/mnt/liferay/deploy{z_label}",
                f"{paths['files'].as_posix()}:/mnt/liferay/files{z_label}",
                f"{paths['scripts'].as_posix()}:/mnt/liferay/scripts{z_label}",
                f"{project_name}-data:/opt/liferay/data",
                f"{paths['modules'].as_posix()}:/opt/liferay/osgi/modules{z_label}",
                f"{paths['cx'].as_posix()}:/opt/liferay/osgi/client-extensions{z_label}",
                f"{paths['portal_log4j'].as_posix()}:/opt/liferay/osgi/log4j{z_label}",
            ],
            "networks": ["liferay-net"],
        }
        if depends_on:
            service["depends_on"] = depends_on

        cpu_limit = meta.get("cpu_limit")
        mem_limit = meta.get("mem_limit")
        if cpu_limit or mem_limit:
            service["deploy"] = {"resources": {"limits": {}}}
            if cpu_limit:
                service["deploy"]["resources"]["limits"]["cpus"] = str(cpu_limit)
            if mem_limit:
                service["deploy"]["resources"]["limits"]["memory"] = (
                    str(mem_limit) + "M"
                )

        if scale == 1:
            liferay_container = meta.get("liferay_container_name") or project_name
            service["container_name"] = liferay_container
            # Host-mapped state and logs only for non-scaled instances
            service["volumes"].extend(
                [
                    f"{liferay_container}-state:/opt/liferay/osgi/state",
                    f"{paths['logs'].as_posix()}:/opt/liferay/logs{z_label}",
                ]
            )
        else:
            liferay_env.extend(
                [
                    "LIFERAY_CLUSTER_PERIOD_LINK_PERIOD_ENABLED=true",
                    "LIFERAY_LUCENE_PERIOD_REPLICATE_PERIOD_WRITE=true",
                ]
            )

        if ssl_enabled:
            traefik_id = f"{project_name}-main"
            service["labels"].extend(
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

        return service

    def _build_search_service(self, meta):
        """Constructs the Sidecar Elasticsearch service if required."""
        # LDM-369: If sidecar is active, we do NOT want a separate search container.
        # Liferay will use its internal sidecar search inside the main container.
        return

    def _build_db_service(self, meta, project_name):
        """Constructs the Database service (MySQL/PostgreSQL) if required."""
        db_type = meta.get("db_type", "postgresql")
        if db_type == "external":
            return None

        tag = str(meta.get("tag") or "latest")
        db_container = meta.get("db_container_name") or f"{project_name}-db"
        scale = int(meta.get("scale_db", 1))

        if db_type in ["postgresql", "postgres"]:
            pg_ver = resolve_dependency_version(tag, "postgresql") or "16"
            service = {
                "image": f"postgres:{pg_ver}",
                "command": [
                    "postgres",
                    "-c",
                    "shared_buffers=1024MB",
                    "-c",
                    "max_connections=200",
                ],
                "environment": {
                    "POSTGRES_PASSWORD": "test",  # nosec B105
                    "POSTGRES_USER": "lportal",
                    "POSTGRES_DB": "lportal",
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U lportal"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 10,
                    "start_period": "60s",
                },
                "networks": ["liferay-net"],
            }
            if scale == 1:
                service["container_name"] = db_container
            else:
                service["deploy"] = {"replicas": scale}
            return service
        if db_type in ["mysql", "mariadb"]:
            is_modern = False
            try:
                major_ver = int(tag.split(".", maxsplit=1)[0])
                if major_ver >= 2024:
                    is_modern = True
            except (ValueError, IndexError):
                pass

            target_mysql = resolve_dependency_version(tag, "mysql")
            target_mariadb = resolve_dependency_version(tag, "mariadb")

            auth_flags = []
            if db_type == "mysql":
                mysql_image_ver = (
                    target_mysql if target_mysql else ("8.4" if is_modern else "5.7")
                )
                try:
                    ver_parts = mysql_image_ver.split(".")
                    if (
                        len(ver_parts) >= 2
                        and int(ver_parts[0]) == 8
                        and int(ver_parts[1]) >= 4
                    ):
                        auth_flags = ["--mysql-native-password=ON"]
                    else:
                        auth_flags = [
                            "--default-authentication-plugin=mysql_native_password"
                        ]
                except ValueError:
                    auth_flags = [
                        "--default-authentication-plugin=mysql_native_password"
                    ]

            image = (
                f"mysql:{mysql_image_ver}"
                if db_type == "mysql"
                else (f"mariadb:{target_mariadb}" if target_mariadb else "mariadb:10.6")
            )
            service = {
                "image": image,
                "command": [
                    "mysqld",
                    "--character-set-server=utf8mb4",
                    "--collation-server=utf8mb4_unicode_ci",
                    "--character-set-filesystem=utf8mb4",
                    "--lower_case_table_names=1",
                    "--bind-address=0.0.0.0",
                    "--skip-name-resolve",
                    *auth_flags,
                ],
                "environment": {
                    "MYSQL_ROOT_PASSWORD": "test",  # nosec B105
                    "MYSQL_USER": "lportal",
                    "MYSQL_PASSWORD": "test",  # nosec B105
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
            if scale == 1:
                service["container_name"] = db_container
            else:
                service["deploy"] = {"replicas": scale}
            return service
        return None

    def _build_extensions_services(
        self, paths, meta, host_name, project_name, ssl_enabled
    ):
        # 4. Append Microservices/Client Extensions
        services = {}
        extensions = []
        if self.manager and hasattr(self.manager, "workspace"):
            extensions = self.manager.workspace.scan_client_extensions(
                paths["root"], paths["cx"], paths["ce_dir"]
            )
        else:
            # Fallback for standalone/mock usage
            from ldm_core.handlers.workspace import WorkspaceService

            cx_handler = WorkspaceService(self.manager)
            extensions = cx_handler.scan_client_extensions(
                paths["root"], paths["cx"], paths["ce_dir"]
            )
        for ext in extensions:
            if ext.get("deploy") and ext.get("is_service"):
                ext_id = ext.get("id")
                svc_id = f"{project_name}-{ext_id}"
                ms_port = ext.get("loadBalancer", {}).get("targetPort", 8080)
                scale = int(meta.get(f"scale_{ext_id}", 1))

                labels = ["traefik.enable=true"]
                services[svc_id] = {
                    "image": f"{svc_id}:latest",
                    "build": {"context": Path(ext["path"]).as_posix()},
                    "pull_policy": "build",
                    "networks": ["liferay-net"],
                    "labels": labels,
                }

                if scale == 1:
                    services[svc_id]["container_name"] = svc_id
                else:
                    services[svc_id]["deploy"] = {"replicas": scale}

                if ssl_enabled:
                    traefik_svc_id = f"{svc_id}-svc"
                    labels.extend(
                        [
                            "traefik.docker.network=liferay-net",
                            f"traefik.http.routers.{traefik_svc_id}.rule=Host(`{ext['id']}.{host_name}`)",
                            f"traefik.http.routers.{traefik_svc_id}.tls=true",
                            f"traefik.http.services.{traefik_svc_id}.loadbalancer.server.port={ms_port}",
                        ]
                    )
                    services[svc_id]["labels"] = labels
        return services
