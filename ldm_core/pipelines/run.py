"""
Orchestrates the main 'ldm run' pipeline.
"""

import contextlib
import platform
import time
import typing
from pathlib import Path

from ldm_core.pipelines.base import Pipeline, PipelineContext, PipelineStage
from ldm_core.pipelines.validation import ValidationStage as SharedValidationStage
from ldm_core.ui import UI
from ldm_core.utils import get_actual_home, get_compose_cmd


class RunPipelineContext(PipelineContext):
    """Strongly typed context for the Run pipeline."""

    def __init__(self, manager, **kwargs):
        super().__init__(**kwargs)
        self.manager = manager

        # Wrap mock args to prevent mock attribute contamination
        if type(manager.args).__name__ in ("MagicMock", "Mock"):

            class SafeArgsWrapper:
                def __init__(self, original):
                    self.__dict__["_original"] = original

                def __getattr__(self, name):
                    val = getattr(self._original, name)
                    if type(val).__name__ in ("MagicMock", "Mock"):
                        return None
                    return val

                def __setattr__(self, name, value):
                    setattr(self._original, name, value)

            self.manager.args = SafeArgsWrapper(manager.args)

        self.set("total_start", kwargs.get("total_start") or time.time())
        self.set("is_new_project", False)
        self.set("init_success", False)
        paths = kwargs.get("paths")
        if paths and not isinstance(paths, dict):
            paths = self.manager.setup_paths(paths)
        self.set("paths", paths or {})
        self.set("project_meta", kwargs.get("project_meta", {}))
        self.set("is_restart", kwargs.get("is_restart", False))
        self.set("project_id", kwargs.get("project_id"))
        self.set("no_up", kwargs.get("no_up"))
        self.set("browser", kwargs.get("browser"))
        self.set("rebuild", kwargs.get("rebuild", False))
        self.set("no_wait", kwargs.get("no_wait", False))
        self.set("show_summary", kwargs.get("show_summary", True))
        self.set("follow", kwargs.get("follow", False))


