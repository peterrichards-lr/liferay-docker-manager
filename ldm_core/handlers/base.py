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
            if platform.system() != "Windows":
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

    def detect_project_path(self, project_id=None):
        """Resolves a project ID or path to a full filesystem path."""
        pid = project_id or getattr(self.args, "project", None)
        if pid:
            p = Path(pid).resolve()
            if (p / PROJECT_META_FILE).exists():
                return p
            p_in_root = SCRIPT_DIR / pid
            if (p_in_root / PROJECT_META_FILE).exists():
                return p_in_root.resolve()
            p_in_cwd = Path.cwd() / pid
            if (p_in_cwd / PROJECT_META_FILE).exists():
                return p_in_cwd.resolve()

            for search_dir in [SCRIPT_DIR, Path.cwd()]:
                for item in search_dir.iterdir():
                    if item.is_dir() and not item.name.startswith("."):
                        meta_file = item / PROJECT_META_FILE
                        if meta_file.exists():
                            meta = self.read_meta(meta_file)
                            if meta.get("project_name") == pid:
                                return item.resolve()
            UI.die(f"Project '{pid}' not found or missing {PROJECT_META_FILE}")

        # Fall back to CWD detection
        cwd = Path.cwd()
        if (
            (cwd / "files" / "portal-ext.properties").exists()
            or (cwd / "deploy").exists()
            or (cwd / PROJECT_META_FILE).exists()
        ):
            return cwd

        # Interactive Fallback
        if not self.non_interactive:
            roots = self.find_dxp_roots()
            if roots:
                UI.heading("Select Project")
                for i, r in enumerate(roots):
                    print(
                        f"[{i + 1}] {r['path'].name} [{UI.CYAN}{r['version']}{UI.COLOR_OFF}]"
                    )
                choice = UI.ask("Select project index", "1")
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(roots):
                        return roots[idx]["path"]
                except (ValueError, IndexError):
                    pass

        return None

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
            "certs": root / ".certs",
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
            run_command(["docker", "version", "--format", "{{.Server.Version}}"])
            return True
        except Exception:
            return False

    def get_resolved_ip(self, host_name):
        if not host_name or host_name == "localhost":
            return "127.0.0.1"
        try:
            return socket.gethostbyname(host_name)
        except socket.gaierror:
            return None
