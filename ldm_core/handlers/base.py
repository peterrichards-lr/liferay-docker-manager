import os
import re
import sys
import platform
import subprocess
import shutil
import time
import socket
from pathlib import Path
from datetime import datetime
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE, SCRIPT_DIR
from ldm_core.utils import run_command, is_within_root


class BaseHandler:
    """Base mixin for LiferayManager containing shared core logic."""

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
                os.chmod(gradlew_path, 0o755)  # nosec B103
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

    def select_project_interactively(self, roots=None, heading="Select Project"):
        """Prompts the user to select a project from a list."""
        if self.non_interactive:
            return None

        project_roots = roots or self.find_dxp_roots()
        if not project_roots:
            return None

        UI.heading(heading)
        for i, r in enumerate(project_roots):
            print(f"[{i + 1}] {r['path'].name} [{UI.CYAN}{r['version']}{UI.COLOR_OFF}]")

        choice = UI.ask("Select project index, 's' to skip, or 'q' to quit", "1")
        if choice.lower() == "q":
            sys.exit(0)

        if choice.lower() == "s":
            return None

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(project_roots):
                return project_roots[idx]
        except (ValueError, IndexError):
            pass
        return None

    def get_running_projects(self):
        """Returns a list of project roots that have at least one running container."""
        all_roots = self.find_dxp_roots()
        running_roots = []
        for root in all_roots:
            p_path = root["path"]
            meta = self.read_meta(p_path / PROJECT_META_FILE)
            p_id = meta.get("container_name") or p_path.name
            # Check if any container for this project is running
            running = run_command(
                [
                    "docker",
                    "ps",
                    "-q",
                    "--filter",
                    f"label=com.liferay.ldm.project={p_id}",
                    "--filter",
                    "status=running",
                ],
                check=False,
            )
            if running:
                running_roots.append(root)
        return running_roots

    def require_compose(self, root_path, silent=False):
        """Verifies that a docker-compose.yml file exists in the project root."""
        if not root_path or not (root_path / "docker-compose.yml").exists():
            if not silent:
                UI.error(f"docker-compose.yml not found in {root_path}")
            return False
        return True

    def detect_project_path(self, project_id=None, for_init=False):
        """Resolves a project ID or path to a full filesystem path."""
        pid = project_id or getattr(self.args, "project", None)

        # Noise Prevention: Warn if running from Home without a project ID
        if not pid and Path.cwd().resolve() == Path.home().resolve():
            UI.warning(
                "You are running LDM from your Home directory. "
                "For better performance and to avoid noise, it is recommended to "
                "run LDM from a dedicated workspace folder (e.g. ~/ldm)."
            )

        if pid:
            # 1. Direct path check (Absolute or Relative)
            p = Path(pid).expanduser().resolve()
            if (p / PROJECT_META_FILE).exists() or (for_init and p.parent.exists()):
                return p

            # 2. Check in global workspace locations
            search_dirs = [Path.cwd(), SCRIPT_DIR]

            # Include custom workspace if set
            custom_workspace = os.environ.get("LDM_WORKSPACE")
            if custom_workspace:
                search_dirs.append(Path(custom_workspace))

            # Common default locations
            for common in [Path.home() / "ldm", Path("/Volumes/SanDisk/ldm")]:
                if common.exists() and common.is_dir():
                    search_dirs.append(common)

            # --- A. Exact Match in any search dir ---
            for s_dir in search_dirs:
                if not s_dir.exists():
                    continue
                p_test = s_dir / pid
                if (p_test / PROJECT_META_FILE).exists() or (
                    for_init and s_dir.exists()
                ):
                    return p_test.resolve()

            # --- B. Deep search by project_name in metadata ---
            seen_dirs = set()
            for s_dir in search_dirs:
                abs_s_dir = s_dir.resolve()
                if abs_s_dir in seen_dirs:
                    continue
                seen_dirs.add(abs_s_dir)

                if not s_dir.exists():
                    continue
                try:
                    for item in s_dir.iterdir():
                        if item.is_dir() and not item.name.startswith("."):
                            meta_file = item / PROJECT_META_FILE
                            if meta_file.exists():
                                meta = self.read_meta(meta_file)
                                if meta.get("project_name") == pid:
                                    return item.resolve()
                except Exception:
                    continue

            if not for_init:
                UI.die(f"Project '{pid}' not found or missing {PROJECT_META_FILE}")

        # Fall back to CWD detection for existing projects
        cwd = Path.cwd()
        if (
            (cwd / "files" / "portal-ext.properties").exists()
            or (cwd / "deploy").exists()
            or (cwd / PROJECT_META_FILE).exists()
        ):
            return cwd

        # Interactive Fallback
        selection = self.select_project_interactively()
        return selection["path"] if selection else None

    def parse_version(self, tag):
        """Parses a Liferay tag (YYYY.qX.N) into a sortable tuple."""
        if not tag:
            return (0, 0, 0)
        match = re.match(r"^(\d{4})\.q([1-4])(?:\.(\d+))?", tag)
        if match:
            year = int(match.group(1))
            quarter = int(match.group(2))
            patch = int(match.group(3)) if match.group(3) else 0
            return (year, quarter, patch)
        return (0, 0, 0)

    def find_dxp_roots(self, search_dir=None):
        """Discovers LDM projects in the target directory by looking for metadata or specific structure."""
        search_dirs = []
        if search_dir:
            search_dirs.append(Path(search_dir))
        else:
            search_dirs.append(Path.cwd())
            # Include custom workspace if set
            custom_workspace = os.environ.get("LDM_WORKSPACE")
            if custom_workspace:
                search_dirs.append(Path(custom_workspace))
            # Common default locations
            for common in [Path.home() / "ldm", Path("/Volumes/SanDisk/ldm")]:
                if common.exists() and common.is_dir():
                    search_dirs.append(common)

        roots = []
        seen_paths = set()

        for s_dir in search_dirs:
            if not s_dir.exists() or not s_dir.is_dir():
                continue

            # Security: If we are in the user's home directory, we should be extremely strict
            # to avoid scanning thousands of unrelated developer folders.
            is_home = s_dir.resolve() == Path.home().resolve()

            try:
                for item in s_dir.iterdir():
                    if item.is_dir() and not item.name.startswith("."):
                        abs_path = item.resolve()
                        if abs_path in seen_paths:
                            continue

                        # A directory is a project IF:
                        # 1. It has the official LDM metadata file (preferred)
                        # 2. It has BOTH 'files' and 'deploy' folders (legacy/scaffolded)
                        # 3. If in HOME, we ONLY accept the metadata file to prevent noise.
                        has_meta = (item / PROJECT_META_FILE).exists()
                        has_structure = (item / "files").exists() and (
                            item / "deploy"
                        ).exists()

                        if has_meta or (not is_home and has_structure):
                            meta = self.read_meta(item / PROJECT_META_FILE)
                            version = meta.get("tag") or "unknown"
                            roots.append({"path": item, "version": version})
                            seen_paths.add(abs_path)
            except Exception:
                continue

        return sorted(roots, key=lambda x: x["path"].name)

    def get_common_dir(self, project_path=None):
        """Finds the 'common' directory by prioritizing CWD, Project Parent, then Binary Location."""
        # 1. Prioritize Current Working Directory (if it exists)
        common_path = Path.cwd() / "common"
        if common_path.exists():
            return common_path

        # 2. Check Project Parent (if running from within a project folder)
        if project_path:
            p_parent_common = Path(project_path).resolve().parent / "common"
            if p_parent_common.exists():
                return p_parent_common

        # 3. Fallback logic
        exe_path = Path(sys.argv[0]).resolve()
        is_source = exe_path.suffix.lower() == ".py"

        if is_source:
            # For source installs (dev), use the repo's common folder
            return SCRIPT_DIR / "common"
        else:
            # For binary installs, we always want the common folder in the CWD
            # unless one was specifically found above. This prevents Permission Denied
            # errors in /usr/local/bin.
            return Path.cwd() / "common"

    def setup_paths(self, root_path):
        root = Path(root_path).resolve()
        common_path = self.get_common_dir(root)

        return {
            "root": root,
            "common": common_path,
            "deploy": root / "deploy",
            "data": root / "data",
            "scripts": root / "scripts",
            "files": root / "files",
            "cx": root / "osgi" / "client-extensions",
            "configs": root / "osgi" / "configs",
            "marketplace": root / "osgi" / "marketplace",
            "state": root / "osgi" / "state",
            "modules": root / "osgi" / "modules",
            "backups": root / "snapshots",
            "compose": root / "docker-compose.yml",
            "ce_dir": root / "client-extensions",
            "routes": root / "routes",
            "logs": root / "logs",
            "log4j": root / "osgi" / "log4j",
            "portal_log4j": root / "osgi" / "portal-log4j",
        }

    def migrate_layout(self, paths):
        """Ensures project structure is consistent with current LDM version."""
        # Ensure core directories exist
        essential_paths = [
            "files",
            "deploy",
            "configs",
            "modules",
            "cx",
            "data",
            "ce_dir",
            "marketplace",
            "scripts",
            "routes",
            "logs",
            "log4j",
            "portal_log4j",
        ]
        for key in essential_paths:
            if key in paths:
                paths[key].mkdir(parents=True, exist_ok=True)
                try:
                    # Bandit: B103 (chmod 777) is used here to ensure the Liferay container
                    # (running as UID 1000) has write access to host-mounted volumes
                    # on macOS/Windows environments.
                    os.chmod(paths[key], 0o777)  # nosec B103
                except Exception:
                    pass

        # Specific subfolders needed by Liferay logic
        if "marketplace" in paths:
            override_dir = paths["marketplace"] / "override"
            override_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(override_dir, 0o777)  # nosec B103
            except Exception:
                pass

        if "routes" in paths:
            try:
                # Ensure the base routes dir is 777
                routes_dir = paths["routes"]
                routes_dir.mkdir(parents=True, exist_ok=True)
                os.chmod(routes_dir, 0o777)  # nosec B103

                # Create default/dxp subfolder which Liferay specifically tries to write to
                # We must ensure EVERY level is writable by the container (UID 1000)
                default_dir = routes_dir / "default"
                default_dir.mkdir(parents=True, exist_ok=True)
                os.chmod(default_dir, 0o777)  # nosec B103

                dxp_routes = default_dir / "dxp"
                dxp_routes.mkdir(parents=True, exist_ok=True)
                os.chmod(dxp_routes, 0o777)  # nosec B103
            except Exception:
                pass

        # 1. Migrate legacy 'osgi/configs' if found in root (cleanup)
        legacy_configs = paths["root"] / "configs"
        if (
            legacy_configs.exists()
            and legacy_configs.is_dir()
            and legacy_configs.resolve() != paths["configs"].resolve()
        ):
            UI.info("Migrating legacy configs...")
            for f in legacy_configs.iterdir():
                dest = paths["configs"] / f.name
                if not dest.exists():
                    shutil.move(str(f), str(dest))
            shutil.rmtree(legacy_configs)

    def read_meta(self, path):
        meta = {}
        if not path.exists():
            return meta
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if v == "None":
                        v = None
                    elif v == "True":
                        v = True
                    elif v == "False":
                        v = False
                    meta[k] = v
        return meta

    def write_meta(self, path, meta):
        path = Path(path)
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            f.write(f"# Generated by LDM ({datetime.now().isoformat()})\n")
            for k, v in sorted(meta.items()):
                if v is not None:
                    f.write(f"{k}={v}\n")
        os.replace(tmp_path, path)

    def safe_rmtree(self, path, root=None):
        path = Path(path).resolve()
        if not path.exists():
            return True
        if root and not is_within_root(path, root):
            UI.error(
                f"Safety violation: Attempted to delete {path} outside root {root}"
            )
            return False
        for i in range(5):
            try:
                shutil.rmtree(path)
                return True
            except Exception as e:
                if i == 4:
                    UI.error(f"Failed to delete {path}: {e}")
                    return False
                time.sleep(2)
        return False

    def check_docker(self):
        try:
            # We use subprocess directly to capture the raw error message if it fails
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

            if res.stderr:
                # Extract the first line of the error to avoid overwhelming the user
                err = res.stderr.splitlines()[0]
                UI.error(f"Docker Error: {err}")
            return False
        except Exception as e:
            if self.verbose:
                UI.error(f"Docker Exception: {e}")
            return False

    def get_resolved_ip(self, host_name):
        if not host_name or host_name == "localhost":
            return "127.0.0.1"
        try:
            return socket.gethostbyname(host_name)
        except socket.gaierror:
            return None

    def get_colima_mount_flags(self, paths):
        """Calculates the unique set of Colima mount flags needed for the given paths."""
        import getpass

        mounts = set()
        real_user = (
            os.environ.get("SUDO_USER") or os.environ.get("USER") or getpass.getuser()
        )

        for p in paths:
            abs_path = Path(p).resolve()
            parts = abs_path.parts

            # Logic: We want to mount the first 3 parts for /Users or /Volumes,
            # or the first 2 parts for other root-level directories.
            # Example: /Users/peter/repos -> /Users/peter
            # Example: /Volumes/SanDisk/projects -> /Volumes/SanDisk
            # Example: /opt/liferay -> /opt

            if len(parts) >= 3 and parts[1] in ["Users", "Volumes"]:
                mount_point = os.path.join(parts[0], parts[1], parts[2])
                # Mask the actual username with $(whoami) for the CLI hint
                if parts[1] == "Users" and real_user and parts[2] == real_user:
                    mount_point = os.path.join(parts[0], parts[1], "$(whoami)")
            elif len(parts) >= 2:
                mount_point = os.path.join(parts[0], parts[1])
            else:
                mount_point = parts[0]

            mounts.add(f"--mount {mount_point}:w")

        return " ".join(sorted(list(mounts)))

    def verify_runtime_environment(self, paths):
        """Verifies volume mounts and synchronizes permissions across the project root."""
        root = paths["root"]

        # Proactively create all required standard directories to prevent Docker "Ghost Mounts"
        # and eliminate chmod warnings during the permission fix phase.
        for p_key in ["data", "deploy", "files", "state", "cx", "configs", "modules"]:
            if p_key in paths:
                paths[p_key].mkdir(parents=True, exist_ok=True)

        # Ensure portal-ext.properties is a file, not a ghost directory
        pe_file = paths["files"] / "portal-ext.properties"
        if pe_file.exists() and pe_file.is_dir():
            UI.warning(f"Removing ghost directory at {pe_file}")
            shutil.rmtree(pe_file)

        if not pe_file.exists():
            pe_file.touch()

        if platform.system().lower() == "darwin":
            UI.info("Verifying volume mounts and directory permissions...")

            # Ensure the project root actually exists before we try to write to it
            root.mkdir(parents=True, exist_ok=True)

            # Create a unique mount-check token to avoid cache hits
            import uuid

            token_val = f"LDM_VERIFY_{uuid.uuid4().hex[:8]}"
            token_file = root / ".ldm_mount_check"
            token_file.write_text(token_val)

            try:
                # Run a single container to verify the mount AND fix permissions for the whole root.
                # We skip .git to avoid altering repository metadata permissions.
                verify_res = run_command(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{root.as_posix()}:/project",
                        "alpine",
                        "sh",
                        "-c",
                        f"if [ \"$(cat /project/.ldm_mount_check 2>/dev/null)\" = \"{token_val}\" ]; then find /project -maxdepth 1 ! -name '.git' ! -name '.' -exec chown -R 1000:1000 {{}} + 2>/dev/null || true; chmod -R 775 /project 2>/dev/null || true; echo 'OK'; else echo 'FAIL'; fi",
                    ]
                )

                if "OK" not in (verify_res or ""):
                    UI.error("\nFATAL: VOLUME MOUNTING IS BROKEN")
                    UI.info(
                        f"{UI.BYELLOW}Reason:{UI.COLOR_OFF} Docker cannot see the files in: {root}"
                    )

                    actual_home = Path.home()
                    try:
                        from ldm_core.utils import get_actual_home

                        actual_home = get_actual_home()
                    except ImportError:
                        pass

                    # Identify all critical paths that might need mounting
                    cert_dir = actual_home / "liferay-docker-certs"
                    mount_hint = self.get_colima_mount_flags([root, cert_dir])

                    UI.info(f"\n{UI.CYAN}To fix this, run:{UI.COLOR_OFF}")
                    UI.info("colima stop")
                    UI.info(
                        f"colima start {mount_hint} --vm-type=vz --mount-type=virtiofs"
                    )

                    sys.exit(1)

                UI.success("Volume mounts verified and permissions synchronized.")
            except Exception as e:
                UI.warning(f"Could not verify mounts automatically: {e}")
            finally:
                if token_file.exists():
                    token_file.unlink()
        else:
            # Standard Linux/Windows fallback
            for key in ["data", "deploy", "state"]:
                if key in paths:
                    # Ensure directory exists before chmodding
                    paths[key].mkdir(parents=True, exist_ok=True)
                    try:
                        if platform.system().lower() != "windows":
                            subprocess.run(
                                ["chmod", "-R", "777", str(paths[key])], check=False
                            )  # nosec B603 B607
                    except Exception:
                        pass

    def check_hostname(self, host_name, expected_ip="127.0.0.1"):
        """Verifies if a hostname resolves to a local IP, providing instructions if not."""
        if not host_name or host_name == "localhost":
            return True

        ip = self.get_resolved_ip(host_name)
        if not ip or not (ip.startswith("127.") or ip in ["::1", "0:0:0:0:0:0:0:1"]):
            UI.error(f"Hostname '{host_name}' does not resolve to your machine.")
            print(
                f"\n{UI.BYELLOW}ACTION REQUIRED:{UI.COLOR_OFF} Please add this to your {UI.WHITE}/etc/hosts{UI.COLOR_OFF} file:"
            )
            print(f"{UI.CYAN}{expected_ip} {host_name} *.{host_name}{UI.COLOR_OFF}\n")
            return False
        return True

    def validate_project_dns(self, project_path):
        """Validates that the project hostname and all extension subdomains resolve to the local proxy IP."""
        meta = self.read_meta(Path(project_path) / PROJECT_META_FILE)
        host_name = meta.get("host_name", "localhost")
        if host_name == "localhost":
            return True, []

        # Find the IP Traefik is actually bound to
        proxy_ip = "127.0.0.1"
        try:
            inspect_res = run_command(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{range .NetworkSettings.Ports.443}}{{.HostIp}}{{end}}",
                    "liferay-proxy-global",
                ],
                check=False,
            )
            if inspect_res and inspect_res.strip():
                # If bound to 0.0.0.0, any loopback is fine.
                # If bound to a specific IP (like 127.0.0.2), we MUST match it.
                proxy_ip = inspect_res.strip()
        except Exception:
            pass

        unresolved = []
        # Check base host
        ip = self.get_resolved_ip(host_name)

        # Validation: If proxy is on 0.0.0.0, allow any 127.x.x.x.
        # Otherwise, strictly require the proxy_ip.
        is_match = False
        if ip:
            if proxy_ip == "0.0.0.0":  # nosec B104
                is_match = ip.startswith("127.") or ip in ["::1", "0:0:0:0:0:0:0:1"]
            else:
                is_match = ip == proxy_ip

        if not ip or not is_match:
            unresolved.append(host_name)

        # Check extensions
        paths = self.setup_paths(project_path)
        exts = self.scan_client_extensions(paths["root"], paths["cx"], paths["ce_dir"])
        for e in exts:
            if (
                e.get("kind") == "Deployment"
                and e.get("deploy", True)
                and e.get("has_load_balancer")
            ):
                ext_domain = f"{e['id']}.{host_name}"
                sub_ip = self.get_resolved_ip(ext_domain)

                sub_match = False
                if sub_ip:
                    if proxy_ip == "0.0.0.0":  # nosec B104
                        sub_match = sub_ip.startswith("127.") or sub_ip in [
                            "::1",
                            "0:0:0:0:0:0:0:1",
                        ]
                    else:
                        sub_match = sub_ip == proxy_ip

                if not sub_ip or not sub_match:
                    unresolved.append(ext_domain)

        return len(unresolved) == 0, unresolved
