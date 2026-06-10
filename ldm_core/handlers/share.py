import contextlib
import json
import os
import platform
import re
import ssl
import subprocess
import urllib.request

from ldm_core.ui import UI
from ldm_core.utils import get_actual_home, version_to_tuple

MIN_LFR_TUNNEL_VERSION = "0.1.0"


class ShareService:
    """Service for lfr-tunnel management (downloader, start, status, stop)."""

    def __init__(self, manager):
        self.manager = manager

    def _get_binary_path(self):
        """Returns the local path for the lfr-tunnel binary."""
        is_windows = platform.system().lower() == "windows"
        bin_name = "lfr-tunnel.exe" if is_windows else "lfr-tunnel"
        return get_actual_home() / ".ldm" / "bin" / bin_name

    def _get_installed_version(self, bin_path):
        """Queries the binary version by running it with -version."""
        if not bin_path.exists():
            return None
        try:
            res = subprocess.run(
                [str(bin_path), "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = (res.stdout or res.stderr or "").strip()
            match = re.search(r"v?(\d+\.\d+\.\d+)", output)
            if match:
                return match.group(1)
        except Exception:
            pass
        return None

    def _ensure_binary(self):
        """Ensures the correct version of lfr-tunnel is installed, downloading it if necessary."""
        bin_path = self._get_binary_path()
        installed_ver = self._get_installed_version(bin_path)

        needs_download = False
        if not installed_ver:
            needs_download = True
        elif version_to_tuple(installed_ver) < version_to_tuple(MIN_LFR_TUNNEL_VERSION):
            UI.warning(
                f"Installed lfr-tunnel version v{installed_ver} is outdated. Minimum required is v{MIN_LFR_TUNNEL_VERSION}."
            )
            needs_download = True

        if not needs_download:
            return bin_path

        # Determine OS and Arch
        sys_type = platform.system().lower()
        if sys_type == "darwin":
            os_name = "darwin"
        elif sys_type == "linux":
            os_name = "linux"
        elif sys_type == "windows":
            os_name = "windows"
        else:
            UI.die(f"Unsupported operating system: {sys_type}")

        machine = platform.machine().lower()
        if machine in ["x86_64", "amd64"]:
            arch_name = "amd64"
        elif machine in ["arm64", "aarch64"]:
            arch_name = "arm64"
        else:
            # Default fallback
            arch_name = "amd64"

        # Construct download URL
        ext = ".exe" if os_name == "windows" else ""
        url = f"https://github.com/peterrichards-lr/lfr-tunnel/releases/latest/download/lfr-tunnel-{os_name}-{arch_name}{ext}"

        bin_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with UI.spinner(
                f"Downloading lfr-tunnel for {os_name}-{arch_name}..."
            ) as s:
                context = ssl._create_unverified_context()  # nosec B323
                with (
                    urllib.request.urlopen(url, context=context) as response,  # nosec B310
                    open(bin_path, "wb") as out_file,
                ):
                    out_file.write(response.read())

            if os_name != "windows":
                bin_path.chmod(bin_path.stat().st_mode | 0o111)  # chmod +x

            # Verify installation
            new_ver = self._get_installed_version(bin_path)
            if not new_ver:
                UI.die("Failed to verify lfr-tunnel installation after download.")
            UI.success(f"lfr-tunnel v{new_ver} ready.")
        except Exception as e:
            UI.die(f"Failed to download/install lfr-tunnel: {e}")

        return bin_path

    def _get_auth_token(self):
        """Retrieves the access token in priority order, prompting the user if missing."""
        # Priority 1: Env var
        token = os.environ.get("LFT_CLIENT_TOKEN")
        if token:
            return token

        # Priority 2: ~/.lfr-tunnel/token file
        token_file = get_actual_home() / ".lfr-tunnel" / "token"
        if token_file.exists():
            with contextlib.suppress(Exception):
                token = token_file.read_text().strip()
                if token:
                    return token

        # Priority 3: LDM global config (.ldmrc)
        config = self.manager.config.get_global_config()
        token = config.get("lfr_tunnel_token")
        if token:
            return token

        # Priority 4: Interactive prompt
        if not self.manager.non_interactive:
            token = UI.ask("Enter your Liferay Tunnel token (LFT_CLIENT_TOKEN)")
            if token:
                token = token.strip()
                config = self.manager.config.get_global_config()
                config["lfr_tunnel_token"] = token
                config_path = get_actual_home() / ".ldmrc"
                with contextlib.suppress(Exception):
                    config_path.write_text(json.dumps(config, indent=4))
                return token

        UI.die(
            "Liferay Tunnel token (LFT_CLIENT_TOKEN) not found. Please set LFT_CLIENT_TOKEN environment variable."
        )
        return None

    def cmd_start(self, subdomain=None, ports=None):
        """Starts the lfr-tunnel background client."""
        bin_path = self._ensure_binary()
        token = self._get_auth_token()

        ports = ports or "8080"

        # Build command list
        cmd = [str(bin_path), "-background", "-ports", ports]
        if subdomain:
            cmd += ["-subdomain", subdomain]

        env = os.environ.copy()
        env["LFT_CLIENT_TOKEN"] = token

        UI.info("Starting lfr-tunnel in the background...")
        try:
            res = subprocess.run(
                cmd, env=env, capture_output=True, text=True, check=False
            )
            if res.returncode == 0:
                UI.success("Tunnel started in the background.")
                if res.stdout:
                    print(res.stdout.strip())
            else:
                UI.error(f"Failed to start tunnel (Exit {res.returncode})")
                if res.stderr:
                    print(res.stderr.strip())
        except Exception as e:
            UI.die(f"Process invocation error: {e}")

    def cmd_status(self):
        """Queries the status of the running background tunnel."""
        bin_path = self._ensure_binary()
        cmd = [str(bin_path), "-status"]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.stdout:
                print(res.stdout.strip())
            if res.stderr and res.returncode != 0:
                print(res.stderr.strip())
        except Exception as e:
            UI.die(f"Process invocation error: {e}")

    def cmd_stop(self):
        """Terminates the background tunnel client."""
        bin_path = self._ensure_binary()
        cmd = [str(bin_path), "-stop"]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.stdout:
                print(res.stdout.strip())
            if res.stderr and res.returncode != 0:
                print(res.stderr.strip())
        except Exception as e:
            UI.die(f"Process invocation error: {e}")
