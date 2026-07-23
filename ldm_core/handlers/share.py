import contextlib
import json
import os
import platform
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

from ldm_core.ui import UI
from ldm_core.utils import download_file, get_actual_home, run_command, version_to_tuple


class ShareService:
    """Service for lfr-tunnel management (downloader, start, status, stop)."""

    def __init__(self, manager):
        self.manager = manager

    def _resolve_existing_binary(self):
        """Resolves the path to an existing, working lfr-tunnel binary if available."""
        is_windows = platform.system().lower() == "windows"
        bin_name = "lfr-tunnel.exe" if is_windows else "lfr-tunnel"

        # 1. Environment variables
        env_bin = os.environ.get("LDM_LFR_TUNNEL_BIN") or os.environ.get(
            "LFR_TUNNEL_BIN"
        )
        if env_bin:
            path = Path(env_bin)
            if self._get_installed_version(path):
                return path

        # 2. Global config
        config = self.manager.config.get_global_config()
        config_bin = config.get("lfr_tunnel_bin")
        if config_bin:
            path = Path(config_bin)
            if self._get_installed_version(path):
                return path

        # 3. System PATH check
        sys_path = shutil.which(bin_name)
        if sys_path:
            path = Path(sys_path)
            if self._get_installed_version(path):
                return path

        # 4. Fallback default location
        default_path = get_actual_home() / ".ldm" / "bin" / bin_name
        if default_path.exists() and self._get_installed_version(default_path):
            return default_path

        return None

    def _get_binary_path(self):
        """Returns the local path for the lfr-tunnel binary, checking PATH/custom locations first."""
        existing = self._resolve_existing_binary()
        if existing:
            return existing

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
                check=False,
            )
            output = (res.stdout or res.stderr or "").strip()
            match = re.search(r"v?(\d+\.\d+\.\d+)", output)
            if match:
                return match.group(1)
        except Exception:
            pass
        return None

    def _get_docker_installed_version(self, image):
        """Queries the docker image version by running it with -version."""
        try:
            res = subprocess.run(
                ["docker", "run", "--rm", image, "-version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            output = (res.stdout or res.stderr or "").strip()
            match = re.search(r"v?(\d+\.\d+\.\d+)", output)
            if match:
                return match.group(1)
        except Exception:
            pass
        return None

    def _ensure_binary(self):  # noqa: C901, PLR0912, PLR0915
        """Ensures the correct version of lfr-tunnel is installed, downloading it if necessary."""
        bin_path = self._get_binary_path()
        installed_ver = self._get_installed_version(bin_path)

        if installed_ver:
            return bin_path

        if getattr(self.manager, "dry_run", False):
            return bin_path

        # Check if user authorized installation
        auto_install = getattr(self.manager.args, "auto_install_lfr_tunnel", False)
        authorized = auto_install

        if not authorized and not self.manager.non_interactive:
            # Interactive prompt fallback
            custom_cmd = (
                os.environ.get("LDM_LFR_TUNNEL_INSTALL_CMD")
                or os.environ.get("LFR_TUNNEL_INSTALL_CMD")
                or self.manager.config.get_global_config().get("lfr_tunnel_install_cmd")
            )
            if custom_cmd:
                prompt_msg = f"lfr-tunnel not found. Run custom installation command '{custom_cmd}'? [Y/n]"
            else:
                prompt_msg = "lfr-tunnel not found. Download and install it from GitHub to ~/.ldm/bin/lfr-tunnel? [Y/n]"

            authorized = UI.confirm(prompt_msg, default="Y")

        if not authorized:
            UI.die(
                "lfr-tunnel binary not found.\n"
                "To automatically install it, run with --auto-install-lfr-tunnel or in interactive mode.\n"
                "Alternatively, configure a custom binary path using 'lfr_tunnel_bin' in ~/.ldmrc or the LDM_LFR_TUNNEL_BIN env var."
            )

        # Run custom installer command if configured
        custom_cmd = (
            os.environ.get("LDM_LFR_TUNNEL_INSTALL_CMD")
            or os.environ.get("LFR_TUNNEL_INSTALL_CMD")
            or self.manager.config.get_global_config().get("lfr_tunnel_install_cmd")
        )

        if custom_cmd:
            UI.detail(f"Running custom installation command: {custom_cmd}")
            import shlex

            safe_cmd = shlex.split(custom_cmd)
            res = run_command(safe_cmd, check=False)
            if res is None:
                UI.die("Custom installation command failed.")

            resolved_bin = self._resolve_existing_binary()
            if resolved_bin:
                new_ver = self._get_installed_version(resolved_bin)
                UI.success(
                    f"lfr-tunnel v{new_ver} ready (installed via custom command)."
                )
                return resolved_bin
            UI.die(
                "Failed to locate lfr-tunnel binary after running the custom installation command."
            )

        # Default fallback: GitHub download
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
            arch_name = "amd64"

        # Construct download URL
        ext = ".exe" if os_name == "windows" else ""
        url = f"https://github.com/peterrichards-lr/lfr-tunnel/releases/latest/download/lfr-tunnel-{os_name}-{arch_name}{ext}"

        bin_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with UI.spinner(f"Downloading lfr-tunnel for {os_name}-{arch_name}..."):
                success = download_file(url, bin_path)
                if not success:
                    raise RuntimeError("Download failed.")

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

    def _verify_compatibility(self, cmd_prefix, local_version):
        """Checks the client against the remote server for minimum and latest versions."""
        if not local_version or not cmd_prefix:
            return

        try:
            res = subprocess.run(
                [*cmd_prefix, "-check-version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
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

            lv_str = (
                local_version if local_version.startswith("v") else f"v{local_version}"
            )
            latest_str = (
                latest_version
                if latest_version.startswith("v")
                else f"v{latest_version}"
            )

            if local_tuple < min_tuple:
                UI.die(
                    f"Your Liferay Tunnel client is too old to connect to the server (Minimum required: {min_version}). "
                    "Please upgrade using 'lfr-tunnel -upgrade' or 'docker pull peterrichards/lfr-tunnel'."
                )
            elif local_tuple < latest_tuple:
                UI.warning(
                    f"An update is available for lfr-tunnel (Current: {lv_str}, Latest: {latest_str})"
                )
        except Exception:
            pass

    def _get_auth_token(self):
        """Retrieves the access token in priority order, prompting the user if missing."""
        # Priority 1: Env var
        token = os.environ.get("LFT_CLIENT_TOKEN")
        if token:
            return token

        # Priority 2: OS Keyring
        from ldm_core.utils import get_keyring_token

        token = get_keyring_token("liferay-docker-manager", "lfr_tunnel_token")
        if token:
            return token

        # Priority 3: ~/.lfr-tunnel/token file
        token_file = get_actual_home() / ".lfr-tunnel" / "token"
        if token_file.exists():
            if platform.system().lower() != "windows":
                try:
                    token_file.chmod(0o600)
                except OSError:
                    pass
            with contextlib.suppress(Exception):
                token = token_file.read_text().strip()
                if token:
                    return token

        # Priority 4: LDM global config (.ldmrc)
        config = self.manager.config.get_global_config()
        token = config.get("lfr_tunnel_token")
        if token:
            return token

        # Priority 5: Interactive prompt
        if not self.manager.non_interactive:
            token = UI.ask("Enter your Liferay Tunnel token (LFT_CLIENT_TOKEN)")
            if token:
                token = token.strip()

                # 1. Save in OS Keyring
                from ldm_core.utils import set_keyring_token

                set_keyring_token("liferay-docker-manager", "lfr_tunnel_token", token)

                # 2. Save securely in LDM global config
                config = self.manager.config.get_global_config()
                config["lfr_tunnel_token"] = token
                from ldm_core.utils import save_global_config_safe

                config_path = get_actual_home() / ".ldmrc"
                save_global_config_safe(config_path, config)

                # 3. Write securely to ~/.lfr-tunnel/token (for the native client)
                from ldm_core.utils import safe_write_text

                safe_write_text(token_file, token, mode=0o600)

                return token

        UI.die(
            "Liferay Tunnel token (LFT_CLIENT_TOKEN) not found. Please set LFT_CLIENT_TOKEN environment variable."
        )
        return None

    def resolve_share_config(self, project_meta=None, provider=None, domain=None):  # noqa: PLR0912
        """Resolves share provider and share domain, prompting the user if not configured."""
        # 1. Resolve provider
        if not provider:
            provider = getattr(self.manager.args, "share_provider", None) or getattr(
                self.manager.args, "provider", None
            )
            if provider and not isinstance(provider, str):
                provider = None
        if not provider and project_meta:
            provider = project_meta.get("share_provider")
        if not provider:
            global_config = self.manager.config.get_global_config()
            provider = global_config.get("share_provider")
        if not provider:
            if self.manager.non_interactive:
                provider = "lfr-tunnel"
            else:
                provider = UI.ask(
                    "Choose sharing provider (lfr-tunnel, lfr-tunnel-docker, ngrok)",
                    "lfr-tunnel",
                )
                if provider not in ["lfr-tunnel", "lfr-tunnel-docker", "ngrok"]:
                    provider = "lfr-tunnel"
                self.manager.config.set_global_config("share_provider", provider)

        # 2. Resolve domain
        if not domain:
            domain = getattr(self.manager.args, "share_domain", None) or getattr(
                self.manager.args, "domain", None
            )
            if domain and not isinstance(domain, str):
                domain = None
        if not domain and project_meta:
            domain = project_meta.get("share_domain")
        if not domain:
            global_config = self.manager.config.get_global_config()
            domain = global_config.get("share_domain")
        if not domain:
            if provider in ["lfr-tunnel", "lfr-tunnel-docker"]:
                if self.manager.non_interactive:
                    domain = "lfr-demo.online"
                else:
                    domain = UI.ask(
                        "Choose sharing domain (lfr-demo.online, lfr-demo.se)",
                        "lfr-demo.online",
                    )
                    self.manager.config.set_global_config("share_domain", domain)
            else:
                domain = ""

        return provider, domain

    def resolve_public_tunnel_urls(self, subdomain, project_id=None):
        """Resolves all public tunnel URLs natively or via Docker API."""
        urls: list = []
        if getattr(self.manager, "dry_run", False):
            return urls

        import json
        import subprocess

        # 1. Try Native CLI
        try:
            bin_path = self._get_binary_path()
            if bin_path and os.path.exists(bin_path):
                result = subprocess.run(
                    [str(bin_path), "-status-json", "-subdomain", subdomain],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                if result.returncode == 0:
                    status_data = json.loads(result.stdout)
                    fetched = status_data.get("public_urls")
                    if fetched and isinstance(fetched, list):
                        urls.extend(fetched)
        except Exception:
            pass

        # 2. Try Docker Runtime Inspector API
        if not urls:
            try:
                host = "lfr-tunnel" if os.path.exists("/.dockerenv") else "127.0.0.1"
                req = urllib.request.Request(f"http://{host}:4040/api/info")
                with urllib.request.urlopen(req, timeout=3) as response:  # nosec B310
                    if response.status == 200:
                        data = json.loads(response.read().decode())
                        fetched = data.get("public_urls")
                        if fetched and isinstance(fetched, list):
                            urls.extend(fetched)
            except Exception:
                pass

        return urls

    def resolve_public_tunnel_url(self, subdomain, project_id=None):
        """Resolves the primary public tunnel URL, falling back to static generation if offline."""
        urls = self.resolve_public_tunnel_urls(subdomain, project_id)
        if urls:
            return urls[0]

        server_url = os.environ.get("LFT_SERVER_URL")
        domain = None

        if not server_url and project_id:
            try:
                root = self.manager.detect_project_path(project_id)
                if root:
                    # Try from metadata
                    meta = self.manager.read_meta(root)
                    if meta and meta.get("share_domain"):
                        domain = meta["share_domain"]

                    if not domain:
                        # Try from .env file
                        env_file = root / ".env"
                        if env_file.exists():
                            for line in env_file.read_text().splitlines():
                                if line.startswith("LFT_SERVER_URL="):
                                    server_url = line.split("=", 1)[1].strip()
                                    break
            except Exception:
                pass

        if not domain:
            if server_url:
                from urllib.parse import urlparse

                parsed = urlparse(server_url)
                netloc = parsed.netloc or parsed.path
                host = netloc.split(":")[0]
                if host.startswith("tunnel."):
                    domain = host[7:]
                else:
                    domain = host
            else:
                # Load from share config resolution fallback
                _, domain = self.resolve_share_config()

        if not domain:
            domain = "lfr-demo.online"

        return f"https://{subdomain}.{domain}"

    def cmd_start(  # noqa: C901, PLR0912, PLR0915
        self,
        project_id=None,
        subdomain=None,
        ports=None,
        provider=None,
        image=None,
        inspector=False,
    ):
        """Starts the active sharing tunnel (lfr-tunnel or ngrok)."""
        root = self.manager.detect_project_path(project_id)
        project_id = root.name if root else None
        project_meta = self.manager.read_meta(root) if root else {}

        # Check for LCP.json to infer ports if not specified
        if not ports:
            lcp_candidates = [Path("lcp.json"), Path("LCP.json")]
            if root:
                lcp_candidates.extend(
                    [
                        root / "lcp.json",
                        root / "LCP.json",
                        root / "liferay" / "lcp.json",
                        root / "liferay" / "LCP.json",
                    ]
                )
            for lcp_cand in lcp_candidates:
                if lcp_cand.exists():
                    try:
                        import json

                        lcp_data = json.loads(lcp_cand.read_text(encoding="utf-8"))
                        if (
                            "ports" in lcp_data
                            and isinstance(lcp_data["ports"], list)
                            and len(lcp_data["ports"]) > 0
                        ):
                            ports = ",".join(str(p) for p in lcp_data["ports"])
                            UI.detail(f"Detected ports {ports} from {lcp_cand.name}")
                            break
                    except Exception:
                        pass

        # Resolve provider and domain
        provider, share_domain = self.resolve_share_config(
            project_meta, provider=provider
        )
        if root and share_domain:
            project_meta["share_domain"] = share_domain
            if provider == "lfr-tunnel":
                self.manager.write_meta(root, project_meta)

        if provider == "lfr-tunnel":
            bin_path = self._ensure_binary()
            installed_ver = self._get_installed_version(bin_path)
            self._verify_compatibility([str(bin_path)], installed_ver)

            token = self._get_auth_token()

            ports = ports or "8080"
            subdomain = subdomain or project_id

            cmd = [str(bin_path), "-background", "-ports", ports]
            if subdomain:
                cmd += ["-subdomain", subdomain]

            # Pass custom target host if configured
            host_name = project_meta.get("host_name", "localhost")
            if host_name and host_name != "localhost":
                cmd += ["-target-host", host_name]

            env = os.environ.copy()
            env["LFT_CLIENT_TOKEN"] = token
            env["LFR_TUNNEL_TOKEN"] = token
            if "LFT_SERVER_URL" not in env and share_domain:
                env["LFT_SERVER_URL"] = f"https://tunnel.{share_domain}"

            if getattr(self.manager, "dry_run", False):
                UI.detail(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(cmd)}"
                )
                UI.success("Tunnel started in the background.")
                public_url = self.resolve_public_tunnel_url(subdomain, project_id)
                UI.success(
                    f"🌍 Public Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
                )
                return

            UI.detail("Starting lfr-tunnel in the background...")
            try:
                res = subprocess.run(
                    cmd, env=env, capture_output=True, text=True, check=False
                )
                if res.returncode == 0:
                    success, err_msg = self._poll_tunnel_health(subdomain)
                    if success:
                        UI.success("Tunnel started in the background.")
                        if hasattr(self.manager, "config") and hasattr(
                            self.manager.config, "track_roi"
                        ):
                            self.manager.config.track_roi(180, "secure sharing tunnel")
                        public_url = self.resolve_public_tunnel_url(
                            subdomain, project_id
                        )
                        UI.success(
                            f"🌍 Public Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
                        )
                        self._sync_gui_state(
                            "start",
                            project_id,
                            public_url,
                            int(ports) if str(ports).isdigit() else 8080,
                        )
                        if res.stdout:
                            print(res.stdout.strip())
                    else:
                        UI.error(f"Tunnel healthcheck failed: {err_msg}")
                        # Clean up background process
                        try:
                            stop_cmd = [str(bin_path), "-stop"]
                            if subdomain:
                                stop_cmd += ["-subdomain", subdomain]
                            subprocess.run(
                                stop_cmd,
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                        except Exception:
                            pass
                        UI.die("Unable to establish tunnel connection.")
                else:
                    err_out = ((res.stderr or "") + "\n" + (res.stdout or "")).lower()
                    if "is already running" in err_out:
                        UI.die(
                            f"Tunnel is already running in the background for this subdomain. "
                            f"Run 'lfr-tunnel -stop -subdomain {subdomain}' to terminate it before trying again."
                        )

                    UI.error(f"Failed to start tunnel (Exit {res.returncode})")
                    if res.stderr:
                        print(res.stderr.strip())
                    log_file = (
                        get_actual_home() / ".lfr-tunnel" / f"client-{subdomain}.log"
                    )
                    if log_file.exists():
                        try:
                            logs_str = log_file.read_text(encoding="utf-8").strip()
                            if logs_str:
                                UI.error("Tunnel Log Output:")
                                print("-" * 40)
                                print(logs_str)
                                print("-" * 40)
                        except Exception:
                            pass
            except Exception as e:
                UI.die(f"Process invocation error: {e}")

        elif provider == "lfr-tunnel-docker":
            if not root:
                UI.die(
                    "lfr-tunnel-docker sharing requires a project context. Run from a project directory or specify -p <project>."
                )

            # Ensure tunnel auth token is set
            token = self._get_auth_token()
            ports = ports or "8080"
            subdomain = subdomain or project_id

            # Set metadata
            project_meta["share"] = "true"
            project_meta["share_provider"] = "lfr-tunnel-docker"
            if subdomain:
                project_meta["share_subdomain"] = subdomain
            if ports:
                project_meta["share_ports"] = str(ports)
            if image:
                project_meta["share_image"] = image
            project_meta["share_inspector"] = "true" if inspector else "false"
            if share_domain:
                project_meta["share_domain"] = share_domain
            base_container = project_meta.get("container_name") or project_id
            project_meta["tunnel_container_name"] = f"{base_container}-lfr-tunnel"

            # Proactive Version Checking for Docker
            docker_image = image or project_meta.get(
                "share_image", "peterrichards/lfr-tunnel:latest"
            )
            installed_ver = self._get_docker_installed_version(docker_image)
            self._verify_compatibility(
                ["docker", "run", "--rm", docker_image], installed_ver
            )

            self.manager.write_meta(root, project_meta)

            # Regenerate compose file and boot lfr-tunnel service
            paths = self.manager.setup_paths(root)
            UI.detail("Regenerating stack configuration with lfr-tunnel sidecar...")
            self.manager.runtime.cmd_run(
                project_id=project_meta.get("container_name") or paths["root"].name,
                no_up=True,
                show_summary=False,
                paths=paths,
                project_meta=project_meta,
            )

            from ldm_core.utils import get_compose_cmd

            compose_base = get_compose_cmd()
            if not compose_base:
                UI.die("Docker Compose not found.")

            if getattr(self.manager, "dry_run", False):
                UI.detail(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(compose_base)} up -d lfr-tunnel"
                )
                UI.success("Tunnel container started in the background.")
                public_url = self.resolve_public_tunnel_url(subdomain, project_id)
                UI.success(
                    f"🌍 Public Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
                )
                return

            UI.detail("Starting lfr-tunnel container...")
            res = subprocess.run(
                [*compose_base, "up", "-d", "lfr-tunnel"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                container_name = (
                    project_meta.get("tunnel_container_name")
                    or f"{base_container}-lfr-tunnel"
                )
                success, err_msg = self._poll_tunnel_health(
                    subdomain, container_name=container_name
                )
                if success:
                    UI.success("Tunnel container started in the background.")
                    if hasattr(self.manager, "config") and hasattr(
                        self.manager.config, "track_roi"
                    ):
                        self.manager.config.track_roi(180, "secure sharing tunnel")
                    public_url = self.resolve_public_tunnel_url(subdomain, project_id)
                    UI.success(
                        f"🌍 Public Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
                    )
                    self._sync_gui_state(
                        "start",
                        project_id,
                        public_url,
                        int(ports) if str(ports).isdigit() else 8080,
                    )
                else:
                    UI.error(f"Tunnel container healthcheck failed: {err_msg}")
                    # Remove container so we don't leave it running in unhealthy state
                    try:
                        subprocess.run(
                            [*compose_base, "rm", "-fs", "lfr-tunnel"],
                            cwd=str(root),
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                    except Exception:
                        pass
                    UI.die("Unable to establish tunnel connection.")
            else:
                UI.error(f"Failed to start tunnel container (Exit {res.returncode})")
                if res.stderr:
                    print(res.stderr.strip())

        elif provider == "ngrok":
            if not root:
                UI.die(
                    "Ngrok sharing requires a project context. Run from a project directory or specify -p <project>."
                )

            # Ensure ngrok auth token is set
            auth_token = self.manager.config.get_ngrok_auth_token()
            if not auth_token:
                UI.detail("An ngrok Auth Token is required to use the expose feature.")
                UI.detail(
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
            UI.detail("Regenerating stack configuration with ngrok sidecar...")
            self.manager.runtime.cmd_run(
                project_id=project_meta.get("container_name") or paths["root"].name,
                no_up=True,
                show_summary=False,
                paths=paths,
                project_meta=project_meta,
            )

            from ldm_core.utils import get_compose_cmd

            compose_base = get_compose_cmd()
            if not compose_base:
                UI.die("Docker Compose not found.")

            if getattr(self.manager, "dry_run", False):
                UI.detail(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(compose_base)} up -d ngrok"
                )
                UI.success("Ngrok container started.")
                return

            UI.detail("Starting ngrok sidecar container...")
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

    def _get_tunnel_api_state(self, port=4040):
        """Queries lfr-tunnel embedded client API at http://127.0.0.1:4040/api/info and /api/state."""
        results = {}
        for endpoint in ("info", "state"):
            url = f"http://127.0.0.1:{port}/api/{endpoint}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "LDM-Client"})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        data = resp.read().decode("utf-8")
                        results[endpoint] = json.loads(data)
            except Exception:
                pass
        return results

    def cmd_status(self, project_id=None):  # noqa: C901, PLR0912, PLR0915
        """Queries the status of the active sharing tunnel."""
        root = self.manager.detect_project_path(project_id)
        project_meta = self.manager.read_meta(root) if root else {}
        provider = project_meta.get("share_provider")

        # First attempt real-time query of lfr-tunnel embedded client API
        api_data = self._get_tunnel_api_state(4040)
        if api_data.get("info") or api_data.get("state"):
            info = api_data.get("info", {})
            state = api_data.get("state", {})

            subdomain = (
                state.get("subdomain")
                or info.get("subdomain", {}).get("name")
                or project_meta.get("share_subdomain")
                or project_id
                or (root.name if root else "tunnel")
            )
            status = info.get("status") or ("healthy" if state.get("connected") else "disconnected")
            conn_state = state.get("connection_state") or (info.get("connection") or {}).get("state", "disconnected")
            public_urls = state.get("public_urls") or info.get("public_urls") or []
            if not public_urls and state.get("public_url"):
                public_urls = [state["public_url"]]

            UI.heading("Liferay Tunnel Status")
            UI.raw(f"  ● {UI.WHITE}Subdomain: {UI.CYAN}{subdomain}{UI.COLOR_OFF}")
            status_color = UI.GREEN if status == "healthy" or state.get("connected") else UI.RED
            UI.raw(f"  ● {UI.WHITE}Status: {status_color}{status}{UI.COLOR_OFF}")
            UI.raw(
                f"  ● {UI.WHITE}Connection State: {UI.WHITE if conn_state in ('connected', 'active') else UI.RED}{conn_state}{UI.COLOR_OFF}"
            )
            if public_urls:
                UI.raw(f"  ● {UI.WHITE}Public URLs:{UI.COLOR_OFF}")
                for url in public_urls:
                    UI.raw(f"    - {UI.GREEN}{url}{UI.COLOR_OFF}")
            UI.raw(
                f"  ● {UI.WHITE}Inspector Dashboard: {UI.CYAN}http://127.0.0.1:4040{UI.COLOR_OFF}"
            )
            UI.raw("")
            return

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
        elif provider == "lfr-tunnel-docker":
            if not root:
                UI.die("lfr-tunnel-docker status requires a project context.")
            from ldm_core.utils import get_compose_cmd

            compose_base = get_compose_cmd()
            if not compose_base:
                UI.die("Docker Compose not found.")

            if getattr(self.manager, "dry_run", False):
                UI.detail(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(compose_base)} ps lfr-tunnel --format {{{{.Status}}}}"
                )
                UI.detail("lfr-tunnel container is running: Up 1 second")
                return

            res = subprocess.run(
                [*compose_base, "ps", "lfr-tunnel", "--format", "{{.Status}}"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            status_output = res.stdout.strip()
            if status_output:
                UI.detail(f"lfr-tunnel container is running: {status_output}")

                # Query status-json inside the container
                subdomain = (
                    project_meta.get("share_subdomain") or project_id or root.name
                )
                container_name = (
                    project_meta.get("tunnel_container_name")
                    or f"{root.name}-lfr-tunnel"
                )
                json_cmd = [
                    "docker",
                    "exec",
                    container_name,
                    "./lfr-tunnel",
                    "-status-json",
                    "-subdomain",
                    subdomain,
                ]
                try:
                    json_res = subprocess.run(
                        json_cmd, capture_output=True, text=True, check=False
                    )
                    json_out = (json_res.stdout or "").strip()
                    if json_out and json_out.startswith("{") and json_out.endswith("}"):
                        data = json.loads(json_out)
                        data.get("running", False)
                        status = data.get("status", "unknown")
                        conn_state = data.get("connection_state", "disconnected")
                        public_urls = data.get("public_urls", [])

                        UI.heading("Liferay Tunnel Container Status")
                        status_color = UI.GREEN if status == "healthy" else UI.RED
                        UI.raw(
                            f"  ● {UI.WHITE}Container Name: {UI.CYAN}{container_name}{UI.COLOR_OFF}"
                        )
                        UI.raw(
                            f"  ● {UI.WHITE}Status: {status_color}{status}{UI.COLOR_OFF}"
                        )
                        UI.raw(
                            f"  ● {UI.WHITE}Connection State: {UI.WHITE if conn_state == 'connected' else UI.RED}{conn_state}{UI.COLOR_OFF}"
                        )
                        if public_urls:
                            UI.raw(f"  ● {UI.WHITE}Public URLs:{UI.COLOR_OFF}")
                            for url in public_urls:
                                UI.raw(f"    - {UI.GREEN}{url}{UI.COLOR_OFF}")
                        UI.raw("")
                        return
                except Exception:
                    pass

                # Fallback to logs if status-json fails
                logs = subprocess.run(
                    [*compose_base, "logs", "--tail", "10", "lfr-tunnel"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if logs.stdout:
                    print(logs.stdout.strip())
            else:
                UI.error("lfr-tunnel container is not running.")
        else:
            # Default to lfr-tunnel
            bin_path = self._ensure_binary()
            subdomain = (
                project_meta.get("share_subdomain")
                or project_id
                or (root.name if root else None)
            )

            if subdomain:
                cmd = [str(bin_path), "-status-json", "-subdomain", subdomain]
                if getattr(self.manager, "dry_run", False):
                    UI.detail(
                        f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(cmd)}"
                    )
                    print("Tunnel Status: Active")
                    return
                try:
                    res = subprocess.run(
                        cmd, capture_output=True, text=True, check=False
                    )
                    output = (res.stdout or "").strip()
                    if output and output.startswith("{") and output.endswith("}"):
                        data = json.loads(output)
                        data.get("running", False)
                        status = data.get("status", "unknown")
                        conn_state = data.get("connection_state", "disconnected")
                        public_urls = data.get("public_urls", [])
                        inspector_port = data.get("inspector_port", 4040)

                        UI.heading("Liferay Tunnel Status")
                        UI.raw(
                            f"  ● {UI.WHITE}Subdomain: {UI.CYAN}{subdomain}{UI.COLOR_OFF}"
                        )
                        status_color = UI.GREEN if status == "healthy" else UI.RED
                        UI.raw(
                            f"  ● {UI.WHITE}Status: {status_color}{status}{UI.COLOR_OFF}"
                        )
                        UI.raw(
                            f"  ● {UI.WHITE}Connection State: {UI.WHITE if conn_state == 'connected' else UI.RED}{conn_state}{UI.COLOR_OFF}"
                        )
                        if public_urls:
                            UI.raw(f"  ● {UI.WHITE}Public URLs:{UI.COLOR_OFF}")
                            for url in public_urls:
                                UI.raw(f"    - {UI.GREEN}{url}{UI.COLOR_OFF}")
                        UI.raw(
                            f"  ● {UI.WHITE}Inspector Dashboard: {UI.CYAN}http://localhost:{inspector_port}{UI.COLOR_OFF}"
                        )
                        UI.raw("")
                        return
                except Exception:
                    pass

            # Fallback to legacy status check
            cmd = [str(bin_path), "-status"]
            if getattr(self.manager, "dry_run", False):
                UI.detail(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(cmd)}"
                )
                print("Tunnel Status: Active")
                return
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.stdout:
                    print(res.stdout.strip())
                if res.stderr and res.returncode != 0:
                    print(res.stderr.strip())
            except Exception as e:
                UI.die(f"Process invocation error: {e}")

    def cmd_stop(self, project_id=None):  # noqa: C901, PLR0912, PLR0915
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

            if getattr(self.manager, "dry_run", False):
                UI.detail(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(compose_base)} rm -fs ngrok"
                )
                UI.success("Ngrok sharing stopped.")
                return

            UI.detail("Stopping ngrok sidecar container...")
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
        elif provider == "lfr-tunnel-docker":
            if not root:
                UI.die(
                    "Project context not found. Specify -p <project> to stop sharing."
                )

            # Disable sharing in metadata
            project_meta["share"] = "false"
            self.manager.write_meta(root, project_meta)

            from ldm_core.utils import get_compose_cmd

            compose_base = get_compose_cmd()
            if not compose_base:
                UI.die("Docker Compose not found.")

            if getattr(self.manager, "dry_run", False):
                UI.detail(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(compose_base)} rm -fs lfr-tunnel"
                )
                UI.success("Tunnel container stopped and removed.")
                return

            UI.detail("Stopping lfr-tunnel container...")
            res = subprocess.run(
                [*compose_base, "rm", "-fs", "lfr-tunnel"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                UI.success("Tunnel container stopped and removed.")
                self._sync_gui_state("stop", project_id)
            else:
                UI.error(f"Failed to stop tunnel container (Exit {res.returncode})")
                if res.stderr:
                    print(res.stderr.strip())
        else:
            # Default to lfr-tunnel
            bin_path = self._ensure_binary()
            subdomain = (
                project_meta.get("share_subdomain")
                or project_id
                or (root.name if root else None)
            )
            cmd = [str(bin_path), "-stop"]
            if subdomain:
                cmd += ["-subdomain", subdomain]
            if getattr(self.manager, "dry_run", False):
                UI.detail(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(cmd)}"
                )
                UI.success("Tunnel stopped.")
                return
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    UI.success("Tunnel stopped.")
                    self._sync_gui_state("stop", project_id)
                if res.stdout:
                    print(res.stdout.strip())
                if res.stderr and res.returncode != 0:
                    print(res.stderr.strip())
            except Exception as e:
                UI.die(f"Process invocation error: {e}")

    def _sync_gui_state(
        self,
        action: str,
        project_id: str,
        public_url: str | None = None,
        target_port: int = 8080,
    ):
        """Synchronizes tunnel state with the standalone Liferay Tunnel GUI Tray App."""
        import json
        import urllib.error
        import urllib.parse
        import urllib.request

        if getattr(self.manager, "dry_run", False):
            return

        try:
            if action == "start":
                req = urllib.request.Request(
                    "http://127.0.0.1:4141/api/tunnels/sync",
                    data=json.dumps(
                        {
                            "source": "ldm",
                            "project": project_id,
                            "status": "active",
                            "target_port": target_port,
                            "public_url": public_url,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            elif action == "stop":
                safe_project = urllib.parse.quote(project_id or "")
                req = urllib.request.Request(
                    f"http://127.0.0.1:4141/api/tunnels/sync?project={safe_project}",
                    method="DELETE",
                )
            else:
                return

            with urllib.request.urlopen(req, timeout=1.0):  # nosec B310
                pass
        except Exception:
            # Silently ignore if the GUI app is not running
            pass

    def _poll_tunnel_health(self, subdomain, container_name=None, timeout=10):  # noqa: C901, PLR0912, PLR0915
        """Polls the lfr-tunnel status to verify connection health using -status-json."""
        import time

        import requests

        start_time = time.time()
        UI.detail("Verifying tunnel connectivity and subdomain lease...")
        while time.time() - start_time < timeout:
            try:
                if container_name:
                    # Query inside the container using docker exec and status-json
                    cmd = [
                        "docker",
                        "exec",
                        container_name,
                        "./lfr-tunnel",
                        "-status-json",
                        "-subdomain",
                        subdomain,
                    ]
                else:
                    # Query native localhost status-json
                    bin_path = self._ensure_binary()
                    cmd = [
                        str(bin_path),
                        "-status-json",
                        "-subdomain",
                        subdomain,
                    ]

                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                output = (res.stdout or "").strip()
                (res.stderr or "").strip()
                is_legacy = not (
                    output and output.startswith("{") and output.endswith("}")
                )

                if not is_legacy:
                    data = json.loads(output)
                    if (
                        data.get("running") is True
                        and data.get("status") == "healthy"
                        and data.get("connection_state") == "connected"
                    ):
                        return True, None
                # Fallback to legacy checks ONLY if legacy binary was explicitly detected
                elif is_legacy:
                    if container_name:
                        cmd_legacy = [
                            "docker",
                            "exec",
                            container_name,
                            "wget",
                            "-qO-",
                            "http://127.0.0.1:4040/api/healthz",
                        ]
                        sub_res = subprocess.run(
                            cmd_legacy, capture_output=True, text=True, check=False
                        )
                        output_legacy = (sub_res.stdout or "") + (sub_res.stderr or "")
                        if "healthy" in output_legacy:
                            return True, None
                        if "404" in output_legacy:
                            UI.warning(
                                "Legacy tunnel version detected. Skipping active health verification."
                            )
                            return True, None
                    else:
                        req_res = requests.get(
                            "http://localhost:4040/api/healthz", timeout=2
                        )
                        if req_res.status_code == 200:
                            data = req_res.json()
                            if data.get("status") == "healthy":
                                return True, None
                        elif req_res.status_code == 404:
                            UI.warning(
                                "Legacy tunnel version detected. Skipping active health verification."
                            )
                            return True, None
            except Exception:
                # Ignore connection errors during initial boot phase
                pass
            time.sleep(0.5)

        # Diagnosis fallback
        error_reason = "Tunnel connection timeout."
        try:
            if container_name:
                inspect_cmd = [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Running}}",
                    container_name,
                ]
                inspect_res = subprocess.run(
                    inspect_cmd, capture_output=True, text=True, check=False
                )
                is_running = (
                    inspect_res.returncode == 0 and "true" in inspect_res.stdout.lower()
                )

                info_parsed = False
                if is_running:
                    cmd = [
                        "docker",
                        "exec",
                        container_name,
                        "wget",
                        "-qO-",
                        "http://127.0.0.1:4040/api/info",
                    ]
                    sub_res = subprocess.run(
                        cmd, capture_output=True, text=True, check=False
                    )
                    if sub_res.returncode == 0:
                        try:
                            info = json.loads(sub_res.stdout)
                            error_reason = self._diagnose_tunnel_info(info, subdomain)
                            info_parsed = True
                        except Exception:
                            pass

                if not info_parsed:
                    log_cmd = ["docker", "logs", "--tail", "20", container_name]
                    log_res = subprocess.run(
                        log_cmd, capture_output=True, text=True, check=False
                    )
                    logs_str = (log_res.stdout or "") + (log_res.stderr or "")

                    if "unauthorized" in logs_str.lower() or "401" in logs_str:
                        error_reason = "Authentication Failed: Gateway returned 401 Unauthorized. Please verify your LFT_CLIENT_TOKEN."
                    elif (
                        "conflict" in logs_str.lower()
                        or "already taken" in logs_str.lower()
                    ):
                        error_reason = f"Subdomain Conflict: Subdomain '{subdomain}' is already taken by another active tunnel."
                    elif (
                        "failed to register" in logs_str.lower()
                        or "gateway error" in logs_str.lower()
                        or "reservation" in logs_str.lower()
                        or "limit" in logs_str.lower()
                    ):
                        lines = logs_str.splitlines()
                        relevant_lines = []
                        for line in lines:
                            if any(
                                k in line.lower()
                                for k in [
                                    "error",
                                    "client",
                                    "register",
                                    "limit",
                                    "portal",
                                    "👉",
                                    "http",
                                ]
                            ):
                                relevant_lines.append(line.strip())
                        if relevant_lines:
                            error_reason = "Gateway Registration Failed:\n" + "\n".join(
                                relevant_lines
                            )
                        else:
                            error_reason = (
                                f"Gateway Registration Failed:\n{logs_str.strip()}"
                            )
                    elif logs_str.strip():
                        if is_running:
                            error_reason = f"Tunnel connection timeout. Container is running but not responsive. Last logs:\n{logs_str.strip()}"
                        else:
                            error_reason = f"Container terminated unexpectedly. Last logs:\n{logs_str.strip()}"
                    elif is_running:
                        error_reason = "Tunnel connection timeout. Container is running but logs are empty."
                    else:
                        error_reason = (
                            "Container terminated unexpectedly (no logs available)."
                        )
            else:
                # Query inspector_port first using status-json for native tunnel diagnostics fallback
                inspector_port = 4040
                info_parsed = False
                try:
                    bin_path = self._ensure_binary()
                    status_cmd = [
                        str(bin_path),
                        "-status-json",
                        "-subdomain",
                        subdomain,
                    ]
                    status_res = subprocess.run(
                        status_cmd, capture_output=True, text=True, check=False
                    )
                    status_out = (status_res.stdout or "").strip()
                    if (
                        status_out
                        and status_out.startswith("{")
                        and status_out.endswith("}")
                    ):
                        status_data = json.loads(status_out)
                        inspector_port = status_data.get("inspector_port", 4040)
                except Exception:
                    pass

                try:
                    req_res = requests.get(
                        f"http://localhost:{inspector_port}/api/info", timeout=2
                    )
                    if req_res.status_code == 200:
                        info = req_res.json()
                        error_reason = self._diagnose_tunnel_info(info, subdomain)
                        info_parsed = True
                except Exception:
                    pass

                if not info_parsed:
                    log_file = (
                        get_actual_home() / ".lfr-tunnel" / f"client-{subdomain}.log"
                    )
                    if log_file.exists():
                        try:
                            logs_str = log_file.read_text(encoding="utf-8").strip()
                            if logs_str:
                                if (
                                    "unauthorized" in logs_str.lower()
                                    or "401" in logs_str
                                ):
                                    error_reason = "Authentication Failed: Gateway returned 401 Unauthorized. Please verify your LFT_CLIENT_TOKEN."
                                elif (
                                    "conflict" in logs_str.lower()
                                    or "already taken" in logs_str.lower()
                                ):
                                    error_reason = f"Subdomain Conflict: Subdomain '{subdomain}' is already taken by another active tunnel."
                                elif (
                                    "failed to register" in logs_str.lower()
                                    or "gateway error" in logs_str.lower()
                                    or "reservation" in logs_str.lower()
                                    or "limit" in logs_str.lower()
                                ):
                                    lines = logs_str.splitlines()
                                    relevant_lines = []
                                    for line in lines:
                                        if any(
                                            k in line.lower()
                                            for k in [
                                                "error",
                                                "client",
                                                "register",
                                                "limit",
                                                "portal",
                                                "👉",
                                                "http",
                                            ]
                                        ):
                                            relevant_lines.append(line.strip())
                                    if relevant_lines:
                                        error_reason = (
                                            "Gateway Registration Failed:\n"
                                            + "\n".join(relevant_lines)
                                        )
                                    else:
                                        error_reason = (
                                            f"Gateway Registration Failed:\n{logs_str}"
                                        )
                                else:
                                    error_reason = f"Tunnel connection timeout. Last logs:\n{logs_str}"
                        except Exception as le:
                            error_reason = f"Tunnel connection timeout. (Unable to read logs: {le})"
        except Exception:
            pass

        return False, error_reason

    def _diagnose_tunnel_info(self, info, subdomain):
        """Helper to extract clean diagnostic error messages from /api/info data."""
        auth = info.get("auth", {})
        if not auth.get("valid", True):
            msg = auth.get("error_message") or "Invalid gateway authentication token."
            return f"Authentication Failed: {msg}"

        sub = info.get("subdomain", {})
        if sub.get("conflict", False) or not sub.get("leased", True):
            return f"Subdomain Conflict: Subdomain '{subdomain}' is already taken by another active tunnel."

        dest = info.get("destination") or {}
        if not dest.get("responsive", True):
            return f"Downstream Offline: Local target port {dest.get('port', 8080)} is not responsive."

        conn_state = (info.get("connection") or {}).get("state", "disconnected")
        return f"Tunnel Connection Error: Connection state is '{conn_state}'."

    def cmd_inspector(self, project_id=None, port=4040):
        """Launches a temporary port-forwarding container to expose the lfr-tunnel inspector dashboard on the specified host port."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            UI.die("Project context required to find tunnel container.")

        project_meta = self.manager.read_meta(root)
        provider = project_meta.get("share_provider")

        if provider != "lfr-tunnel-docker":
            if provider == "lfr-tunnel":
                UI.die(
                    "Inspector forwarding via docker is only supported for the 'lfr-tunnel-docker' provider. "
                    "For native 'lfr-tunnel', the dashboard is already listening directly on the host (e.g. http://localhost:4040)."
                )
            UI.die(
                f"Inspector forwarding is only supported for 'lfr-tunnel-docker' provider (current: '{provider}')."
            )

        container_name = f"{root.name}-lfr-tunnel"

        # Check if the tunnel container is actually running
        cmd = ["docker", "inspect", "-f", "{{.State.Running}}", container_name]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0 or "true" not in res.stdout.lower():
            UI.die(
                f"Tunnel container '{container_name}' is not running. Please start the tunnel first "
                "(e.g. using 'ldm run --share' or 'ldm share start')."
            )

        proxy_name = f"{root.name}-lfr-tunnel-inspector-proxy"
        # In case a stale one exists, clean it up first
        subprocess.run(
            ["docker", "rm", "-f", proxy_name], capture_output=True, check=False
        )

        proxy_cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            proxy_name,
            "--network",
            "liferay-net",
            "-p",
            f"{port}:4040",
            "alpine/socat",
            "tcp-listen:4040,fork,reuseaddr",
            f"tcp-connect:{container_name}:4040",
        ]

        if getattr(self.manager, "dry_run", False):
            UI.detail(
                f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(proxy_cmd)}"
            )
            return

        UI.detail(f"Forwarding local port {port} to {container_name}:4040...")
        UI.success(f"Inspector dashboard is now accessible at: http://localhost:{port}")
        UI.detail("Press Ctrl+C to stop forwarding.")

        try:
            subprocess.run(proxy_cmd, check=True)
        except KeyboardInterrupt:
            UI.detail("\nStopping port forwarding...")
            subprocess.run(
                ["docker", "stop", proxy_name], capture_output=True, check=False
            )
            UI.success("Port forwarding stopped.")
        except subprocess.CalledProcessError as e:
            UI.error(f"Failed to start port forwarding proxy: {e}")
