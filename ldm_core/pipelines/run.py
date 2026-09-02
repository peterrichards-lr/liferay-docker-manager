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
from ldm_core.utils import get_actual_home, get_compose_cmd, shared_database_name


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


def _resolve_pipeline_target_context(manager, project_meta, root):
    """Resolves this command's TargetContext exactly once, as early as
    ProjectInitializationStage knows the project's metadata -- pinning an
    unpinned project's first-ever resolved target in the process. Every
    later stage in this pipeline reads the result from
    `context.get("target_context")` instead of independently re-deriving
    it (the exact anti-pattern that produced the same "falsy value skips
    the persisted-default fallback" bug three separate times in this
    codebase). See docs/explanation/remote-node-architecture.md."""
    from ldm_core.config import resolve_target_context

    explicit_target = getattr(manager, "target", None)
    # Test-safety: a bare MagicMock() manager (used throughout this
    # pipeline's unit tests) auto-generates a `.target` attribute that is
    # itself a MagicMock, not a real string/None -- mirrors the
    # SafeArgsWrapper guard above for the identical class of problem.
    if type(explicit_target).__name__ in ("MagicMock", "Mock"):
        explicit_target = None

    return resolve_target_context(
        explicit_target=explicit_target,
        meta=project_meta if isinstance(project_meta, dict) else None,
        project_root=root,
    )


def project_has_own_db_service(db_type, use_shared_db):
    """Whether compose defines a `<project>-db` service for this project.

    Must agree with composer._build_db_service, which returns early for
    `db_type == "external" or db_mode == "shared"`. When they disagreed, the
    pipeline named `<project>-db` as a startup dependency for an `external`
    project and ran `docker compose up -d <project>-db` against a service the
    compose file never defined -- with check=True, so it raised. `--db external`
    could not work in the default isolated mode.

    Extracted so the rule can be asserted directly. Inline, the only way to test
    it was to restate it in the test, which would then pass no matter what the
    pipeline actually did.
    """
    return db_type not in ("hypersonic", "external") and not use_shared_db


class ProjectInitializationStage(PipelineStage):
    """Handles project selection, discovery, and path setup."""

    def execute(self, context: PipelineContext) -> None:  # noqa: C901, PLR0912
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        paths = context.get("paths")
        project_meta = context.get("project_meta")
        if paths and project_meta:
            context.set("root", paths.get("root"))
            context.set(
                "target_context",
                _resolve_pipeline_target_context(
                    manager, project_meta, paths.get("root")
                ),
            )
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
                # LDM-#996: this is LDM's own initialization/orchestration layer
                # failing to establish the project's directory structure after
                # the user already supplied a valid name -- an orchestration
                # error, not a user-input validation problem.
                UI.die("Failed to resolve project path.", exit_code=4)

        project_id = root.name
        paths = manager.setup_paths(root)
        project_meta = manager.read_meta(paths["root"])

        is_new_project = not any(
            (root / f).exists() for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]
        )
        if is_new_project:
            UI.print_banner()
            if getattr(manager.args, "vanilla", False):
                UI.detail("Vanilla start requested: Bypassing pre-warmed seeding.")
        elif getattr(
            manager.args, "command", ""
        ) != "quickstart" and not project_meta.get("is_quickstart"):
            # LDM-#1036: this is the only context the user gets before the
            # countdown below asks them to decide whether to cancel -- it
            # must stay visible by default (UI.info, gated only on
            # --quiet), not UI.detail (gated behind --info/--verbose, which
            # nothing here implies), or the countdown fires with zero
            # explanation of what continuing will do.
            UI.info(
                f"The LDM project '{project_id}' already exists and this command will reconfigure it."
            )
            UI.interruptible_pause(5, "Press CTRL+C to cancel ")

        if project_meta.pop("is_quickstart", None):
            manager.write_meta(paths["root"], project_meta)

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
        context.set(
            "target_context",
            _resolve_pipeline_target_context(manager, project_meta, paths["root"]),
        )
        return

    def rollback(self, context: PipelineContext) -> None:
        context = typing.cast(RunPipelineContext, context)
        is_new_project = context.get("is_new_project")
        init_success = context.get("init_success")
        root = context.get("root")
        project_id = context.get("project_id")

        if is_new_project and not init_success:
            if root and root.exists():
                UI.detail(f"Cleaning up failed initialization: {root}")
                context.manager.safe_rmtree(root)
            if project_id:
                context.manager.unregister_project(project_id)


