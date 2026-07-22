import json
import shutil
import subprocess
from pathlib import Path

import yaml

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import (
    ProjectLock,
    get_actual_home,
    get_compose_cmd,
)


class OrchestrationService(BaseHandler):
    """Orchestration service for runtime operations."""

    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager

    def cmd_run(
        self,
        project_id=None,
        is_restart=False,
        no_up=None,
        browser=None,
        **kwargs,
    ):
        """Main entry point for starting or updating a project stack."""
        from ldm_core.pipelines.run import RunPipelineContext, create_run_pipeline

        pipeline = create_run_pipeline()
        context = RunPipelineContext(
            self.manager,
            project_id=project_id,
            is_restart=is_restart,
            no_up=no_up,
            browser=browser,
            **kwargs,
        )
        return pipeline.run(context)

    def cmd_start(self, project_id=None, service=None, all_projects=False):
        """Starts project containers."""
        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id, fatal=False)
            if not root:
                UI.die(
                    "Project not found or not initialized. Please use 'ldm run' to initialize and configure a new project."
                )
            targets = [root]

        if not targets:
            UI.detail("No projects found to start.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.detail(f"Starting project: {root.name}...")
            with ProjectLock(root):
                cmd = [*compose_base, "start"]
                if service:
                    cmd.append(service)
                self.manager.run_command(cmd, capture_output=capture, cwd=str(root))

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
            UI.detail("No projects found to stop.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.detail(f"Stopping project: {root.name}...")
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
            UI.detail("No projects found to restart.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.detail(f"Restarting project: {root.name}...")
            cmd = [*compose_base, "restart"]
            if service:
                cmd.append(service)
            self.manager.run_command(cmd, capture_output=capture, cwd=str(root))

    def cmd_down(  # noqa: C901, PLR0912, PLR0915
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
                UI.detail(
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
            UI.detail("No projects found to tear down.")
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
                        UI.detail(
                            f"  {UI.BYELLOW}- [Dry Run] Would remove hosts entries: {', '.join(to_clean)}{UI.COLOR_OFF}"
                        )
                    else:
                        self.manager._remove_hosts_entries(hostnames=to_clean)

            if is_dry_run:
                UI.detail(
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
                meta = self.manager.read_meta(root)
                if meta:
                    ldm_version = meta.get("ldm_version")
                    if ldm_version and self.manager.parse_version(ldm_version) >= (
                        2,
                        11,
                        75,
                    ):
                        pass
                    from ldm_core.utils import resolve_infrastructure_mode

                    db_mode = resolve_infrastructure_mode(
                        "database_mode", meta, self.manager.defaults
                    )
                    db_type = meta.get("db_type", "postgresql")

                    if db_mode == "shared" and db_type != "hypersonic":
                        from ldm_core.utils import sanitize_id

                        project_name = meta.get("project_name", root.name)
                        db_name = (
                            f"lportal_{sanitize_id(project_name).replace('-', '_')}"
                        )
                        global_db_container = (
                            "liferay-db-mysql-global"
                            if db_type in ["mysql", "mariadb"]
                            else "liferay-db-global"
                        )

                        if is_dry_run:
                            UI.detail(
                                f"  {UI.BYELLOW}- [Dry Run] Would drop database {db_name} from shared container {global_db_container}{UI.COLOR_OFF}"
                            )
                        else:
                            UI.detail(f"Dropping shared database schema: {db_name}")
                            drop_cmd = []
                            if db_type == "postgresql":
                                drop_cmd = [
                                    "docker",
                                    "exec",
                                    global_db_container,
                                    "dropdb",
                                    "-U",
                                    "liferay",
                                    "--if-exists",
                                    db_name,
                                ]
                            elif db_type in ["mysql", "mariadb"]:
                                drop_cmd = [
                                    "docker",
                                    "exec",
                                    global_db_container,
                                    "mysql",
                                    "-u",
                                    "root",
                                    "-pliferay",
                                    "-e",
                                    f"DROP DATABASE IF EXISTS {db_name};",
                                ]

                            if drop_cmd:
                                try:
                                    subprocess.run(
                                        drop_cmd, check=False, capture_output=True
                                    )
                                except Exception as e:
                                    UI.warning(
                                        f"Failed to drop shared database {db_name} (container might be offline): {e}"
                                    )

                if is_dry_run:
                    UI.warning(
                        f"  {UI.BYELLOW}- [Dry Run] Would unregister project {root.name} and permanently delete directory {root}{UI.COLOR_OFF}"
                    )
                else:
                    UI.warning(f"Permanently deleting project directory: {root.name}")

                    # Release the lock before attempting deletion to avoid WinError 32 on Windows
                    path_key = Path(root).resolve().as_posix()
                    if (
                        hasattr(self.manager, "_active_locks")
                        and path_key in self.manager._active_locks
                    ):
                        self.manager._active_locks[path_key].release()
                        del self.manager._active_locks[path_key]

                    self.manager.unregister_project(root.name)
                    self.manager.safe_rmtree(root)

    def cmd_deploy(self, project_id=None, targets=None, service=None):
        """Deploys a project, specific services, or individual artifacts."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        paths = self.manager.setup_paths(root)

        # Normalize targets (legacy support for service parameter)
        if service and not targets:
            targets = [service]
        elif not targets:
            targets = []

        if not targets:
            # Full stack sync
            self.cmd_run(
                project_id=project_id,
                rebuild=getattr(self.manager.args, "rebuild", False),
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
                UI.detail(f"Deploying service '{svc}'...")
                self.manager.run_command(
                    [*get_compose_cmd(), "up", "-d", svc],
                    capture_output=False,
                    cwd=str(root),
                )
        else:
            UI.success("Artifact deployment complete.")

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

        UI.detail(f"Opening browser: {UI.CYAN}{url}{UI.COLOR_OFF}")
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
            UI.detail("No projects found for SSL renewal.")
            return

        actual_home = get_actual_home()
        cert_dir = actual_home / "liferay-docker-certs"

        for target in targets:
            host_name = target["meta"].get("host_name")
            if host_name and host_name != "localhost":
                UI.detail(f"Renewing SSL for {UI.CYAN}{host_name}{UI.COLOR_OFF}...")
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
                UI.detail(
                    f"  {UI.BYELLOW}- Would stop/tear down project stack (down).{UI.COLOR_OFF}"
                )
            else:
                UI.detail(
                    f"  {UI.BYELLOW}- Would stop project stack if running.{UI.COLOR_OFF}"
                )

            targets = ["data", "logs", "state"] if target == "all" else [target]
            for t in targets:
                if t in ["data", "state"]:
                    volume_name = f"{c_name}-{t}"
                    UI.detail(
                        f"  {UI.BYELLOW}- Would delete Docker named volume: {volume_name}{UI.COLOR_OFF}"
                    )
                paths = self.manager.setup_paths(root)
                path = paths.get(t)
                if path and path.exists():
                    UI.detail(
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
                except Exception as e:
                    UI.detail(f"Warning removing docker volume {volume_name}: {e}")

            if path and path.exists():
                UI.detail(f"  - Cleaning {t} (host)...")
                shutil.rmtree(path)
                path.mkdir(parents=True, exist_ok=True)

        UI.success(f"Project {root.name} reset successful.")
        return None

    def _generate_keycloak_realm(self, project_root):
        """Dynamically generates the keycloak-realm.json to avoid tracking secrets in git."""

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

    def cmd_shell(self, project_id=None, service="liferay"):
        """Enters a project container via bash."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        service_name = service or "liferay"

        # LDM-381: Resolve the actual container name using labels
        target_container = self.manager.resolve_container(root.name, service_name)

        UI.detail(f"Entering container: {target_container}")
        try:
            subprocess.run(
                ["docker", "exec", "-it", target_container, "/bin/bash"], check=False
            )
        except KeyboardInterrupt:
            pass

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

        UI.detail(f"Connecting to Gogo shell on localhost:{port}...")
        try:
            subprocess.run(["telnet", "localhost", str(port)], check=False)
        except FileNotFoundError:
            UI.error("telnet not found. Run: telnet localhost " + str(port))
        except KeyboardInterrupt:
            pass

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
            UI.detail(f"Reseed {root.name} from {tag} ({db_type}/{search_mode})...")
            UI.detail(
                f"  {UI.BYELLOW}- [Dry Run] Would reset project stack (cmd_reset all).{UI.COLOR_OFF}"
            )
            UI.detail(
                f"  {UI.BYELLOW}- [Dry Run] Would fetch and extract new seed for tag: {tag}.{UI.COLOR_OFF}"
            )
            up_flag = getattr(self.manager.args, "up", False)
            if up_flag:
                UI.detail(
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
                    UI.detail(
                        f"Run {UI.CYAN}ldm run {root.name}{UI.COLOR_OFF} to start the project."
                    )
            else:
                UI.error("Reseed failed.")
        return None

    def _scan_for_expected_deployables(self, root_path):  # noqa: C901, PLR0912
        """Scans workspace deploy and client-extensions paths for deployable targets.

        Returns a dict of {bundle_symbolic_name_or_cx_id: expected_state}
        """
        import zipfile

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
                                unfolded_lines: list[str] = []
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
