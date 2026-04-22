import json
import shutil
import subprocess
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE


class CloudHandler:
    """Mixin for Liferay Cloud (LCP) integration."""

    def __init__(self, args=None):
        self.args = args
        self.verbose = getattr(args, "verbose", False)
        self.non_interactive = getattr(args, "non_interactive", False)

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
        if UI.confirm("Run 'lcp login' now?", "Y"):
            lcp_bin = shutil.which("lcp")
            try:
                # lcp login is interactive and may open a browser
                subprocess.run([lcp_bin, "login"], check=True)
                return True
            except Exception as e:
                UI.error(f"Login failed: {e}")

        UI.die("Authentication required for cloud operations.")

    def _run_lcp_cmd(self, args, capture_json=True, project=None, env=None):
        """Runs an LCP command and returns parsed JSON or output string."""
        lcp_bin = shutil.which("lcp")
        if not lcp_bin:
            UI.die("LCP CLI not found.")

        cmd = [lcp_bin] + args
        if project:
            cmd.extend(["--project", project])
        if env:
            cmd.extend(["--environment", env])

        # The LCP CLI version used by the user does not support --json
        # We disable it globally to prevent "Unknown argument: json" errors
        capture_json = False

        if capture_json:
            cmd.extend(["--json"])

        try:
            if capture_json:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return json.loads(res.stdout)
            else:
                # For streaming or direct output
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return res.stdout
        except subprocess.CalledProcessError as e:
            UI.error(f"LCP command failed: {e.stderr or e.stdout}")
            return None
        except Exception as e:
            UI.error(f"LCP error: {e}")
            return None

    def _get_cloud_liferay_version(self, cp_id, target_env):
        """Attempts to detect the Liferay version from the cloud environment configuration."""
        data = self._run_lcp_cmd(["service", "list"], project=cp_id, env=target_env)
        if data:
            # Look for liferay service
            for service in data:
                if service.get("id") == "liferay":
                    image = service.get("image")
                    if image and ":" in image:
                        tag = image.split(":")[1]
                        return tag
        return None

    def cmd_cloud_fetch(self, project_id=None, env_id=None, follow=False):
        """Orchestrates the cloud-fetch command logic."""
        self.ensure_cloud_auth()

        root_path = self.detect_project_path(project_id, for_init=True)
        if not root_path:
            return

        is_new_project = not (root_path / PROJECT_META_FILE).exists()
        from ldm_core.utils import sanitize_id

        project_meta = self.read_meta(root_path)
        cp_id = sanitize_id(
            project_meta.get("cloud_project_id")
            or project_meta.get("project_id")
            or root_path.name
        )

        # Use provided env_id or positional arg
        target_env = sanitize_id(env_id or getattr(self.args, "env_id", None))

        if getattr(self.args, "list_envs", False) or not target_env:
            UI.heading(f"Available Liferay Cloud Environments (Project: {cp_id})")
            data = self._run_lcp_cmd(["list"])
            if data:
                print(data)  # Since it's plain text now, just print it
            return

        if getattr(self.args, "list_backups", False):
            UI.heading(f"Liferay Cloud Backups: {cp_id} / {target_env}")
            data = self._run_lcp_cmd(["backup", "list"], project=cp_id, env=target_env)
            if data:
                for backup in data[:10]:  # Show latest 10
                    date = backup.get("created", "unknown")
                    backup_id = backup.get("id")
                    print(f"  [{UI.CYAN}{backup_id}{UI.COLOR_OFF}] {date}")
            return

        if getattr(self.args, "logs", False):
            service = getattr(self.args, "service", "liferay")
            UI.heading(f"Remote Logs: {cp_id} / {target_env} ({service})")
            lcp_args = ["log", "--service", service]
            if getattr(self.args, "follow", False):
                lcp_args.append("--follow")
            self._run_lcp_cmd(
                lcp_args,
                capture_json=False,
                project=cp_id,
                env=target_env,
            )
            return

        if getattr(self.args, "sync_env", False):
            UI.heading(f"Syncing Cloud Environment Variables: {cp_id} / {target_env}")
            data = self._run_lcp_cmd(["env", "list"], project=cp_id, env=target_env)
            if data:
                custom_env = json.loads(project_meta.get("custom_env", "{}"))
                for var in data:
                    k, v = var.get("key"), var.get("value")
                    if k and v:
                        custom_env[k] = v
                        UI.info(f"  Synced {k}")
                project_meta["custom_env"] = json.dumps(custom_env)
                self.write_meta(root_path, project_meta)
                UI.success("Metadata updated.")
            return

        if getattr(self.args, "download", False) or getattr(
            self.args, "restore", False
        ):
            UI.heading(f"Downloading Cloud Backups: {cp_id} / {target_env}")
            # Find latest backup ID
            backups = self._run_lcp_cmd(
                ["backup", "list"], project=cp_id, env=target_env
            )
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
                    "--destination",
                    str(snapshot_dir),
                ],
                capture_json=False,
                project=cp_id,
                env=target_env,
            )
            UI.success(f"Backups downloaded to {snapshot_dir}")

            # Checksum Verification
            self._verify_cloud_backup_checksums(snapshot_dir, latest)

            if getattr(self.args, "restore", False):
                # Seeded Start: Boost performance for new project restorations
                if is_new_project:
                    tag_for_seed = self._get_cloud_liferay_version(cp_id, target_env)
                    if tag_for_seed:
                        paths = self.setup_paths(root_path)
                        # We use the db type from args or default to mysql (Liferay Cloud standard)
                        db_type_for_seed = getattr(self.args, "db", None) or "mysql"
                        if self._ensure_seeded(tag_for_seed, db_type_for_seed, paths):
                            # Refresh meta from seed before merging restoration changes
                            seed_meta = self.read_meta(root_path)
                            project_meta.update(seed_meta)

                UI.info("Triggering local restore...")
                self.cmd_restore(project_id=project_id, backup_dir=snapshot_dir)
            return

        UI.info(
            f"Environment '{target_env}' (Project: {cp_id}) selected. Use flags (--list-backups, --download, --logs, --sync-env) to perform actions."
        )

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
                    UI.info(f"Verifying {file_name} checksum...")
                    import hashlib

                    md5 = hashlib.md5()  # nosec B324
                    with open(file_path, "rb") as f:
                        for chunk in iter(lambda: f.read(4096), b""):
                            md5.update(chunk)
                    actual = md5.hexdigest()
                    if actual == expected:
                        UI.info(f"  {file_name}: {UI.GREEN}OK{UI.COLOR_OFF}")
                    else:
                        UI.warning(
                            f"  {file_name}: {UI.RED}CHECKSUM MISMATCH{UI.COLOR_OFF}"
                        )
                        UI.warning(f"    Expected: {expected}")
                        UI.warning(f"    Actual:   {actual}")
