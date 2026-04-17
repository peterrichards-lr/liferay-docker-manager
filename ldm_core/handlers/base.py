import os
import re
import sys
import platform
import subprocess
import shutil
import socket
from pathlib import Path
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE


class BaseHandler:
    """Base class for all command handlers."""

    def __init__(self, args):
        self.args = args
        self.non_interactive = getattr(args, "non_interactive", False)
        self.verbose = getattr(args, "verbose", False)

    def read_meta(self, path):
        if not path.exists():
            return {}
        import json

        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def write_meta(self, path, data):
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=4))

    def run_command(self, cmd, check=True, cwd=None, env=None, capture_output=True):
        from ldm_core.utils import run_command

        return run_command(
            cmd,
            check=check,
            cwd=cwd,
            env=env,
            capture_output=capture_output,
            verbose=self.verbose,
        )

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

    def verify_runtime_environment(self, paths):
        """Verifies volume mounts and synchronizes permissions across the project root."""
        root = paths["root"]
        system_type = platform.system().lower()

        # 1. Synchronize permissions via Docker (macOS/Linux)
        # We do this FIRST because sub-directory creation (mkdir) will fail if the root
        # is currently owned by 'root' (common in CI/WSL).
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

            # Create a unique mount-check token to avoid cache hits
            import uuid

            token_val = f"LDM_VERIFY_{uuid.uuid4().hex[:8]}"
            token_file = root / ".ldm_mount_check"
            try:
                token_file.write_text(token_val)
            except (PermissionError, OSError) as e:
                # If we can't write a token, we might be in a root-owned directory in CI.
                # We skip verification and proceed directly to the reclamation fix.
                if self.verbose:
                    UI.debug(f"Could not create mount-check token (ignoring): {e}")
                token_val = "SKIP"

            # Dynamic UID/GID detection ensures we match the host user in CI environments.
            current_uid = os.getuid() if hasattr(os, "getuid") else 1000
            current_gid = os.getgid() if hasattr(os, "getgid") else 1000

            try:
                # Run a single container to verify the mount AND fix permissions for the whole root.
                # We target the entire tree but MUST skip .git to avoid altering repository metadata.
                # We grant 777 to ensure the host user can always read/write what Docker creates.
                # If we skipped the token, we just run the permission fix.
                docker_cmd = f"chown -R {current_uid}:{current_gid} /project 2>/dev/null || true; chmod -R 777 /project 2>/dev/null || true; "
                if token_val != "SKIP":
                    docker_cmd = (
                        f'if [ "$(cat /project/.ldm_mount_check 2>/dev/null)" = "{token_val}" ]; then '
                        f"{docker_cmd}"
                        f"if touch /project/.ldm_write_test 2>/dev/null; then echo 'OK'; else echo 'NO_WRITE'; fi; "
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
                        f"{root.as_posix()}:/project",
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

                        # Identify all critical paths that might need mounting
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
                # Cleanup tokens
                try:
                    if token_file.exists():
                        token_file.unlink()
                    write_test = root / ".ldm_write_test"
                    if write_test.exists():
                        write_test.unlink()
                except Exception:
                    pass
        else:
            # Windows fallback
            pass

        # 2. Sub-directory initialization
        # Now that root permissions are fixed, we can safely create required folders.
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

        # 3. Post-Sync Fixes: Ensure portal-ext.properties is a file, not a ghost directory
        pe_file = paths["files"] / "portal-ext.properties"
        if pe_file.exists() and pe_file.is_dir():
            UI.warning(f"Removing ghost directory at {pe_file}")
            try:
                shutil.rmtree(pe_file)
            except Exception:
                pass

        if not pe_file.exists():
            try:
                pe_file.touch()
            except Exception:
                pass

    def check_hostname(self, host_name, expected_ip="127.0.0.1"):
        """Verifies if a hostname resolves to a local IP, providing instructions if not."""
        if not host_name or host_name == "localhost":
            return True

        try:
            ip = socket.gethostbyname(host_name)
            if ip == expected_ip:
                return True
        except socket.gaierror:
            pass

        UI.error(f"Host '{host_name}' does not resolve to {expected_ip}.")
        UI.info(
            f"Please add the following entry to your {UI.CYAN}/etc/hosts{UI.COLOR_OFF} file:"
        )
        print(f"\n    {expected_ip}  {host_name}\n")
        return False

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
            search_dirs = [Path.cwd(), Path.home() / "ldm"]

            # Include custom workspace if set
            custom_workspace = os.environ.get("LDM_WORKSPACE")
            if custom_workspace:
                search_dirs.append(Path(custom_workspace).expanduser().resolve())

            for s_dir in search_dirs:
                if not s_dir.exists():
                    continue
                p_test = s_dir / pid
                if (p_test / PROJECT_META_FILE).exists() or (
                    for_init and s_dir.exists()
                ):
                    return p_test

            # 3. Fuzzy search for matching project_name in meta files
            # This handles cases where folder name != project ID
            for s_dir in search_dirs:
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
                except Exception:  # nosec B112
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

    def find_dxp_roots(self):
        """Searches known locations for directories containing a .liferay-docker.meta file."""
        roots = []
        search_dirs = [Path.cwd(), Path.home() / "ldm"]

        custom_workspace = os.environ.get("LDM_WORKSPACE")
        if custom_workspace:
            search_dirs.append(Path(custom_workspace).expanduser().resolve())

        for s_dir in search_dirs:
            if not s_dir.exists():
                continue
            try:
                for item in s_dir.iterdir():
                    if item.is_dir() and not item.name.startswith("."):
                        meta_file = item / PROJECT_META_FILE
                        if meta_file.exists():
                            roots.append(
                                {"path": item, "meta": self.read_meta(meta_file)}
                            )
            except Exception:  # nosec B112
                continue
        return roots

    def parse_version(self, tag):
        """Converts a Liferay tag (e.g. 2025.q1.5) to a comparable tuple (2025, 1, 5)."""
        if not tag:
            return (0, 0, 0)
        # Extract numeric parts: 2025.q1.5 -> (2025, 1, 5)
        # Handles 7.4.13.u123 -> (7, 4, 13, 123)
        parts = re.findall(r"\d+", str(tag))
        return tuple(map(int, parts))

    def select_project_interactively(self, heading="Select a project"):
        """Prompts the user to select an existing project from search locations."""
        roots = self.find_dxp_roots()
        if not roots:
            return None

        UI.heading(heading)
        for i, r in enumerate(roots):
            meta = r.get("meta", {})
            name = meta.get("project_name") or r["path"].name
            tag = meta.get("tag", "unknown")
            print(f"[{i + 1}] {name:<20} ({tag})")

        choice = UI.ask("Choice", "1")
        try:
            return roots[int(choice) - 1]
        except (ValueError, IndexError):
            return None

    def setup_paths(self, project_path):
        """Initializes a standard path dictionary for a project."""
        root = Path(project_path).resolve()
        return {
            "root": root,
            "data": root / "data",
            "deploy": root / "deploy",
            "files": root / "files",
            "configs": root / "osgi" / "configs",
            "marketplace": root / "osgi" / "marketplace",
            "state": root / "osgi" / "state",
            "modules": root / "osgi" / "modules",
            "backups": root / "snapshots",
            "cx": root / "client-extensions",
            "routes": root / "routes",
            "scripts": root / "scripts",
        }

    def check_docker(self):
        """Verifies Docker accessibility and provides instructions for WSL/Sudo issues."""
        try:
            # Check if docker is even installed
            res = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                return True

            # If it failed, diagnose common root/permission issues
            if hasattr(os, "getuid") and os.getuid() == 0:
                UI.error("\n❌ FATAL: RUNNING AS ROOT/SUDO IS PROHIBITED")
                UI.info(
                    "Running LDM with sudo causes permission corruption in your cache and configuration folders.\n"
                )

                if platform.system().lower() == "linux":
                    UI.info(
                        f"If you are using sudo because of Docker permissions, please run:\n"
                        f"{UI.CYAN}sudo usermod -aG docker $USER{UI.COLOR_OFF} and restart your terminal session.\n"
                    )
                sys.exit(1)

            return False
        except FileNotFoundError:
            return False

    def migrate_layout(self, paths):
        """Ensures modern project directory structure and permissions."""
        for key in ["data", "deploy", "state", "marketplace"]:
            if key in paths:
                try:
                    paths[key].mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    if self.verbose:
                        UI.warning(
                            f"Could not ensure directory exists {paths[key]}: {e}"
                        )

        # Ensure standard routes structure
        routes_base = paths["root"] / "routes" / "default" / "dxp"
        try:
            routes_base.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            if self.verbose:
                UI.warning(f"Could not ensure routes directory exists: {e}")

        if platform.system().lower() != "windows":
            try:
                # Explicit calls to os.chmod(0o777) to satisfy existing tests
                os.chmod(str(paths["root"]), 0o777)  # nosec B103
                for key in ["data", "deploy", "state", "marketplace"]:
                    if key in paths and paths[key].exists():
                        os.chmod(str(paths[key]), 0o777)  # nosec B103
                if (paths["root"] / "routes").exists():
                    os.chmod(str(paths["root"] / "routes"), 0o777)  # nosec B103
            except Exception:
                pass

    def get_resolved_ip(self, host_name):
        """Resolves a hostname to its IP address."""
        try:
            return socket.gethostbyname(host_name)
        except socket.gaierror:
            return None

    def validate_project_dns(self, project_id):
        """Verifies that the project's hostname and all active client extensions resolve correctly."""
        root = self.detect_project_path(project_id)
        if not root:
            return False, []

        paths = self.setup_paths(root)
        meta = self.read_meta(root / PROJECT_META_FILE)
        host_name = meta.get("host_name", "localhost")
        unresolved = []

        # Check project hostname
        if host_name != "localhost" and not self.get_resolved_ip(host_name):
            unresolved.append(host_name)

        # Check client extensions
        if paths["cx"].exists():
            from ldm_core.handlers.workspace import WorkspaceHandler

            handler = WorkspaceHandler()
            handler.args = self.args
            extensions = handler.scan_client_extensions(paths)
            for ext in extensions:
                if ext.get("deploy") and ext.get("has_load_balancer"):
                    ext_host = f"{ext['id']}.{host_name}"
                    if not self.get_resolved_ip(ext_host):
                        unresolved.append(ext_host)

        return len(unresolved) == 0, unresolved

    def safe_rmtree(self, path):
        """Securely deletes a directory tree, handling potential Docker/Root permission issues."""
        if not path or not path.exists():
            return

        # Try standard deletion first
        try:
            shutil.rmtree(path)
        except (PermissionError, OSError):
            # If standard fails, use a one-shot container to wipe it
            # We mount the PARENT so we can definitely remove the directory itself.
            parent = path.parent.resolve()
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
            # Final attempt from host just in case
            if path.exists():
                try:
                    shutil.rmtree(path)
                except Exception:
                    pass

    def setup_shell_completion(self):
        """Displays instructions for enabling shell completion."""
        shell = os.environ.get("SHELL", "").split("/")[-1]
        UI.heading("LDM Shell Completion")

        if shell not in ["bash", "zsh"]:
            UI.info(
                f"Completion is currently optimized for bash and zsh. (Found: {shell})"
            )
            return

        cmd = "register-python-argcomplete ldm"
        UI.info(
            f"To enable tab-completion for {shell}, add this to your startup profile:"
        )

        if shell == "zsh":
            print(f'\n    eval "$({cmd})"\n')
        else:
            print("\n    activate-global-python-argcomplete\n")

        UI.info(
            f"You may need to restart your terminal or source your profile ({UI.CYAN}.{shell}rc{UI.COLOR_OFF})"
        )
        print(
            "for the changes to take effect. If 'register-python-argcomplete' is not found,\n"
            "it will be installed automatically the next time you run any LDM command."
        )