class ProjectInitializationStage(PipelineStage):
    """Handles project selection, discovery, and path setup."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        paths = context.get("paths")
        project_meta = context.get("project_meta")
        if paths and project_meta:
            context.set("root", paths.get("root"))
            return

        project_id = context.get("project_id")

        project_id = (
            project_id
            or manager.args.project
            or getattr(manager.args, "project_flag", None)
        )
        if getattr(manager.args, "select", False) and not project_id:
            if manager.non_interactive:
                UI.die("Project selection is not supported in non-interactive mode.")
            selection = manager.select_project_interactively(
                heading="Available Projects"
            )
            if not selection:
                context.stopped = True
                return
            if selection.get("new"):
                project_id = None
            else:
                project_id = selection["path"].name

        root = manager.detect_project_path(project_id, for_init=True)
        if not root:
            if manager.non_interactive:
                UI.die("Project not found and no name provided to initialize.")
            default_name = f"ldm-{int(time.time())}"
            project_id = UI.ask("Enter a new project name to initialize", default_name)
            if not project_id:
                context.stopped = True
                return
            root = manager.detect_project_path(project_id, for_init=True)
            if not root:
                UI.die("Failed to resolve project path.")

        project_id = root.name
        is_new_project = not any(
            (root / f).exists() for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]
        )
        if is_new_project:
            UI.print_banner()
            if getattr(manager.args, "vanilla", False):
                UI.info("Vanilla start requested: Bypassing pre-warmed seeding.")

        paths = manager.setup_paths(root)
        project_meta = manager.read_meta(paths["root"])

        scale_list = getattr(manager.args, "scale_list", None)
        if scale_list:
            for arg in scale_list:
                if "=" in arg:
                    service, count = arg.split("=", 1)
                    if count.isdigit():
                        project_meta[f"scale_{service}"] = count
            manager.write_meta(paths["root"], project_meta)

        context.set("project_id", project_id)
        context.set("root", root)
        context.set("is_new_project", is_new_project)
        context.set("paths", paths)
        context.set("project_meta", project_meta)
        return

    def rollback(self, context: PipelineContext) -> None:
        context = typing.cast(RunPipelineContext, context)
        is_new_project = context.get("is_new_project")
        init_success = context.get("init_success")
        root = context.get("root")
        project_id = context.get("project_id")

        if is_new_project and not init_success:
            if root and root.exists():
                UI.info(f"Cleaning up failed initialization: {root}")
                context.manager.safe_rmtree(root)
            if project_id:
                context.manager.unregister_project(project_id)


class RuntimeValidationStage(PipelineStage):
    """Validates runtime, Docker engine state, port collisions, and downgrade constraints."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        project_meta = context.get("project_meta")
        project_id = context.get("project_id")
        is_restart = context.get("is_restart")
        paths = context.get("paths")

        from ldm_core.docker_service import DockerService

        container_name = project_meta.get("container_name") or project_id
        no_up = context.get("no_up")
        if no_up is None:
            no_up = getattr(manager.args, "no_up", False)

        if not no_up and not is_restart:
            if DockerService.is_running(container_name):
                if manager.non_interactive:
                    UI.die(
                        f"Project '{project_id}' is already running. Use 'ldm restart' to apply updates, or 'ldm stop' it first."
                    )
                elif not UI.confirm(
                    f"Project '{project_id}' is already running. Reconfigure and restart?",
                    "Y",
                ):
                    context.stopped = True
                    return
                context.set("is_restart", True)

        tag = project_meta.get("tag")
        db_type = project_meta.get("db_type", "postgresql")
        from ldm_core.utils import resolve_dependency_version

        current_pg_ver = None
        if db_type in ["postgresql", "postgres"]:
            current_pg_ver = resolve_dependency_version(tag, "postgresql") or "16"

        current_mysql_ver = None
        if db_type in ["mysql", "mariadb"]:
            if db_type == "mysql":
                current_mysql_ver = resolve_dependency_version(tag, "mysql") or "5.7"
            else:
                current_mysql_ver = resolve_dependency_version(tag, "mariadb") or "10.6"

        current_es_major = "8"
        if tag:
            es_version = resolve_dependency_version(tag, "elasticsearch")
            if es_version:
                current_es_major = es_version.split(".")[0]

        if not getattr(manager.args, "force_downgrade", False):
            last_lr_ver = project_meta.get("last_run_liferay_version")
            if (
                last_lr_ver
                and tag
                and manager.parse_version(tag) < manager.parse_version(last_lr_ver)
            ):
                UI.die(
                    f"Downgrade detected: Liferay version tag changed from '{last_lr_ver}' to '{tag}'. "
                    f"This can cause schema corruption. Use '--force-downgrade' to bypass."
                )

            last_pg_ver = project_meta.get("last_run_postgres_version")
            if last_pg_ver and current_pg_ver:
                try:
                    last_major = last_pg_ver.split(".")[0]
                    curr_major = current_pg_ver.split(".")[0]
                    if last_major != curr_major:
                        if manager.parse_version(
                            current_pg_ver
                        ) > manager.parse_version(last_pg_ver):
                            UI.die(
                                f"Incompatible database directory: PostgreSQL version changed from '{last_pg_ver}' (major version {last_major}) to '{current_pg_ver}' (major version {curr_major}). "
                                f"PostgreSQL does not support in-place major version upgrades on the same data directory.\n"
                                f"To resolve this, please:\n"
                                f"  1. Back up your database if needed (e.g. running your old version instance and exporting).\n"
                                f"  2. Reset the database container and volume: ldm reset {paths['root'].name} --db\n"
                                f"  3. Restart the project to initialize a new clean database container.\n"
                                f"  4. Restore your database snapshot."
                            )
                except Exception:
                    pass

            if (
                last_pg_ver
                and current_pg_ver
                and manager.parse_version(current_pg_ver)
                < manager.parse_version(last_pg_ver)
            ):
                UI.die(
                    f"Downgrade detected: PostgreSQL version changed from '{last_pg_ver}' to '{current_pg_ver}'. "
                    f"PostgreSQL does not support automatic database directory downgrades. Use '--force-downgrade' to bypass."
                )

            last_mysql_ver = project_meta.get("last_run_mysql_version")
            if last_mysql_ver and current_mysql_ver:
                try:
                    last_major = last_mysql_ver.split(".")[0]
                    curr_major = current_mysql_ver.split(".")[0]
                    if last_major != curr_major:
                        if manager.parse_version(
                            current_mysql_ver
                        ) > manager.parse_version(last_mysql_ver):
                            UI.die(
                                f"Incompatible database directory: {db_type.upper()} version changed from '{last_mysql_ver}' (major version {last_major}) to '{current_mysql_ver}' (major version {curr_major}). "
                                f"{db_type.upper()} does not support in-place major version upgrades on the same data directory.\n"
                                f"To resolve this, please:\n"
                                f"  1. Back up your database if needed (e.g. running your old version instance and exporting).\n"
                                f"  2. Reset the database container and volume: ldm reset {paths['root'].name} --db\n"
                                f"  3. Restart the project to initialize a new clean database container.\n"
                                f"  4. Restore your database snapshot."
                            )
                except Exception:
                    pass

            last_es_major = project_meta.get("last_run_elasticsearch_major")
            if last_es_major and last_es_major != current_es_major:
                es_dir_name = f"elasticsearch{current_es_major}"
                es_path = paths["data"] / es_dir_name
                if es_path.exists():
                    UI.warning(
                        f"Upgrade detected: Elasticsearch version changed from major '{last_es_major}' to '{current_es_major}'."
                    )
                    UI.info(
                        f"Automatically clearing stale search indices at {es_path} to prevent container startup crashes..."
                    )
                    from ldm_core.utils import safe_rmtree

                    with contextlib.suppress(Exception):
                        safe_rmtree(es_path)

        context.set("current_pg_ver", current_pg_ver)
        context.set("current_mysql_ver", current_mysql_ver)
        context.set("current_es_major", current_es_major)
        return


