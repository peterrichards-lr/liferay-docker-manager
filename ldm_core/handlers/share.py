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

        if installed_ver:
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

    def _verify_compatibility(self, bin_path, local_version):
        """Checks the binary against the remote server for minimum and latest versions."""
        if not local_version or not bin_path.exists():
            return

        try:
            res = subprocess.run(
                [str(bin_path), "-check-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode != 0 or not res.stdout:
                return

            data = json.loads(res.stdout.strip())
            latest_version = data.get("latest_version")
            min_version = data.get("min_version")

            if not latest_version or not min_version:
                return

            local_tuple = version_to_tuple(local_version)
            min_tuple = version_to_tuple(min_version)
            latest_tuple = version_to_tuple(latest_version)

            if local_tuple < min_tuple:
                UI.die(
                    f"Your Liferay Tunnel client is too old to connect to the server. Minimum required version is {min_version}."
                )
            elif local_tuple < latest_tuple:
                UI.warning(
                    f"A new version of Liferay Tunnel ({latest_version}) is available. You are running v{local_version}."
                )
        except Exception:
            pass

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

    def cmd_start(self, project_id=None, subdomain=None, ports=None, provider=None):
        """Starts the active sharing tunnel (lfr-tunnel or ngrok)."""
        root = self.manager.detect_project_path(project_id)
        project_id = root.name if root else None
        project_meta = self.manager.read_meta(root) if root else {}

        # Resolve provider
        if not provider:
            provider = project_meta.get("share_provider") or "lfr-tunnel"

        if provider == "lfr-tunnel":
            bin_path = self._ensure_binary()
            installed_ver = self._get_installed_version(bin_path)
            self._verify_compatibility(bin_path, installed_ver)

            token = self._get_auth_token()

            ports = ports or "8080"
            subdomain = subdomain or project_id

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

        elif provider == "ngrok":
            if not root:
                UI.die(
                    "Ngrok sharing requires a project context. Run from a project directory or specify -p <project>."
                )

            # Ensure ngrok auth token is set
            auth_token = self.manager.config.get_ngrok_auth_token()
            if not auth_token:
                UI.info("An ngrok Auth Token is required to use the expose feature.")
                UI.info(
                    "You can find yours at: https://dashboard.ngrok.com/get-started/your-authtoken"
                )
                if not self.manager.non_interactive:
                    auth_token = UI.ask("Enter your ngrok Auth Token")
                if auth_token:
                    self.manager.config.set_ngrok_auth_token(auth_token)
                    UI.success("Saved ngrok token to global configuration.")
                else:
                    UI.die("No token provided. Ngrok cannot be configured.")

            # Set metadata
            project_meta["share"] = "true"
            project_meta["share_provider"] = "ngrok"
            project_meta["expose"] = "true"
            if subdomain:
                project_meta["share_subdomain"] = subdomain

            self.manager.write_meta(root, project_meta)

            # Regenerate compose file and boot ngrok service
            paths = self.manager.setup_paths(root)
            UI.info("Regenerating stack configuration with ngrok sidecar...")
            self.manager.runtime.sync_stack(
                paths, project_meta, no_up=True, show_summary=False
            )

            from ldm_core.utils import get_compose_cmd

            compose_base = get_compose_cmd()
            if not compose_base:
                UI.die("Docker Compose not found.")

            UI.info("Starting ngrok sidecar container...")
            res = subprocess.run(
                [*compose_base, "up", "-d", "ngrok"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                UI.success("Ngrok container started.")
                self.manager.runtime._print_ngrok_url(
                    project_meta.get("container_name") or project_id
                )
            else:
                UI.error(f"Failed to start ngrok container (Exit {res.returncode})")
                if res.stderr:
                    print(res.stderr.strip())

    def cmd_status(self, project_id=None):
        """Queries the status of the active sharing tunnel."""
        root = self.manager.detect_project_path(project_id)
        project_meta = self.manager.read_meta(root) if root else {}
        provider = project_meta.get("share_provider")

        if (
            provider == "ngrok"
            or str(project_meta.get("expose", "false")).lower() == "true"
        ):
            container_name = project_meta.get("container_name") or (
                root.name if root else None
            )
            if container_name:
                self.manager.runtime._print_ngrok_url(container_name)
            else:
                UI.error("No active ngrok container context found.")
        else:
            # Default to lfr-tunnel
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

    def cmd_stop(self, project_id=None):
        """Terminates the active sharing tunnel."""
        root = self.manager.detect_project_path(project_id)
        project_meta = self.manager.read_meta(root) if root else {}
        provider = project_meta.get("share_provider")

        if (
            provider == "ngrok"
            or str(project_meta.get("expose", "false")).lower() == "true"
        ):
            if not root:
                UI.die(
                    "Project context not found. Specify -p <project> to stop sharing."
                )

            # Disable expose in metadata
            project_meta["share"] = "false"
            project_meta["expose"] = "false"
            self.manager.write_meta(root, project_meta)

            from ldm_core.utils import get_compose_cmd

            compose_base = get_compose_cmd()
            if not compose_base:
                UI.die("Docker Compose not found.")

            UI.info("Stopping ngrok sidecar container...")
            res = subprocess.run(
                [*compose_base, "rm", "-fs", "ngrok"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                UI.success("Ngrok sharing stopped.")
            else:
                UI.error(f"Failed to stop ngrok container (Exit {res.returncode})")
                if res.stderr:
                    print(res.stderr.strip())
        else:
            # Default to lfr-tunnel
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
