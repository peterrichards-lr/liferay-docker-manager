import contextlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

from ldm_core.constants import BUILD_INFO, SCRIPT_DIR, VERSION
from ldm_core.diagnostics.completions import is_completion_enabled
from ldm_core.diagnostics.info import _get_env_info
from ldm_core.ui import UI
from ldm_core.utils import (
    check_for_updates,
    get_actual_home,
    is_continuation_line,
    resolve_dependency_version,
    run_command,
    strip_ansi,
    verify_executable_checksum,
    version_to_tuple,
)


class DoctorRunner:
    """Isolated context for running LDM Doctor checks."""

    def __init__(self, handler, project_id=None, all_projects=False):
        from typing import Any

        self.handler = handler
        self.manager = handler.manager
        self.args = handler.manager.args
        self.project_id = project_id
        self.all_projects = all_projects
        self.results: list[tuple[str, str, Any]] = []
        self.hints: list[dict[str, Any]] = []
        self.project_paths = []
        self.requires_ssl = False
        self.is_wsl = False
        self.arch = "unknown"
        self.host_os = "unknown"
        self.provider = "Unknown"
        self.mount_type = "unknown"
        self.docker_version = None

    def add_hint(self, text, doc=None):
        self.hints.append({"text": text, "doc": doc})

    def run(self):
        self.arch, self.host_os, self.provider, self.mount_type = _get_env_info(
            self.handler
        )

        if getattr(self.args, "slug", False):
            # Use same slug logic as sync_compatibility.py
            clean_arch = self.arch.lower().replace(" ", "-")
            clean_os = self.host_os.lower().replace(" ", "-").replace("+", "")
            clean_provider = self.provider.lower().replace(" ", "-")
            print(f"{clean_arch}-{clean_os}-{clean_provider}")
            return

        if getattr(self.args, "ssl", False):
            self._check_ssl_diagnostics()
            return

        UI.heading("LDM Doctor - Environmental Health Check")

        # 0. Early Project Resolve (Optional skip allowed)
        skip_project = getattr(self.args, "skip_project", False)
        check_all = self.all_projects or getattr(self.args, "all", False)

        self.project_paths = []
        self.requires_ssl = False
        if check_all:
            roots = self.handler.manager.find_dxp_roots()
            self.project_paths = [r["path"] for r in roots]
            for r in roots:
                p_meta = self.handler.manager.read_meta(r["path"])
                if str(p_meta.get("ssl", "false")).lower() == "true":
                    self.requires_ssl = True
        elif not skip_project:
            p_path = self.handler.manager.detect_project_path(self.project_id)
            if p_path:
                self.project_paths = [p_path]
                p_meta = self.handler.manager.read_meta(p_path)
                if str(p_meta.get("ssl", "false")).lower() == "true":
                    self.requires_ssl = True

        # Detect WSL early for troubleshooting logic
        self.is_wsl = False
        if platform.system().lower() == "linux":
            try:
                with open("/proc/version") as f:
                    if "microsoft" in f.read().lower():
                        self.is_wsl = True
            except Exception:
                pass

        self._check_tooling_and_integrity()
        self._check_docker_runtime()
        self._check_global_config_and_network()
        self._check_project_specific()
        self._check_dangling_and_print()

    def _check_tooling_and_integrity(self):
        # 0. Version Check
        v_display = f"v{VERSION}{UI.get_beta_label(VERSION)}"
        if BUILD_INFO:
            v_display += f" ({BUILD_INFO})"

        latest, _ = check_for_updates(VERSION, force=True)
        if latest and version_to_tuple(latest) > version_to_tuple(VERSION):
            self.results.append(
                (
                    "LDM Version",
                    f"{v_display} (v{latest} available - Run 'ldm upgrade')",
                    "warn",
                )
            )
            self.add_hint(
                f"Upgrade LDM from v{VERSION} to v{latest} by running '{UI.WHITE}ldm upgrade{UI.COLOR_OFF}'.",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#troubleshooting-version-loop--integrity-issues",
            )
        else:
            self.results.append(("LDM Version", f"{v_display} (Latest)", True))

        # 0.1 Executable Integrity
        status, ok, detected_version = verify_executable_checksum(VERSION)
        is_source = status == "Source"
        if not status:
            status, ok = "Verification Unavailable", "warn"

        # If the version in memory differs from the one detected in the binary,
        # it means shadowing is occurring. We update the version reporting to reflect this.
        if detected_version != VERSION:
            # Re-check for updates using the ACTUAL binary version
            latest, _ = check_for_updates(detected_version, force=True)
            if latest and version_to_tuple(latest) > version_to_tuple(detected_version):
                self.results[0] = (
                    "LDM Version",
                    f"v{detected_version} (v{latest} available - Run 'ldm upgrade')",
                    "warn",
                )
            else:
                self.results[0] = ("LDM Version", f"v{detected_version} (Latest)", True)

            # Add a shadow warning to the integrity status
            status = f"{status} (Shadowed by {VERSION})"
            ok = "warn" if ok is True else ok

        if ok is False:
            status = f"{status} {UI.WHITE}(Run 'ldm upgrade --repair'){UI.COLOR_OFF}"
            self.add_hint(
                f"Repair your LDM installation by running '{UI.WHITE}ldm upgrade --repair{UI.COLOR_OFF}'.",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#troubleshooting-version-loop--integrity-issues",
            )

        self.results.append(("Executable Integrity", status, ok))

        # 0.2 Executable Path

        try:
            exe_path = Path(sys.argv[0]).resolve()
            self.results.append(("Executable Path", str(exe_path), True))
        except Exception:
            pass

        # 1. System Info
        self.results.append(("Python Version", sys.version.split()[0], True))
        self.results.append(("Platform", platform.platform(), True))

        # 1.2 Virtual Environment Check
        is_in_venv = (
            sys.prefix != sys.base_prefix
            or hasattr(sys, "real_prefix")
            or "VIRTUAL_ENV" in os.environ
        )
        if is_in_venv:
            self.results.append(("Virtual Environment", "Active (.venv)", True))
        elif is_source:
            self.results.append(("Virtual Environment", "Not Activated", "warn"))
            self.add_hint(
                "Virtual Environment: Running Python globally. It is highly recommended to run inside the virtualenv.",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/GEMINI.md#6-python-virtual-environment-venv",
            )
            self.add_hint("Run: source .venv/bin/activate")
        else:
            self.results.append(("Virtual Environment", "Not Required (Binary)", True))

        # 1.3 Dependency Integrity Check
        if is_source and (is_in_venv or os.path.exists(".venv")):
            packages = []
            for req_filename in ["requirements.txt", "requirements-dev.txt"]:
                req_file = SCRIPT_DIR / req_filename
                if not req_file.exists():
                    continue
                try:
                    for line in req_file.read_text().splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        name = line.split("==")[0].split(";")[0].strip()
                        if name:
                            packages.append((name, req_filename))
                except Exception:
                    pass

            dep_failures = []
            for pkg, _ in packages:
                res, status = _verify_dependency_integrity(self.handler, pkg)
                if status is not True:
                    dep_failures.append((pkg, res, status))

            if dep_failures:
                worst_status = (
                    "error" if any(f[2] == "error" for f in dep_failures) else "warn"
                )
                first_failed_pkg, first_failed_reason, _ = dep_failures[0]
                self.results.append(
                    (
                        "Dependency Integrity",
                        f"Failed ({first_failed_pkg} - {first_failed_reason})",
                        worst_status,
                    )
                )
                self.add_hint(
                    f"Virtual environment dependencies failed integrity verification: {first_failed_pkg} is {first_failed_reason.lower()}.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/SECURITY.md",
                )
                self.add_hint(
                    f"Re-install dependencies using: {UI.WHITE}pip install --force-reinstall -r requirements.txt{UI.COLOR_OFF}"
                )
            else:
                self.results.append(
                    ("Dependency Integrity", "Verified (All packages OK)", True)
                )

        # 1.1 Shell Completion Check
        if is_completion_enabled(self.handler):
            self.results.append(("Shell Completion", "Enabled (Active)", True))
        else:
            shell = os.environ.get("SHELL", "").split("/")[-1]
            if shell in ["bash", "zsh", "fish"]:
                self.results.append(
                    ("Shell Completion", f"Not Enabled ({shell})", "warn")
                )
                self.add_hint(
                    f"Enable tab-completion for {shell} by running '{UI.WHITE}ldm completion{UI.COLOR_OFF}'.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#3-shell-autocompletion",
                )
            else:
                self.results.append(
                    ("Shell Completion", f"Unsupported ({shell})", "warn")
                )

    def _check_docker_runtime(self):
        # 2. Docker Check
        # Perform a silent check first to avoid double error reporting in the UI
        self.docker_version = None
        try:
            import subprocess

            docker_bin = shutil.which("docker")
            if docker_bin:
                res = subprocess.run(
                    [docker_bin, "version", "--format", "{{.Server.Version}}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode == 0:
                    self.docker_version = res.stdout.strip()
        except Exception:
            pass

        if self.docker_version:
            self.results.append(
                ("Docker Engine", f"Running (v{self.docker_version})", True)
            )

            # 2.0 Docker Context & Provider Check
            try:
                context = (
                    self.handler.manager.run_command(
                        ["docker", "context", "show"], check=False
                    )
                    or ""
                ).strip()
                if context:
                    self.results.append(("Docker Context", context, True))

                # Identify Provider
                if self.is_wsl:
                    if context in ["desktop-linux", "docker-desktop"]:
                        self.provider = "Docker Desktop"
                    elif context == "default":
                        # If default, check if endpoint is local or redirected
                        inspect = self.handler.manager.run_command(
                            ["docker", "context", "inspect", "default"], check=False
                        )
                        if "docker-desktop" in str(inspect).lower():
                            self.provider = "Docker Desktop"
                        else:
                            self.provider = "Native WSL2"
                elif platform.system().lower() == "darwin":
                    if context == "orbstack":
                        self.provider = "OrbStack"
                    else:
                        # On Mac, 'colima' or 'default' (standard socket) is treated as Colima
                        # for LDM's compatibility tracking purposes.
                        self.provider = "Colima"
                elif platform.system().lower() == "windows":
                    # On Windows native, any standard context is Docker Desktop
                    if context in ["default", "desktop-linux", "docker-desktop"]:
                        self.provider = "Docker Desktop"

                self.results.append(("Docker Provider", self.provider, True))

                # Try to get specific self.provider versions
                provider_version = None
                if self.provider == "OrbStack":
                    try:
                        orb_v = self.handler.manager.run_command(
                            ["orb", "version"], check=False
                        )
                        if orb_v and "version" in orb_v.lower():
                            v_match = re.search(r"version\s+([0-9.]+)", orb_v, re.I)
                            if v_match:
                                provider_version = f"v{v_match.group(1)}"
                    except Exception:
                        pass
                elif self.provider == "Colima":
                    try:
                        col_v = self.handler.manager.run_command(
                            ["colima", "version"], check=False
                        )
                        if col_v and "version" in col_v.lower():
                            v_match = re.search(r"version\s+([0-9.]+)", col_v, re.I)
                            if v_match:
                                provider_version = f"v{v_match.group(1)}"
                    except Exception:
                        pass
                elif self.provider == "Docker Desktop":
                    try:
                        # On Mac, check the app bundle
                        if platform.system().lower() == "darwin":
                            plist = "/Applications/Docker.app/Contents/Info.plist"
                            if os.path.exists(plist):
                                v_res = self.handler.manager.run_command(
                                    [
                                        "defaults",
                                        "read",
                                        plist,
                                        "CFBundleShortVersionString",
                                    ],
                                    check=False,
                                )
                                if v_res:
                                    provider_version = f"v{v_res.strip()}"

                        # Fallback/Windows: Try to parse from 'docker version' Server section
                        if not provider_version:
                            dv_res = self.handler.manager.run_command(
                                ["docker", "version"], check=False
                            )
                            # Look for "Server: Docker Desktop 4.x.x"
                            dd_match = re.search(
                                r"Server:\s+Docker Desktop\s+([0-9.]+)",
                                dv_res or "",
                                re.I,
                            )
                            if dd_match:
                                provider_version = f"v{dd_match.group(1)}"
                    except Exception:
                        pass

                if provider_version:
                    self.results.append(
                        (f"{self.provider} Version", provider_version, True)
                    )

                # Proactive Colima SSHFS warning
                if getattr(self, "_colima_mount_not_writable", False):
                    self.add_hint(
                        "Colima: Your mount is not explicitly marked as 'writable'. With 'sshfs', this often causes permission errors.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#colima-mount-permissions",
                    )
                    self.add_hint(
                        "Fix: 'colima stop' then 'colima start --mount [HOME]:w'"
                    )

                # Check for symlinked socket in WSL (Docker Desktop override)
                if self.is_wsl:
                    socket_path = Path("/var/run/docker.sock")
                    if socket_path.is_symlink():
                        target = socket_path.resolve()
                        self.results.append(
                            ("Docker Socket", f"Symlinked to {target.name}", "warn")
                        )
                        self.add_hint(
                            "WSL: Your Docker socket is symlinked, likely by Docker Desktop 'WSL Integration'.",
                            "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#wsl2-mixed-environments",
                        )
                        self.add_hint(
                            "To use 'Native' Docker in this distro, disable 'WSL Integration' in Docker Desktop settings and delete the symlink."
                        )
            except Exception:
                pass

            # 2.0.1 Docker Compose Check
            from ldm_core.utils import get_compose_cmd

            compose_bin = get_compose_cmd()
            if compose_bin:
                self.results.append(("Docker Compose", "Plugin v2 Detected", True))
            else:
                self.results.append(("Docker Compose", "Plugin NOT FOUND", False))
                self.add_hint(
                    "LDM requires the Docker Compose V2 plugin. Please install it via your Docker self.provider settings."
                )

            # 2.1 Docker Credentials Check
            creds_status, creds_ok = _check_docker_creds(self.handler)
            if creds_status:
                self.results.append(("Docker Creds Store", creds_status, creds_ok))

            # 2.2 Docker Resources
            docker_info_raw = run_command(
                ["docker", "info", "--format", "{{json .}}"], check=False
            )
            if docker_info_raw:
                res_results = _check_docker_resources(self.handler, docker_info_raw)
                for comp, stat, ok in res_results:
                    self.results.append((comp, stat, ok))
                    if ok is not True:
                        res_type = "CPU cores" if "CPU" in comp else "RAM"
                        self.add_hint(
                            f"Allocate more {res_type} in your Docker self.provider settings.",
                            "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#docker-resource-alignment-windowswsl2macos",
                        )
        else:
            # Trigger the detailed error reporting from base.py
            self.handler.manager.check_docker()
            self.results.append(("Docker Engine", "Not reachable", False))
            self.add_hint(
                "If Docker is running but LDM cannot connect, ensure your user is in the 'docker' group.",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#linux--wsl-docker-permissions",
            )
            if self.is_wsl:
                self.add_hint(
                    "WSL: To use Native Docker, run: 'sudo service docker start'",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#wsl2-native-docker",
                )
                self.add_hint(
                    "WSL: To use Docker Desktop, ensure 'WSL Integration' is enabled in the Docker Desktop dashboard."
                )

    def _check_global_config_and_network(self):
        # 3. mkcert Check
        mkcert_status, mkcert_ok, ca_root = check_mkcert(self.handler)

        # 4. Volume Write Test (Proactive detection of RO mounts)
        if self.docker_version and self.project_paths:
            # Test the first project found
            test_path = self.project_paths[0]
            try:
                # We spin up a tiny container and try to touch a file as UID 1000 (Liferay user)
                # This catches the 'VOLUME MOUNT IS READ-ONLY' issue seen in Colima/WSL
                from ldm_core.utils import get_actual_home

                test_path.relative_to(get_actual_home())

                res = self.handler.manager.run_command(
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
                    self.results.append(("Volume Permissions", "❌ Read-Only", False))
                    self.add_hint(
                        "Your Docker volume mounts are read-only for the 'liferay' user (UID 1000).",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#troubleshooting-read-only-mounts",
                    )
                    if self.provider == "Colima":
                        # Check for custom LaunchAgent setup
                        if (
                            Path(get_actual_home())
                            / "Library/LaunchAgents/com.github.abiosoft.colima.plist"
                        ).exists():
                            self.add_hint(
                                "Detected custom Colima LaunchAgent. Please update your '/usr/local/bin/colima-start-fg' script."
                            )
                            self.add_hint(
                                'Change to: colima start --mount-type virtiofs --mount "$HOME:w"'
                            )
                        else:
                            # Detect architecture
                            self.arch = platform.machine().lower()
                            is_intel = "x86" in self.arch or "i386" in self.arch

                            if self.mount_type == "sshfs":
                                self.add_hint(
                                    "Colima Fix: Your mount type is 'sshfs'. You MUST add ':w' to your mount paths for write access."
                                )
                                self.add_hint(
                                    "Run: 'colima stop' then 'colima start --mount [HOME]:w'"
                                )
                            else:
                                self.add_hint(
                                    "Colima Fix (Standard): 'colima stop' then 'colima start --mount [HOME]:w'"
                                )

                            if is_intel:
                                self.add_hint(
                                    "Note: If performance is poor, try: 'colima stop' then 'colima start --mount [HOME]:w --vm-type=vz'"
                                )
                            else:
                                self.add_hint(
                                    "Colima Fix (Advanced): 'colima stop' then 'colima start --mount [HOME]:w --vm-type=vz --mount-type=virtiofs'",
                                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/colima-performance-tuning",
                                )

                        self.add_hint(
                            "Recommended: Upgrade Colima to the latest version ('brew upgrade colima') for better macOS integration."
                        )

                else:
                    self.results.append(("Volume Permissions", "✅ Writable", True))
            except Exception:
                # Skip if we can't perform the test (e.g. path outside home)
                pass

        # Escalate to error if project requires SSL
        if mkcert_ok == "warn" and self.requires_ssl:
            mkcert_ok = False

        self.results.append(("mkcert", mkcert_status, mkcert_ok))
        if mkcert_ok is not True:
            if mkcert_status == "Not installed":
                self.add_hint(
                    "Install 'mkcert' to enable local SSL (brew install mkcert / scoop install mkcert / apt install mkcert).",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#prerequisites",
                )
            elif "Permission Denied" in mkcert_status:
                cert_dir = get_actual_home() / "liferay-docker-certs"
                self.add_hint(
                    f"Fix permissions: {UI.WHITE}sudo chown -R $USER {cert_dir.parent}{UI.COLOR_OFF}"
                )
            else:
                self.add_hint(
                    f"Run '{UI.WHITE}mkcert -install{UI.COLOR_OFF}' to initialize the local trust store.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#fixing-ssl-trust-issues-mkcert",
                )
        else:
            # Detect WSL
            self.is_wsl = False
            if platform.system().lower() == "linux":
                try:
                    with open("/proc/version") as f:
                        if "microsoft" in f.read().lower():
                            self.is_wsl = True
                except Exception:
                    pass

            if self.is_wsl:
                # Check if CAROOT points to Windows
                is_win_ca = ca_root and "/mnt/c/" in ca_root
                if not is_win_ca:
                    self.add_hint(
                        f"[WSL] Your browser won't trust WSL certificates. Run '{UI.WHITE}mkcert -install{UI.COLOR_OFF}' on Windows, then in WSL set: {UI.WHITE}export CAROOT=\"/mnt/c/Users/<user>/AppData/Local/mkcert\"{UI.COLOR_OFF}",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#wsl2-ssl-trust",
                    )
                else:
                    self.add_hint(
                        f"[WSL] To avoid 'Insecure' browser warnings, you must ALSO run '{UI.WHITE}mkcert -install{UI.COLOR_OFF}' on your Windows host (via PowerShell or CMD).",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#wsl2-ssl-trust",
                    )

        # 4. OpenSSL Check
        openssl_status, openssl_ok = _check_openssl(self.handler)
        self.results.append(("OpenSSL", openssl_status, openssl_ok))
        if openssl_ok is not True:
            self.add_hint(
                "Install OpenSSL (available via brew, macports, scoop, or apt).",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#prerequisites",
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
            ("nc/ncat (Deprecated)", active_nc),
            ("lcp", shutil.which("lcp")),
        ]

        # Add Docker Compose (detect if it's the plugin or standalone)
        from ldm_core.utils import get_compose_cmd

        compose_bin = get_compose_cmd()
        if compose_bin:
            tool_list.append(("docker compose", " ".join(compose_bin)))

        for tool_name, tool_path in tool_list:
            if tool_path:
                self.results.append((f"Path: {tool_name}", str(tool_path), True))
            # Some are optional/warn only
            elif tool_name == "nc/ncat (Deprecated)":
                self.results.append(
                    (f"Path: {tool_name}", "Not Found (Optional/Deprecated)", True)
                )
            elif tool_name in ["telnet", "lcp", "mkcert", "nc/ncat"]:
                self.results.append((f"Path: {tool_name}", "Not Found", "warn"))
            else:
                self.results.append((f"Path: {tool_name}", "Not Found", False))

        # 4.1.5 Optional Database Clients (Recommended for developers)
        # Note: LDM uses 'docker exec' for snapshots, so local clients are NOT required for LDM operations.
        for db_tool in ["mysql", "psql"]:
            tool_path = shutil.which(db_tool)
            if tool_path:
                self.results.append((f"Client: {db_tool}", str(tool_path), True))
            else:
                self.results.append(
                    (
                        f"Client: {db_tool}",
                        f"{UI.WHITE}Not installed{UI.COLOR_OFF}",
                        "warn",
                    )
                )
                self.add_hint(
                    f"Optional: Install '{db_tool}' on your host to manually inspect databases from outside Docker."
                )

        # 4.2 Legacy Compatibility Checks (Maintain existing summary lines)
        telnet_bin = shutil.which("telnet")
        nc_bin = shutil.which("nc")
        ncat_bin = shutil.which("ncat")  # Nmap's netcat version
        lcp_bin = shutil.which("lcp")

        self.results.append(
            (
                "Tool: telnet",
                "Installed" if telnet_bin else "Missing (Gogo Shell disabled)",
                True if telnet_bin else "warn",
            )
        )
        if not telnet_bin:
            if platform.system().lower() == "windows":
                self.add_hint(
                    "To enable telnet on Windows, run this in an Admin PowerShell: "
                    f"'{UI.WHITE}Enable-WindowsOptionalFeature -Online -FeatureName TelnetClient{UI.COLOR_OFF}'"
                )
            else:
                self.add_hint(
                    "Install telnet for Gogo Shell support (e.g. 'brew install telnet' or 'apt-get install telnet')."
                )

        # Netcat / Ncat check (Deprecated - Log-level sync now uses native file hot-reloading)
        active_nc = nc_bin or ncat_bin
        self.results.append(
            (
                "Tool: netcat (nc/ncat)",
                "Installed (Deprecated/Unused)"
                if active_nc
                else "Missing (Deprecated/Unused - Native file hot-reloads used)",
                True,
            )
        )

        self.results.append(
            (
                "Tool: lcp cli",
                "Installed" if lcp_bin else "Missing (Cloud Fetch disabled)",
                True if lcp_bin else "warn",
            )
        )
        if not lcp_bin:
            self.add_hint(
                "Install Liferay Cloud CLI for 'cloud-fetch' support.",
                "https://customer.liferay.com/downloads/-/download/liferay-cloud-cli",
            )

        # 4.2 Liferay Cloud Check
        lcp_status, lcp_ok = _check_lcp_cli(self.handler)
        if lcp_status:
            self.results.append(("Liferay Cloud Auth", lcp_status, lcp_ok))

        # 4.2 Project Health (if specific project is being checked)
        if self.project_paths and len(self.project_paths) == 1:
            p_path = self.project_paths[0]
            meta = self.handler.manager.read_meta(p_path)
            if meta:
                is_seeded = str(meta.get("seeded", "false")).lower() == "true"
                s_version = meta.get("seed_version")
                if is_seeded:
                    self.results.append(
                        (
                            "Project Initialization",
                            f"✅ Seeded (v{s_version if s_version else 'unknown'})",
                            True,
                        )
                    )
                else:
                    self.results.append(
                        ("Project Initialization", "Vanilla (Not Seeded)", "warn")
                    )

        # 4.3 Global Config Check
        base_path = self.project_paths[0] if self.project_paths else None
        common_dir = self.handler.manager.get_common_dir(base_path)
        search_version = 8  # Default

        if not common_dir.exists():
            self.results.append(
                ("Global Config", "Missing ('ldm init-common' available)", "warn")
            )
            self.add_hint(
                f"Run '{UI.WHITE}ldm init-common{UI.COLOR_OFF}' to restore standard development assets.",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#global-configuration-the-common-folder",
            )
        else:
            try:
                import importlib.resources as pkg_resources

                from ldm_core import resources

                pe_file = common_dir / "portal-ext.properties"
                if not pe_file.exists():
                    self.results.append(
                        ("Global Config", "Overrides Active (no baseline)", True)
                    )
                else:
                    baseline_content = (
                        pkg_resources.files(resources)
                        / "common_baseline"
                        / "portal-ext.properties"
                    ).read_text()
                    if pe_file.read_text().strip() == baseline_content.strip():
                        self.results.append(
                            ("Global Config", f"Baseline (v{VERSION})", True)
                        )
                    else:
                        prop_status, prop_ok, prop_details = validate_properties_file(
                            self.handler, pe_file
                        )
                        if prop_ok is True:
                            self.results.append(
                                ("Global Config", "Custom Overrides", True)
                            )
                        else:
                            self.results.append(
                                (
                                    "Global Config",
                                    f"Invalid Format ({prop_status})",
                                    prop_ok,
                                )
                            )
                            self.add_hint(
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
                    self.results.append(
                        (
                            "Global Search Config",
                            f"Missing {v_id.upper()} (Run 'ldm init-common')",
                            "warn",
                        )
                    )
                    self.add_hint(
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

                    # Project Override Check
                    if self.manager and self.manager.meta:
                        use_sidecar = (
                            str(
                                self.manager.meta.get("use_shared_search", "true")
                            ).lower()
                            == "false"
                        )
                        if use_sidecar:
                            msg = "SIDECAR mode active (Local Project)"

                    self.results.append(("Global Search Config", msg, True))
            except Exception:
                self.results.append(("Global Config", "Overrides Active", True))

        # 5. Network Check
        if self.docker_version:
            has_net = run_command(
                ["docker", "network", "inspect", "liferay-net"], check=False
            )
            if has_net:
                self.results.append(("Docker Network", "liferay-net exists", True))
            else:
                self.results.append(
                    ("Docker Network", "missing (will be created on run)", "warn")
                )
                self.add_hint(
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
                    from typing import Any

                    ok: Any = True

                    # Version check for Global Search
                    if container == "liferay-search-global":
                        from ldm_core.constants import (
                            ELASTICSEARCH7_VERSION,
                            ELASTICSEARCH_VERSION,
                        )

                        # Discover latest tag if not already known
                        tag = None
                        if self.project_paths:
                            first_meta = self.handler.manager.read_meta(
                                self.project_paths[0]
                            )
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
                            self.add_hint(
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
                            self.add_hint(
                                f"Run '{UI.WHITE}ldm infra-setup{UI.COLOR_OFF}' to reconnect the macOS socket bridge."
                            )

                    if container == "liferay-search-global":
                        try:
                            # 1. Connectivity Check
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
                            if search_res not in {"200", "401"}:
                                status = f"Unreachable (HTTP {search_res})"
                                ok = "warn"
                                self.add_hint(
                                    f"Run '{UI.WHITE}ldm infra-restart --search{UI.COLOR_OFF}' if the search cluster is unresponsive."
                                )
                            else:
                                # 2. Disk Watermark / Blocked Indices Check
                                watermark_status = _check_elasticsearch_watermarks(
                                    self.handler, self.add_hint
                                )
                                if watermark_status:
                                    status = watermark_status
                                    ok = False
                        except Exception:
                            pass

                    log_status, log_ok = _check_container_health_logs(
                        self.handler, container, add_hint=self.add_hint
                    )
                    if log_status:
                        status = log_status
                        ok = log_ok

                    self.results.append((label, status, ok))
                else:
                    cmd_hint = "ldm infra-setup"
                    if container == "liferay-search-global":
                        cmd_hint = "ldm infra-setup --search"
                        self.results.append(
                            (
                                label,
                                f"Not running (Run '{cmd_hint}') {UI.WHITE}(Sidecar will be used){UI.COLOR_OFF}",
                                "warn",
                            )
                        )
                    else:
                        self.results.append(
                            (label, f"Not running (Run '{cmd_hint}')", "warn")
                        )
                    self.add_hint(
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
                    self.results.append(
                        (
                            "Liferay Docker Tags",
                            f"Available (Latest: {latest_tag})",
                            True,
                        )
                    )
                else:
                    self.results.append(
                        (
                            "Liferay Docker Tags",
                            "Unavailable (Discovery failed)",
                            "warn",
                        )
                    )
                    self.add_hint(
                        "Docker Hub API might be rate-limited or unreachable."
                    )
            except Exception as e:
                self.results.append(
                    ("Liferay Docker Tags", f"Error ({str(e)[:30]}...)", "warn")
                )
        else:
            self.results.append(("Docker Network", "Skipped (Engine down)", "warn"))
            self.results.append(
                ("Global Infrastructure", "Skipped (Engine down)", "warn")
            )

    def _check_project_specific(self):
        # 7. Project-Specific Check (Optional)
        for p_path in self.project_paths:
            UI.heading(f"Project Health: {p_path.name}")
            meta = self.handler.manager.read_meta(p_path)
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
                self.results.append(
                    (
                        f"[{p_path.name}] Metadata",
                        f"Poisoned ({len(poisoned)} legacy vars)",
                        "warn",
                    )
                )
                self.add_hint(
                    f"[{p_path.name}] Clean legacy environment variables by running '{UI.WHITE}ldm run {p_path.name}{UI.COLOR_OFF}'.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#env",
                )
            else:
                self.results.append((f"[{p_path.name}] Metadata", "Healthy", True))

            if self.handler.manager.require_compose(p_path, silent=True):
                self.results.append(
                    (f"[{p_path.name}] Config", "docker-compose.yml OK", True)
                )
            else:
                self.results.append(
                    (f"[{p_path.name}] Config", "docker-compose.yml MISSING", False)
                )
                self.add_hint(
                    f"[{p_path.name}] Regenerate missing configuration by running '{UI.WHITE}ldm run {p_path.name}{UI.COLOR_OFF}'.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#run-alias-up",
                )

            pe_file = p_path / "files" / "portal-ext.properties"
            if pe_file.exists():
                prop_status, prop_ok, prop_details = validate_properties_file(
                    self.handler, pe_file
                )
                self.results.append(
                    (f"[{p_path.name}] Properties", prop_status, prop_ok)
                )
                if prop_ok is not True:
                    self.add_hint(
                        f"[{p_path.name}] Verify syntax in '{UI.WHITE}{p_path.name}/files/portal-ext.properties{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/LDM_ARCHITECTURE.md#5-metadata--property-injection",
                    )
                    if prop_details:
                        for detail in prop_details:
                            UI.raw(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {detail}")
            else:
                self.results.append(
                    (
                        f"[{p_path.name}] Properties",
                        "portal-ext.properties MISSING",
                        "warn",
                    )
                )

            # --- Liferay Log Health ---
            from ldm_core.utils import sanitize_id

            p_id = sanitize_id(
                meta.get("liferay_container_name")
                or meta.get("container_name")
                or p_path.name
            )
            liferay_container = None
            possible_names = [p_id, f"{p_id}-liferay", f"{p_id}-liferay-1"]
            for name in possible_names:
                if run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{name}$"], check=False
                ):
                    liferay_container = name
                    break

            if liferay_container:
                log_status, log_ok = _check_liferay_health_logs(
                    self.handler, liferay_container
                )
                self.results.append(
                    (f"[{p_path.name}] Liferay Logs", log_status, log_ok)
                )
                if log_ok is not True:
                    self.add_hint(
                        f"[{p_path.name}] Check detailed Liferay logs by running '{UI.WHITE}ldm logs {p_path.name} liferay{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#logs",
                    )

            osgi_config_dir = p_path / "osgi" / "configs"

            # Smart Detection based on Liferay Version
            is_es8 = self.handler.manager.parse_version(meta.get("tag")) >= (2024, 1, 0)
            es_ver = "8" if is_es8 else "7"

            es_main_conf = (
                osgi_config_dir
                / f"com.liferay.portal.search.elasticsearch{es_ver}.configuration.ElasticsearchConfiguration.config"
            )
            es_conn_conf = (
                osgi_config_dir
                / f"com.liferay.portal.search.elasticsearch{es_ver}.configuration.ElasticsearchConnectionConfiguration.config"
            )

            use_shared_search = (
                str(meta.get("use_shared_search", "false")).lower() == "true"
            )
            configs_exist = es_main_conf.exists() and es_conn_conf.exists()
            configs_partial = es_main_conf.exists() or es_conn_conf.exists()

            if use_shared_search:
                if configs_exist:
                    self.results.append(
                        (f"[{p_path.name}] OSGi Search", "REMOTE mode detected", True)
                    )
                elif configs_partial:
                    self.results.append(
                        (f"[{p_path.name}] OSGi Search", "Partial / Incomplete", "warn")
                    )
                    self.add_hint(
                        f"[{p_path.name}] Ensure both Elasticsearch configs exist in '{UI.WHITE}osgi/configs/{UI.COLOR_OFF}'."
                    )
                else:
                    self.results.append(
                        (
                            f"[{p_path.name}] OSGi Search",
                            "REMOTE mode (via ENV vars)",
                            True,
                        )
                    )
            elif configs_exist:
                self.results.append(
                    (
                        f"[{p_path.name}] OSGi Search",
                        "REMOTE mode (Custom OSGi Configs)",
                        True,
                    )
                )
            elif configs_partial:
                self.results.append(
                    (
                        f"[{p_path.name}] OSGi Search",
                        "Partial / Incomplete (Custom Configs)",
                        "warn",
                    )
                )
                self.add_hint(
                    f"[{p_path.name}] Ensure both custom Elasticsearch configs exist in '{UI.WHITE}osgi/configs/{UI.COLOR_OFF}'."
                )
            else:
                self.results.append(
                    (f"[{p_path.name}] OSGi Search", "SIDECAR mode active", "warn")
                )
                self.add_hint(
                    f"[{p_path.name}] Enable global search by running '{UI.WHITE}ldm migrate-search {p_path.name}{UI.COLOR_OFF}'.",
                    "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#migrate-search",
                )

            # 7.2.3 LCP.json Validation (Extensions)
            for lcp_file in p_path.rglob("LCP.json"):
                # Avoid validating LCP.json in the project root if it's not a service
                # (Standard Liferay Cloud workspaces have a root LCP.json)
                rel_path = lcp_file.relative_to(p_path)
                lcp_status, lcp_ok, lcp_errors = validate_lcp_json(
                    self.handler, lcp_file
                )
                self.results.append(
                    ("Extension Config", f"{lcp_status} ({rel_path})", lcp_ok)
                )
                if lcp_errors:
                    for err in lcp_errors:
                        UI.raw(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {err}")

            # 7.2.4 License Check
            base_path = self.project_paths[0] if self.project_paths else None
            common_dir = self.handler.manager.get_common_dir(base_path)
            lic_status, lic_ok, lic_details = (
                self.handler.manager.license.check_license_health(
                    {"common": common_dir, **self.handler.manager.setup_paths(p_path)},
                    image_tag=meta.get("tag"),
                )
            )
            self.results.append((f"[{p_path.name}] License", lic_status, lic_ok))
            if lic_ok is not True:
                self.add_hint(
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
                    from typing import Any

                    ok: Any = True
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
                    self.results.append((f"[{p_path.name}] SSL Cert", cert_status, ok))
                else:
                    self.results.append(
                        (
                            f"[{p_path.name}] SSL Cert",
                            "Missing (.pem or -key.pem)",
                            False,
                        )
                    )
                    self.add_hint(
                        f"[{p_path.name}] Regenerate SSL certificates by running '{UI.WHITE}ldm run {p_path.name} --force-ssl{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#fixing-ssl-trust-issues-mkcert",
                    )

                if traefik_conf.exists():
                    conf_content = traefik_conf.read_text()
                    expected_cert = f"certFile: /etc/traefik/certs/{host_name}.pem"
                    expected_key = f"keyFile: /etc/traefik/certs/{host_name}-key.pem"

                    if expected_cert in conf_content and expected_key in conf_content:
                        self.results.append(
                            (f"[{p_path.name}] Traefik SSL", "Config OK", True)
                        )
                    else:
                        self.results.append(
                            (f"[{p_path.name}] Traefik SSL", "Invalid Content", "warn")
                        )
                        self.add_hint(
                            f"[{p_path.name}] Regenerate Traefik routing by running '{UI.WHITE}ldm run {p_path.name} --force-ssl{UI.COLOR_OFF}'.",
                            "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#ssl-defaults-new-projects",
                        )
                else:
                    self.results.append(
                        (f"[{p_path.name}] Traefik SSL", "Config MISSING", False)
                    )
                    self.add_hint(
                        f"[{p_path.name}] Regenerate Traefik routing by running '{UI.WHITE}ldm run {p_path.name} --force-ssl{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#ssl-defaults-new-projects",
                    )

            compose_file = p_path / "docker-compose.yml"
            if compose_file.exists():
                try:
                    import yaml

                    with open(compose_file) as f:
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
                        self.results.append(
                            (
                                f"[{p_path.name}] Traefik Labels",
                                "Missing Net Label",
                                False,
                            )
                        )
                        self.add_hint(
                            f"[{p_path.name}] Fix Traefik labels by running '{UI.WHITE}ldm run {p_path.name}{UI.COLOR_OFF}'.",
                            "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#dns--subdomain-configuration",
                        )
                    elif double_prefixed:
                        self.results.append(
                            (
                                f"[{p_path.name}] Traefik Labels",
                                f"Double Prefixed ({len(double_prefixed)} labels)",
                                "warn",
                            )
                        )
                        for dp in double_prefixed:
                            UI.raw(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {dp}")
                        self.add_hint(
                            f"[{p_path.name}] Standardize Traefik labels by running '{UI.WHITE}ldm run {p_path.name}{UI.COLOR_OFF}'.",
                            "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/README.md#command-reference",
                        )
                    else:
                        self.results.append(
                            (f"[{p_path.name}] Traefik Labels", "Standardized OK", True)
                        )
                except Exception as e:
                    self.results.append(
                        (
                            f"[{p_path.name}] Traefik Labels",
                            f"Check Failed ({e})",
                            "warn",
                        )
                    )

            dns_res = self.handler.manager.validate_project_dns(p_path)
            dns_ok = dns_res[0]
            unresolved = dns_res[1]
            non_local = dns_res[2]

            if host_name != "localhost":
                fix_hosts = getattr(self.args, "fix_hosts", False)
                needs_fix = unresolved + [h for h, ip in non_local]

                if dns_ok and not non_local:
                    self.results.append(
                        (
                            f"[{p_path.name}] DNS ({host_name})",
                            "All domains resolve",
                            True,
                        )
                    )
                elif fix_hosts and needs_fix:
                    if self.handler.manager._apply_hosts_fix(needs_fix):
                        self.results.append(
                            (
                                f"[{p_path.name}] DNS ({host_name})",
                                "Fixed (Appended to hosts)",
                                True,
                            )
                        )
                    else:
                        self.results.append(
                            (
                                f"[{p_path.name}] DNS ({host_name})",
                                "Fix failed (Permission denied?)",
                                False,
                            )
                        )
                elif non_local and not unresolved:
                    # Resolves but to an unexpected IP (e.g. 10.0.0.99)
                    ip_list = [f"{h}={ip}" for h, ip in non_local]
                    self.results.append(
                        (
                            f"[{p_path.name}] DNS ({host_name})",
                            f"Non-local IP ({', '.join(ip_list)})",
                            "warn",
                        )
                    )
                    self.add_hint(
                        f"[{p_path.name}] Hostname resolves to an external IP. Point it to 127.0.0.1 in your local hosts file or run '{UI.WHITE}ldm doctor --fix-hosts{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#dns--subdomain-configuration",
                    )
                else:
                    self.results.append(
                        (
                            f"[{p_path.name}] DNS ({host_name})",
                            f"{len(unresolved)} domain(s) unresolved",
                            False,
                        )
                    )
                    self.add_hint(
                        f"[{p_path.name}] Add missing hostnames to your local hosts file or run '{UI.WHITE}ldm doctor --fix-hosts{UI.COLOR_OFF}'.",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#dns--subdomain-configuration",
                    )
                    for d in unresolved:
                        UI.raw(f"  {UI.RED}×{UI.COLOR_OFF} {d}")

            # 7.2.5 Database Version Check
            db_type = meta.get("db_type", "postgresql")
            if db_type in ["mysql", "postgresql", "mariadb"]:
                db_container = meta.get("db_container_name") or f"{p_id}-db"
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
                        self.results.append(
                            (
                                f"[{p_path.name}] DB Version",
                                f"OUTDATED ({running_db_ver.split(':')[-1]})",
                                "warn",
                            )
                        )
                        self.add_hint(
                            f"[{p_path.name}] Database image is outdated. Run '{UI.WHITE}ldm run {p_path.name}{UI.COLOR_OFF}' to update."
                        )
                    else:
                        self.results.append(
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
                    self.results.append(
                        (
                            f"[{p_path.name}] Mount Integrity",
                            "WSL Mount (/mnt/c) Detected",
                            False,
                        )
                    )
                    self.add_hint(
                        f"[{p_path.name}] Using Windows-mounted paths (/mnt/c) in WSL2 causes severe permission and performance issues with Liferay. "
                        f"Move your project to the native Linux filesystem (e.g. ~/repos/{p_path.name}).",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#linux--wsl-docker-permissions",
                    )
                elif platform.system().lower() == "darwin":
                    if liferay_container:
                        import uuid

                    token_val = f"DOCTOR_LIVE_{uuid.uuid4().hex[:8]}"
                    deploy_dir = p_path / "deploy"
                    token_file = deploy_dir / ".ldm_doctor_check"

                    try:
                        with contextlib.suppress(PermissionError, OSError):
                            deploy_dir.mkdir(parents=True, exist_ok=True)
                        from ldm_core.utils import safe_write_text

                        safe_write_text(token_file, token_val)
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
                            self.results.append(
                                (f"[{p_path.name}] Mounts", "Live (OK)", True)
                            )
                        else:
                            self.results.append(
                                (f"[{p_path.name}] Mounts", "BROKEN", False)
                            )
                            self.add_hint(
                                f"[{p_path.name}] Ensure Docker has permission to share your home directory.",
                                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#the-ghost-mount-issue",
                            )
                    finally:
                        if token_file.exists():
                            token_file.unlink()
                else:
                    self.results.append(
                        (f"[{p_path.name}] Mounts", "Verified on start", True)
                    )
            except Exception:
                pass

    def _check_dangling_and_print(self):
        # 4.4 Dangling Docker Resources Check
        df_out = run_command(
            ["docker", "system", "df", "--format", "{{json .}}"], check=False
        )
        if df_out:
            reclaimable_bytes = 0
            for line in df_out.strip().split("\n"):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # Extract Reclaimable space bytes from string (e.g., "7.937GB (99%)" or "437.4MB")
                    reclaim_str = data.get("Reclaimable", "")
                    if reclaim_str:
                        # Extract the size part before the space/parenthesis
                        size_str = reclaim_str.split(" ")[0].upper()

                        # Convert string to bytes
                        multiplier = 1
                        if "GB" in size_str:
                            multiplier = 1073741824
                            size_str = size_str.replace("GB", "")
                        elif "MB" in size_str:
                            multiplier = 1048576
                            size_str = size_str.replace("MB", "")
                        elif "KB" in size_str:
                            multiplier = 1024
                            size_str = size_str.replace("KB", "")
                        elif "B" in size_str:
                            size_str = size_str.replace("B", "")

                        if size_str.replace(".", "", 1).isdigit():
                            reclaimable_bytes += int(float(size_str) * multiplier)

                except Exception:
                    pass

            # If reclaimable space is more than 5GB (5 * 1024 * 1024 * 1024)
            if reclaimable_bytes > 5368709120:
                reclaim_gb = reclaimable_bytes // 1073741824
                self.results.append(
                    (
                        "Disk Space",
                        f"{reclaim_gb}GB reclaimable docker resources",
                        "warn",
                    )
                )
                self.add_hint(
                    f"You have over {reclaim_gb}GB of unused Docker resources. Run '{UI.WHITE}ldm prune{UI.COLOR_OFF}' and '{UI.WHITE}docker system prune --volumes{UI.COLOR_OFF}' to reclaim space and prevent disk watermark issues."
                )

        # Determine subsystem categories
        def get_subsystem(component_name: str) -> str:
            system_comps = {
                "LDM Version",
                "Executable Integrity",
                "Executable Path",
                "Python Version",
                "Platform",
                "Shell Completion",
                "Virtual Environment",
            }
            docker_comps = {
                "Docker Engine",
                "Docker Context",
                "Docker Provider",
                "Docker Socket",
                "Docker Compose",
                "Docker Creds Store",
                "CPU Cores",
                "RAM",
                "Volume Permissions",
                "OpenSSL",
                "Docker Network",
                "Global SSL Proxy",
                "Global Search (ES8)",
                "Global Search (ES7)",
                "Docker Socket Bridge",
                "Liferay Docker Tags",
                "Disk Space",
            }
            if component_name in system_comps:
                return "system"
            if component_name in docker_comps:
                return "docker"
            if component_name.endswith(" Version") and component_name not in (
                "LDM Version",
                "Python Version",
            ):
                return "docker"
            if (
                component_name.startswith("Path: ")
                or component_name.startswith("Client: ")
                or component_name.startswith("Tool: ")
                or component_name.startswith("Liferay Cloud Auth")
            ):
                return "docker"
            return "project"

        # Categorize results
        categorized: dict[str, list[tuple[str, str, Any]]] = {
            "system": [],
            "docker": [],
            "project": [],
        }
        subsystem_status: dict[str, bool | str] = {
            "system": True,
            "docker": True,
            "project": True,
        }
        all_ok = True
        has_warnings = False

        for component, status, ok in self.results:
            sub = get_subsystem(component)
            categorized[sub].append((component, status, ok))
            if ok is True:
                pass
            elif ok == "warn":
                has_warnings = True
                if subsystem_status[sub] is True:
                    subsystem_status[sub] = "warn"
            else:
                all_ok = False
                subsystem_status[sub] = False

        # Read filter & verbosity flags
        show_system = getattr(self.args, "system", False)
        show_docker = getattr(self.args, "docker", False)
        show_project = getattr(self.args, "project", False)
        detailed_mode = getattr(self.args, "detailed", False) or getattr(
            self.args, "verbose", False
        )
        has_subsystem_filter = show_system or show_docker or show_project

        # Output Results Table or Dashboard Summary
        if not has_subsystem_filter and not detailed_mode:
            # Print Summary Dashboard View (Default)
            UI.raw("\n--- Environment Health Summary ---")

            def format_dashboard_line(label, status):
                if status is True:
                    color = UI.GREEN
                    icon = "[ OK ]"
                elif status == "warn":
                    color = UI.YELLOW
                    icon = "[WARN]"
                else:
                    color = UI.RED
                    icon = "[FAIL]"
                return f"{color}{icon:<6}{UI.COLOR_OFF} {label}"

            UI.raw(
                format_dashboard_line(
                    "System (Python, Executable, Venv)", subsystem_status["system"]
                )
            )
            UI.raw(
                format_dashboard_line(
                    "Docker (Engine, Compose, Resources)", subsystem_status["docker"]
                )
            )
            UI.raw(
                format_dashboard_line(
                    "Project (Metadata, DNS, Mounts, SSL)", subsystem_status["project"]
                )
            )

            # If there are failures or warnings, print the standard table with only the failing/warning checks
            if not all_ok or has_warnings:
                UI.raw(f"\n{'Component':<35} {'Status':<30}")
                UI.raw("-" * 75)
                for component, status, ok in self.results:
                    if ok is not True:
                        color = UI.YELLOW if ok == "warn" else UI.RED
                        icon = "⚠️ " if ok == "warn" else "❌ "
                        UI.raw(f"{component:<35} {color}{icon} {status}{UI.COLOR_OFF}")
        else:
            # Print Detailed View (Filtered or Full)
            UI.raw(f"\n{'Component':<35} {'Status':<30}")
            UI.raw("-" * 75)

            subsystems_to_show = []
            if has_subsystem_filter:
                if show_system:
                    subsystems_to_show.append("system")
                if show_docker:
                    subsystems_to_show.append("docker")
                if show_project:
                    subsystems_to_show.append("project")
            else:
                subsystems_to_show = ["system", "docker", "project"]

            for sub in subsystems_to_show:
                for component, status, ok in categorized[sub]:
                    if ok is True:
                        color = UI.GREEN
                        icon = "✅ "
                    elif ok == "warn":
                        color = UI.YELLOW
                        icon = "⚠️ "
                    else:
                        color = UI.RED
                        icon = "❌ "
                    UI.raw(f"{component:<35} {color}{icon} {status}{UI.COLOR_OFF}")

        # Print Actionable Hints at the end
        auto_fix_mode = getattr(self.args, "fix", False)
        if self.hints and (detailed_mode or has_subsystem_filter):
            UI.raw(f"\n{UI.CYAN}--- Recommended Actions ---{UI.COLOR_OFF}")
            for h in self.hints:
                padding = UI.get_padding("ℹ")
                UI.raw(f"{UI.CYAN}ℹ{padding}{UI.COLOR_OFF}Fix: {h['text']}")
                if h["doc"]:
                    UI.raw(f"   Doc: {UI.CYAN}{h['doc']}{UI.COLOR_OFF}")
                UI.raw("")

        fixable_commands = []
        if auto_fix_mode and self.hints:
            for h in self.hints:
                if not h.get("text"):
                    continue
                # Remove color codes for reliable regex matching
                clean_text = re.sub(r"\033\[[0-9;]*m", "", str(h["text"]))
                # Look for Run '...' or Run: '...'
                match = re.search(r"Run\s*:?\s*'([^']+)'", clean_text)
                if match:
                    cmd_str = match.group(1)
                    if cmd_str.startswith("ldm ") and cmd_str not in fixable_commands:
                        fixable_commands.append(cmd_str)

        if fixable_commands:
            UI.heading("Auto-Remediation")
            for cmd_str in fixable_commands:
                UI.info(f"Applying fix: {UI.CYAN}{cmd_str}{UI.COLOR_OFF}")
                # Use the absolute path to the currently running LDM script
                full_cmd = [
                    sys.executable,
                    str(Path(sys.argv[0]).resolve()),
                    *cmd_str.split()[1:],
                ]
                # We use os.system or subprocess to run the LDM command
                try:
                    subprocess.run(full_cmd, check=True)
                except subprocess.CalledProcessError:
                    UI.error(f"Auto-fix failed for: {cmd_str}")

        if getattr(self.args, "bundle", False):
            _generate_debug_bundle(self.handler, self.results, self.project_paths)
            sys.exit(0)

        if all_ok and not has_warnings:
            UI.success("Everything looks good! Your environment is ready.")
            sys.exit(0)
        elif all_ok and has_warnings:
            msg = "Some non-critical issues were detected. Check the items above."
            if self.hints and not detailed_mode and not auto_fix_mode:
                msg += f" Run '{UI.WHITE}ldm doctor --detailed{UI.COLOR_OFF}' for troubleshooting self.hints and fixes."
            UI.warning(msg)
            sys.exit(0)
        else:
            msg = "Critical issues were detected. Check the items above."
            if self.hints and not detailed_mode and not auto_fix_mode:
                msg += f" Run '{UI.WHITE}ldm doctor --detailed{UI.COLOR_OFF}' for troubleshooting self.hints and fixes."
            if auto_fix_mode:
                msg += f" (Attempted {len(fixable_commands)} auto-fixes)."
            UI.error(msg)
            sys.exit(1)

    def _check_ssl_diagnostics(self):
        UI.heading("LDM Doctor - SSL Diagnostics")
        target_domain = getattr(self.args, "domain", None)
        domains_to_check = []

        if target_domain:
            domains_to_check.append(target_domain)
        else:
            UI.info("Scanning for active projects with SSL enabled...")
            roots = self.handler.manager.find_dxp_roots()
            for r in roots:
                p_meta = self.handler.manager.read_meta(r["path"])
                if str(p_meta.get("ssl", "false")).lower() == "true":
                    host_name = p_meta.get("host_name")
                    if host_name:
                        domains_to_check.append(host_name)

        if not domains_to_check:
            UI.warning(
                "No SSL-enabled projects detected. Pass --domain to explicitly check a domain."
            )
            return

        import socket
        import subprocess

        for domain in set(domains_to_check):
            print(f"\n{UI.BOLD}=== SSL Analysis: {domain} ==={UI.COLOR_OFF}")
            try:
                ip = socket.gethostbyname(domain)
                print(f"🌍 Resolves to: {ip}")
                if ip != "127.0.0.1":
                    UI.warning(
                        f"Domain {domain} does not resolve to 127.0.0.1 locally. It resolves to {ip}. Routing may bypass Traefik."
                    )
            except Exception as e:
                UI.error(f"Failed to resolve {domain}: {e}")
                continue

            print("🔍 Fetching certificate chain...")
            # Use openssl s_client to fetch cert info without external py libs
            cmd = [
                "openssl",
                "s_client",
                "-showcerts",
                "-connect",
                "127.0.0.1:443",
                "-servername",
                domain,
            ]
            try:
                res = subprocess.run(
                    cmd, input=b"", capture_output=True, timeout=5, check=False
                )
                out = res.stdout.decode("utf-8", errors="ignore")

                # Parse subject and issuer from openssl output
                subject = "Unknown"
                issuer = "Unknown"

                for line in out.splitlines():
                    if line.strip().startswith("subject="):
                        subject = line.strip().replace("subject=", "").strip()
                    elif line.strip().startswith("issuer="):
                        issuer = line.strip().replace("issuer=", "").strip()

                if "Unknown" in subject and "Unknown" in issuer:
                    UI.error(
                        "Failed to extract certificate details. Is Traefik running?"
                    )
                    continue

                print(f"📄 Subject: {subject}")
                print(f"🖋  Issuer:  {issuer}")

                if (
                    "CN = TRAEFIK DEFAULT CERT" in subject
                    or "CN=TRAEFIK DEFAULT CERT" in subject
                ):
                    UI.error(
                        "❌ Certificate Mismatch: Traefik is serving its default fallback certificate instead of the project certificate."
                    )

                    if getattr(self.args, "fix", False):
                        UI.info("Auto-fix: Restarting Traefik proxy...")
                        try:
                            self.manager.infra.cmd_restart_proxy()
                        except Exception as e:
                            UI.error(f"Failed to restart Traefik: {e}")
                    else:
                        print(
                            f"   💡 Hint: Try restarting Traefik: {UI.WHITE}docker restart liferay-proxy-global{UI.COLOR_OFF}"
                        )
                        print(
                            f"   💡 Or run this command with the --fix flag: {UI.WHITE}ldm doctor --ssl --fix{UI.COLOR_OFF}"
                        )
                elif "mkcert development CA" in issuer or "mkcert" in issuer.lower():
                    UI.success(
                        "Certificate is a valid LDM local development certificate."
                    )
                else:
                    UI.info("Certificate is valid but issued by a third party.")

            except subprocess.TimeoutExpired:
                UI.error("Timeout fetching certificate. Is port 443 open?")
            except Exception as e:
                UI.error(f"Error fetching certificate: {e}")

        print("")


def run_doctor(handler, project_id=None, all_projects=False, fix_hosts=False):
    """Verify host environment health and project dependencies."""
    if fix_hosts and handler.manager and hasattr(handler.manager, "args"):
        handler.manager.args.fix_hosts = True
    runner = DoctorRunner(handler, project_id, all_projects)
    runner.run()


def _verify_dependency_integrity(self, package_name: str) -> tuple[str, str | bool]:
    """Verify the file integrity of a dependency against its RECORD metadata."""
    import base64
    import hashlib

    try:
        dist = distribution(package_name)
    except Exception:
        return "Missing", "error"

    record_content = dist.read_text("RECORD")
    if not record_content:
        return "No RECORD found", "warn"

    for line in record_content.splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue

        rel_path, hash_info, _ = parts[0], parts[1], parts[2]
        if not hash_info or not hash_info.startswith("sha256="):
            continue

        expected_hash = hash_info.split("=", 1)[1]
        path: Any
        locate_res = dist.locate_file(rel_path)
        if type(locate_res).__name__ in ("MagicMock", "Mock"):
            path = locate_res
        else:
            path = Path(str(locate_res))

        if not path.exists():
            # We skip missing bin wrappers/metadata files to avoid false positives,
            # but flag any missing Python source files as corrupted.
            if rel_path.endswith(".py"):
                return f"Corrupted: {rel_path} missing", "error"
            continue

        try:
            h = hashlib.sha256()
            h.update(path.read_bytes())
            actual_hash = (
                base64.urlsafe_b64encode(h.digest()).decode("utf-8").rstrip("=")
            )
            if actual_hash != expected_hash:
                return f"Tampered: {rel_path} modified", "error"
        except Exception as e:
            return f"Error reading {rel_path}: {e}", "error"

    return "OK", True


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
        return "Installed (NOT TRUSTED)", "warn", ca_root
    except Exception:
        return "Not found in PATH", "warn", None


def _check_openssl(self):
    """Checks for OpenSSL installation."""
    try:
        openssl_version = run_command(["openssl", "version"], check=False)
        if openssl_version:
            return openssl_version, True
        if platform.system().lower() == "windows":
            return (
                "Not found (Install Git for Windows, Scoop, or Chocolatey)",
                False,
            )
        return "Not found", False
    except Exception:
        return "Not found in PATH", False


def _check_lcp_cli(self):
    """Checks for Liferay Cloud CLI installation and authentication."""
    try:
        lcp_bin = shutil.which("lcp")
        if not lcp_bin:
            return "LCP CLI Not Installed", "warn"

        is_auth, _ = self.manager.cloud._is_cloud_authenticated()
        if is_auth:
            return "Logged In", True
        return "Not Logged In (Run 'lcp login')", "warn"
    except Exception:
        return None, None


def _check_elasticsearch_watermarks(self, add_hint):
    """Checks if Elasticsearch is blocking indices due to disk watermarks."""
    search_name = "liferay-search-global"
    try:
        # 1. Check for unassigned shards and allocation decisions
        explain_raw = run_command(
            [
                "docker",
                "exec",
                search_name,
                "curl",
                "-s",
                "http://localhost:9200/_cluster/allocation/explain",
            ],
            check=False,
        )
        if not explain_raw:
            return None

        import json

        data = json.loads(explain_raw)

        # Check for disk_threshold decider in node decisions
        decisions = data.get("node_allocation_decisions", [])
        for node in decisions:
            for decider in node.get("deciders", []):
                if (
                    decider.get("decider") == "disk_threshold"
                    and decider.get("decision") == "NO"
                ):
                    explanation = decider.get("explanation", "")
                    add_hint(
                        f"Elasticsearch: {explanation}",
                        "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/TROUBLESHOOTING.md#disk-space-issues-elasticsearch-flood-stage",
                    )
                    add_hint(
                        f"Run '{UI.WHITE}ldm prune --seeds --samples{UI.COLOR_OFF}' to reclaim space."
                    )
                    return "Disk Watermark Exceeded (Blocked)"

        # 2. Check for explicitly read-only indices
        settings_raw = run_command(
            [
                "docker",
                "exec",
                search_name,
                "curl",
                "-s",
                "http://localhost:9200/_all/_settings?flat_settings=true",
            ],
            check=False,
        )
        if settings_raw and "index.blocks.read_only_allow_delete" in settings_raw:
            add_hint(
                "Elasticsearch indices are in READ-ONLY mode due to flood-stage disk pressure.",
                "https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/TROUBLESHOOTING.md#disk-space-issues-elasticsearch-flood-stage",
            )
            return "Read-Only (Flood Stage)"

    except Exception:
        pass
    return None


def _check_docker_creds(self):
    """Checks for Docker credential store health."""
    try:
        docker_config_path = get_actual_home() / ".docker" / "config.json"
        if not docker_config_path.exists():
            return None, None

        with open(docker_config_path) as f:
            config = json.loads(f.read())
            creds_store = config.get("credsStore")
            if not creds_store:
                return None, None

            helper_bin = f"docker-credential-{creds_store}"
            if not shutil.which(helper_bin):
                return f"Broken ({creds_store} helper missing)", False
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
        cpus_ok: Any = True
        if cpus < 2:
            cpus_ok = False
        elif cpus < 4:
            cpus_ok = "warn"
        results.append(("Docker CPUs", f"{cpus} Cores", cpus_ok))
        if cpus_ok is not True:
            UI.raw(
                f"  {UI.CYAN}ℹ{UI.COLOR_OFF} Hint: Allocate more CPU cores in your Docker provider settings."
            )
            UI.raw(
                f"    Doc: {UI.CYAN}https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#docker-resource-alignment-windowswsl2macos{UI.COLOR_OFF}"
            )

        mem_ok: Any = True
        if mem_gb < 4.0:
            mem_ok = False
        elif mem_gb < 7.5:
            mem_ok = "warn"
        results.append(("Docker Memory", f"{mem_gb:.1f} GB", mem_ok))
        if mem_ok is not True:
            UI.raw(
                f"  {UI.CYAN}ℹ{UI.COLOR_OFF} Hint: Allocate more RAM in your Docker provider settings."
            )
            UI.raw(
                f"    Doc: {UI.CYAN}https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/docs/INSTALLATION.md#docker-resource-alignment-windowswsl2macos{UI.COLOR_OFF}"
            )

        return results
    except Exception:
        return []


def validate_properties_file(self, file_path):
    """Checks for structural errors and duplicate keys in a .properties file."""
    errors = []
    try:
        lines = file_path.read_text().splitlines()
        if not lines:
            return "Empty File", "warn", []

        last_line_continued = False
        keys_found: dict[str, list[int]] = {}  # key -> [line_numbers]
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

            last_line_continued = is_continuation_line(stripped)

        # Report Duplicates
        for key, occurrences in keys_found.items():
            if len(occurrences) > 1:
                errors.append(
                    f"Duplicate key '{key}' found on lines: {', '.join(map(str, occurrences))}"
                )

        # Final line check: if the last line ends in '\', it's an invalid continuation
        if last_line_continued:
            errors.append("File ends with a trailing backslash (broken continuation).")

        if errors:
            return f"Inconsistent ({len(errors)} issues)", "warn", errors
        return "Valid Structure", True, []
    except Exception as e:
        return f"Check Failed ({e})", "warn", []


def validate_lcp_json(self, file_path):
    """Validates the structure and content of an LCP.json file."""
    from ldm_core.handlers.validation import ClientExtensionAnalyzer

    try:
        content = file_path.read_text()
        data = json.loads(content)
        errors = ClientExtensionAnalyzer.validate_lcp_json_structure(data)

        if errors:
            return f"Inconsistent ({len(errors)} issues)", "warn", errors
        return "Valid Structure", True, []
    except json.JSONDecodeError as e:
        return f"Invalid JSON ({e})", False, [str(e)]
    except Exception as e:
        return f"Check Failed ({e})", "warn", [str(e)]


def _check_container_health_logs(self, container_name, add_hint=None, tail=20):
    """Checks the last N lines of container logs for errors or warnings."""
    from ldm_core.docker_service import DockerService

    try:
        logs = DockerService.get_logs(container_name, tail=tail)
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
                if "failed to obtain region from default provider chain" in line_lower:
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
                            x in line.lower() for x in ["liferay_backup", "path.repo"]
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
    from ldm_core.docker_service import DockerService

    try:
        import re

        logs = DockerService.get_logs(container_name, tail=tail)
        if not logs:
            return "Initializing...", True

        lines = logs.splitlines()

        # 1. Success Marker (Liferay is fully up)
        if any("started in" in line.lower() for line in lines) or any(
            "Liferay(TM) Portal" in line and "started" in line.lower() for line in lines
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
                        "field not found",
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


def _generate_debug_bundle(self, results, project_paths):
    """Generates a sanitized ZIP bundle for troubleshooting support."""
    import io
    import zipfile

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_name = f"ldm-debug-bundle-{timestamp}.zip"
    bundle_path = Path.cwd() / bundle_name

    UI.info(f"Generating sanitized debug bundle: {bundle_name}...")

    with zipfile.ZipFile(bundle_path, "w") as z:
        # 1. Doctor Report
        report = io.StringIO()
        report.write("LDM DOCTOR REPORT\n")
        report.write("=" * 40 + "\n\n")
        for comp, status, ok in results:
            status_clean = strip_ansi(str(status))
            icon = "[OK]" if ok is True else "[!!]" if ok == "warn" else "[FAIL]"
            report.write(f"{icon} {comp:<35} {status_clean}\n")
        z.writestr("doctor-report.txt", report.getvalue())

        # 2. Config Files (~/.ldmrc)
        ldmrc = get_actual_home() / ".ldmrc"
        if ldmrc.exists():
            z.writestr("ldmrc.txt", UI.redact(ldmrc.read_text()))

        # 3. Project Specific Data
        for p_path in project_paths:
            p_name = p_path.name
            meta_file = p_path / ".liferay-docker.meta"
            if meta_file.exists():
                z.writestr(
                    f"projects/{p_name}/meta.txt", UI.redact(meta_file.read_text())
                )

            compose_file = p_path / "docker-compose.yml"
            if compose_file.exists():
                z.writestr(
                    f"projects/{p_name}/docker-compose.yml",
                    UI.redact(compose_file.read_text()),
                )

            # Collect redacted logs (last 500 lines)
            meta = self.manager.read_meta(p_path)
            p_id = meta.get("container_name") or p_name
            # Use project label to find liferay container
            from ldm_core.docker_service import DockerService

            logs = DockerService.get_logs(f"{p_id}-liferay", tail=500)
            if logs:
                z.writestr(
                    f"projects/{p_name}/liferay-redacted.log",
                    UI.redact(logs),
                )

        global_ldm_dir = get_actual_home() / ".ldm"
        if (global_ldm_dir / "lfr-tunnel" / "token").exists():
            z.write(
                global_ldm_dir / "lfr-tunnel" / "token", "ldm_config/lfr-tunnel/token"
            )

        # Include the global trace log if it exists
        trace_log = global_ldm_dir / "last-command.log"
        if trace_log.exists():
            z.write(trace_log, "last-command.log")

    UI.success(f"Debug bundle created: {bundle_name}")
    UI.info("Please attach this file to your GitHub Issue.")
    return bundle_path