class ConfigResolutionStage(PipelineStage):
    """Resolves tags, databases, archtypes, and constructs project configuration."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        project_meta = context.get("project_meta")
        paths = context.get("paths")
        project_id = context.get("project_id")

        no_up = context.get("no_up")
        if no_up is None:
            no_up = getattr(manager.args, "no_up", False)

        tag_latest = getattr(manager.args, "tag_latest", False)
        prefix = getattr(manager.args, "tag_prefix", None)
        if tag_latest or prefix:
            tag = None
        else:
            tag = (
                manager.args.tag
                or project_meta.get("tag")
                or manager.defaults.get("tag")
            )
        is_portal = (
            getattr(manager.args, "portal", False)
            or str(project_meta.get("portal", manager.defaults.get("portal"))).lower()
            == "true"
        )

        if tag:
            from ldm_core.utils import resolve_liferay_docker_tag

            resolved_tag, resolved_is_portal = resolve_liferay_docker_tag(tag, manager)
            if resolved_tag:
                tag = resolved_tag
                is_portal = resolved_is_portal
            elif tag.startswith("dxp-"):
                is_portal = False
                tag = tag[4:]
            elif tag.startswith("portal-"):
                is_portal = True
                tag = tag[7:]

        host_name = (
            manager.args.host_name
            or project_meta.get("host_name")
            or manager.defaults.get("host_name")
            or "localhost"
        )

        if getattr(manager.args, "ssl", None) is True and not getattr(
            manager.args, "host_name", None
        ):
            if not manager.non_interactive:
                host_name = UI.ask("Enter project Virtual Hostname", host_name)

        db_type = (
            getattr(manager.args, "db", None)
            or project_meta.get("db_type")
            or manager.defaults.get("db_type")
            or "postgresql"
        )

        archetype_name = getattr(manager.args, "archetype", None) or project_meta.get(
            "archetype"
        )
        if archetype_name:
            from ldm_core.constants import SCRIPT_DIR

            archetype_dir = (
                SCRIPT_DIR / "ldm_core" / "resources" / "archetypes" / archetype_name
            )
            if not archetype_dir.exists():
                UI.die(
                    f"Archetype '{archetype_name}' not found. Available archetypes: {[d.name for d in (SCRIPT_DIR / 'ldm_core' / 'resources' / 'archetypes').iterdir() if d.is_dir()]}"
                )
            project_meta["archetype"] = archetype_name

        if db_type == "external" and not project_meta.get("jdbc_url"):
            UI.heading("External Database Configuration")
            project_meta["jdbc_url"] = UI.ask(
                "JDBC URL (e.g. jdbc:postgresql://host:5432/db)",
                "jdbc:postgresql://db:5432/lportal",
            )
            project_meta["jdbc_user"] = UI.ask("Database Username", "liferay")
            project_meta["jdbc_pass"] = UI.ask("Database Password", "liferay")

        jvm_args = getattr(manager.args, "jvm_args", None) or project_meta.get(
            "jvm_args"
        )
        port_val = getattr(manager.args, "port", None) or project_meta.get(
            "port", manager.defaults.get("port")
        )
        port = int(port_val) if port_val is not None else 8080

        project_meta["root"] = str(paths["root"].resolve())
        project_meta["project_name"] = project_id

        base_container_name = project_meta.get("container_name") or project_id
        project_meta["container_name"] = base_container_name
        project_meta["liferay_container_name"] = base_container_name
        project_meta["db_container_name"] = f"{base_container_name}-db"
        project_meta["tunnel_container_name"] = f"{base_container_name}-lfr-tunnel"

        ssl_val = manager.composer._is_ssl_active(host_name, project_meta)

        if not no_up:
            port = manager._pre_flight_checks(
                host_name, port, ssl_enabled=ssl_val, meta=project_meta
            )
        else:
            manager.check_registry_collisions(
                project_id, paths["root"], host_name=host_name
            )

        project_meta["port"] = port

        if getattr(manager.args, "reindex", False):
            (paths["root"] / ".ldm_reindex").touch()
            project_meta["reindex_required"] = "true"

        no_vol_cache = (
            getattr(manager.args, "no_vol_cache", False)
            or str(project_meta.get("no_vol_cache", "false")).lower() == "true"
        )

        is_external_volume = platform.system().lower() == "darwin" and str(
            paths["root"]
        ).startswith("/Volumes/")
        if is_external_volume and not getattr(manager.args, "internal_state", None):
            if str(project_meta.get("internal_state", "false")).lower() != "true":
                UI.info(
                    "External volume detected. Automatically enabling '--internal-state' for stability."
                )
                project_meta["internal_state"] = "true"

        internal_state = (
            getattr(manager.args, "internal_state", False)
            or str(project_meta.get("internal_state", "false")).lower() == "true"
        )
        no_jvm_verify = (
            getattr(manager.args, "no_jvm_verify", False)
            or str(project_meta.get("no_jvm_verify", "false")).lower() == "true"
        )
        no_tld_skip = (
            getattr(manager.args, "no_tld_skip", False)
            or str(project_meta.get("no_tld_skip", "false")).lower() == "true"
        )

        env_type = getattr(manager.args, "env_type", None) or project_meta.get(
            "env_type", "dev"
        )
        cpu_limit = getattr(manager.args, "cpu_limit", None) or project_meta.get(
            "cpu_limit"
        )
        mem_limit = getattr(manager.args, "mem_limit", None) or project_meta.get(
            "mem_limit"
        )

        if not jvm_args:
            jvm_args = manager.composer.get_default_jvm_args()

        is_samples = getattr(manager.args, "samples", False)
        if is_samples:
            config_handler = manager.config
            if host_name == "localhost":
                if manager.non_interactive:
                    UI.die("--samples requires a custom hostname.")
                host_name = UI.ask("Enter project Virtual Hostname", "samples.local")
            if not tag:
                tag = config_handler.get_samples_tag()
            if not db_type:
                db_type = config_handler.get_samples_db_type()

        if not tag:
            tag_latest = getattr(manager.args, "tag_latest", False)
            prefix = getattr(manager.args, "tag_prefix", None)

            can_discover = tag_latest or bool(prefix)
            if manager.non_interactive:
                can_discover = True

            from ldm_core.constants import API_BASE_DXP, API_BASE_PORTAL
            from ldm_core.utils import discover_latest_tag

            api_base = API_BASE_PORTAL if is_portal else API_BASE_DXP
            default_rt = manager.defaults.get("release_type", "lts")
            rt = getattr(manager.args, "release_type", None)
            if not rt:
                rt = "any" if prefix else default_rt

            if not can_discover:
                if manager.verbose:
                    UI.info(
                        f"Pre-resolving latest {rt.upper()} release to populate default prompt..."
                    )
                default_resolved_tag = discover_latest_tag(
                    api_base,
                    release_type=rt,
                    prefix_filter=prefix,
                    verbose=manager.verbose,
                )
                ans = UI.ask(
                    "Release type (lts|u|qr|latest), prefix, or specific tag",
                    default_resolved_tag,
                )
                if ans == default_resolved_tag:
                    tag = default_resolved_tag
                elif ans.lower() in ["any", "latest", "u", "lts", "qr"]:
                    release_type = "any" if ans.lower() == "latest" else ans.lower()
                    if manager.verbose:
                        UI.info(f"Discovering latest {ans.upper()} release...")
                    tag = discover_latest_tag(
                        api_base, release_type=release_type, verbose=manager.verbose
                    )
                    if not tag:
                        UI.die(f"Could not find any tags for release type: {ans}")
                else:
                    if manager.verbose:
                        UI.info(f"Discovering latest tag matching prefix: {ans}...")
                    tag = discover_latest_tag(
                        api_base,
                        release_type="any",
                        prefix_filter=ans,
                        verbose=manager.verbose,
                    )
                    if not tag:
                        tag = ans
            else:
                if manager.verbose:
                    UI.info("Automatically discovering latest Liferay tag...")
                tag = discover_latest_tag(
                    api_base,
                    release_type=rt,
                    prefix_filter=prefix,
                    verbose=manager.verbose,
                )
                if not tag:
                    UI.die(
                        "Failed to discover latest Liferay tag. Please specify one explicitly with -t."
                    )
                if manager.verbose:
                    UI.success(f"Using tag: {tag}")

        external_snapshot = getattr(manager.args, "snapshot", None)
        if external_snapshot:
            snap_path = Path(external_snapshot).resolve()
            snap_meta = manager.read_meta(snap_path)
            tag = tag or snap_meta.get("tag")
            db_type = db_type or snap_meta.get("db_type")

        if tag and tag != project_meta.get("tag"):
            from ldm_core.utils import validate_liferay_tag

            if manager.verbose:
                UI.info(f"Validating tag '{tag}' against Liferay releases...")
            if not validate_liferay_tag(tag):
                UI.warning(
                    f"Tag '{tag}' is not listed in official Liferay releases. If this is not a custom image, the Docker pull may fail."
                )

        is_share = (
            getattr(manager.args, "share", False) is True
            or getattr(manager.args, "expose", False) is True
            or str(project_meta.get("share", "false")).lower() == "true"
        )
        share_subdomain = getattr(
            manager.args, "share_subdomain", None
        ) or project_meta.get("share_subdomain")
        share_image = getattr(manager.args, "share_image", None) or project_meta.get(
            "share_image"
        )
        share_inspector = (
            getattr(manager.args, "share_inspector", False) is True
            or str(project_meta.get("share_inspector", "false")).lower() == "true"
        )

        share_domain = getattr(manager.args, "share_domain", None) or project_meta.get(
            "share_domain"
        )
        share_provider = getattr(
            manager.args, "share_provider", None
        ) or project_meta.get("share_provider")

        if is_share and getattr(manager.args, "expose", False) is True:
            share_provider = "ngrok"

        if is_share and share_provider != "ngrok":
            share_provider, share_domain = manager.share.resolve_share_config(
                project_meta
            )

        if not share_provider:
            share_provider = "lfr-tunnel"

        is_expose = (
            getattr(manager.args, "expose", False) is True
            or str(project_meta.get("expose", "false")).lower() == "true"
            or (is_share and share_provider == "ngrok")
        )
        if is_expose:
            auth_token = manager.config.get_ngrok_auth_token()
            if not auth_token:
                UI.info(
                    "An ngrok Auth Token is required to use the expose feature (it enables custom host headers and HTTPS)."
                )
                UI.info(
                    f"You can find yours at: {UI.CYAN}https://dashboard.ngrok.com/get-started/your-authtoken{UI.COLOR_OFF}"
                )
                auth_token = UI.ask("Enter your ngrok Auth Token")
                if auth_token:
                    manager.config.set_ngrok_auth_token(auth_token)
                    UI.success("Saved ngrok token to global configuration.")
                else:
                    UI.warning("No token provided. Ngrok will not be configured.")
                    is_expose = False
                    if hasattr(manager.args, "expose"):
                        manager.args.expose = False
                    is_share = False

        from ldm_core.utils import resolve_infrastructure_mode

        search_mode = resolve_infrastructure_mode(
            "search_mode", project_meta, manager.defaults
        )
        use_shared_search = search_mode == "shared"
        if getattr(manager.args, "sidecar", False):
            use_shared_search = False

        persist_osgi_arg = getattr(manager.args, "persist_osgi", None)
        if persist_osgi_arg is not None:
            persist_osgi = persist_osgi_arg
        else:
            persist_osgi = (
                str(project_meta.get("persist_osgi", "false")).lower() == "true"
            )

        no_captcha = (
            getattr(manager.args, "no_captcha", False)
            or str(project_meta.get("no_captcha", "false")).lower() == "true"
        )
        fast_login = (
            getattr(manager.args, "fast_login", False)
            or str(project_meta.get("fast_login", "false")).lower() == "true"
        )

        features = getattr(manager.args, "feature", None)
        if features:
            flat_features = []
            for f in features:
                flat_features.extend([x.strip() for x in f.split(",") if x.strip()])
            project_meta["features"] = ",".join(flat_features)

        project_meta.update(
            {
                "project_name": project_id,
                "tag": tag or tag_latest or "",
                "portal": str(is_portal).lower(),
                "host_name": host_name,
                "container_name": project_id,
                "ssl": str(ssl_val).lower(),
                "db_type": db_type or project_meta.get("db_type", "postgresql"),
                "port": port,
                "jvm_args": jvm_args,
                "use_shared_search": str(use_shared_search).lower(),
                "no_vol_cache": str(no_vol_cache).lower(),
                "internal_state": str(internal_state).lower(),
                "no_jvm_verify": str(no_jvm_verify).lower(),
                "no_tld_skip": str(no_tld_skip).lower(),
                "no_captcha": str(no_captcha).lower(),
                "fast_login": str(fast_login).lower(),
                "persist_osgi": str(persist_osgi).lower(),
                "features": project_meta.get("features", ""),
                "env_type": env_type,
                "cpu_limit": cpu_limit,
                "mem_limit": mem_limit,
                "expose": str(is_expose).lower(),
                "share": str(is_share).lower(),
                "share_subdomain": share_subdomain or "",
                "share_provider": share_provider,
                "share_image": share_image or "",
                "share_inspector": str(share_inspector).lower(),
                "share_domain": share_domain or "",
                "archetype": archetype_name or project_meta.get("archetype", ""),
            }
        )

        context.set("host_name", host_name)
        context.set("tag", tag)
        context.set("db_type", db_type)
        context.set("use_shared_search", use_shared_search)
        context.set("is_samples", is_samples)
        context.set("external_snapshot", external_snapshot)


class EnvironmentSetupStage(PipelineStage):
    """Initializes external volumes, seeds templates, and clears obsolete locks."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        project_meta = context.get("project_meta")
        paths = context.get("paths")
        is_new_project = context.get("is_new_project")
        tag = context.get("tag")
        db_type = context.get("db_type")
        project_id = context.get("project_id")

        if is_new_project and manager.assets._ensure_seeded(tag, db_type, paths):
            from ldm_core.constants import SEED_VERSION

            project_meta = manager.read_meta(paths["root"])
            project_meta["seeded"] = "true"
            project_meta["seed_version"] = str(SEED_VERSION)
            manager.write_meta(paths["root"], project_meta)
            if hasattr(manager, "config") and hasattr(manager.config, "track_roi"):
                manager.config.track_roi(840, "first-boot seeding")

            if project_meta.get("archetype"):
                from ldm_core.constants import SCRIPT_DIR

                archetype_dir = (
                    SCRIPT_DIR
                    / "ldm_core"
                    / "resources"
                    / "archetypes"
                    / project_meta["archetype"]
                )
                if archetype_dir.exists():
                    import shutil

                    for item in archetype_dir.iterdir():
                        if item.name not in ["archetype.json", "compose-overlay.yml"]:
                            dest = paths["root"] / item.name
                            if item.is_dir():
                                shutil.copytree(item, dest, dirs_exist_ok=True)
                            else:
                                shutil.copy2(item, dest)
                    if project_meta["archetype"] == "keycloak-sso":
                        manager.runtime._generate_keycloak_realm(paths["root"])

            context.set("init_success", True)
            manager.register_project(
                project_id, paths["root"], host_name=project_meta.get("host_name")
            )
        elif is_new_project:
            context.set("init_success", True)
            manager.register_project(
                project_id, paths["root"], host_name=project_meta.get("host_name")
            )

        no_up = context.get("no_up")
        if no_up is None:
            no_up = getattr(manager.args, "no_up", False)
        if not no_up:
            manager.verify_runtime_environment(paths)

        if str(project_meta.get("persist_osgi", "false")).lower() == "true":
            osgi_state_dir = paths["state"]
            tag_marker = osgi_state_dir / ".ldm_tag"
            if osgi_state_dir.exists():
                with contextlib.suppress(Exception):
                    saved_tag = (
                        tag_marker.read_text().strip() if tag_marker.exists() else None
                    )
                    if saved_tag != tag:
                        UI.warning(
                            f"OSGi state invalidation: Liferay tag changed from '{saved_tag}' to '{tag}'. Wiping state to prevent bundle conflicts."
                        )
                        import shutil

                        for item in osgi_state_dir.iterdir():
                            if item.is_dir():
                                shutil.rmtree(item, ignore_errors=True)
                            else:
                                item.unlink(missing_ok=True)
            osgi_state_dir.mkdir(parents=True, exist_ok=True)
            if tag:
                tag_marker.write_text(tag)

        es_data = paths["data"] / "elasticsearch8"
        use_volumes = manager.composer.is_using_named_volumes()
        if es_data.exists() and not use_volumes:
            UI.detail("Clearing stale search locks and enforcing permissions...")
            for lock_file in es_data.rglob("write.lock"):
                with contextlib.suppress(Exception):
                    lock_file.unlink()
            if platform.system().lower() != "windows":
                from ldm_core.utils import run_command

                run_command(["chmod", "-R", "777", str(es_data)], check=False)

        is_samples = context.get("is_samples")
        if is_samples:
            manager.config.sync_samples(paths)

        manager.write_meta(paths["root"], project_meta)


