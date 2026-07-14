import json
import os
import platform
import re
import shutil
import tarfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

if TYPE_CHECKING:
    pass

from ldm_core.constants import SCRIPT_DIR, VERSION
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
        """Parse client-extension.yaml content using PyYAML for robust structured extraction.

        Uses yaml.safe_load instead of fragile regex matching so that comments,
        nested configuration blocks, and multi-line YAML values are all handled
        correctly.  Falls back to an empty result on any parse failure.
        """
        info: dict[str, Any] = {"type": None, "oauth_erc": None}
        try:
            data = yaml.safe_load(content)
            if isinstance(data, dict):
                # Top-level "type" field (single-block format)
                if "type" in data:
                    info["type"] = str(data["type"]).strip()
                # Top-level oAuthApplicationHeadlessServer ERC (single-block format)
                if "oAuthApplicationHeadlessServer" in data:
                    info["oauth_erc"] = str(
                        data["oAuthApplicationHeadlessServer"]
                    ).strip()
                else:
                    # Multi-block format: scan nested configuration dicts
                    for block in data.values():
                        if not isinstance(block, dict):
                            continue
                        if info["type"] is None and "type" in block:
                            info["type"] = str(block["type"]).strip()
                        if info["oauth_erc"] is None and (
                            erc := block.get("oAuthApplicationHeadlessServer")
                        ):
                            info["oauth_erc"] = str(erc).strip()
                        if info["type"] and info["oauth_erc"]:
                            break
        except Exception:
            pass
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

    def scan_client_extensions(
        self, root_dir, osgi_cx_dir, ce_build_dir, host_name=None
    ):
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

                        if host_name:
                            self._rewrite_oauth_urls_in_zip(
                                dest_zip, host_name, item.stem.lower().replace("_", "-")
                            )

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

                if host_name:
                    dest_zip = osgi_cx_dir / f"{item.name}.zip"
                    if not dest_zip.exists():
                        alt_name = item.name.replace("-", "_")
                        if (osgi_cx_dir / f"{alt_name}.zip").exists():
                            dest_zip = osgi_cx_dir / f"{alt_name}.zip"
                        elif (
                            osgi_cx_dir / f"{item.name.replace('_', '-')}.zip"
                        ).exists():
                            dest_zip = (
                                osgi_cx_dir / f"{item.name.replace('_', '-')}.zip"
                            )

                    if dest_zip.exists():
                        self._rewrite_oauth_urls_in_zip(
                            dest_zip, host_name, item.name.lower().replace("_", "-")
                        )

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

        # 4. Sync Fragment Overrides
        for override_file in [
            workspace_root / ".ldm" / "fragment-overrides.json",
            workspace_root / "configs" / "fragment-overrides.json",
        ]:
            if override_file.exists():
                dest = paths["root"] / ".ldm" / "fragment-overrides.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not overwrite and dest.exists():
                    UI.detail("  - Skipping existing fragment overrides")
                    continue
                try:
                    if override_file.resolve() != dest.resolve():
                        safe_copy(override_file, dest)
                        UI.detail("  + Synced Fragment Overrides")
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

        self.manager.setup_paths(project_path)

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

    def cmd_link(self, source_path):
        """Initialize project with a persistent link to a source workspace and start monitoring."""
        from ldm_core.utils import UI

        try:
            source = Path(source_path).resolve()
            is_valid_dir = source.exists() and source.is_dir()
        except Exception:
            is_valid_dir = False

        if not is_valid_dir:
            UI.die(
                "Error: Source path to link must be a local Liferay Workspace directory."
            )

        project_name = self.cmd_import(str(source), is_init_from=True)
        if project_name:
            self.manager.args.project = project_name

        self.cmd_monitor(str(source))

    def cmd_clone(self, source_path):
        """Clone a remote Git repository workspace and initialize it."""
        from ldm_core.utils import UI

        is_remote = (
            source_path.startswith(("http://", "https://", "git@"))
            or "://" in source_path
        )
        if not is_remote:
            UI.die("Error: Source path to clone must be a valid Git repository URL.")

        self.manager.args.clone_only = True
        return self.cmd_import(source_path)

    def cmd_init_from(self, source_path):
        """Deprecated: Initialize project from workspace."""
        from ldm_core.utils import UI

        UI.warning(
            "Deprecation Warning: 'ldm init-from' is deprecated. Please use: 'ldm link <workspace-path>' instead."
        )
        return self.cmd_link(source_path)

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
            if parsed.scheme in ("http", "https") and parsed.hostname in (
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

    def cmd_validate(self, project_id=None):
        """Runs the Pre-Flight Client Extension Analyzer against the workspace."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        from ldm_core.handlers.validation import ClientExtensionAnalyzer

        if ClientExtensionAnalyzer.analyze_workspace(root):
            from ldm_core.utils import UI

            UI.success(
                "Validation passed. Client extensions appear structurally sound."
            )
        else:
            from ldm_core.utils import UI

            UI.error("Validation failed. See warnings above.")

    def cmd_import(self, source_path, is_init_from=False, is_internal=False):
        from ldm_core.utils import UI

        is_remote = (
            source_path.startswith(("http://", "https://", "git@"))
            or "://" in source_path
        )
        if not is_remote:
            try:
                source_p = Path(source_path).resolve()
                if source_p.is_dir() and not is_init_from and not is_internal:
                    UI.die(
                        "Error: To integrate a local Liferay Workspace, please use: 'ldm link <workspace-path>'"
                    )
            except Exception:
                pass

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
                "ldm_version": VERSION,
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
                    return self.cmd_import(
                        str(local_path), is_init_from=is_init_from, is_internal=True
                    )
                finally:
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir)
            else:
                # Git URL / GitHub Repo URL
                project_name = getattr(self.manager.args, "project", None) or getattr(
                    self.manager.args, "project_flag", None
                )
                parsed = self._parse_github_repo(source_path)
                from ldm_core.utils import get_github_token

                github_token = get_github_token()
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
                                # and fall back to standard cloning to ensure the user gets the workspace code
                                asset_size = ldmp_asset.get("size", 0)
                                if asset_size > 10240:
                                    has_ldmp = True
                                else:
                                    UI.die(
                                        f"Remote LDM package '{ldmp_asset.get('name')}' is too small ({asset_size} bytes) "
                                        f"and appears to be empty/vanilla. To clone the workspace code directly, please use: 'ldm clone {source_path}'"
                                    )
                        elif api_resp.status_code == 403:
                            UI.warning(
                                "GitHub API rate limit exceeded. Falling back to standard git clone. (Set GITHUB_TOKEN to avoid this)"
                            )
                        else:
                            UI.debug(f"GitHub API returned {api_resp.status_code}")
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

                        final_host_name = (
                            getattr(self.manager.args, "host_name", None)
                            or project_meta.get("host_name")
                            or "localhost"
                        )
                        ssl_arg = getattr(self.manager.args, "ssl", None)
                        UI.debug(f"ssl_arg={ssl_arg}, type={type(ssl_arg)}")
                        if ssl_arg is not None:
                            final_ssl = str(ssl_arg).lower()
                        elif getattr(self.manager.args, "host_name", None) is not None:
                            final_ssl = str(final_host_name != "localhost").lower()
                            UI.debug(
                                f"final_host_name={final_host_name}, final_ssl={final_ssl}"
                            )
                        else:
                            final_ssl = str(project_meta.get("ssl") or "false").lower()

                        project_meta.update(
                            {
                                "project_name": project_name,
                                "container_name": safe_container_name,
                                "port": str(
                                    getattr(self.manager.args, "port", None)
                                    or project_meta.get("port")
                                    or 8080
                                ),
                                "ssl": final_ssl,
                                "host_name": final_host_name,
                                "last_run": datetime.now().isoformat(),
                                "restored_from_package": "true",
                                "package_includes_database": str(
                                    manifest.get("includes_database", "false")
                                ).lower(),
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
                    # Only boot if the package actually included a database snapshot.
                    # If it did not, we should keep it stopped so the caller (quickstart) can seed it first.
                    if not original_no_run and manifest.get("includes_database") in [
                        True,
                        "true",
                    ]:
                        self.manager.cmd_run(project_id=project_name, is_restart=True)

                    return project_name

                # Standard clone path (no .ldmp package available, or --clone-only is specified)
                if not getattr(self.manager.args, "clone_only", False):
                    UI.die(
                        "No compiled LDM Package (.ldmp) found in GitHub Releases. To clone the workspace code directly, please use: 'ldm clone <repository-url>'"
                    )

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

                    if (
                        is_github
                        and "GITHUB_TOKEN" not in os.environ
                        and "GITHUB_PAT" not in os.environ
                    ):
                        UI.info(
                            "Note: GITHUB_TOKEN/GITHUB_PAT environment variable is not set. If this is a private repository, cloning may fail."
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
                                    f"Guide: Please export a valid GITHUB_TOKEN or GITHUB_PAT: 'export GITHUB_PAT=your_pat'."
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
                    self.cmd_import(
                        str(temp_git_dir), is_init_from=is_init_from, is_internal=True
                    )
                finally:
                    self.manager.args.no_run = original_no_run
                    self.manager.args.backup_dir = original_backup_dir

                    if temp_git_dir.exists():
                        shutil.rmtree(temp_git_dir)

                # Boot stack if needed
                if not original_no_run:
                    self.manager.cmd_run(project_id=project_name, is_restart=True)

                return project_name

        # --- Delegate to ImportPipeline for local file/dir execution ---
        from ldm_core.pipelines.import_pipeline import (
            ImportPipeline,
            ImportPipelineContext,
        )

        project_name = getattr(self.manager.args, "project", None) or getattr(
            self.manager.args, "project_flag", None
        )

        context = ImportPipelineContext(
            manager=self.manager,
            source_path=source_path,
            project_name=project_name,
            is_init_from=is_init_from,
        )
        pipeline = ImportPipeline()
        pipeline.run(context)

        return context.get("project_name")

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

        if template_info.get("private"):
            from ldm_core.utils import get_github_token

            if not get_github_token():
                UI.die(
                    f"Template '{template_name}' requires a private repository.\n"
                    "Please authenticate using the GitHub CLI ('gh auth login') or set the GITHUB_PAT environment variable."
                )

        # Ensure project argument is set so import target matches
        self.manager.args.project = project_name
        self.manager.args.browser = True

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

        restored_from_pkg = (
            str(project_meta.get("restored_from_package", "false")).lower() == "true"
        )
        pkg_has_db = (
            str(project_meta.get("package_includes_database", "false")).lower()
            == "true"
        )
        if restored_from_pkg and pkg_has_db:
            UI.info(
                "Project was restored from LDM package snapshot. Skipping database seeding."
            )
        else:
            # 3. Apply fresh database seed
            UI.info(f"Seeding database for {tag} ({db_type}/{search_mode})...")
            paths = self.manager.setup_paths(project_path)
            if not self.manager.assets._fetch_seed(tag, db_type, search_mode, paths):
                UI.info(
                    "No pre-warmed seed applied. Liferay will initialize the database schema on first boot (this may take several minutes)."
                )

            # 4. Start stack and launch browser
            UI.info("Starting quickstart services stack...")
            self.manager.runtime.cmd_run(project_name)

        # 5. Share via tunnel if requested
        if share:
            UI.info("Exposing quickstart tunnel...")
            self.manager.share.cmd_start(project_name, subdomain=share_subdomain)

    def _rewrite_oauth_urls_in_zip(self, zip_path: Path, host_name: str, ext_name: str):
        if not host_name or host_name == "localhost":
            return

        import os
        import tempfile
        import zipfile

        from ldm_core.utils import safe_extract

        protocol = "https"
        external_url = f"{protocol}://{ext_name}.{host_name}"

        modified = False
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            try:
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    safe_extract(zip_ref, tmp_path)
            except Exception as e:
                UI.warning(f"Failed to extract zip for OAuth rewriting: {e}")
                return

            config_file = None
            is_json = False
            if (tmp_path / "client-extension.yaml").exists():
                config_file = tmp_path / "client-extension.yaml"
            else:
                json_files = list(tmp_path.glob("*.client-extension-config.json"))
                if json_files:
                    config_file = json_files[0]
                    is_json = True

            if not config_file:
                return

            try:
                content = config_file.read_text(encoding="utf-8")
                if is_json:
                    import json

                    data = json.loads(content)
                else:
                    data = yaml.safe_load(content)

                if not isinstance(data, dict):
                    return

                for _key, block in data.items():
                    if isinstance(block, dict) and block.get("type") in [
                        "oAuthApplicationUserAgent",
                        "oAuthApplicationHeadlessServer",
                    ]:
                        service_address = block.get(".serviceAddress", "")
                        if "localhost" in service_address:
                            block[".serviceAddress"] = f"{ext_name}.{host_name}"
                            modified = True

                        if (
                            block.get(".serviceScheme") == "http"
                            and protocol == "https"
                        ):
                            block[".serviceScheme"] = "https"
                            modified = True

                        hp_url = block.get("homePageURL", "")
                        if "localhost" in hp_url:
                            block["homePageURL"] = external_url
                            modified = True

                        redirects = block.get("redirectURIs", [])
                        if redirects and isinstance(redirects, list):
                            new_redirects = []
                            for uri in redirects:
                                if "localhost" in uri:
                                    new_uri = re.sub(
                                        r"https?://localhost:\d+", external_url, uri
                                    )
                                    new_redirects.append(new_uri)
                                    if new_uri != uri:
                                        modified = True
                                else:
                                    new_redirects.append(uri)
                            block["redirectURIs"] = new_redirects

                        ts = block.get("typeSettings", [])
                        if isinstance(ts, list):
                            for i, setting in enumerate(ts):
                                if (
                                    setting.startswith(".serviceAddress=")
                                    and "localhost" in setting
                                ):
                                    ts[i] = f".serviceAddress={ext_name}.{host_name}"
                                    modified = True
                                elif (
                                    setting.startswith(".serviceScheme=")
                                    and protocol == "https"
                                ):
                                    ts[i] = ".serviceScheme=https"
                                    modified = True
                                elif (
                                    setting.startswith("homePageURL=")
                                    and "localhost" in setting
                                ):
                                    ts[i] = f"homePageURL={external_url}"
                                    modified = True
                                elif (
                                    "redirectURIs=" in setting
                                    and "localhost" in setting
                                ):
                                    ts[i] = re.sub(
                                        r"https?://localhost:\d+", external_url, setting
                                    )
                                    modified = True

                if modified:
                    import time

                    current_ts = int(time.time() * 1000)
                    for _key, block in data.items():
                        if isinstance(block, dict) and "buildTimestamp" in block:
                            # Bump the timestamp to force Liferay to re-evaluate it over the snapshot DB
                            block["buildTimestamp"] = current_ts

                    if is_json:
                        import json

                        config_file.write_text(
                            json.dumps(data, indent=2),
                            encoding="utf-8",
                        )
                    else:

                        class NoAliasDumper(yaml.SafeDumper):
                            def ignore_aliases(self, data):
                                return True

                        config_file.write_text(
                            yaml.dump(data, Dumper=NoAliasDumper, sort_keys=False),
                            encoding="utf-8",
                        )

                    temp_zip_path = tmp_path / "repacked.zip"
                    with zipfile.ZipFile(
                        temp_zip_path, "w", zipfile.ZIP_DEFLATED
                    ) as new_zip:
                        for root, _dirs, files in os.walk(tmp_path):
                            for file in files:
                                if file == "repacked.zip":
                                    continue
                                file_path = Path(root) / file
                                arcname = file_path.relative_to(tmp_path)
                                new_zip.write(file_path, arcname)

                    # Atomically overwrite the original zip.  Writing to a
                    # temp file inside the same TemporaryDirectory and then
                    # calling Path.replace() ensures that an interruption
                    # (SIGINT, OOM, disk-full) never leaves the workspace in a
                    # state where the original archive is gone but the
                    # replacement has not yet landed.
                    temp_zip_path.replace(zip_path)
                    UI.detail(
                        f"  + Dynamically rewrote OAuth profile URLs in {zip_path.name}"
                    )

            except Exception as e:
                UI.warning(
                    f"Failed to modify client-extension.yaml for OAuth rewriting in {zip_path.name}: {e}"
                )

    def cmd_set_version(self, product_key):
        """Updates the workspace gradle.properties liferay.workspace.product version."""
        project_name = getattr(self.manager.args, "project", None)
        project_path = self.manager.detect_project_path(project_name)
        if not project_path or not project_path.exists():
            UI.die("Could not resolve project path.")
            return

        gradle_props = project_path / "gradle.properties"
        if not gradle_props.exists():
            gradle_props = project_path / "liferay" / "gradle.properties"

        if not gradle_props.exists():
            UI.die(
                f"Could not find a valid gradle.properties in the workspace: {project_path}"
            )
            return

        UI.heading(f"Updating Workspace Version for: {project_path.name}")
        content = gradle_props.read_text(encoding="utf-8")

        if "liferay.workspace.product" not in content:
            UI.die(
                f"The property 'liferay.workspace.product' was not found in {gradle_props.name}"
            )
            return

        # Update the property

        new_content = re.sub(
            r"liferay\.workspace\.product\s*=\s*[^\r\n]+",
            f"liferay.workspace.product={product_key}",
            content,
        )

        gradle_props.write_text(new_content, encoding="utf-8")

        # Verify compatibility
        UI.info(
            f"Updated liferay.workspace.product to {product_key} in {gradle_props.name}"
        )

        from ldm_core.utils import resolve_liferay_docker_tag

        resolved_tag, is_portal = resolve_liferay_docker_tag(product_key, self.manager)
        if not resolved_tag:
            UI.warning(
                f"Could not cleanly resolve a Docker tag for '{product_key}'. Falling back to stripping prefix."
            )
            tag = re.sub(r"^(dxp|portal)-", "", product_key)
        else:
            tag = resolved_tag

        # Update meta explicitly to trigger restart warnings if running
        meta = self.manager.read_meta(project_path)
        meta["tag"] = tag
        if resolved_tag:
            meta["portal"] = "true" if is_portal else "false"
        self.manager.write_meta(project_path, meta)

        UI.success(
            f"Successfully bumped workspace version to {product_key} (Mapped Tag: {tag})."
        )
        UI.info("To apply this upgrade to the running environment, execute:")
        print(f"    {UI.BYELLOW}ldm restart --upgrade-db{UI.COLOR_OFF}")
