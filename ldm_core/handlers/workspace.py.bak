import contextlib
import json
import os
import platform
import re
import shutil
import sys
import tarfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    pass

from ldm_core.constants import SCRIPT_DIR
from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import (
    atomic_copy,
    calculate_sha256,
    is_env_var_blacklisted,
    is_within_root,
    load_env_blacklist,
    safe_copy,
    safe_move,
)


class WorkspaceService(BaseHandler):
    """Service for workspace management (import, monitor, scanning)."""

    def __init__(self, manager):
        super().__init__(manager.args)
        self.manager = manager

    def _ensure_stopped(self, project_name, project_path):
        """Ensures that the project is not running, stopping it if requested or possible."""
        if not project_path.exists():
            return

        from ldm_core.docker_service import DockerService

        meta = self.manager.read_meta(project_path)
        c_name = meta.get("container_name") or project_name
        if DockerService.is_running(c_name):
            if getattr(self.manager.args, "leave_running", False):
                UI.die(
                    f"Project '{project_name}' is currently running. `--leave-running` was specified, so the project remains running. Aborting import."
                )
            elif (
                getattr(self.manager.args, "stop_running", False)
                or self.manager.non_interactive
            ):
                UI.info(f"Stopping running project '{project_name}' automatically...")
                self.manager.cmd_stop(project_id=project_name)
            elif UI.confirm(
                f"Project '{project_name}' is currently running. Stop it before continuing?",
                "Y",
            ):
                self.manager.cmd_stop(project_id=project_name)
            else:
                UI.die("Import aborted. Cannot modify a running project's foundation.")

    def cmd_init(self, project_id=None):
        """Scaffolds a project without starting it."""
        # Set the no_up flag on args to ensure it doesn't try to start
        self.manager.args.no_up = True

        UI.info(f"Initializing project shell: {project_id or 'interactively'}")
        self.manager.cmd_run(project_id)
        UI.success(
            "Initialization complete. You can now run 'ldm doctor' or 'ldm run'."
        )

    def _parse_client_extension_yaml(self, content):
        info: dict[str, Any] = {"type": None, "oauth_erc": None}
        type_match = re.search(r"^\s*type:\s*(\w+)", content, re.MULTILINE)
        if type_match:
            info["type"] = type_match.group(1).strip()
        oauth_match = re.search(
            r"^\s*oAuthApplicationHeadlessServer:\s*([^\s\n]+)", content, re.MULTILINE
        )
        if oauth_match:
            info["oauth_erc"] = oauth_match.group(1).strip()
        return info

    def _get_effective_blacklist(self, paths=None):
        blacklist = load_env_blacklist(SCRIPT_DIR / "common" / "env-blacklist.txt")
        if paths and paths.get("root"):
            proj_blacklist = load_env_blacklist(paths["root"] / "env-blacklist.txt")
            blacklist.extend(proj_blacklist)
        return sorted(set(blacklist))

    def _parse_client_extension_config_json(self, content):
        try:
            data = json.loads(content)
            prefix = "com.liferay.oauth2.provider.configuration.OAuth2ProviderApplicationHeadlessServerConfiguration~"
            for key, val in data.items():
                if key.startswith(prefix):
                    return key[len(prefix) :]
                if val.get("type") == "oAuthApplicationHeadlessServer":
                    return val.get("projectId") or val.get("projectName")
        except Exception:
            pass
        return None

    def _parse_lcp_json(self, content, context_name=None):
        info: dict[str, Any] = {
            "id": None,
            "kind": None,
            "deploy": True,  # Default to True if not specified
            "cpu": 1,
            "memory": 512,
            "ports": [],
            "loadBalancer": None,
            "readinessProbe": None,
            "livenessProbe": None,
            "oauth_erc": None,
            "env": {},
            "has_load_balancer": False,
        }
        try:
            data = json.loads(content)

            # Proactive Validation
            # Create a temporary file to use the validator (which expects a path)
            import tempfile
            from pathlib import Path

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as tf:
                tf.write(content)
                tf_path = Path(tf.name)

            try:
                status, ok, errors = self.manager.diagnostics.validate_lcp_json(tf_path)
                if not ok or ok == "warn":
                    header = "LCP.json Issue"
                    if context_name:
                        header = f"LCP.json Issue ({context_name})"
                    UI.warning(f"{header}: {status}")
                    if errors:
                        for err in errors:
                            UI.raw(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {err}")
            finally:
                if tf_path.exists():
                    tf_path.unlink()

            info["id"] = data.get("id")
            if data.get("kind"):
                info["kind"] = data["kind"].capitalize()
            for k in [
                "cpu",
                "memory",
                "ports",
                "deploy",
                "loadBalancer",
                "readinessProbe",
                "livenessProbe",
            ]:
                if k in data:
                    info[k] = data[k]
            info["has_load_balancer"] = "loadBalancer" in data or any(
                p.get("external")
                for p in cast(list[dict[str, Any]], info.get("ports", []))
            )
            env = data.get("env", {})
            info["env"] = env
            if "LIFERAY_BATCH_OAUTH_APP_ERC" in env:
                info["oauth_erc"] = env["LIFERAY_BATCH_OAUTH_APP_ERC"]
        except Exception:
            pass
        return info

    def _scan_extension_metadata(self, folder_path=None, zip_ref=None):
        info: dict[str, Any] = {
            "id": None,
            "type": None,
            "kind": None,
            "deploy": True,
            "cpu": 1,
            "memory": 512,
            "ports": [],
            "loadBalancer": None,
            "readinessProbe": None,
            "livenessProbe": None,
            "oauth_erc": None,
            "env": {},
            "has_load_balancer": False,
        }

        def merge_info(new_info):
            for k in info:
                if new_info.get(k) is not None:
                    if k == "has_load_balancer":
                        info[k] = info[k] or new_info[k]
                    elif k == "ports" and new_info[k]:
                        cast(list, info[k]).extend(new_info[k])
                    elif k == "loadBalancer" and new_info[k]:
                        if info[k] is None:
                            info[k] = {}
                        cast(dict, info[k]).update(new_info[k])
                    else:
                        info[k] = new_info[k]
            if new_info.get("env"):
                cast(dict, info["env"]).update(new_info["env"])

        if zip_ref:
            for f in zip_ref.namelist():
                name = Path(f).name
                if name in ["client-extension.yaml", "client-extension.yml"]:
                    merge_info(
                        self._parse_client_extension_yaml(
                            zip_ref.read(f).decode("utf-8")
                        )
                    )
                elif name == "LCP.json":
                    ctx = Path(zip_ref.filename).name
                    merge_info(
                        self._parse_lcp_json(
                            zip_ref.read(f).decode("utf-8"), context_name=ctx
                        )
                    )
                elif name == "client-extension-config.json" or f.endswith(
                    ".client-extension-config.json"
                ):
                    erc = self._parse_client_extension_config_json(
                        zip_ref.read(f).decode("utf-8")
                    )
                    if erc:
                        info["oauth_erc"] = erc
        elif folder_path:
            yaml_file = next(folder_path.glob("client-extension.y*ml"), None)
            if yaml_file:
                merge_info(self._parse_client_extension_yaml(yaml_file.read_text()))
            lcp_file = folder_path / "LCP.json"
            if lcp_file.exists():
                merge_info(
                    self._parse_lcp_json(
                        lcp_file.read_text(), context_name=folder_path.name
                    )
                )
            cfg_file = next(folder_path.glob("*client-extension-config.json"), None)
            if cfg_file:
                erc = self._parse_client_extension_config_json(cfg_file.read_text())
                if erc:
                    info["oauth_erc"] = erc
        return info

    def scan_client_extensions(self, root_dir, osgi_cx_dir, ce_build_dir):
        extensions: list[dict[str, Any]] = []
        if not root_dir.exists():
            return extensions

        # Paths based on Core Mandates:
        # root_dir / client-extensions -> Build Source of Truth
        # root_dir / osgi / client-extensions -> Liferay Auto-deploy (ZIPs)
        ce_source_truth = root_dir / "client-extensions"
        ce_source_truth.mkdir(parents=True, exist_ok=True)
        osgi_cx_dir.mkdir(parents=True, exist_ok=True)

        found_ids = set()

        # 1. Process ZIPs from the workspace build directory (ldm-cx-samples/client-extensions/*/dist/*.zip)
        for item in [i for i in ce_build_dir.iterdir() if i.suffix.lower() == ".zip"]:
            try:
                # Root project folder is the build context (Source of Truth)
                target_folder = ce_source_truth / item.stem
                root_zip_copy = ce_source_truth / item.name

                # Copy to root first to expand (if not already there)
                is_same = False
                try:
                    if item.resolve() == root_zip_copy.resolve() or os.path.samefile(
                        item, root_zip_copy
                    ):
                        is_same = True
                except Exception:
                    pass

                if not is_same:
                    safe_copy(item, root_zip_copy)

                with zipfile.ZipFile(root_zip_copy, "r") as zip_ref:
                    ext_info = self._scan_extension_metadata(zip_ref=zip_ref)
                    namelist = zip_ref.namelist()

                    if ext_info["type"] or any(
                        Path(f).name in ["client-extension.yaml", "LCP.json"]
                        for f in namelist
                    ):
                        is_service = (
                            any(Path(f).name == "Dockerfile" for f in namelist)
                            and ext_info.get("kind") != "Job"
                            and ext_info.get("deploy", True) is not False
                        )

                        if is_service:
                            if target_folder.exists():
                                self.manager.safe_rmtree(target_folder)
                            target_folder.mkdir(parents=True)
                            from ldm_core.utils import safe_extract

                            safe_extract(zip_ref, target_folder)

                        # Move the original ZIP from root to OSGI for Liferay's scanner
                        dest_zip = osgi_cx_dir / item.name
                        is_same_dest = False
                        try:
                            if (
                                root_zip_copy.resolve() == dest_zip.resolve()
                                or os.path.samefile(root_zip_copy, dest_zip)
                            ):
                                is_same_dest = True
                        except Exception:
                            pass

                        if not is_same_dest:
                            if dest_zip.exists():
                                os.remove(dest_zip)
                            safe_copy(root_zip_copy, dest_zip)

                        extensions.append(
                            {
                                "name": item.stem.lower().replace("_", "-"),
                                "id": ext_info.get("id") or item.stem,
                                "path": target_folder,  # Build from root/client-extensions/
                                "is_service": is_service,
                                "port": next(
                                    (
                                        p.get("port")
                                        for p in ext_info.get("ports", [])
                                        if p.get("external")
                                    ),
                                    80,
                                ),
                                **ext_info,
                            }
                        )
            except Exception as e:
                UI.error(f"Failed to process {item.name}: {e}")

        # 2. Process existing folders in the Source of Truth
        for item in [
            i
            for i in ce_source_truth.iterdir()
            if i.is_dir() and not i.name.startswith(".")
        ]:
            ext_info = self._scan_extension_metadata(folder_path=item)
            if ext_info["type"] or (item / "LCP.json").exists():
                found_ids.add(item.name)
                is_service = (item / "Dockerfile").exists() and ext_info.get(
                    "kind"
                ) != "Job"
                port = next(
                    (
                        p.get("port")
                        for p in ext_info.get("ports", [])
                        if p.get("external")
                    ),
                    80,
                )
                entry = {
                    "id": ext_info.get("id") or item.name,
                    "name": item.name.lower().replace("_", "-"),
                    "port": port,
                    "path": item,  # Already in source of truth
                    "is_service": is_service,
                    **ext_info,
                }
                existing = next((e for e in extensions if e["id"] == entry["id"]), None)
                if existing:
                    existing.update(entry)
                else:
                    extensions.append(entry)

        return extensions

    def scan_standalone_services(self, root_path):
        services: list[dict[str, Any]] = []
        services_dir = root_path / "services"
        if not services_dir.exists():
            return services
        for item in [
            i
            for i in services_dir.iterdir()
            if i.is_dir() and not i.name.startswith(".")
        ]:
            if (item / "LCP.json").exists() and (item / "Dockerfile").exists():
                ext_info = self._scan_extension_metadata(folder_path=item)
                port = next(
                    (
                        p.get("port")
                        for p in ext_info.get("ports", [])
                        if p.get("external")
                    ),
                    80,
                )
                services.append(
                    {
                        "id": ext_info.get("id") or item.name,
                        "name": item.name.lower().replace("_", "-"),
                        "path": item,
                        "port": port,
                        "is_standalone": True,
                        **ext_info,
                    }
                )
        return services

    def get_host_passthrough_env(self, paths=None, target_id=None):
        blacklist = self._get_effective_blacklist(paths)

        # 1. Global Strip-Forwarding (LDM_VAR=xxx -> VAR=xxx in ALL containers)
        global_pool = {
            k[4:]: v
            for k, v in os.environ.items()
            if k.upper().startswith("LDM_") and not is_env_var_blacklisted(k, blacklist)
        }

        # 2. Passthrough Prefixes (Preserve prefix, forward to ALL containers)
        # Default set covers Liferay Cloud and common AI providers
        passthrough_prefixes = [
            "LXC_",
            "COM_LIFERAY_LXC_",
            "OPENAI_",
            "GEMINI_",
            "ANTHROPIC_",
            "MISTRAL_",
        ]
        # Allow user to extend this list via host environment
        extra_prefixes = os.environ.get("LDM_FORWARD_PREFIXES")
        if extra_prefixes:
            passthrough_prefixes.extend(
                [p.strip() for p in extra_prefixes.split(",") if p.strip()]
            )

        global_pool.update(
            {
                k: v
                for k, v in os.environ.items()
                if any(k.upper().startswith(p.upper()) for p in passthrough_prefixes)
                and not is_env_var_blacklisted(k, blacklist)
            }
        )

        if not target_id:
            # Multi-service request (used for initialcompose generation)
            res = [f"{k}={v}" for k, v in global_pool.items()]
            exts = self.scan_client_extensions(
                paths["root"], paths["cx"], paths["ce_dir"]
            )
            services = self.scan_standalone_services(paths["root"])

            # Add un-stripped service/liferay vars to the comprehensive list
            for k, v in os.environ.items():
                if is_env_var_blacklisted(k, blacklist):
                    continue
                if k.upper().startswith("LIFERAY_") or any(
                    k.upper().startswith(e["id"].upper().replace("-", "_") + "_")
                    for e in exts + services
                ):
                    res.append(f"{k}={v}")
            return sorted(set(res))

        # Targeted service request: [SERVICE_ID]_VAR=xxx -> VAR=xxx
        prefix = target_id.upper().replace("-", "_") + "_"
        targeted = {
            k[len(prefix) :] if target_id.lower() != "liferay" else k: v
            for k, v in os.environ.items()
            if k.upper().startswith(prefix) and not is_env_var_blacklisted(k, blacklist)
        }
        return [f"{k}={v}" for k, v in {**global_pool, **targeted}.items()]

    def _hydrate_from_workspace(self, workspace_root, paths, overwrite=True):
        """Initial scan and sync of artifacts from workspace to project."""
        UI.info("Scanning workspace for built artifacts...")

        # 1. Sync Client Extensions (ZIPs)
        ce_dir = workspace_root / "client-extensions"
        if ce_dir.exists():
            # Look in root and standard dist folders
            for dist_zip in list(ce_dir.glob("*.zip")) + list(
                ce_dir.glob("*/dist/*.zip")
            ):
                self._sync_cx_artifact(dist_zip, paths, overwrite=overwrite)

        # 2. Sync Modules & Themes (JARs from build/libs)
        for folder in ["modules", "themes"]:
            base_dir = workspace_root / folder
            if base_dir.exists():
                for jar in base_dir.glob("**/build/libs/*.[jw]ar"):
                    # Check if it's a valid bundle (not sources/javadoc)
                    if not any(
                        x in jar.name.lower()
                        for x in ["-sources", "-javadoc", "-tests"]
                    ):
                        dest = paths["modules"] / jar.name
                        if not overwrite and dest.exists():
                            UI.detail(f"  - Skipping existing module: {jar.name}")
                            continue
                        atomic_copy(jar, dest)
                        UI.detail(f"  + Synced {folder.capitalize()[:-1]}: {jar.name}")

        # 3. Sync Fragments (ZIPs)
        frag_dir = workspace_root / "fragments"
        if frag_dir.exists():
            # Look in root and any nested zips
            for zip_file in list(frag_dir.glob("*.zip")) + list(
                frag_dir.glob("*/dist/*.zip")
            ):
                # Check if it's a fragment or a CX being miscategorized
                try:
                    with zipfile.ZipFile(zip_file, "r") as zip_ref:
                        if "liferay-deploy-fragments.json" in zip_ref.namelist():
                            dest = paths["deploy"] / zip_file.name
                            if not overwrite and dest.exists():
                                UI.detail(
                                    f"  - Skipping existing fragment: {zip_file.name}"
                                )
                                continue
                            atomic_copy(zip_file, dest)
                            UI.detail(f"  + Synced Fragment: {zip_file.name}")
                        else:
                            # If it's a ZIP in fragments but not a fragment, try syncing as CX
                            self._sync_cx_artifact(zip_file, paths, overwrite=overwrite)
                except Exception:
                    pass
        return True

    def _sync_cx_artifact(self, zip_path, paths, overwrite=True):
        """Internal helper for the mandatory 3-step CX sync sequence."""
        ce_source_truth = paths["root"] / "client-extensions"
        ce_source_truth.mkdir(parents=True, exist_ok=True)

        # Step 1: Copy ZIP to root client-extensions/
        root_zip_path = ce_source_truth / zip_path.name

        # In 'no-overwrite' mode, we check if the final destination exists
        dest_zip = paths["cx"] / zip_path.name
        if not overwrite and dest_zip.exists():
            UI.detail(f"  - Skipping existing CX: {zip_path.name}")
            return

        if zip_path.resolve() != root_zip_path.resolve():
            safe_copy(zip_path, root_zip_path)
        # Step 2: Expand ZIP in root for Docker builds
        try:
            with zipfile.ZipFile(root_zip_path, "r") as zip_ref:
                target_folder = ce_source_truth / zip_path.stem
                if target_folder.exists():
                    if not overwrite:
                        # If skipping, we don't clear/re-expand the build folder either
                        pass
                    else:
                        shutil.rmtree(target_folder)
                        target_folder.mkdir(parents=True)
                        from ldm_core.utils import safe_extract

                        safe_extract(zip_ref, target_folder)
                else:
                    target_folder.mkdir(parents=True)
                    from ldm_core.utils import safe_extract

                    safe_extract(zip_ref, target_folder)

                if overwrite or not dest_zip.exists():
                    UI.detail(f"  + Synced & Expanded CX: {zip_path.name}")
        except Exception as e:
            UI.error(f"  ! Failed to expand CX {zip_path.name}: {e}")

        # Step 3: Move original ZIP to osgi/client-extensions/ for Liferay
        if dest_zip.exists():
            if not overwrite:
                if root_zip_path.exists():
                    os.remove(root_zip_path)
                return
            os.remove(dest_zip)
        safe_move(str(root_zip_path), str(dest_zip))

    def _prompt_cloud_hydration(self, source_path, project_name=None):
        """Helper to prompt for and orchestrate Liferay Cloud data hydration."""
        from ldm_core.utils import is_lcp_workspace

        source = Path(source_path).resolve()
        is_cloud = is_lcp_workspace(source)

        hydrate_env = getattr(self.manager.args, "hydrate_from", None)

        # Automation Path: If --hydrate-from is provided, we skip prompts
        if is_cloud and hydrate_env:
            self._execute_cloud_hydration(hydrate_env, source_path, project_name)
            return

        # Interactive Path
        if is_cloud and not self.manager.non_interactive:
            UI.info("\n> Detected Liferay Cloud Workspace structure.")
            if UI.confirm(
                "Would you also like to pull the remote database and document library to complete the local replica?",
                "Y",
            ):
                default_env = self.manager.defaults.get("target_env", "prd")
                env_id = UI.ask(
                    "Which environment would you like to mirror (e.g., prd, uat)",
                    default_env,
                )
                if env_id:
                    self._execute_cloud_hydration(
                        env_id.strip(), source_path, project_name
                    )

    def _execute_cloud_hydration(self, env_id, source_path, project_name):
        """Internal helper to execute the cloud fetch/restore/sync sequence."""
        # Persist the chosen environment for future cloud operations
        project_path = self.manager.detect_project_path(project_name, for_init=True)
        if not project_path:
            return

        p_meta = self.manager.read_meta(project_path)
        p_meta["cloud_env_id"] = env_id
        self.manager.write_meta(project_path, p_meta)

        paths = self.manager.setup_paths(project_path)

        UI.info(f"Fetching backups from '{env_id}'...")
        old_download = getattr(self.manager.args, "download", False)
        old_restore = getattr(self.manager.args, "restore", False)
        old_sync_env = getattr(self.manager.args, "sync_env", False)
        old_env_id = getattr(self.manager.args, "env_id", None)
        old_project = getattr(self.manager.args, "project", None)

        if project_name:
            self.manager.args.project = project_name

        try:
            # Pass the original source path down to cloud fetch
            # so sync_env can find the LCP.json file
            self.manager.args.source_path = str(source_path)

            # 1. Sync Env Vars (Do this first so they are in place for the restoration boot)
            # LDM-423: Skip env sync if --no-env-sync is provided
            if not getattr(self.manager.args, "no_env_sync", False):
                self.manager.args.download = False
                self.manager.args.restore = False
                self.manager.args.sync_env = True
                self.manager.cloud.cmd_cloud_fetch()
            else:
                UI.info("  - Skipping environment variable sync (--no-env-sync).")

            # 2. Fetch Data & Restore
            # We set no_run=True to prevent cmd_restore from starting the stack early.
            # The outer cmd_import/cmd_init_from will handle the final startup.
            self.manager.args.download = True
            self.manager.args.restore = True
            self.manager.args.sync_env = False
            self.manager.args.env_id = env_id

            old_no_run = getattr(self.manager.args, "no_run", False)
            try:
                self.manager.args.no_run = True
                self.manager.cloud.cmd_cloud_fetch()
            finally:
                self.manager.args.no_run = old_no_run

        except SystemExit:
            UI.warning(
                "Cloud hydration could not be completed. Falling back to local runtime only."
            )
        finally:
            self.manager.args.download = old_download
            self.manager.args.restore = old_restore
            self.manager.args.sync_env = old_sync_env
            self.manager.args.env_id = old_env_id
            self.manager.args.project = old_project
            if hasattr(self.manager.args, "source_path"):
                delattr(self.manager.args, "source_path")

    def cmd_init_from(self, source_path):
        """Initialize project with a persistent link to a source workspace and start monitoring."""
        # 1. Perform a standard import (but we will keep the link)
        project_name = self.cmd_import(source_path, is_init_from=True)
        if project_name:
            self.manager.args.project = project_name

        # 2. Immediately start monitoring
        self.cmd_monitor(source_path)

    def _parse_github_repo(self, url: str) -> tuple[str, str] | None:
        if not url:
            return None
        url = url.strip().split("?")[0].split("#")[0]

        # Handle SSH format: git@github.com:owner/repo.git
        if url.startswith("git@github.com:"):
            path = url.split("git@github.com:", 1)[1]
            if path.endswith(".git"):
                path = path[:-4]
            subparts = [p for p in path.split("/") if p]
            if len(subparts) >= 2:
                return subparts[0], subparts[1]
            return None

        # Handle HTTP/HTTPS format: https://github.com/owner/repo or https://github.com/owner/repo/tree/master
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https") and parsed.netloc in (
                "github.com",
                "www.github.com",
            ):
                path = parsed.path
                if path.endswith(".git"):
                    path = path[:-4]
                subparts = [p for p in path.split("/") if p]
                if len(subparts) >= 2:
                    return subparts[0], subparts[1]
        except Exception:
            pass
        return None

    def cmd_import(self, source_path, is_init_from=False):
        is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
        if is_dry_run:
            project_name = getattr(self.manager.args, "project", None) or getattr(
                self.manager.args, "project_flag", None
            )
            if not project_name:
                parsed = self._parse_github_repo(source_path)
                if parsed:
                    project_name = parsed[1]
                else:
                    project_name = source_path.split("/")[-1]
                    if project_name.endswith(".git"):
                        project_name = project_name[:-4]
                    if project_name.endswith(".zip") or project_name.endswith(".ldmp"):
                        project_name = project_name.split(".")[0]

            if not project_name:
                project_name = "demo-project"

            UI.info(
                f"{UI.BYELLOW}[DRY RUN] Would import workspace:{UI.COLOR_OFF} {source_path} -> project: {project_name}"
            )

            project_path = self.manager.detect_project_path(project_name)
            project_meta = {
                "project_name": project_name,
                "container_name": project_name,
                "port": "8080",
                "ssl": "false",
                "host_name": "localhost",
                "tag": "2026.q1.4-lts",
                "db_type": "postgresql",
            }
            self.manager.write_meta(project_path, project_meta)
            return project_name

        # Remote URL check (http://, https://, git@)
        if source_path.startswith(("http://", "https://", "git@")):
            import subprocess

            import requests

            clean_url = source_path.split("?")[0].split("#")[0].lower()
            is_archive_url = any(
                clean_url.endswith(suffix)
                for suffix in [".zip", ".tgz", ".gz", ".tar", ".ldmp"]
            )

            if is_archive_url:
                temp_dir = (
                    Path.cwd()
                    / ".ldm_temp"
                    / f"download_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                )
                temp_dir.mkdir(parents=True, exist_ok=True)
                archive_name = (
                    source_path.split("?")[0].split("#")[0].split("/")[-1]
                    or "download.ldmp"
                )
                archive_name = Path(archive_name).name
                local_path = (temp_dir / archive_name).resolve()

                if not is_within_root(local_path, temp_dir):
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir)
                    UI.die("Security Violation: Invalid remote archive path.")

                UI.info(f"Downloading remote archive: {source_path}...")
                try:
                    response = requests.get(source_path, stream=True, timeout=30)
                    response.raise_for_status()
                    with open(local_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                except Exception as e:
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir)
                    UI.die(f"Failed to download remote archive: {e}")

                # Download signature if enabled
                verify_enabled = getattr(self.manager.args, "verify", True)
                if verify_enabled:
                    sha_url = source_path + ".sha256"
                    try:
                        sha_resp = requests.get(sha_url, timeout=10)
                        if sha_resp.status_code == 200:
                            sha_name = f"{local_path.name}.sha256"
                            sha_path = (temp_dir / sha_name).resolve()
                            if is_within_root(sha_path, temp_dir):
                                sha_path.write_text(sha_resp.text.strip())
                                UI.info("Downloaded checksum signature.")
                            else:
                                UI.warning(
                                    "Security Warning: Signature file containment check failed."
                                )
                    except Exception:
                        pass

                try:
                    return self.cmd_import(str(local_path), is_init_from=is_init_from)
                finally:
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir)
            else:
                # Git URL / GitHub Repo URL
                project_name = getattr(self.manager.args, "project", None) or getattr(
                    self.manager.args, "project_flag", None
                )
                parsed = self._parse_github_repo(source_path)
                github_token = os.environ.get("GITHUB_TOKEN")
                clone_only = getattr(self.manager.args, "clone_only", False)
                has_ldmp = False
                ldmp_asset = None
                sha_asset = None
                owner, repo = None, None

                if parsed and not clone_only:
                    import requests

                    owner, repo = parsed
                    headers = {}
                    if github_token:
                        headers["Authorization"] = f"token {github_token}"
                    api_url = (
                        f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
                    )
                    try:
                        api_resp = requests.get(api_url, headers=headers, timeout=20)
                        if api_resp.status_code == 200:
                            release_data = api_resp.json()
                            assets = release_data.get("assets", [])
                            for asset in assets:
                                name = asset.get("name", "")
                                if name.endswith(".ldmp"):
                                    ldmp_asset = asset
                                elif name.endswith(".ldmp.sha256"):
                                    sha_asset = asset
                            if ldmp_asset and sha_asset:
                                # Safety Check: If the remote package is empty/vanilla (e.g. created by headless CI),
                                # its size will be extremely small (typically <10KB). In this case, do not use it
                                # and fall back to standard cloning to ensure the user gets the workspace code.
                                asset_size = ldmp_asset.get("size", 0)
                                if asset_size > 10240:
                                    has_ldmp = True
                                else:
                                    UI.warning(
                                        f"Remote LDM package '{ldmp_asset.get('name')}' is too small ({asset_size} bytes) "
                                        "and appears to be empty/vanilla. Falling back to standard clone and build."
                                    )
                    except Exception as e:
                        UI.debug(f"GitHub Release API query failed: {e}")

                if has_ldmp:
                    # 1. Download and restore standalone LDMP package without cloning
                    if not project_name:
                        project_name = repo
                        if self.manager.non_interactive:
                            UI.info(f"Using default project name: {project_name}")
                        else:
                            project_name = UI.ask("Project Name", project_name)
                        self.manager.args.project = project_name

                    project_path = self.manager.detect_project_path(
                        project_name, for_init=True
                    )
                    self.manager.check_uncommitted_changes(project_path)
                    self._ensure_stopped(project_name, project_path)

                    temp_pkg_dir = (
                        Path.cwd()
                        / ".ldm_temp"
                        / f"package_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    )
                    temp_pkg_dir.mkdir(parents=True, exist_ok=True)

                    ldmp_name = Path(ldmp_asset["name"]).name
                    sha_name = Path(sha_asset["name"]).name

                    ldmp_path = (temp_pkg_dir / ldmp_name).resolve()
                    sha_path = (temp_pkg_dir / sha_name).resolve()

                    if not is_within_root(
                        ldmp_path, temp_pkg_dir
                    ) or not is_within_root(sha_path, temp_pkg_dir):
                        if temp_pkg_dir.exists():
                            shutil.rmtree(temp_pkg_dir)
                        UI.die("Security Violation: Invalid package asset name.")

                    UI.info(f"Downloading LDM package: {ldmp_name}...")
                    try:
                        headers_dl = {"Accept": "application/octet-stream"}
                        if github_token:
                            headers_dl["Authorization"] = f"token {github_token}"

                        dl_url = ldmp_asset["url"]
                        r = requests.get(
                            dl_url, headers=headers_dl, stream=True, timeout=60
                        )
                        r.raise_for_status()
                        with open(ldmp_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)

                        dl_sha_url = sha_asset["url"]
                        r_sha = requests.get(dl_sha_url, headers=headers_dl, timeout=20)
                        r_sha.raise_for_status()
                        sha_path.write_text(r_sha.text.strip())
                    except Exception as e:
                        if temp_pkg_dir.exists():
                            shutil.rmtree(temp_pkg_dir)
                        UI.die(f"Failed to download LDM Package assets: {e}")

                    # Checksum Verify
                    actual_sha = calculate_sha256(ldmp_path)
                    expected_sha = sha_path.read_text().strip().split()[0]

                    if actual_sha != expected_sha:
                        if temp_pkg_dir.exists():
                            shutil.rmtree(temp_pkg_dir)
                        UI.die("Security Violation: SHA-256 verification failed.")

                    UI.success("LDM package checksum verified successfully.")

                    temp_extract_dir = (
                        Path.cwd()
                        / ".ldm_temp"
                        / f"extract_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    )
                    temp_extract_dir.mkdir(parents=True, exist_ok=True)

                    UI.info("Extracting LDM package...")
                    try:
                        with tarfile.open(ldmp_path, "r:gz") as tar:
                            from ldm_core.utils import safe_extract

                            safe_extract(tar, temp_extract_dir)
                    except Exception as e:
                        if temp_pkg_dir.exists():
                            shutil.rmtree(temp_pkg_dir)
                        if temp_extract_dir.exists():
                            shutil.rmtree(temp_extract_dir)
                        UI.die(f"Failed to extract LDM package: {e}")

                    # Verify manifest meta
                    manifest_file = temp_extract_dir / "meta"
                    if not manifest_file.exists():
                        if temp_pkg_dir.exists():
                            shutil.rmtree(temp_pkg_dir)
                        if temp_extract_dir.exists():
                            shutil.rmtree(temp_extract_dir)
                        UI.die("Invalid LDM Package: Missing manifest 'meta' file.")

                    manifest = self.manager.read_meta(temp_extract_dir) or {}

                    db_type = manifest.get("db_type")
                    if db_type and db_type not in [
                        "postgresql",
                        "mysql",
                        "mariadb",
                        "hypersonic",
                    ]:
                        if temp_pkg_dir.exists():
                            shutil.rmtree(temp_pkg_dir)
                        if temp_extract_dir.exists():
                            shutil.rmtree(temp_extract_dir)
                        UI.die(
                            f"Unsupported database type '{db_type}' in LDM package manifest."
                        )

                    github_repo_manifest = manifest.get("github_repository")
                    if not github_repo_manifest:
                        if temp_pkg_dir.exists():
                            shutil.rmtree(temp_pkg_dir)
                        if temp_extract_dir.exists():
                            shutil.rmtree(temp_extract_dir)
                        UI.die(
                            "Security Violation: Manifest is missing 'github_repository' attribute."
                        )

                    if github_repo_manifest.lower() != f"{owner}/{repo}".lower():
                        if temp_pkg_dir.exists():
                            shutil.rmtree(temp_pkg_dir)
                        if temp_extract_dir.exists():
                            shutil.rmtree(temp_extract_dir)
                        UI.die("Security Violation: Repository origin mismatch.")

                    # Save original settings and restore
                    original_no_run = getattr(self.manager.args, "no_run", False)
                    self.manager.args.no_run = True
                    original_backup_dir = getattr(self.manager.args, "backup_dir", None)

                    try:
                        # Extract LDMP contents directly into the project directory
                        paths = self.manager.setup_paths(project_path)
                        for p in [
                            v
                            for v in paths.values()
                            if isinstance(v, Path) and not v.suffix
                        ]:
                            p.mkdir(parents=True, exist_ok=True)
                        self.manager.verify_runtime_environment(paths)

                        project_meta = self.manager.read_meta(project_path) or {}
                        if "tag" in manifest:
                            project_meta["tag"] = manifest["tag"]
                        if "db_type" in manifest:
                            project_meta["db_type"] = manifest["db_type"]

                        from ldm_core.utils import sanitize_id

                        safe_container_name = sanitize_id(project_name)

                        project_meta.update(
                            {
                                "project_name": project_name,
                                "container_name": safe_container_name,
                                "port": str(
                                    getattr(self.manager.args, "port", None)
                                    or project_meta.get("port")
                                    or 8080
                                ),
                                "ssl": str(
                                    getattr(self.manager.args, "ssl", None)
                                    or project_meta.get("ssl")
                                    or "false"
                                ).lower(),
                                "host_name": getattr(
                                    self.manager.args, "host_name", None
                                )
                                or project_meta.get("host_name")
                                or "localhost",
                                "last_run": datetime.now().isoformat(),
                            }
                        )
                        self.manager.write_meta(project_path, project_meta)

                        UI.info(
                            "Restoring database and volume assets from LDM package..."
                        )
                        self.manager._skip_git_check = True
                        try:
                            self.manager.snapshot.cmd_restore(
                                project_name, backup_dir=temp_extract_dir
                            )
                        finally:
                            if hasattr(self.manager, "_skip_git_check"):
                                delattr(self.manager, "_skip_git_check")
                        UI.success(f"Project created at: {project_path}")
                    finally:
                        self.manager.args.no_run = original_no_run
                        self.manager.args.backup_dir = original_backup_dir
                        if temp_pkg_dir.exists():
                            shutil.rmtree(temp_pkg_dir)
                        if temp_extract_dir.exists():
                            shutil.rmtree(temp_extract_dir)

                    # Boot stack if needed
                    if not original_no_run:
                        self.manager.cmd_run(project_id=project_name, is_restart=True)

                    return project_name

                # Standard clone path (no .ldmp package available, or --clone-only is specified)
                if not project_name:
                    if parsed:
                        project_name = parsed[1]
                    else:
                        project_name = source_path.split("/")[-1]
                        if project_name.endswith(".git"):
                            project_name = project_name[:-4]

                    if self.manager.non_interactive:
                        UI.info(f"Using default project name: {project_name}")
                    else:
                        project_name = UI.ask("Project Name", project_name)
                    self.manager.args.project = project_name

                project_path = self.manager.detect_project_path(
                    project_name, for_init=True
                )
                self.manager.check_uncommitted_changes(project_path)
                self._ensure_stopped(project_name, project_path)

                temp_git_dir = (
                    Path.cwd()
                    / ".ldm_temp"
                    / f"clone_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                )
                temp_git_dir.mkdir(parents=True, exist_ok=True)

                UI.info(f"Cloning remote repository: {source_path}...")

                # Authentication check note
                if source_path.startswith("git@"):
                    UI.detail(
                        "Using SSH protocol for clone. Assumes SSH agent or key is loaded."
                    )
                elif source_path.startswith("https://"):
                    from urllib.parse import urlparse

                    is_github = False
                    try:
                        parsed_url = urlparse(source_path)
                        if parsed_url.netloc in ("github.com", "www.github.com"):
                            is_github = True
                    except Exception:
                        pass

                    if is_github and "GITHUB_TOKEN" not in os.environ:
                        UI.info(
                            "Note: GITHUB_TOKEN environment variable is not set. If this is a private repository, cloning may fail."
                        )

                try:
                    res = subprocess.run(
                        ["git", "clone", "--", source_path, str(temp_git_dir)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if res.returncode != 0:
                        stderr = res.stderr or ""
                        if (
                            "Permission denied (publickey)" in stderr
                            or "Repository not found" in stderr
                        ):
                            if source_path.startswith("git@"):
                                UI.die(
                                    f"Git clone failed: Authentication error.\n"
                                    f"Details: {stderr.strip()}\n"
                                    f"Guide: Please configure your SSH keys (e.g. running 'ssh-add <path_to_key>') or verify repository access permissions."
                                )
                            else:
                                UI.die(
                                    f"Git clone failed: Authentication error.\n"
                                    f"Details: {stderr.strip()}\n"
                                    f"Guide: Please export a valid GITHUB_TOKEN: 'export GITHUB_TOKEN=your_pat'."
                                )
                        else:
                            UI.die(f"Git clone failed:\n{stderr.strip()}")
                except Exception as e:
                    UI.die(f"Failed to execute git clone: {e}")

                # Save original settings and call recursive cmd_import
                original_no_run = getattr(self.manager.args, "no_run", False)
                self.manager.args.no_run = True
                original_backup_dir = getattr(self.manager.args, "backup_dir", None)

                try:
                    # Import the code elements
                    self.cmd_import(str(temp_git_dir), is_init_from=is_init_from)
                finally:
                    self.manager.args.no_run = original_no_run
                    self.manager.args.backup_dir = original_backup_dir

                    if temp_git_dir.exists():
                        shutil.rmtree(temp_git_dir)

                # Boot stack if needed
                if not original_no_run:
                    self.manager.cmd_run(project_id=project_name, is_restart=True)

                return project_name

        source = Path(source_path).resolve()
        temp_extract_dir = None
        is_brand_new = False
        init_success = False

        try:
            if not source.exists():
                UI.die(f"Source path not found: {source}")
            if not self.manager._check_java_version("21"):
                UI.die("Incorrect system Java version. LDM import requires JDK 21.")

            if source.is_file():
                if source.suffix.lower() not in [
                    ".zip",
                    ".tgz",
                    ".gz",
                    ".tar",
                    ".ldmp",
                ]:
                    UI.die(f"Unsupported source format: {source.suffix}")

                # Verification logic (Integrity Track)
                verify_enabled = getattr(self.manager.args, "verify", True)
                sha_file = source.with_name(f"{source.name}.sha256")

                if verify_enabled:
                    if sha_file.exists():
                        UI.info(f"Verifying integrity of {source.name}...")

                        actual_sha = calculate_sha256(source)
                        expected_sha = sha_file.read_text().strip()
                        if actual_sha != expected_sha:
                            UI.die(
                                f"Integrity check failed for archive: {source.name}\n"
                                f"Expected: {expected_sha}\n"
                                f"Actual:   {actual_sha}\n"
                                f"The archive file may be corrupted or tampered with."
                            )
                        UI.success("Archive integrity verified.")
                    else:
                        UI.warning(
                            "Archive does not have an integrity checksum. Proceeding without verification."
                        )
                else:
                    UI.warning("Integrity verification disabled via --no-verify.")

                temp_extract_dir = (
                    Path.cwd()
                    / ".ldm_temp"
                    / f"import_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                )
                temp_extract_dir.mkdir(parents=True, exist_ok=True)
                UI.info("Extracting source archive...")
                from ldm_core.utils import safe_extract

                if source.suffix.lower() == ".zip":
                    with zipfile.ZipFile(source, "r") as z:
                        safe_extract(z, temp_extract_dir)
                else:
                    with tarfile.open(
                        source,
                        "r:gz"
                        if source.suffix.lower() in [".tgz", ".gz", ".ldmp"]
                        else "r:",
                    ) as t:
                        safe_extract(t, temp_extract_dir)

                for r, _d, f in os.walk(temp_extract_dir):
                    if (
                        Path(r) / "liferay" / "LCP.json"
                    ).exists() or "gradle.properties" in f:
                        source = Path(r)
                        break

            # Check if this is an LDM Package (.ldmp)
            is_ldmp = temp_extract_dir and (temp_extract_dir / "meta").exists()
            if is_ldmp:
                manifest = self.manager.read_meta(temp_extract_dir) or {}

                db_type = manifest.get("db_type")
                if db_type and db_type not in [
                    "postgresql",
                    "mysql",
                    "mariadb",
                    "hypersonic",
                ]:
                    if temp_extract_dir.exists():
                        shutil.rmtree(temp_extract_dir)
                    UI.die(
                        f"Unsupported database type '{db_type}' in LDM package manifest."
                    )

                # Resolve project name
                project_name = getattr(self.manager.args, "project", None) or getattr(
                    self.manager.args, "project_flag", None
                )
                if not project_name:
                    project_name = source.stem if source.is_file() else source.name
                    if self.manager.non_interactive:
                        UI.info(f"Using default project name: {project_name}")
                    else:
                        project_name = UI.ask("Project Name", project_name)

                project_path = self.manager.detect_project_path(
                    project_name, for_init=True
                )

                self.manager.check_uncommitted_changes(project_path)
                self._ensure_stopped(project_name, project_path)

                overwrite = True
                is_brand_new = not project_path.exists()

                if project_path.exists():
                    if self.manager.non_interactive:
                        UI.info(
                            f"Project '{project_name}' exists. Overwriting in non-interactive mode."
                        )
                    else:
                        ans = UI.ask(
                            f"Project '{project_name}' exists. Overwrite? [y]es, [n]o (skip existing), [c]lean, [q]uit",
                            "Y",
                        ).upper()
                        if ans == "C":
                            UI.info(
                                f"Cleaning existing project directory: {project_path}"
                            )
                            self.manager.safe_rmtree(project_path)
                            is_brand_new = True
                        elif ans == "N":
                            overwrite = False
                            UI.info("Proceeding in 'skip existing' mode.")
                        elif ans == "Y":
                            overwrite = True
                        else:
                            UI.die("Initialization aborted.")

                paths = self.manager.setup_paths(project_path)
                for p in [
                    v for v in paths.values() if isinstance(v, Path) and not v.suffix
                ]:
                    p.mkdir(parents=True, exist_ok=True)

                self.manager.verify_runtime_environment(paths)

                project_meta = self.manager.read_meta(project_path) or {}
                if "tag" in manifest:
                    project_meta["tag"] = manifest["tag"]
                if "db_type" in manifest:
                    project_meta["db_type"] = manifest["db_type"]

                from ldm_core.utils import sanitize_id

                safe_container_name = sanitize_id(project_name)
                if project_name != safe_container_name and getattr(
                    self.manager.args, "verbose", False
                ):
                    UI.info(
                        f"Project name '{project_name}' contains invalid characters for Docker. Using '{safe_container_name}' for container names."
                    )

                project_meta.update(
                    {
                        "project_name": project_name,
                        "container_name": safe_container_name,
                        "port": str(
                            getattr(self.manager.args, "port", None)
                            or project_meta.get("port")
                            or 8080
                        ),
                        "ssl": str(
                            getattr(self.manager.args, "ssl", None)
                            or project_meta.get("ssl")
                            or "false"
                        ).lower(),
                        "host_name": getattr(self.manager.args, "host_name", None)
                        or project_meta.get("host_name")
                        or "localhost",
                        "last_run": datetime.now().isoformat(),
                    }
                )
                self.manager.write_meta(project_path, project_meta)

                # Restore database and volume assets
                UI.info("Restoring database and volume assets from LDM package...")
                self.manager.snapshot.cmd_restore(
                    project_name, backup_dir=temp_extract_dir
                )

                UI.success(f"Project created at: {project_path}")

                if not getattr(self.manager.args, "no_run", False):
                    self.manager.cmd_run(project_id=project_name, is_restart=True)

                return project_name

            workspace_root = (
                source / "liferay" if (source / "liferay").exists() else source
            )
            from ldm_core.utils import is_lcp_workspace

            is_cloud = is_lcp_workspace(source)

            # Project Naming Logic (Standardized)
            project_name = getattr(self.manager.args, "project", None) or getattr(
                self.manager.args, "project_flag", None
            )
            if not project_name:
                project_name = source.name
                if self.manager.non_interactive:
                    UI.info(f"Using default project name: {project_name}")
                else:
                    project_name = UI.ask("Project Name", project_name)

            project_path = self.manager.detect_project_path(project_name, for_init=True)

            # Check if project is currently running to prevent filesystem corruption during import
            self._ensure_stopped(project_name, project_path)

            overwrite = True
            is_brand_new = not project_path.exists()
            init_success = False

            if project_path.exists():
                if self.manager.non_interactive:
                    UI.info(
                        f"Project '{project_name}' exists. Overwriting in non-interactive mode."
                    )
                else:
                    ans = UI.ask(
                        f"Project '{project_name}' exists. Overwrite? [y]es, [n]o (skip existing), [c]lean, [q]uit",
                        "Y",
                    ).upper()
                    if ans == "C":
                        UI.info(f"Cleaning existing project directory: {project_path}")
                        self.manager.safe_rmtree(project_path)
                        is_brand_new = True
                    elif ans == "N":
                        overwrite = False
                        UI.info("Proceeding in 'skip existing' mode.")
                    elif ans == "Y":
                        overwrite = True
                    else:
                        UI.die("Initialization aborted.")

            paths = self.manager.setup_paths(project_path)
            for p in [
                v for v in paths.values() if isinstance(v, Path) and not v.suffix
            ]:
                p.mkdir(parents=True, exist_ok=True)

            # Fail Fast: Verify volume mounting before performing any sync/import work
            self.manager.verify_runtime_environment(paths)

            if getattr(self.manager.args, "build", False):
                UI.heading(f"Building Workspace: {workspace_root.name}")
                gradlew = workspace_root / (
                    "gradlew" if platform.system() != "Windows" else "gradlew.bat"
                )
                if (
                    not gradlew.exists()
                    and is_cloud
                    and (workspace_root.parent / gradlew.name).exists()
                ):
                    gradlew = workspace_root.parent / gradlew.name
                if gradlew.exists():
                    if not self.manager._check_gradle_java_version(gradlew, "21"):
                        UI.die("Gradle requires JDK 21.")
                    if platform.system() != "Windows":
                        # Bandit: B103 (chmod 0o755) is safe for gradlew.
                        try:
                            os.chmod(gradlew, 0o755)  # nosec B103
                        except Exception:
                            pass
                    try:
                        UI.info(f"Executing clean build in {gradlew.parent}...")
                        self.manager.run_command(
                            [str(gradlew), "clean", "build", "-x", "test"],
                            capture_output=False,
                            cwd=str(gradlew.parent),
                        )
                    except Exception as e:
                        UI.error(f"Build failed: {e}")
                        if self.manager.non_interactive:
                            UI.die("Build failed in non-interactive mode. Aborting.")
                        if not UI.confirm("Continue anyway? (y/n/q)", "N"):
                            sys.exit(1)
                else:
                    UI.warning("gradlew not found. Skipping build.")

            # 5. Finalize Meta (Match cmd_run rules)
            host_name = getattr(self.manager.args, "host_name", None)
            if not host_name:
                if self.manager.non_interactive:
                    host_name = self.manager.defaults.get("host_name")
                else:
                    host_name = UI.ask(
                        "Enter project Virtual Hostname",
                        self.manager.defaults.get("host_name"),
                    )

            # SSL Rule: Default to True only if host_name is NOT localhost
            ssl_arg = getattr(self.manager.args, "ssl", None)
            use_ssl = ssl_arg if ssl_arg is not None else host_name != "localhost"

            # DNS Check (Intelligent & Auto-Fixing)
            self.manager.ensure_hostnames_resolve(
                project_path, host_name, project_id=project_name
            )

            custom_env = {
                k: v
                for env_pair in (getattr(self.manager.args, "env", None) or [])
                if "=" in env_pair
                for k, v in [env_pair.split("=", 1)]
            }
            if use_ssl:
                self.manager.diagnostics.check_mkcert()

            from ldm_core.utils import sanitize_id

            safe_container_name = sanitize_id(project_name)
            if project_name != safe_container_name and getattr(
                self.manager.args, "verbose", False
            ):
                UI.info(
                    f"Project name '{project_name}' contains invalid characters for Docker. Using '{safe_container_name}' for container names."
                )

            project_meta = {
                "project_name": project_name,
                "container_name": safe_container_name,
                "port": str(getattr(self.manager.args, "port", None) or 8080),
                "ssl": str(use_ssl).lower(),
                "ssl_port": "443",
                "host_name": host_name,
                "last_run": datetime.now().isoformat(),
                "mount_logs": str(
                    getattr(self.manager.args, "mount_logs", None) or False
                ).lower(),
                "gogo_port": str(
                    getattr(self.manager.args, "gogo_port", None) or "None"
                ),
                "custom_env": json.dumps(custom_env),
                "db_type": getattr(self.manager.args, "db", None),
                "workspace_path": str(source) if is_init_from else None,
            }

            gradle_props = workspace_root / "gradle.properties"
            if gradle_props.exists():
                for line in gradle_props.read_text().splitlines():
                    if "liferay.workspace.product" in line and "=" in line:
                        raw_product = line.split("=", 1)[1].strip()
                        from ldm_core.utils import resolve_liferay_docker_tag

                        resolved_tag, is_portal = resolve_liferay_docker_tag(
                            raw_product, self.manager
                        )
                        if resolved_tag:
                            tag = resolved_tag
                            project_meta["portal"] = "true" if is_portal else "false"
                        else:
                            tag = re.sub(r"^(dxp|portal)-", "", raw_product)
                        # Workspace product always wins
                        project_meta["tag"] = tag
                        self.manager.args.tag = tag
                        UI.info(f"Extracted version: {tag}")

                        # Seeded Start: Boost performance if tag is known
                        if not project_path.exists() or overwrite:
                            db_type_for_seed = (
                                getattr(self.manager.args, "db", None) or "hypersonic"
                            )
                            if self.manager.assets._ensure_seeded(
                                tag, db_type_for_seed, paths
                            ):
                                # Refresh meta from seed before merging workspace changes
                                seed_meta = self.manager.read_meta(project_path)
                                project_meta.update(seed_meta)
                        break

            if is_cloud:
                cli_cloud_id = getattr(self.manager.args, "cloud_project", None)
                if cli_cloud_id:
                    project_meta["cloud_project_id"] = cli_cloud_id
                else:
                    root_lcp_path = source / "lcp.json"
                    if not root_lcp_path.exists():
                        root_lcp_path = source / "LCP.json"
                    if root_lcp_path.exists():
                        try:
                            root_lcp = json.loads(root_lcp_path.read_text())
                            if "id" in root_lcp:
                                project_meta["cloud_project_id"] = root_lcp["id"]
                        except Exception:
                            pass

                # If cloud_project_id still not found, prompt or error
                if not project_meta.get("cloud_project_id"):
                    default_id = source.name
                    if self.manager.non_interactive:
                        UI.die(
                            "Liferay Cloud project ID could not be determined. Please specify it using --cloud-project.",
                            exit_code=2,
                        )
                    else:
                        UI.info(
                            "Liferay Cloud project ID could not be automatically determined from a root LCP.json."
                        )
                        cloud_id = UI.ask("Liferay Cloud Project ID", default_id)
                        project_meta["cloud_project_id"] = cloud_id

                lcp_path = workspace_root / "LCP.json"
                if lcp_path.exists():
                    try:
                        lcp = json.loads(lcp_path.read_text())
                        for k in ["memory", "cpu"]:
                            if k in lcp:
                                project_meta[k] = lcp[k]
                    except Exception as e:
                        UI.warning(f"Failed to parse LCP.json: {e}")

            config_src = (
                workspace_root
                / "configs"
                / getattr(self.manager.args, "target_env", "local")
            )
            if config_src.exists():
                pe = config_src / "portal-ext.properties"
                if pe.exists():
                    safe_copy(pe, paths["files"] / "portal-ext.properties")
                    UI.success("Imported portal-ext.properties")
                osgi_src = config_src / "osgi" / "configs"
                if osgi_src.exists():
                    count = 0
                    for f in list(osgi_src.glob("*.config")) + list(
                        osgi_src.glob("*.cfg")
                    ):
                        safe_copy(f, paths["configs"] / f.name)
                        count += 1
                    if count > 0:
                        UI.success(f"Imported {count} OSGi configs.")

                deploy_src = config_src / "deploy"
                if deploy_src.exists():
                    count = 0
                    for f in deploy_src.glob("*"):
                        if f.is_file():
                            safe_copy(f, paths["deploy"] / f.name)
                            count += 1
                    if count > 0:
                        UI.success(f"Imported {count} assets from deploy/.")

            def import_zips(search_base, label, target_dir, overwrite=False):
                count = 0
                if not search_base.exists():
                    return 0

                # Check root of search_base
                zips_at_root = list(search_base.glob("*.zip"))

                # Check subdirectories
                ext_folders = [
                    f
                    for f in search_base.iterdir()
                    if f.is_dir() and not f.name.startswith(".")
                ]

                nested_zips: list[Path] = []
                for folder in ext_folders:
                    nested_zips.extend(list(folder.glob("dist/*.zip")))
                    nested_zips.extend(list(folder.glob("*.zip")))

                for z in list(set(zips_at_root + nested_zips)):
                    if label == "Fragment":
                        try:
                            with zipfile.ZipFile(z, "r") as zip_ref:
                                if (
                                    "liferay-deploy-fragments.json"
                                    not in zip_ref.namelist()
                                ):
                                    UI.error(f"{z.name} missing fragment descriptor.")
                                    continue
                        except Exception:
                            UI.error(f"{z.name} corrupt.")
                            continue
                    target_file = target_dir / z.name
                    if target_file.exists() and not overwrite:
                        UI.debug(f"Skipping existing {label}: {z.name}")
                        continue

                    safe_copy(z, target_file)
                    count += 1
                return count

            import_zips(
                workspace_root / "client-extensions",
                "Extension",
                paths["ce_dir"],
                overwrite,
            )
            import_zips(
                workspace_root / "fragments", "Fragment", paths["ce_dir"], overwrite
            )

            for search_folder in ["modules", "themes"]:
                base = workspace_root / search_folder
                if base.exists():
                    for root, dirs, _files in os.walk(base):
                        if "build" in dirs:
                            libs = Path(root) / "build" / "libs"
                            if libs.exists():
                                for f in libs.glob("*.[jw]ar"):
                                    if not any(
                                        x in f.name.lower()
                                        for x in ["-sources", "-javadoc", "-tests"]
                                    ):
                                        safe_copy(f, paths["modules"] / f.name)

            if is_cloud:
                infra_dirs = [
                    "liferay",
                    "backup",
                    "ci",
                    "database",
                    "search",
                    "webserver",
                    ".git",
                ]
                for item in [
                    i
                    for i in workspace_root.parent.iterdir()
                    if i.is_dir()
                    and i.name not in infra_dirs
                    and not i.name.startswith(".")
                ]:
                    if (item / "LCP.json").exists() and (item / "Dockerfile").exists():
                        dest = paths["root"] / "services" / item.name
                        if dest.exists():
                            self.manager.safe_rmtree(dest)
                        shutil.copytree(item, dest, copy_function=safe_copy)

            backup_dir_path = getattr(self.manager.args, "backup_dir", None)
            if backup_dir_path:
                backup_dir = Path(backup_dir_path).resolve()
                if backup_dir.exists():
                    self.manager.snapshot._restore_from_cloud_layout(
                        backup_dir, paths, project_meta
                    )

            self._hydrate_from_workspace(workspace_root, paths, overwrite=overwrite)

            self.manager.write_meta(project_path, project_meta)
            init_success = True
            UI.success(f"Project created at: {project_path}")

            # Offer PaaS data hydration after code is imported
            self._prompt_cloud_hydration(source_path, project_name=project_name)

            if not getattr(self.manager.args, "no_run", False):
                self.manager.cmd_run(project_id=project_name, is_restart=True)
                if getattr(self.manager.args, "share", False):
                    UI.info("Exposing imported workspace tunnel...")
                    share_subdomain = getattr(
                        self.manager.args, "share_subdomain", None
                    )
                    if hasattr(self.manager, "share"):
                        self.manager.share.cmd_start(
                            project_name, subdomain=share_subdomain
                        )
        finally:
            if temp_extract_dir:
                self.manager.safe_rmtree(temp_extract_dir)

                # Clean up parent .ldm_temp if it's now empty
                ldm_temp = temp_extract_dir.parent
                if (
                    ldm_temp.exists()
                    and ldm_temp.is_dir()
                    and not any(ldm_temp.iterdir())
                ):
                    with contextlib.suppress(OSError):
                        ldm_temp.rmdir()

            # Rollback: If a brand-new project failed to initialize, clean it up
            # and unregister it to avoid leaving a half-baked 'unknown' project.
            if is_brand_new and not init_success:
                if project_path.exists():
                    UI.info(f"Cleaning up failed initialization: {project_path}")
                    self.manager.safe_rmtree(project_path)
                self.manager.unregister_project(project_name)

        return project_name

    def cmd_monitor(self, source_path=None):
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
            from watchdog.observers.polling import PollingObserverVFS
        except ImportError:
            UI.die("watchdog required: pip install watchdog")

        project_id = (
            getattr(self.manager.args, "project", None)
            or self.manager.detect_project_path()
        )
        if not project_id:
            UI.die(
                "No project specified and no project found in current directory. "
                "Use 'ldm monitor <project_name>' or navigate to a project folder."
            )

        paths = self.manager.setup_paths(project_id)
        project_meta = self.manager.read_meta(paths["root"])

        if not source_path:
            source_path = project_meta.get("workspace_path")
            if not source_path:
                UI.die(
                    "No workspace path provided and project is not linked to a source."
                )
            UI.info(f"Using linked workspace: {source_path}")

        source = Path(source_path).resolve()
        from ldm_core.utils import is_lcp_workspace

        workspace_root = (
            source / "liferay"
            if (source / "liferay").exists() and is_lcp_workspace(source)
            else source
        )
        UI.heading(f"Monitoring: {workspace_root.name}")

        class WorkspaceEventHandler(FileSystemEventHandler):
            def __init__(self, manager, workspace_root, paths, project_meta, delay):
                (
                    self.manager,
                    self.workspace_root,
                    self.paths,
                    self.project_meta,
                    self.delay,
                ) = (manager, workspace_root, paths, project_meta, delay)
                self.timer, self.pending_files, self.lock = (
                    None,
                    set(),
                    threading.Lock(),
                )

            def on_created(self, event):
                if not event.is_directory:
                    self._handle_event(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    self._handle_event(event.src_path)

            def _handle_event(self, path):
                p = Path(path)

                # Performance Optimization: Skip massive build/node_modules directories early
                if any(
                    x in p.parts for x in ["build", "node_modules", ".gradle", ".lsp"]
                ):
                    # We only care about the specific artifacts in 'dist' or 'libs'
                    if not any(x in p.parts for x in ["dist", "libs"]):
                        UI.detail(f"Monitor: Skipping deep build file: {p.name}")
                        return

                # Refined Filtering Logic:
                # 1. client-extensions/**/*.zip
                # 2. fragments/**/*.zip
                # 3. modules/*/build/libs/*.jar

                is_valid = False
                if p.suffix.lower() == ".zip":
                    if "client-extensions" in p.parts or "fragments" in p.parts:
                        is_valid = True
                elif p.suffix.lower() in [".jar", ".war"] and (
                    "modules" in p.parts and "build" in p.parts and "libs" in p.parts
                ):
                    is_valid = True

                if is_valid:
                    UI.detail(f"Monitor: Detected valid artifact: {p.name}")
                    with self.lock:
                        self.pending_files.add(p)
                        if self.timer:
                            self.timer.cancel()
                        self.timer = threading.Timer(self.delay, self._process_pending)
                        self.timer.start()
                else:
                    UI.detail(f"Monitor: Ignoring non-artifact change: {p.name}")

            def _process_pending(self):
                with self.lock:
                    files, self.pending_files, self.timer = (
                        list(self.pending_files),
                        set(),
                        None,
                    )
                if not files:
                    return

                updated_services = set()
                for f in files:
                    # 1. Determine action based on type
                    if f.suffix.lower() == ".zip":
                        # Client Extension
                        self.manager._sync_cx_artifact(f, self.paths)
                        if "client-extensions" in f.parts:
                            # Only trigger targeted deploy if it's a Docker-based service (SSCE)
                            svc_id = f.stem.lower().replace("_", "-")
                            target_folder = self.paths["ce_dir"] / f.stem
                            if (target_folder / "Dockerfile").exists():
                                updated_services.add(svc_id)
                    else:
                        # JARs for Liferay modules (sync to deploy)
                        dest_path = self.paths["deploy"] / f.name
                        UI.detail(f"Syncing Module: {f.name}")
                        atomic_copy(f, dest_path)

                # 3. Trigger deployment from the project's internal state
                if updated_services:
                    for svc in updated_services:
                        self.manager.cmd_deploy(service=svc)
                else:
                    self.manager.cmd_deploy()

                UI.success("Deployment complete.")

        # On macOS, Native Observer (Kqueue) often hits file descriptor limits
        # because it requires an open file handle for every directory.
        # PollingObserver is much safer for large workspace monitoring.
        # Polling Optimization: Exclude massive dependencies to reduce file/stat overhead
        ignored_dirs = {
            "node_modules",
            "build",
            ".gradle",
            ".git",
            ".idea",
            ".vscode",
            "backup",
            "ci",
            "database",
            "search",
            "webserver",
        }

        def filtered_scandir(path=None):
            for entry in os.scandir(path):
                if entry.is_dir(follow_symlinks=False) and entry.name in ignored_dirs:
                    continue
                yield entry

        is_mac = platform.system().lower() == "darwin"
        delay = float(getattr(self.manager.args, "delay", 2.0))

        if is_mac:
            # Proactively increase file descriptor limits for this process
            try:
                import resource

                _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)

                # Try to set a generous limit (e.g., 4096)
                new_soft = min(hard, 4096)
                resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))

                if self.manager.verbose:
                    UI.info(f"OS File Limits: Soft={new_soft}, Hard={hard}")
            except Exception:
                pass

            if self.manager.verbose:
                UI.info("Using PollingObserver for macOS stability.")
            observer = PollingObserverVFS(
                stat=os.stat, listdir=filtered_scandir, polling_interval=delay
            )
        else:
            observer = Observer()

        UI.info("Scanning for workspace branches...")
        watch_targets = []
        allowed_branches = ["client-extensions", "modules", "fragments"]

        for branch in allowed_branches:
            target = workspace_root / branch
            if target.exists():
                watch_targets.append(target)
                UI.detail(f"  + Watching: {branch}")

        if not watch_targets:
            watch_targets = [workspace_root]

        handler = WorkspaceEventHandler(
            self,
            workspace_root,
            paths,
            project_meta,
            float(getattr(self.manager.args, "delay", 2.0)),
        )

        for target in watch_targets:
            try:
                # We now watch branches recursively but the handler filters precisely
                observer.schedule(handler, str(target), recursive=True)
            except OSError as e:
                if e.errno == 24:  # Too many open files
                    if not is_mac:
                        UI.error(
                            "Hit system file limit. Switching to PollingObserver..."
                        )
                        # Switch to polling for this and future targets
                        if not isinstance(observer, PollingObserverVFS):
                            observer.stop()
                            observer = PollingObserverVFS(
                                stat=os.stat,
                                listdir=filtered_scandir,
                                polling_interval=delay,
                            )
                            observer.schedule(handler, str(target), recursive=True)
                            observer.start()
                    else:
                        UI.die(
                            f"Fatal: OS file limit reached even with Polling. Path: {target}"
                        )
                else:
                    raise e

        observer.start()

        try:
            UI.info("Watching for changes (Press Ctrl+C to stop)...")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
            UI.info("Monitor stopped.")
        observer.join()

    def cmd_fork(self, source, target, snapshot=None):
        """Forks an existing project into a new one, cloning database and DL assets."""
        is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
        if is_dry_run:
            UI.info(
                f"{UI.BYELLOW}[DRY RUN] Would fork project: {source} -> {target}{UI.COLOR_OFF}"
            )
            return target

        from ldm_core.utils import sanitize_id

        # 1. Resolve paths
        source_root = self.manager.detect_project_path(source, fatal=False)
        if not source_root:
            UI.die(f"Source project '{source}' does not exist.")
            return None

        # Target directory should be sibling of source
        target_root = source_root.parent / target
        if target_root.exists():
            # Check if there are any project files in target
            has_meta = any(
                (target_root / f).exists()
                for f in [
                    "meta",
                    ".liferay-docker.meta",
                    ".ldm.meta",
                    "docker-compose.yml",
                ]
            )
            if has_meta:
                UI.die(f"Target project '{target}' already exists at: {target_root}")
                return None

        # 2. Setup paths
        source_paths = self.manager.setup_paths(source_root)
        target_paths = self.manager.setup_paths(target_root)

        # 3. Determine or create snapshot to restore
        snapshot_dir = None
        if snapshot:
            snapshot_dir = source_paths["backups"] / snapshot
            if not snapshot_dir.exists():
                UI.die(
                    f"Snapshot '{snapshot}' not found in source project: {source_paths['backups']}"
                )
                return None
        else:
            UI.info(f"Creating backup snapshot of '{source}' for forking...")
            old_name = getattr(self.manager.args, "name", None)
            self.manager.args.name = f"Fork backup of {source}"
            try:
                self.manager.snapshot.cmd_snapshot(project_id=source)
            finally:
                self.manager.args.name = old_name

            snaps = self.manager.snapshot._get_snapshots(source_paths)
            if not snaps:
                UI.die(f"Failed to create snapshot of source project '{source}'.")
                return None
            snapshot_dir = snaps[-1]["path"]

        # 4. Read source metadata and construct target metadata
        source_meta = self.manager.read_meta(source_root) or {}
        target_meta = dict(source_meta)

        # Mutate target metadata fields
        sanitized_target = sanitize_id(target)
        target_meta.update(
            {
                "project_name": target,
                "container_name": sanitized_target,
                "db_container_name": f"{sanitized_target}-db",
                "tunnel_container_name": f"{sanitized_target}-tunnel",
                "host_name": f"{sanitized_target}.local",
                "seeded": "true",
            }
        )

        # Port Resolution: Scan starting from source port + 1 to find a free host port
        source_port = 8080
        try:
            source_port = int(source_meta.get("port") or 8080)
        except Exception:
            pass

        new_port = source_port + 1
        while not self.manager.check_port("127.0.0.1", new_port):
            new_port += 1

        target_meta["port"] = str(new_port)

        # 5. Create target directory and write metadata
        target_root.mkdir(parents=True, exist_ok=True)
        self.manager.write_meta(target_root, target_meta)

        # 6. Register target project in global registry
        self.manager.register_project(target, target_root, target_meta["host_name"])

        # 7. Restore the snapshot into the new target project
        UI.info(f"Restoring cloned snapshot data to fork project '{target}'...")
        self.manager.snapshot.cmd_restore(
            project_id=target, backup_dir=str(snapshot_dir)
        )

        # 8. Rebuild composition & configurations cleanly for target
        UI.info(f"Synchronizing compose stack for fork project '{target}'...")
        self.manager.runtime.sync_stack(
            target_paths, target_meta, no_up=True, show_summary=True
        )

        UI.success(
            f"Successfully forked project '{source}' to '{target}'!\n"
            f"  - Target directory: {target_root}\n"
            f"  - Port resolved:    {new_port}\n"
            f"  - Host resolved:    {target_meta['host_name']}\n\n"
            f"You can now run: {UI.CYAN}ldm run {target}{UI.COLOR_OFF}"
        )
        return target

    def cmd_quickstart(self, template_name, share=False, share_subdomain=None):
        """Bootstraps a predefined accelerator stack, imports, seeds, runs, and exposes it."""
        templates = {
            "aica": {
                "repo": "https://github.com/peterrichards-lr/liferay-ai-commerce-accelerator.git",
                "default_name": "liferay-ai-commerce-accelerator",
            }
        }

        # Load user templates overrides if file exists
        from ldm_core.utils import get_actual_home

        user_templates_path = get_actual_home() / ".ldm_templates.json"
        if user_templates_path.exists():
            try:
                import json

                overrides = json.loads(user_templates_path.read_text())
                for key, val in overrides.items():
                    if (
                        isinstance(val, dict)
                        and "repo" in val
                        and "default_name" in val
                    ):
                        templates[key.lower()] = val
            except Exception as e:
                UI.warning(
                    f"Failed to load quickstart templates from {user_templates_path}: {e}"
                )

        name_lower = template_name.lower()
        if name_lower not in templates:
            UI.die(f"Unrecognized quickstart template: {template_name}")
            return

        template_info = templates[name_lower]
        repo_url = template_info["repo"]
        project_name = template_info["default_name"]

        # Ensure project argument is set so import target matches
        self.manager.args.project = project_name

        UI.heading(f"Starting Quickstart: {template_name.upper()}")

        # 1. Import workspace repository
        UI.info(f"Importing template workspace from: {repo_url}...")
        self.cmd_import(repo_url)

        # Detect paths after import
        project_path = self.manager.detect_project_path(project_name)
        if not project_path or not project_path.exists():
            UI.die(f"Failed to locate imported workspace directory for {project_name}.")
            return

        project_meta = self.manager.read_meta(project_path)
        tag = project_meta.get("tag")
        db_type = project_meta.get("db_type", "postgresql")

        if not tag:
            tag = "2026.q1.4-lts"  # sensible fallback version
            UI.warning(
                f"Project metadata missing 'tag'. Falling back to default Liferay tag: {tag}"
            )

        # 2. Determine search mode
        default_shared = (
            "true" if self.manager.parse_version(tag) >= (2025, 1, 0) else "false"
        )
        use_shared = (
            str(project_meta.get("use_shared_search", default_shared)).lower() == "true"
        )
        if not use_shared and self.manager.parse_version(tag) >= (2025, 2, 0):
            use_shared = True
        search_mode = "shared" if use_shared else "sidecar"

        # 3. Apply fresh database seed
        UI.info(f"Seeding database for {tag} ({db_type}/{search_mode})...")
        paths = self.manager.setup_paths(project_path)
        if not self.manager.assets._fetch_seed(tag, db_type, search_mode, paths):
            UI.die("Failed to seed database during quickstart.")
            return

        # 4. Start stack and launch browser
        UI.info("Starting quickstart services stack...")
        self.manager.args.browser = True
        self.manager.runtime.cmd_run(project_name)

        # 5. Share via tunnel if requested
        if share:
            UI.info("Exposing quickstart tunnel...")
            self.manager.share.cmd_start(project_name, subdomain=share_subdomain)
