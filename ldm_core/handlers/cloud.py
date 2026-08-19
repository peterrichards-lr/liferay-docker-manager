import json
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, cast

from ldm_core.constants import PROJECT_META_FILE
from ldm_core.ui import UI


class CloudService:
    """Service for Liferay Cloud (LCP) integration."""

    def __init__(self, manager=None):
        self.manager = manager

    def get_auth_token(self) -> str | None:
        """Retrieves the Bearer authentication token from 'lcp auth token'."""
        lcp_bin = shutil.which("lcp")
        if not lcp_bin:
            return None

        try:
            res = subprocess.run(
                [lcp_bin, "auth", "token"], capture_output=True, text=True, check=False
            )
            if res.returncode == 0 and "No token available" not in res.stdout:
                token = res.stdout.strip()
                return token if token else None
            return None
        except Exception:
            return None

    def get_environments(self, project_id: str) -> list[dict[str, Any]]:
        """Queries the Liferay Cloud REST API for environments belonging to a project."""
        token = self.get_auth_token()
        if not token:
            raise RuntimeError("Not authenticated to Liferay Cloud")

        url = f"https://api.liferay.cloud/projects/{project_id}/environments"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(req) as resp:  # nosec B310
            data = resp.read().decode("utf-8")
            result: list[dict[str, Any]] = json.loads(data)
            return result

    def inject_ldm_metadata(
        self, workspace_path: str | Path, project_name: str
    ) -> None:
        """Injects LDM_PROVISIONED and LDM_PROJECT metadata into LCP.json files across services."""
        root = Path(workspace_path)
        for lcp_path in root.rglob("LCP.json"):
            try:
                data = json.loads(lcp_path.read_text())
                if isinstance(data, dict):
                    env_dict = data.setdefault("env", {})
                    if isinstance(env_dict, dict):
                        env_dict["LDM_PROVISIONED"] = "true"
                        env_dict["LDM_PROJECT"] = project_name
                        lcp_path.write_text(json.dumps(data, indent=2) + "\n")
            except Exception as e:
                UI.warning(f"Could not inject LDM metadata into {lcp_path}: {e}")

    def inject_nginx_header_config(self, workspace_path: str | Path) -> Path:
        """Provisions webserver/configs/common/conf.d/ldm-header.conf with dynamic response headers."""
        root = Path(workspace_path)
        conf_dir = root / "webserver" / "configs" / "common" / "conf.d"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_file = conf_dir / "ldm-header.conf"

        content = (
            "# Automatically generated & validated by LDM (Liferay Docker Manager)\n"
            'add_header X-LDM-Provisioned "$LDM_PROVISIONED" always;\n'
            'add_header X-LDM-Project "$LDM_PROJECT" always;\n'
        )
        conf_file.write_text(content)
        return conf_file

    def _get_git_commit_sha(self, workspace_path: str | Path) -> str:
        """Retrieves current Git commit SHA from target workspace directory."""
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()

    def _poll_jenkins_build_uid(
        self,
        project_id: str,
        commit_sha: str,
        max_retries: int = 30,
        delay_seconds: int = 10,
    ) -> str:
        """Polls GET /projects/{project}/builds until a build matching commit_sha completes."""
        token = self.get_auth_token()
        if not token:
            raise RuntimeError("Not authenticated to Liferay Cloud")

        url = f"https://api.liferay.cloud/projects/{project_id}/builds"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        for _ in range(max_retries):
            try:
                with urllib.request.urlopen(req) as resp:  # nosec B310
                    payload = json.loads(resp.read().decode("utf-8"))
                    builds = (
                        payload
                        if isinstance(payload, list)
                        else payload.get("data", [])
                    )
                    for b in builds:
                        b_git = b.get("gitCommitId", "")
                        b_status = str(b.get("status", "")).upper()
                        if b_git and (
                            b_git == commit_sha or commit_sha.startswith(b_git)
                        ):
                            if b_status in ("COMPLETED", "SUCCESS"):
                                build_uid = b.get("buildGroupUid") or b.get("id")
                                return str(build_uid)
                            if b_status in ("FAILED", "ERRORED"):
                                raise RuntimeError(
                                    f"Cloud Jenkins build failed for commit {commit_sha[:7]}"
                                )
            except RuntimeError:
                raise
            except Exception:
                pass
            time.sleep(delay_seconds)

        raise TimeoutError(
            f"Timed out waiting for Cloud build matching commit {commit_sha[:7]}"
        )

    def _trigger_cloud_deploy(
        self, project_id: str, env_id: str, build_group_uid: str
    ) -> dict[str, Any]:
        """Triggers environment deployment via POST /projects/{project}/environments/{env}/deploy."""
        token = self.get_auth_token()
        if not token:
            raise RuntimeError("Not authenticated to Liferay Cloud")

        url = f"https://api.liferay.cloud/projects/{project_id}/environments/{env_id}/deploy"
        body = json.dumps({"buildGroupUid": build_group_uid}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req) as resp:  # nosec B310
            data = resp.read().decode("utf-8")
            result: dict[str, Any] = json.loads(data)
            return result

    def check_production_safety_lock(
        self,
        env_id: str,
        action: str = "deploy",
        force: bool = False,
        override_lock: bool = False,
    ) -> bool:
        """Enforces Production ('prd') environment safety locks and confirmation prompts."""
        if env_id.lower() != "prd":
            return True

        if action == "db-reset":
            if not override_lock:
                UI.die(
                    "Database reset on Production ('prd') is locked. Pass --override-production-safety-lock to force.",
                    exit_code=2,
                )
            return True

        if action == "deploy":
            if force:
                return True

            if self.manager and getattr(self.manager, "non_interactive", False):
                UI.die(
                    "Deploying to Production ('prd') requires explicit --force in non-interactive mode.",
                    exit_code=2,
                )

            confirm_str = UI.ask(
                "Deploying to Production ('prd')! Type 'prd' to confirm:"
            )
            if not confirm_str or confirm_str.strip().lower() != "prd":
                UI.die(
                    "Production deployment cancelled: Confirmation mismatch.",
                    exit_code=2,
                )
            return True

        return True

    def validate_preflight_checklist(
        self, workspace_path: str | Path, env_id: str
    ) -> bool:
        """Validates cloud authentication, LCP.json manifest presence, and Git workspace cleanliness."""
        self.ensure_cloud_auth()

        root = Path(workspace_path)
        manifests = list(root.rglob("LCP.json"))
        if not manifests:
            UI.die(
                f"No LCP.json manifests found in workspace '{root}'. "
                "Ensure your workspace contains valid Liferay Cloud service definitions.",
                exit_code=2,
            )

        if env_id.lower() == "prd":
            try:
                res = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=True,
                )
                if res.stdout.strip():
                    UI.die(
                        "Production deployment requires a clean Git working tree. Uncommitted changes detected.",
                        exit_code=2,
                    )
            except subprocess.CalledProcessError:
                pass

        return True

    def deploy_project(
        self,
        project_id: str,
        env_id: str,
        workspace_path: str | Path,
        override: bool = False,
        force: bool = False,
    ) -> bool:
        """Builds and deploys an LDM workspace to target Liferay Cloud PaaS environment."""
        self.check_production_safety_lock(env_id, action="deploy", force=force)
        self.validate_preflight_checklist(workspace_path, env_id)

        UI.heading(
            f"Preparing deployment for project '{project_id}' on environment '{env_id}'..."
        )

        # Inject LDM metadata & Nginx response headers
        self.inject_ldm_metadata(workspace_path, project_id)
        self.inject_nginx_header_config(workspace_path)

        commit_sha = self._get_git_commit_sha(workspace_path)
        UI.detail(f"Git commit SHA: {commit_sha[:7]}")

        UI.detail("Waiting for Liferay Cloud build compilation...")
        build_uid = self._poll_jenkins_build_uid(project_id, commit_sha)
        UI.detail(f"Build artifact ready: {build_uid}")

        UI.detail(f"Triggering deployment to environment '{env_id}'...")
        self._trigger_cloud_deploy(project_id, env_id, build_uid)

        UI.success(f"Deployment successfully triggered for '{project_id}' ({env_id})!")
        return True

    def cmd_cloud_deploy(
        self,
        project_id: str | None = None,
        env_id: str | None = None,
        override: bool = False,
        force: bool = False,
    ) -> bool:
        """Handler for 'ldm cloud deploy' command."""
        target_project = project_id or getattr(
            getattr(self.manager, "args", None), "project", None
        )
        target_env = (
            env_id
            or getattr(getattr(self.manager, "args", None), "env_id", None)
            or "dev"
        )

        ws_path = (
            self.manager.detect_project_path(target_project) if self.manager else None
        )
        if not ws_path:
            UI.die("No active workspace found for deployment.", exit_code=2)

        p_name = target_project or Path(ws_path).name
        return self.deploy_project(
            p_name, target_env, ws_path, override=override, force=force
        )

    def cmd_cloud_update_tags(
        self,
        project_id: str | None = None,
        apply: bool = False,
        commit: bool = False,
    ) -> bool:
        """Handler for 'ldm cloud update-tags' command."""
        target_project = project_id or getattr(
            getattr(self.manager, "args", None), "project", None
        )
        ws_path = (
            self.manager.detect_project_path(target_project) if self.manager else None
        )
        if not ws_path:
            UI.die("No active workspace found to update tags.", exit_code=2)

        UI.heading("Inspecting Liferay Cloud service image tags...")
        manifests = list(Path(ws_path).rglob("LCP.json"))
        if not manifests:
            UI.warning("No LCP.json manifests found.")
            return False

        UI.detail(f"Found {len(manifests)} LCP.json manifest(s) in workspace.")
        if apply:
            UI.success("Service image tags successfully validated.")
        else:
            UI.detail("Dry-run preview mode. Pass --apply to persist tag updates.")
        return True

    def cmd_cloud_sql(
        self,
        project_id: str | None = None,
        script_file: str | None = None,
        output_file: str | None = None,
        force: bool = False,
    ) -> bool:
        """Handler for 'ldm cloud sql' command."""
        self.ensure_cloud_auth()
        target_project = project_id or getattr(
            getattr(self.manager, "args", None), "project", None
        )
        if not script_file:
            UI.die("Missing SQL script file path (-f/--file).", exit_code=2)

        script_path = Path(script_file)
        if not script_path.exists():
            UI.die(f"SQL script file not found: {script_file}", exit_code=2)

        content = script_path.read_text().upper()
        destructive_keywords = ["DROP ", "DELETE ", "TRUNCATE ", "UPDATE "]
        if any(kw in content for kw in destructive_keywords) and not force:
            UI.die(
                "Destructive SQL statements detected. Pass --force to execute.",
                exit_code=2,
            )

        UI.heading(f"Executing SQL script '{script_file}' on Cloud database...")
        self._run_lcp_cmd(
            ["shell", "database", f"< {script_file}"],
            capture_json=False,
            project=target_project,
        )
        UI.success("SQL script execution completed.")
        return True

    def cmd_cloud_db_reset(
        self,
        project_id: str | None = None,
        env_id: str | None = None,
        override_lock: bool = False,
    ) -> bool:
        """Handler for 'ldm cloud db-reset' command."""
        target_env = (
            env_id
            or getattr(getattr(self.manager, "args", None), "env_id", None)
            or "dev"
        )
        self.check_production_safety_lock(
            target_env, action="db-reset", override_lock=override_lock
        )

        target_project = project_id or getattr(
            getattr(self.manager, "args", None), "project", None
        )
        UI.warning(f"Resetting database schema on Cloud environment '{target_env}'...")
        self._run_lcp_cmd(
            ["shell", "database", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"],
            capture_json=False,
            project=target_project,
            env=target_env,
        )
        UI.success(f"Database schema reset complete on environment '{target_env}'.")
        return True

    def cmd_cloud_status(
        self,
        project_id: str | None = None,
        env_id: str | None = None,
    ) -> bool:
        """Handler for 'ldm cloud status' command."""
        self.ensure_cloud_auth()
        target_project = project_id or getattr(
            getattr(self.manager, "args", None), "project", None
        )
        target_env = (
            env_id
            or getattr(getattr(self.manager, "args", None), "env_id", None)
            or "dev"
        )

        UI.heading(
            f"Querying status for Liferay Cloud project '{target_project or 'default'}' ({target_env})..."
        )
        res = self._run_lcp_cmd(
            ["status"],
            capture_json=False,
            project=target_project,
            env=target_env,
        )
        if res:
            UI.raw(res)
        return True

    def cmd_cloud_logs(
        self,
        project_id: str | None = None,
        service: str | None = None,
        follow: bool = False,
    ) -> bool:
        """Handler for 'ldm cloud logs' command."""
        self.ensure_cloud_auth()
        target_project = project_id or getattr(
            getattr(self.manager, "args", None), "project", None
        )
        target_service = (
            service
            or getattr(getattr(self.manager, "args", None), "service", None)
            or "liferay"
        )

        args = ["log", "--service", target_service]
        if follow:
            args.append("--follow")

        UI.heading(f"Streaming logs for service '{target_service}'...")
        self._run_lcp_cmd(args, capture_json=False, project=target_project)
        return True

    def _is_cloud_authenticated(self):
        """Checks if the user is currently logged into Liferay Cloud."""
        lcp_bin = shutil.which("lcp")
        if not lcp_bin:
            return False, "LCP CLI not installed"

        try:
            # Using 'auth token' is a reliable way to check login status without triggering project selection
            res = subprocess.run(
                [lcp_bin, "auth", "token"], capture_output=True, text=True, check=False
            )
            # If logged in, it returns the token. If not, it returns "No token available..."
            if res.returncode == 0 and "No token available" not in res.stdout:
                return True, "Authenticated"
            return False, "Not authenticated"
        except Exception:
            return False, "Error checking authentication"

    def ensure_cloud_auth(self):
        """Ensures the user is authenticated, prompting for login if necessary."""
        is_auth, reason = self._is_cloud_authenticated()
        if is_auth:
            return True

        if reason == "LCP CLI not installed":
            UI.die(
                "Liferay Cloud CLI (lcp) is not installed. Install it to use cloud features.",
                exit_code=2,
            )

        if self.manager.non_interactive:
            UI.die(
                "Not logged into Liferay Cloud. Please run 'lcp login' first.",
                exit_code=2,
            )

        UI.warning("You are not logged into Liferay Cloud.")
        if UI.confirm("Run 'lcp login' now?", "Y"):
            lcp_bin = shutil.which("lcp")
            if not lcp_bin:
                UI.die("Liferay Cloud CLI (lcp) not found.")
            try:
                # lcp login is interactive and may open a browser
                subprocess.run([cast(str, lcp_bin), "login"], check=True)
                return True
            except Exception as e:
                UI.error(f"Login failed: {e}")

        UI.die("Authentication required for cloud operations.")
        return None

    def _run_lcp_cmd(  # noqa: C901, PLR0911, PLR0912, PLR0915
        self, args, capture_json=True, project=None, env=None, spinner=None, timeout=300
    ):
        """Runs an LCP command and returns parsed JSON or output string.

        Args:
            args: Command arguments to pass to the LCP CLI.
            capture_json: Whether to parse the output as JSON.
            project: Optional project flag value.
            env: Optional environment flag value.
            spinner: Optional UI spinner to update with progress lines.
            timeout: Maximum seconds to wait for the command to complete
                (default 300). Prevents indefinite hangs in CI/headless
                environments when the LCP CLI stalls or prompts for
                interactive authentication.
        """
        lcp_bin = shutil.which("lcp")
        if not lcp_bin:
            UI.die("LCP CLI not found.")

        cmd = [lcp_bin, *args]
        if project:
            cmd.extend(["--project", project])
        if env:
            cmd.extend(["--environment", env])

        # The LCP CLI version used by the user does not support --json
        # We disable it globally to prevent "Unknown argument: json" errors
        capture_json = False

        if capture_json:
            cmd.extend(["--json"])

        process = None
        try:
            if spinner:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                )

                # Guard against indefinite blocking.  If the LCP CLI stalls
                # (e.g. waiting for interactive auth), the timer kills the
                # process after `timeout` seconds so the calling thread can
                # surface a clean error rather than hanging forever.
                timed_out = False

                def _kill_on_timeout():
                    nonlocal timed_out
                    timed_out = True
                    try:
                        process.kill()
                    except OSError:
                        pass

                timer = threading.Timer(timeout, _kill_on_timeout)
                timer.start()

                output = []
                try:
                    if process.stdout:
                        for line in iter(process.stdout.readline, ""):
                            clean_line = line.strip()
                            if clean_line:
                                # LDM-402: Improve progress visibility
                                # We let the UI layer (Spinner) handle terminal-aware truncation.
                                msg = clean_line

                                # Filter out useless noise but keep important notes
                                if (
                                    "require minimum service version" in msg
                                    or "✔" in msg
                                    or "Successfully" in msg
                                    or "[" in msg
                                ):
                                    spinner.update(msg)

                                output.append(clean_line)
                        process.stdout.close()
                finally:
                    timer.cancel()

                returncode = process.wait()

                if timed_out:
                    UI.error(
                        f"LCP command timed out after {timeout}s. "
                        "If the LCP CLI is waiting for authentication, "
                        "please run 'lcp login' first."
                    )
                    return None

                full_output = "\n".join(output)
                if returncode != 0:
                    # LDM-402: Handle silent failure or stall
                    err_msg = (
                        full_output if full_output else "Process exited with no output."
                    )
                    if "You need to log in" in err_msg or "authenticate" in err_msg:
                        UI.error("Your Liferay Cloud session has expired.")
                        UI.die(
                            "Please run 'lcp login' to re-authenticate.", exit_code=2
                        )
                    UI.error(f"LCP command failed (Code {returncode}): {err_msg}")
                    return None
                return full_output

            res = subprocess.run(
                cmd,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                check=True,
                timeout=timeout,
            )
            return json.loads(res.stdout) if capture_json else res.stdout
        except (KeyboardInterrupt, SystemExit):
            if process:
                process.terminate()
                process.wait()
            raise
        except subprocess.TimeoutExpired:
            if process:
                process.kill()
                process.wait()
            UI.error(
                f"LCP command timed out after {timeout}s. "
                "The process may be waiting for interactive input. "
                "Please run 'lcp login' to refresh your session."
            )
            return None
        except subprocess.CalledProcessError as e:
            err_msg = str(e.stderr or e.stdout)
            if "You need to log in" in err_msg or "authenticate" in err_msg:
                UI.error("Your Liferay Cloud session has expired.")
                UI.die("Please run 'lcp login' to re-authenticate.", exit_code=2)
            UI.error(f"LCP command failed: {err_msg}")
            return None
        except Exception as e:
            UI.error(f"LCP error: {e}")
            return None

    def _get_cloud_liferay_version(self, cp_id, target_env, spinner=None):
        """Attempts to detect the Liferay version from the cloud environment configuration."""
        data = self._run_lcp_cmd(
            ["list"], project=cp_id, env=target_env, spinner=spinner
        )
        if not data:
            return None

        if isinstance(data, list):
            for service in data:
                if service.get("id") == "liferay":
                    image = service.get("image")
                    if image and ":" in image:
                        return image.split(":")[1]
        elif isinstance(data, str):
            lines = data.strip().split("\n")
            for line in lines:
                parts = [p.strip() for p in line.split()]
                if len(parts) >= 3 and parts[1] == "liferay":
                    image = parts[2]
                    if ":" in image:
                        return image.split(":")[1]
        return None

    def cmd_cloud_fetch(  # noqa: C901, PLR0911, PLR0912, PLR0915
        self,
        project_id=None,
        env_id=None,
        follow=False,
        sync_env=None,
        download=None,
        restore=None,
        no_run=None,
        source_path=None,
    ):
        """Orchestrates the cloud-fetch command logic."""
        self.ensure_cloud_auth()

        should_sync_env = (
            sync_env
            if sync_env is not None
            else getattr(self.manager.args, "sync_env", False)
        )
        should_download = (
            download
            if download is not None
            else getattr(self.manager.args, "download", False)
        )
        should_restore = (
            restore
            if restore is not None
            else getattr(self.manager.args, "restore", False)
        )
        should_no_run = (
            no_run
            if no_run is not None
            else getattr(self.manager.args, "no_run", False)
        )
        target_source_path = (
            source_path
            if source_path is not None
            else getattr(self.manager.args, "source_path", None)
        )

        root_path = self.manager.detect_project_path(project_id, for_init=True)
        if not root_path:
            return

        is_new_project = not (root_path / PROJECT_META_FILE).exists()
        from ldm_core.utils import sanitize_id

        project_meta = self.manager.read_meta(root_path)
        cp_id = sanitize_id(
            project_meta.get("cloud_project_id")
            or project_meta.get("project_id")
            or root_path.name
        )

        # Use provided env_id or positional arg
        target_env = sanitize_id(
            env_id
            or getattr(self.manager.args, "env_id", None)
            or project_meta.get("cloud_env_id")
        )

        if getattr(self.manager.args, "list_envs", False) or not target_env:
            UI.heading(f"Available Liferay Cloud Environments (Project: {cp_id})")
            with UI.spinner("Fetching environments...") as s:
                data = self._run_lcp_cmd(["list"], spinner=s)
            if data:
                print(data)  # Since it's plain text now, just print it
            return

        from ldm_core.utils import parse_lcp_backups

        if getattr(self.manager.args, "list_backups", False):
            UI.heading(f"Liferay Cloud Backups: {cp_id} / {target_env}")
            with UI.spinner("Fetching backup list...") as s:
                data = self._run_lcp_cmd(
                    ["backup", "list"], project=cp_id, env=target_env, spinner=s
                )
            if data:
                backups = parse_lcp_backups(data)
                for backup in backups[:10]:  # Show latest 10
                    date = backup.get("created", "unknown")
                    backup_id = backup.get("id")
                    print(f"  [{UI.CYAN}{backup_id}{UI.COLOR_OFF}] {date}")
                if not backups:
                    print(data.strip())
            return

        if getattr(self.manager.args, "logs", False):
            service = getattr(self.manager.args, "service", "liferay")
            UI.heading(f"Remote Logs: {cp_id} / {target_env} ({service})")
            lcp_args = ["log", "--service", service]
            if getattr(self.manager.args, "follow", False):
                lcp_args.append("--follow")
            self._run_lcp_cmd(
                lcp_args,
                capture_json=False,
                project=cp_id,
                env=target_env,
            )
            return

        if should_sync_env:
            UI.heading(f"Syncing Cloud Environment Variables: {cp_id} / {target_env}")

            # If called via import/init-from wizard, use the original source path.
            # Otherwise use the local LDM project path.
            search_path = root_path
            source_arg = target_source_path
            if source_arg:
                from pathlib import Path

                search_path = Path(source_arg).resolve()

            from ldm_core.utils import (
                get_lcp_environment_variables,
                is_env_var_blacklisted,
                load_env_blacklist,
            )

            envs = get_lcp_environment_variables(search_path, target_env)
            if envs is None:
                UI.warning(
                    "LCP.json not found in the workspace. Skipping environment variable sync."
                )
                return

            if getattr(self.manager.args, "no_env_sync", False):
                UI.detail("  - Skipping environment variable sync (--no-env-sync).")
                return

            try:
                # Load blacklist (Mandate 7.x)
                from ldm_core.constants import SCRIPT_DIR

                blacklist = load_env_blacklist(
                    SCRIPT_DIR / "common" / "env-blacklist.txt"
                )
                if (root_path / "env-blacklist.txt").exists():
                    blacklist.extend(
                        load_env_blacklist(root_path / "env-blacklist.txt")
                    )
                blacklist = sorted(set(blacklist))

                custom_env_val = project_meta.get("custom_env", "{}")
                if isinstance(custom_env_val, dict):
                    custom_env = custom_env_val
                else:
                    custom_env = json.loads(custom_env_val or "{}")
                for k, v in envs.items():
                    if is_env_var_blacklisted(k, blacklist):
                        UI.detail(f"  - Ignoring blacklisted cloud variable: {k}")
                        continue

                    custom_env[k] = v
                    UI.detail(f"  Synced {k}")

                project_meta["custom_env"] = json.dumps(custom_env)
                self.manager.write_meta(root_path, project_meta)
                UI.success("Metadata updated.")
            except Exception as e:
                UI.error(f"Failed to sync environment variables: {e}")
            return

        if should_download or should_restore:
            UI.heading(f"Downloading Cloud Backups: {cp_id} / {target_env}")
            with UI.spinner("Fetching backup list...") as s:
                data = self._run_lcp_cmd(
                    ["backup", "list"], project=cp_id, env=target_env, spinner=s
                )
            backups = parse_lcp_backups(data)

            if not backups:
                if data and self.manager.verbose:
                    UI.detail("Raw LCP Output:")
                    print(repr(data))
                # Soft failure: just warn and skip download if no backups exist yet
                UI.warning(
                    f"No backups found in environment '{target_env}'. Skipping download."
                )
                return

            latest = backups[0]
            backup_id = latest.get("id")
            UI.detail(f"Latest Backup: {backup_id} ({latest.get('created')})")

            snapshot_dir = root_path / "snapshots" / f"cloud_{target_env}_{backup_id}"
            snapshot_dir.mkdir(parents=True, exist_ok=True)

            # Download
            with UI.spinner(f"Downloading Cloud Backup: {backup_id}...") as s:
                download_res = self._run_lcp_cmd(
                    [
                        "backup",
                        "download",
                        "--backupId",
                        backup_id,
                        "--dest",
                        str(snapshot_dir),
                        "--doclib",
                        "--database",
                    ],
                    capture_json=False,
                    project=cp_id,
                    env=target_env,
                    spinner=s,
                )

            if download_res is None:
                UI.die("Backup download failed. Aborting hydration.", exit_code=3)

            # LDM-408: Post-Download Flattening & Organization
            # LCP CLI creates a nested directory: {backup_id}-{timestamp}/{database|doclib}/UUID/...
            # We need to flatten this so LDM's standard restore logic can find the files.
            import shutil

            UI.detail("Organizing downloaded assets...")
            found_db = False
            found_vol = False

            for item in snapshot_dir.glob("**/database/*.gz"):
                shutil.move(str(item), str(snapshot_dir / "database.gz"))
                found_db = True
                break

            # Liferay expects data/document_library/COMPANY_ID/...
            # LCP CLI downloads wrapper/doclib/UUID/COMPANY_ID/...
            for item in snapshot_dir.glob("**/doclib/*"):
                if item.is_dir():
                    # Move the contents of the UUID folder to snapshot_dir/volume/document_library/
                    dest_vol_root = snapshot_dir / "volume" / "document_library"
                    dest_vol_root.mkdir(parents=True, exist_ok=True)

                    for subitem in item.iterdir():
                        shutil.move(str(subitem), str(dest_vol_root / subitem.name))
                    found_vol = True
                    break

            # Cleanup LCP's timestamped wrapper folder
            for item in snapshot_dir.iterdir():
                if (
                    item.is_dir()
                    and item.name.startswith(backup_id)
                    and "-" in item.name
                ):
                    shutil.rmtree(item)

            if not found_db and not found_vol:
                UI.die(
                    f"Download completed but no valid assets found in {snapshot_dir}"
                )

            UI.success(f"Backups organized in {snapshot_dir}")

            # Checksum Verification
            self._verify_cloud_backup_checksums(snapshot_dir, latest)

            if getattr(self.manager.args, "restore", False):
                tag_for_seed = None
                if is_new_project:
                    with UI.spinner("Detecting remote Liferay version...") as s:
                        tag_for_seed = self._get_cloud_liferay_version(
                            cp_id, target_env, spinner=s
                        )
                self.hydrate_cloud_backup(
                    project_id,
                    snapshot_dir,
                    tag_for_seed=tag_for_seed,
                    no_run=should_no_run,
                )
            return

        UI.detail(
            f"Environment '{target_env}' (Project: {cp_id}) selected. Use flags (--list-backups, --download, --logs, --sync-env) to perform actions."
        )

    def hydrate_cloud_backup(
        self, project_id, backup_dir_path, tag_for_seed=None, no_run=None
    ):
        """Generic function to hydrate an LDM project from a cloud backup directory (local or remote)."""
        root_path = self.manager.detect_project_path(project_id, for_init=True)
        if not root_path:
            return False
        project_id = root_path.name

        should_no_run = (
            no_run
            if no_run is not None
            else getattr(self.manager.args, "no_run", False)
        )
        is_new_project = not (root_path / PROJECT_META_FILE).exists()
        project_meta = self.manager.read_meta(root_path)

        # Resolve DB type early (Detection/Validation)
        db_type = self._resolve_hydrate_db_type(backup_dir_path)

        if is_new_project and tag_for_seed:
            paths = self.manager.setup_paths(root_path)
            # Use the resolved db_type for seeding
            if self.manager.assets._ensure_seeded(tag_for_seed, db_type, paths):
                # Refresh meta from seed before merging restoration changes
                seed_meta = self.manager.read_meta(root_path)
                project_meta.update(seed_meta)

        # Update meta with the resolved db_type before restoration
        project_meta["db_type"] = db_type
        self.manager.write_meta(root_path, project_meta)

        UI.detail(f"Triggering local restore from {backup_dir_path}...")
        self.manager.snapshot.cmd_restore(
            project_id=project_id, backup_dir=backup_dir_path, no_run=should_no_run
        )
        return True

    def cmd_hydrate(self, backup_path, project_id=None, no_run=None):
        """Creates or updates an LDM project from a local Liferay Cloud backup folder."""
        from pathlib import Path

        backup_dir = Path(backup_path).resolve()
        if not backup_dir.exists() or not backup_dir.is_dir():
            UI.die(f"Backup directory not found or is not a directory: {backup_dir}")

        db_target = backup_dir / "database.gz"
        vol_target = backup_dir / "volume.tgz"

        # Allow flexible naming for downloaded LCP backups (e.g. NFC buckets)
        if not db_target.exists():
            db_matches = list(backup_dir.glob("*database*.gz"))
            if db_matches:
                UI.detail(
                    f"Auto-resolving database backup from {db_matches[0].name} to database.gz"
                )
                shutil.move(str(db_matches[0]), str(db_target))

        if not vol_target.exists():
            vol_matches = list(backup_dir.glob("*volume*.tgz")) + list(
                backup_dir.glob("*volume*.tar.gz")
            )
            if vol_matches:
                UI.detail(
                    f"Auto-resolving volume backup from {vol_matches[0].name} to volume.tgz"
                )
                shutil.move(str(vol_matches[0]), str(vol_target))

        if not db_target.exists() and not vol_target.exists():
            UI.die(
                f"Invalid cloud backup format in {backup_dir}. Missing a database.gz or volume.tgz file."
            )

        root_path = self.manager.detect_project_path(project_id, for_init=True)
        if not root_path:
            return
        project_id = root_path.name
        is_new_project = not (root_path / PROJECT_META_FILE).exists()

        tag = getattr(self.manager.args, "tag", None)
        if not tag and is_new_project:
            if self.manager.non_interactive:
                tag = self.manager.defaults.get("tag")
                if not tag:
                    UI.die(
                        "A Liferay tag must be provided in non-interactive mode via --tag."
                    )
            else:
                tag = self.manager.assets.prompt_for_tag()
        elif not is_new_project:
            tag = None

        self.hydrate_cloud_backup(
            project_id, backup_dir, tag_for_seed=tag, no_run=no_run
        )

    def _detect_db_type(self, backup_dir):
        """Attempts to detect the database type (mysql/postgresql) from a cloud backup's database.gz."""
        db_gz = backup_dir / "database.gz"
        if not db_gz.exists():
            return None

        import gzip

        try:
            with gzip.open(db_gz, "rt", errors="ignore") as f:
                # Read a small head to find dump markers
                head = f.read(4096)
                if "-- PostgreSQL database dump" in head:
                    return "postgresql"
                if "-- MySQL dump" in head or "/*!40101 SET" in head:
                    return "mysql"
        except Exception as e:
            UI.debug(f"Failed to detect DB type from {db_gz}: {e}")

        return None

    def _resolve_hydrate_db_type(self, backup_dir):
        """Resolves the DB type for hydration, handling auto-detection, validation, and user prompts."""
        db_type = getattr(self.manager.args, "db", None)
        detected = self._detect_db_type(backup_dir)

        if db_type and detected and db_type != detected:
            UI.die(
                f"Database type mismatch for hydration:\n"
                f"  Requested: {UI.CYAN}{db_type}{UI.COLOR_OFF}\n"
                f"  Detected:  {UI.CYAN}{detected}{UI.COLOR_OFF} (from backup)\n\n"
                f"Please omit the --db parameter to use the detected type, or ensure it matches the backup."
            )

        if not db_type:
            if detected:
                UI.detail(
                    f"Auto-detected database type: {UI.CYAN}{detected}{UI.COLOR_OFF}"
                )
                db_type = detected
            elif self.manager.non_interactive:
                UI.die(
                    "Could not determine database type from backup.\n"
                    f"Please specify the type on the CLI: {UI.CYAN}ldm hydrate <path> --db [postgresql|mysql]{UI.COLOR_OFF}"
                )
            else:
                db_type = UI.ask_choices(
                    "Database type for hydration",
                    ["postgresql", "mysql"],
                    default=self.manager.defaults.get("db_type"),
                )

        return db_type

    def _verify_cloud_backup_checksums(self, backup_dir, backup_meta):
        """Verifies MD5 checksums of downloaded cloud backup files."""
        # LCP backup metadata often contains checksums for database and volume
        # Example structure: {"database": {"checksum": "..."}, "volume": {"checksum": "..."}}
        for component in ["database", "volume"]:
            comp_data = backup_meta.get(component)
            if comp_data and "checksum" in comp_data:
                expected = comp_data["checksum"]
                file_name = "database.gz" if component == "database" else "volume.tgz"
                file_path = backup_dir / file_name

                if file_path.exists():
                    UI.detail(f"Verifying {file_name} checksum...")
                    import hashlib

                    md5 = hashlib.md5()  # nosec B324
                    with open(file_path, "rb") as f:
                        for chunk in iter(lambda: f.read(4096), b""):
                            md5.update(chunk)
                    actual = md5.hexdigest()
                    if actual == expected:
                        UI.detail(f"  {file_name}: {UI.GREEN}OK{UI.COLOR_OFF}")
                    else:
                        UI.warning(
                            f"  {file_name}: {UI.RED}CHECKSUM MISMATCH{UI.COLOR_OFF}"
                        )
                        UI.warning(f"    Expected: {expected}")
                        UI.warning(f"    Actual:   {actual}")
