import os
import re
import sys
import platform
import shutil
import json
import subprocess
from pathlib import Path
from ldm_core.ui import UI
from ldm_core.constants import SCRIPT_DIR, VERSION, BUILD_INFO
from ldm_core.handlers.base import BaseHandler
from ldm_core.utils import (
    run_command,
    get_actual_home,
    check_for_updates,
    version_to_tuple,
    verify_executable_checksum,
    strip_ansi,
    resolve_dependency_version,
)


class DiagnosticsHandler(BaseHandler):
    """Mixin for diagnostic and maintenance commands."""

    def __init__(self, args=None):
        super().__init__(args)

    def cmd_status(self, project_id=None, all_projects=False):
        """Displays a summary of active global services and projects."""
        UI.heading("LDM Service Status")

        # 1. Global Infrastructure
        UI.raw(f"{UI.WHITE}Global Infrastructure:{UI.COLOR_OFF}")
        infra = [
            ("liferay-proxy-global", "SSL Proxy (Traefik)"),
            ("liferay-search-global", "Search (ES)"),
            ("liferay-docker-proxy", "macOS Socket Bridge"),
        ]

        any_infra = False
        for container, label in infra:
            res = run_command(
                ["docker", "ps", "-q", "-f", f"name=^{container}$"], check=False
            )
            if res:
                inspect = run_command(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{.State.Status}} {{.Config.Image}}",
                        container,
                    ],
                    check=False,
                )
                if inspect:
                    status, image = inspect.split(" ", 1)
                    UI.raw(
                        f"  {UI.GREEN}●{UI.COLOR_OFF} {label:<25} {status.capitalize():<10} {image}"
                    )
                    any_infra = True

        if not any_infra:
            print(
                f"  {UI.WHITE}No global services are currently running.{UI.COLOR_OFF}"
            )

        print()

        # 2. Project Status
        if all_projects:
            UI.raw(f"{UI.WHITE}All Managed Projects:{UI.COLOR_OFF}")
        elif project_id:
            UI.raw(f"{UI.WHITE}Project Status: {project_id}{UI.COLOR_OFF}")
        else:
            UI.raw(f"{UI.WHITE}Active Projects:{UI.COLOR_OFF}")

        roots = []
        if project_id:
            root_path = self.detect_project_path(project_id)
            if root_path:
                # We need to match find_dxp_roots structure
                roots = [{"path": root_path, "version": "unknown"}]
                meta = self.read_meta(root_path)
                if meta.get("tag"):
                    roots[0]["version"] = meta["tag"]
        else:
            roots = self.find_dxp_roots()

        active_projects = False

        for r in roots:
            path = r["path"]
            meta = self.read_meta(path)
            p_id = meta.get("container_name") or path.name

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
                active_projects = True
                host = meta.get("host_name", "localhost")
                ssl = str(meta.get("ssl")).lower() == "true"
                proto = "https" if ssl else "http"
                port = (
                    str(meta.get("ssl_port", "443"))
                    if ssl
                    else str(meta.get("port", "8080"))
                )
                url = f"{proto}://{host}"
                if (ssl and port != "443") or (not ssl and port != "80"):
                    url += f":{port}"

                UI.raw(
                    f"  {UI.GREEN}●{UI.COLOR_OFF} {p_id:<25} {r['version']:<15} {url}"
                )

                # List active extensions
                extensions = meta.get("extensions", [])
                if extensions:
                    for ext in extensions:
                        ext_id = ext.get("id")
                        # Check if this extension is running
                        ext_running = run_command(
                            [
                                "docker",
                                "ps",
                                "-q",
                                "--filter",
                                f"label=com.liferay.ldm.project={p_id}",
                                "--filter",
                                f"label=com.docker.compose.service={ext_id}",
                                "--filter",
                                "status=running",
                            ],
                            check=False,
                        )
                        if ext_running:
                            ext_url = ext.get("url", "N/A")
                            UI.raw(
                                f"    {UI.WHITE}└─{UI.COLOR_OFF} {ext_id:<23} {ext_url}"
                            )
            elif all_projects:
                UI.raw(
                    f"  {UI.WHITE}○{UI.COLOR_OFF} {p_id:<25} {r['version']:<15} {UI.WHITE}Stopped{UI.COLOR_OFF}"
                )
                active_projects = True

        if not active_projects:
            UI.raw(f"  {UI.WHITE}No projects are currently running.{UI.COLOR_OFF}")

        if not any_infra and not active_projects:
            sys.exit(1)
        sys.exit(0)

    def cmd_update_check(self, force=True):
        UI.heading("LDM Update Check")
        latest, url = check_for_updates(VERSION, force=force)
        if not latest:
            UI.error("Could not reach GitHub to check for updates.")
            return

        if version_to_tuple(latest) <= version_to_tuple(VERSION):
            UI.success(f"You are up to date! (v{VERSION})")
        else:
            print(
                f"{UI.BYELLOW}[!] A new version is available: v{latest}{UI.COLOR_OFF}"
            )
            print(f"    Current version: v{VERSION}")
            print(f"    Download: {UI.CYAN}{url}{UI.COLOR_OFF}\n")

    def cmd_clear_cache(self):
        """Deprecated: Use ldm cache instead."""
        self.cmd_cache(target="tags")

    def cmd_cache(self, target="all"):
        """Manages LDM internal caches (tags, projects)."""
        UI.heading("LDM Cache Management")

        home = get_actual_home()
        tag_cache = home / ".liferay_docker_cache.json"

        cleared = []

        if target in ["tags", "all"]:
            if tag_cache.exists():
                os.remove(tag_cache)
                cleared.append("Docker tag cache")

        if target in ["seeds", "all"]:
            cache_dir = home / ".ldm" / "seeds"
            if cache_dir.exists():
                count = len(list(cache_dir.glob("*.tar.gz")))
                if count > 0:
                    import shutil

                    shutil.rmtree(cache_dir)
                    cleared.append(f"Pre-warmed seeds ({count} files)")

        if target in ["samples", "all"]:
            cache_dir = home / ".ldm" / "references" / "samples"
            if cache_dir.exists():
                import shutil

                shutil.rmtree(cache_dir)
                cleared.append("Sample pack cache")

        if not cleared:
            UI.info("No caches found to clear.")
        else:
            UI.success(f"Successfully cleared: {', '.join(cleared)}")

    def _get_manual_upgrade_cmd(self, url, exe_path):
        """Generates a platform-appropriate manual download and install command."""
        system = platform.system().lower()
        if system in ["win32", "windows"]:
            return f'Invoke-WebRequest -Uri "{url}" -OutFile "{exe_path}"'
        else:
            # Check if parent directory is writable to decide on sudo
            try:
                parent_writable = os.access(exe_path.parent, os.W_OK)
            except Exception:
                parent_writable = False
            prefix = "sudo " if not parent_writable else ""
            return f'{prefix}curl -L "{url}" -o "{exe_path}" && {prefix}chmod +x "{exe_path}"'

    def cmd_upgrade(self):
        """Self-upgrade the LDM binary to the latest version."""
        UI.heading("LDM Self-Upgrade")
        is_repair = getattr(self.args, "repair", False)
        pre_release = getattr(self.args, "pre_release", False)

        if is_repair:
            latest = VERSION
            system = platform.system().lower()
            machine = platform.machine().lower()
            target_asset = "ldm-linux"
            if system == "darwin":
                # Architecture-aware naming
                if machine == "arm64":
                    target_asset = "ldm-macos-arm64"
                else:
                    target_asset = "ldm-macos-x86_64"
            elif system in ["win32", "windows"]:
                target_asset = "ldm-windows.exe"
            url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{VERSION}/{target_asset}"
        else:
            # 1. Check for updates
            # Force is True for manual upgrade requests to ensure we bypass cache
            latest, url = check_for_updates(
                VERSION, force=True, pre_release=pre_release
            )

            if not latest:
                UI.error("Failed to check for updates.")
                UI.info("Please check your internet connection or try again later.")
                return

            is_beta = "-" in VERSION
            if is_beta and not pre_release:
                # User is on beta but wants stable (Switching Tiers)
                UI.info(
                    f"You are currently on a beta build ({UI.BYELLOW}v{VERSION}{UI.COLOR_OFF})."
                )
                UI.info(
                    f"The latest stable version is {UI.GREEN}v{latest}{UI.COLOR_OFF}."
                )
                if not UI.confirm("Switch back to the stable release tier?", "N"):
                    return
            elif version_to_tuple(latest) <= version_to_tuple(VERSION):
                tier = " (stable)" if not pre_release else " (pre-release)"
                UI.success(f"LDM is already up to date v{VERSION}{tier}.")
                return

        if is_repair:
            UI.info(f"Repairing current version: v{latest}")
        else:
            UI.info(f"New version found: v{latest}")

        if not url or not url.startswith("http"):
            UI.die("Download URL not found for your architecture.")

        prompt = (
            f"Repair v{latest}?"
            if is_repair
            else f"Upgrade from v{VERSION} to v{latest}?"
        )
        if not self.non_interactive and not UI.confirm(prompt, "Y"):
            UI.info("Operation aborted.")
            return

        # 2. Preparation
        exe_path = Path(sys.argv[0]).resolve()
        if exe_path.suffix.lower() == ".py":
            UI.die(
                "Self-upgrade is only supported for standalone binaries. Please use 'git pull' for source installations."
            )

        import tempfile

        # Download to a system temporary directory that is always writable
        tmp_fd, tmp_path_str = tempfile.mkstemp(prefix="ldm-upgrade-")
        temp_new = Path(tmp_path_str)
        os.close(tmp_fd)

        # 3. Download
        UI.info(f"Downloading v{latest}...")
        try:
            import requests

            response = requests.get(
                url, headers={"User-Agent": "ldm-cli"}, timeout=30, stream=True
            )
            response.raise_for_status()
            with open(temp_new, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        except requests.exceptions.HTTPError as e:
            if temp_new.exists():
                temp_new.unlink()
            if e.response.status_code == 404:
                UI.die(
                    "A release build may be in progress. Please try again later. (HTTP 404: File not found)"
                )
            else:
                UI.die("Download failed.", e)
        except Exception as e:
            if temp_new.exists():
                temp_new.unlink()
            manual_cmd = self._get_manual_upgrade_cmd(url, exe_path)
            UI.error(f"Download failed: {e}")
            UI.info(
                f"You can upgrade manually by running:\n\n    {UI.CYAN}{manual_cmd}{UI.COLOR_OFF}\n"
            )
            sys.exit(1)

        # 4. Verify Integrity
        UI.info("Verifying integrity...")
        import hashlib

        sha = hashlib.sha256()
        with open(temp_new, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha.update(chunk)
        new_hash = sha.hexdigest()

        # Fetch official checksums.txt
        checksum_url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{latest}/checksums.txt"
        try:
            import requests

            response = requests.get(
                checksum_url, headers={"User-Agent": "ldm-cli"}, timeout=10
            )
            if response.status_code == 200:
                official_data = response.text

                # Extract filename from URL to match exact hash in checksums.txt
                target_name = url.split("/")[-1]

                verified = False
                for line in official_data.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        hash_val = parts[0]
                        file_name = parts[1]
                        if file_name == target_name and new_hash == hash_val:
                            verified = True
                            break

                if not verified:
                    if temp_new.exists():
                        temp_new.unlink()
                    manual_cmd = self._get_manual_upgrade_cmd(url, exe_path)
                    UI.error("Integrity verification failed! The hash does not match.")
                    UI.info(
                        f"If you trust this build, install manually:\n\n    {UI.CYAN}{manual_cmd}{UI.COLOR_OFF}\n"
                    )
                    sys.exit(1)
            elif response.status_code == 404:
                if temp_new.exists():
                    temp_new.unlink()
                UI.die(
                    "A release build may be in progress. Please try again later. (HTTP 404: Failed to fetch checksums)"
                )
            else:
                if temp_new.exists():
                    temp_new.unlink()
                UI.die(f"Failed to fetch checksums (HTTP {response.status_code})")
        except Exception as e:
            UI.warning(
                f"Could not verify hash remotely ({e}). Proceeding with caution..."
            )

        # 5. Atomic Swap
        UI.info("Applying update...")
        import subprocess

        try:
            if platform.system().lower() == "windows":
                # Windows replacement logic via temporary batch file with retry loop
                # We need to ensure the calling process has exited before we can move the file.
                bat_path = exe_path.with_suffix(".update.bat")
                bat_content = f"""@echo off
setlocal enabledelayedexpansion
set "RETRIES=0"
set "MAX_RETRIES=10"

:RETRY
timeout /t 2 /nobreak > nul
taskkill /F /IM ldm.exe /T >nul 2>&1

move /y "{temp_new}" "{exe_path}" >nul 2>&1
if !errorlevel! equ 0 (
    start "" "{exe_path}" doctor
    goto :CLEANUP
)

set /a "RETRIES+=1"
if !RETRIES! lss !MAX_RETRIES! (
    echo [!RETRIES!/!MAX_RETRIES!] Access denied, retrying in 2 seconds...
    goto :RETRY
)

echo [ERROR] Failed to apply update after !MAX_RETRIES! attempts. 
echo [ERROR] Please try running the following command manually in an elevated terminal:
echo move /y "{temp_new}" "{exe_path}"
pause

:CLEANUP
(goto) 2>nul & del "%~f0"
"""
                bat_path.write_text(bat_content)
                UI.success(
                    "Update staged. LDM will restart in a new window to complete."
                )

                # Bandit: B602 (shell=True) is necessary here to launch the independent Windows batch updater.
                # The path is internally generated and sanitized.
                subprocess.Popen(["cmd.exe", "/c", str(bat_path)], shell=True)  # nosec B602
                sys.exit(0)
            else:
                # Unix atomic rename
                # Bandit: B103 (chmod 0o755) is necessary to make the newly downloaded binary executable.
                try:
                    os.chmod(temp_new, 0o755)  # nosec B103
                except Exception:
                    pass
                import shutil

                try:
                    # Use shutil.move instead of os.replace because it handles
                    # 'Invalid cross-device link' (Errno 18) by falling back to copy+unlink.
                    shutil.move(str(temp_new), str(exe_path))
                    UI.success(f"Successfully upgraded to v{latest}!")
                except (PermissionError, OSError):
                    UI.info(
                        "\nRequesting permission to replace the binary in system path..."
                    )
                    try:
                        # Use sudo to copy the file from /tmp to system path
                        # We use cp + rm instead of mv to avoid 'Invalid cross-device link' errors
                        # on systems where /tmp is a different filesystem (like Fedora/tmpfs).
                        subprocess.run(
                            ["sudo", "cp", str(temp_new), str(exe_path)], check=True
                        )
                        subprocess.run(["sudo", "rm", str(temp_new)], check=True)
                        UI.success(f"Successfully upgraded to v{latest}!")
                    except Exception as e:
                        UI.error(f"Failed to replace binary even with sudo: {e}")
                        UI.info(
                            f'Please run manually: {UI.CYAN}sudo cp "{temp_new}" "{exe_path}" && sudo rm "{temp_new}"{UI.COLOR_OFF}'
                        )
                        return

        except Exception as e:
            if temp_new.exists():
                temp_new.unlink()
            manual_cmd = self._get_manual_upgrade_cmd(url, exe_path)
            UI.error(f"Failed to apply update: {e}")
            UI.info(
                f"Please try the manual installation command:\n\n    {UI.CYAN}{manual_cmd}{UI.COLOR_OFF}\n"
            )
            sys.exit(1)

        # 7. Post-Upgrade: Shell Completion Check
        UI.info("\nChecking shell completion status...")
        if not self.is_completion_enabled():
            UI.warning("Shell completion is not enabled for 'ldm' in this session.")
            UI.info("To enable tab-completion for commands and projects, run:")
            print(f"\n    {UI.CYAN}ldm completion{UI.COLOR_OFF}\n")
        else:
            UI.success("Shell completion is active.")

    def is_completion_enabled(self):
        """Checks if completion setup is present in the user's shell profile."""
        home = get_actual_home()
        # Use SHELL if available, otherwise fallback to empty string
        raw_shell = os.environ.get("SHELL", "").lower()

        # Get just the binary name (e.g. /bin/zsh -> zsh)
        shell = raw_shell.split("/")[-1] if "/" in raw_shell else raw_shell
        if shell.endswith(".exe"):
            shell = shell[:-4]

        # Define profile files based on shell
        profiles = []
        if "zsh" in shell:
            profiles = [home / ".zshrc"]
        elif "bash" in shell:
            profiles = [home / ".bashrc", home / ".bash_profile", home / ".profile"]
        elif "fish" in shell:
            profiles = [home / ".config/fish/config.fish"]
        elif "powershell" in shell or "pwsh" in shell:
            profiles = [
                home / "Documents/PowerShell/Microsoft.PowerShell_profile.ps1",
                home / "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
            ]

        # Look for the setup strings
        markers = ["ldm completion", "register-python-argcomplete ldm"]

        for profile in profiles:
            if profile.exists():
                try:
                    content = profile.read_text()
                    if any(marker in content for marker in markers):
                        return True
                except Exception:  # nosec B112
                    continue

        return False

    def _get_env_info(self):
        """Extracts architecture, OS, and Docker provider information."""
        arch = "Unknown"
        host_os = "Unknown"
        provider = "Unknown"

        # 1. Architecture & OS
        try:
            platform_str = (
                f"{platform.system()}-{platform.release()}-{platform.machine()}"
            )
            p_low = platform_str.lower()
            is_mac = "mac" in p_low or "darwin" in p_low

            if "arm64" in p_low or "aarch64" in p_low:
                arch = "Apple Silicon" if is_mac else "ARM64"
            elif "x86_64" in p_low or "amd64" in p_low or "i386" in p_low:
                arch = "Apple Intel" if is_mac else "x86_64"

            if is_mac:
                # Improved mapping: darwin21 = macOS 12 Monterey, etc.
                ver_match = re.search(r"darwin[-]?(\d+)", p_low)
                if not ver_match:
                    ver_match = re.search(r"macos[-]?(\d+)", p_low)

                if ver_match:
                    v_num = int(ver_match.group(1))
                    if v_num >= 20:
                        v_macos = v_num - 9
                        names = {
                            11: "Big Sur",
                            12: "Monterey",
                            13: "Ventura",
                            14: "Sonoma",
                            15: "Sequoia",
                            16: "16",
                            17: "17",
                        }
                        name = names.get(v_macos, str(v_macos))
                        host_os = f"macOS {v_macos} {name}".strip()
                    else:
                        host_os = f"macOS {v_num}"
                else:
                    host_os = "macOS 11+"
            elif "microsoft" in p_low or "windows" in p_low:
                host_os = "Windows 11"
                arch = "Windows PC"
            elif "fedora" in p_low:
                # Capture major version if possible
                fedora_match = re.search(r"fc(\d+)", p_low)
                host_os = (
                    f"Fedora {fedora_match.group(1) if fedora_match else ''}".strip()
                )
                arch = "Linux Workstation"
            elif "ubuntu" in p_low:
                ubuntu_match = re.search(r"(\d+\.\d+)", p_low)
                host_os = (
                    f"Ubuntu {ubuntu_match.group(1) if ubuntu_match else ''}".strip()
                )
                arch = "Linux Node" if "server" in p_low else "Linux Workstation"
            elif "linux" in p_low:
                host_os = "Linux"
                arch = "Linux Workstation"
        except Exception:
            pass

        # 2. Docker Provider
        mount_type = None
        try:
            context = run_command(["docker", "context", "show"], check=False).strip()
            if context:
                inspect = run_command(
                    ["docker", "context", "inspect", context], check=False
                )
                if inspect:
                    data = json.loads(inspect)[0]
                    endpoint = (
                        data.get("Endpoints", {}).get("docker", {}).get("Host", "")
                    )
                    if endpoint:
                        if ".colima" in endpoint:
                            provider = "Colima"
                        elif "orbstack" in endpoint:
                            provider = "OrbStack"
                        elif "docker.sock" in endpoint or "docker_engine" in endpoint:
                            # Standard socket. Determine if it's Native or Desktop.
                            sys_type = platform.system().lower()
                            if sys_type == "linux":
                                # Check for WSL
                                try:
                                    with open("/proc/version", "r") as f:
                                        if "microsoft" in f.read().lower():
                                            provider = "Native WSL2"
                                        else:
                                            provider = "Native Docker"
                                except Exception:
                                    provider = "Native Docker"
                            else:
                                provider = "Docker Desktop"

                    if provider == "Unknown":
                        if context == "colima":
                            provider = "Colima"
                        elif context == "orbstack":
                            provider = "OrbStack"
                        elif context == "desktop-linux":
                            provider = "Docker Desktop"

            # 3. Final safety wash for slug/sync compatibility
            p_low = platform.system().lower()
            if provider == "Unknown":
                if p_low == "linux":
                    try:
                        with open("/proc/version", "r") as f:
                            if "microsoft" in f.read().lower():
                                provider = "Native WSL2"
                            else:
                                provider = "Native Docker"
                    except Exception:
                        provider = "Native Docker"
                elif p_low == "windows" or "win32" in p_low:
                    provider = "Docker Desktop"
                elif p_low == "darwin":
                    # Colima and Orbstack usually have distinct context names
                    # but if we are here, default to Docker Desktop
                    provider = "Docker Desktop"

            # 4. Colima-specific info
            if provider == "Colima":
                try:
                    # 'colima status' contains mountType in its output
                    status_out = run_command(["colima", "status"], check=False)
                    if status_out:
                        for line in status_out.strip().split("\n"):
                            if "mountType:" in line:
                                mount_type = line.split("mountType:")[1].strip()
                                break

                    # 4. Check colima.yaml for explicit 'writable' flag
                    # This is more reliable for 'sshfs' than just checking 'mount' output
                    import yaml

                    home = get_actual_home()
                    config_path = (
                        home
                        / ".colima"
                        / (context if context != "default" else "default")
                        / "colima.yaml"
                    )
                    if config_path.exists():
                        with open(config_path, "r") as f:
                            config = yaml.safe_load(f)
                            mounts = config.get("mounts", [])
                            is_explicitly_writable = False
                            for m in mounts:
                                # Standard home mount check
                                if (
                                    m.get("location") == str(home)
                                    or m.get("location") == "/Users"
                                    or m.get("location").startswith("/Users/")
                                ):
                                    if m.get("writable") is True:
                                        is_explicitly_writable = True
                                        break

                            # Store this in a way doctor can use
                            if not is_explicitly_writable and mount_type == "sshfs":
                                # We'll use this to trigger a warning even if the write test hasn't run yet
                                setattr(self, "_colima_mount_not_writable", True)
                except Exception:
                    pass

        except Exception:
            pass

        return arch, host_os, provider, mount_type

    def cmd_doctor(self, project_id=None, all_projects=False):
        """Verify host environment health and project dependencies."""
        arch, host_os, provider, mount_type = self._get_env_info()

        if getattr(self.args, "slug", False):
            # Use same slug logic as sync_compatibility.py
            clean_arch = arch.lower().replace(" ", "-")
            clean_os = host_os.lower().replace(" ", "-").replace("+", "")
            clean_provider = provider.lower().replace(" ", "-")
            print(f"{clean_arch}-{clean_os}-{clean_provider}")
            return

        UI.heading("LDM Doctor - Environmental Health Check")

        # 0. Early Project Resolve (Optional skip allowed)
        skip_project = getattr(self.args, "skip_project", False)
        check_all = all_projects or getattr(self.args, "all", False)

        project_paths = []
        requires_ssl = False
        if check_all:
            roots = self.find_dxp_roots()
            project_paths = [r["path"] for r in roots]
            for r in roots:
                p_meta = self.read_meta(r["path"])
                if str(p_meta.get("ssl", "false")).lower() == "true":
                    requires_ssl = True
        elif not skip_project:
            p_path = self.detect_project_path(project_id)
            if p_path:
                project_paths = [p_path]
                p_meta = self.read_meta(p_path)
                if str(p_meta.get("ssl", "false")).lower() == "true":
                    requires_ssl = True

        results = []
        hints = []

        def add_hint(text, doc=None):
            hints.append({"text": text, "doc": doc})

        # Detect WSL early for troubleshooting logic
        is_wsl = False
        if platform.system().lower() == "linux":
            try:
                with open("/proc/version", "r") as f:
                    if "microsoft" in f.read().lower():
                        is_wsl = True
            except Exception:
                pass

        # 0. Version Check
        v_display = f"v{VERSION}{UI.get_beta_label(VERSION)}"
        if BUILD_INFO:
            v_display += f" ({BUILD_INFO})"

        latest, _ = check_for_updates(VERSION, force=True)
        if latest and version_to_tuple(latest) > version_to_tuple(VERSION):
            results.append(
                (
                    "LDM Version",
                    f"{v_display} (v{latest} available - Run 'ldm upgrade')",
                    "warn",
                )
            )
            add_hint(
                f"Upgrade LDM from v{VERSION} to v{latest} by running '{UI.WHITE}ldm upgrade{UI.COLOR_OFF}'.",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#troubleshooting-version-loop--integrity-issues",
            )
        else:
            results.append(("LDM Version", f"{v_display} (Latest)", True))

        # 0.1 Executable Integrity
        status, ok, detected_version = verify_executable_checksum(VERSION)
        if not status:
            status, ok = "Verification Unavailable", "warn"

        # If the version in memory differs from the one detected in the binary,
        # it means shadowing is occurring. We update the version reporting to reflect this.
        if detected_version != VERSION:
            # Re-check for updates using the ACTUAL binary version
            latest, _ = check_for_updates(detected_version, force=True)
            if latest and version_to_tuple(latest) > version_to_tuple(detected_version):
                results[0] = (
                    "LDM Version",
                    f"v{detected_version} (v{latest} available - Run 'ldm upgrade')",
                    "warn",
                )
            else:
                results[0] = ("LDM Version", f"v{detected_version} (Latest)", True)

            # Add a shadow warning to the integrity status
            status = f"{status} (Shadowed by {VERSION})"
            ok = "warn" if ok is True else ok

        if ok is False:
            status = f"{status} {UI.WHITE}(Run 'ldm upgrade --repair'){UI.COLOR_OFF}"
            add_hint(
                f"Repair your LDM installation by running '{UI.WHITE}ldm upgrade --repair{UI.COLOR_OFF}'.",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#troubleshooting-version-loop--integrity-issues",
            )

        results.append(("Executable Integrity", status, ok))

        # 0.2 Executable Path

        try:
            exe_path = Path(sys.argv[0]).resolve()
            results.append(("Executable Path", str(exe_path), True))
        except Exception:
            pass

        # 1. System Info
        results.append(("Python Version", sys.version.split()[0], True))
        results.append(("Platform", platform.platform(), True))

        # 1.1 Shell Completion Check
        if self.is_completion_enabled():
            results.append(("Shell Completion", "Enabled (Active)", True))
        else:
            shell = os.environ.get("SHELL", "").split("/")[-1]
            if shell in ["bash", "zsh", "fish"]:
                results.append(("Shell Completion", f"Not Enabled ({shell})", "warn"))
                add_hint(
                    f"Enable tab-completion for {shell} by running '{UI.WHITE}ldm completion{UI.COLOR_OFF}'.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#3-shell-autocompletion",
                )
            else:
                results.append(("Shell Completion", f"Unsupported ({shell})", "warn"))

        # 2. Docker Check
        # Perform a silent check first to avoid double error reporting in the UI
        docker_version = None
        try:
            docker_bin = shutil.which("docker")
            if docker_bin:
                res = subprocess.run(
                    [docker_bin, "version", "--format", "{{.Server.Version}}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode == 0:
                    docker_version = res.stdout.strip()
        except Exception:
            pass

        if docker_version:
            results.append(("Docker Engine", f"Running (v{docker_version})", True))

            # 2.0 Docker Context & Provider Check
            try:
                context = (
                    self.run_command(["docker", "context", "show"], check=False) or ""
                ).strip()
                if context:
                    results.append(("Docker Context", context, True))

                # Identify Provider
                if is_wsl:
                    if context in ["desktop-linux", "docker-desktop"]:
                        provider = "Docker Desktop"
                    elif context == "default":
                        # If default, check if endpoint is local or redirected
                        inspect = self.run_command(
                            ["docker", "context", "inspect", "default"], check=False
                        )
                        if "docker-desktop" in str(inspect).lower():
                            provider = "Docker Desktop"
                        else:
                            provider = "Native WSL2"
                elif platform.system().lower() == "darwin":
                    if context == "orbstack":
                        provider = "OrbStack"
                    else:
                        # On Mac, 'colima' or 'default' (standard socket) is treated as Colima
                        # for LDM's compatibility tracking purposes.
                        provider = "Colima"
                elif platform.system().lower() == "windows":
                    # On Windows native, any standard context is Docker Desktop
                    if context in ["default", "desktop-linux", "docker-desktop"]:
                        provider = "Docker Desktop"

                results.append(("Docker Provider", provider, True))

                # Proactive Colima SSHFS warning
                if getattr(self, "_colima_mount_not_writable", False):
                    add_hint(
                        "Colima: Your mount is not explicitly marked as 'writable'. With 'sshfs', this often causes permission errors.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#colima-mount-permissions",
                    )
                    add_hint("Fix: 'colima stop' then 'colima start --mount [HOME]:w'")

                # Check for symlinked socket in WSL (Docker Desktop override)
                if is_wsl:
                    socket_path = Path("/var/run/docker.sock")
                    if socket_path.is_symlink():
                        target = socket_path.resolve()
                        results.append(
                            ("Docker Socket", f"Symlinked to {target.name}", "warn")
                        )
                        add_hint(
                            "WSL: Your Docker socket is symlinked, likely by Docker Desktop 'WSL Integration'.",
                            "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#wsl2-mixed-environments",
                        )
                        add_hint(
                            "To use 'Native' Docker in this distro, disable 'WSL Integration' in Docker Desktop settings and delete the symlink."
                        )
            except Exception:
                pass

            # 2.0.1 Docker Compose Check
            from ldm_core.utils import get_compose_cmd

            compose_bin = get_compose_cmd()
            if compose_bin:
                results.append(("Docker Compose", "Plugin v2 Detected", True))
            else:
                results.append(("Docker Compose", "Plugin NOT FOUND", False))
                add_hint(
                    "LDM requires the Docker Compose V2 plugin. Please install it via your Docker provider settings."
                )

            # 2.1 Docker Credentials Check
            creds_status, creds_ok = self._check_docker_creds()
            if creds_status:
                results.append(("Docker Creds Store", creds_status, creds_ok))

            # 2.2 Docker Resources
            docker_info_raw = run_command(
                ["docker", "info", "--format", "{{json .}}"], check=False
            )
            if docker_info_raw:
                res_results = self._check_docker_resources(docker_info_raw)
                for comp, stat, ok in res_results:
                    results.append((comp, stat, ok))
                    if ok is not True:
                        res_type = "CPU cores" if "CPU" in comp else "RAM"
                        add_hint(
                            f"Allocate more {res_type} in your Docker provider settings.",
                            "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#docker-resource-alignment-windowswsl2macos",
                        )
        else:
            # Trigger the detailed error reporting from base.py
            self.check_docker()
            results.append(("Docker Engine", "Not reachable", False))
            add_hint(
                "If Docker is running but LDM cannot connect, ensure your user is in the 'docker' group.",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#linux--wsl-docker-permissions",
            )
            if is_wsl:
                add_hint(
                    "WSL: To use Native Docker, run: 'sudo service docker start'",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#wsl2-native-docker",
                )
                add_hint(
                    "WSL: To use Docker Desktop, ensure 'WSL Integration' is enabled in the Docker Desktop dashboard."
                )

        # 3. mkcert Check
        mkcert_status, mkcert_ok, ca_root = self.check_mkcert()

        # 4. Volume Write Test (Proactive detection of RO mounts)
        if docker_version and project_paths:
            # Test the first project found
            test_path = project_paths[0]
            try:
                # We spin up a tiny container and try to touch a file as UID 1000 (Liferay user)
                # This catches the 'VOLUME MOUNT IS READ-ONLY' issue seen in Colima/WSL
                from ldm_core.utils import get_actual_home

                rel_path = test_path.relative_to(get_actual_home())

                res = self.run_command(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{test_path}:/test-mount",
                        "alpine",
                        "sh",
                        "-c",
                        "touch /test-mount/.ldm-write-test && rm /test-mount/.ldm-write-test",
                    ],
                    check=False,
                )

                if "Permission denied" in str(res) or "Read-only file system" in str(
                    res
                ):
                    results.append(("Volume Permissions", "❌ Read-Only", False))
                    add_hint(
                        "Your Docker volume mounts are read-only for the 'liferay' user (UID 1000).",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#troubleshooting-read-only-mounts",
                    )
                    if provider == "Colima":
                        # Check for custom LaunchAgent setup
                        if (
                            Path(get_actual_home())
                            / "Library/LaunchAgents/com.github.abiosoft.colima.plist"
                        ).exists():
                            add_hint(
                                "Detected custom Colima LaunchAgent. Please update your '/usr/local/bin/colima-start-fg' script."
                            )
                            add_hint(
                                'Change to: colima start --mount-type virtiofs --mount "$HOME:w"'
                            )
                        else:
                            # Detect architecture
                            arch = platform.machine().lower()
                            is_intel = "x86" in arch or "i386" in arch

                            if mount_type == "sshfs":
                                add_hint(
                                    "Colima Fix: Your mount type is 'sshfs'. You MUST add ':w' to your mount paths for write access."
                                )
                                add_hint(
                                    "Run: 'colima stop' then 'colima start --mount [HOME]:w'"
                                )
                            else:
                                add_hint(
                                    "Colima Fix (Standard): 'colima stop' then 'colima start --mount [HOME]:w'"
                                )

                            if is_intel:
                                add_hint(
                                    "Note: If performance is poor, try: 'colima stop' then 'colima start --mount [HOME]:w --vm-type=vz'"
                                )
                            else:
                                add_hint(
                                    "Colima Fix (Advanced): 'colima stop' then 'colima start --mount [HOME]:w --vm-type=vz --mount-type=virtiofs'",
                                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/colima-performance-tuning",
                                )

                        add_hint(
                            "Recommended: Upgrade Colima to the latest version ('brew upgrade colima') for better macOS integration."
                        )

                else:
                    results.append(("Volume Permissions", "✅ Writable", True))
            except Exception:
                # Skip if we can't perform the test (e.g. path outside home)
                pass

        # Escalate to error if project requires SSL
        if mkcert_ok == "warn" and requires_ssl:
            mkcert_ok = False

        results.append(("mkcert", mkcert_status, mkcert_ok))
        if mkcert_ok is not True:
            if mkcert_status == "Not installed":
                add_hint(
                    "Install 'mkcert' to enable local SSL (brew install mkcert / scoop install mkcert / apt install mkcert).",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#prerequisites",
                )
            elif "Permission Denied" in mkcert_status:
                cert_dir = get_actual_home() / "liferay-docker-certs"
                add_hint(
                    f"Fix permissions: {UI.WHITE}sudo chown -R $USER {cert_dir.parent}{UI.COLOR_OFF}"
                )
            else:
                add_hint(
                    f"Run '{UI.WHITE}mkcert -install{UI.COLOR_OFF}' to initialize the local trust store.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#fixing-ssl-trust-issues-mkcert",
                )
        else:
            # Detect WSL
            is_wsl = False
            if platform.system().lower() == "linux":
                try:
                    with open("/proc/version", "r") as f:
                        if "microsoft" in f.read().lower():
                            is_wsl = True
                except Exception:
                    pass

            if is_wsl:
                # Check if CAROOT points to Windows
                is_win_ca = ca_root and "/mnt/c/" in ca_root
                if not is_win_ca:
                    add_hint(
                        f"[WSL] Your browser won't trust WSL certificates. Run '{UI.WHITE}mkcert -install{UI.COLOR_OFF}' on Windows, then in WSL set: {UI.WHITE}export CAROOT=\"/mnt/c/Users/<user>/AppData/Local/mkcert\"{UI.COLOR_OFF}",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#wsl2-ssl-trust",
                    )
                else:
                    add_hint(
                        f"[WSL] To avoid 'Insecure' browser warnings, you must ALSO run '{UI.WHITE}mkcert -install{UI.COLOR_OFF}' on your Windows host (via PowerShell or CMD).",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#wsl2-ssl-trust",
                    )

        # 4. OpenSSL Check
        openssl_status, openssl_ok = self._check_openssl()
        results.append(("OpenSSL", openssl_status, openssl_ok))
        if openssl_ok is not True:
            add_hint(
                "Install OpenSSL (available via brew, macports, scoop, or apt).",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#prerequisites",
            )

        # 4.1 Core Orchestration Tools & Path Integrity
        nc_bin = shutil.which("nc")
        ncat_bin = shutil.which("ncat")
        active_nc = nc_bin or ncat_bin

        tool_list = [
            ("docker", shutil.which("docker")),
            ("mkcert", shutil.which("mkcert")),
            ("openssl", shutil.which("openssl")),
            ("telnet", shutil.which("telnet")),
            ("nc/ncat", active_nc),
            ("lcp", shutil.which("lcp")),
        ]

        # Add Docker Compose (detect if it's the plugin or standalone)
        from ldm_core.utils import get_compose_cmd

        compose_bin = get_compose_cmd()
        if compose_bin:
            tool_list.append(("docker compose", " ".join(compose_bin)))

        for tool_name, tool_path in tool_list:
            if tool_path:
                results.append((f"Path: {tool_name}", str(tool_path), True))
            else:
                # Some are optional/warn only
                if tool_name in ["telnet", "lcp", "mkcert", "nc/ncat"]:
                    results.append((f"Path: {tool_name}", "Not Found", "warn"))
                else:
                    results.append((f"Path: {tool_name}", "Not Found", False))

        # 4.1.5 Optional Database Clients (Recommended for developers)
        # Note: LDM uses 'docker exec' for snapshots, so local clients are NOT required for LDM operations.
        for db_tool in ["mysql", "psql"]:
            tool_path = shutil.which(db_tool)
            if tool_path:
                results.append((f"Client: {db_tool}", str(tool_path), True))
            else:
                results.append(
                    (
                        f"Client: {db_tool}",
                        f"{UI.WHITE}Not installed{UI.COLOR_OFF}",
                        "warn",
                    )
                )
                add_hint(
                    f"Optional: Install '{db_tool}' on your host to manually inspect databases from outside Docker."
                )

        # 4.2 Legacy Compatibility Checks (Maintain existing summary lines)
        telnet_bin = shutil.which("telnet")
        nc_bin = shutil.which("nc")
        ncat_bin = shutil.which("ncat")  # Nmap's netcat version
        lcp_bin = shutil.which("lcp")

        results.append(
            (
                "Tool: telnet",
                "Installed" if telnet_bin else "Missing (Gogo Shell disabled)",
                True if telnet_bin else "warn",
            )
        )
        if not telnet_bin:
            if platform.system().lower() == "windows":
                add_hint(
                    "To enable telnet on Windows, run this in an Admin PowerShell: "
                    f"'{UI.WHITE}Enable-WindowsOptionalFeature -Online -FeatureName TelnetClient{UI.COLOR_OFF}'"
                )
            else:
                add_hint(
                    "Install telnet for Gogo Shell support (e.g. 'brew install telnet' or 'apt-get install telnet')."
                )

        # Netcat / Ncat check
        active_nc = nc_bin or ncat_bin
        results.append(
            (
                "Tool: netcat (nc/ncat)",
                "Installed" if active_nc else "Missing (Log Level sync disabled)",
                True if active_nc else "warn",
            )
        )
        if not active_nc:
            if platform.system().lower() == "windows":
                add_hint(
                    f"Install nmap for Windows to get '{UI.WHITE}ncat{UI.COLOR_OFF}': "
                    f"'{UI.WHITE}winget install Insecure.Nmap{UI.COLOR_OFF}'"
                )
            else:
                add_hint("Install netcat for log-level synchronization.")

        results.append(
            (
                "Tool: lcp cli",
                "Installed" if lcp_bin else "Missing (Cloud Fetch disabled)",
                True if lcp_bin else "warn",
            )
        )
        if not lcp_bin:
            add_hint(
                "Install Liferay Cloud CLI for 'cloud-fetch' support.",
                "https://customer.liferay.com/downloads/-/download/liferay-cloud-cli",
            )

        # 4.2 Liferay Cloud Check
        lcp_status, lcp_ok = self._check_lcp_cli()
        if lcp_status:
            results.append(("Liferay Cloud Auth", lcp_status, lcp_ok))

        # 4.2 Project Health (if specific project is being checked)
        if project_paths and len(project_paths) == 1:
            p_path = project_paths[0]
            meta = self.read_meta(p_path)
            if meta:
                is_seeded = str(meta.get("seeded", "false")).lower() == "true"
                s_version = meta.get("seed_version")
                if is_seeded:
                    results.append(
                        (
                            "Project Initialization",
                            f"✅ Seeded (v{s_version if s_version else 'unknown'})",
                            True,
                        )
                    )
                else:
                    results.append(
                        ("Project Initialization", "Vanilla (Not Seeded)", "warn")
                    )

        # 4.3 Global Config Check
        base_path = project_paths[0] if project_paths else None
        common_dir = self.get_common_dir(base_path)
        search_version = 8  # Default

        if not common_dir.exists():
            results.append(
                ("Global Config", "Missing ('ldm init-common' available)", "warn")
            )
            add_hint(
                f"Run '{UI.WHITE}ldm init-common{UI.COLOR_OFF}' to restore standard development assets.",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#global-configuration-the-common-folder",
            )
        else:
            try:
                import importlib.resources as pkg_resources
                from ldm_core import resources

                pe_file = common_dir / "portal-ext.properties"
                if not pe_file.exists():
                    results.append(
                        ("Global Config", "Overrides Active (no baseline)", True)
                    )
                else:
                    baseline_content = (
                        pkg_resources.files(resources)
                        / "common_baseline"
                        / "portal-ext.properties"
                    ).read_text()
                    if pe_file.read_text().strip() == baseline_content.strip():
                        results.append(
                            ("Global Config", f"Baseline (v{VERSION})", True)
                        )
                    else:
                        prop_status, prop_ok, prop_details = (
                            self.validate_properties_file(pe_file)
                        )
                        if prop_ok is True:
                            results.append(("Global Config", "Custom Overrides", True))
                        else:
                            results.append(
                                (
                                    "Global Config",
                                    f"Invalid Format ({prop_status})",
                                    prop_ok,
                                )
                            )
                            add_hint(
                                f"Verify the syntax in '{UI.WHITE}common/portal-ext.properties{UI.COLOR_OFF}'."
                            )
                            if prop_details:
                                for detail in prop_details:
                                    print(
                                        f"  {UI.YELLOW}⚠{UI.COLOR_OFF} [Global] {detail}"
                                    )

                search_inspect = run_command(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.Config.Image}}",
                        "liferay-search-global",
                    ],
                    check=False,
                )
                search_version = 8
                if search_inspect and ":7." in search_inspect:
                    search_version = 7

                v_id = "elasticsearch7" if search_version == 7 else "elasticsearch8"
                es_main = (
                    common_dir
                    / f"com.liferay.portal.search.{v_id}.configuration.ElasticsearchConfiguration.config"
                )
                es_conn = (
                    common_dir
                    / f"com.liferay.portal.search.{v_id}.configuration.ElasticsearchConnectionConfiguration-REMOTE.config"
                )

                if not es_main.exists() or not es_conn.exists():
                    results.append(
                        (
                            "Global Search Config",
                            f"Missing {v_id.upper()} (Run 'ldm init-common')",
                            "warn",
                        )
                    )
                    add_hint(
                        f"Run '{UI.WHITE}ldm init-common{UI.COLOR_OFF}' to restore search configuration files."
                    )
                else:
                    msg = f"REMOTE mode ready ({v_id.upper()})"
                    if search_version == 8:
                        compat_main = (
                            common_dir
                            / "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConfiguration.config"
                        )
                        if compat_main.exists():
                            msg = "REMOTE mode ready (ES8 + ES7 Compat)"

                    results.append(("Global Search Config", msg, True))
            except Exception:
                results.append(("Global Config", "Overrides Active", True))

        # 5. Network Check
        if docker_version:
            has_net = run_command(
                ["docker", "network", "inspect", "liferay-net"], check=False
            )
            if has_net:
                results.append(("Docker Network", "liferay-net exists", True))
            else:
                results.append(
                    ("Docker Network", "missing (will be created on run)", "warn")
                )
                add_hint(
                    "Shared infrastructure network 'liferay-net' will be created during project startup.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#infra-setup-infra-down-infra-restart",
                )

            # 6. Global Services Check
            search_label = "Global Search (ES8)"
            if search_version == 7:
                search_label = "Global Search (ES7)"

            global_services = [
                ("liferay-proxy-global", "Global SSL Proxy"),
                ("liferay-search-global", search_label),
            ]

            if platform.system().lower() == "darwin":
                global_services.append(("liferay-docker-proxy", "Docker Socket Bridge"))

            for container, label in global_services:
                is_running = run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{container}$"], check=False
                )
                if is_running:
                    status = "Running"
                    ok = True

                    # Version check for Global Search
                    if container == "liferay-search-global":
                        from ldm_core.constants import (
                            ELASTICSEARCH_VERSION,
                            ELASTICSEARCH7_VERSION,
                        )

                        # Discover latest tag if not already known
                        tag = None
                        if project_paths:
                            first_meta = self.read_meta(project_paths[0])
                            tag = first_meta.get("tag")

                        target_ver = resolve_dependency_version(tag, "elasticsearch")
                        if not target_ver:
                            target_ver = (
                                ELASTICSEARCH_VERSION
                                if search_version == 8
                                else ELASTICSEARCH7_VERSION
                            )

                        running_ver = run_command(
                            [
                                "docker",
                                "inspect",
                                "-f",
                                "{{.Config.Image}}",
                                container,
                            ],
                            check=False,
                        )
                        if running_ver and target_ver not in running_ver:
                            status = f"Running (OUTDATED: {running_ver.split(':')[-1]})"
                            ok = False
                            add_hint(
                                f"Your Global Search container is outdated. Please run '{UI.WHITE}ldm infra-restart --search{UI.COLOR_OFF}'."
                            )

                    if container == "liferay-docker-proxy":
                        inspect = run_command(
                            [
                                "docker",
                                "network",
                                "inspect",
                                "liferay-net",
                                "-f",
                                "{{range .Containers}}{{.Name}} {{end}}",
                            ],
                            check=False,
                        )
                        if container not in (inspect or ""):
                            status = "Isolated (Connect with ldm infra-setup)"
                            ok = "warn"
                            add_hint(
                                f"Run '{UI.WHITE}ldm infra-setup{UI.COLOR_OFF}' to reconnect the macOS socket bridge."
                            )

                    if container == "liferay-search-global":
                        try:
                            search_res = run_command(
                                [
                                    "docker",
                                    "exec",
                                    "liferay-search-global",
                                    "curl",
                                    "-s",
                                    "-o",
                                    "/dev/null",
                                    "-w",
                                    "%{http_code}",
                                    "http://localhost:9200",
                                ],
                                check=False,
                            )
                            if search_res != "200" and search_res != "401":
                                status = f"Unreachable (HTTP {search_res})"
                                ok = "warn"
                                add_hint(
                                    f"Run '{UI.WHITE}ldm infra-restart --search{UI.COLOR_OFF}' if the search cluster is unresponsive."
                                )
                        except Exception:
                            pass

                    log_status, log_ok = self._check_container_health_logs(
                        container, add_hint=add_hint
                    )
                    if log_status:
                        status = log_status
                        ok = log_ok

                    results.append((label, status, ok))
                else:
                    cmd_hint = "ldm infra-setup"
                    if container == "liferay-search-global":
                        cmd_hint = "ldm infra-setup --search"
                        results.append(
                            (
                                label,
                                f"Not running (Run '{cmd_hint}') {UI.WHITE}(Sidecar will be used){UI.COLOR_OFF}",
                                "warn",
                            )
                        )
                    else:
                        results.append(
                            (label, f"Not running (Run '{cmd_hint}')", "warn")
                        )
                    add_hint(
                        f"Start shared infrastructure by running '{UI.WHITE}{cmd_hint}{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#infra-setup-infra-down-infra-restart",
                    )

            # 7. Tag Discovery Check
            from ldm_core.constants import API_BASE_DXP
            from ldm_core.utils import discover_latest_tag

            try:
                # Use a cached/quick check for doctor
                latest_tag = discover_latest_tag(
                    API_BASE_DXP, release_type="lts", verbose=False
                )
                if latest_tag:
                    results.append(
                        (
                            "Liferay Docker Tags",
                            f"Available (Latest: {latest_tag})",
                            True,
                        )
                    )
                else:
                    results.append(
                        (
                            "Liferay Docker Tags",
                            "Unavailable (Discovery failed)",
                            "warn",
                        )
                    )
                    add_hint("Docker Hub API might be rate-limited or unreachable.")
            except Exception as e:
                results.append(
                    ("Liferay Docker Tags", f"Error ({str(e)[:30]}...)", "warn")
                )
        else:
            results.append(("Docker Network", "Skipped (Engine down)", "warn"))
            results.append(("Global Infrastructure", "Skipped (Engine down)", "warn"))

        # 7. Project-Specific Check (Optional)
        for p_path in project_paths:
            UI.heading(f"Project Health: {p_path.name}")
            meta = self.read_meta(p_path)
            env_args = meta.get("env_args", [])
            blacklisted_prefixes = [
                "LIFERAY_ELASTICSEARCH",
                "LIFERAY_WEB_SERVER",
                "LIFERAY_VIRTUAL_HOSTS",
                "LIFERAY_CLUSTER",
                "LIFERAY_LUCENE",
                "COM_LIFERAY_LXC_DXP",
            ]
            poisoned = [
                arg
                for arg in env_args
                if any(arg.startswith(p) for p in blacklisted_prefixes)
            ]
            if poisoned:
                results.append(
                    (
                        f"[{p_path.name}] Metadata",
                        f"Poisoned ({len(poisoned)} legacy vars)",
                        "warn",
                    )
                )
                add_hint(
                    f"[{p_path.name}] Clean legacy environment variables by running '{UI.WHITE}ldm run {p_path.name}{UI.COLOR_OFF}'.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#env",
                )
            else:
                results.append((f"[{p_path.name}] Metadata", "Healthy", True))

            if self.require_compose(p_path, silent=True):
                results.append(
                    (f"[{p_path.name}] Config", "docker-compose.yml OK", True)
                )
            else:
                results.append(
                    (f"[{p_path.name}] Config", "docker-compose.yml MISSING", False)
                )
                add_hint(
                    f"[{p_path.name}] Regenerate missing configuration by running '{UI.WHITE}ldm run {p_path.name}{UI.COLOR_OFF}'.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#run-alias-up",
                )

            pe_file = p_path / "files" / "portal-ext.properties"
            if pe_file.exists():
                prop_status, prop_ok, prop_details = self.validate_properties_file(
                    pe_file
                )
                results.append((f"[{p_path.name}] Properties", prop_status, prop_ok))
                if prop_ok is not True:
                    add_hint(
                        f"[{p_path.name}] Verify syntax in '{UI.WHITE}{p_path.name}/files/portal-ext.properties{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/LDM_ARCHITECTURE.md#5-metadata--property-injection",
                    )
                    if prop_details:
                        for detail in prop_details:
                            UI.raw(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {detail}")
            else:
                results.append(
                    (
                        f"[{p_path.name}] Properties",
                        "portal-ext.properties MISSING",
                        "warn",
                    )
                )

            # --- Liferay Log Health ---
            from ldm_core.utils import sanitize_id

            p_id = sanitize_id(meta.get("container_name") or p_path.name)
            liferay_container = None
            possible_names = [f"{p_id}-liferay", f"{p_id}-liferay-1", p_id]
            for name in possible_names:
                if run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{name}$"], check=False
                ):
                    liferay_container = name
                    break

            if liferay_container:
                log_status, log_ok = self._check_liferay_health_logs(liferay_container)
                results.append((f"[{p_path.name}] Liferay Logs", log_status, log_ok))
                if log_ok is not True:
                    add_hint(
                        f"[{p_path.name}] Check detailed Liferay logs by running '{UI.WHITE}ldm logs {p_path.name} liferay{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#logs",
                    )

            osgi_config_dir = p_path / "osgi" / "configs"

            # Smart Detection based on Liferay Version
            is_es8 = self.parse_version(meta.get("tag")) >= (2024, 1, 0)
            es_ver = "8" if is_es8 else "7"

            es_main_conf = (
                osgi_config_dir
                / f"com.liferay.portal.search.elasticsearch{es_ver}.configuration.ElasticsearchConfiguration.config"
            )
            es_conn_conf = (
                osgi_config_dir
                / f"com.liferay.portal.search.elasticsearch{es_ver}.configuration.ElasticsearchConnectionConfiguration.config"
            )

            if es_main_conf.exists() and es_conn_conf.exists():
                results.append(
                    (f"[{p_path.name}] OSGi Search", "REMOTE mode detected", True)
                )
            elif es_main_conf.exists() or es_conn_conf.exists():
                results.append(
                    (f"[{p_path.name}] OSGi Search", "Partial / Incomplete", "warn")
                )
                add_hint(
                    f"[{p_path.name}] Ensure both Elasticsearch configs exist in '{UI.WHITE}osgi/configs/{UI.COLOR_OFF}'."
                )
            else:
                results.append(
                    (
                        f"[{p_path.name}] OSGi Search",
                        "Missing (Liferay will start sidecar)",
                        "warn",
                    )
                )
                add_hint(
                    f"[{p_path.name}] Enable global search by running '{UI.WHITE}ldm migrate-search {p_path.name}{UI.COLOR_OFF}'.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#migrate-search",
                )

            # 7.2.3 LCP.json Validation (Extensions)
            for lcp_file in p_path.rglob("LCP.json"):
                # Avoid validating LCP.json in the project root if it's not a service
                # (Standard Liferay Cloud workspaces have a root LCP.json)
                rel_path = lcp_file.relative_to(p_path)
                lcp_status, lcp_ok, lcp_errors = self.validate_lcp_json(lcp_file)
                results.append((f"Extension Config ({rel_path})", lcp_status, lcp_ok))
                if lcp_errors:
                    for err in lcp_errors:
                        UI.raw(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {err}")

            # 7.2.4 License Check
            lic_status, lic_ok, lic_details = self.check_license_health(
                {"common": common_dir, **self.setup_paths(p_path)},
                image_tag=meta.get("tag"),
            )
            results.append((f"[{p_path.name}] License", lic_status, lic_ok))
            if lic_ok is not True:
                add_hint(
                    f"[{p_path.name}] Place a valid DXP license in global '{UI.WHITE}common/{UI.COLOR_OFF}' or '{UI.WHITE}deploy/{UI.COLOR_OFF}'.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/LDM_ARCHITECTURE.md#key-architectural-pillars",
                )
            if lic_details:
                for detail in lic_details:
                    UI.raw(f"  {UI.CYAN}ℹ{UI.COLOR_OFF} {detail}")

            host_name = meta.get("host_name", "localhost")
            ssl_enabled = str(meta.get("ssl", "false")).lower() == "true"
            ssl_cert_name = meta.get("ssl_cert")

            if ssl_enabled and host_name != "localhost":
                actual_home = get_actual_home()
                cert_dir = actual_home / "liferay-docker-certs"
                cert_file = cert_dir / (ssl_cert_name or f"{host_name}.pem")
                key_file = cert_dir / cert_file.name.replace(".pem", "-key.pem")
                traefik_conf = cert_dir / f"traefik-{host_name}.yml"

                if cert_file.exists() and key_file.exists():
                    cert_status = "Cert & Key OK"
                    openssl_bin = shutil.which("openssl")
                    if openssl_bin:
                        try:
                            expiry_res = run_command(
                                [
                                    openssl_bin,
                                    "x509",
                                    "-enddate",
                                    "-noout",
                                    "-in",
                                    str(cert_file),
                                ],
                                check=False,
                            )
                            if expiry_res and "notAfter=" in expiry_res:
                                expiry_str = expiry_res.split("=", 1)[1].strip()

                                # Parse OpenSSL date: "Feb 24 10:57:51 2123 GMT"
                                try:
                                    from datetime import datetime

                                    # We try multiple common OpenSSL formats
                                    formats = [
                                        "%b %d %H:%M:%S %Y %Z",
                                        "%b %d %H:%M:%S %Y",
                                    ]
                                    expiry_dt = None
                                    for fmt in formats:
                                        try:
                                            # Strip potential double spaces (sometimes seen in openssl output)
                                            clean_expiry = " ".join(expiry_str.split())
                                            expiry_dt = datetime.strptime(
                                                clean_expiry, fmt
                                            )
                                            break
                                        except Exception:  # nosec B112
                                            continue

                                    if expiry_dt:
                                        now = datetime.now()
                                        diff = expiry_dt - now
                                        days = diff.days

                                        if days < 0:
                                            cert_status = (
                                                f"EXPIRED ({abs(days)} days ago)"
                                            )
                                            ok = False
                                        elif days < 30:
                                            cert_status = (
                                                f"Expires in {days} days! (Renew soon)"
                                            )
                                            ok = "warn"
                                        else:
                                            cert_status = (
                                                f"Valid ({days} days remaining)"
                                            )
                                except Exception:
                                    cert_status = f"Valid until {expiry_str}"
                        except Exception:
                            pass
                    results.append((f"[{p_path.name}] SSL Cert", cert_status, ok))
                else:
                    results.append(
                        (
                            f"[{p_path.name}] SSL Cert",
                            "Missing (.pem or -key.pem)",
                            False,
                        )
                    )
                    add_hint(
                        f"[{p_path.name}] Regenerate SSL certificates by running '{UI.WHITE}ldm run {p_path.name} --force-ssl{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#fixing-ssl-trust-issues-mkcert",
                    )

                if traefik_conf.exists():
                    conf_content = traefik_conf.read_text()
                    expected_cert = f"certFile: /etc/traefik/certs/{host_name}.pem"
                    expected_key = f"keyFile: /etc/traefik/certs/{host_name}-key.pem"

                    if expected_cert in conf_content and expected_key in conf_content:
                        results.append(
                            (f"[{p_path.name}] Traefik SSL", "Config OK", True)
                        )
                    else:
                        results.append(
                            (f"[{p_path.name}] Traefik SSL", "Invalid Content", "warn")
                        )
                        add_hint(
                            f"[{p_path.name}] Regenerate Traefik routing by running '{UI.WHITE}ldm run {p_path.name} --force-ssl{UI.COLOR_OFF}'.",
                            "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#ssl-defaults-new-projects",
                        )
                else:
                    results.append(
                        (f"[{p_path.name}] Traefik SSL", "Config MISSING", False)
                    )
                    add_hint(
                        f"[{p_path.name}] Regenerate Traefik routing by running '{UI.WHITE}ldm run {p_path.name} --force-ssl{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#ssl-defaults-new-projects",
                    )

            compose_file = p_path / "docker-compose.yml"
            if compose_file.exists():
                try:
                    import yaml

                    with open(compose_file, "r") as f:
                        compose_data = yaml.safe_load(f)

                    liferay_service = compose_data.get("services", {}).get(
                        "liferay", {}
                    )
                    labels = liferay_service.get("labels", [])
                    label_list = []
                    if isinstance(labels, list):
                        label_list = labels
                    elif isinstance(labels, dict):
                        label_list = [f"{k}={v}" for k, v in labels.items()]

                    p_id = meta.get("container_name") or p_path.name
                    has_net_label = any(
                        "traefik.docker.network=liferay-net" in label
                        for label in label_list
                    )
                    double_prefixed = []
                    for label in label_list:
                        if "=" in label:
                            key = label.split("=", 1)[0]
                            if key.count(p_id) > 1:
                                double_prefixed.append(key)

                    if not has_net_label:
                        results.append(
                            (
                                f"[{p_path.name}] Traefik Labels",
                                "Missing Net Label",
                                False,
                            )
                        )
                        add_hint(
                            f"[{p_path.name}] Fix Traefik labels by running '{UI.WHITE}ldm run {p_path.name}{UI.COLOR_OFF}'.",
                            "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#dns--subdomain-configuration",
                        )
                    elif double_prefixed:
                        results.append(
                            (
                                f"[{p_path.name}] Traefik Labels",
                                f"Double Prefixed ({len(double_prefixed)} labels)",
                                "warn",
                            )
                        )
                        for dp in double_prefixed:
                            UI.raw(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {dp}")
                        add_hint(
                            f"[{p_path.name}] Standardize Traefik labels by running '{UI.WHITE}ldm run {p_path.name}{UI.COLOR_OFF}'.",
                            "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#command-reference",
                        )
                    else:
                        results.append(
                            (f"[{p_path.name}] Traefik Labels", "Standardized OK", True)
                        )
                except Exception as e:
                    results.append(
                        (
                            f"[{p_path.name}] Traefik Labels",
                            f"Check Failed ({e})",
                            "warn",
                        )
                    )

            dns_res = self.validate_project_dns(p_path)
            dns_ok = dns_res[0]
            unresolved = dns_res[1]
            non_local = dns_res[2]

            if host_name != "localhost":
                fix_hosts = getattr(self.args, "fix_hosts", False)
                needs_fix = unresolved + [h for h, ip in non_local]

                if dns_ok and not non_local:
                    results.append(
                        (
                            f"[{p_path.name}] DNS ({host_name})",
                            "All domains resolve",
                            True,
                        )
                    )
                elif fix_hosts and needs_fix:
                    if self._apply_hosts_fix(needs_fix):
                        results.append(
                            (
                                f"[{p_path.name}] DNS ({host_name})",
                                "Fixed (Appended to hosts)",
                                True,
                            )
                        )
                    else:
                        results.append(
                            (
                                f"[{p_path.name}] DNS ({host_name})",
                                "Fix failed (Permission denied?)",
                                False,
                            )
                        )
                elif non_local and not unresolved:
                    # Resolves but to an unexpected IP (e.g. 10.0.0.99)
                    ip_list = [f"{h}={ip}" for h, ip in non_local]
                    results.append(
                        (
                            f"[{p_path.name}] DNS ({host_name})",
                            f"Non-local IP ({', '.join(ip_list)})",
                            "warn",
                        )
                    )
                    add_hint(
                        f"[{p_path.name}] Hostname resolves to an external IP. Point it to 127.0.0.1 in your local hosts file or run '{UI.WHITE}ldm doctor --fix-hosts{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#dns--subdomain-configuration",
                    )
                else:
                    results.append(
                        (
                            f"[{p_path.name}] DNS ({host_name})",
                            f"{len(unresolved)} domain(s) unresolved",
                            False,
                        )
                    )
                    add_hint(
                        f"[{p_path.name}] Add missing hostnames to your local hosts file or run '{UI.WHITE}ldm doctor --fix-hosts{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#dns--subdomain-configuration",
                    )
                    for d in unresolved:
                        UI.raw(f"  {UI.RED}×{UI.COLOR_OFF} {d}")

            # 7.2.5 Database Version Check
            db_type = meta.get("db_type", "hypersonic")
            if db_type in ["mysql", "postgresql", "mariadb"]:
                db_container = f"{p_id}-db"
                # Check if running
                is_db_running = run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{db_container}$"], check=False
                )
                if is_db_running:
                    target_db_ver = resolve_dependency_version(meta.get("tag"), db_type)
                    running_db_ver = run_command(
                        ["docker", "inspect", "-f", "{{.Config.Image}}", db_container],
                        check=False,
                    )
                    if (
                        target_db_ver
                        and running_db_ver
                        and target_db_ver not in running_db_ver
                    ):
                        results.append(
                            (
                                f"[{p_path.name}] DB Version",
                                f"OUTDATED ({running_db_ver.split(':')[-1]})",
                                "warn",
                            )
                        )
                        add_hint(
                            f"[{p_path.name}] Database image is outdated. Run '{UI.WHITE}ldm run {p_path.name}{UI.COLOR_OFF}' to update."
                        )
                    else:
                        results.append(
                            (f"[{p_path.name}] DB Version", "Up to date", True)
                        )

            # 7.2.6 Mount Integrity Check
            try:
                # Detect if the project is located on a Windows mount in WSL
                # This is a major source of permission and performance issues
                is_wsl_mount = False
                if platform.system().lower() == "linux" and "/mnt/" in str(
                    p_path.resolve()
                ):
                    is_wsl_mount = True

                if is_wsl_mount:
                    results.append(
                        (
                            f"[{p_path.name}] Mount Integrity",
                            "WSL Mount (/mnt/c) Detected",
                            False,
                        )
                    )
                    add_hint(
                        f"[{p_path.name}] Using Windows-mounted paths (/mnt/c) in WSL2 causes severe permission and performance issues with Liferay. "
                        f"Move your project to the native Linux filesystem (e.g. ~/repos/{p_path.name}).",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#linux--wsl-docker-permissions",
                    )
                else:
                    if platform.system().lower() == "darwin":
                        if liferay_container:
                            import uuid

                        token_val = f"DOCTOR_LIVE_{uuid.uuid4().hex[:8]}"
                        deploy_dir = p_path / "deploy"
                        token_file = deploy_dir / ".ldm_doctor_check"

                        try:
                            deploy_dir.mkdir(parents=True, exist_ok=True)
                            token_file.write_text(token_val)
                            verify_res = run_command(
                                [
                                    "docker",
                                    "exec",
                                    liferay_container,
                                    "cat",
                                    "/opt/liferay/deploy/.ldm_doctor_check",
                                ],
                                check=False,
                            )

                            if token_val in (verify_res or ""):
                                results.append(
                                    (f"[{p_path.name}] Mounts", "Live (OK)", True)
                                )
                            else:
                                results.append(
                                    (f"[{p_path.name}] Mounts", "BROKEN", False)
                                )
                                add_hint(
                                    f"[{p_path.name}] Ensure Docker has permission to share your home directory.",
                                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#the-ghost-mount-issue",
                                )
                        finally:
                            if token_file.exists():
                                token_file.unlink()
                    else:
                        results.append(
                            (f"[{p_path.name}] Mounts", "Verified on start", True)
                        )
            except Exception:
                pass

        # Print Results Table
        UI.raw(f"\n{'Component':<35} {'Status':<30}")
        UI.raw("-" * 75)

        all_ok, has_warnings = True, False
        for component, status, ok in results:
            if ok is True:
                color = UI.GREEN
                icon = "✅ "
            elif ok == "warn":
                color = UI.YELLOW
                icon = "⚠️ "
                has_warnings = True
            else:
                color = UI.RED
                icon = "❌ "
                all_ok = False
            UI.raw(f"{component:<35} {color}{icon} {status}{UI.COLOR_OFF}")

        # Print Actionable Hints at the end
        if hints:
            UI.raw(f"\n{UI.CYAN}--- Recommended Actions ---{UI.COLOR_OFF}")
            for h in hints:
                padding = UI.get_padding("ℹ")
                UI.raw(f"{UI.CYAN}ℹ{padding}{UI.COLOR_OFF}Fix: {h['text']}")
                if h["doc"]:
                    # Align with the start of 'Fix:'
                    # Icon(1) + Padding(2) = 3 spaces
                    UI.raw(f"   Doc: {UI.CYAN}{h['doc']}{UI.COLOR_OFF}")
                UI.raw("")

        if all_ok and not has_warnings:
            UI.success("Everything looks good! Your environment is ready.")
            sys.exit(0)
        elif all_ok and has_warnings:
            UI.warning("Some non-critical issues were detected. Check the items above.")
            sys.exit(0)
        else:
            UI.error("Critical issues were detected. Check the items above.")
            sys.exit(1)

    def check_mkcert(self):
        """Checks for mkcert installation, root CA trust, and write permissions."""
        try:
            mkcert_bin = shutil.which("mkcert")
            if not mkcert_bin:
                return "Not installed", "warn", None

            ca_root = run_command([mkcert_bin, "-CAROOT"], check=False)
            if not (ca_root and os.path.exists(ca_root) and os.listdir(ca_root)):
                return "Installed (Root CA NOT FOUND)", "warn", ca_root

            # Permission Check for global certs folder
            cert_dir = get_actual_home() / "liferay-docker-certs"
            if cert_dir.exists():
                if not os.access(cert_dir, os.W_OK):
                    return (
                        "Installed (Permission Denied to Certs Folder)",
                        "warn",
                        ca_root,
                    )
            elif not os.access(cert_dir.parent, os.W_OK):
                return (
                    "Installed (Permission Denied to Home Directory)",
                    "warn",
                    ca_root,
                )

            # Deep check for Root CA trust on macOS
            is_trusted = True
            if platform.system().lower() == "darwin":
                trust_check = run_command(
                    ["security", "find-certificate", "-c", "mkcert"],
                    check=False,
                )
                if not trust_check:
                    is_trusted = False

            if is_trusted:
                return "Installed (Root CA Trusted)", True, ca_root
            else:
                return "Installed (NOT TRUSTED)", "warn", ca_root
        except Exception:
            return "Not found in PATH", "warn", None

    def _check_openssl(self):
        """Checks for OpenSSL installation."""
        try:
            openssl_version = run_command(["openssl", "version"], check=False)
            if openssl_version:
                return openssl_version, True
            else:
                if platform.system().lower() == "windows":
                    return (
                        "Not found (Install Git for Windows, Scoop, or Chocolatey)",
                        False,
                    )
                else:
                    return "Not found", False
        except Exception:
            return "Not found in PATH", False

    def _check_lcp_cli(self):
        """Checks for Liferay Cloud CLI installation and authentication."""
        try:
            lcp_bin = shutil.which("lcp")
            if not lcp_bin:
                return "LCP CLI Not Installed", "warn"

            is_auth, _ = self._is_cloud_authenticated()
            if is_auth:
                return "Logged In", True
            else:
                return "Not Logged In (Run 'lcp login')", "warn"
        except Exception:
            return None, None

    def _check_docker_creds(self):
        """Checks for Docker credential store health."""
        try:
            docker_config_path = get_actual_home() / ".docker" / "config.json"
            if not docker_config_path.exists():
                return None, None

            with open(docker_config_path, "r") as f:
                config = json.loads(f.read())
                creds_store = config.get("credsStore")
                if not creds_store:
                    return None, None

                helper_bin = f"docker-credential-{creds_store}"
                if not shutil.which(helper_bin):
                    return f"Broken ({creds_store} helper missing)", False
                else:
                    return f"OK ({creds_store})", True
        except Exception:
            return None, None

    def _check_docker_resources(self, docker_info_raw):
        """Checks Docker host CPU and Memory resources."""
        try:
            info = json.loads(docker_info_raw)
            cpus = info.get("NCPU", 0)
            mem_bytes = info.get("MemTotal", 0)
            mem_gb = mem_bytes / (1024**3)

            results = []
            cpus_ok = True
            if cpus < 2:
                cpus_ok = False
            elif cpus < 4:
                cpus_ok = "warn"
            results.append(("Docker CPUs", f"{cpus} Cores", cpus_ok))
            if cpus_ok is not True:
                print(
                    f"  {UI.CYAN}ℹ{UI.COLOR_OFF} Hint: Allocate more CPU cores in your Docker provider settings."
                )
                print(
                    f"    Doc: {UI.CYAN}https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#docker-resource-alignment-windowswsl2macos{UI.COLOR_OFF}"
                )

            mem_ok = True
            if mem_gb < 4.0:
                mem_ok = False
            elif mem_gb < 7.5:
                mem_ok = "warn"
            results.append(("Docker Memory", f"{mem_gb:.1f} GB", mem_ok))
            if mem_ok is not True:
                print(
                    f"  {UI.CYAN}ℹ{UI.COLOR_OFF} Hint: Allocate more RAM in your Docker provider settings."
                )
                print(
                    f"    Doc: {UI.CYAN}https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/installation.md#docker-resource-alignment-windowswsl2macos{UI.COLOR_OFF}"
                )

            return results
        except Exception:
            return []

    def cmd_list(self):
        UI.heading("LDM Sandbox Projects")
        roots = self.find_dxp_roots()
        if not roots:
            UI.info("No projects found.")
            return

        print(
            f"{UI.WHITE}{'Project':<25} {'Version':<15} {'Status':<12} {'URL'}{UI.COLOR_OFF}"
        )
        print("-" * 80)

        for r in roots:
            path = r["path"]
            meta = self.read_meta(path)
            name = meta.get("container_name") or path.name
            version = r["version"]

            # Check container status
            containers_status = run_command(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    f"label=com.liferay.ldm.project={name}",
                    "--format",
                    "{{.State}}",
                ],
                check=False,
            )
            if containers_status:
                states = containers_status.splitlines()
                running_count = states.count("running")
                total_count = len(states)
                if total_count > 1:
                    status = (
                        f"Running ({running_count}/{total_count})"
                        if running_count > 0
                        else f"Stopped (0/{total_count})"
                    )
                    status_color = UI.GREEN if running_count > 0 else UI.WHITE
                else:
                    status = states[0].capitalize()
                    status_color = UI.GREEN if status == "Running" else UI.WHITE
            else:
                status = "Stopped"
                status_color = UI.WHITE

            # Access URL
            host = meta.get("host_name", "localhost")
            port = meta.get("port", "8080")
            ssl = str(meta.get("ssl")).lower() == "true"
            ssl_port = meta.get("ssl_port", "443")

            proto = "https" if ssl else "http"
            access_port = (
                f":{ssl_port}"
                if (ssl and ssl_port != "443")
                else (f":{port}" if not ssl else "")
            )
            url = f"{proto}://{host}{access_port}"

            # Seeded Indicator
            seeded = str(meta.get("seeded", "false")).lower() == "true"
            seeded_indicator = f" {UI.GREEN}🌱{UI.COLOR_OFF}" if seeded else ""

            print(
                f"{name + seeded_indicator:<35} {version:<15} {status_color}{status:<12}{UI.COLOR_OFF} {UI.CYAN}{url}{UI.COLOR_OFF}"
            )

    def cmd_prune(self):
        UI.heading("LDM Global Maintenance - Pruning Orphaned Resources")
        clean_hosts = getattr(self.args, "clean_hosts", False)

        roots = self.find_dxp_roots()
        active_projects = set()
        active_hostnames = set()
        for r in roots:
            meta = self.read_meta(r["path"])
            # Use container_name from meta, or fall back to folder name
            name = meta.get("container_name") or r["path"].name
            active_projects.add(name)
            host = meta.get("host_name")
            if host and host != "localhost":
                active_hostnames.add(host)

        if self.verbose:
            UI.debug(
                f"Active projects identified: {', '.join(active_projects) if active_projects else 'None'}"
            )

        # 1. Orphaned Containers
        # We look for containers with our management label
        containers_raw = run_command(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "label=com.liferay.ldm.managed=true",
                "--format",
                '{{.Names}}|{{.Label "com.liferay.ldm.project"}}',
            ],
            check=False,
        )

        orphans = []
        if containers_raw:
            for line in containers_raw.splitlines():
                line = line.strip()
                if not line or "|" not in line:
                    continue

                # Docker names can sometimes have a leading slash
                name, project = line.split("|", 1)
                name = name.lstrip("/")

                if not project or project not in active_projects:
                    orphans.append(name)

        if orphans:
            UI.info(f"Found {len(orphans)} orphaned containers from deleted projects:")
            for o in orphans:
                print(f"  - {o}")
            if self.non_interactive or UI.confirm("Remove them? (y/n/q)", "N"):
                for o in orphans:
                    run_command(["docker", "rm", "-f", o])
                UI.success("Orphaned containers removed.")
        else:
            UI.info("No orphaned containers found.")

        # 2. Orphaned Search Snapshots
        search_name = "liferay-search-global"
        if run_command(["docker", "ps", "-q", "-f", f"name={search_name}"]):
            snaps_raw = run_command(
                [
                    "docker",
                    "exec",
                    search_name,
                    "curl",
                    "-s",
                    "localhost:9200/_snapshot/liferay_backup/_all",
                ],
                check=False,
            )
            if snaps_raw:
                try:
                    data = json.loads(snaps_raw)
                    all_snaps = data.get("snapshots", [])
                    orphaned_snaps = []
                    for s in all_snaps:
                        s_name = s.get("snapshot", "")
                        # LDM search snapshots follow the pattern [project-name]-[timestamp]
                        if "-" in s_name:
                            project_id = s_name.rsplit("-", 2)[0]
                            if project_id not in active_projects:
                                orphaned_snaps.append(s_name)
                        elif s_name == "initial_snapshot":
                            # Special case for legacy manual snapshots
                            orphaned_snaps.append(s_name)

                    if orphaned_snaps:
                        UI.info(
                            f"Found {len(orphaned_snaps)} orphaned search snapshots:"
                        )
                        for s in orphaned_snaps:
                            print(f"  - {s}")
                        if self.non_interactive or UI.confirm(
                            "Remove them from global vault?", "N"
                        ):
                            for s in orphaned_snaps:
                                run_command(
                                    [
                                        "docker",
                                        "exec",
                                        search_name,
                                        "curl",
                                        "-s",
                                        "-X",
                                        "DELETE",
                                        f"localhost:9200/_snapshot/liferay_backup/{s}",
                                    ],
                                    check=False,
                                )
                            UI.success("Orphaned search snapshots removed.")
                    else:
                        UI.info("No orphaned search snapshots found.")
                except Exception:
                    pass

        # 3. Clean up .tmp files
        tmp_files = list(SCRIPT_DIR.glob("**/.*.tmp"))
        if tmp_files:
            UI.info(f"Found {len(tmp_files)} temporary files.")
            if self.non_interactive or UI.confirm("Remove them? (y/n/q)", "Y"):
                for f in tmp_files:
                    f.unlink()
                UI.success("Temporary files removed.")

        # 4. Orphaned SSL Certificates
        cert_dir = get_actual_home() / "liferay-docker-certs"
        if cert_dir.exists():
            orphaned_certs = []
            # Patterns to look for: {host}.pem, {host}-key.pem, traefik-{host}.yml
            for f in cert_dir.iterdir():
                if not f.is_file():
                    continue

                host = None
                if f.name.startswith("traefik-") and f.suffix == ".yml":
                    host = f.name[8:-4]
                elif f.name.endswith("-key.pem"):
                    host = f.name[:-8]
                elif f.suffix == ".pem":
                    host = f.name[:-4]

                if host and host not in active_hostnames:
                    orphaned_certs.append(f)

            if orphaned_certs:
                UI.info(f"Found {len(orphaned_certs)} orphaned SSL artifacts:")
                for c in orphaned_certs:
                    print(f"  - {c.name}")
                if self.non_interactive or UI.confirm(
                    "Remove them from global cert store?", "N"
                ):
                    for c in orphaned_certs:
                        c.unlink()
                    UI.success("Orphaned SSL artifacts removed.")
            else:
                UI.info("No orphaned SSL artifacts found.")

        # 5. DNS Cleanup (Explicitly requested via --clean-hosts)
        if clean_hosts:
            if UI.confirm("Remove ALL LDM-managed entries from your hosts file?", "N"):
                self._remove_hosts_entries(all_ldm=True)

        UI.info("Prune complete.")

    def validate_properties_file(self, file_path):
        """Checks for structural errors and duplicate keys in a .properties file."""
        errors = []
        try:
            lines = file_path.read_text().splitlines()
            if not lines:
                return "Empty File", "warn", []

            last_line_continued = False
            keys_found = {}  # key -> [line_numbers]
            for i, line in enumerate(lines):
                line_num = i + 1
                stripped = line.strip()

                # If the previous line ended in '\', this line MUST be a continuation
                if last_line_continued:
                    if not stripped:
                        errors.append(
                            f"Broken continuation (L{line_num}): Backslash followed by empty line"
                        )
                    elif "=" in stripped and not stripped.startswith(("#", "!")):
                        # Check if this looks like a new property starting too early
                        errors.append(
                            f"Merge collision (L{line_num}): Backslash followed by new property '{stripped[:15]}...'"
                        )

                # Update state for next line
                if not stripped or stripped.startswith(("#", "!")):
                    last_line_continued = False
                    continue

                # Normal orphaned line check (no '=' and not a continuation)
                if not last_line_continued and "=" not in stripped:
                    errors.append(f"Orphaned line (L{line_num}): '{stripped[:20]}...'")

                # Duplicate Key Detection
                if not last_line_continued and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key:
                        if key in keys_found:
                            keys_found[key].append(line_num)
                        else:
                            keys_found[key] = [line_num]

                last_line_continued = stripped.endswith("\\")

            # Report Duplicates
            for key, occurrences in keys_found.items():
                if len(occurrences) > 1:
                    errors.append(
                        f"Duplicate key '{key}' found on lines: {', '.join(map(str, occurrences))}"
                    )

            # Final line check: if the last line ends in '\', it's an invalid continuation
            if last_line_continued:
                errors.append(
                    "File ends with a trailing backslash (broken continuation)."
                )

            if errors:
                return f"Inconsistent ({len(errors)} issues)", "warn", errors
            return "Valid Structure", True, []
        except Exception as e:
            return f"Check Failed ({e})", "warn", []

    def validate_lcp_json(self, file_path):
        """Validates the structure and content of an LCP.json file."""
        errors = []
        try:
            content = file_path.read_text()
            data = json.loads(content)

            # 1. Mandatory ID
            if not data.get("id"):
                errors.append("Missing mandatory 'id' field.")

            # 2. Port Validation
            ports = data.get("ports", [])
            if not isinstance(ports, list):
                errors.append("'ports' must be an array.")
            else:
                for i, p in enumerate(ports):
                    if not isinstance(p, dict):
                        errors.append(f"Port at index {i} must be an object.")
                        continue
                    if not p.get("targetPort"):
                        errors.append(f"Port at index {i} missing 'targetPort'.")

            # 3. Load Balancer / External Port Consistency
            has_lb = "loadBalancer" in data
            has_external_port = any(
                p.get("external") for p in ports if isinstance(p, dict)
            )

            if has_lb and not has_external_port:
                errors.append(
                    "loadBalancer defined but no ports are marked as 'external: true'."
                )

            # 4. Resource Limits
            for res in ["cpu", "memory"]:
                val = data.get(res)
                if val is not None and not isinstance(val, (int, float)):
                    errors.append(f"'{res}' must be a numeric value.")

            if errors:
                return f"Inconsistent ({len(errors)} issues)", "warn", errors
            return "Valid Structure", True, []
        except json.JSONDecodeError as e:
            return f"Invalid JSON ({e})", False, [str(e)]
        except Exception as e:
            return f"Check Failed ({e})", "warn", [str(e)]

    def _check_container_health_logs(self, container_name, add_hint=None, tail=20):
        """Checks the last N lines of container logs for errors or warnings."""
        try:
            # We capture BOTH stdout and stderr
            import subprocess

            res = subprocess.run(
                ["docker", "logs", "--tail", str(tail), container_name],
                capture_output=True,
                text=True,
                check=False,
            )
            logs = (res.stdout or "") + (res.stderr or "")
            if not logs:
                return None, None

            # Strip ANSI color codes before analysis to ensure reliable string matching
            clean_logs = strip_ansi(logs)
            lines = clean_logs.splitlines()

            import re

            # 1. Critical Errors (Hard failure keywords)
            # Use word boundaries (\b) to avoid matching "error_logs" or other non-error strings
            critical_keywords = [
                r"\bERROR\b",
                r"\bERR\b",
                r"\bFATAL\b",
                r"\bCRITICAL\b",
                "Exit Code: 1",
                r"\bexception\b",
            ]
            for line in lines:
                line_lower = line.lower()
                if any(re.search(k, line, re.IGNORECASE) for k in critical_keywords):
                    # --- NOISE REDUCTION FILTERS ---
                    # 1. Ignore "error" strings within WARN level messages (common in JSON logs)
                    if "warn" in line.upper() and "error" in line_lower:
                        continue
                    # 2. Ignore known non-fatal "operation not supported" errors
                    if "operation not supported" in line_lower:
                        continue
                    # 3. Ignore benign AWS/S3 credential discovery failures (common in ES8)
                    if (
                        "failed to obtain region from default provider chain"
                        in line_lower
                    ):
                        continue
                    if (
                        "software.amazon.awssdk.core.exception.SdkClientException"
                        in line_lower
                    ):
                        continue
                    # 4. Ignore transient ES boot state errors
                    if (
                        "clusterblockexception" in line_lower
                        and "state not recovered" in line_lower
                    ):
                        continue
                    # 5. Detect Disk Pressure (Flood Stage)
                    if "flood stage disk watermark" in line_lower:
                        if add_hint:
                            add_hint(
                                "Elasticsearch disk pressure detected! Reclaim space or move Docker to a different drive.",
                                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/TROUBLESHOOTING.md#moving-docker-to-an-external-drive-macos--colima",
                            )
                        continue

                    return f"Critical (Error in logs: {line.strip()[:40]}...)", False

            # 2. Warnings
            warning_keywords = [
                r"\bWARN\b",
                r"\bWRN\b",
                r"\bWARNING\b",
                "retrying",
                "client version .* is too old",
            ]
            for line in lines:
                if any(re.search(k, line, re.IGNORECASE) for k in warning_keywords):
                    # Ignore benign Traefik container churn warnings
                    if "Failed to inspect container" in line:
                        continue
                    # Ignore benign SLF4J warnings
                    if "SLF4J:" in line:
                        continue
                    # Ignore ES deprecation warnings (very noisy in logs)
                    if "deprecation.elasticsearch" in line.lower():
                        continue
                    # Ignore benign HAProxy missing timeout warnings (Docker Socket Bridge)
                    if "missing timeouts" in line and "docker-events" in line:
                        continue
                    if line.strip().startswith("|") and any(
                        x in line for x in ["timeout", "problem", "invalid"]
                    ):
                        continue

                    # Ignore benign ES8 initialization status/info lines caught by regex
                    # Handles both "level": "INFO" (with space) and ECS "log.level": "INFO"
                    if "@timestamp" in line:
                        if re.search(
                            r'("(log\.)?level")\s*:\s*"(INFO|WARN)"',
                            line,
                            re.IGNORECASE,
                        ):
                            # ES8 Watermark filtering (Legitimate for clusters, but noisy in single-node dev)
                            if "flood stage disk watermark" in line.lower():
                                continue
                            # ES8 backup/repository registration warnings (handled by infra-setup)
                            if any(
                                x in line.lower()
                                for x in ["liferay_backup", "path.repo"]
                            ):
                                continue
                            if "ERROR" not in line.upper():
                                continue

                    return f"Warning (Issue in logs: {line.strip()[:40]}...)", "warn"

            return None, None
        except Exception:
            return None, None

    def _check_liferay_health_logs(self, container_name, tail=50):
        """Checks the last N lines of Liferay logs for startup status and errors."""
        try:
            import subprocess
            import re

            res = subprocess.run(
                ["docker", "logs", "--tail", str(tail), container_name],
                capture_output=True,
                text=True,
                check=False,
            )
            logs = (res.stdout or "") + (res.stderr or "")
            if not logs:
                return "Initializing...", True

            lines = logs.splitlines()

            # 1. Success Marker (Liferay is fully up)
            if any("started in" in line.lower() for line in lines) or any(
                "Liferay(TM) Portal" in line and "started" in line.lower()
                for line in lines
            ):
                return "Ready", True

            # 2. Critical Errors (Hard failure keywords)
            critical_keywords = [
                r"\bERROR\b",
                r"\bFATAL\b",
                r"Elasticsearch.*minimum version requirement",
                "Table .* already exists",
                "Exception in thread",
            ]
            for line in lines:
                if any(re.search(k, line, re.IGNORECASE) for k in critical_keywords):
                    # Filter out known non-fatal exceptions (e.g. session timeouts or expected background noise)
                    if any(
                        noise in line
                        for noise in [
                            "SessionTimeout",
                            "GarbageCollector",
                            "Keep-Alive",
                        ]
                    ):
                        continue
                    return f"Critical (Error: {line.strip()[:40]}...)", False

            # 3. Startup Progress
            if any("Starting Liferay" in line for line in lines):
                return "Starting...", True

            # 4. Warnings
            warning_keywords = [
                r"\bWARN\b",
                r"\bWRN\b",
                r"\bWARNING\b",
                "Missing license",
                "slow",
            ]
            for line in lines:
                if any(re.search(k, line, re.IGNORECASE) for k in warning_keywords):
                    return f"Warning (Issue: {line.strip()[:40]}...)", "warn"

            return "Running (Starting Up)", True
        except Exception:
            return "Running", True
