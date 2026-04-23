import os
import re
import sys
import json
import time
import platform
import subprocess
import shutil
from pathlib import Path
from ldm_core.ui import UI
from ldm_core.constants import SCRIPT_DIR
from ldm_core.utils import get_actual_home


class BaseHandler:
    """Base mixin for LiferayManager containing shared core logic."""

    def __init__(self, args):
        self.args = args
        self.non_interactive = getattr(args, "non_interactive", False)
        self.verbose = getattr(args, "verbose", False)

    def _pre_flight_checks(self, host_name, port, ssl_enabled=False, meta=None):
        """Runs critical safety checks before starting containers."""
        root = Path(meta.get("root"))
        # 1. RAM Check
        mem_limit = meta.get("mem_limit") if meta else None
        self.check_ram(mem_limit=mem_limit)

        # 2. Hostname Check
        if host_name != "localhost":
            if not self.check_hostname(host_name):
                if self.non_interactive:
                    UI.die(f"Hostname resolution failed for '{host_name}'.")
                UI.warning(f"Hostname '{host_name}' does not resolve to an IP.")
                UI.info("LDM can try to fix this by adding an entry to /etc/hosts.")
                if UI.confirm("Add host entry? (Requires sudo)", "Y"):
                    self.run_command(["sudo", "ldm", "fix-hosts", host_name])

        # 3. Port Check
        resolved_ip = (
            self.get_resolved_ip(host_name) if host_name != "localhost" else "127.0.0.1"
        )
        if not self.check_port(resolved_ip, port):
            if self.non_interactive:
                UI.die(f"Port {port} is already in use on {resolved_ip}.")
            new_port = self.find_available_port(resolved_ip, port)
            UI.warning(f"Port {port} is in use. Using {new_port} instead.")
            port = new_port

        # 4. Registry Conflict Check
        project_name = meta.get("project_name") if meta else None
        if project_name and root:
            self.check_registry_collisions(project_name, root, host_name=host_name)

        return port

    def check_ram(self, mem_limit=None):
        """Verifies if the host has enough RAM allocated to Docker."""
        try:
            res = self.run_command(
                ["docker", "info", "--format", "{{.MemTotal}}"], check=False
            )
            if not res:
                return
            mem_total = int(res)
            # Default to 8GB if no limit specified
            min_required = 8 * 1024 * 1024 * 1024
            if mem_limit:
                # Convert 12g to bytes
                if "g" in mem_limit.lower():
                    min_required = (
                        int(mem_limit.lower().replace("g", "")) * 1024 * 1024 * 1024
                    )
                elif "m" in mem_limit.lower():
                    min_required = int(mem_limit.lower().replace("m", "")) * 1024 * 1024

            if mem_total < min_required:
                UI.warning(
                    f"Docker has less than {UI.format_size(min_required)} RAM allocated."
                )
                UI.info(
                    "Liferay might be slow or unstable. Increase RAM in Docker Desktop settings."
                )
        except Exception:
            pass

    def check_port(self, ip, port):
        """Checks if a port is available on a specific IP."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            try:
                s.bind((ip, int(port)))
                return True
            except (socket.error, OverflowError):
                return False

    def find_available_port(self, ip, start_port):
        """Finds the next available port starting from a given number."""
        port = int(start_port)
        while not self.check_port(ip, port):
            port += 1
            if port > 65535:
                UI.die("No available ports found.")
        return port

    def check_registry_collisions(self, project_name, project_root, host_name=None):
        """Checks if another project with the same name or hostname exists at a different path."""
        from ldm_core.constants import REGISTRY_FILE
        from ldm_core.utils import get_actual_home

        actual_home = get_actual_home()
        registry_path = actual_home / ".ldm" / REGISTRY_FILE
        if not registry_path.exists():
            return

        try:
            # Robust path resolution
            if isinstance(project_root, dict):
                current_root_path = Path(project_root.get("root", ".")).resolve()
            else:
                current_root_path = Path(project_root).resolve()

            registry = json.loads(registry_path.read_text())

            # 1. Check Project Name
            existing_path = registry.get(project_name)
            if existing_path:
                if isinstance(existing_path, dict):
                    existing_path = existing_path.get("path")

                abs_existing = str(Path(existing_path).resolve())
                abs_current = str(current_root_path)

                if abs_existing != abs_current:
                    UI.die(
                        f"Project collision: '{project_name}' is already registered at:\n"
                        f"  {UI.CYAN}{existing_path}{UI.COLOR_OFF}\n"
                        f"Current path:\n"
                        f"  {UI.CYAN}{abs_current}{UI.COLOR_OFF}\n\n"
                        f"Run {UI.BOLD}ldm down --delete{UI.COLOR_OFF} in the original folder to remove it."
                    )

            # 2. Check Hostname (if provided and not localhost)
            if host_name and host_name != "localhost":
                for name, data in registry.items():
                    if name == project_name:
                        continue

                    existing_host = None
                    existing_path = None
                    if isinstance(data, dict):
                        existing_host = data.get("host")
                        existing_path = data.get("path")

                    if existing_host == host_name:
                        abs_existing = str(Path(existing_path).resolve())
                        abs_current = str(current_root_path)

                        if abs_existing != abs_current:
                            UI.die(
                                f"Hostname collision: '{host_name}' is already registered for project '{name}' at:\n"
                                f"  {UI.CYAN}{existing_path}{UI.COLOR_OFF}\n\n"
                                f"Each project must have a unique Virtual Hostname."
                            )
        except Exception:
            pass

    def register_project(self, project_name, project_root, host_name=None):
        """Registers a project in the global registry."""
        from ldm_core.constants import REGISTRY_FILE
        from ldm_core.utils import get_actual_home, safe_write_text

        actual_home = get_actual_home()
        ldm_dir = actual_home / ".ldm"
        ldm_dir.mkdir(parents=True, exist_ok=True)
        registry_path = ldm_dir / REGISTRY_FILE

        registry = {}
        if registry_path.exists():
            try:
                registry = json.loads(registry_path.read_text())
            except Exception:
                pass

        # We store both path and host for better collision detection
        registry[project_name] = {
            "path": str(Path(project_root).resolve()),
            "host": host_name,
            "last_seen": time.time(),
        }

        try:
            safe_write_text(registry_path, json.dumps(registry, indent=4))
        except Exception as e:
            UI.warning(f"Failed to update registry: {e}")

    def unregister_project(self, project_name):
        """Removes a project from the global registry."""
        from ldm_core.constants import REGISTRY_FILE
        from ldm_core.utils import get_actual_home, safe_write_text

        actual_home = get_actual_home()
        registry_path = actual_home / ".ldm" / REGISTRY_FILE

        if not registry_path.exists():
            return

        try:
            registry = json.loads(registry_path.read_text())
            if project_name in registry:
                del registry[project_name]
                safe_write_text(registry_path, json.dumps(registry, indent=4))
        except Exception:
            pass

    def _check_java_version(self, expected="21"):
        """Verifies the system Java version."""
        java_bin = shutil.which("java")
        if not java_bin:
            return False
        try:
            # Bandit: B603 (subprocess_without_shell_equals_true) is a general warning.
            res = subprocess.run(  # nosec B603
                [java_bin, "-version"], capture_output=True, text=True, check=True
            )
            output = res.stderr

            match = re.search(rf'version\s+"({expected}\.[^"]+)"', output)
            if match:
                if self.verbose:
                    UI.info(f"Verified system Java version: {match.group(1)}")
                return True
            UI.error(f"System Java version mismatch. Expected JDK {expected}.")
            print(f"{UI.WHITE}Actual output:{UI.COLOR_OFF}\n{output}")
            return False
        except Exception as e:
            UI.error(f"Failed to verify Java version: {e}")
            return False

    def _check_gradle_java_version(self, gradlew_path, expected="21"):
        """Verifies the JVM version used by Gradle."""
        try:
            if platform.system().lower() != "windows":
                # Bandit: B103 (chmod 0o755) is safe for gradlew as it needs to be executable.
                try:
                    os.chmod(gradlew_path, 0o755)  # nosec B103
                except Exception:
                    pass
            # Bandit: B603 (subprocess_without_shell_equals_true) is a general warning.
            res = subprocess.run(  # nosec B603
                [str(gradlew_path), "-v"], capture_output=True, text=True, check=True
            )
            output = res.stdout
            match = re.search(rf"JVM:\s+({expected}\.[^\s]+)", output)
            if match:
                if self.verbose:
                    UI.info(f"Verified Gradle JVM version: {match.group(1)}")
                return True
            UI.error(f"Gradle JVM version mismatch. Expected JDK {expected}.")
            print(f"{UI.WHITE}Actual output:{UI.COLOR_OFF}\n{output}")
            return False
        except Exception as e:
            UI.error(f"Failed to verify Gradle JVM version: {e}")
            return False

    def read_meta(self, path):
        """Reads project metadata, supporting both modern and legacy filenames."""
        from ldm_core.utils import read_meta
        import os

        # If passed a dict, return it (already loaded)
        if isinstance(path, dict):
            return path
        # If passed a directory (as string or Path), look for metadata files
        try:
            p_str = str(path)
            if os.path.isdir(p_str):
                for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]:
                    f_path = os.path.join(p_str, f)
                    if os.path.exists(f_path):
                        return read_meta(Path(f_path))
                return {}
        except Exception:
            pass
        return read_meta(path)

    def write_meta(self, path, meta):
        """Writes project metadata, preserving the existing filename if possible."""
        from ldm_core.utils import write_meta

        # If passed a dict as path, skip (logic error in caller)
        if isinstance(path, dict):
            return

        try:
            p = Path(path)
            # Ensure we are targeting a file inside the directory
            if p.suffix not in [".meta", ""]:
                # Assuming it's already a file path
                target = p
            else:
                target = p / "meta"
                if (p / ".liferay-docker.meta").exists():
                    target = p / ".liferay-docker.meta"

            # Ensure the parent directory exists
            target.parent.mkdir(parents=True, exist_ok=True)
            write_meta(target, meta)
        except Exception:
            pass

    def run_command(self, cmd, check=True, cwd=None, env=None, capture_output=True):
        from ldm_core.utils import run_command

        # Safety: If cwd is a dictionary (common error in refactored handlers), extract root
        if isinstance(cwd, dict):
            cwd = str(cwd.get("root", "."))
        elif hasattr(cwd, "resolve"):  # Path-like
            cwd = str(cwd)

        return run_command(
            cmd,
            check=check,
            cwd=cwd,
            env=env,
            capture_output=capture_output,
            verbose=self.verbose,
        )

    def require_compose(self, root_path, silent=False):
        """Verifies that a docker-compose.yml file exists in the project root."""
        if not root_path or not (root_path / "docker-compose.yml").exists():
            if not silent:
                UI.error(f"docker-compose.yml not found in {root_path}")
            return False
        return True

    def get_container_status(self, container_name):
        """Returns the health or status of a container."""
        try:
            # First try to get health status
            health = self.run_command(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Health.Status}}",
                    container_name,
                ],
                check=False,
            )
            if health and health.strip():
                return health.strip().lower()

            # Fallback to general state
            status = self.run_command(
                ["docker", "inspect", "-f", "{{.State.Status}}", container_name],
                check=False,
            )
            if status and status.strip():
                return status.strip().lower()
        except Exception:
            pass
        return "unknown"

    def select_project_interactively(self, roots=None, heading="Select Project"):
        """Prompts the user to select a project from a list."""
        if self.non_interactive:
            return None

        project_roots = roots or self.find_dxp_roots()
        if not project_roots:
            return None

        # Fuzzy selection loop
        filter_str = ""
        while True:
            UI.heading(heading)
            filtered = [
                r
                for r in project_roots
                if not filter_str or filter_str.lower() in r["path"].name.lower()
            ]

            if not filtered:
                UI.warning(
                    f"No projects match '{filter_str}'. Clear filter (Enter) or 'q' to quit."
                )
                filter_str = ""
                continue

            for i, r in enumerate(filtered):
                print(
                    f"[{i + 1}] {r['path'].name} [{UI.CYAN}{r['version']}{UI.COLOR_OFF}]"
                )

            prompt = "\nSelect index, type to filter"
            if filter_str:
                prompt += f" (Current: {UI.CYAN}{filter_str}{UI.COLOR_OFF})"
            prompt += ", 's' to skip, or 'q' to quit"

            choice = UI.ask(prompt, "1" if len(filtered) == 1 else None)

            if not choice:
                if filter_str:
                    filter_str = ""
                    continue
                return None

            if str(choice).lower() == "q":
                sys.exit(0)
            if str(choice).lower() == "s":
                return None

            # If choice is numeric, it's an index selection
            if str(choice).isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(filtered):
                    return filtered[idx]
                UI.error(f"Invalid index: {choice}")
            else:
                # Treat as filter string
                filter_str = str(choice)

    def detect_project_path(self, project_id=None, for_init=False):
        """Resolves a project ID or path to a full filesystem path."""
        pid = project_id or getattr(self.args, "project", None)

        if not pid and Path.cwd().resolve() == Path.home().resolve():
            UI.warning(
                "You are running LDM from your Home directory. "
                "For better performance and to avoid noise, it is recommended to "
                "run LDM from a dedicated workspace folder (e.g. ~/ldm)."
            )

        if pid:
            p = Path(pid).expanduser().resolve()
            # Safety: exists() can raise PermissionError if the dir is 0700 root-owned
            try:
                # Support multiple metadata filenames
                has_meta = any(
                    (p / f).exists()
                    for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]
                )
                if has_meta:
                    return p
                # If for_init, we allow the path as long as it doesn't exist as a file
                if for_init:
                    if p.is_file():
                        UI.die(
                            f"Cannot initialize project: '{p}' already exists and is a file."
                        )
                    if p.parent.exists():
                        return p
            except PermissionError:
                # If we get permission denied, but the path exists, it's definitely the project
                if p.is_dir():
                    return p

            # Discovery Search paths
            search_dirs = []
            custom_workspace = os.environ.get("LDM_WORKSPACE")

            if custom_workspace:
                search_dirs.append(Path(custom_workspace).expanduser().resolve())
            else:
                search_dirs = [
                    Path.cwd(),
                    Path.home() / "ldm",
                ]
                # Only fallback to SCRIPT_DIR if we are NOT initializing a new project
                if not for_init:
                    search_dirs.append(SCRIPT_DIR)

                # MacOS specific fallback
                if Path("/Volumes/SanDisk/ldm").exists():
                    search_dirs.append(Path("/Volumes/SanDisk/ldm"))

            for s_dir in search_dirs:
                if not s_dir.exists():
                    continue
                p_test = s_dir / pid
                try:
                    # Support multiple metadata filenames
                    has_meta = any(
                        (p_test / f).exists()
                        for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]
                    )
                    if has_meta:
                        return p_test
                    if for_init and s_dir.exists() and not p_test.is_file():
                        return p_test
                except PermissionError:
                    if p_test.is_dir():
                        return p_test

            for s_dir in search_dirs:
                if not s_dir.exists():
                    continue
                try:
                    for item in s_dir.iterdir():
                        if item.is_dir() and not item.name.startswith("."):
                            # Support multiple metadata filenames
                            meta_file = None
                            for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]:
                                if (item / f).exists():
                                    meta_file = item / f
                                    break

                            try:
                                if meta_file:
                                    meta = self.read_meta(item)
                                    if meta.get("project_name") == pid:
                                        return item.resolve()
                            except PermissionError:
                                continue
                except Exception:
                    continue

            if not for_init:
                UI.die(
                    f"Project '{pid}' not found or missing metadata ('meta' or '.liferay-docker.meta')"
                )

        cwd = Path.cwd()
        # Check for multiple metadata filenames
        has_meta = any(
            (cwd / f).exists() for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]
        )
        if (
            (cwd / "files" / "portal-ext.properties").exists()
            or (cwd / "deploy").exists()
            or has_meta
        ):
            return cwd

        selection = self.select_project_interactively()
        return selection["path"] if selection else None

    def find_dxp_roots(self, search_dir=None):
        """Discovers LDM projects via utils."""
        from ldm_core.utils import find_dxp_roots

        return find_dxp_roots(search_dir)

    def parse_version(self, tag):
        """Parses a Liferay tag into a sortable tuple."""
        if not tag:
            return (0, 0, 0)
        parts = re.findall(r"\d+", str(tag))
        return tuple(map(int, parts))

    def get_common_dir(self, project_path=None):
        """Finds the 'common' directory by prioritizing CWD, Project Parent, then Binary Location."""
        # Safety: If passed a dict, extract root
        if isinstance(project_path, dict):
            project_path = project_path.get("root", ".")

        common_path = Path.cwd() / "common"
        if common_path.exists():
            return common_path

        if project_path:
            p_parent_common = Path(project_path).resolve().parent / "common"
            if p_parent_common.exists():
                return p_parent_common

        exe_path = Path(sys.argv[0]).resolve()
        is_source = exe_path.suffix.lower() == ".py"

        if is_source:
            return SCRIPT_DIR / "common"
        else:
            return Path.cwd() / "common"

    def setup_paths(self, project_path):
        """Initializes a standard path dictionary for a project."""
        # Safety: If passed a dict (common error in refactored handlers), extract root
        if isinstance(project_path, dict):
            project_path = project_path.get("root", ".")
        elif hasattr(project_path, "resolve"):  # Path-like
            project_path = str(project_path)

        root = Path(project_path).resolve()
        common_path = self.get_common_dir(root)
        return {
            "root": root,
            "common": common_path,
            "data": root / "data",
            "deploy": root / "deploy",
            "files": root / "files",
            "configs": root / "osgi" / "configs",
            "marketplace": root / "osgi" / "marketplace",
            "state": root / "osgi" / "state",
            "modules": root / "osgi" / "modules",
            "backups": root / "snapshots",
            "cx": root / "osgi" / "client-extensions",
            "routes": root / "routes",
            "scripts": root / "scripts",
            "logs": root / "logs",
            "log4j": root / "osgi" / "log4j",
            "portal_log4j": root / "osgi" / "portal-log4j",
            "ce_dir": root / "client-extensions",
            "compose": root / "docker-compose.yml",
        }

    def verify_runtime_environment(self, paths):
        """Verifies volume mounts and synchronizes permissions across the project root."""
        # Safety: If passed a direct path (common error in refactored handlers), initialize paths dict
        if not isinstance(paths, dict):
            paths = self.setup_paths(paths)

        root = paths["root"]
        system_type = platform.system().lower()

        if system_type in ["darwin", "linux"]:
            # Ensure the project root actually exists before we try to write to it
            try:
                if not root.exists():
                    root.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                if self.verbose:
                    UI.warning(f"Could not ensure root directory exists: {e}")

            if self.verbose:
                UI.info("Synchronizing directory permissions via Docker...")

            import uuid

            token_val = f"LDM_VERIFY_{uuid.uuid4().hex[:8]}"
            token_file = root / ".ldm_mount_check"
            try:
                token_file.write_text(token_val)
            except (PermissionError, OSError) as e:
                if self.verbose:
                    UI.debug(f"Could not create mount-check token (ignoring): {e}")
                token_val = "SKIP"

            current_uid = os.getuid() if hasattr(os, "getuid") else 1000
            current_gid = os.getgid() if hasattr(os, "getgid") else 1000

            try:
                # HARDENING: Mount the parent directory to ensure we can definitely
                # fix permissions and ownership for the project root directory itself.
                # Mount propagation can sometimes block chown on the mount point itself.
                parent = root.parent.resolve()
                rel_root = root.name

                # Aggressive sub-directory creation and reclamation
                # We specifically ensure the root directory (/workspace/{rel_root}) is chowned/chmodded
                docker_cmd = (
                    f"mkdir -p /workspace/{rel_root}/data /workspace/{rel_root}/deploy /workspace/{rel_root}/files "
                    f"/workspace/{rel_root}/osgi/state /workspace/{rel_root}/osgi/configs /workspace/{rel_root}/osgi/modules "
                    f"/workspace/{rel_root}/routes /workspace/{rel_root}/snapshots 2>/dev/null || true; "
                    f"chown -R {current_uid}:{current_gid} /workspace/{rel_root} 2>/dev/null || true; "
                    f"chmod -R 777 /workspace/{rel_root} 2>/dev/null || true; "
                    f"chmod 777 /workspace/{rel_root} 2>/dev/null || true; "
                )

                if token_val != "SKIP":
                    docker_cmd = (
                        f'if [ "$(cat /workspace/{rel_root}/.ldm_mount_check 2>/dev/null)" = "{token_val}" ]; then '
                        f"{docker_cmd}"
                        f"if touch /workspace/{rel_root}/.ldm_write_test 2>/dev/null; then echo 'OK'; else echo 'NO_WRITE'; fi; "
                        f"else echo 'FAIL'; fi"
                    )
                else:
                    # In SKIP mode, we just do our best and assume success if the command finishes
                    docker_cmd = f"{docker_cmd} echo 'OK'"

                verify_res = self.run_command(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{parent.as_posix()}:/workspace",
                        "alpine",
                        "sh",
                        "-c",
                        docker_cmd,
                    ]
                )

                if "OK" not in (verify_res or ""):
                    if "NO_WRITE" in (verify_res or ""):
                        UI.error("\n❌ FATAL: VOLUME MOUNT IS READ-ONLY")
                        UI.info(
                            f"{UI.BYELLOW}Reason:{UI.COLOR_OFF} Docker can see the files, but the 'liferay' user cannot write to: {root}"
                        )
                    else:
                        UI.error("\n❌ FATAL: VOLUME MOUNTING IS BROKEN")
                        UI.info(
                            f"{UI.BYELLOW}Reason:{UI.COLOR_OFF} Docker cannot see the files in: {root}"
                        )

                    if system_type == "darwin":
                        actual_home = Path.home()
                        try:
                            from ldm_core.utils import get_actual_home

                            actual_home = get_actual_home()
                        except ImportError:
                            pass

                        cert_dir = actual_home / "liferay-docker-certs"
                        mount_hint = self.get_colima_mount_flags([root, cert_dir])

                        UI.info(f"\n{UI.CYAN}To fix this, run:{UI.COLOR_OFF}")
                        UI.info("colima stop")
                        UI.info(
                            f"colima start {mount_hint} --vm-type=vz --mount-type=virtiofs"
                        )

                    sys.exit(1)

                if system_type == "darwin" and self.verbose:
                    UI.success("Volume mounts verified and permissions synchronized.")
            except Exception as e:
                if self.verbose:
                    UI.warning(f"Could not verify mounts automatically: {e}")
            finally:
                try:
                    if token_file.exists():
                        token_file.unlink()
                    write_test = root / ".ldm_write_test"
                    if write_test.exists():
                        write_test.unlink()
                except Exception:
                    pass
        else:
            pass

        for p_key in [
            "data",
            "deploy",
            "files",
            "state",
            "cx",
            "configs",
            "modules",
            "backups",
        ]:
            if p_key in paths:
                try:
                    paths[p_key].mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    if self.verbose:
                        UI.warning(f"Could not create directory {paths[p_key]}: {e}")

        pe_file = paths["files"] / "portal-ext.properties"
        if pe_file.exists() and pe_file.is_dir():
            UI.warning(f"Removing ghost directory at {pe_file}")
            self.safe_rmtree(pe_file)

        if not pe_file.exists():
            try:
                pe_file.touch()
            except Exception:
                pass

    def check_docker(self):
        """Verifies Docker accessibility."""
        try:
            docker_bin = shutil.which("docker")
            if not docker_bin:
                return False

            res = subprocess.run(
                [docker_bin, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                return True

            if hasattr(os, "getuid") and os.getuid() == 0:
                UI.error("\n❌ FATAL: RUNNING AS ROOT/SUDO IS PROHIBITED")
                if platform.system().lower() == "linux":
                    UI.info(
                        f"If you are using sudo because of Docker permissions, please run:\n"
                        f"{UI.CYAN}sudo usermod -aG docker $USER{UI.COLOR_OFF} and restart your terminal session.\n"
                    )
                sys.exit(1)

            return False
        except Exception:
            return False

    def migrate_layout(self, paths):
        """Ensures modern project directory structure and permissions."""
        # Safety: If passed a direct path (common error in refactored handlers), initialize paths dict
        if not isinstance(paths, dict):
            paths = self.setup_paths(paths)

        essential_paths = [
            "data",
            "deploy",
            "state",
            "marketplace",
            "files",
            "configs",
            "modules",
            "cx",
            "scripts",
            "routes",
            "logs",
            "log4j",
            "portal_log4j",
        ]
        for key in essential_paths:
            if key in paths:
                try:
                    paths[key].mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    if self.verbose:
                        UI.warning(
                            f"Could not ensure directory exists {paths[key]}: {e}"
                        )

        routes_base = paths["root"] / "routes" / "default" / "dxp"
        try:
            routes_base.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            if self.verbose:
                UI.warning(f"Could not ensure routes directory exists: {e}")

        if platform.system().lower() != "windows":
            try:
                os.chmod(str(paths["root"]), 0o777)  # nosec B103
                for key in essential_paths:
                    if key in paths and paths[key].exists():
                        os.chmod(str(paths[key]), 0o777)  # nosec B103
            except Exception:
                pass

    def get_resolved_ip(self, host_name):
        """Resolves a hostname to its IP address."""
        if not host_name or host_name == "localhost":
            return "127.0.0.1"
        try:
            import socket

            return socket.gethostbyname(host_name)
        except socket.gaierror:
            return None

    def check_hostname(self, host_name):
        """Verifies that the hostname resolves to the local machine, helping the user fix it if not."""
        if host_name == "localhost":
            return True

        resolved_ip = self.get_resolved_ip(host_name)
        if resolved_ip:
            return True

        UI.error(f"Hostname '{host_name}' does not resolve to any IP address.")
        UI.info(
            f"Please add it to your local hosts file or run '{UI.WHITE}ldm doctor --fix-hosts{UI.COLOR_OFF}'."
        )
        return False

    def validate_project_dns(self, project_id):
        """Verifies that the project's hostname and all active client extensions resolve correctly."""
        root = self.detect_project_path(project_id)
        if not root:
            return False, [], []

        paths = self.setup_paths(root)
        meta = self.read_meta(root)
        host_name = meta.get("host_name", "localhost")
        if host_name == "localhost":
            return True, [], []

        unresolved = []
        non_local = []

        # Get host's own primary IP for local check
        import socket

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0)
            try:
                # does not even have to be reachable
                s.connect(("10.254.254.254", 1))
                host_ip = s.getsockname()[0]
            except Exception:
                host_ip = "127.0.0.1"
            finally:
                s.close()
        except Exception:
            host_ip = "127.0.0.1"

        local_ips = ["127.0.0.1", "0.0.0.0", host_ip]  # nosec B104

        def check_host(h):
            ip = self.get_resolved_ip(h)
            if not ip:
                unresolved.append(h)
            elif ip not in local_ips:
                non_local.append((h, ip))

        check_host(host_name)

        if paths["cx"].exists():
            from ldm_core.handlers.workspace import WorkspaceHandler

            handler = WorkspaceHandler()
            handler.args = self.args
            extensions = handler.scan_client_extensions(
                paths["root"], paths["cx"], paths["ce_dir"]
            )
            for ext in extensions:
                if ext.get("deploy") and ext.get("has_load_balancer"):
                    ext_host = f"{ext['id']}.{host_name}"
                    check_host(ext_host)

        return len(unresolved) == 0 and len(non_local) == 0, unresolved, non_local

    def safe_rmtree(self, path):
        """Securely deletes a directory tree, handling potential Docker/Root permission issues."""
        if not path or not path.exists():
            return

        try:
            shutil.rmtree(path)
        except (PermissionError, OSError):
            parent = path.parent.resolve()

            # Harden for Colima/Lima:
            # We use standard /var/run/docker.sock to avoid 'operation not supported' errors
            # when mounting host paths into cleanup containers.
            self.run_command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{parent.as_posix()}:/parent",
                    "alpine",
                    "sh",
                    "-c",
                    f"chmod -R 777 /parent/{path.name} 2>/dev/null || true; rm -rf /parent/{path.name} 2>/dev/null || true",
                ],
                check=False,
            )

            # Fallback check
            if path.exists():
                try:
                    shutil.rmtree(path)
                except Exception:
                    pass

    def get_colima_mount_flags(self, paths):
        """Generates the necessary --mount flags for Colima based on project paths."""
        mounts = set()
        real_user = os.environ.get("USER") or os.environ.get("LOGNAME")

        for p in paths:
            abs_path = Path(p).resolve()
            parts = abs_path.parts

            if len(parts) >= 3 and parts[1] in ["Users", "Volumes"]:
                mount_point = os.path.join(parts[0], parts[1], parts[2])
                if parts[1] == "Users" and real_user and parts[2] == real_user:
                    mount_point = os.path.join(parts[0], parts[1], "$(whoami)")
            elif len(parts) >= 2:
                mount_point = os.path.join(parts[0], parts[1])
            else:
                mount_point = parts[0]

            mounts.add(f"--mount {mount_point}:w")

        return " ".join(sorted(list(mounts)))

    def get_resource_path(self, filename):
        """Resiliently locates internal resource files (supports source vs bundled)."""
        # 1. Check bundled package structure (site-packages/ldm_core/resources)
        path = SCRIPT_DIR / "ldm_core" / "resources" / filename
        if path.exists():
            return path

        # 2. Check source development structure (root/resources)
        path = SCRIPT_DIR / "resources" / filename
        if path.exists():
            return path

        return None

    def _refresh_man_symlink(self):
        """Ensures a stable symlink for the man page exists in ~/.ldm/man/man1/."""
        if platform.system().lower() == "windows":
            return

        try:
            man_source = self.get_resource_path("ldm.1")
            if not man_source:
                return

            home = get_actual_home()
            man_dir = home / ".ldm" / "man" / "man1"
            man_dir.mkdir(parents=True, exist_ok=True)
            man_link = man_dir / "ldm.1"

            if man_link.is_symlink() or man_link.exists():
                man_link.unlink()

            man_link.symlink_to(man_source)
        except Exception:
            # Silent fail for symlink refresh
            pass

    def cmd_completion(self, target_shell=None):
        """Displays instructions or outputs shellcode for enabling completion."""
        # Detect active shell if not provided
        active_shell = os.environ.get("SHELL", "").split("/")[-1].lower()
        if active_shell.endswith(".exe"):
            active_shell = active_shell[:-4]

        # Normalize pwsh to powershell for internal logic
        if active_shell == "pwsh":
            active_shell = "powershell"

        # Refresh man symlink so 'man ldm' setup is always ready
        self._refresh_man_symlink()

        # If target_shell is specifically requested via CLI (e.g. 'ldm completion zsh')
        # we MUST only output shellcode to stdout to avoid breaking 'eval'.
        if target_shell:
            target_shell = target_shell.lower()
            try:
                import argcomplete

                if target_shell == "zsh":
                    # We use the internal argcomplete shellcode generator
                    code = argcomplete.shellcode(["ldm"], shell="zsh")  # nosec B604
                    # Zsh requires compinit to support the 'compdef' command used by argcomplete
                    print("# LDM Zsh Completion Initialization")
                    print(
                        "(( $+functions[compdef] )) || { autoload -U compinit && compinit }"
                    )
                    print(code)
                    return
                elif target_shell == "bash":
                    print(
                        argcomplete.shellcode(["ldm"], shell="bash")  # nosec B604
                    )
                    return
                elif target_shell == "fish":
                    print(
                        argcomplete.shellcode(["ldm"], shell="fish")  # nosec B604
                    )
                    return
                elif target_shell == "powershell":
                    # PowerShell doesn't have native argcomplete support, so we provide a bridge script
                    print("# LDM PowerShell Completion Bridge")
                    print(
                        "if (-not (Get-Command ldm -ErrorAction SilentlyContinue)) { return }"
                    )
                    print("$scriptblock = {")
                    print(
                        "    param($commandName, $wordToComplete, $cursorPosition, $commandAst, $fakeBoundParameters)"
                    )
                    print("    $env:COMP_LINE = $commandAst.ToString()")
                    print("    $env:COMP_POINT = $cursorPosition")
                    print("    $env:_ARGCOMPLETE = 1")
                    print("    $results = & ldm 2>$null")
                    print("    $results | ForEach-Object {")
                    print(
                        "        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)"
                    )
                    print("    }")
                    print(
                        "    Remove-Item Env:COMP_LINE, Env:COMP_POINT, Env:_ARGCOMPLETE"
                    )
                    print("}")
                    print(
                        "Register-ArgumentCompleter -Native -CommandName ldm -ScriptBlock $scriptblock"
                    )
                    return
            except Exception as e:
                # If generation fails, we print the error to stderr so eval ignores it
                print(f"Error generating completion: {e}", file=sys.stderr)
                return

        UI.heading("LDM Shell Completion")
        shell = active_shell
        if shell not in ["bash", "zsh", "fish", "powershell"]:
            UI.info(
                f"Completion is currently optimized for bash, zsh, fish, and powershell. (Found: {shell})"
            )
            return

        UI.info(
            f"To enable tab-completion for {UI.BYELLOW}{shell}{UI.COLOR_OFF}, add this to your startup profile:"
        )

        if shell == "zsh":
            print('\n    eval "$(ldm completion zsh)"\n')
            profile = ".zshrc"
        elif shell == "bash":
            print('\n    eval "$(ldm completion bash)"\n')
            profile = ".bashrc"
        elif shell == "fish":
            print("\n    ldm completion fish | source\n")
            profile = "config.fish"
        elif shell == "powershell":
            print("\n    ldm completion powershell | Out-String | Invoke-Expression\n")
            profile = "Microsoft.PowerShell_profile.ps1"

        UI.info(
            f"To support native {UI.BOLD}man ldm{UI.COLOR_OFF}, add this to the same file:"
        )
        print('\n    export MANPATH="$MANPATH:$HOME/.ldm/man"\n')

        UI.info(
            f"You may need to restart your terminal or source your profile ({UI.CYAN}~/{profile}{UI.COLOR_OFF})"
        )
        print("for the changes to take effect.")

    def cmd_man(self):
        """Displays the ldm manual page."""
        self._refresh_man_symlink()
        man_path = self.get_resource_path("ldm.1")
        if not man_path:
            UI.die("Manual page 'ldm.1' not found in resources.")

        # On macOS/Linux, we can use 'man -l' to view a local file
        # Fallback to 'less' if 'man' is not found or fails
        try:
            import subprocess

            if platform.system().lower() != "windows":
                # Check if man supports -l (macOS and most Linux)
                res = subprocess.run(
                    ["man", "--help"], capture_output=True, text=True, check=False
                )
                if "-l" in res.stdout or "-l" in res.stderr:
                    subprocess.run(["man", "-l", str(man_path)])
                else:
                    # Fallback to less with roff processing if possible, or raw text
                    # We can use mandoc or groff if available
                    if shutil.which("mandoc"):
                        subprocess.run(
                            f"mandoc -Tutf8 {man_path} | less -R",
                            shell=True,  # nosec B602 B604
                        )
                    elif shutil.which("groff"):
                        subprocess.run(
                            f"groff -man -Tascii {man_path} | less -R",
                            shell=True,  # nosec B602 B604
                        )
                    else:
                        subprocess.run(["less", str(man_path)])
            else:
                # Windows fallback to notepad or similar
                subprocess.run(["notepad", str(man_path)])
        except Exception as e:
            UI.error(f"Failed to display manual: {e}")
            UI.info(f"You can view the raw manual file at: {man_path}")
