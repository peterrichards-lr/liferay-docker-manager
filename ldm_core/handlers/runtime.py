import contextlib
import platform
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from ldm_core.docker_service import DockerService
from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import get_actual_home, get_compose_cmd, open_browser, strip_ansi


class RuntimeService(BaseHandler):
    """Service for container lifecycle and orchestration."""

    def __init__(self, manager=None):
        super().__init__(manager.args if manager else None)
        self.manager = manager

    def _generate_keycloak_realm(self, project_root):
        """Dynamically generates the keycloak-realm.json to avoid tracking secrets in git."""
        import json

        from ldm_core.utils import safe_write_text

        realm_data = {
            "realm": "liferay",
            "enabled": True,
            "users": [
                {
                    "username": "test",
                    "enabled": True,
                    "email": "test@liferay.com",
                    "firstName": "Test",
                    "lastName": "Test",
                    "credentials": [
                        {"type": "password", "value": "test", "temporary": False}
                    ],
                }
            ],
            "clients": [
                {
                    "clientId": "liferay-client",
                    "enabled": True,
                    "clientAuthenticatorType": "client-secret",
                    "secret": "secret",  # pragma: allowlist secret
                    "redirectUris": ["*"],
                    "webOrigins": ["*"],
                    "publicClient": False,
                    "protocol": "openid-connect",
                }
            ],
        }

        safe_write_text(
            project_root / "keycloak-realm.json", json.dumps(realm_data, indent=2)
        )

    def cmd_run(self, project_id=None, is_restart=False):
        """Main entry point for starting or updating a project stack."""
        total_start = time.time()
        project_id = (
            project_id
            or self.manager.args.project
            or getattr(self.manager.args, "project_flag", None)
        )
        if getattr(self.manager.args, "select", False) and not project_id:
            if self.manager.non_interactive:
                UI.die("Project selection is not supported in non-interactive mode.")
            selection = self.manager.select_project_interactively(
                heading="Available Projects"
            )
            if not selection:
                return
            if selection.get("new"):
                project_id = None
            else:
                project_id = selection["path"].name

        root = self.manager.detect_project_path(project_id, for_init=True)
        if not root:
            if self.manager.non_interactive:
                UI.die("Project not found and no name provided to initialize.")

            default_name = f"ldm-{int(time.time())}"
            project_id = UI.ask("Enter a new project name to initialize", default_name)
            if not project_id:
                return
            root = self.manager.detect_project_path(project_id, for_init=True)
            if not root:
                UI.die("Failed to resolve project path.")

        project_id = root.name
        is_new_project = not any(
            (root / f).exists() for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]
        )
        if is_new_project:
            UI.print_banner()

        init_success = False
        paths = self.manager.setup_paths(root)
        project_meta = self.manager.read_meta(paths["root"])

        scale_list = getattr(self.manager.args, "scale_list", None)
        if scale_list:
            for arg in scale_list:
                if "=" in arg:
                    service, count = arg.split("=", 1)
                    if count.isdigit():
                        project_meta[f"scale_{service}"] = count
            self.manager.write_meta(paths["root"], project_meta)

        try:
            # Check if project is already running to prevent unexpected container conflicts
            from ldm_core.docker_service import DockerService

            container_name = project_meta.get("container_name") or project_id
            if not getattr(self.manager.args, "no_up", False) and not is_restart:
                if DockerService.is_running(container_name):
                    if self.manager.non_interactive:
                        UI.die(
                            f"Project '{project_id}' is already running. Use 'ldm restart' to apply updates, or 'ldm stop' it first."
                        )
                    elif not UI.confirm(
                        f"Project '{project_id}' is already running. Reconfigure and restart?",
                        "Y",
                    ):
                        return
                    is_restart = True

            tag_latest = getattr(self.manager.args, "tag_latest", False)
            prefix = getattr(self.manager.args, "tag_prefix", None)
            if tag_latest or prefix:
                tag = None
            else:
                tag = (
                    self.manager.args.tag
                    or project_meta.get("tag")
                    or self.manager.defaults.get("tag")
                )
            is_portal = (
                getattr(self.manager.args, "portal", False)
                or str(
                    project_meta.get("portal", self.manager.defaults.get("portal"))
                ).lower()
                == "true"
            )

            # LDM-381: Sanitize tag and auto-detect image type from prefix
            if tag:
                from ldm_core.utils import resolve_liferay_docker_tag

                resolved_tag, resolved_is_portal = resolve_liferay_docker_tag(
                    tag, self.manager
                )
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
                self.manager.args.host_name
                or project_meta.get("host_name")
                or self.manager.defaults.get("host_name")
            )

            # Interactive prompt if --ssl is passed but no explicit host-name is given
            if getattr(self.manager.args, "ssl", None) is True and not getattr(
                self.manager.args, "host_name", None
            ):
                if not self.manager.non_interactive:
                    host_name = UI.ask("Enter project Virtual Hostname", host_name)

            db_type = (
                getattr(self.manager.args, "db", None)
                or project_meta.get("db_type")
                or self.manager.defaults.get("db_type")
            )

            # --- Extensible Stack Archetypes (LDM-Guided-Onboarding) ---
            archetype_name = getattr(
                self.manager.args, "archetype", None
            ) or project_meta.get("archetype")
            if archetype_name:
                from ldm_core.constants import SCRIPT_DIR

                archetype_dir = (
                    SCRIPT_DIR
                    / "ldm_core"
                    / "resources"
                    / "archetypes"
                    / archetype_name
                )
                if not archetype_dir.exists():
                    UI.die(
                        f"Archetype '{archetype_name}' not found. Available archetypes: {[d.name for d in (SCRIPT_DIR / 'ldm_core' / 'resources' / 'archetypes').iterdir() if d.is_dir()]}"
                    )
                project_meta["archetype"] = archetype_name

            # --- External Database (LDM-Guided-Onboarding) ---
            if db_type == "external" and not project_meta.get("jdbc_url"):
                UI.heading("External Database Configuration")
                project_meta["jdbc_url"] = UI.ask(
                    "JDBC URL (e.g. jdbc:postgresql://host:5432/db)",
                    "jdbc:postgresql://db:5432/lportal",
                )
                project_meta["jdbc_user"] = UI.ask("Database Username", "liferay")
                project_meta["jdbc_pass"] = UI.ask("Database Password", "liferay")

            jvm_args = getattr(self.manager.args, "jvm_args", None) or project_meta.get(
                "jvm_args"
            )
            port_val = getattr(self.manager.args, "port", None) or project_meta.get(
                "port", self.manager.defaults.get("port")
            )
            port = int(port_val) if port_val is not None else 8080

            # FAIL FAST: Pre-flight checks before expensive operations
            project_meta["root"] = str(root.resolve())
            project_meta["project_name"] = project_id

            # Container Naming Persistence (LDM-388)
            # Ensure container names are "frozen" in metadata for consistency
            base_container_name = project_meta.get("container_name") or project_id
            project_meta["container_name"] = base_container_name
            project_meta["liferay_container_name"] = base_container_name
            project_meta["db_container_name"] = f"{base_container_name}-db"
            project_meta["tunnel_container_name"] = f"{base_container_name}-lfr-tunnel"

            paths = self.manager.setup_paths(root)
            ssl_val = self.manager.composer._is_ssl_active(host_name, project_meta)

            if not getattr(self.manager.args, "no_up", False):
                port = self.manager._pre_flight_checks(
                    host_name, port, ssl_enabled=ssl_val, meta=project_meta
                )
            else:
                # LDM-381: Even if not starting, check for registry collisions
                # This ensures we don't accidentally register a nested project or name conflict.
                self.manager.check_registry_collisions(
                    project_id, paths["root"], host_name=host_name
                )

            project_meta["port"] = port

            # LDM-422: Handle Manual Reindex Flag
            if getattr(self.manager.args, "reindex", False):
                self.flag_reindex(paths["root"])
                project_meta["reindex_required"] = "true"

            # Performance Overrides
            no_vol_cache = (
                getattr(self.manager.args, "no_vol_cache", False)
                or str(project_meta.get("no_vol_cache", "false")).lower() == "true"
            )

            # LDM-384: Auto-detect external volumes and enable internal-state
            is_external_volume = platform.system().lower() == "darwin" and str(
                root
            ).startswith("/Volumes/")
            if is_external_volume and not getattr(
                self.manager.args, "internal_state", None
            ):
                if str(project_meta.get("internal_state", "false")).lower() != "true":
                    UI.info(
                        "External volume detected. Automatically enabling '--internal-state' for stability."
                    )
                    project_meta["internal_state"] = "true"

            internal_state = (
                getattr(self.manager.args, "internal_state", False)
                or str(project_meta.get("internal_state", "false")).lower() == "true"
            )
            no_jvm_verify = (
                getattr(self.manager.args, "no_jvm_verify", False)
                or str(project_meta.get("no_jvm_verify", "false")).lower() == "true"
            )
            no_tld_skip = (
                getattr(self.manager.args, "no_tld_skip", False)
                or str(project_meta.get("no_tld_skip", "false")).lower() == "true"
            )

            env_type = getattr(self.manager.args, "env_type", None) or project_meta.get(
                "env_type", "dev"
            )
            cpu_limit = getattr(
                self.manager.args, "cpu_limit", None
            ) or project_meta.get("cpu_limit")
            mem_limit = getattr(
                self.manager.args, "mem_limit", None
            ) or project_meta.get("mem_limit")

            if not jvm_args:
                jvm_args = self.manager.composer.get_default_jvm_args()

            is_samples = getattr(self.manager.args, "samples", False)
            if is_samples:
                config_handler = self.manager.config
                if host_name == "localhost":
                    if self.manager.non_interactive:
                        UI.die("--samples requires a custom hostname.")
                    host_name = UI.ask(
                        "Enter project Virtual Hostname", "samples.local"
                    )
                if not tag:
                    tag = config_handler.get_samples_tag()
                if not db_type:
                    db_type = config_handler.get_samples_db_type()

            if not tag:
                tag_latest = getattr(self.manager.args, "tag_latest", False)
                prefix = getattr(self.manager.args, "tag_prefix", None)

                can_discover = tag_latest or bool(prefix)

                # LDM-388: Auto-discover default tag (e.g., latest LTS) in non-interactive mode if no tag is provided
                if self.manager.non_interactive:
                    can_discover = True

                from ldm_core.constants import API_BASE_DXP, API_BASE_PORTAL
                from ldm_core.utils import discover_latest_tag

                api_base = API_BASE_PORTAL if is_portal else API_BASE_DXP

                default_rt = self.manager.defaults.get("release_type", "lts")
                rt = getattr(self.manager.args, "release_type", None)
                if not rt:
                    rt = "any" if prefix else default_rt

                if not can_discover:
                    # Interactive Tag Discovery Sequence
                    # Pre-resolve the default so the user sees the actual tag they will get by pressing Enter
                    if self.manager.verbose:
                        UI.info(
                            f"Pre-resolving latest {rt.upper()} release to populate default prompt..."
                        )

                    default_resolved_tag = discover_latest_tag(
                        api_base,
                        release_type=rt,
                        prefix_filter=prefix,
                        verbose=self.manager.verbose,
                    )

                    ans = UI.ask(
                        "Release type (lts|u|qr), prefix, or specific tag",
                        default_resolved_tag,
                    )

                    if ans == default_resolved_tag:
                        tag = default_resolved_tag
                    elif ans.lower() in ["any", "u", "lts", "qr"]:
                        if self.manager.verbose:
                            UI.info(f"Discovering latest {ans.upper()} release...")
                        tag = discover_latest_tag(
                            api_base,
                            release_type=ans.lower(),
                            verbose=self.manager.verbose,
                        )
                        if not tag:
                            UI.die(f"Could not find any tags for release type: {ans}")
                    else:
                        # Treat as prefix or exact tag
                        if self.manager.verbose:
                            UI.info(f"Discovering latest tag matching prefix: {ans}...")
                        tag = discover_latest_tag(
                            api_base,
                            release_type="any",
                            prefix_filter=ans,
                            verbose=self.manager.verbose,
                        )
                        if not tag:
                            # Fallback: trust the user input if not found in the registry (e.g. custom or unlisted tag)
                            tag = ans
                else:
                    if self.manager.verbose:
                        UI.info("Automatically discovering latest Liferay tag...")
                    tag = discover_latest_tag(
                        api_base,
                        release_type=rt,
                        prefix_filter=prefix,
                        verbose=self.manager.verbose,
                    )
                    if not tag:
                        UI.die(
                            "Failed to discover latest Liferay tag. Please specify one explicitly with -t."
                        )
                    if self.manager.verbose:
                        UI.success(f"Using tag: {tag}")

            external_snapshot = getattr(self.manager.args, "snapshot", None)
            if external_snapshot:
                snap_path = Path(external_snapshot).resolve()
                snap_meta = self.manager.read_meta(snap_path)
                tag = tag or snap_meta.get("tag")
                db_type = db_type or snap_meta.get("db_type")

            # Validate tag against official releases if it's new or user-provided
            if tag and tag != project_meta.get("tag"):
                from ldm_core.utils import validate_liferay_tag

                if self.manager.verbose:
                    UI.info(f"Validating tag '{tag}' against Liferay releases...")
                if not validate_liferay_tag(tag):
                    UI.warning(
                        f"Tag '{tag}' is not listed in official Liferay releases. If this is not a custom image, the Docker pull may fail."
                    )

            is_share = (
                getattr(self.manager.args, "share", False) is True
                or getattr(self.manager.args, "expose", False) is True
                or str(project_meta.get("share", "false")).lower() == "true"
            )
            share_subdomain = getattr(
                self.manager.args, "share_subdomain", None
            ) or project_meta.get("share_subdomain")
            share_image = getattr(
                self.manager.args, "share_image", None
            ) or project_meta.get("share_image")
            share_inspector = (
                getattr(self.manager.args, "share_inspector", False) is True
                or str(project_meta.get("share_inspector", "false")).lower() == "true"
            )

            share_domain = getattr(
                self.manager.args, "share_domain", None
            ) or project_meta.get("share_domain")
            share_provider = getattr(
                self.manager.args, "share_provider", None
            ) or project_meta.get("share_provider")

            if is_share and getattr(self.manager.args, "expose", False) is True:
                share_provider = "ngrok"

            if is_share and share_provider != "ngrok":
                share_provider, share_domain = self.manager.share.resolve_share_config(
                    project_meta
                )

            if not share_provider:
                share_provider = "lfr-tunnel"

            is_expose = (
                getattr(self.manager.args, "expose", False) is True
                or str(project_meta.get("expose", "false")).lower() == "true"
                or (is_share and share_provider == "ngrok")
            )
            if is_expose:
                auth_token = self.manager.config.get_ngrok_auth_token()
                if not auth_token:
                    UI.info(
                        "An ngrok Auth Token is required to use the expose feature (it enables custom host headers and HTTPS)."
                    )
                    UI.info(
                        f"You can find yours at: {UI.CYAN}https://dashboard.ngrok.com/get-started/your-authtoken{UI.COLOR_OFF}"
                    )
                    auth_token = UI.ask("Enter your ngrok Auth Token")
                    if auth_token:
                        self.manager.config.set_ngrok_auth_token(auth_token)
                        UI.success("Saved ngrok token to global configuration.")
                    else:
                        UI.warning("No token provided. Ngrok will not be configured.")
                        is_expose = False
                        if hasattr(self.manager.args, "expose"):
                            self.manager.args.expose = False
                        is_share = False

            if is_new_project and self.manager.assets._ensure_seeded(
                tag, db_type, paths
            ):
                from ldm_core.constants import SEED_VERSION

                project_meta = self.manager.read_meta(paths["root"])
                project_meta["seeded"] = "true"
                project_meta["seed_version"] = str(SEED_VERSION)
                self.manager.write_meta(paths["root"], project_meta)
                if hasattr(self.manager, "config") and hasattr(
                    self.manager.config, "track_roi"
                ):
                    self.manager.config.track_roi(840, "first-boot seeding")
                is_new_project = False

            default_shared = (
                "true"
                if self.manager.defaults.get("search_mode") == "shared"
                else "false"
            )
            use_shared_search = (
                str(project_meta.get("use_shared_search", default_shared)).lower()
                == "true"
            )
            if getattr(self.manager.args, "sidecar", False):
                use_shared_search = False

            # Resolve persist_osgi flag
            persist_osgi_arg = getattr(self.manager.args, "persist_osgi", None)
            if persist_osgi_arg is not None:
                persist_osgi = persist_osgi_arg
            else:
                persist_osgi = (
                    str(project_meta.get("persist_osgi", "false")).lower() == "true"
                )

            self.manager.verify_runtime_environment(paths)

            # OSGi State Persistence Validation
            if persist_osgi:
                osgi_state_dir = paths["state"]
                tag_marker = osgi_state_dir / ".ldm_tag"

                if osgi_state_dir.exists():
                    with contextlib.suppress(Exception):
                        saved_tag = (
                            tag_marker.read_text().strip()
                            if tag_marker.exists()
                            else None
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
                tag_marker.write_text(tag)

            # Proactive Search Lock Clearing (LDM-369)
            # Stale lock files in sidecar indices cause 'access_denied_exception' and block indexing.
            # We also enforce permissions again just before boot.
            es_data = paths["data"] / "elasticsearch8"
            use_volumes = self.manager.composer.is_using_named_volumes()

            if es_data.exists() and not use_volumes:
                UI.detail("Clearing stale search locks and enforcing permissions...")
                for lock_file in es_data.rglob("write.lock"):
                    with contextlib.suppress(Exception):
                        lock_file.unlink()

                if platform.system().lower() != "windows":
                    from ldm_core.utils import run_command

                    run_command(["chmod", "-R", "777", str(es_data)], check=False)

            if is_samples:
                self.manager.config.sync_samples(paths)

            no_captcha = (
                getattr(self.manager.args, "no_captcha", False)
                or str(project_meta.get("no_captcha", "false")).lower() == "true"
            )
            fast_login = (
                getattr(self.manager.args, "fast_login", False)
                or str(project_meta.get("fast_login", "false")).lower() == "true"
            )

            features = getattr(self.manager.args, "feature", None)
            if features:
                # Flatten potential comma-separated values
                flat_features = []
                for f in features:
                    flat_features.extend([x.strip() for x in f.split(",") if x.strip()])
                project_meta["features"] = ",".join(flat_features)

            project_meta.update(
                {
                    "project_name": project_id,
                    "tag": tag,
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
            self.manager.write_meta(paths["root"], project_meta)

            # --- Extensible Stack Archetypes Asset Copy ---
            if is_new_project and project_meta.get("archetype"):
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

                    # Copy everything except archetype.json and compose-overlay.yml
                    for item in archetype_dir.iterdir():
                        if item.name not in ["archetype.json", "compose-overlay.yml"]:
                            dest = paths["root"] / item.name
                            if item.is_dir():
                                shutil.copytree(item, dest, dirs_exist_ok=True)
                            else:
                                shutil.copy2(item, dest)

                    if project_meta["archetype"] == "keycloak-sso":
                        self._generate_keycloak_realm(paths["root"])

            init_success = True
            self.manager.register_project(
                project_id, paths["root"], host_name=host_name
            )

            if is_samples or external_snapshot:
                self.sync_stack(
                    paths, project_meta, no_up=True, no_wait=True, show_summary=False
                )
                self.manager.run_command(
                    [*get_compose_cmd(), "up", "-d", "db"], cwd=str(paths["root"])
                )
                time.sleep(5)
                self.manager.snapshot.cmd_restore(
                    project_id,
                    auto_index=1 if is_samples else None,
                    backup_dir=external_snapshot if not is_samples else None,
                )

            self.sync_stack(
                paths,
                project_meta,
                follow=getattr(self.manager.args, "follow", False),
                rebuild=getattr(self.manager.args, "rebuild", False),
                no_up=getattr(self.manager.args, "no_up", False),
                no_wait=getattr(self.manager.args, "no_wait", False),
                total_start=total_start,
            )
        finally:
            # Rollback: If a brand-new project failed to initialize, clean it up
            # and unregister it to avoid inconsistent state or 'zombie' registry entries.
            if is_new_project and not init_success:
                if root.exists():
                    UI.info(f"Cleaning up failed initialization: {root}")
                    self.manager.safe_rmtree(root)
                self.manager.unregister_project(project_id)

    def cmd_reseed(self, project_id=None):
        """Triggers a re-bootstrap of the project from a fresh seed."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return None
        project_meta = self.manager.read_meta(root)
        tag = project_meta.get("tag")
        db_type = project_meta.get("db_type")

        default_shared = (
            "true" if self.manager.parse_version(tag) >= (2025, 1, 0) else "false"
        )
        use_shared = (
            str(project_meta.get("use_shared_search", default_shared)).lower() == "true"
        )
        if not use_shared and self.manager.parse_version(tag) >= (2025, 2, 0):
            use_shared = True
        search_mode = "shared" if use_shared else "sidecar"

        if not tag:
            UI.die("Project missing tag metadata. Cannot reseed.")

        is_dry_run = getattr(self.manager, "dry_run", False)
        if is_dry_run:
            UI.info(f"Reseed {root.name} from {tag} ({db_type}/{search_mode})...")
            UI.info(
                f"  {UI.BYELLOW}- [Dry Run] Would reset project stack (cmd_reset all).{UI.COLOR_OFF}"
            )
            UI.info(
                f"  {UI.BYELLOW}- [Dry Run] Would fetch and extract new seed for tag: {tag}.{UI.COLOR_OFF}"
            )
            up_flag = getattr(self.manager.args, "up", False)
            if up_flag:
                UI.info(
                    f"  {UI.BYELLOW}- [Dry Run] Would start the project containers (cmd_run).{UI.COLOR_OFF}"
                )
            UI.success(
                f"[Dry Run] Project {root.name} reseed completed (no changes made)."
            )
            return True

        if UI.confirm(
            f"Reseed {root.name} from {tag} ({db_type}/{search_mode})? ALL LOCAL DATA WILL BE LOST.",
            "N",
        ):
            self.cmd_reset(root.name, target="all")
            paths = self.manager.setup_paths(root)
            if self.manager.assets._fetch_seed(tag, db_type, search_mode, paths):
                UI.success("Reseed complete.")
                up_flag = getattr(self.manager.args, "up", False)
                if up_flag or (
                    not self.manager.non_interactive
                    and UI.confirm("Do you want to start the project now?", "Y")
                ):
                    self.cmd_run(project_id)
                else:
                    UI.info(
                        f"Run {UI.CYAN}ldm run {root.name}{UI.COLOR_OFF} to start the project."
                    )
            else:
                UI.error("Reseed failed.")
        return None

    def _scan_for_expected_deployables(self, root_path):
        """Scans workspace deploy and client-extensions paths for deployable targets.

        Returns a dict of {bundle_symbolic_name_or_cx_id: expected_state}
        """
        import zipfile

        import yaml

        targets = {}

        # 1. Scan configs/common/deploy and deploy directories
        deploy_dirs = [
            root_path / "configs" / "common" / "deploy",
            root_path / "deploy",
        ]

        for d in deploy_dirs:
            if not d.exists() or not d.is_dir():
                continue
            for item in d.glob("*"):
                if item.suffix.lower() in [".jar", ".war"]:
                    try:
                        with zipfile.ZipFile(item) as z:
                            try:
                                manifest_content = z.read(
                                    "META-INF/MANIFEST.MF"
                                ).decode("utf-8", errors="ignore")
                                # Unfold manifest lines
                                unfolded_lines = []
                                for line in manifest_content.splitlines():
                                    if line.startswith(" ") and unfolded_lines:
                                        unfolded_lines[-1] += line[1:]
                                    else:
                                        unfolded_lines.append(line)

                                symbolic_name = None
                                is_fragment = False
                                for line in unfolded_lines:
                                    if line.startswith("Bundle-SymbolicName:"):
                                        val = line.split(":", 1)[1].strip()
                                        symbolic_name = val.split(";")[0].strip()
                                    elif line.startswith("Fragment-Host:"):
                                        is_fragment = True

                                if symbolic_name:
                                    expected_state = (
                                        "Resolved" if is_fragment else "Active"
                                    )
                                    targets[symbolic_name] = expected_state
                            except KeyError:
                                pass
                    except Exception as e:
                        UI.debug(f"Failed to scan manifest for {item.name}: {e}")

        # 2. Scan client-extensions directory
        cx_dir = root_path / "client-extensions"
        if cx_dir.exists() and cx_dir.is_dir():
            for item in cx_dir.glob("*"):
                if item.is_dir():
                    yaml_file = item / "client-extension.yaml"
                    if yaml_file.exists():
                        try:
                            with open(yaml_file) as f:
                                cx_yaml = yaml.safe_load(f)
                                if cx_yaml and isinstance(cx_yaml, dict):
                                    for key, val in cx_yaml.items():
                                        if isinstance(val, dict):
                                            targets[key] = "Active"
                        except Exception as e:
                            UI.debug(
                                f"Failed to parse client-extension.yaml in {item.name}: {e}"
                            )

        return targets

    def cmd_wait(
        self,
        project_id=None,
        timeout=900,
        wait_for_deployables=False,
        wait_for_bundles=None,
    ):
        """Block execution until project is fully ready (HTTP 200/302)."""
        if timeout is None:
            timeout = 900

        root = self.manager.detect_project_path(project_id)
        if not root:
            return None
        meta = self.manager.read_meta(root)
        host_name = meta.get("host_name", "localhost")

        # 1. Wait for Container/Log Readiness
        if not self._wait_for_ready(meta, host_name, timeout=timeout):
            UI.die(f"Project '{project_id}' failed to become ready within {timeout}s.")

        # Determine target expected deployables
        expected_targets = {}
        if wait_for_deployables:
            expected_targets.update(self._scan_for_expected_deployables(root))
        if wait_for_bundles:
            for b in wait_for_bundles.split(","):
                expected_targets[b.strip()] = "Active"

        # 2. Wait for HTTP Availability
        UI.info(
            f"Verifying HTTP accessibility for {UI.CYAN}{host_name}{UI.COLOR_OFF}..."
        )
        ssl_enabled = self.manager.composer._is_ssl_active(host_name, meta)
        port = meta.get("port", 8080)
        protocol = "https" if ssl_enabled else "http"

        # LDM-388: Use explicit IP for local checks to avoid CI IPv6 quirks
        target_host = "127.0.0.1" if host_name == "localhost" else host_name
        url = f"{protocol}://{target_host}"
        if not ssl_enabled and port != 80:
            url += f":{port}"

        phase_start = time.time()
        import requests

        http_ready = False
        while time.time() - phase_start < timeout:
            try:
                # Use a short timeout for the request itself
                response = requests.get(url, timeout=5, verify=False)  # nosec B501
                if response.status_code in [200, 302]:
                    UI.success(
                        f"Project '{project_id}' is responding to HTTP ({response.status_code})."
                    )
                    http_ready = True
                    break
            except Exception:
                pass
            time.sleep(2)

        if not http_ready:
            UI.die(
                f"Project '{project_id}' is running but HTTP {url} is not responding correctly."
            )

        # 2b. Wait for Deployables (OSGi & Client Extensions) if any targets exist
        if expected_targets:
            UI.info(
                f"Waiting for {len(expected_targets)} deployable targets to be fully active..."
            )
            container_name = (
                meta.get("liferay_container_name")
                or meta.get("container_name")
                or root.name
            )

            # Wait for deploy directory inside container to clear
            UI.info("Checking deploy directory queue status...")
            deploy_clear = False
            deploy_start = time.time()
            while time.time() - deploy_start < timeout:
                try:
                    res = DockerService.exec(
                        container_name,
                        ["ls", "/opt/liferay/deploy"],
                        check=False,
                    )
                    if res:
                        files = [f.strip() for f in res.splitlines() if f.strip()]
                        deployables = [
                            f for f in files if f.endswith((".jar", ".zip", ".war"))
                        ]
                        if not deployables:
                            deploy_clear = True
                            break
                    else:
                        deploy_clear = True
                        break
                except Exception:
                    pass
                time.sleep(2)

            if not deploy_clear:
                UI.warning(
                    "Deploy directory queue did not clear, proceeding to Gogo console verification..."
                )

            # Wait for targets via Gogo Shell
            UI.info("Verifying target OSGi bundle and Client Extension states...")
            gogo_ready = False
            gogo_start = time.time()
            while time.time() - gogo_start < timeout:
                try:
                    res = DockerService.exec(
                        container_name,
                        ["sh", "-c", "echo 'lb -s' | telnet localhost 11311"],
                        check=False,
                    )
                    if res and "|" in res:
                        # Parse lb -s output
                        bundles = {}
                        for line in res.splitlines():
                            parts = [p.strip() for p in line.split("|")]
                            if len(parts) >= 4:
                                state = parts[1]
                                sym_name = parts[3]
                                bundles[sym_name] = state

                        satisfied = True
                        for target, expected in expected_targets.items():
                            # Direct match
                            if target in bundles:
                                if bundles[target] != expected:
                                    satisfied = False
                                    break
                            else:
                                # Client Extension match: symbolic name contains the target ID and "client.extension"
                                cx_bundle_found = False
                                for sym_name, state in bundles.items():
                                    if (
                                        target in sym_name
                                        and "client.extension" in sym_name
                                    ):
                                        cx_bundle_found = True
                                        if state != expected:
                                            satisfied = False
                                        break
                                if not cx_bundle_found or not satisfied:
                                    satisfied = False
                                    break

                        if satisfied:
                            UI.success(
                                "All deployables and client extensions are fully started."
                            )
                            gogo_ready = True
                            break
                    elif res:
                        # Gogo console responded but not with the bundle table (e.g. error/command not found)
                        break
                except Exception as e:
                    UI.debug(f"Gogo shell query failed: {e}")
                time.sleep(3)

            if not gogo_ready:
                UI.warning(
                    "Some deployable targets did not reach active state via Gogo console verification."
                )

        # 3. Wait for System to become Idle (CPU Drop)
        UI.info("Waiting for background initialization to complete (CPU Idle)...")
        idle_checks = 0
        consecutive_required = 3
        cpu_threshold = 15.0  # Consider < 15% CPU to be "idle" for Liferay

        phase_start = time.time()
        while time.time() - phase_start < timeout:
            try:
                result = self.manager.run_command(
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{.CPUPerc}}",
                        meta.get("container_name"),
                    ],
                    capture_output=True,
                    check=False,
                )
                if result:
                    cpu_str = result.strip().replace("%", "")
                    try:
                        cpu = float(cpu_str)
                        if cpu < cpu_threshold:
                            idle_checks += 1
                            if idle_checks >= consecutive_required:
                                UI.success(
                                    f"Project '{project_id}' is fully initialized and idle."
                                )
                                return True
                        else:
                            idle_checks = 0
                    except ValueError:
                        pass
            except Exception:
                pass
            time.sleep(2)

        UI.warning(
            f"Project '{project_id}' did not reach an idle state within the timeout, but is responding to HTTP."
        )
        return True

    def _print_ngrok_url(self, project_id):
        """Fetches and prints the public ngrok URL from the running container."""
        import json

        from ldm_core.ui import UI

        container_name = f"{project_id}-ngrok-1"
        try:
            result = self.manager.run_command(
                [
                    "docker",
                    "exec",
                    container_name,
                    "curl",
                    "-s",
                    "http://localhost:4040/api/tunnels",
                ],
                capture_output=True,
                check=False,
            )
            if result and result.stdout:
                data = json.loads(result.stdout)
                for tunnel in data.get("tunnels", []):
                    if tunnel.get("public_url", "").startswith("https://"):
                        public_url = tunnel["public_url"]
                        UI.success(
                            f"🌍 Public ngrok Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
                        )
                        return
        except Exception:
            pass
        UI.warning("ngrok container is running, but failed to retrieve public URL.")

    def _wait_for_ready(self, project_meta, host_name, total_start=None, timeout=600):
        """Wait for Liferay to become healthy and provide access information."""
        container_name = project_meta.get("container_name")
        start_time = time.time()

        with UI.spinner(
            f"Waiting for Liferay to become healthy ({container_name})..."
        ) as spinner:
            last_notified_time = 0
            while time.time() - start_time < timeout:
                elapsed = time.time() - start_time
                # Notify every 30 seconds (Robust timestamp check)
                if elapsed - last_notified_time >= 30:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    duration_str = UI.format_duration(elapsed)
                    spinner.update(f"Still waiting for Liferay ({duration_str})...")
                    last_notified_time = elapsed
                    UI.detail(
                        f"[{timestamp}] Still waiting for Liferay to become healthy... ({duration_str})"
                    )

                    # Proactive Log Monitoring: Look for ERRORS
                    try:
                        logs = self.manager.run_command(
                            ["docker", "logs", "--tail", "100", container_name],
                            check=False,
                            capture_output=True,
                        )
                        if logs:
                            error_lines = [
                                line.strip()
                                for line in logs.splitlines()
                                if "ERROR" in line.upper()
                                or "FATAL" in line.upper()
                                or "CRITICAL" in line.upper()
                            ]
                            if error_lines:
                                UI.warning(
                                    f"LDM detected {len(error_lines)} error(s) in the logs."
                                )
                                # Display the most recent unique error
                                last_unique_error = list(dict.fromkeys(error_lines))[-1]
                                UI.info(
                                    f"Recent log error: {UI.YELLOW}{last_unique_error[:120]}...{UI.COLOR_OFF}"
                                )

                                # --- Auto-Thaw & Hints Win ---
                                from ldm_core.utils import (
                                    check_troubleshooting_signatures,
                                )

                                advice = None
                                for err_line in reversed(error_lines):
                                    advice = check_troubleshooting_signatures(err_line)
                                    if advice:
                                        break

                                if advice:
                                    UI.warning(f"Troubleshooting Advice:\n  {advice}")

                                if (
                                    "ClusterBlockException" in last_unique_error
                                    or "index.blocks.read_only" in last_unique_error
                                ):
                                    UI.warning(
                                        "Detected Elasticsearch disk pressure blocking Liferay startup."
                                    )
                                    if self.manager.infra.thaw_elasticsearch():
                                        UI.success(
                                            "Auto-Thaw successful. Liferay should now proceed."
                                        )
                                    else:
                                        UI.info(
                                            f"💡 {UI.CYAN}Hint:{UI.COLOR_OFF} Your disk is likely full. Run '{UI.WHITE}ldm prune --seeds --samples{UI.COLOR_OFF}' to free space."
                                        )

                                UI.info(
                                    f"Check full logs: {UI.WHITE}ldm logs -f {container_name}{UI.COLOR_OFF}"
                                )
                    except Exception:
                        pass

                    last_notified_time = elapsed

                # LDM-385: Enhanced readiness check
                # We look for the Tomcat 'Server startup' log marker as it's often
                # faster/more reliable than the Docker healthcheck in CI.
                ready_by_logs = False
                try:
                    logs = self.manager.run_command(
                        ["docker", "logs", "--tail", "100", container_name],
                        check=False,
                        capture_output=True,
                    )
                    if (
                        logs
                        and "org.apache.catalina.startup.Catalina.start Server startup in"
                        in logs
                    ):
                        ready_by_logs = True
                except Exception:
                    pass

                status = self.manager.run_command(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.State.Health.Status}}",
                        container_name,
                    ],
                    check=False,
                )

                if status == "healthy" or ready_by_logs:
                    # LDM-422: Proactive Search Reindex Monitoring (UX Win)
                    if (
                        str(project_meta.get("reindex_required", "false")).lower()
                        == "true"
                    ):
                        spinner.update("Search reindexing in progress...")
                        reindex_start = time.time()
                        reindex_timeout = 900  # 15 minutes max
                        found_start = False

                        while time.time() - reindex_start < reindex_timeout:
                            try:
                                # Fetch logs to catch the transition
                                reindex_logs = self.manager.run_command(
                                    ["docker", "logs", "--tail", "200", container_name],
                                    check=False,
                                    capture_output=True,
                                )

                                # Phase 1: Detect Start
                                if not found_start and (
                                    "reindexing all" in reindex_logs.lower()
                                ):
                                    spinner.update("Reindexing all search indexes...")
                                    found_start = True

                                # Phase 2: Detect Completion
                                if "reindexing all" in reindex_logs.lower() and (
                                    "completed in" in reindex_logs.lower()
                                    or "finished" in reindex_logs.lower()
                                ):
                                    break

                                # Fallback: Idle CPU check
                                if time.time() - reindex_start > 120:
                                    stats = self.manager.run_command(
                                        [
                                            "docker",
                                            "stats",
                                            "--no-stream",
                                            "--format",
                                            "{{.CPUPerc}}",
                                            container_name,
                                        ],
                                        check=False,
                                        capture_output=True,
                                    )
                                    if (
                                        stats
                                        and float(stats.strip().replace("%", "")) < 5.0
                                    ):
                                        break

                            except Exception:
                                pass
                            time.sleep(5)

                        # Clear the flag so we don't wait on future boots
                        project_meta["reindex_required"] = "false"
                        root_path = self.manager.detect_project_path(
                            project_id=None, for_init=True
                        )
                        if root_path:
                            self.manager.write_meta(root_path, project_meta)
                    # If we bypassed by logs, wait a tiny bit to ensure the port is truly bound
                    if status != "healthy":
                        time.sleep(2)

                    ts = getattr(self.manager.args, "total_start", None)
                    duration_total = (
                        time.time() - float(ts) if ts else time.time() - start_time
                    )

                    duration_str = UI.format_duration(duration_total)

                    UI.success(f"Liferay is ready! (Total time: {duration_str})")
                    share_enabled = (
                        str(project_meta.get("share", "false")).lower() == "true"
                        or str(project_meta.get("expose", "false")).lower() == "true"
                        or getattr(self.manager.args, "share", False)
                    )
                    proxy_ports = self.manager.infra.get_proxy_ports()
                    active_ssl_port = proxy_ports["https"]

                    access_url = None
                    if share_enabled:
                        share_provider = (
                            project_meta.get("share_provider")
                            or getattr(self.manager.args, "share_provider", None)
                            or "lfr-tunnel"
                        )
                        share_subdomain = (
                            project_meta.get("share_subdomain")
                            or getattr(self.manager.args, "share_subdomain", None)
                            or project_meta.get("project_name")
                            or host_name
                        )
                        if share_provider in ["lfr-tunnel", "lfr-tunnel-docker"]:
                            access_url = self.manager.share.resolve_public_tunnel_url(
                                share_subdomain, project_meta.get("project_name")
                            )

                    if not access_url:
                        access_url = (
                            f"https://{host_name}"
                            if host_name != "localhost"
                            else f"http://localhost:{project_meta.get('port', 8080)}"
                        )
                        if host_name != "localhost" and active_ssl_port != 443:
                            access_url = f"https://{host_name}:{active_ssl_port}"

                    UI.info(
                        f"Access your instance at: {UI.CYAN}{UI.BOLD}{access_url}{UI.COLOR_OFF}"
                    )
                    is_legacy_expose = (
                        str(project_meta.get("expose", "false")).lower() == "true"
                        and str(project_meta.get("share", "false")).lower() != "true"
                    )
                    if is_legacy_expose:
                        self._print_ngrok_url(project_meta.get("container_name"))

                    if str(project_meta.get("share", "false")).lower() == "true":
                        share_subdomain = project_meta.get(
                            "share_subdomain"
                        ) or project_meta.get("project_name")
                        share_port = project_meta.get("port", 8080)
                        share_provider = (
                            project_meta.get("share_provider") or "lfr-tunnel"
                        )
                        self.manager.share.cmd_start(
                            project_id=project_meta.get("project_name"),
                            subdomain=share_subdomain,
                            ports=str(share_port),
                            provider=share_provider,
                            image=project_meta.get("share_image"),
                            inspector=str(
                                project_meta.get("share_inspector", "false")
                            ).lower()
                            == "true",
                        )

                    UI.detail("=== Useful Commands ===")
                    UI.detail(
                        f"  {UI.CYAN}ldm logs -f {container_name}{UI.COLOR_OFF}  Tail logs"
                    )
                    UI.detail(
                        f"  {UI.CYAN}ldm shell {container_name}{UI.COLOR_OFF}    Enter bash"
                    )
                    UI.detail(
                        f"  {UI.CYAN}ldm status {container_name}{UI.COLOR_OFF}   Check health"
                    )
                    UI.detail(
                        f"  {UI.CYAN}ldm stop {container_name}{UI.COLOR_OFF}     Stop stack"
                    )
                    UI.detail("")

                    if getattr(self.manager.args, "browser", False):
                        UI.info(f"Launching browser: {access_url}/web/guest/home")
                        open_browser(f"{access_url}/web/guest/home")
                    return True

                # Fail fast if container exited
                container_state = self.manager.get_container_status(container_name)
                if container_state == "exited":
                    UI.error(
                        f"Liferay container '{container_name}' exited unexpectedly."
                    )
                    return False

                time.sleep(5)  # Shorter sleep for more responsive status checks

        UI.error("\nTimed out waiting for Liferay to become healthy.")
        return False

    def sync_stack(
        self,
        paths,
        project_meta,
        follow=False,
        rebuild=False,
        no_up=False,
        no_wait=False,
        show_summary=True,
        total_start=None,
    ):
        """Orchestrates stack configuration and startup."""
        # Ensure paths is a dictionary for subscripting
        if not isinstance(paths, dict):
            paths = self.manager.setup_paths(paths)

        compose_base = get_compose_cmd()
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )

        # Check for Liferay/PostgreSQL Downgrade (LDM-Safe-Downgrade-Prevention)
        tag = project_meta.get("tag")
        db_type = project_meta.get("db_type", "postgresql")
        current_pg_ver = None
        if db_type in ["postgresql", "postgres"]:
            from ldm_core.utils import resolve_dependency_version

            current_pg_ver = resolve_dependency_version(tag, "postgresql") or "16"

        if not getattr(self.manager.args, "force_downgrade", False):
            last_lr_ver = project_meta.get("last_run_liferay_version")
            if last_lr_ver and self.manager.parse_version(
                tag
            ) < self.manager.parse_version(last_lr_ver):
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
                        if self.manager.parse_version(
                            current_pg_ver
                        ) > self.manager.parse_version(last_pg_ver):
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
                and self.manager.parse_version(current_pg_ver)
                < self.manager.parse_version(last_pg_ver)
            ):
                UI.die(
                    f"Downgrade detected: PostgreSQL version changed from '{last_pg_ver}' to '{current_pg_ver}'. "
                    f"PostgreSQL does not support automatic database directory downgrades. Use '--force-downgrade' to bypass."
                )

        # Check for Liferay Upgrade (Issue #209)
        project_id = project_meta.get("container_name")
        last_lr_ver = project_meta.get("last_run_liferay_version")
        is_upgrade = False
        if last_lr_ver:
            try:
                is_upgrade = self.manager.parse_version(
                    tag
                ) > self.manager.parse_version(last_lr_ver)
            except Exception:
                pass

        upgrade_db = False
        if is_upgrade:
            # 1. Database Backup Option
            backup_on_upgrade = getattr(self.manager.args, "backup_on_upgrade", False)
            no_backup_on_upgrade = getattr(
                self.manager.args, "no_backup_on_upgrade", False
            )

            if not backup_on_upgrade and not no_backup_on_upgrade:
                if not self.manager.non_interactive:
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
                if db_type_val not in ["hypersonic", "external"]:
                    # Check if DB container is running
                    is_running = self.manager.run_command(
                        ["docker", "ps", "-q", "-f", f"name=^{db_container}$"]
                    )
                    compose_file = paths["root"] / "docker-compose.yml"
                    if compose_file.exists() and not is_running:
                        UI.info(
                            "Starting database container temporarily to take a snapshot backup..."
                        )
                        self.manager.run_command(
                            [*compose_base, "-f", str(compose_file), "up", "-d", "db"]
                        )
                        time.sleep(5)

                    # Create the snapshot
                    snapshot_name = f"Pre-upgrade snapshot to {tag}"
                    old_args_name = getattr(self.manager.args, "name", None)
                    self.manager.args.name = snapshot_name
                    try:
                        self.manager.snapshot.cmd_snapshot(project_id)
                        UI.success(
                            f"Database backup snapshot '{snapshot_name}' created successfully."
                        )
                    except Exception as e:
                        UI.warning(f"Failed to create pre-upgrade database backup: {e}")
                    finally:
                        if old_args_name is not None:
                            self.manager.args.name = old_args_name
                        elif hasattr(self.manager.args, "name"):
                            delattr(self.manager.args, "name")

            # 2. Database Auto-Upgrade Option
            if getattr(self.manager.args, "upgrade_db", False):
                upgrade_db = True
            elif getattr(self.manager.args, "no_upgrade_db", False):
                upgrade_db = False
            elif not self.manager.non_interactive:
                UI.warning(
                    "New Liferay versions often require a database schema upgrade."
                )
                if UI.confirm(
                    "Do you want to run Liferay's database auto-upgrade tool on startup?",
                    default=True,
                ):
                    upgrade_db = True
            else:
                upgrade_db = False

        liferay_env = ["LIFERAY_HOME=/opt/liferay"]
        if upgrade_db:
            liferay_env.append(
                "LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true"
            )
        project_id = project_meta.get("container_name")
        host_name = project_meta.get("host_name", "localhost")

        ssl_enabled = self.manager.composer._is_ssl_active(host_name, project_meta)
        ssl_port_val = project_meta.get("ssl_port", 443)
        ssl_port = int(ssl_port_val) if ssl_port_val is not None else 443
        use_shared_search = (
            str(project_meta.get("use_shared_search", "true")).lower() == "true"
        )
        db_mode = (
            getattr(self.manager.args, "database_mode", None)
            or project_meta.get("database_mode")
            or self.manager.defaults.get("database_mode", "isolated")
        )
        use_shared_db = db_mode == "shared"

        if host_name != "localhost":
            liferay_env.extend(
                [
                    "LIFERAY_WEB_PERIOD_SERVER_PERIOD_DISPLAY_PERIOD_NODE_PERIOD_NAME=true",
                    "LIFERAY_REDIRECT_PERIOD_URL_PERIOD_IPS_PERIOD_ALLOWED=127.0.0.1,0.0.0.0/0",
                ]
            )

        self.manager.infra._ensure_network()
        if ssl_enabled or getattr(self.manager.args, "search", False) or use_shared_db:
            infra_start = time.time()
            resolved_ip = self.manager.get_resolved_ip(host_name) or "127.0.0.1"

            if ssl_enabled and not no_up:
                ssl_start = time.time()
                actual_home = get_actual_home()
                cert_dir = actual_home / "liferay-docker-certs"
                self.manager.infra.setup_ssl(cert_dir, host_name)
                if self.manager.verbose:
                    duration_str = UI.format_duration(time.time() - ssl_start)
                    UI.debug(f"SSL certificate generation took: {duration_str}")

            ssl_port = self.manager.infra.setup_infrastructure(
                resolved_ip,
                ssl_port,
                use_ssl=ssl_enabled,
                quiet=not show_summary,
                use_shared_search=use_shared_search,
                use_shared_db=use_shared_db,
            )
            project_meta["ssl_port"] = ssl_port

            if use_shared_db and not no_up:
                from ldm_core.utils import sanitize_id

                db_name = f"lportal_{sanitize_id(project_id).replace('-', '_')}"
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
                exists_check = self.manager.run_command(
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
                    self.manager.run_command(create_cmd, check=True)
                    UI.success(
                        f"Created database '{db_name}' on global PostgreSQL container."
                    )

            if self.manager.verbose:
                duration_str = UI.format_duration(time.time() - infra_start)
                UI.debug(f"Infrastructure setup took: {duration_str}")

        config_handler = self.manager.config
        config_handler.sync_common_assets(
            paths, version=project_meta.get("tag"), project_meta=project_meta
        )
        config_handler.sync_logging(paths)

        # LDM-369: DO NOT include-and-override=portal-developer.properties by default.
        # This MUST happen after sync_common_assets to ensure it sticks.
        config_handler.remove_portal_ext(paths, ["include-and-override"])

        self.manager.composer.write_docker_compose(
            paths, project_meta, liferay_env=liferay_env
        )

        UI.debug("Validating generated docker-compose.yml syntax...")
        self.manager.run_command(
            [*get_compose_cmd(), "config", "--quiet"],
            cwd=str(paths["root"]),
            check=True,
        )

        # Check for port collisions across all exposed host ports in docker-compose.yml
        compose_file = paths["root"] / "docker-compose.yml"
        if (
            compose_file.exists()
            and not no_up
            and not getattr(self.manager.args, "no_up", False)
        ):
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

                for svc_name, host_port in ports_to_check:
                    container_name = services[svc_name].get("container_name")
                    if not container_name:
                        container_name = f"{project_id}-{svc_name}-1"

                    from ldm_core.docker_service import DockerService

                    if not DockerService.is_running(container_name):
                        if not self.manager.check_port("127.0.0.1", host_port):
                            UI.die(
                                f"Port conflict detected: Port {host_port} is already in use on the host "
                                f"and is required by service '{svc_name}' in your compose configuration.\n"
                                f"Please stop the service currently using port {host_port} before starting LDM."
                            )
            except SystemExit:
                raise
            except Exception as e:
                UI.debug(
                    f"Failed to check port collisions from docker-compose.yml: {e}"
                )

        cmd = [*compose_base, "up", "-d", "--remove-orphans"]
        if rebuild:
            cmd.append("--build")

        if show_summary:
            tag_val = project_meta.get("tag")
            db_val = project_meta.get("db_type", "postgresql")
            port_val = project_meta.get("port", 8080)

            # LDM-413: Hide port 8080 if SSL is active
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
            # Save successfully run Liferay/PostgreSQL versions to metadata to prevent unsafe downgrades
            tag_val = project_meta.get("tag")
            project_meta["last_run_liferay_version"] = tag_val
            db_type_val = project_meta.get("db_type", "postgresql")
            if db_type_val in ["postgresql", "postgres"]:
                from ldm_core.utils import resolve_dependency_version

                current_pg = resolve_dependency_version(tag_val, "postgresql") or "16"
                project_meta["last_run_postgres_version"] = current_pg
            self.manager.write_meta(paths["root"], project_meta)

            if self.manager.verbose and total_start:
                duration_str = UI.format_duration(time.time() - total_start)
                UI.debug(f"Time to orchestration start: {duration_str}")

            db_type = project_meta.get("db_type", "postgresql")
            deps = []
            if db_type != "hypersonic":
                deps.append("db")

            if deps:
                UI.detail(
                    f"Starting dependencies: {UI.CYAN}{', '.join(deps)}{UI.COLOR_OFF}..."
                )
                self.manager.run_command(
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
                        status = self.manager.get_container_status(
                            f"{project_id}-{dep}-1"
                        )
                        if status in {"healthy", "running"}:
                            time.sleep(2)
                            break
                        if status == "exited":
                            UI.error(f"Dependency '{dep}' exited unexpectedly.")
                            return False
                        time.sleep(2)

            # LDM-381: Reclaim volume permissions on Linux before starting
            # This prevents host-side 'Permission denied' errors when Liferay container
            # (running as UID 1000) takes ownership of bind-mounted folders.
            if platform.system().lower() == "linux":
                from ldm_core.utils import reclaim_volume_permissions

                for p_key in ["deploy", "logs", "osgi", "files"]:
                    if p_key in paths:
                        reclaim_volume_permissions(paths[p_key])

            self.manager.run_command(
                cmd, cwd=str(paths["root"]), capture_output=not follow
            )

            if follow:
                self.manager.run_command(
                    [*compose_base, "logs", "-f"], cwd=str(paths["root"])
                )
                return True
            if not no_wait:
                # LDM-388: Defensive timeout handling (Fallback to 900s)
                timeout_val = getattr(self.manager.args, "timeout", 900)
                if timeout_val is None:
                    timeout_val = 900

                return self._wait_for_ready(
                    project_meta,
                    host_name,
                    total_start,
                    timeout=timeout_val,
                )

        if no_wait:
            if str(project_meta.get("share", "false")).lower() == "true":
                share_subdomain = project_meta.get(
                    "share_subdomain"
                ) or project_meta.get("project_name")
                share_port = project_meta.get("port", 8080)
                share_provider = project_meta.get("share_provider") or "lfr-tunnel"
                self.manager.share.cmd_start(
                    project_id=project_meta.get("project_name"),
                    subdomain=share_subdomain,
                    ports=str(share_port),
                    provider=share_provider,
                    image=project_meta.get("share_image"),
                    inspector=str(project_meta.get("share_inspector", "false")).lower()
                    == "true",
                )
            UI.success(f"Project '{project_id}' started in background.")
            return True

        return True

    def cmd_stop(self, project_id=None, service=None, all_projects=False):
        """Stops project containers."""
        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets:
            UI.info("No projects found to stop.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.info(f"Stopping project: {root.name}...")
            cmd = [*compose_base, "stop"]
            if service:
                cmd.append(service)
            self.manager.run_command(cmd, capture_output=capture, cwd=str(root))

    def cmd_restart(self, project_id=None, service=None, all_projects=False):
        """Restarts project containers."""
        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets:
            UI.info("No projects found to restart.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.info(f"Restarting project: {root.name}...")
            cmd = [*compose_base, "restart"]
            if service:
                cmd.append(service)
            self.manager.run_command(cmd, capture_output=capture, cwd=str(root))

    def cmd_down(
        self,
        project_id=None,
        service=None,
        all_projects=False,
        delete=False,
        infra=False,
        clean_hosts=False,
    ):
        """Tears down project containers and volumes."""
        is_dry_run = getattr(self.manager, "dry_run", False)

        if infra:
            if is_dry_run:
                UI.info(
                    f"{UI.BYELLOW}[Dry Run] Would tear down global Traefik infrastructure.{UI.COLOR_OFF}"
                )
            else:
                self.manager.infra.cmd_infra_down()

        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets and not infra:
            UI.info("No projects found to tear down.")
            return

        for root in targets:
            UI.warning(f"Tearing down stack: {root.name}")

            # DNS Cleanup (if requested)
            if clean_hosts:
                meta = self.manager.read_meta(root)
                host = meta.get("host_name")
                if host and host != "localhost":
                    # Collect subdomains as well (from extensions)
                    unresolved, _non_local = self.manager.validate_project_dns(root)[1:]
                    # We remove the primary host and any unresolved subdomains
                    to_clean = [host, *unresolved]
                    if is_dry_run:
                        UI.info(
                            f"  {UI.BYELLOW}- [Dry Run] Would remove hosts entries: {', '.join(to_clean)}{UI.COLOR_OFF}"
                        )
                    else:
                        self.manager._remove_hosts_entries(hostnames=to_clean)

            if is_dry_run:
                UI.info(
                    f"  {UI.BYELLOW}- [Dry Run] Would run docker compose down -v --remove-orphans in {root.name}{UI.COLOR_OFF}"
                )
            else:
                compose_base = get_compose_cmd()
                capture = not (UI.INFO_MODE or UI.VERBOSE)
                cmd = [*compose_base, "down", "-v", "--remove-orphans"]
                if (root / "docker-compose.yml").exists():
                    self.manager.run_command(cmd, capture_output=capture, cwd=str(root))
                else:
                    UI.debug(
                        f"No docker-compose.yml found in {root}. Skipping docker-compose down."
                    )

            if delete:
                if is_dry_run:
                    UI.warning(
                        f"  {UI.BYELLOW}- [Dry Run] Would unregister project {root.name} and permanently delete directory {root}{UI.COLOR_OFF}"
                    )
                else:
                    UI.warning(f"Permanently deleting project directory: {root.name}")
                    self.manager.unregister_project(root.name)
                    self.manager.safe_rmtree(root)

    def cmd_browser(self, project_id=None):
        """Opens the project's URL in the default browser."""
        from ldm_core.utils import open_browser

        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        meta = self.manager.read_meta(root)
        host_name = meta.get("host_name", "localhost")
        ssl_enabled = str(meta.get("ssl", "false")).lower() == "true"
        port = meta.get("port", 8080)

        protocol = "https" if ssl_enabled else "http"
        url = f"{protocol}://{host_name}"
        if not ssl_enabled and port != 80:
            url += f":{port}"

        UI.info(f"Opening browser: {UI.CYAN}{url}{UI.COLOR_OFF}")
        open_browser(url)

    def cmd_renew_ssl(self, project_id=None, all_projects=False):
        """Forces renewal of SSL certificates for projects."""
        targets = []
        if all_projects:
            targets = [
                {"path": r["path"], "meta": self.manager.read_meta(r["path"])}
                for r in self.manager.find_dxp_roots()
            ]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                meta = self.manager.read_meta(root)
                targets.append({"path": root, "meta": meta})

        if not targets:
            UI.info("No projects found for SSL renewal.")
            return

        actual_home = get_actual_home()
        cert_dir = actual_home / "liferay-docker-certs"

        for target in targets:
            host_name = target["meta"].get("host_name")
            if host_name and host_name != "localhost":
                UI.info(f"Renewing SSL for {UI.CYAN}{host_name}{UI.COLOR_OFF}...")
                # Delete existing certs to force renewal
                for f in [f"{host_name}.pem", f"{host_name}-key.pem"]:
                    if (cert_dir / f).exists():
                        (cert_dir / f).unlink()
                self.manager.infra.setup_ssl(cert_dir, host_name)

        UI.success(
            "SSL renewal complete. Changes will be detected by Traefik automatically."
        )

    def cmd_reset(self, project_id=None, target="all"):
        """Wipes local state (data, logs, osgi/state) for a project."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return None

        is_dry_run = getattr(self.manager, "dry_run", False)
        if is_dry_run:
            UI.warning(
                f"[Dry Run] Resetting {UI.BOLD}{root.name}{UI.COLOR_OFF} ({target})..."
            )
            meta = self.manager.read_meta(root)
            c_name = meta.get("container_name") or root.name
            if target == "all":
                UI.info(
                    f"  {UI.BYELLOW}- Would stop/tear down project stack (down).{UI.COLOR_OFF}"
                )
            else:
                UI.info(
                    f"  {UI.BYELLOW}- Would stop project stack if running.{UI.COLOR_OFF}"
                )

            targets = ["data", "logs", "state"] if target == "all" else [target]
            for t in targets:
                if t in ["data", "state"]:
                    volume_name = f"{c_name}-{t}"
                    UI.info(
                        f"  {UI.BYELLOW}- Would delete Docker named volume: {volume_name}{UI.COLOR_OFF}"
                    )
                paths = self.manager.setup_paths(root)
                path = paths.get(t)
                if path and path.exists():
                    UI.info(
                        f"  {UI.BYELLOW}- Would delete host directory: {path.relative_to(root) if path.is_relative_to(root) else path}{UI.COLOR_OFF}"
                    )
            UI.success(
                f"[Dry Run] Project {root.name} reset completed (no changes made)."
            )
            return True

        UI.warning(f"Resetting {UI.BOLD}{root.name}{UI.COLOR_OFF} ({target})...")

        meta = self.manager.read_meta(root)
        c_name = meta.get("container_name") or root.name
        from ldm_core.docker_service import DockerService

        is_running = DockerService.is_running(c_name)

        # LDM-388: If target is 'all', we must 'down -v' to destroy anonymous DB volumes
        if target == "all":
            self.cmd_down(root.name, delete=False)
        elif is_running:
            self.cmd_stop(root.name)

        # 2. Wipe directories
        paths = self.manager.setup_paths(root)
        targets = []
        targets = ["data", "logs", "state"] if target == "all" else [target]

        for t in targets:
            path = paths.get(t)

            # LDM-369: Handle Named Volumes (Hybrid Mount Strategy)
            if t in ["data", "state"]:
                volume_name = f"{c_name}-{t}"
                # Check if this volume exists in Docker
                try:
                    res = self.manager.run_command(
                        ["docker", "volume", "ls", "-q", "-f", f"name=^{volume_name}$"],
                        check=False,
                    )
                    if res.strip():
                        UI.detail(
                            f"  - Removing Docker volume {UI.CYAN}{volume_name}{UI.COLOR_OFF}..."
                        )
                        self.manager.run_command(
                            ["docker", "volume", "rm", "-f", volume_name], check=False
                        )
                except Exception:
                    pass

            if path and path.exists():
                UI.detail(f"  - Cleaning {t} (host)...")
                shutil.rmtree(path)
                path.mkdir(parents=True, exist_ok=True)

        UI.success(f"Project {root.name} reset successful.")
        return None

    def cmd_gogo(self, project_id=None):
        """Connects to the OSGi Gogo shell."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        meta = self.manager.read_meta(root)
        port = meta.get("gogo_port")

        if not port or port == "None":
            UI.die(
                "Gogo shell is not exposed. Run 'ldm run --gogo-port <port>' to enable it."
            )

        UI.info(f"Connecting to Gogo shell on localhost:{port}...")
        try:
            subprocess.run(["telnet", "localhost", str(port)])
        except FileNotFoundError:
            UI.error("telnet not found. Run: telnet localhost " + str(port))
        except KeyboardInterrupt:
            pass

    def _cmd_logs_instance(
        self,
        project_id=None,
        service=None,
        instance=1,
        follow=False,
        tail="100",
        timestamps=False,
        since=None,
        until=None,
        grep=None,
        grep_i=False,
        grep_v=False,
        level=None,
    ):
        """Stream logs from a single scaled replica via 'docker logs'.

        Container name is resolved using the pattern stored in project metadata
        (written by cmd_scale). Falls back to the Docker Compose v2 naming
        convention: {project}-{service}-{index}.
        """
        root = self.manager.detect_project_path(project_id)
        if not root:
            UI.die("Project not found.")

        meta = self.manager.read_meta(root)
        from ldm_core.utils import sanitize_id

        project_name = sanitize_id(meta.get("container_name") or root.name)

        # Default service to 'liferay' when not specified
        svc = (
            service[0]
            if isinstance(service, list) and service
            else (service or "liferay")
        )

        # Validate instance index against stored scale
        scale_key = f"scale_{svc}"
        max_instances = int(meta.get(scale_key, 1))
        if instance < 1 or instance > max_instances:
            if max_instances == 1:
                UI.error(
                    f"Service '{svc}' is not scaled (only 1 instance). "
                    f"Use 'ldm logs' without --instance to view its logs."
                )
            else:
                UI.error(
                    f"Invalid instance index {instance} for service '{svc}'. "
                    f"Valid range: 1–{max_instances} (current scale={max_instances})."
                )
            return

        # Fast path: use pattern stored in metadata by cmd_scale
        pattern_key = f"container_name_pattern_{svc}"
        pattern = meta.get(pattern_key)
        if pattern:
            container_name = pattern.replace("{index}", str(instance))
        else:
            # Fallback: Docker Compose v2 standard naming convention
            container_name = f"{project_name}-{svc}-{instance}"

        # Confirm the container exists
        check = self.manager.run_command(
            ["docker", "ps", "-a", "-q", "-f", f"name=^{container_name}$"],
            check=False,
        )
        if not check:
            UI.error(
                f"Container '{container_name}' not found. "
                f"Is '{project_name}' running with {max_instances} replica(s)?"
            )
            return

        UI.info(
            f"Streaming logs for {UI.CYAN}{container_name}{UI.COLOR_OFF} "
            f"(instance {instance} of {max_instances})..."
        )

        cmd = ["docker", "logs"]
        if follow:
            cmd.append("-f")
        if tail:
            cmd.extend(["--tail", str(tail)])
        if timestamps:
            cmd.append("-t")
        if since:
            cmd.extend(["--since", str(since)])
        if until:
            cmd.extend(["--until", str(until)])
        cmd.append(container_name)

        self._run_log_command(
            cmd,
            grep=grep,
            grep_i=grep_i,
            grep_v=grep_v,
            level=level,
            follow=follow,
        )

    def _run_log_command(
        self,
        cmd,
        env=None,
        cwd=None,
        grep=None,
        grep_i=False,
        grep_v=False,
        level=None,
        follow=False,
    ):
        """Runs the log command, streaming, filtering, and performing troubleshooting diagnostics."""
        if not grep and not level and not follow:
            self.manager.run_command(
                cmd, env=env, cwd=cwd, capture_output=False, check=False
            )
            return

        import os
        import re
        import shutil
        import subprocess
        import sys

        from ldm_core.utils import check_troubleshooting_signatures

        seen_troubleshooting = set()

        # Build regex pattern if grep is specified
        pattern = None
        if grep:
            flags = re.IGNORECASE if grep_i else 0
            try:
                pattern = re.compile(grep, flags)
            except re.error as e:
                UI.die(f"Invalid grep regular expression: {e}")

        # Severity level configuration
        SEVERITY_LEVELS = {
            "DEBUG": 10,
            "INFO": 20,
            "WARN": 30,
            "WARNING": 30,
            "ERROR": 40,
            "FATAL": 50,
        }

        target_severity = None
        if level:
            norm_level = level.upper()
            target_severity = SEVERITY_LEVELS.get(norm_level)
            if target_severity is None:
                UI.die(f"Invalid log level: {level}")

        LEVEL_PATTERNS = {
            "FATAL": re.compile(r"\bFATAL\b|\[FATAL\]"),
            "ERROR": re.compile(r"\bERROR\b|\[ERROR\]"),
            "WARN": re.compile(r"\bWARN(?:ING)?\b|\[WARN(?:ING)?\]"),
            "INFO": re.compile(r"\bINFO\b|\[INFO\]"),
            "DEBUG": re.compile(r"\bDEBUG\b|\[DEBUG\]"),
        }

        def get_line_level(line):
            for lvl in ["FATAL", "ERROR", "WARN", "INFO", "DEBUG"]:
                if LEVEL_PATTERNS[lvl].search(line):
                    return lvl
            return None

        # Resolve path to command executable (Bandit B607)
        if isinstance(cmd, list) and len(cmd) > 0:
            executable = shutil.which(cmd[0])
            if executable:
                cmd[0] = executable

        display_cmd = UI.redact(" ".join(cmd) if isinstance(cmd, list) else cmd)
        if self.manager.verbose:
            UI.debug(f"Executing log command: {display_cmd}")

        if getattr(self.manager, "dry_run", False):
            UI.info(
                f"{UI.BYELLOW}[DRY RUN] Would execute log command:{UI.COLOR_OFF} {display_cmd}"
            )
            return

        run_env = os.environ.copy() if env is None else env.copy()
        run_env["DOCKER_CLI_HINTS"] = "false"
        if "DOCKER_API_VERSION" not in run_env:
            run_env["DOCKER_API_VERSION"] = "1.44"

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                env=run_env,
                cwd=cwd,
                bufsize=1,
            )

            # Default print_subsequent state.
            # If level filtering is active, default to False to hide startup noise.
            print_subsequent = level is None

            try:
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        stripped_line = line.rstrip("\r\n")
                        clean_line = strip_ansi(stripped_line)

                        # 1. Level Filter evaluation
                        if target_severity is not None:
                            line_level = get_line_level(clean_line)
                            if line_level is not None:
                                level_severity = SEVERITY_LEVELS[line_level]
                                print_subsequent = level_severity >= target_severity
                                match_level = print_subsequent
                            else:
                                match_level = print_subsequent
                        else:
                            match_level = True

                        # 2. Grep Filter evaluation
                        if match_level:
                            if pattern is not None:
                                match_grep = bool(pattern.search(clean_line))
                                if grep_v:
                                    match_grep = not match_grep
                            else:
                                match_grep = True
                        else:
                            match_grep = False

                        advice = (
                            check_troubleshooting_signatures(clean_line)
                            if follow
                            else None
                        )
                        if advice and advice not in seen_troubleshooting:
                            seen_troubleshooting.add(advice)
                            print(
                                f"\n{UI.BYELLOW}⚠️  LDM TROUBLESHOOTING ADVICE:{UI.COLOR_OFF}"
                            )
                            print(f"👉 {UI.BWHITE}{advice}{UI.COLOR_OFF}\n")
                            sys.stdout.flush()

                        if match_grep:
                            print(stripped_line)
                            sys.stdout.flush()
            finally:
                if process.stdout:
                    process.stdout.close()
            process.wait()
        except KeyboardInterrupt:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

    def cmd_logs(
        self,
        project_id=None,
        service=None,
        all_projects=False,
        infra=False,
        follow=False,
        no_wait=False,
        tail="100",
        timestamps=False,
        since=None,
        until=None,
        instance=None,
        grep=None,
        grep_i=False,
        grep_v=False,
        level=None,
    ):
        """Shows logs for a project or global infrastructure."""
        if instance is not None:
            self._cmd_logs_instance(
                project_id=project_id,
                service=service,
                instance=instance,
                follow=follow,
                tail=tail,
                timestamps=timestamps,
                since=since,
                until=until,
                grep=grep,
                grep_i=grep_i,
                grep_v=grep_v,
                level=level,
            )
            return

        if infra:
            UI.info("Showing infrastructure logs...")
            containers = []
            if not service or "proxy" in service:
                containers.append("liferay-proxy-global")
            if not service or "es" in service:
                containers.append("liferay-search-global")

            for container in containers:
                self.manager.run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{container}$"]
                )

            infra_compose = self.manager.get_resource_path("infra-compose.yml")
            if not infra_compose:
                UI.die("Infrastructure compose file 'infra-compose.yml' not found.")

            cmd = [*get_compose_cmd(), "-f", str(infra_compose), "logs"]
            if follow:
                cmd.append("-f")

            if tail:
                cmd.extend(["--tail", str(tail)])

            if timestamps:
                cmd.append("-t")

            if since:
                cmd.extend(["--since", str(since)])

            if until:
                cmd.extend(["--until", str(until)])

            env = self.manager.infra._get_infra_env()
            self._run_log_command(
                cmd,
                env=env,
                grep=grep,
                grep_i=grep_i,
                grep_v=grep_v,
                level=level,
                follow=follow,
            )
        else:
            targets = []
            if all_projects:
                targets = [r["path"] for r in self.manager.find_dxp_roots()]
            else:
                root = self.manager.detect_project_path(project_id)
                if root:
                    targets = [root]

            if not targets:
                UI.info("No running projects found to show logs.")
                return

            for root in targets:
                if self.manager.verbose:
                    UI.debug(f"Processing logs for project: {root.name} in {root}")

                meta = self.manager.read_meta(root)
                c_name = meta.get("container_name") or root.name
                target_service = (
                    service if service and not isinstance(service, list) else "liferay"
                )

                # LDM-381: Resolve the actual container name using labels
                actual_container = self.manager.resolve_container(
                    root.name, target_service
                )

                # Check if it exists
                check_cmd = [
                    "docker",
                    "ps",
                    "-a",
                    "-q",
                    "-f",
                    f"name=^{actual_container}$",
                ]
                if not self.manager.run_command(check_cmd, check=False):
                    if no_wait:
                        UI.error(
                            f"Service '{target_service}' in project '{root.name}' does not exist. Skipping."
                        )
                        continue

                    UI.info(
                        f"Waiting for container {UI.CYAN}{root.name}{UI.COLOR_OFF} (service: {target_service})..."
                    )
                    start_wait = time.time()
                    found = False
                    while time.time() - start_wait < 60:
                        elapsed = int(time.time() - start_wait)
                        if elapsed > 0 and elapsed % 10 == 0:
                            UI.info(
                                f"  ... still waiting for '{root.name}' ({elapsed}s)"
                            )

                        # Re-resolve in case it was created during wait
                        actual_container = self.manager.resolve_container(
                            root.name, target_service
                        )
                        if self.manager.run_command(check_cmd, check=False):
                            found = True
                            break
                        time.sleep(2)

                    if not found:
                        UI.error(f"Container '{root.name}' did not appear within 60s.")
                        continue

                if follow:
                    log_dir = root / "logs"
                    if not log_dir.exists():
                        if no_wait:
                            UI.error(
                                f"Logs directory missing in {root.name}. Skipping."
                            )
                            continue

                        UI.info(f"Waiting for logs directory in {root.name}...")
                        start_wait = time.time()
                        while not log_dir.exists() and time.time() - start_wait < 30:
                            time.sleep(1)

                cmd = [*get_compose_cmd(), "logs"]
                if follow:
                    cmd.append("-f")

                if tail:
                    cmd.extend(["--tail", str(tail)])

                if timestamps:
                    cmd.append("-t")

                if since:
                    cmd.extend(["--since", str(since)])

                if until:
                    cmd.extend(["--until", str(until)])

                if service:
                    if isinstance(service, list):
                        cmd.extend(service)
                    else:
                        cmd.append(service)
                self._run_log_command(
                    cmd,
                    cwd=str(root),
                    grep=grep,
                    grep_i=grep_i,
                    grep_v=grep_v,
                    level=level,
                    follow=follow,
                )

    def cmd_deploy(self, project_id=None, targets=None, service=None):
        """Deploys a project, specific services, or individual artifacts."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        paths, meta = self.manager.setup_paths(root), self.manager.read_meta(root)

        # Normalize targets (legacy support for service parameter)
        if service and not targets:
            targets = [service]
        elif not targets:
            targets = []

        if not targets:
            # Full stack sync
            self.sync_stack(
                paths, meta, rebuild=getattr(self.manager.args, "rebuild", False)
            )
            return

        # Handle specific targets (services or files)
        from ldm_core.utils import atomic_copy

        services_to_up = set()
        for t in targets:
            t_path = Path(t)
            if t_path.exists() and t_path.is_file():
                ext = t_path.suffix.lower()
                if ext in [".jar", ".war"]:
                    dest = paths["modules"] / t_path.name
                    UI.detail(f"Syncing Module: {t_path.name}")
                    atomic_copy(t_path, dest)
                elif ext == ".zip":
                    # Potentially a CX or Fragment
                    from ldm_core.handlers.workspace import WorkspaceService

                    handler = WorkspaceService(self.manager)
                    handler._sync_cx_artifact(t_path, paths)
                else:
                    UI.warning(f"Unsupported file type for deployment: {t}")
            else:
                # Treat as service name
                services_to_up.add(t)

        if services_to_up:
            for svc in sorted(services_to_up):
                UI.info(f"Deploying service '{svc}'...")
                self.manager.run_command(
                    [*get_compose_cmd(), "up", "-d", svc],
                    capture_output=False,
                    cwd=str(root),
                )
        else:
            UI.success("Artifact deployment complete.")

    def cmd_shell(self, project_id=None, service="liferay"):
        """Enters a project container via bash."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        service_name = service or "liferay"

        # LDM-381: Resolve the actual container name using labels
        target_container = self.manager.resolve_container(root.name, service_name)

        UI.info(f"Entering container: {target_container}")
        try:
            subprocess.run(["docker", "exec", "-it", target_container, "/bin/bash"])
        except KeyboardInterrupt:
            pass

    def cmd_scale(self, project_id, scale_args, no_run=False):
        """Scales project services."""
        project_path = self.manager.detect_project_path(project_id)
        if not project_path:
            UI.die("Project not found.")

        meta = self.manager.read_meta(project_path)
        from ldm_core.utils import sanitize_id

        project_name = sanitize_id(meta.get("container_name") or project_path.name)

        for arg in scale_args:
            if "=" not in arg:
                UI.error(f"Invalid scale argument: {arg}. Expected service=number")
                continue
            service, count = arg.split("=", 1)
            if not count.isdigit():
                UI.error(f"Invalid scale count for {service}: {count}")
                continue
            meta[f"scale_{service}"] = count
            # Store the standard naming pattern so future lookups avoid docker ps.
            # Docker Compose v2 convention: {compose_project}-{service}-{index}
            meta[f"container_name_pattern_{service}"] = (
                f"{project_name}-{service}-{{index}}"
            )

        self.manager.write_meta(project_path, meta)
        UI.success(f"Updated scale factors for project {project_path.name}")

        if not no_run:
            # Trigger regeneration and restart (pass is_restart=True to bypass running check)
            self.cmd_run(project_id, is_restart=True)

    def cmd_migrate_search(self, project_id=None):
        """Migrates a project from Sidecar to Global Elasticsearch."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return

        p_id = root.name
        paths = self.manager.setup_paths(p_id)

        # 1. Ensure Liferay is NOT running
        is_running = self.manager.run_command(
            ["docker", "ps", "-q", "-f", f"name=^{p_id}$"], check=False
        )
        if is_running:
            UI.die(
                f"Project '{p_id}' is currently running. Please stop it first with: ldm stop {p_id}"
            )

        UI.heading(f"Migrating '{p_id}' to Global Search")

        # 2. Check if Global Search is running
        search_running = self.manager.run_command(
            ["docker", "ps", "-q", "-f", "name=^liferay-search-global$"], check=False
        )
        if not search_running:
            if (
                UI.ask(
                    "Global Search container is not running. Start it now?", "Y"
                ).upper()
                == "Y"
            ):
                self.manager.infra.setup_global_search()
            else:
                UI.die("Migration aborted. Global Search is required.")

        # 3. Clean up internal indices
        data_dir = paths["data"]
        indices_found = False
        for es_dir in ["elasticsearch7", "elasticsearch8"]:
            target = data_dir / es_dir
            if target.exists():
                UI.detail(f"Removing internal index directory: {target}")
                shutil.rmtree(target)
                indices_found = True

        if not indices_found:
            UI.detail("No internal sidecar indices found. (Already clean?)")

        # 4. Sync configuration
        UI.detail("Applying Global Search configurations...")
        # We force use_shared_search=True in meta
        project_meta = self.manager.read_meta(root)
        project_meta["use_shared_search"] = "true"
        self.manager.write_meta(root, project_meta)

        # sync_common_assets will now find the global search running and copy the configs
        self.manager.config.sync_common_assets(paths)

        UI.success(
            f"Migration complete! Project '{p_id}' is now configured for Global Search."
        )

        if not self.manager.non_interactive:
            if UI.ask("Restart project now?", "Y").upper() == "Y":
                self.cmd_run(project_id)

    def cmd_reindex(self, project_id=None):
        """Triggers search reindexing (immediately if running, otherwise on next boot)."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return

        from ldm_core.docker_service import DockerService

        meta = self.manager.read_meta(root)
        container_name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or root.name
        )
        force_boot = getattr(self.manager.args, "force_boot", False)

        is_running = DockerService.is_running(container_name)

        if is_running and not force_boot:
            UI.info(
                f"Liferay container '{container_name}' is running. Triggering immediate runtime reindex..."
            )
            groovy_code = 'com.liferay.portal.kernel.search.IndexWriterHelperUtil.reindex(0, "reindex", [com.liferay.portal.kernel.util.PortalUtil.getDefaultCompanyId()] as long[], null)'
            command_list = [
                "sh",
                "-c",
                f"echo '{groovy_code}' | telnet localhost 11311",
            ]
            try:
                DockerService.exec(container_name, command_list, check=True)
                UI.success(
                    f"Successfully triggered immediate runtime reindex on '{container_name}'."
                )
                return
            except Exception as e:
                UI.warning(
                    f"Failed to execute immediate reindex via Gogo shell ({e}). Falling back to boot-time scheduling."
                )

        if self.flag_reindex(root):
            UI.success(
                f"Project '{root.name}' scheduled for search reindex on next boot."
            )
            if not self.manager.non_interactive:
                if UI.confirm("Do you want to restart the project now to apply?", "Y"):
                    self.cmd_run(root.name)
        else:
            UI.error(f"Failed to schedule reindex for project '{root.name}'.")