class RuntimeValidationStage(PipelineStage):
    """Validates runtime, Docker engine state, port collisions, and downgrade constraints."""

    def execute(self, context: PipelineContext) -> None:  # noqa: C901, PLR0912, PLR0915
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        project_meta = context.get("project_meta")
        project_id = context.get("project_id")
        is_restart = context.get("is_restart")
        paths = context.get("paths")

        custom_containers = project_meta.get("custom_containers")
        if custom_containers:
            from ldm_core.handlers.validation import CustomContainerValidator

            container_errors = CustomContainerValidator.validate_custom_containers(
                custom_containers
            )
            if container_errors:
                for err in container_errors:
                    UI.error(f"Custom container configuration error: {err}")
                UI.die(
                    "Custom container validation failed. Please fix your configuration in .ldmrc or project.json."
                )

        from ldm_core.docker_service import DockerService

        container_name = project_meta.get("container_name") or project_id
        no_up = context.get("no_up")
        if no_up is None:
            no_up = getattr(manager.args, "no_up", False)

        if not no_up and not is_restart:
            if DockerService.is_running(container_name):
                if manager.non_interactive:
                    # LDM-#1094: exit_code=5 (Idempotent No-Op), not the
                    # generic 1 -- automation calling `ldm run`/`up`
                    # non-interactively needs to tell "nothing to do, it's
                    # already up" apart from an actual validation failure.
                    UI.die(
                        f"Project '{project_id}' is already running. Use 'ldm restart' to apply updates, or 'ldm stop' it first.",
                        exit_code=5,
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
                    UI.detail(
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

    def _resolve_tag(self, manager, project_meta, is_samples, is_portal):  # noqa: C901, PLR0912, PLR0915
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

        is_nightly = getattr(manager.args, "nightly", False) or getattr(
            manager.args, "master", False
        )
        if is_nightly and not tag:
            rt = "nightly"
            can_discover = True

        if is_samples and not tag:
            tag = manager.config.get_samples_tag()

        if not tag:
            can_discover = tag_latest or bool(prefix) or is_nightly
            if manager.non_interactive:
                can_discover = True

            from ldm_core.constants import API_BASE_DXP, API_BASE_PORTAL
            from ldm_core.utils import discover_latest_tag

            api_base = API_BASE_PORTAL if is_portal else API_BASE_DXP
            default_rt = str(manager.defaults.get("release_type", "lts") or "lts")
            raw_rt = getattr(manager.args, "release_type", None)
            rt = (
                str(raw_rt)
                if raw_rt
                else (
                    "nightly"
                    if is_nightly
                    # LDM-#1080: --tag-latest means "the latest Liferay tag,
                    # period" -- it must not be silently narrowed down to
                    # the global release_type default (normally "lts"),
                    # which would make it resolve the latest *LTS* tag
                    # instead of the true latest (which may well be a
                    # newer quarterly RC). Only fall back to that default
                    # when the user gave neither --tag-latest nor a prefix
                    # and hasn't specified --release-type either.
                    else ("any" if (prefix or tag_latest) else default_rt)
                )
            )
            # LDM-#1061: explicit "latest" -> "any" normalization, matching the
            # interactive prompt's own mapping below, rather than relying on
            # discover_latest_tag()'s implicit "no recognized filter -> no
            # filtering" fallthrough to coincidentally produce the same result.
            if rt.lower() == "latest":
                rt = "any"

            if not can_discover:
                if manager.verbose:
                    UI.detail(
                        f"Pre-resolving latest {rt.upper()} release to populate default prompt..."
                    )
                default_resolved_tag = discover_latest_tag(
                    api_base,
                    release_type=rt,
                    prefix_filter=prefix,
                    verbose=manager.verbose,
                )
                ans = UI.ask(
                    "Release type (lts|u|qr|nightly|master|latest), prefix, or specific tag",
                    default_resolved_tag,
                )
                if ans == default_resolved_tag:
                    tag = default_resolved_tag
                elif ans.lower() in [
                    "any",
                    "latest",
                    "u",
                    "lts",
                    "qr",
                    "nightly",
                    "master",
                ]:
                    release_type = "any" if ans.lower() == "latest" else ans.lower()
                    if manager.verbose:
                        UI.detail(f"Discovering latest {ans.upper()} release...")
                    tag = discover_latest_tag(
                        api_base, release_type=release_type, verbose=manager.verbose
                    )
                    if not tag:
                        # LDM-#996: a failed external API lookup (Docker Hub tag
                        # discovery), not a user-input validation error.
                        UI.die(
                            f"Could not find any tags for release type: {ans}",
                            exit_code=3,
                        )
                else:
                    if manager.verbose:
                        UI.detail(f"Discovering latest tag matching prefix: {ans}...")
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
                    UI.detail("Automatically discovering latest Liferay tag...")
                tag = discover_latest_tag(
                    api_base,
                    release_type=rt,
                    prefix_filter=prefix,
                    verbose=manager.verbose,
                )
                if not tag:
                    # LDM-#996: same external-API-failure category as the
                    # release-type branch above.
                    UI.die(
                        "Failed to discover latest Liferay tag. Please specify one explicitly with -t.",
                        exit_code=3,
                    )
                if manager.verbose:
                    UI.success(f"Using tag: {tag}")

        return tag, is_portal

    def _resolve_database(self, manager, project_meta, is_samples):
        db_type = (
            getattr(manager.args, "db", None)
            or project_meta.get("db_type")
            or manager.defaults.get("db_type")
            or "postgresql"
        )
        if (
            is_samples
            and not getattr(manager.args, "db", None)
            and not project_meta.get("db_type")
        ):
            db_type = manager.config.get_samples_db_type()

        if db_type == "external" and not project_meta.get("jdbc_url"):
            UI.heading("External Database Configuration")
            project_meta["jdbc_url"] = UI.ask(
                "JDBC URL (e.g. jdbc:postgresql://host:5432/db)",
                "jdbc:postgresql://db:5432/lportal",
            )
            project_meta["jdbc_user"] = UI.ask("Database Username", "liferay")
            project_meta["jdbc_pass"] = UI.ask("Database Password", "liferay")

        return db_type

    def _resolve_share_and_expose(self, manager, project_meta):
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
                UI.detail(
                    "An ngrok Auth Token is required to use the expose feature (it enables custom host headers and HTTPS)."
                )
                UI.detail(
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

        return {
            "is_share": is_share,
            "share_subdomain": share_subdomain,
            "share_image": share_image,
            "share_inspector": share_inspector,
            "share_domain": share_domain,
            "share_provider": share_provider,
            "is_expose": is_expose,
        }

    def execute(self, context: PipelineContext) -> None:  # noqa: C901, PLR0912, PLR0915
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        project_meta = context.get("project_meta")
        paths = context.get("paths")
        project_id = context.get("project_id")

        no_up = context.get("no_up")
        if no_up is None:
            no_up = getattr(manager.args, "no_up", False)

        is_samples = getattr(manager.args, "samples", False)

        # LDM-#1285: `--vanilla` is an intent flag -- "give me a Liferay with
        # nothing pre-populated" -- not merely a second name for `--no-seed`,
        # which is a mechanism flag meaning "skip the pre-warmed seed archive".
        #
        # Before this, `--vanilla` was consulted only at the seeding gate
        # (handlers/assets.py), so `ldm run --vanilla --samples` skipped the
        # seed and then restored a sample snapshot -- a combination that
        # silently contradicted itself and produced a decidedly non-vanilla
        # instance. Refusing is better than silently dropping whichever flag
        # loses: quietly ignoring one the user explicitly typed is its own
        # surprise, and LDM prefers loud refusal for contradictory input.
        if getattr(manager.args, "vanilla", False):
            conflicting = []
            if is_samples:
                conflicting.append("--samples")
            if getattr(manager.args, "snapshot", None):
                conflicting.append("--snapshot")
            if conflicting:
                UI.die(
                    f"--vanilla cannot be combined with {' or '.join(conflicting)}: "
                    "--vanilla means nothing pre-populated, while "
                    f"{conflicting[0]} restores content into the project. "
                    "Drop one of them -- use --no-seed instead of --vanilla if "
                    "you only meant to skip the pre-warmed seed.",
                    exit_code=1,
                )

        is_portal = (
            getattr(manager.args, "portal", False)
            or str(project_meta.get("portal", manager.defaults.get("portal"))).lower()
            == "true"
        )

        tag, is_portal = self._resolve_tag(manager, project_meta, is_samples, is_portal)

        host_name = (
            manager.args.host_name
            or project_meta.get("host_name")
            or manager.defaults.get("host_name")
            or "localhost"
        )

        if is_samples:
            if host_name == "localhost":
                if manager.non_interactive:
                    UI.die("--samples requires a custom hostname.")
                host_name = UI.ask("Enter project Virtual Hostname", "samples.local")

        if getattr(manager.args, "ssl", None) is True and not getattr(
            manager.args, "host_name", None
        ):
            if not manager.non_interactive:
                host_name = UI.ask("Enter project Virtual Hostname", host_name)

        db_type = self._resolve_database(manager, project_meta, is_samples)

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
                UI.detail(
                    "External volume detected. Automatically enabling '--internal-state' for stability."
                )
                project_meta["internal_state"] = "true"

        internal_state = (
            getattr(manager.args, "internal_state", False)
            or str(project_meta.get("internal_state", "false")).lower() == "true"
        )
        # LDM-#1447: both of these are inert. They are computed here and
        # persisted to meta below, and nothing reads them again -- there is no
        # `-Xverify:none` anywhere in the codebase and no TLD configuration at
        # all, so the "defaults" the documentation described never existed.
        #
        # They are kept rather than removed because AGENTS.md forbids breaking
        # existing flags: dropping them would fail any script that passes one.
        # But a flag that silently does nothing is how this survived unnoticed,
        # so passing one explicitly now says so.
        #
        # `-Xverify:none` should NOT simply be implemented: it has been
        # deprecated since JDK 13 and these images run Java 21
        # (compatibility.json, >=2025.q2.0), where it warns and does nothing.
        # A real TLD skip remains worthwhile -- tracked in LDM-#1446.
        no_jvm_verify = (
            getattr(manager.args, "no_jvm_verify", False)
            or str(project_meta.get("no_jvm_verify", "false")).lower() == "true"
        )
        no_tld_skip = (
            getattr(manager.args, "no_tld_skip", False)
            or str(project_meta.get("no_tld_skip", "false")).lower() == "true"
        )
        for flag_name, was_passed in (
            ("--no-jvm-verify", getattr(manager.args, "no_jvm_verify", False)),
            ("--no-tld-skip", getattr(manager.args, "no_tld_skip", False)),
        ):
            if was_passed:
                UI.warning(
                    f"{flag_name} has no effect and is accepted only for "
                    "compatibility (LDM-#1446)."
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
            jvm_args = manager.composer.get_default_jvm_args(
                target_name=project_meta.get("target")
            )

        external_snapshot = getattr(manager.args, "snapshot", None)
        if external_snapshot:
            snap_path = Path(external_snapshot).resolve()
            snap_meta = manager.read_meta(snap_path)
            tag = tag or snap_meta.get("tag")
            db_type = db_type or snap_meta.get("db_type")

        if tag and tag != project_meta.get("tag"):
            from ldm_core.utils import validate_liferay_tag

            if manager.verbose:
                UI.detail(f"Validating tag '{tag}' against Liferay releases...")
            if not validate_liferay_tag(tag):
                UI.warning(
                    f"Tag '{tag}' is not listed in official Liferay releases. If this is not a custom image, the Docker pull may fail."
                )

        share_expose_config = self._resolve_share_and_expose(manager, project_meta)

        from ldm_core.utils import resolve_infrastructure_mode

        # LDM-#1362: the CLI override MUST be passed. Without it
        # `--search-mode shared` was silently ignored -- `resolve_infrastructure_mode`
        # prioritises override > meta > defaults, and the override never
        # arrived, so the flag produced a SIDECAR Elasticsearch embedded in the
        # Liferay container: the exact opposite of the memory saving shared mode
        # exists for. Same omission as #1359 (`database_mode`) and #1374
        # (`no_up`); the sibling `database_mode` call twenty lines below has
        # always passed it.
        search_mode = resolve_infrastructure_mode(
            "search_mode",
            project_meta,
            manager.defaults,
            getattr(manager.args, "search_mode", None),
        )
        use_shared_search = search_mode == "shared"
        # `--sidecar` is a separate, older flag meaning "definitely not shared".
        # It wins over the resolved mode, which is the pre-existing behaviour.
        if getattr(manager.args, "sidecar", False):
            use_shared_search = False
            search_mode = "sidecar"

        # LDM-#1359: resolved here, before the metadata below is assembled, so
        # the mode can be PERSISTED. It used to be resolved only at compose
        # time, which left `database_mode` absent from meta -- so every later
        # command (snapshot, restore, db query, orchestration) resolved it from
        # defaults instead and silently assumed "isolated" for a project that
        # was provisioned "shared".
        db_mode_resolved = resolve_infrastructure_mode(
            "database_mode",
            project_meta,
            manager.defaults,
            getattr(manager.args, "database_mode", None),
        )

        # LDM-#1361 removed the LDM-#1360 refusal that used to sit here.
        # `--database-mode shared --db mysql` exited 1 because the only global
        # container was `postgres:<ver>` while `_inject_liferay_db_env` emitted
        # `jdbc:mariadb://liferay-db-global:3306/...` -- the MySQL port of a
        # PostgreSQL container, which could never connect. There is now a
        # global MySQL container per `shared_database_container`, so the
        # combination is provisioned rather than refused. Nothing replaces the
        # guard: Hypersonic is still downgraded to isolated (with a warning) by
        # `_inject_liferay_db_env`, and `external` never consults a global
        # container at all.

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
                "tag": tag or "",
                "portal": str(is_portal).lower(),
                "host_name": host_name,
                "container_name": project_id,
                "ssl": str(ssl_val).lower(),
                "db_type": db_type or project_meta.get("db_type", "postgresql"),
                "port": port,
                "jvm_args": jvm_args,
                "use_shared_search": str(use_shared_search).lower(),
                # LDM-#1359: persisted so later commands agree with the run
                # that provisioned the project.
                "database_mode": db_mode_resolved,
                # LDM-#1362: same reasoning for search. Without this, every
                # later command resolved the mode from defaults and could
                # disagree with how the project was actually provisioned --
                # and `ldm info` had to guess it from the Liferay tag.
                "search_mode": search_mode,
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
                "expose": str(share_expose_config["is_expose"]).lower(),
                "share": str(share_expose_config["is_share"]).lower(),
                "share_subdomain": share_expose_config["share_subdomain"] or "",
                "share_provider": share_expose_config["share_provider"],
                "share_image": share_expose_config["share_image"] or "",
                "share_inspector": str(share_expose_config["share_inspector"]).lower(),
                "share_domain": share_expose_config["share_domain"] or "",
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

    def execute(self, context: PipelineContext) -> None:  # noqa: C901, PLR0912, PLR0915
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        project_meta = context.get("project_meta")
        paths = context.get("paths")
        is_new_project = context.get("is_new_project")
        tag = context.get("tag")
        db_type = context.get("db_type")
        project_id = context.get("project_id")

        if getattr(manager.args, "command", "") != "quickstart":
            UI.phase(1, 3, "Synchronizing Assets")

        if is_new_project and manager.assets._ensure_seeded(tag, db_type, paths):
            from ldm_core.constants import SEED_VERSION

            project_meta = manager.read_meta(paths["root"])
            project_meta["seeded"] = "true"
            project_meta["seed_version"] = str(SEED_VERSION)
            manager.write_meta(paths["root"], project_meta)
            # LDM-#1509: this rebinds a LOCAL name. Without putting it back,
            # context["project_meta"] still holds the pre-seed dict, and the
            # later stages re-read that and write it straight back over the
            # file -- three times -- dropping `seeded` and `seed_version`.
            # `ldm doctor` then reported a genuinely seeded project as
            # "Vanilla (Not Seeded)" while the same run had printed
            # "Project bootstrapped from seed".
            context.set("project_meta", project_meta)
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
        else:
            # LDM-#1324: register on EVERY run, not only when the project is
            # new. An existing project that was never registered -- created by
            # hand, cloned, or predating registration -- stayed unregistered
            # forever, discoverable only while it happened to sit one level
            # under the search directory, because find_dxp_roots() scans with
            # iterdir(). The E2E verification project is exactly that shape:
            # its directory and `meta` are written directly and then run, so
            # `is_new_project` is False and it was never recorded.
            #
            # This also refreshes `last_seen`, which was otherwise frozen at
            # creation time and useless for ordering by recency.
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
            # LDM-#1285: `--vanilla` promises nothing pre-populated, and a
            # host-persisted OSGi state directory from a previous run is
            # pre-populated bundle state. Wiping it unconditionally here is what
            # makes the promise hold even when the tag has not changed --
            # otherwise `--persist-osgi` would quietly carry resolved bundles
            # into an instance the user asked to be pristine.
            is_vanilla = getattr(manager.args, "vanilla", False)
            if osgi_state_dir.exists():
                with contextlib.suppress(Exception):
                    saved_tag = (
                        tag_marker.read_text().strip() if tag_marker.exists() else None
                    )
                    if is_vanilla:
                        UI.warning(
                            "--vanilla: wiping persisted OSGi state so the "
                            "instance starts with no pre-resolved bundles."
                        )
                    if is_vanilla or saved_tag != tag:
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

        use_volumes = manager.composer.is_using_named_volumes()
        if use_volumes and not no_up:
            # LDM-#817: a freshly-created Named Volume (via `docker volume
            # create` / `docker compose up` on first use, or after `ldm reset`
            # deletes and recreates one) starts out root-owned. Equinox runs
            # as UID 1000 inside the Liferay container and cannot create its
            # OSGi lock file in a root-owned, empty volume, crashing on first
            # boot with "Unable to create lock manager". #817 was closed
            # claiming this was fixed, but the actual chown injection was
            # never implemented -- reopened and fixed here for real, reusing
            # the same volume-ownership mechanism the snapshot-restore path
            # already relies on (LDM-420 in ldm_core/snapshot/volumes.py),
            # rather than depending entirely on the Liferay image's own
            # entrypoint to self-chown a volume it didn't create.
            manager.snapshot.volumes.hydrate_named_volumes_with_sync_wait(paths)

        es_data = paths["data"] / "elasticsearch8"
        if es_data.exists() and not use_volumes:
            UI.detail("Clearing stale search locks and enforcing permissions...")
            for lock_file in es_data.rglob("write.lock"):
                with contextlib.suppress(Exception):
                    lock_file.unlink()
            if platform.system().lower() != "windows":
                from ldm_core.utils import run_command

                # LDM-369 / LDM-#1507: `777` required, and this one is host-side
                # on purpose. `es_data` is the *sidecar* Elasticsearch index
                # directory, bind-mounted (this branch only runs when
                # `not use_volumes`) into the Liferay container, whose sidecar
                # ES process runs as uid 1000. The host owns the tree, so
                # without the world bits the sidecar fails its writes with
                # `access_denied_exception` and leaves the index read-only --
                # fragment indexing then fails silently (`d1677e84`).
                #
                # UNVERIFIED (LDM-#1507): why this is a plain host-side `chmod`
                # rather than `reclaim_volume_permissions` is not recorded
                # anywhere -- `d1677e84` introduced it without saying. It does
                # differ in two observable ways: it changes no ownership, and
                # it needs no helper container, so it still works when the
                # files are host-owned and costs no Docker round trip on a
                # path that runs immediately before every boot. It cannot
                # repair root-owned files, which the helper can. Do not
                # collapse the two without establishing which property the
                # sidecar actually depends on.
                run_command(["chmod", "-R", "777", str(es_data)], check=False)

        is_samples = context.get("is_samples")
        if is_samples:
            manager.config.sync_samples(paths)

        manager.write_meta(paths["root"], project_meta)


class ComposerStage(PipelineStage):
    """Generates compose definitions and applies overrides."""

    def execute(self, context: PipelineContext) -> None:  # noqa: C901, PLR0912, PLR0915
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

                import shutil

                if db_type_val not in ["hypersonic", "external"] and shutil.which(
                    "docker"
                ):
                    is_running = manager.run_command(
                        ["docker", "ps", "-q", "-f", f"name=^{db_container}$"],
                        check=False,
                    )
                    compose_file = paths["root"] / "docker-compose.yml"
                    if compose_file.exists() and not is_running:
                        UI.detail(
                            "Starting database container temporarily to take a snapshot backup..."
                        )
                        db_svc = f"{sanitize_id(paths['root'].name)}-db"
                        db_args = (
                            ["up", "-d", db_svc] if not use_shared_db else ["up", "-d"]
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
                    UI.detail(
                        "How would you like to resolve this search configuration?"
                    )
                    UI.detail(
                        "  [1] Keep configs: Connect to my own external Remote cluster (Default)"
                    )
                    UI.detail(
                        "  [2] Delete configs: Fallback to LDM Sidecar (Internal) mode"
                    )
                    UI.detail(
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
                    UI.detail(
                        "Keeping custom configs. LDM Sidecar injection will be bypassed."
                    )

        from ldm_core.utils import resolve_infrastructure_mode

        # LDM-#1359: this resolution was already correct -- it passes the CLI
        # override. The defect was `_inject_liferay_db_env` in composer.py
        # omitting it, so the compose file and this disagreed. Kept explicit
        # (rather than reusing the earlier local, which is in another stage's
        # scope) and now backed by the persisted meta value.
        db_mode = resolve_infrastructure_mode(
            "database_mode",
            project_meta,
            manager.defaults,
            getattr(manager.args, "database_mode", None),
        )
        use_shared_db = db_mode == "shared"
        context.set("use_shared_db", use_shared_db)
        if use_shared_db or use_shared_search:
            UI.detail("Utilizing Global Shared Infrastructure")

        if host_name != "localhost":
            liferay_env.extend(
                [
                    "LIFERAY_WEB_PERIOD_SERVER_PERIOD_DISPLAY_PERIOD_NODE_PERIOD_NAME=true",
                    "LIFERAY_REDIRECT_PERIOD_URL_PERIOD_IPS_PERIOD_ALLOWED=127.0.0.1,0.0.0.0/0",
                ]
            )

        import shutil

        no_up = context.get("no_up")
        if no_up is None:
            no_up = getattr(manager.args, "no_up", False)

        if shutil.which("docker") and not no_up:
            # Reads the TargetContext ProjectInitializationStage already
            # resolved (and possibly pinned) for this command -- falls back
            # to the pre-migration ad-hoc lookup only when this stage is
            # invoked directly without running the full pipeline first
            # (isolated unit tests), so a real target never gets resolved
            # (and no pin ever written) outside the one real call site.
            target_context = context.get("target_context")
            if target_context is not None:
                target_name = target_context.target.name
            else:
                target_name = getattr(manager, "target", None) or (
                    project_meta.get("target")
                    if isinstance(project_meta, dict)
                    else None
                )
            manager.infra._ensure_network(target_name)

        ssl_enabled = str(project_meta.get("ssl", "false")).lower() == "true"
        ssl_port = project_meta.get("ssl_port", 443)

        # Provision what the resolved modes actually need, and only when
        # something is being started.
        #
        # Deliberately OUTSIDE the `ssl_enabled or --search or use_shared_db`
        # gate below: a shared-SEARCH-only project satisfies none of those, so
        # nesting this inside it meant the provisioning never ran for exactly
        # the case #1363 is about. Found by booting it -- the static reading
        # looked correct.
        # LDM-#1361: the engine decides WHICH global container is provisioned,
        # so it has to be read here and threaded through. Reads the persisted
        # meta (ProjectInitializationStage writes `db_type`), which is the same
        # source the compose file's JDBC URL is built from -- so the container
        # provisioned and the container Liferay dials cannot disagree.
        shared_db_type = project_meta.get("db_type", "postgresql")

        if shutil.which("docker") and not no_up:
            if use_shared_search:
                UI.detail("Ensuring global search service is running...")
                manager.infra.setup_global_search()
            if use_shared_db:
                UI.detail("Ensuring global database service is running...")
                manager.infra.setup_global_database(db_type=shared_db_type)

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

            import shutil

            if shutil.which("docker") and not no_up:
                ssl_port = manager.infra.setup_infrastructure(
                    resolved_ip,
                    ssl_port,
                    use_ssl=ssl_enabled,
                    quiet=getattr(manager.args, "quiet", False),
                    use_shared_search=use_shared_search,
                    use_shared_db=use_shared_db,
                    db_type=shared_db_type,
                )
            project_meta["ssl_port"] = ssl_port

            # LDM-#1363: `ldm run` assumed the global infrastructure was
            # already up. For search this meant Liferay was pointed at
            # `liferay-search-global:9200` and started against a container that
            # did not exist -- it then indexed nowhere, silently. For the
            # database the `docker exec liferay-db-global` below simply failed.
            #
            # Neither container was provisioned here: `setup_global_search` and
            # `setup_global_database` were only reachable via `ldm infra setup`
            # (handlers/infra.py) and, for search, an interactive prompt in
            # runtime/search.py that `ldm run` never reaches. #1365 made this
            # routine -- the test suite removed `liferay-search-global` on every
            # run -- so a developer's next shared-search project booted broken.
            #
            if shutil.which("docker") and use_shared_db and not no_up:
                # LDM-#1401: honour the active target. These commands used the
                # bare `docker` executable while every neighbour resolves a
                # prefix, so for a project on a remote target the global
                # container was created on the REMOTE engine (see
                # infra.setup_global_database, which resolves it correctly)
                # while this per-project CREATE DATABASE ran against the LOCAL
                # daemon. The existence check runs with check=False, so a local
                # daemon without that container simply returns nothing and the
                # create proceeds -- failing, if at all, with an error naming
                # the wrong daemon.
                #
                # Resolved here rather than reusing the `target_name` computed
                # earlier in this method: that binding sits inside a different
                # conditional and is not guaranteed to exist at this point.
                #
                # Shared *database* on a remote target is supported --
                # setup_global_search's docstring records that the first-time
                # remote limitation is specific to search, whose data dirs are
                # host bind-mounts, and contrasts it with the database's
                # Docker-managed named volume.
                from ldm_core.docker_service import DockerService
                from ldm_core.utils import shared_database_container

                _shared_db_target = context.get("target_context")
                if _shared_db_target is not None:
                    _shared_db_target_name = _shared_db_target.target.name
                else:
                    _shared_db_target_name = getattr(manager, "target", None) or (
                        project_meta.get("target")
                        if isinstance(project_meta, dict)
                        else None
                    )
                docker_prefix = DockerService.get_docker_cmd_prefix(
                    _shared_db_target_name
                )

                global_db = shared_database_container(shared_db_type)
                db_name = shared_database_name(context.get("project_id"))
                is_mysql_global = str(shared_db_type).lower() in ("mysql", "mariadb")
                UI.detail(f"Ensuring global database '{db_name}' exists...")

                if is_mysql_global:
                    # LDM-#1361: the DDL runs as `root`, not `lportal`. The
                    # official MySQL image grants MYSQL_USER privileges on
                    # MYSQL_DATABASE only, so `lportal` cannot CREATE DATABASE
                    # at all -- and the GRANT below is what lets Liferay, which
                    # connects as `lportal`, use the database once it exists.
                    # Omitting it produces a successful create followed by an
                    # access-denied at Liferay boot. Matches the `root`
                    # credentials the teardown DROP in runtime/orchestration.py
                    # already assumes.
                    check_cmd = [
                        *docker_prefix,
                        "exec",
                        global_db,
                        "mysql",
                        "-u",
                        "root",
                        "-ptest",
                        "-N",
                        "-B",
                        "-e",
                        f"SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = '{db_name}'",  # nosec B608
                    ]
                    create_cmd = [
                        *docker_prefix,
                        "exec",
                        global_db,
                        "mysql",
                        "-u",
                        "root",
                        "-ptest",
                        "-e",
                        (
                            f"CREATE DATABASE IF NOT EXISTS {db_name} "
                            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; "
                            f"GRANT ALL PRIVILEGES ON {db_name}.* TO 'lportal'@'%'; "
                            "FLUSH PRIVILEGES;"
                        ),
                    ]
                else:
                    check_cmd = [
                        *docker_prefix,
                        "exec",
                        global_db,
                        "psql",
                        "-U",
                        "lportal",
                        "-d",
                        "lportal",
                        "-tc",
                        f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'",  # nosec B608
                    ]
                    create_cmd = [
                        *docker_prefix,
                        "exec",
                        global_db,
                        "psql",
                        "-U",
                        "lportal",
                        "-d",
                        "lportal",
                        "-c",
                        f"CREATE DATABASE {db_name};",
                    ]

                exists_check = manager.run_command(
                    check_cmd, check=False, capture_output=True
                )
                if not exists_check or "1" not in exists_check:
                    manager.run_command(create_cmd, check=True)
                    UI.success(
                        f"Created database '{db_name}' on global "
                        f"{'MySQL' if is_mysql_global else 'PostgreSQL'} container."
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
            paths,
            project_meta,
            liferay_env=liferay_env,
            target_context=context.get("target_context"),
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
                from ldm_core.docker_service import DockerService

                # LDM-#1417: ask Docker's allocator, once, before probing any
                # socket. `docker compose up` refuses a taken port from this
                # table -- "port is already allocated" -- without ever
                # attempting a host bind, and on Windows the socket probes
                # cannot see a container-held port at all. Resolved here
                # rather than per-service so the whole loop costs one call.
                docker_held_ports = DockerService.published_host_ports()

                for svc_name, mapped_port in ports_to_check:
                    container_name = (
                        services[svc_name].get("container_name")
                        or f"{context.get('project_id')}-{svc_name}-1"
                    )

                    if not DockerService.is_running(container_name):
                        if mapped_port in docker_held_ports or not manager.check_port(
                            "127.0.0.1", mapped_port
                        ):
                            # LDM-#996: staying fatal here is deliberate. By this
                            # point the port is written into the generated
                            # docker-compose.yml, so moving it means regenerating
                            # the compose file, not just picking a new number.
                            #
                            # LDM-#1350: the *advice* was wrong, though. Telling
                            # the user to stop a process contradicts LDM's own
                            # documented behaviour of moving to the next free
                            # port, which the pre-flight check really does
                            # (handlers/base.py). The pre-flight printed nothing
                            # because the port was genuinely free when it ran;
                            # what sits between the two checks is a seed download
                            # that can take minutes. Re-running is what actually
                            # resolves it, so name the port a re-run would pick.
                            #
                            # Bounded scan, not find_available_port(): that walks
                            # to 65535 and would issue ~57k probes on a busy host,
                            # inside an error path. A short window gives the same
                            # answer whenever one exists nearby, since the
                            # pre-flight also takes the first free port above this
                            # one.
                            alternative = next(
                                (
                                    candidate
                                    for candidate in range(
                                        mapped_port + 1,
                                        min(mapped_port + 21, 65536),
                                    )
                                    if candidate not in docker_held_ports
                                    and manager.check_port("127.0.0.1", candidate)
                                ),
                                None,
                            )
                            # LDM-#1397: the promise of a re-run picking another
                            # port is only true for the port the pre-flight
                            # actually governs, on an interactive run. Under -y
                            # the pre-flight dies rather than re-selecting
                            # (handlers/base.py), and services with a literal
                            # port in the compose builder -- kibana's 5601,
                            # lfr-tunnel's 4040 -- are never pre-flighted at all,
                            # so a re-run regenerates the same port and fails
                            # identically. Promising otherwise sends the user
                            # round a loop that cannot terminate.
                            preflight_governs = svc_name == "liferay"
                            non_interactive = getattr(manager, "non_interactive", False)
                            if (
                                alternative
                                and preflight_governs
                                and not non_interactive
                            ):
                                tip = (
                                    f"Re-run 'ldm run' -- the pre-flight check will "
                                    f"select port {alternative} instead. Or free up "
                                    f"port {mapped_port} and re-run to keep it."
                                )
                            elif preflight_governs and non_interactive:
                                tip = (
                                    f"A re-run will fail the same way: with -y the "
                                    f"pre-flight refuses rather than moving the port. "
                                    f"Free up port {mapped_port}, or set an explicit "
                                    f"one with --port, then re-run."
                                )
                            else:
                                tip = (
                                    f"Service '{svc_name}' has a fixed port, so a "
                                    f"re-run will produce {mapped_port} again. Free "
                                    f"up port {mapped_port}, or disable/move that "
                                    f"service, then re-run."
                                )
                            # LDM-#1479: say what is HOLDING the port, not just
                            # what needs it. Working that out is per-OS and is
                            # the slow part of resolving a conflict. Diagnosis
                            # is best-effort and must never change the outcome:
                            # this is still exit 4 with the same advice if
                            # nothing can be identified.
                            holder = None
                            try:
                                from ldm_core.utils import native_port_listener

                                container = DockerService.container_publishing_port(
                                    mapped_port
                                )
                                if container:
                                    holder = f"container '{container}'"
                                else:
                                    listener = native_port_listener(mapped_port)
                                    if listener:
                                        holder = listener
                            except Exception as diag_err:  # pragma: no cover
                                UI.debug(f"Port holder lookup failed: {diag_err}")

                            held_by = (
                                f" It is currently held by {holder}." if holder else ""
                            )
                            UI.die(
                                f"Port conflict detected: Port {mapped_port} is already in use on the host "
                                f"and is required by service '{svc_name}' in your compose configuration."
                                f"{held_by}",
                                tip=tip,
                                exit_code=4,
                            )
            except SystemExit:
                raise
            except Exception as e:
                UI.debug(
                    f"Failed to check port collisions from docker-compose.yml: {e}"
                )

        manager.write_meta(paths["root"], project_meta)


def _patch_docker_prefix(manager, target_context):
    """Returns the `docker` argv prefix to use for `docker cp` (LDM-#1264).

    `compose_base` cannot be reused: it is a *compose* prefix, and `cp` is a
    plain docker subcommand. A remote target carries its own prefix; otherwise
    resolve from the manager's target the same way the compose prefix is.
    """
    if target_context is not None and target_context.is_remote:
        return target_context.docker_prefix

    from ldm_core.docker_service import DockerService

    return DockerService.get_docker_cmd_prefix(getattr(manager, "target", None))


class ExecutionStage(PipelineStage):
    """Boots dependencies, checks readiness, and starts Liferay."""

    def execute(self, context: PipelineContext) -> None:  # noqa: C901, PLR0912, PLR0915
        context = typing.cast(RunPipelineContext, context)
        manager = context.manager
        paths = context.get("paths")
        project_meta = context.get("project_meta")
        project_id = context.get("project_id")

        # LDM-#1307: every Docker-facing identifier built here must go through
        # sanitize_id. The compose file defines services and containers under
        # the sanitized name, so a raw project id -- which may contain any
        # Unicode the user typed -- never resolves against it.
        from ldm_core.utils import sanitize_id

        safe_project_id = sanitize_id(project_id) if project_id else project_id

        is_samples = context.get("is_samples")
        external_snapshot = context.get("external_snapshot")
        # LDM-#1374: the args fallback is required. Three sibling stages
        # (:233, :612, :977) resolve this as context-then-args; this one read
        # only the context, and the CLI never puts `no_up` there -- `cli.py`
        # dispatches `("run", None)` as `cmd_run(project)` with no kwarg, so
        # `context.set("no_up", None)` at :52 leaves it None.
        #
        # `if not None` is true, so the guarded block below always ran:
        # `ldm run --no-up` started the stack and waited for readiness. The
        # sibling flag `--no-seed` worked throughout because it is read from
        # `args` directly, which is what made this look like a parser problem.
        no_up = context.get("no_up")
        if no_up is None:
            no_up = getattr(manager.args, "no_up", False)

        # Reads the TargetContext ProjectInitializationStage already
        # resolved (and possibly pinned) for this command. Falling back to
        # a raw (possibly-None) target_name here -- as this used to -- was
        # a real, live bug: DockerService.get_compose_cmd_prefix(None) used
        # to short-circuit to a plain local `docker compose` prefix without
        # ever consulting a persisted default target (see #1149), and the
        # `if target_name:` guard below skipped the remote sync entirely
        # for the same reason -- a project relying solely on a persisted
        # `ldm target use` default (no explicit --node, no project-meta
        # pin) would build a compose file with correctly remote-mapped
        # bind mounts, then never actually rsync the project files there
        # and start the containers against the *local* Docker daemon
        # instead. The isolated-test fallback below (no target_context on
        # context) mirrors the old ad-hoc lookup for stage-level unit tests
        # that invoke this stage directly without running the full
        # pipeline first.
        target_context = context.get("target_context")
        if target_context is not None:
            if target_context.is_remote:
                from ldm_core.config import sync_project_to_target

                sync_project_to_target(
                    paths["root"], target_name=target_context.target.name
                )
            compose_base = target_context.compose_prefix
        else:
            target_name = getattr(manager, "target", None) or (
                project_meta.get("target") if isinstance(project_meta, dict) else None
            )
            if target_name:
                from ldm_core.config import sync_project_to_target

                sync_project_to_target(paths["root"], target_name=target_name)

            from ldm_core.docker_service import DockerService

            compose_base = DockerService.get_compose_cmd_prefix(target_name)
        db_type = (
            project_meta.get("db_type", "postgresql")
            if isinstance(project_meta, dict)
            else "postgresql"
        )
        use_shared_db = context.get("use_shared_db")

        if is_samples or external_snapshot:
            db_svc = f"{sanitize_id(paths['root'].name)}-db"
            db_args = ["up", "-d", db_svc] if not use_shared_db else ["up", "-d"]
            manager.run_command([*compose_base, *db_args], cwd=str(paths["root"]))
            time.sleep(5)
            manager.snapshot.cmd_restore(
                project_id,
                auto_index=1 if is_samples else None,
                backup_dir=external_snapshot if not is_samples else None,
            )

        if getattr(manager.args, "command", "") != "quickstart":
            UI.phase(2, 3, "Starting Container Stack")

        # LDM-#1264: portal patches must be copied in *after* the container
        # exists but *before* it boots -- OSGi resolves bundles at startup, so
        # patching a running container needs a second restart and briefly runs
        # the unpatched JAR. `up -d` gives no such seam, so when patches are
        # present the single `up` becomes create -> cp -> start.
        from ldm_core.runtime import portal_patches as _portal_patches

        # Gated on `no_up`: with nothing being started there is nothing to
        # patch, and the version policy must not abort a run that was only ever
        # going to write configuration.
        portal_patch_plan = (
            []
            if no_up
            else _portal_patches.plan_patches(
                manager,
                paths["root"],
                project_meta.get("tag") if isinstance(project_meta, dict) else None,
                force=getattr(manager.args, "force_portal_patches", False),
            )
        )

        if portal_patch_plan:
            cmd = [*compose_base, "create", "--remove-orphans"]
        else:
            cmd = [*compose_base, "up", "-d", "--remove-orphans"]
        rebuild = context.get("rebuild") or getattr(manager.args, "rebuild", False)
        if rebuild:
            cmd.append("--build")

        force_recreate = context.get("force_recreate") or getattr(
            manager.args, "force_recreate", False
        )
        if force_recreate:
            cmd.append("--force-recreate")

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

            UI.detail(
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
            if project_has_own_db_service(db_type, use_shared_db):
                deps.append(f"{safe_project_id}-db")

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
                        container_target = (
                            dep
                            if dep.startswith(project_id)
                            else f"{project_id}-{dep}-1"
                        )
                        status = manager.get_container_status(container_target)
                        if status in {"healthy", "running"}:
                            time.sleep(2)
                            break
                        if status == "exited":
                            UI.error(f"Dependency '{dep}' exited unexpectedly.")
                            context.stopped = True
                            return None
                        time.sleep(2)
            elif use_shared_db and db_type != "hypersonic":
                from ldm_core.utils import shared_database_container

                global_db_container = shared_database_container(db_type)
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

            # LDM-#1090/#1133: this used to always reclaim permissions on
            # the LOCAL path via a plain local `docker run`, gated only on
            # *this* machine's OS -- for a project on a remote target, the
            # files that matter are the already-synced remote copies (this
            # host's OS is irrelevant to them), and a plain local `docker
            # run` can't even see them. Redirect via the same
            # docker_prefix/map_path the rest of this stage already uses
            # for a remote target; otherwise keep the existing local-only
            # gating unchanged.
            #
            # LDM-#1507: `777` required in both branches below, for the same
            # reason and on the same four trees. These are bind-mounted into
            # the Liferay container, whose Tomcat/Equinox runs as uid 1000; the
            # helper chowns them to the host uid, which is 1001 on Linux CI and
            # never 1000. At `750` the container cannot write `logs/`, cannot
            # pick up a hot deploy from `deploy/`, and cannot manage its own
            # `osgi/state` -- which is precisely the native-Linux breakage
            # LDM-#645 (`6861e26e`) was raised to undo. The remote branch is a
            # deliberate mirror of the local one (LDM-#1090/#1133, `0695db27`)
            # and if anything needs it more: remote targets are Linux by this
            # project's conventions, so no Docker Desktop uid translation
            # papers over a narrower mode there.
            target_context = context.get("target_context")
            if target_context is not None and target_context.is_remote:
                from ldm_core.utils import reclaim_volume_permissions

                for p_key in ["deploy", "logs", "osgi", "files"]:
                    if p_key in paths:
                        reclaim_volume_permissions(
                            target_context.map_path(paths[p_key]),
                            chmod_val="777",
                            docker_prefix=target_context.docker_prefix,
                        )
            elif platform.system().lower() == "linux" or getattr(
                manager.args, "fix_permissions", False
            ):
                from ldm_core.utils import reclaim_volume_permissions

                for p_key in ["deploy", "logs", "osgi", "files"]:
                    if p_key in paths:
                        reclaim_volume_permissions(paths[p_key], chmod_val="777")

            follow = context.get("follow") or getattr(manager.args, "follow", False)
            manager.run_command(cmd, cwd=str(paths["root"]), capture_output=not follow)

            if portal_patch_plan:
                # `cmd` was a `create`, so the containers exist but are not
                # running. Patch, then start.
                _portal_patches.copy_patches_into(
                    manager,
                    portal_patch_plan,
                    project_meta.get("container_name") or project_id,
                    _patch_docker_prefix(manager, target_context),
                    force=getattr(manager.args, "force_portal_patches", False),
                )
                manager.run_command(
                    [*compose_base, "start"],
                    cwd=str(paths["root"]),
                    capture_output=not follow,
                )

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

                if getattr(manager.args, "command", "") != "quickstart":
                    UI.phase(3, 3, "Awaiting Liferay Readiness")

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
            # LDM-#1508: the line above just printed project_id; a tip that
            # omits it makes the user supply it again.
            UI.hint(
                f"Run 'ldm link <path-to-cx>' to attach client extensions, "
                f"or 'ldm logs -f {project_id}' to tail logs."
            )

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
