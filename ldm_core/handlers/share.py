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

        if getattr(self.manager, "dry_run", False):
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

    def resolve_public_tunnel_url(self, subdomain):
        """Resolves the public tunnel URL using LFT_SERVER_URL or defaulting to lfr-demo.online."""
        server_url = os.environ.get("LFT_SERVER_URL")
        domain = "lfr-demo.online"
        if server_url:
            from urllib.parse import urlparse

            parsed = urlparse(server_url)
            netloc = parsed.netloc or parsed.path
            host = netloc.split(":")[0]
            if host.startswith("tunnel."):
                domain = host[7:]
            else:
                domain = host
        return f"https://{subdomain}.{domain}"

    def cmd_start(
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
            env["LFR_TUNNEL_TOKEN"] = token

            if getattr(self.manager, "dry_run", False):
                UI.info(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(cmd)}"
                )
                UI.success("Tunnel started in the background.")
                public_url = self.resolve_public_tunnel_url(subdomain)
                UI.success(
                    f"🌍 Public Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
                )
                return

            UI.info("Starting lfr-tunnel in the background...")
            try:
                res = subprocess.run(
                    cmd, env=env, capture_output=True, text=True, check=False
                )
                if res.returncode == 0:
                    success, err_msg = self._poll_tunnel_health(subdomain)
                    if success:
                        UI.success("Tunnel started in the background.")
                        public_url = self.resolve_public_tunnel_url(subdomain)
                        UI.success(
                            f"🌍 Public Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
                        )
                        if res.stdout:
                            print(res.stdout.strip())
                    else:
                        UI.error(f"Tunnel healthcheck failed: {err_msg}")
                        # Clean up background process
                        try:
                            subprocess.run(
                                [str(bin_path), "-stop"],
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                        except Exception:
                            pass
                        UI.die("Unable to establish tunnel connection.")
                else:
                    UI.error(f"Failed to start tunnel (Exit {res.returncode})")
                    if res.stderr:
                        print(res.stderr.strip())
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
                project_meta["port"] = int(ports)
            if image:
                project_meta["share_image"] = image
            project_meta["share_inspector"] = "true" if inspector else "false"
            base_container = project_meta.get("container_name") or project_id
            project_meta["tunnel_container_name"] = f"{base_container}-lfr-tunnel"

            self.manager.write_meta(root, project_meta)

            # Regenerate compose file and boot lfr-tunnel service
            paths = self.manager.setup_paths(root)
            UI.info("Regenerating stack configuration with lfr-tunnel sidecar...")
            self.manager.runtime.sync_stack(
                paths, project_meta, no_up=True, show_summary=False
            )

            from ldm_core.utils import get_compose_cmd

            compose_base = get_compose_cmd()
            if not compose_base:
                UI.die("Docker Compose not found.")

            if getattr(self.manager, "dry_run", False):
                UI.info(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(compose_base)} up -d lfr-tunnel"
                )
                UI.success("Tunnel container started in the background.")
                public_url = self.resolve_public_tunnel_url(subdomain)
                UI.success(
                    f"🌍 Public Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
                )
                return

            UI.info("Starting lfr-tunnel container...")
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
                    public_url = self.resolve_public_tunnel_url(subdomain)
                    UI.success(
                        f"🌍 Public Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
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

            if getattr(self.manager, "dry_run", False):
                UI.info(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(compose_base)} up -d ngrok"
                )
                UI.success("Ngrok container started.")
                return

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
        elif provider == "lfr-tunnel-docker":
            if not root:
                UI.die("lfr-tunnel-docker status requires a project context.")
            from ldm_core.utils import get_compose_cmd

            compose_base = get_compose_cmd()
            if not compose_base:
                UI.die("Docker Compose not found.")

            if getattr(self.manager, "dry_run", False):
                UI.info(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(compose_base)} ps lfr-tunnel --format {{{{.Status}}}}"
                )
                UI.info("lfr-tunnel container is running: Up 1 second")
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
                UI.info(f"lfr-tunnel container is running: {status_output}")
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
            cmd = [str(bin_path), "-status"]
            if getattr(self.manager, "dry_run", False):
                UI.info(
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

            if getattr(self.manager, "dry_run", False):
                UI.info(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(compose_base)} rm -fs ngrok"
                )
                UI.success("Ngrok sharing stopped.")
                return

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
                UI.info(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(compose_base)} rm -fs lfr-tunnel"
                )
                UI.success("Tunnel container stopped and removed.")
                return

            UI.info("Stopping lfr-tunnel container...")
            res = subprocess.run(
                [*compose_base, "rm", "-fs", "lfr-tunnel"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                UI.success("Tunnel container stopped and removed.")
            else:
                UI.error(f"Failed to stop tunnel container (Exit {res.returncode})")
                if res.stderr:
                    print(res.stderr.strip())
        else:
            # Default to lfr-tunnel
            bin_path = self._ensure_binary()
            cmd = [str(bin_path), "-stop"]
            if getattr(self.manager, "dry_run", False):
                UI.info(
                    f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(cmd)}"
                )
                UI.success("Tunnel stopped.")
                return
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.stdout:
                    print(res.stdout.strip())
                if res.stderr and res.returncode != 0:
                    print(res.stderr.strip())
            except Exception as e:
                UI.die(f"Process invocation error: {e}")

    def _poll_tunnel_health(self, subdomain, container_name=None, timeout=10):
        """Polls the lfr-tunnel client inspector /api/healthz endpoint to verify connection health."""
        import time

        import requests

        start_time = time.time()
        url = "http://localhost:4040/api/healthz"

        UI.info("Verifying tunnel connectivity and subdomain lease...")
        while time.time() - start_time < timeout:
            try:
                if container_name:
                    # Query inside the container using docker exec and wget
                    cmd = [
                        "docker",
                        "exec",
                        container_name,
                        "wget",
                        "-S",
                        "--spider",
                        "http://localhost:4040/api/healthz",
                    ]
                    sub_res = subprocess.run(
                        cmd, capture_output=True, text=True, check=False
                    )
                    # wget -S headers go to stderr
                    output = sub_res.stderr or ""
                    if "200 OK" in output:
                        return True, None
                    if "404 Not Found" in output:
                        UI.warning(
                            "Legacy tunnel version detected. Skipping active health verification."
                        )
                        return True, None
                else:
                    # Query native localhost inspector API
                    req_res = requests.get(url, timeout=2)
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
                if (
                    inspect_res.returncode == 0
                    and "false" in inspect_res.stdout.lower()
                ):
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
                    ):
                        for line in logs_str.splitlines():
                            if (
                                "gateway error" in line.lower()
                                or "failed to register" in line.lower()
                                or "error" in line.lower()
                            ):
                                error_reason = (
                                    f"Gateway Registration Failed: {line.strip()}"
                                )
                                break
                        else:
                            error_reason = (
                                "Gateway Registration Failed. Please check the logs."
                            )
                    else:
                        error_reason = f"Container terminated unexpectedly. Last logs:\n{logs_str.strip()}"
                else:
                    cmd = [
                        "docker",
                        "exec",
                        container_name,
                        "wget",
                        "-qO-",
                        "http://localhost:4040/api/info",
                    ]
                    sub_res = subprocess.run(
                        cmd, capture_output=True, text=True, check=False
                    )
                    if sub_res.returncode == 0:
                        info = json.loads(sub_res.stdout)
                        error_reason = self._diagnose_tunnel_info(info, subdomain)
            else:
                req_res = requests.get("http://localhost:4040/api/info", timeout=2)
                if req_res.status_code == 200:
                    info = req_res.json()
                    error_reason = self._diagnose_tunnel_info(info, subdomain)
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

        dest = info.get("destination", {})
        if not dest.get("responsive", True):
            return f"Downstream Offline: Local target port {dest.get('port', 8080)} is not responsive."

        conn_state = info.get("connection", {}).get("state", "disconnected")
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
            UI.info(
                f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {' '.join(proxy_cmd)}"
            )
            return

        UI.info(f"Forwarding local port {port} to {container_name}:4040...")
        UI.success(f"Inspector dashboard is now accessible at: http://localhost:{port}")
        UI.info("Press Ctrl+C to stop forwarding.")

        try:
            subprocess.run(proxy_cmd, check=True)
        except KeyboardInterrupt:
            UI.info("\nStopping port forwarding...")
            subprocess.run(
                ["docker", "stop", proxy_name], capture_output=True, check=False
            )
            UI.success("Port forwarding stopped.")
        except subprocess.CalledProcessError as e:
            UI.error(f"Failed to start port forwarding proxy: {e}")
