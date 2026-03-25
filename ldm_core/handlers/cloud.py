import json
import shutil
import subprocess
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE


class CloudHandler:
    """Mixin for Liferay Cloud (LCP) integration."""

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
                "Liferay Cloud CLI (lcp) is not installed. Install it to use cloud features."
            )

        if self.non_interactive:
            UI.die("Not logged into Liferay Cloud. Please run 'lcp login' first.")

        UI.warning("You are not logged into Liferay Cloud.")
        if UI.ask("Run 'lcp login' now?", "Y").upper() == "Y":
            lcp_bin = shutil.which("lcp")
            try:
                # lcp login is interactive and may open a browser
                subprocess.run([lcp_bin, "login"], check=True)
                return True
            except Exception as e:
                UI.error(f"Login failed: {e}")

        UI.die("Authentication required for cloud operations.")

    def _run_lcp_cmd(self, args, capture_json=True, env=None):
        """Runs an LCP command and returns parsed JSON or output string."""
        lcp_bin = shutil.which("lcp")
        if not lcp_bin:
            UI.die("LCP CLI not found.")

        cmd = [lcp_bin] + args
        if capture_json:
            cmd.extend(["--json"])

        try:
            if capture_json:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return json.loads(res.stdout)
            else:
                # For streaming or direct output
                subprocess.run(cmd, check=True)
                return True
        except subprocess.CalledProcessError as e:
            UI.error(f"LCP command failed: {e.stderr}")
            return None
        except Exception as e:
            UI.error(f"LCP error: {e}")
            return None

    def cmd_cloud_fetch(self, project_id=None, env_id=None):
        """Orchestrates the cloud-fetch command logic."""
        self.ensure_cloud_auth()

        root_path = self.detect_project_path(project_id)
        if not root_path:
            return

        project_meta = self.read_meta(root_path / PROJECT_META_FILE)

        # Use provided env_id or positional arg
        target_env = env_id or getattr(self.args, "env_id", None)

        if getattr(self.args, "list_envs", False) or not target_env:
            UI.heading("Available Liferay Cloud Environments")
            data = self._run_lcp_cmd(["list"])
            if data:
                # If we have a lot of data, we just print it all
                # Some LCP CLI versions return a list of projects, others a flat list of project-envs
                for item in data:
                    p_name = item.get("name") or item.get("project", "Unknown")
                    p_id = item.get("id") or item.get("projectId")

                    if "environments" in item:
                        # Hierarchical format
                        UI.info(f"Project: {p_name} ({p_id})")
                        for env in item.get("environments", []):
                            print(
                                f"  - {UI.CYAN}{env.get('id')}{UI.COLOR_OFF} ({env.get('name')})"
                            )
                    else:
                        # Flat format (Project-Env as a single line)
                        # Filter out infra if not explicitly requested? No, show all for now.
                        status = item.get("status", "")
                        status_color = (
                            UI.GREEN if "running" in status.lower() else UI.WHITE
                        )
                        print(
                            f"  - {UI.CYAN}{p_id:<30}{UI.COLOR_OFF} [{status_color}{status}{UI.COLOR_OFF}]"
                        )
            return

        if getattr(self.args, "list_backups", False):
            UI.heading(f"Liferay Cloud Backups: {target_env}")
            data = self._run_lcp_cmd(["backup", "list", "--environment", target_env])
            if data:
                for backup in data[:10]:  # Show latest 10
                    date = backup.get("created", "unknown")
                    backup_id = backup.get("id")
                    print(f"  [{UI.CYAN}{backup_id}{UI.COLOR_OFF}] {date}")
            return

        if getattr(self.args, "logs", False):
            service = getattr(self.args, "service", "liferay")
            UI.heading(f"Remote Logs: {target_env} ({service})")
            self._run_lcp_cmd(
                ["log", "--environment", target_env, "--service", service],
                capture_json=False,
            )
            return

        if getattr(self.args, "sync_env", False):
            UI.heading(f"Syncing Cloud Environment Variables: {target_env}")
            data = self._run_lcp_cmd(["env", "list", "--environment", target_env])
            if data:
                custom_env = json.loads(project_meta.get("custom_env", "{}"))
                for var in data:
                    k, v = var.get("key"), var.get("value")
                    if k and v:
                        custom_env[k] = v
                        UI.info(f"  Synced {k}")
                project_meta["custom_env"] = json.dumps(custom_env)
                self.write_meta(root_path / PROJECT_META_FILE, project_meta)
                UI.success("Metadata updated.")
            return

        if getattr(self.args, "download", False) or getattr(
            self.args, "restore", False
        ):
            UI.heading(f"Downloading Cloud Backups: {target_env}")
            # Find latest backup ID
            backups = self._run_lcp_cmd(["backup", "list", "--environment", target_env])
            if not backups:
                UI.die("No backups found.")

            latest = backups[0]
            backup_id = latest.get("id")
            UI.info(f"Latest Backup: {backup_id} ({latest.get('created')})")

            snapshot_dir = root_path / "snapshots" / f"cloud_{target_env}_{backup_id}"
            snapshot_dir.mkdir(parents=True, exist_ok=True)

            # Download
            self._run_lcp_cmd(
                [
                    "backup",
                    "download",
                    backup_id,
                    "--environment",
                    target_env,
                    "--destination",
                    str(snapshot_dir),
                ],
                capture_json=False,
            )
            UI.success(f"Backups downloaded to {snapshot_dir}")

            if getattr(self.args, "restore", False):
                UI.info("Triggering local restore...")
                # We need to set the index or path for the restore command
                # Since SnapshotHandler.cmd_restore usually lists or takes an index,
                # we might need to point it to this new folder.
                # For now, we point them to the folder.
                print(
                    f"\n{UI.BYELLOW}RESTORE READY:{UI.COLOR_OFF} Run the following to apply:"
                )
                print(
                    f"{UI.CYAN}ldm restore {project_id} --backup-dir {snapshot_dir}{UI.COLOR_OFF}"
                )
            return

        UI.info(
            f"Environment '{target_env}' selected. Use flags (--list-backups, --download, --logs, --sync-env) to perform actions."
        )