class ComposerStage(PipelineStage):
    """Generates compose definitions and applies overrides."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        project_meta = context.get("project_meta")
        paths = context.get("paths")
        host_name = context.get("host_name")
        use_shared_search = context.get("use_shared_search")
        tag = context.get("tag")

        last_lr_ver = project_meta.get("last_run_liferay_version")
        is_upgrade = False
        if last_lr_ver and tag:
            try:
                is_upgrade = manager.parse_version(tag) > manager.parse_version(
                    last_lr_ver
                )
            except Exception:
                pass

        upgrade_db = False
        if is_upgrade:
            backup_on_upgrade = getattr(manager.args, "backup_on_upgrade", False)
            no_backup_on_upgrade = getattr(manager.args, "no_backup_on_upgrade", False)

            if not backup_on_upgrade and not no_backup_on_upgrade:
                if not manager.non_interactive:
                    UI.warning(
                        f"Upgrade detected: Liferay version is changing from '{last_lr_ver}' to '{tag}'."
                    )
                    if UI.confirm(
                        "Would you like to take a database backup snapshot before proceeding?",
                        default=True,
                    ):
                        backup_on_upgrade = True

            if backup_on_upgrade:
                from ldm_core.utils import sanitize_id

                container_name = sanitize_id(
                    project_meta.get("liferay_container_name")
                    or project_meta.get("container_name")
                    or paths["root"].name
                )
                db_container = project_meta.get("db_container_name")
                if not db_container:
                    db_container = f"{container_name}-db"

                db_type_val = project_meta.get("db_type", "postgresql")
                from ldm_core.utils import resolve_infrastructure_mode

                db_mode = resolve_infrastructure_mode(
                    "database_mode", project_meta, manager.defaults
                )
                use_shared_db = db_mode == "shared"

                if db_type_val not in ["hypersonic", "external"]:
                    is_running = manager.run_command(
                        ["docker", "ps", "-q", "-f", f"name=^{db_container}$"]
                    )
                    compose_file = paths["root"] / "docker-compose.yml"
                    if compose_file.exists() and not is_running:
                        UI.info(
                            "Starting database container temporarily to take a snapshot backup..."
                        )
                        db_args = (
                            ["up", "-d", "db"] if not use_shared_db else ["up", "-d"]
                        )
                        manager.run_command(
                            [*get_compose_cmd(), "-f", str(compose_file), *db_args]
                        )
                        time.sleep(5)

                    snapshot_name = f"Pre-upgrade snapshot to {tag}"
                    try:
                        manager.snapshot.cmd_snapshot(
                            context.get("project_id"), name=snapshot_name
                        )
                        UI.success(
                            f"Database backup snapshot '{snapshot_name}' created successfully."
                        )
                    except Exception as e:
                        UI.warning(f"Failed to create pre-upgrade database backup: {e}")

            if getattr(manager.args, "upgrade_db", False):
                upgrade_db = True
            elif getattr(manager.args, "no_upgrade_db", False):
                upgrade_db = False
            elif not manager.non_interactive:
                UI.warning(
                    "New Liferay versions often require a database schema upgrade."
                )
                if UI.confirm(
                    "Do you want to run Liferay's database auto-upgrade tool on startup?",
                    default=True,
                ):
                    upgrade_db = True

        liferay_env = ["LIFERAY_HOME=/opt/liferay"]
        if upgrade_db:
            liferay_env.append(
                "LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true"
            )

        if not use_shared_search:
            is_es8 = manager.parse_version(tag) >= (2024, 1, 0) if tag else True
            es_ver = "8" if is_es8 else "7"
            es_main_conf = (
                paths.get("configs", paths["root"] / "osgi" / "configs")
                / f"com.liferay.portal.search.elasticsearch{es_ver}.configuration.ElasticsearchConfiguration.config"
            )
            if es_main_conf.exists():
                UI.warning(
                    f"Custom Elasticsearch OSGi configs detected in '{es_main_conf.parent.name}', but LDM Shared Search is disabled."
                )
                search_mode_arg = getattr(manager.args, "search_mode", None)
                choice = "1"
                if search_mode_arg == "sidecar" or getattr(
                    manager.args, "sidecar", False
                ):
                    choice = "2"
                elif search_mode_arg == "shared":
                    choice = "3"
                elif search_mode_arg == "remote":
                    choice = "1"
                elif not manager.non_interactive:
                    UI.info("How would you like to resolve this search configuration?")
                    UI.info(
                        "  [1] Keep configs: Connect to my own external Remote cluster (Default)"
                    )
                    UI.info(
                        "  [2] Delete configs: Fallback to LDM Sidecar (Internal) mode"
                    )
                    UI.info(
                        "  [3] Delete configs: Migrate to LDM Global (Shared) Search"
                    )
                    choice = UI.ask("Select an option [1/2/3]", "1").strip()
                if choice == "2":
                    es_main_conf.unlink()
                    es_conn_conf = es_main_conf.with_name(
                        f"com.liferay.portal.search.elasticsearch{es_ver}.configuration.ElasticsearchConnectionConfiguration.config"
                    )
                    if es_conn_conf.exists():
                        es_conn_conf.unlink()
                    UI.success("Removed custom configs. Proceeding with Sidecar mode.")
                elif choice == "3":
                    es_main_conf.unlink()
                    es_conn_conf = es_main_conf.with_name(
                        f"com.liferay.portal.search.elasticsearch{es_ver}.configuration.ElasticsearchConnectionConfiguration.config"
                    )
                    if es_conn_conf.exists():
                        es_conn_conf.unlink()
                    use_shared_search = True
                    project_meta["use_shared_search"] = "true"
                    manager.write_meta(paths["root"], project_meta)
                    context.set("use_shared_search", True)
                    UI.success("Migrating to Global Shared Search.")
                else:
                    UI.info(
                        "Keeping custom configs. LDM Sidecar injection will be bypassed."
                    )

        from ldm_core.utils import resolve_infrastructure_mode

        db_mode = resolve_infrastructure_mode(
            "database_mode",
            project_meta,
            manager.defaults,
            getattr(manager.args, "database_mode", None),
        )
        use_shared_db = db_mode == "shared"
        context.set("use_shared_db", use_shared_db)
        if use_shared_db or use_shared_search:
            UI.info("Utilizing Global Shared Infrastructure")

        if host_name != "localhost":
            liferay_env.extend(
                [
                    "LIFERAY_WEB_PERIOD_SERVER_PERIOD_DISPLAY_PERIOD_NODE_PERIOD_NAME=true",
                    "LIFERAY_REDIRECT_PERIOD_URL_PERIOD_IPS_PERIOD_ALLOWED=127.0.0.1,0.0.0.0/0",
                ]
            )

        manager.infra._ensure_network()
        ssl_enabled = str(project_meta.get("ssl", "false")).lower() == "true"
        ssl_port = project_meta.get("ssl_port", 443)
        no_up = context.get("no_up")
        if no_up is None:
            no_up = getattr(manager.args, "no_up", False)

        if ssl_enabled or getattr(manager.args, "search", False) or use_shared_db:
            infra_start = time.time()
            resolved_ip = manager.get_resolved_ip(host_name) or "127.0.0.1"
            if ssl_enabled and not no_up:
                ssl_start = time.time()
                actual_home = get_actual_home()
                cert_dir = actual_home / "liferay-docker-certs"
                manager.infra.setup_ssl(cert_dir, host_name)
                if manager.verbose:
                    UI.debug(
                        f"SSL certificate generation took: {UI.format_duration(time.time() - ssl_start)}"
                    )

            ssl_port = manager.infra.setup_infrastructure(
                resolved_ip,
                ssl_port,
                use_ssl=ssl_enabled,
                quiet=getattr(manager.args, "quiet", False),
                use_shared_search=use_shared_search,
                use_shared_db=use_shared_db,
            )
            project_meta["ssl_port"] = ssl_port

            if use_shared_db and not no_up:
                from ldm_core.utils import sanitize_id

                db_name = f"lportal_{sanitize_id(context.get('project_id')).replace('-', '_')}"
                UI.info(f"Ensuring global database '{db_name}' exists...")
                check_cmd = [
                    "docker",
                    "exec",
                    "liferay-db-global",
                    "psql",
                    "-U",
                    "lportal",
                    "-d",
                    "lportal",
                    "-tc",
                    f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'",  # nosec B608
                ]
                exists_check = manager.run_command(
                    check_cmd, check=False, capture_output=True
                )
                if not exists_check or "1" not in exists_check:
                    create_cmd = [
                        "docker",
                        "exec",
                        "liferay-db-global",
                        "psql",
                        "-U",
                        "lportal",
                        "-d",
                        "lportal",
                        "-c",
                        f"CREATE DATABASE {db_name};",
                    ]
                    manager.run_command(create_cmd, check=True)
                    UI.success(
                        f"Created database '{db_name}' on global PostgreSQL container."
                    )

            if manager.verbose:
                UI.debug(
                    f"Infrastructure setup took: {UI.format_duration(time.time() - infra_start)}"
                )

        config_handler = manager.config
        config_handler.sync_common_assets(paths, version=tag, project_meta=project_meta)
        config_handler.sync_logging(paths)
        config_handler.remove_portal_ext(paths, ["include-and-override"])

        manager.composer.write_docker_compose(
            paths, project_meta, liferay_env=liferay_env
        )

        import shutil

        if shutil.which("docker"):
            UI.debug("Validating generated docker-compose.yml syntax...")
            manager.run_command(
                [*get_compose_cmd(), "config", "--quiet"],
                cwd=str(paths["root"]),
                check=True,
            )

        compose_file = paths["root"] / "docker-compose.yml"
        if compose_file.exists() and not no_up:
            import yaml

            try:
                with open(compose_file) as f:
                    compose_data = yaml.safe_load(f) or {}
                ports_to_check = []
                services = compose_data.get("services", {})
                for svc_name, svc_conf in services.items():
                    ports = svc_conf.get("ports", [])
                    for port_entry in ports:
                        if isinstance(port_entry, str):
                            parts = port_entry.split(":")
                            if len(parts) >= 2:
                                host_port = parts[-2]
                                if host_port.isdigit():
                                    ports_to_check.append((svc_name, int(host_port)))
                        elif isinstance(port_entry, dict):
                            published = port_entry.get("published")
                            if published:
                                ports_to_check.append((svc_name, int(published)))
                for svc_name, mapped_port in ports_to_check:
                    container_name = (
                        services[svc_name].get("container_name")
                        or f"{context.get('project_id')}-{svc_name}-1"
                    )
                    from ldm_core.docker_service import DockerService

                    if not DockerService.is_running(container_name):
                        if not manager.check_port("127.0.0.1", mapped_port):
                            UI.die(
                                f"Port conflict detected: Port {mapped_port} is already in use on the host "
                                f"and is required by service '{svc_name}' in your compose configuration.\n"
                                f"Please stop the service currently using port {mapped_port} before starting LDM."
                            )
            except SystemExit:
                raise
            except Exception as e:
                UI.debug(
                    f"Failed to check port collisions from docker-compose.yml: {e}"
                )

        manager.write_meta(paths["root"], project_meta)


class ExecutionStage(PipelineStage):
    """Boots dependencies, checks readiness, and starts Liferay."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        paths = context.get("paths")
        project_meta = context.get("project_meta")
        project_id = context.get("project_id")

        is_samples = context.get("is_samples")
        external_snapshot = context.get("external_snapshot")
        no_up = context.get("no_up")
        if no_up is None:
            no_up = getattr(manager.args, "no_up", False)
        compose_base = get_compose_cmd()
        db_type = project_meta.get("db_type", "postgresql")
        use_shared_db = context.get("use_shared_db")

        if is_samples or external_snapshot:
            db_args = ["up", "-d", "db"] if not use_shared_db else ["up", "-d"]
            manager.run_command([*compose_base, *db_args], cwd=str(paths["root"]))
            time.sleep(5)
            manager.snapshot.cmd_restore(
                project_id,
                auto_index=1 if is_samples else None,
                backup_dir=external_snapshot if not is_samples else None,
            )

        cmd = [*compose_base, "up", "-d", "--remove-orphans"]
        rebuild = context.get("rebuild") or getattr(manager.args, "rebuild", False)
        if rebuild:
            cmd.append("--build")

        show_summary = context.get("show_summary") and not getattr(
            manager.args, "quiet", False
        )
        if show_summary:
            tag_val = project_meta.get("tag")
            db_val = project_meta.get("db_type", "postgresql")
            port_val = project_meta.get("port", 8080)
            host_name = project_meta.get("host_name")
            ssl_enabled = str(project_meta.get("ssl", "false")).lower() == "true"
            ssl_port = project_meta.get("ssl_port", 443)

            display_port = f":{port_val}"
            if ssl_enabled and port_val == 8080:
                display_port = ""

            UI.info(
                f"{UI.WHITE}⚡{UI.COLOR_OFF} Starting {UI.BYELLOW}{project_id}{UI.COLOR_OFF} stack ({tag_val}, {db_val}, {host_name}{display_port})..."
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
            tag_val = project_meta.get("tag")
            project_meta["last_run_liferay_version"] = tag_val
            db_type_val = project_meta.get("db_type", "postgresql")
            if db_type_val in ["postgresql", "postgres"]:
                from ldm_core.utils import resolve_dependency_version

                current_pg = resolve_dependency_version(tag_val, "postgresql") or "16"
                project_meta["last_run_postgres_version"] = current_pg
            if db_type_val in ["mysql", "mariadb"]:
                from ldm_core.utils import resolve_dependency_version

                current_mysql = resolve_dependency_version(tag_val, "mysql") or (
                    "5.7" if db_type_val == "mysql" else "10.6"
                )
                project_meta["last_run_mysql_version"] = current_mysql
            current_es = (
                "7"
                if tag_val and any(v in tag_val for v in ["7.3", "7.2", "7.1", "7.0"])
                else "8"
            )
            project_meta["last_run_elasticsearch_major"] = current_es

            manager.write_meta(paths["root"], project_meta)

            if manager.verbose:
                duration_str = UI.format_duration(
                    time.time() - context.get("total_start")
                )
                UI.debug(f"Time to orchestration start: {duration_str}")

            deps = []
            if db_type != "hypersonic" and not use_shared_db:
                deps.append("db")

            if deps:
                UI.detail(
                    f"Starting dependencies: {UI.CYAN}{', '.join(deps)}{UI.COLOR_OFF}..."
                )
                manager.run_command(
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
                        status = manager.get_container_status(f"{project_id}-{dep}-1")
                        if status in {"healthy", "running"}:
                            time.sleep(2)
                            break
                        if status == "exited":
                            UI.error(f"Dependency '{dep}' exited unexpectedly.")
                            context.stopped = True
                            return None
                        time.sleep(2)
            elif use_shared_db and db_type != "hypersonic":
                global_db_container = (
                    "liferay-db-mysql-global"
                    if db_type in ["mysql", "mariadb"]
                    else "liferay-db-global"
                )
                UI.detail(
                    f"Waiting for shared database ({UI.CYAN}{global_db_container}{UI.COLOR_OFF}) to be ready..."
                )
                start_wait = time.time()
                while time.time() - start_wait < 60:
                    status = manager.get_container_status(global_db_container)
                    if status in {"healthy", "running"}:
                        time.sleep(2)
                        break
                    if status == "exited":
                        UI.error(
                            f"Global database container '{global_db_container}' exited unexpectedly."
                        )
                        context.stopped = True
                        return None
                    time.sleep(2)

            if platform.system().lower() == "linux":
                from ldm_core.utils import reclaim_volume_permissions

                for p_key in ["deploy", "logs", "osgi", "files"]:
                    if p_key in paths:
                        reclaim_volume_permissions(paths[p_key], chmod_val="777")

            follow = context.get("follow") or getattr(manager.args, "follow", False)
            manager.run_command(cmd, cwd=str(paths["root"]), capture_output=not follow)

            if follow:
                context.set("logs_attached", True)
                manager.run_command(
                    [*compose_base, "logs", "-f"], cwd=str(paths["root"])
                )
                return None
            no_wait = context.get("no_wait") or getattr(manager.args, "no_wait", False)
            if not no_wait:
                timeout_val = getattr(manager.args, "timeout", 900)
                if timeout_val is None:
                    timeout_val = 900
                return manager.runtime._wait_for_ready(
                    project_meta,
                    host_name,
                    context.get("total_start"),
                    timeout=timeout_val,
                    browser=context.get("browser"),
                )

        no_wait = getattr(manager.args, "no_wait", False)
        if no_wait:
            if str(project_meta.get("share", "false")).lower() == "true":
                share_subdomain = project_meta.get(
                    "share_subdomain"
                ) or project_meta.get("project_name")
                share_port = project_meta.get("port", 8080)
                share_provider = project_meta.get("share_provider") or "lfr-tunnel"
                manager.share.cmd_start(
                    project_id=project_meta.get("project_name"),
                    subdomain=share_subdomain,
                    ports=str(share_port),
                    provider=share_provider,
                    image=project_meta.get("share_image"),
                    inspector=str(project_meta.get("share_inspector", "false")).lower()
                    == "true",
                )
            UI.success(f"Project '{project_id}' started in background.")

        return None


def create_run_pipeline() -> Pipeline:
    pipeline = Pipeline(name="RunPipeline")
    pipeline.add_stage(ProjectInitializationStage())
    pipeline.add_stage(SharedValidationStage())
    pipeline.add_stage(RuntimeValidationStage())
    pipeline.add_stage(ConfigResolutionStage())
    pipeline.add_stage(EnvironmentSetupStage())
    pipeline.add_stage(ComposerStage())
    pipeline.add_stage(ExecutionStage())
    return pipeline
