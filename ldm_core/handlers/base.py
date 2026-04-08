import os
import re
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

        choice = UI.ask("Select project index (or 'q' to quit)", "1")
        if choice.lower() == "q":
            import sys

            sys.exit(0)

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(project_roots):
                return project_roots[idx]
        except (ValueError, IndexError):
            pass
        return None

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
        if pid:
            # 1. Direct path check
            p = Path(pid).resolve()
            if (p / PROJECT_META_FILE).exists() or for_init:
                return p

            # 2. Check in CWD first (important for standalone binaries)
            p_in_cwd = Path.cwd() / pid
            if (p_in_cwd / PROJECT_META_FILE).exists() or for_init:
                return p_in_cwd.resolve()

            # 3. Check in SCRIPT_DIR (developer repo workflow)
            p_in_root = SCRIPT_DIR / pid
            if (p_in_root / PROJECT_META_FILE).exists():
                return p_in_root.resolve()

            # 4. Deep search by project_name in metadata
            for search_dir in [Path.cwd(), SCRIPT_DIR]:
                if not search_dir.exists():
                    continue
                for item in search_dir.iterdir():
                    if item.is_dir() and not item.name.startswith("."):
                        meta_file = item / PROJECT_META_FILE
                        if meta_file.exists():
                            meta = self.read_meta(meta_file)
                            if meta.get("project_name") == pid:
                                return item.resolve()

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
        search_dir = Path(search_dir or Path.cwd())
        roots = []
        if not search_dir.exists():
            return roots
        for item in search_dir.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                if (
                    (item / "files").exists()
                    or (item / "deploy").exists()
                    or (item / PROJECT_META_FILE).exists()
                ):
                    meta = self.read_meta(item / PROJECT_META_FILE)
                    version = meta.get("tag") or "unknown"
                    roots.append({"path": item, "version": version})
        return sorted(roots, key=lambda x: x["path"].name)

    def setup_paths(self, root_path):
        root = Path(root_path).resolve()
        return {
            "root": root,
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
        mounts = set()
        real_user = os.environ.get("SUDO_USER") or os.environ.get("USER")

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
                        f"if [ \"$(cat /project/.ldm_mount_check 2>/dev/null)\" = \"{token_val}\" ]; then find /project -maxdepth 1 ! -name '.git' ! -name '.' -exec chown -R 1000:1000 {{}} + && chmod -R 775 /project && echo 'OK'; else echo 'FAIL'; fi",
                    ]
                )

                if "OK" not in (verify_res or ""):
                    UI.error("\n❌ FATAL: VOLUME MOUNTING IS BROKEN")
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
                    import sys

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
                    try:
                        if platform.system().lower() != "windows":
                            subprocess.run(
                                ["chmod", "-R", "777", str(paths[key])], check=False
                            )  # nosec B603 B607
                    except Exception:
                        pass

    def validate_project_dns(self, project_path):
        """Validates that the project hostname and all extension subdomains resolve to loopback."""
        meta = self.read_meta(Path(project_path) / PROJECT_META_FILE)
        host_name = meta.get("host_name", "localhost")
        if host_name == "localhost":
            return True, []

        unresolved = []
        # Check base host
        ip = self.get_resolved_ip(host_name)
        if not ip or not (ip.startswith("127.") or ip in ["::1", "0:0:0:0:0:0:0:1"]):
            unresolved.append(host_name)

        # Check extensions
        paths = self.setup_paths(project_path)
        # scan_client_extensions is inherited from WorkspaceHandler
        exts = self.scan_client_extensions(paths["root"], paths["cx"], paths["ce_dir"])
        for e in exts:
            # Criteria based on user requirements:
            # 1. kind is "Deployment" (Services, not Jobs)
            # 2. deploy flag is True (Active)
            # 3. has_load_balancer is True (Has a public routing entry)
            if (
                e.get("kind") == "Deployment"
                and e.get("deploy", True)
                and e.get("has_load_balancer")
            ):
                ext_domain = f"{e['id']}.{host_name}"
                ip = self.get_resolved_ip(ext_domain)
                if not ip or not (
                    ip.startswith("127.") or ip in ["::1", "0:0:0:0:0:0:0:1"]
                ):
                    unresolved.append(ext_domain)

        return len(unresolved) == 0, unresolved
