import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ldm_core.manager import LiferayManager

import contextlib

from ldm_core.constants import SCRIPT_DIR
from ldm_core.ui import UI
from ldm_core.utils import get_actual_home, safe_cwd


class BaseHandler:
    """Base mixin for LiferayManager containing shared core logic."""

    def __init__(self, args):
        self.args = args
        self.non_interactive = getattr(args, "non_interactive", False)
        self.verbose = getattr(args, "verbose", False)
        # Type stubs for LiferayManager methods (satisfied by mixin resolution)
        self.manager: LiferayManager = None  # type: ignore

    def cmd_run(self, *args, **kwargs): ...
    def cmd_stop(self, *args, **kwargs): ...
    def cmd_deploy(self, *args, **kwargs): ...
    def cmd_snapshot(self, *args, **kwargs): ...
    def cmd_restore(self, *args, **kwargs): ...
    def cmd_reset(self, *args, **kwargs): ...
    def cmd_infra_setup(self, *args, **kwargs): ...
    def cmd_infra_down(self, *args, **kwargs): ...
    def cmd_infra_restart(self, *args, **kwargs): ...
    def cmd_status(self, *args, **kwargs): ...
    def cmd_list(self, *args, **kwargs): ...
    def cmd_config(self, *args, **kwargs): ...
    def cmd_shell(self, *args, **kwargs): ...
    def cmd_env(self, *args, **kwargs): ...
    def cmd_scale(self, *args, **kwargs): ...
    def cmd_rm(self, *args, **kwargs): ...
    def cmd_edit(self, *args, **kwargs): ...
    def cmd_upgrade(self, *args, **kwargs): ...
    def cmd_version(self, *args, **kwargs): ...
    def cmd_doctor(self, *args, **kwargs): ...

    def flag_reindex(self, project_path):
        """Marks the project for a full search reindex on next boot."""
        meta = self.read_meta(project_path)
        if meta:
            meta["reindex_required"] = "true"
            self.write_meta(project_path, meta)
            return True
        return False

    def cmd_dev_setup(self, *args, **kwargs): ...
    def cmd_migrate_search(self, *args, **kwargs): ...

    def get_samples_root(self, *args, **kwargs): ...
    def check_mkcert(self, *args, **kwargs): ...
    def _ensure_network(self, *args, **kwargs): ...
    def setup_infrastructure(self, *args, **kwargs): ...
    def setup_ssl(self, *args, **kwargs): ...
    def write_docker_compose(self, *args, **kwargs): ...
    def update_portal_ext(self, *args, **kwargs): ...
    def _get_infra_env(self, *args, **kwargs): ...
    def _is_ssl_active(self, *args, **kwargs): ...
    def _restore_from_cloud_layout(self, *args, **kwargs): ...
    def validate_lcp_json(self, *args, **kwargs): ...
    def _is_cloud_authenticated(self, *args, **kwargs): ...
    def get_host_passthrough_env(self, *args, **kwargs): ...
    def sync_stack(self, *args, **kwargs): ...
    def get_default_jvm_args(self, *args, **kwargs): ...

    def is_wsl(self):
        """Checks if the current environment is WSL."""
        if platform.system().lower() == "linux":
            try:
                with open("/proc/version") as f:
                    if "microsoft" in f.read().lower():
                        return True
            except Exception:
                pass
        return False

    def ensure_hostnames_resolve(self, root, host_name, project_id=None):
        """Verifies that the main host and all extension subdomains resolve, fixing them if needed."""
        if host_name == "localhost":
            return True

        # Windows/WSL .local warning
        if host_name.endswith(".local") and (
            platform.system().lower() == "windows" or self.is_wsl()
        ):
            UI.warning(f"Hostname '{host_name}' uses the '.local' TLD.")
            UI.info(
                "On Windows, '.local' is reserved for mDNS and may ignore your hosts file."
            )
            UI.info("Recommended: Use '.test' or '.internal' instead.")

        # Collect all required hostnames (main + extensions)
        required_hosts = [host_name]
        paths = self.setup_paths(root)
        if paths["cx"].exists():
            from ldm_core.handlers.workspace import WorkspaceService

            handler = WorkspaceService(self)
            extensions = handler.scan_client_extensions(
                paths["root"], paths["cx"], paths["ce_dir"]
            )
            for ext in extensions:
                if ext.get("deploy") and ext.get("has_load_balancer"):
                    required_hosts.append(f"{ext['id']}.{host_name}")

        unresolved = [
            h for h in required_hosts if not self.check_hostname(h, silent=True)
        ]

        if unresolved:
            if self.non_interactive:
                UI.info(
                    f"Missing host entries detected: {', '.join(unresolved)}. Attempting non-interactive fix..."
                )
                if self._apply_hosts_fix(unresolved):
                    # Give the OS a moment to refresh DNS cache
                    import time

                    time.sleep(0.5)
                    still_broken = [
                        h for h in unresolved if not self.check_hostname(h, silent=True)
                    ]
                    if not still_broken:
                        UI.success("All host entries fixed automatically.")
                        return True
                    UI.die(
                        f"Hostname resolution failed for: {', '.join(still_broken)} even after attempted fix."
                    )
                else:
                    UI.die(
                        "Hostname resolution failed and fix could not be applied non-interactively."
                    )
            else:
                UI.warning(f"Missing host entries detected: {', '.join(unresolved)}")
                UI.info("LDM can try to fix this by adding entries to /etc/hosts.")
                if UI.confirm("Add host entries? (Requires sudo)", "Y"):
                    return self._apply_hosts_fix(unresolved)
                UI.die("Aborted. Hostnames must resolve to continue.")

        return True

    def _pre_flight_checks(self, host_name, port, ssl_enabled=False, meta=None):
        """Runs critical safety checks before starting containers."""
        root = Path(meta.get("root"))
        # 1. RAM Check
        mem_limit = meta.get("mem_limit") if meta else None
        self.check_ram(mem_limit=mem_limit)

        # 2. Hostname Check
        project_id = meta.get("project_name") if meta else None
        self.ensure_hostnames_resolve(root, host_name, project_id=project_id)

        # 3. Port Check (Only if not using SSL proxy which handles routing dynamically)
        if not ssl_enabled:
            resolved_ip = (
                self.get_resolved_ip(host_name)
                if host_name != "localhost"
                else "127.0.0.1"
            )
            # Handle cases where resolution fails (e.g. immediately after hosts update)
            if not resolved_ip:
                resolved_ip = "127.0.0.1"

            # Skip port check if the container is already running (it already owns the port)
            from ldm_core.docker_service import DockerService

            container_name = meta.get("container_name") if meta else None
            is_running = (
                DockerService.is_running(container_name) if container_name else False
            )

            if not is_running and not self.check_port(resolved_ip, port):
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

    LDM_HOST_TAG = "# [LDM]"

    def _apply_hosts_fix(self, unresolved_domains):
        """Attempts to append missing host entries to the system hosts file."""
        # 1. Filter out empty or already resolved (sanity check)
        domains = sorted({d for d in unresolved_domains if d and d != "localhost"})
        if not domains:
            return True

        # Standard LDM expected IP for local development
        target_ip = "127.0.0.1"
        is_windows = platform.system().lower() == "windows"
        is_wsl = self.is_wsl()

        new_entries = []
        for d in domains:
            # Standardize on explicit host entries with the LDM tag for easy removal later
            new_entries.append(f"{target_ip} {d} {self.LDM_HOST_TAG}")

        if not new_entries:
            return True

        content_to_add = "\n# Added by Liferay Docker Manager (LDM)\n"
        content_to_add += "\n".join(new_entries) + "\n"

        hosts_path = (
            r"C:\Windows\System32\drivers\etc\hosts" if is_windows else "/etc/hosts"
        )

        try:
            if is_windows or is_wsl:
                # Target the Windows hosts file
                win_hosts = r"C:\Windows\System32\drivers\etc\hosts"
                UI.info(
                    f"Requesting permission to update Windows hosts file: {win_hosts}..."
                )

                # Command construction:
                # 1. Use powershell (or powershell.exe in WSL)
                # 2. Use 'Start-Process -Verb RunAs' to trigger the UAC prompt
                # 3. Add-Content with explicit UTF8 (No BOM) for Windows DNS compatibility
                exe = "powershell.exe" if is_wsl else "powershell"
                cmd = [
                    exe,
                    "-Command",
                    f"Start-Process powershell -Verb RunAs -ArgumentList \"Add-Content -Path {win_hosts} -Value '{content_to_add}' -Encoding UTF8\"",
                ]
                subprocess.run(cmd, check=True)

                if is_wsl:
                    UI.success("Windows hosts file updated via WSL interop.")
                    UI.info(
                        "WSL will sync this change automatically (on next restart or via DNS cache)."
                    )
            else:
                # Standard Linux / macOS (non-WSL)
                UI.info(f"Requesting elevated privileges to update: {hosts_path}")
                UI.detail(f"Adding entries:\n{content_to_add.strip()}")

                sudo_prefix = ["sudo", "-n"] if self.non_interactive else ["sudo"]
                cmd = [*sudo_prefix, "tee", "-a", hosts_path]

                process = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL
                )
                process.communicate(input=content_to_add.encode())

                if process.returncode != 0:
                    if self.non_interactive:
                        UI.die(
                            f"Elevation failed (sudo -n). Please run manually: ldm fix-hosts {' '.join(domains)}"
                        )
                    return False

            return True
        except Exception as e:
            UI.error(f"Failed to update hosts file: {e}")
            return False

    def _remove_hosts_entries(self, hostnames=None, all_ldm=False):
        """Removes LDM-tagged entries from the system hosts file."""
        if not hostnames and not all_ldm:
            return True

        is_windows = platform.system().lower() == "windows"
        is_wsl = self.is_wsl()

        # Construction of the PowerShell/Bash removal script
        # This script filters the hosts file for lines containing our tag (and optionally hostname)
        if is_windows or is_wsl:
            exe = "powershell.exe" if is_wsl else "powershell"
            win_hosts = r"C:\Windows\System32\drivers\etc\hosts"

            # PS Logic: Read file, filter out lines matching criteria, write back as UTF8 (no BOM)
            if all_ldm:
                filter_logic = (
                    f'where {{ $_ -notmatch "{re.escape(self.LDM_HOST_TAG)}" }}'
                )
            else:
                patterns = [
                    f'($_ -match "{re.escape(self.LDM_HOST_TAG)}" -and $_ -match "{re.escape(h)}")'
                    for h in hostnames
                ]
                filter_logic = f"where {{ -not ({' -or '.join(patterns)}) }}"

            ps_cmd = (
                f"$c = Get-Content -Path {win_hosts}; "
                f"$c | {filter_logic} | Set-Content -Path {win_hosts} -Encoding UTF8"
            )

            try:
                UI.info("Requesting permission to clean Windows hosts file...")
                subprocess.run(
                    [
                        exe,
                        "-Command",
                        f"Start-Process powershell -Verb RunAs -ArgumentList '{ps_cmd}'",
                    ],
                    check=True,
                )
                return True
            except Exception as e:
                UI.error(f"Failed to clean Windows hosts file: {e}")
                return False
        else:
            # macOS / Linux
            hosts_path = "/etc/hosts"
            UI.info(f"Requesting elevated privileges to clean: {hosts_path}")

            try:
                # Use sudo sed to surgically remove lines
                sudo_prefix = ["sudo", "-n"] if self.non_interactive else ["sudo"]
                cmd = [
                    *sudo_prefix,
                    "sed",
                    "-i",
                    ".bak" if platform.system().lower() == "darwin" else "",
                ]

                # ... remaining cmd logic ...
                UI.detail(f"Command: {' '.join(cmd)}")

                if all_ldm:
                    cmd.append(f"/{re.escape(self.LDM_HOST_TAG)}/d")
                else:
                    for h in hostnames:
                        # Remove lines matching the tag AND the specific hostname
                        cmd.extend(
                            ["-e", f"/{re.escape(self.LDM_HOST_TAG)}.*{re.escape(h)}/d"]
                        )

                cmd.append(hosts_path)
                subprocess.run(cmd, check=True)
                return True
            except Exception as ex:
                UI.error(f"Failed to clean {hosts_path}: {ex}")
                return False
        return True

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

            if mem_total and mem_total < min_required:
                UI.detail(
                    f"Docker has less than {UI.format_size(min_required)} RAM allocated."
                )
                UI.detail(
                    "Liferay might be slow or unstable. Increase RAM in Docker Desktop settings."
                )
        except Exception:
            pass

    def check_port(self, ip, port):
        """Checks if a port is available on a specific IP."""
        import errno
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            try:
                s.bind((ip, int(port)))
                return True
            except PermissionError:
                # EACCES: non-root trying to bind to privileged port (< 1024).
                # Fall back to connect_ex check to see if a process is already listening.
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn_s:
                    conn_s.settimeout(0.5)
                    res = conn_s.connect_ex((ip, int(port)))
                    return res != 0
            except OSError as e:
                if e.errno in (errno.EACCES, errno.EPERM):
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn_s:
                        conn_s.settimeout(0.5)
                        res = conn_s.connect_ex((ip, int(port)))
                        return res != 0
                return False
            except OverflowError:
                return False

    def find_available_port(self, ip, start_port, exclude=None):
        """Finds the next available port starting from a given number."""
        port = int(start_port)
        exclude_ports = exclude or []
        while port in exclude_ports or not self.check_port(ip, port):
            port += 1
            if port > 65535:
                UI.die("No available ports found.")
        return port

    def check_registry_collisions(self, project_name, project_root, host_name=None):
        """Checks if another project with the same name or hostname exists at a different path."""
        from ldm_core.constants import REGISTRY_FILE

        actual_home = get_actual_home()
        registry_path = actual_home / ".ldm" / REGISTRY_FILE
        if not registry_path.exists():
            return

        try:
            registry = json.loads(registry_path.read_text())
        except Exception as e:
            UI.warning(f"Failed to load registry: {e}")
            return

        # Robust path resolution
        if isinstance(project_root, dict):
            current_root_path = Path(project_root.get("root", ".")).resolve()
        else:
            current_root_path = Path(project_root).resolve()

        # 1. Check Project Name
        existing_path = registry.get(project_name)
        if existing_path:
            if isinstance(existing_path, dict):
                existing_path = existing_path.get("path")

            abs_existing = str(Path(existing_path).resolve())
            abs_current = str(current_root_path)

            if abs_existing != abs_current:
                if not Path(abs_existing).exists():
                    UI.info(
                        f"Stale registry entry found for project '{project_name}' at: {existing_path} (path no longer exists). Cleaning up..."
                    )
                    self.unregister_project(project_name)
                    try:
                        registry = json.loads(registry_path.read_text())
                    except Exception:
                        registry = {}
                else:
                    overwrite = getattr(self.args, "overwrite_registry", False)
                    if not overwrite:
                        if self.non_interactive:
                            overwrite = True
                        else:
                            ans = UI.ask(
                                f"Project '{project_name}' is already registered at:\n"
                                f"  {existing_path}\n"
                                f"Unregister the old project and register at the new path? [y/N]",
                                "N",
                            ).upper()
                            if ans == "Y":
                                overwrite = True

                    if overwrite:
                        # Automatically stop/down the old project stack if docker-compose.yml exists
                        old_root = Path(existing_path).resolve()
                        if (old_root / "docker-compose.yml").exists():
                            from ldm_core.utils import get_compose_cmd

                            compose_base = get_compose_cmd()
                            UI.info(
                                f"Tearing down conflicting stack at: {existing_path}..."
                            )
                            try:
                                capture = not (UI.INFO_MODE or UI.VERBOSE)
                                self.run_command(
                                    [*compose_base, "down", "-v", "--remove-orphans"],
                                    check=False,
                                    capture_output=capture,
                                    cwd=str(old_root),
                                )
                            except Exception as e:
                                UI.warning(
                                    f"Failed to run compose down for old project stack: {e}"
                                )

                        UI.info(
                            f"Unregistering existing project '{project_name}' from: {existing_path}"
                        )
                        self.unregister_project(project_name)
                        try:
                            registry = json.loads(registry_path.read_text())
                        except Exception:
                            registry = {}

                    else:
                        UI.die(
                            f"Project collision: '{project_name}' is already registered at:\n"
                            f"  {UI.CYAN}{existing_path}{UI.COLOR_OFF}\n"
                            f"Current path:\n"
                            f"  {UI.CYAN}{abs_current}{UI.COLOR_OFF}\n\n"
                            f"Run {UI.BOLD}ldm down --delete{UI.COLOR_OFF} in the original folder to remove it, "
                            f"or run with {UI.BOLD}--overwrite-registry{UI.COLOR_OFF} to automatically overwrite the registry."
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

    def register_project(self, project_name, project_root, host_name=None):
        """Registers a project in the global registry."""
        from ldm_core.constants import REGISTRY_FILE
        from ldm_core.utils import safe_write_text

        actual_home = get_actual_home()
        ldm_dir = actual_home / ".ldm"
        from ldm_core.utils import safe_mkdir

        safe_mkdir(ldm_dir, parents=True, exist_ok=True)

        registry_path = ldm_dir / REGISTRY_FILE

        registry = {}
        if registry_path.exists():
            with contextlib.suppress(Exception):
                registry = json.loads(registry_path.read_text())

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
        from ldm_core.utils import safe_write_text

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

            match = re.search(r'version\s+"(\d+)\.', output)
            if match:
                actual_version = int(match.group(1))
                if actual_version >= int(expected):
                    if self.verbose:
                        UI.info(
                            f"Verified system Java version is JDK {actual_version} (>= {expected})"
                        )
                    return True
            UI.error(
                f"System Java version mismatch. Expected JDK {expected} or higher."
            )
            print(f"{UI.WHITE}Actual output:{UI.COLOR_OFF}\n{output.strip()}")
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
            match = re.search(r"JVM:\s+(\d+)\.", output)
            if match:
                actual_version = int(match.group(1))
                if actual_version >= int(expected):
                    if self.verbose:
                        UI.info(
                            f"Verified Gradle JVM version is JDK {actual_version} (>= {expected})"
                        )
                    return True
            UI.error(f"Gradle JVM version mismatch. Expected JDK {expected} or higher.")
            print(f"{UI.WHITE}Actual output:{UI.COLOR_OFF}\n{output.strip()}")
            return False
        except Exception as e:
            UI.error(f"Failed to verify Gradle JVM version: {e}")
            return False

    def read_meta(self, path):
        """Reads project metadata, supporting both modern and legacy filenames."""
        import os

        from ldm_core.utils import read_meta

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
            from ldm_core.utils import safe_mkdir

            safe_mkdir(target.parent, parents=True, exist_ok=True)
            write_meta(target, meta)
        except Exception:
            pass

    def run_command(
        self,
        cmd,
        check=True,
        cwd=None,
        env=None,
        capture_output=True,
        stdout_file=None,
    ):
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
            stdout_file=stdout_file,
        )

    def require_compose(self, root_path, silent=False):
        """Verifies that a docker-compose.yml file exists in the project root."""
        if not root_path or not (root_path / "docker-compose.yml").exists():
            if not silent:
                UI.error(f"docker-compose.yml not found in {root_path}")
            return False
        return True

    def resolve_container(self, project_name, service="liferay"):
        """Resolves a service to an actual container name or ID via labels."""
        from ldm_core.utils import sanitize_id

        safe_name = sanitize_id(project_name)

        cmd = [
            "docker",
            "ps",
            "-a",
            "--format",
            "{{.Names}}",
            "-f",
            f"label=com.liferay.ldm.project={safe_name}",
            "-f",
            f"label=com.docker.compose.service={service}",
        ]
        res = self.run_command(cmd, check=False)
        if res:
            # Return the first matching name
            return res.splitlines()[0].strip()

        # Fallback to standard naming convention
        return f"{safe_name}-{service}-1"

    def get_container_status(self, container_name):
        """Returns the health or status of a container."""
        from ldm_core.docker_service import DockerService

        try:
            # First try to get health status
            health = DockerService.get_health(container_name)
            if health and health != "unknown":
                return health

            # Fallback to general state
            status = DockerService.get_status(container_name)
            if status and status != "unknown":
                return status
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

            # Check for duplicate names to decide if we need to show paths
            names = [r["path"].name for r in filtered]
            from collections import Counter

            counts = Counter(names)

            for i, r in enumerate(filtered):
                name = r["path"].name
                version = r["version"]
                path_obj = r["path"]

                display_line = f"[{i + 1}] {name} [{UI.CYAN}{version}{UI.COLOR_OFF}]"

                # If there are duplicates, or if we want to always show a hint of where it is
                if counts[name] > 1:
                    # Try to make path relative to home or CWD
                    from ldm_core.utils import get_actual_home

                    home = get_actual_home()
                    try:
                        if path_obj.is_relative_to(home):
                            display_path = f"~/{path_obj.relative_to(home)}"
                        elif path_obj.is_relative_to(Path.cwd()):
                            display_path = f"./{path_obj.relative_to(Path.cwd())}"
                        else:
                            display_path = str(path_obj)
                    except Exception:
                        display_path = str(path_obj)

                    display_line = f"[{i + 1}] {name} {UI.DIM}({display_path}){UI.COLOR_OFF} [{UI.CYAN}{version}{UI.COLOR_OFF}]"

                print(display_line)

            prompt = "\nSelect index, type to filter"
            if filter_str:
                prompt += f" (Current: {UI.CYAN}{filter_str}{UI.COLOR_OFF})"
            prompt += ", 'n' for new, 's' to skip, or 'q' to quit"

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
            if str(choice).lower() == "n":
                return {"new": True}

            # If choice is numeric, it's an index selection
            if str(choice).isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(filtered):
                    return filtered[idx]
                UI.error(f"Invalid index: {choice}")
            else:
                # Treat as filter string
                filter_str = str(choice)

    def detect_project_path(self, project_id=None, for_init=False, fatal=True):
        path = self._detect_project_path_raw(project_id, for_init=for_init, fatal=fatal)
        if path:
            self._acquire_lock_if_needed(path)
        return path

    def _acquire_lock_if_needed(self, path):
        if not path:
            return

        lock_commands = {
            "run",
            "up",
            "stop",
            "restart",
            "down",
            "rm",
            "deploy",
            "snapshot",
            "restore",
            "hydrate",
            "import",
            "init-from",
            "scale",
        }
        cmd = getattr(self.args, "command", None)
        subcmd = getattr(self.args, "subcommand", None)

        is_config_lock = cmd == "config" and subcmd in [
            "edit",
            "rebuild-properties",
            "revert-properties",
            "reset-properties",
        ]
        is_rescue_lock = cmd == "system" and subcmd == "rescue"

        if cmd in lock_commands or is_config_lock or is_rescue_lock:
            from ldm_core.utils import ProjectLock

            mgr = getattr(self, "manager", None) or self
            if not hasattr(mgr, "_active_locks"):
                mgr._active_locks = {}  # type: ignore[attr-defined, union-attr]

            path_key = Path(path).resolve().as_posix()
            if path_key not in mgr._active_locks:  # type: ignore[attr-defined, union-attr]
                lock = ProjectLock(path)
                try:
                    lock.acquire()
                    mgr._active_locks[path_key] = lock  # type: ignore[attr-defined, union-attr]
                except RuntimeError as e:
                    UI.die(str(e))

    def _detect_project_path_raw(self, project_id=None, for_init=False, fatal=True):
        """Resolves a project ID or path to a full filesystem path."""
        no_home_warn = False
        if hasattr(self, "args") and self.args is not None:
            val = getattr(self.args, "no_home_warn", False)
            if not hasattr(val, "_mock_return_value"):
                no_home_warn = bool(val)
        if not no_home_warn and hasattr(self, "defaults") and self.defaults is not None:
            val = self.defaults.get("no_home_warn", "false")
            if not hasattr(val, "_mock_return_value"):
                no_home_warn = str(val).lower() == "true"

        cwd = safe_cwd()
        if cwd:
            try:
                resolved_cwd = cwd.resolve()
                home = get_actual_home().resolve()
                if resolved_cwd == home:
                    if not no_home_warn and not getattr(
                        BaseHandler, "_warned_home", False
                    ):
                        BaseHandler._warned_home = True  # type: ignore[attr-defined]
                        UI.warning(
                            "You are running LDM from your Home directory. "
                            "For better performance and to avoid noise, it is recommended to "
                            "run LDM from a dedicated workspace folder (e.g. ~/ldm)."
                        )
            except Exception:
                pass

        pid = (
            project_id
            or getattr(self.args, "project", None)
            or getattr(self.args, "project_flag", None)
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
                    # If the project is directly in the home directory, warn the user
                    if (
                        p.parent == Path.home().resolve()
                        and not no_home_warn
                        and not getattr(BaseHandler, "_warned_home", False)
                    ):
                        BaseHandler._warned_home = True  # type: ignore[attr-defined]
                        UI.warning(
                            "You are running LDM from your Home directory. "
                            "For better performance and to avoid noise, it is recommended to "
                            "run LDM from a dedicated workspace folder (e.g. ~/ldm)."
                        )
                    return p
                # If for_init, we allow the path as long as it doesn't exist as a file
                if for_init:
                    if p.is_file():
                        UI.die(
                            f"Cannot initialize project: '{p}' already exists and is a file."
                        )
                    if p.parent.exists():
                        # If the new project is directly in the home directory, warn the user
                        if (
                            p.parent == Path.home().resolve()
                            and not no_home_warn
                            and not getattr(BaseHandler, "_warned_home", False)
                        ):
                            BaseHandler._warned_home = True  # type: ignore[attr-defined]
                            UI.warning(
                                "You are running LDM from your Home directory. "
                                "For better performance and to avoid noise, it is recommended to "
                                "run LDM from a dedicated workspace folder (e.g. ~/ldm)."
                            )
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
                cwd = safe_cwd()
                search_dirs = []
                if cwd:
                    search_dirs.extend([cwd, cwd.parent])
                search_dirs.append(get_actual_home() / "ldm")

                # Only fallback to SCRIPT_DIR if we are NOT initializing a new project
                if not for_init:
                    search_dirs.append(SCRIPT_DIR)

                # MacOS specific fallback
                if Path("/Volumes/SanDisk/ldm").exists():
                    search_dirs.append(Path("/Volumes/SanDisk/ldm"))

            # LDM-383: Priority 4 - Global Registry
            from ldm_core.constants import REGISTRY_FILE

            registry_path = get_actual_home() / ".ldm" / REGISTRY_FILE
            if registry_path.exists():
                try:
                    registry = json.loads(registry_path.read_text())
                    for name, data in registry.items():
                        path_str = data.get("path") if isinstance(data, dict) else data
                        if path_str:
                            item = Path(path_str)
                            if item.exists() and item.is_dir():
                                # Check if this registered project matches the PID
                                if pid in (item.name, name):
                                    return item.resolve()

                                # Deep check metadata if folder name didn't match
                                meta_file = None
                                for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]:
                                    if (item / f).exists():
                                        meta_file = item / f
                                        break
                                if meta_file:
                                    meta = self.read_meta(item)
                                    if (
                                        meta.get("project_name") == pid
                                        or meta.get("container_name") == pid
                                    ):
                                        return item.resolve()
                except Exception:
                    pass

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
                    has_structure = (p_test / "files").exists() and (
                        p_test / "deploy"
                    ).exists()
                    if has_meta or has_structure:
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
                                    # LDM-383: Match by folder name, project_name, or container_name
                                    if (
                                        item.name == pid
                                        or meta.get("project_name") == pid
                                        or meta.get("container_name") == pid
                                    ):
                                        return item.resolve()
                            except PermissionError:
                                continue
                except Exception:  # nosec B112
                    continue

        cwd = safe_cwd()
        if cwd:
            cwd = cwd.resolve()
            # Check for multiple metadata filenames
            has_meta = any(
                (cwd / f).exists()
                for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]
            )

            # LDM-383: If pid is provided, check if CWD metadata matches it
            if pid and has_meta:
                try:
                    meta = self.read_meta(cwd)
                    if (
                        cwd.name == pid
                        or meta.get("project_name") == pid
                        or meta.get("container_name") == pid
                    ):
                        return cwd
                except Exception:
                    pass

            if (
                (cwd / "files" / "portal-ext.properties").exists()
                or (cwd / "deploy").exists()
                or has_meta
            ):
                # If no pid or pid matches directory name, return CWD
                if not pid or cwd.name == pid:
                    return cwd

        if pid:
            # If we reached here, a PID was specified but not found in any search dir or CWD
            if not for_init and fatal:
                UI.die(
                    f"Project '{pid}' not found. Searched subdirectories of: "
                    f"{', '.join(str(d) for d in search_dirs)} and the current folder."
                )
            elif not fatal:
                return None

        selection = self.select_project_interactively()
        if selection and selection.get("new"):
            return None
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
        """Finds the 'common' directory by prioritizing Env, CWD, Project Parent, then Global ~/.ldm/common."""
        # Priority 1: Environment override
        env_common = os.environ.get("LDM_COMMON_DIR")
        if env_common:
            return Path(env_common).resolve()

        # Safety: If passed a dict, extract root
        if isinstance(project_path, dict):
            project_path = project_path.get("root", ".")

        from ldm_core.utils import safe_cwd

        cwd = safe_cwd()
        common_path = (cwd / "common") if cwd else None
        if common_path and common_path.exists():
            return common_path

        if project_path:
            p_path = Path(project_path).resolve()
            p_parent_common = p_path.parent / "common"
            if p_parent_common.exists():
                return p_parent_common

            p_grandparent_common = p_path.parent.parent / "common"
            if p_grandparent_common.exists():
                return p_grandparent_common

        exe_path = Path(sys.argv[0]).resolve()
        is_source = exe_path.suffix.lower() == ".py"

        if is_source:
            if (SCRIPT_DIR / "common").exists():
                return SCRIPT_DIR / "common"

        global_common = get_actual_home() / ".ldm" / "common"
        if global_common.exists():
            return global_common

        return (cwd / "common") if cwd else get_actual_home() / ".ldm" / "common"

    def get_common_dirs(self, project_path=None):
        """Finds all active 'common' directories in order of priority (Global first, then Local)."""
        dirs = []

        # 1. Global User Home common dir
        global_common = get_actual_home() / ".ldm" / "common"
        if global_common.exists():
            dirs.append(global_common.resolve())

        # 2. Local / Env common dir
        env_common = os.environ.get("LDM_COMMON_DIR")
        if env_common:
            env_path = Path(env_common).resolve()
            if env_path.exists() and env_path not in dirs:
                dirs.append(env_path)
        else:
            if isinstance(project_path, dict):
                project_path = project_path.get("root", ".")

            from ldm_core.utils import safe_cwd

            cwd = safe_cwd()

            candidates = []
            if cwd:
                candidates.append(cwd / "common")
            if project_path:
                p_path = Path(project_path).resolve()
                candidates.append(p_path.parent / "common")
                candidates.append(p_path.parent.parent / "common")

            for cand in candidates:
                cand_res = cand.resolve()
                if cand_res.exists():
                    if cand_res not in dirs:
                        dirs.append(cand_res)
                    break  # Only take the first matching local common dir

        return dirs

    def setup_paths(self, project_path):
        """Initializes a standard path dictionary for a project."""
        # Safety: If passed a dict (common error in refactored handlers), extract root
        if isinstance(project_path, dict):
            project_path = project_path.get("root", ".")
        elif hasattr(project_path, "resolve"):  # Path-like
            project_path = str(project_path)

        root = Path(project_path).resolve()
        common_dirs = self.get_common_dirs(root)
        common_path = common_dirs[-1] if common_dirs else self.get_common_dir(root)
        return {
            "root": root,
            "common": common_path,
            "common_dirs": common_dirs,
            "data": root / "data",
            "deploy": root / "deploy",
            "files": root / "files",
            "osgi": root / "osgi",
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
                    from ldm_core.utils import safe_mkdir

                    safe_mkdir(root, parents=True, exist_ok=True)
            except Exception as e:
                if self.verbose:
                    UI.warning(f"Could not ensure root directory exists: {e}")

            if self.verbose:
                UI.info("Synchronizing directory permissions via Docker...")

            import uuid

            from ldm_core.utils import safe_write_text

            token_val = f"LDM_VERIFY_{uuid.uuid4().hex[:8]}"
            token_file = root / ".ldm_mount_check"
            try:
                safe_write_text(token_file, token_val)
            except (PermissionError, OSError) as e:
                if self.verbose:
                    UI.debug(f"Could not create mount-check token (ignoring): {e}")
                token_val = "SKIP"  # nosec B105

            try:
                # HARDENING: Mount the project root directly for the write test.
                # Mounting the parent was sometimes too aggressive on older macOS versions.

                # Aggressive sub-directory creation
                docker_cmd = (
                    "mkdir -p /workspace/data /workspace/deploy /workspace/files "
                    "/workspace/logs /workspace/osgi/state /workspace/osgi/configs "
                    "/workspace/osgi/modules /workspace/osgi/portal-log4j /workspace/osgi/log4j "
                    "/workspace/osgi/marketplace /workspace/osgi/client-extensions "
                    "/workspace/routes /workspace/snapshots 2>/dev/null || true; "
                )

                if token_val != "SKIP":  # nosec B105
                    docker_cmd += (
                        f'if [ "$(cat /workspace/.ldm_mount_check 2>/dev/null)" = "{token_val}" ]; then '
                        f"if touch /workspace/.ldm_write_test 2>/dev/null; then echo 'OK'; "
                        # If touch fails, try an aggressive fix once before reporting failure
                        f"else chmod 777 /workspace 2>/dev/null; "
                        f"if touch /workspace/.ldm_write_test 2>/dev/null; then echo 'OK'; else echo 'NO_WRITE'; fi; "
                        f"fi; "
                        f"else echo 'FAIL'; fi"
                    )
                else:
                    docker_cmd += " echo 'OK'"

                verify_res = self.run_command(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{root.as_posix()}:/workspace",
                        "alpine",
                        "sh",
                        "-c",
                        docker_cmd,
                    ]
                )

                if "OK" not in (verify_res or ""):
                    # ... (rest of error handling) ...
                    if "NO_WRITE" in (verify_res or ""):
                        UI.error("FATAL: VOLUME MOUNT IS READ-ONLY")
                        UI.info(
                            f"{UI.BYELLOW}Reason:{UI.COLOR_OFF} Docker can see the files, but the 'liferay' user cannot write to: {root}"
                        )
                    else:
                        UI.error("FATAL: VOLUME MOUNTING IS BROKEN")
                        UI.info(
                            f"{UI.BYELLOW}Reason:{UI.COLOR_OFF} Docker cannot see the files in: {root}"
                        )

                    if system_type == "darwin":
                        actual_home = Path.home()

                        with contextlib.suppress(Exception):
                            from ldm_core.utils import get_actual_home

                            actual_home = get_actual_home()

                        cert_dir = actual_home / "liferay-docker-certs"
                        mount_hint = self.get_colima_mount_flags([root, cert_dir])

                        # Detect architecture to provide accurate advice
                        arch = platform.machine().lower()
                        is_intel = "x86" in arch or "i386" in arch

                        UI.info(f"\n{UI.CYAN}To fix this, run:{UI.COLOR_OFF}")
                        UI.info("colima stop")

                        if is_intel:
                            # Intel Macs (especially on Monterey) often need sshfs with :w
                            # We explicitly suggest adding :w to the HOME mount
                            UI.info("colima start --mount /Users/$(whoami):w")
                            UI.info(
                                f"{UI.WHITE}Note: If write errors persist, try: 'colima stop' then 'colima start --vm-type vz --mount /Users/$(whoami):w'{UI.COLOR_OFF}"
                            )
                        else:
                            # Apple Silicon defaults to vz/virtiofs
                            UI.info(
                                f"colima start {mount_hint} --vm-type vz --mount-type virtiofs"
                            )

                    sys.exit(1)

                if system_type == "darwin" and self.verbose:
                    UI.success("Volume mounts verified and permissions synchronized.")

                # Only reclaim permissions for specific volumes that need it (data, state, logs, deploy, etc.)
                # Reclaiming the root itself causes ownership issues for the host user (e.g. in CI)
                from ldm_core.utils import reclaim_volume_permissions

                composer = getattr(self, "composer", None)
                if not composer and hasattr(self, "manager") and self.manager:
                    composer = getattr(self.manager, "composer", None)
                use_volumes = composer.is_using_named_volumes() if composer else False

                for v in [
                    "data",
                    "state",
                    "logs",
                    "deploy",
                    "log4j",
                    "portal_log4j",
                    "files",
                    "configs",
                    "backups",
                    "cx",
                    "routes",
                ]:
                    if v in paths and paths[v].exists():
                        # LDM-382: Skip reclamation for data/state if they are Docker-managed volumes
                        if use_volumes and v in ["data", "state"]:
                            continue
                        reclaim_volume_permissions(paths[v])

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
                from ldm_core.utils import safe_mkdir

                safe_mkdir(paths[p_key], parents=True, exist_ok=True)

        pe_file = paths["files"] / "portal-ext.properties"
        if pe_file.exists() and pe_file.is_dir():
            UI.warning(f"Removing ghost directory at {pe_file}")
            self.safe_rmtree(pe_file)

        if not pe_file.exists():
            with contextlib.suppress(Exception):
                pe_file.touch()

    def check_docker(self):
        """Verifies Docker accessibility."""
        if os.getenv("LDM_IGNORE_DOCKER", "false").lower() == "true":
            return True

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
                UI.error("FATAL: RUNNING AS ROOT/SUDO IS PROHIBITED")
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
                from ldm_core.utils import safe_mkdir

                safe_mkdir(paths[key], parents=True, exist_ok=True)

        routes_base = paths["root"] / "routes" / "default" / "dxp"
        from ldm_core.utils import safe_mkdir

        safe_mkdir(routes_base, parents=True, exist_ok=True)

    def get_resolved_ip(self, host_name):
        """Resolves a hostname to its IP address."""
        if not host_name or host_name == "localhost":
            return "127.0.0.1"
        try:
            import socket

            return socket.gethostbyname(host_name)
        except socket.gaierror:
            return None

    def check_hostname(self, host_name, silent=False):
        """Verifies that the hostname resolves to the local machine."""
        if host_name == "localhost":
            return True

        resolved_ip = self.get_resolved_ip(host_name)
        if not resolved_ip:
            if not silent:
                UI.error(f"Hostname '{host_name}' does not resolve to any IP address.")
                UI.info(
                    f"Please add it to your local hosts file or run '{UI.WHITE}ldm doctor --fix-hosts{UI.COLOR_OFF}'."
                )
            return False

        # Get host's own primary IP for local check
        import socket

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0)
            try:
                s.connect(("10.254.254.254", 1))
                host_ip = s.getsockname()[0]
            except Exception:
                host_ip = "127.0.0.1"
            finally:
                s.close()
        except Exception:
            host_ip = "127.0.0.1"

        local_ips = ["127.0.0.1", "0.0.0.0", host_ip]  # nosec B104

        if resolved_ip in local_ips:
            return True

        if not silent:
            UI.warning(
                f"Hostname '{host_name}' resolves to non-local IP: {resolved_ip}"
            )
            UI.info("It should resolve to 127.0.0.1 for local development.")
        return False

    def cmd_fix_hosts(self, target=None):
        """Fixes missing host entries for a specific hostname or a LDM project."""
        if not target:
            # If no target provided, run doctor's fix-hosts logic (scans all/current project)
            return self.cmd_doctor(fix_hosts=True)

        # 1. Try to treat as a project first (non-fatal)
        root = self.detect_project_path(target, fatal=False)
        if root:
            UI.info(f"Scanning project '{target}' for required hostnames...")
            dns_ok, unresolved, non_local = self.validate_project_dns(root)
            needs_fix = unresolved + [h for h, ip in non_local]

            if not needs_fix:
                UI.success(f"All domains for project '{target}' resolve correctly.")
                return True

            UI.info(f"Found {len(needs_fix)} domain(s) needing fix.")
            return self._apply_hosts_fix(needs_fix)

        # 2. Otherwise treat as direct hostname
        return self._apply_hosts_fix([target])

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
            from ldm_core.handlers.workspace import WorkspaceService

            handler = WorkspaceService(self)
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
        from ldm_core.utils import safe_rmtree

        safe_rmtree(path)

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

        return " ".join(sorted(mounts))

    def check_uncommitted_changes(self, project_path):
        """Checks if a project has uncommitted git changes in critical paths."""
        if getattr(self, "_skip_git_check", False):
            return True

        p = Path(project_path).resolve()
        if not p.exists() or not (p / ".git").exists():
            return True

        import subprocess

        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                cwd=str(p),
                check=False,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return True

            lines = res.stdout.strip().split("\n")
            critical_changes = []
            for line in lines:
                parts = line.strip().split(None, 1)
                if len(parts) < 2:
                    continue
                filepath = parts[1]
                # Critical folders or files
                if (
                    filepath.startswith("files/")
                    or filepath.startswith("configs/")
                    or filepath in {".env", "docker-compose.yml"}
                ):
                    critical_changes.append(filepath)

            if not critical_changes:
                return True

            force = getattr(self.args, "force", False)
            if force:
                return True

            UI.warning(
                "You have uncommitted changes in critical project files:\n"
                + "\n".join(f"  - {f}" for f in critical_changes[:10])
                + (
                    f"\n  ... and {len(critical_changes) - 10} more"
                    if len(critical_changes) > 10
                    else ""
                )
            )

            if self.non_interactive:
                UI.die(
                    "Aborting to protect uncommitted changes. "
                    "Run with --force to overwrite these files."
                )

            ans = UI.ask(
                "Do you want to proceed and overwrite these changes? [y/N]", "N"
            ).upper()
            if ans != "Y":
                UI.die("Aborting operation to protect uncommitted changes.")

        except Exception as e:
            UI.debug(f"Failed to check git status: {e}")
        return True
