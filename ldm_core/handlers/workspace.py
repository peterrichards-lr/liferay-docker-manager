import os
import re
import json
import time
import shutil
import platform
import subprocess
import tarfile
import zipfile
import gzip
import threading
from pathlib import Path
from datetime import datetime
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE, SCRIPT_DIR
from ldm_core.utils import run_command, load_env_blacklist, is_env_var_blacklisted

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    Observer = None
    FileSystemEventHandler = object


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
            "cpu": None,
            "memory": None,
            "ports": [],
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
            for k in ["cpu", "memory", "ports", "readinessProbe", "livenessProbe"]:
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
            "cpu": None,
            "memory": None,
            "ports": [],
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
        ce_build_dir.mkdir(parents=True, exist_ok=True)
        osgi_cx_dir.mkdir(parents=True, exist_ok=True)
        found_ids = set()

        for item in [i for i in ce_build_dir.iterdir() if i.suffix.lower() == ".zip"]:
            try:
                with zipfile.ZipFile(item, "r") as zip_ref:
                    ext_info = self._scan_extension_metadata(zip_ref=zip_ref)
                    namelist = zip_ref.namelist()
                    if ext_info["type"] or any(
                        Path(f).name in ["client-extension.yaml", "LCP.json"]
                        for f in namelist
                    ):
                        target_folder = ce_build_dir / item.stem
                        if (
                            any(Path(f).name == "Dockerfile" for f in namelist)
                            and ext_info.get("kind") != "Job"
                        ):
                            if target_folder.exists():
                                self.safe_rmtree(target_folder)
                            target_folder.mkdir(parents=True)
                            zip_ref.extractall(target_folder)
                        dest_zip = osgi_cx_dir / item.name
                        if dest_zip.exists():
                            os.remove(dest_zip)
                        shutil.move(str(item), str(dest_zip))
                        extensions.append(
                            {
                                "name": item.stem.lower().replace("_", "-"),
                                "id": ext_info.get("id") or item.stem,
                                **ext_info,
                            }
                        )
            except Exception as e:
                UI.error(f"Failed to process {item.name}: {e}")

        for item in [
            i
            for i in ce_build_dir.iterdir()
            if i.is_dir() and not i.name.startswith(".")
        ]:
            ext_info = self._scan_extension_metadata(folder_path=item)
            if ext_info["type"] or (item / "LCP.json").exists():
                found_ids.add(item.name)
                entry = {
                    "id": ext_info.get("id") or item.name,
                    "name": item.name.lower().replace("_", "-"),
                    "port": 8080,
                    **ext_info,
                }
                if (item / "Dockerfile").exists():
                    entry["path"] = item
                existing = next((e for e in extensions if e["id"] == entry["id"]), None)
                if existing:
                    existing.update(entry)
                else:
                    extensions.append(entry)

        for folder in [
            f
            for f in ce_build_dir.iterdir()
            if f.is_dir() and f.name not in found_ids and not f.name.startswith(".")
        ]:
            self.safe_rmtree(folder)
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
                    8080,
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

    def cmd_import(self, source_path):
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
                    SCRIPT_DIR
                    / "temp"
                    / f"import_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                )
                temp_extract_dir.mkdir(parents=True)
                UI.info("Extracting source archive...")
                if source.suffix.lower() == ".zip":
                    with zipfile.ZipFile(source, "r") as z:
                        z.extractall(temp_extract_dir)
                else:
                    with tarfile.open(
                        source,
                        "r:gz" if source.suffix.lower() in [".tgz", ".gz"] else "r:",
                    ) as t:
                        self.safe_extract(t, temp_extract_dir)

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
                if not self.non_interactive:
                    project_name = UI.ask("Project Name", project_name)

            project_path = SCRIPT_DIR / project_name

            if project_path.exists():
                if (
                    UI.ask(
                        f"Project '{project_name}' exists. Overwrite contents? (y/n/q)",
                        "N",
                    ).upper()
                    != "Y"
                ):
                    UI.die("Initialization aborted.")

            paths = self.setup_paths(project_path)
            for p in [
                v for v in paths.values() if isinstance(v, Path) and not v.suffix
            ]:
                p.mkdir(parents=True, exist_ok=True)

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
                        os.chmod(gradlew, 0o755)
                    try:
                        UI.info(f"Executing clean build in {gradlew.parent}...")
                        run_command(
                            [str(gradlew), "clean", "build", "-x", "test"],
                            capture_output=False,
                            cwd=str(gradlew.parent),
                        )
                    except Exception as e:
                        UI.error(f"Build failed: {e}")
                        if UI.ask("Continue anyway? (y/n/q)", "N").upper() != "Y":
                            import sys

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
                "workspace_path": str(source) if not temp_extract_dir else None,
            }

            gradle_props = workspace_root / "gradle.properties"
            if gradle_props.exists():
                for line in gradle_props.read_text().splitlines():
                    if "liferay.workspace.product" in line and "=" in line:
                        tag = re.sub(
                            r"^(dxp|portal)-", "", line.split("=", 1)[1].strip()
                        )
                        project_meta["tag"] = tag
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
                for ext_folder in [
                    f
                    for f in search_base.iterdir()
                    if f.is_dir() and not f.name.startswith(".")
                ]:
                    dist_dir = ext_folder / "dist"
                    zips = list(dist_dir.glob("*.zip")) if dist_dir.exists() else []
                    for z in zips:
                        if label == "Fragment":
                            try:
                                with zipfile.ZipFile(z, "r") as zip_ref:
                                    if (
                                        "liferay-deploy-fragments.json"
                                        not in zip_ref.namelist()
                                    ):
                                        UI.error(
                                            f"{ext_folder.name} missing fragment descriptor."
                                        )
                                        continue
                            except Exception:
                                UI.error(f"{ext_folder.name} corrupt.")
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
                    vol = backup_dir / "volume.tgz"
                    if vol.exists():
                        UI.info("Restoring volume...")
                        with tarfile.open(vol, "r:gz") as tar:
                            self.safe_extract(tar, paths["data"])
                    db_dump = backup_dir / "database.gz"
                    if db_dump.exists():
                        UI.info("Detecting database dialect from dump...")
                        db_type = None
                        try:
                            with gzip.open(
                                db_dump, "rt", encoding="utf-8", errors="ignore"
                            ) as f:
                                head = "".join([f.readline() for _ in range(50)])
                                if "PostgreSQL" in head:
                                    db_type = "postgresql"
                                elif "MySQL" in head or "MariaDB" in head:
                                    db_type = "mysql"
                        except Exception:
                            pass

                        if db_type:
                            UI.info(
                                f"Detected {db_type} dump. Preparing orchestrated restore..."
                            )
                            project_meta["db_type"] = db_type
                            project_meta["db_name"] = "lportal"
                            project_meta["db_user"] = "liferay"
                            project_meta["db_pass"] = "liferay"
                            self.write_meta(
                                project_path / PROJECT_META_FILE, project_meta
                            )

                            self.sync_stack(
                                paths, project_meta, show_summary=False, no_up=True
                            )
                            db_container = f"{project_meta['container_name']}-db"

                            UI.info(f"Starting database container: {db_container}...")
                            run_command(
                                ["docker", "compose", "up", "-d", "db"],
                                cwd=str(project_path),
                            )

                            UI.info("Waiting for database service to become ready...")
                            start_time, db_ready = time.time(), False
                            while time.time() - start_time < 60:
                                status = run_command(
                                    [
                                        "docker",
                                        "inspect",
                                        "-f",
                                        "{{.State.Health.Status}}",
                                        db_container,
                                    ],
                                    check=False,
                                )
                                if status == "healthy":
                                    db_ready = True
                                    break

                                if db_type == "postgresql":
                                    res = run_command(
                                        [
                                            "docker",
                                            "exec",
                                            db_container,
                                            "pg_isready",
                                            "-U",
                                            "liferay",
                                            "-d",
                                            "lportal",
                                        ],
                                        check=False,
                                    )
                                    if res and "accepting connections" in res:
                                        db_ready = True
                                        break
                                else:
                                    res = run_command(
                                        [
                                            "docker",
                                            "exec",
                                            db_container,
                                            "mysqladmin",
                                            "ping",
                                            "-h",
                                            "localhost",
                                            "-u",
                                            "liferay",
                                            "-pliferay",
                                        ],
                                        check=False,
                                    )
                                    if res and "mysqld is alive" in res:
                                        db_ready = True
                                        break
                                time.sleep(2)

                            if db_ready:
                                if db_type == "postgresql":
                                    UI.info(
                                        "Creating compatibility roles for Cloud restoration..."
                                    )
                                    run_command(
                                        [
                                            "docker",
                                            "exec",
                                            db_container,
                                            "psql",
                                            "-U",
                                            "liferay",
                                            "-d",
                                            "lportal",
                                            "-c",
                                            "CREATE ROLE cloudsqlsuperuser;",
                                        ],
                                        check=False,
                                    )

                                UI.info("Streaming database dump into container...")
                                try:
                                    cmd = (
                                        f"gunzip -c {db_dump} | docker exec -i {db_container} "
                                        + (
                                            "psql -U liferay -d lportal"
                                            if db_type == "postgresql"
                                            else "mysql -u liferay -pliferay lportal"
                                        )
                                    )
                                    stdout = (
                                        None if self.verbose else subprocess.DEVNULL
                                    )
                                    subprocess.run(
                                        cmd,
                                        shell=True,
                                        check=True,
                                        stdout=stdout,
                                        stderr=(
                                            None if self.verbose else subprocess.STDOUT
                                        ),
                                    )
                                    UI.success("Database restored.")
                                except Exception as e:
                                    UI.error(f"Database restore failed: {e}")
                            else:
                                UI.error(
                                    "Database container failed to become ready. Restore skipped."
                                )
                        else:
                            UI.warning(
                                "Could not determine database dialect. Restore skipped."
                            )

            self.write_meta(project_path / PROJECT_META_FILE, project_meta)
            UI.success(f"Project created at: {project_path}")
            if not getattr(self.args, "no_run", False):
                self.args.project = str(project_path)
                self.cmd_run(is_restart=True)
        finally:
            if temp_extract_dir:
                self.safe_rmtree(temp_extract_dir)

    def cmd_monitor(self, source_path):
        if not Observer:
            UI.die("watchdog required: pip install watchdog")
        project_id = getattr(self.args, "project", None) or self.detect_project_path()
        paths = self.setup_paths(project_id)
        project_meta = self.read_meta(paths["root"] / PROJECT_META_FILE)
        if not source_path:
            source_path = project_meta.get("workspace_path")
            if not source_path:
                UI.die("No workspace path provided.")
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
                ) = manager, workspace_root, paths, project_meta, delay
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
                if (p.suffix.lower() == ".zip" and p.parent.name == "dist") or (
                    p.suffix.lower() in [".jar", ".war"]
                    and p.parent.name == "libs"
                    and p.parent.parent.name == "build"
                ):
                    with self.lock:
                        self.pending_files.add(p)
                        if self.timer:
                            self.timer.cancel()
                        self.timer = threading.Timer(self.delay, self._process_pending)
                        self.timer.start()

            def _process_pending(self):
                with self.lock:
                    files, self.pending_files, self.timer = (
                        list(self.pending_files),
                        set(),
                        None,
                    )
                if not files:
                    return
                for f in files:
                    dest = (
                        self.paths["ce_dir"] / f.name
                        if f.suffix.lower() == ".zip"
                        else self.paths["modules"] / f.name
                    )
                    UI.info(f"Syncing: {f.name}")
                    shutil.copy2(f, dest)
                self.manager.sync_stack(
                    self.paths, self.project_meta, show_summary=False
                )
                UI.success("Deployment complete.")

        observer = Observer()
        observer.schedule(
            WorkspaceEventHandler(
                self,
                workspace_root,
                paths,
                project_meta,
                float(getattr(self.args, "delay", 2.0)),
            ),
            str(workspace_root),
            recursive=True,
        )
        observer.start()
        try:
            UI.info("Watching for changes (Press Ctrl+C to stop)...")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
            UI.info("Monitor stopped.")
        observer.join()
