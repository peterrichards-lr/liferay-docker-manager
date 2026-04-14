import os
import re
import json
import time
import shutil
import platform
import tarfile
import zipfile
import threading
import sys
from pathlib import Path
from datetime import datetime
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE, SCRIPT_DIR
from ldm_core.utils import run_command, load_env_blacklist, is_env_var_blacklisted


class WorkspaceHandler:
    """Mixin for workspace management (import, monitor, scanning)."""

    def _parse_client_extension_yaml(self, content):
        info = {"type": None, "oauth_erc": None}
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
        return sorted(list(set(blacklist)))

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

    def _parse_lcp_json(self, content):
        info = {
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
                p.get("external") for p in info["ports"]
            )
            env = data.get("env", {})
            info["env"] = env
            if "LIFERAY_BATCH_OAUTH_APP_ERC" in env:
                info["oauth_erc"] = env["LIFERAY_BATCH_OAUTH_APP_ERC"]
        except Exception:
            pass
        return info

    def _scan_extension_metadata(self, folder_path=None, zip_ref=None):
        info = {
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
            for k in info.keys():
                if new_info.get(k) is not None:
                    if k == "has_load_balancer":
                        info[k] = info[k] or new_info[k]
                    elif k == "ports" and new_info[k]:
                        info[k].extend(new_info[k])
                    elif k == "loadBalancer" and new_info[k]:
                        if info[k] is None:
                            info[k] = {}
                        info[k].update(new_info[k])
                    else:
                        info[k] = new_info[k]
            if new_info.get("env"):
                info["env"].update(new_info["env"])

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
                    merge_info(self._parse_lcp_json(zip_ref.read(f).decode("utf-8")))
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
                merge_info(self._parse_lcp_json(lcp_file.read_text()))
            cfg_file = next(folder_path.glob("*client-extension-config.json"), None)
            if cfg_file:
                erc = self._parse_client_extension_config_json(cfg_file.read_text())
                if erc:
                    info["oauth_erc"] = erc
        return info

    def scan_client_extensions(self, root_dir, osgi_cx_dir, ce_build_dir):
        extensions = []
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

                # Copy to root first to expand
                shutil.copy2(item, root_zip_copy)

                with zipfile.ZipFile(root_zip_copy, "r") as zip_ref:
                    ext_info = self._scan_extension_metadata(zip_ref=zip_ref)
                    namelist = zip_ref.namelist()

                    if ext_info["type"] or any(
                        Path(f).name in ["client-extension.yaml", "LCP.json"]
                        for f in namelist
                    ):
                        if (
                            any(Path(f).name == "Dockerfile" for f in namelist)
                            and ext_info.get("kind") != "Job"
                        ):
                            if target_folder.exists():
                                self.safe_rmtree(target_folder)
                            target_folder.mkdir(parents=True)
                            from ldm_core.utils import safe_extract

                            safe_extract(zip_ref, target_folder)

                        # Move the original ZIP from root to OSGI for Liferay's scanner
                        dest_zip = osgi_cx_dir / item.name
                        if dest_zip.exists():
                            os.remove(dest_zip)
                        shutil.move(str(root_zip_copy), str(dest_zip))

                        extensions.append(
                            {
                                "name": item.stem.lower().replace("_", "-"),
                                "id": ext_info.get("id") or item.stem,
                                "path": target_folder,  # Build from root/client-extensions/
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
                    **ext_info,
                }
                existing = next((e for e in extensions if e["id"] == entry["id"]), None)
                if existing:
                    existing.update(entry)
                else:
                    extensions.append(entry)

        return extensions

    def scan_standalone_services(self, root_path):
        services = []
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
        global_pool = {
            k[4:]: v
            for k, v in os.environ.items()
            if k.upper().startswith("LDM_") and not is_env_var_blacklisted(k, blacklist)
        }
        global_pool.update(
            {
                k: v
                for k, v in os.environ.items()
                if any(k.startswith(p) for p in ["LXC_", "COM_LIFERAY_LXC_"])
                and not is_env_var_blacklisted(k, blacklist)
            }
        )

        if not target_id:
            res = [f"{k}={v}" for k, v in global_pool.items()]
            exts = self.scan_client_extensions(
                paths["root"], paths["cx"], paths["ce_dir"]
            )
            services = self.scan_standalone_services(paths["root"])
            for k, v in os.environ.items():
                if is_env_var_blacklisted(k, blacklist):
                    continue
                if k.upper().startswith("LIFERAY_") or any(
                    k.upper().startswith(e["id"].upper().replace("-", "_") + "_")
                    for e in exts + services
                ):
                    res.append(f"{k}={v}")
            return sorted(list(set(res)))

        prefix = target_id.upper().replace("-", "_") + "_"
        targeted = {
            k[len(prefix) :] if target_id.lower() != "liferay" else k: v
            for k, v in os.environ.items()
            if k.upper().startswith(prefix) and not is_env_var_blacklisted(k, blacklist)
        }
        return [f"{k}={v}" for k, v in {**global_pool, **targeted}.items()]

    def _hydrate_from_workspace(self, workspace_root, paths):
        """Initial scan and sync of artifacts from workspace to project."""
        UI.info("Scanning workspace for built artifacts...")

        # 1. Sync Client Extensions (ZIPs)
        ce_dir = workspace_root / "client-extensions"
        if ce_dir.exists():
            # Look in root and standard dist folders
            for dist_zip in list(ce_dir.glob("*.zip")) + list(
                ce_dir.glob("*/dist/*.zip")
            ):
                self._sync_cx_artifact(dist_zip, paths)

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
                        shutil.copy2(jar, paths["modules"] / jar.name)
                        UI.info(f"  + Synced {folder.capitalize()[:-1]}: {jar.name}")

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
                            shutil.copy2(zip_file, paths["deploy"] / zip_file.name)
                            UI.info(f"  + Synced Fragment: {zip_file.name}")
                        else:
                            # If it's a ZIP in fragments but not a fragment, try syncing as CX
                            self._sync_cx_artifact(zip_file, paths)
                except Exception:
                    pass

    def _sync_cx_artifact(self, zip_path, paths):
        """Internal helper for the mandatory 3-step CX sync sequence."""
        ce_source_truth = paths["root"] / "client-extensions"
        ce_source_truth.mkdir(parents=True, exist_ok=True)

        # Step 1: Copy ZIP to root client-extensions/
        root_zip_path = ce_source_truth / zip_path.name
        shutil.copy2(zip_path, root_zip_path)

        # Step 2: Expand ZIP in root for Docker builds
        try:
            with zipfile.ZipFile(root_zip_path, "r") as zip_ref:
                target_folder = ce_source_truth / zip_path.stem
                if target_folder.exists():
                    shutil.rmtree(target_folder)
                target_folder.mkdir(parents=True)
                from ldm_core.utils import safe_extract

                safe_extract(zip_ref, target_folder)
                UI.info(f"  + Synced & Expanded CX: {zip_path.name}")
        except Exception as e:
            UI.error(f"Failed to expand {zip_path.name}: {e}")

        # Step 3: Move original ZIP to osgi/client-extensions/ for Liferay
        dest_zip = paths["cx"] / zip_path.name
        if dest_zip.exists():
            os.remove(dest_zip)
        shutil.move(str(root_zip_path), str(dest_zip))

    def cmd_init_from(self, source_path):
        """Initialize project with a persistent link to a source workspace and start monitoring."""
        # 1. Perform a standard import (but we will keep the link)
        self.cmd_import(source_path, is_init_from=True)

        # 2. Immediately start monitoring
        self.cmd_monitor(source_path)

    def cmd_import(self, source_path, is_init_from=False):
        source = Path(source_path).resolve()
        temp_extract_dir = None

        try:
            if not source.exists():
                UI.die(f"Source path not found: {source}")
            if not self._check_java_version("21"):
                UI.die("Incorrect system Java version. LDM import requires JDK 21.")

            if source.is_file():
                if source.suffix.lower() not in [".zip", ".tgz", ".gz", ".tar"]:
                    UI.die(f"Unsupported source format: {source.suffix}")
                temp_extract_dir = (
                    Path.cwd()
                    / ".ldm_temp"
                    / f"import_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                )
                temp_extract_dir.mkdir(parents=True)
                UI.info("Extracting source archive...")
                from ldm_core.utils import safe_extract

                if source.suffix.lower() == ".zip":
                    with zipfile.ZipFile(source, "r") as z:
                        safe_extract(z, temp_extract_dir)
                else:
                    with tarfile.open(
                        source,
                        "r:gz" if source.suffix.lower() in [".tgz", ".gz"] else "r:",
                    ) as t:
                        safe_extract(t, temp_extract_dir)

                for r, d, f in os.walk(temp_extract_dir):
                    if (
                        Path(r) / "liferay" / "LCP.json"
                    ).exists() or "gradle.properties" in f:
                        source = Path(r)
                        break

            workspace_root = (
                source / "liferay"
                if (source / "liferay" / "LCP.json").exists()
                else source
            )
            is_cloud = (source / "liferay" / "LCP.json").exists()

            # Project Naming Logic (Standardized)
            project_name = getattr(self.args, "project", None) or getattr(
                self.args, "project_flag", None
            )
            if not project_name:
                project_name = source.name
                if self.non_interactive:
                    UI.info(f"Using default project name: {project_name}")
                else:
                    project_name = UI.ask("Project Name", project_name)

            project_path = self.detect_project_path(project_name, for_init=True)

            if project_path.exists():
                if self.non_interactive:
                    UI.info(
                        f"Project '{project_name}' exists. Overwriting in non-interactive mode."
                    )
                else:
                    ans = UI.ask(
                        f"Project '{project_name}' exists. Overwrite contents? (y/n/c/q)",
                        "N",
                    ).upper()
                    if ans == "C":
                        UI.info(f"Cleaning existing project directory: {project_path}")
                        self.safe_rmtree(project_path)
                    elif ans != "Y":
                        UI.die("Initialization aborted.")

            paths = self.setup_paths(project_path)
            for p in [
                v for v in paths.values() if isinstance(v, Path) and not v.suffix
            ]:
                p.mkdir(parents=True, exist_ok=True)

            # Fail Fast: Verify volume mounting before performing any sync/import work
            self.verify_runtime_environment(paths)

            if getattr(self.args, "build", False):
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
                    if not self._check_gradle_java_version(gradlew, "21"):
                        UI.die("Gradle requires JDK 21.")
                    if platform.system() != "Windows":
                        # Bandit: B103 (chmod 0o755) is safe for gradlew.
                        os.chmod(gradlew, 0o755)  # nosec B103
                    try:
                        UI.info(f"Executing clean build in {gradlew.parent}...")
                        run_command(
                            [str(gradlew), "clean", "build", "-x", "test"],
                            capture_output=False,
                            cwd=str(gradlew.parent),
                        )
                    except Exception as e:
                        UI.error(f"Build failed: {e}")
                        if self.non_interactive:
                            UI.die("Build failed in non-interactive mode. Aborting.")
                        if UI.ask("Continue anyway? (y/n/q)", "N").upper() != "Y":
                            sys.exit(1)
                else:
                    UI.warning("gradlew not found. Skipping build.")

            host_name = getattr(self.args, "host_name", "localhost")
            use_ssl = getattr(self.args, "ssl", host_name != "localhost")
            custom_env = {
                k: v
                for env_pair in (getattr(self.args, "env", None) or [])
                if "=" in env_pair
                for k, v in [env_pair.split("=", 1)]
            }
            if use_ssl:
                self.check_mkcert()

            project_meta = {
                "project_name": project_name,
                "container_name": project_name.replace(".", "-"),
                "port": str(getattr(self.args, "port", 8080)),
                "ssl": str(use_ssl),
                "ssl_port": "443",
                "host_name": host_name,
                "last_run": datetime.now().isoformat(),
                "mount_logs": str(getattr(self.args, "mount_logs", False)).lower(),
                "gogo_port": str(getattr(self.args, "gogo_port", "None")),
                "custom_env": json.dumps(custom_env),
                "db_type": getattr(self.args, "db", None),
                "workspace_path": str(source) if is_init_from else None,
            }

            gradle_props = workspace_root / "gradle.properties"
            if gradle_props.exists():
                for line in gradle_props.read_text().splitlines():
                    if "liferay.workspace.product" in line and "=" in line:
                        tag = re.sub(
                            r"^(dxp|portal)-", "", line.split("=", 1)[1].strip()
                        )
                        # Workspace product always wins
                        project_meta["tag"] = tag
                        self.args.tag = tag
                        UI.info(f"Extracted version: {tag}")
                        break

            if is_cloud:
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
                workspace_root / "configs" / getattr(self.args, "target_env", "local")
            )
            if config_src.exists():
                pe = config_src / "portal-ext.properties"
                if pe.exists():
                    shutil.copy2(pe, paths["files"] / "portal-ext.properties")
                    UI.success("Imported portal-ext.properties")
                osgi_src = config_src / "osgi" / "configs"
                if osgi_src.exists():
                    count = 0
                    for f in list(osgi_src.glob("*.config")) + list(
                        osgi_src.glob("*.cfg")
                    ):
                        shutil.copy2(f, paths["configs"] / f.name)
                        count += 1
                    if count > 0:
                        UI.success(f"Imported {count} OSGi configs.")

            def import_zips(search_base, label, target_dir):
                count = 0
                if not search_base.exists():
                    return 0

                # Check root of search_base
                root_zips = list(search_base.glob("*.zip"))

                # Check subdirectories
                ext_folders = [
                    f
                    for f in search_base.iterdir()
                    if f.is_dir() and not f.name.startswith(".")
                ]

                nested_zips = []
                for folder in ext_folders:
                    nested_zips.extend(list(folder.glob("dist/*.zip")))
                    nested_zips.extend(list(folder.glob("*.zip")))

                for z in list(set(root_zips + nested_zips)):
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
                    shutil.copy2(z, target_dir / z.name)
                    count += 1
                return count

            import_zips(
                workspace_root / "client-extensions", "Extension", paths["ce_dir"]
            )
            import_zips(workspace_root / "fragments", "Fragment", paths["ce_dir"])

            for search_folder in ["modules", "themes"]:
                base = workspace_root / search_folder
                if base.exists():
                    for root, dirs, files in os.walk(base):
                        if "build" in dirs:
                            libs = Path(root) / "build" / "libs"
                            if libs.exists():
                                for f in libs.glob("*.[jw]ar"):
                                    if not any(
                                        x in f.name.lower()
                                        for x in ["-sources", "-javadoc", "-tests"]
                                    ):
                                        shutil.copy2(f, paths["modules"] / f.name)

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
                            self.safe_rmtree(dest)
                        shutil.copytree(item, dest)

            backup_dir_path = getattr(self.args, "backup_dir", None)
            if backup_dir_path:
                backup_dir = Path(backup_dir_path).resolve()
                if backup_dir.exists():
                    self._restore_from_cloud_layout(backup_dir, paths, project_meta)

            self._hydrate_from_workspace(workspace_root, paths)

            self.write_meta(project_path / PROJECT_META_FILE, project_meta)
            UI.success(f"Project created at: {project_path}")
            if not getattr(self.args, "no_run", False):
                self.args.project = project_name
                from ldm_core.handlers.stack import StackHandler

                StackHandler.cmd_run(self, is_restart=True)
        finally:
            if temp_extract_dir:
                self.safe_rmtree(temp_extract_dir)

    def cmd_monitor(self, source_path=None):
        try:
            from watchdog.observers import Observer
            from watchdog.observers.polling import PollingObserver
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            UI.die("watchdog required: pip install watchdog")

        project_id = getattr(self.args, "project", None) or self.detect_project_path()
        if not project_id:
            UI.die(
                "No project specified and no project found in current directory. "
                "Use 'ldm monitor <project_name>' or navigate to a project folder."
            )

        paths = self.setup_paths(project_id)
        project_meta = self.read_meta(paths["root"] / PROJECT_META_FILE)

        if not source_path:
            source_path = project_meta.get("workspace_path")
            if not source_path:
                UI.die(
                    "No workspace path provided and project is not linked to a source."
                )
            UI.info(f"Using linked workspace: {source_path}")

        source = Path(source_path).resolve()
        workspace_root = (
            source / "liferay" if (source / "liferay" / "LCP.json").exists() else source
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
                        if self.manager.verbose:
                            UI.info(f"Monitor: Skipping deep build file: {p.name}")
                        return

                # Refined Filtering Logic:
                # 1. client-extensions/**/*.zip
                # 2. fragments/**/*.zip
                # 3. modules/*/build/libs/*.jar

                is_valid = False
                if p.suffix.lower() == ".zip":
                    if "client-extensions" in p.parts or "fragments" in p.parts:
                        is_valid = True
                elif p.suffix.lower() in [".jar", ".war"]:
                    if (
                        "modules" in p.parts
                        and "build" in p.parts
                        and "libs" in p.parts
                    ):
                        is_valid = True

                if is_valid:
                    if self.manager.verbose:
                        UI.info(f"Monitor: Detected valid artifact: {p.name}")
                    with self.lock:
                        self.pending_files.add(p)
                        if self.timer:
                            self.timer.cancel()
                        self.timer = threading.Timer(self.delay, self._process_pending)
                        self.timer.start()
                elif self.manager.verbose:
                    UI.info(f"Monitor: Ignoring non-artifact change: {p.name}")

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
                        UI.info(f"Syncing Module: {f.name}")
                        shutil.copy2(f, dest_path)

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
        is_mac = platform.system().lower() == "darwin"

        if is_mac:
            # Proactively increase file descriptor limits for this process
            try:
                import resource

                soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)

                # Try to set a generous limit (e.g., 4096)
                new_soft = min(hard, 4096)
                resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))

                if self.verbose:
                    UI.info(f"OS File Limits: Soft={new_soft}, Hard={hard}")
            except Exception:
                pass

            UI.info(
                "Using PollingObserver for macOS stability (avoids 'Too many open files')."
            )
            observer = PollingObserver()
        else:
            observer = Observer()

        UI.info("Scanning for workspace branches...")
        watch_targets = []
        allowed_branches = ["client-extensions", "modules", "fragments"]

        for branch in allowed_branches:
            target = workspace_root / branch
            if target.exists():
                watch_targets.append(target)
                UI.info(f"  + Watching: {branch}")

        if not watch_targets:
            watch_targets = [workspace_root]

        handler = WorkspaceEventHandler(
            self,
            workspace_root,
            paths,
            project_meta,
            float(getattr(self.args, "delay", 2.0)),
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
                        if not isinstance(observer, PollingObserver):
                            observer.stop()
                            observer = PollingObserver()
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
