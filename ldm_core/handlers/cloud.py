import json
import shutil
import subprocess
from typing import cast

from ldm_core.constants import PROJECT_META_FILE
from ldm_core.ui import UI


class CloudService:
    """Service for Liferay Cloud (LCP) integration."""

    def __init__(self, manager=None):
        self.manager = manager

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

    def _run_lcp_cmd(
        self, args, capture_json=True, project=None, env=None, spinner=None
    ):
        """Runs an LCP command and returns parsed JSON or output string."""
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
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                )
                output = []
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
                                spinner.update_message(msg)

                            output.append(clean_line)
                    process.stdout.close()
                returncode = process.wait()
                full_output = "\n".join(output)
                if returncode != 0:
                    # LDM-402: Handle silent failure or stall
                    err_msg = (
                        full_output if full_output else "Process exited with no output."
                    )
                    UI.error(f"LCP command failed (Code {returncode}): {err_msg}")
                    return None
                return full_output

            if capture_json:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return json.loads(res.stdout)
            # For streaming or direct output
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout
        except (KeyboardInterrupt, SystemExit):
            if process:
                process.terminate()
                process.wait()
            raise
        except subprocess.CalledProcessError as e:
            UI.error(f"LCP command failed: {e.stderr or e.stdout}")
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

    def cmd_cloud_fetch(self, project_id=None, env_id=None, follow=False):
        """Orchestrates the cloud-fetch command logic."""
        self.ensure_cloud_auth()

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

        if getattr(self.manager.args, "sync_env", False):
            UI.heading(f"Syncing Cloud Environment Variables: {cp_id} / {target_env}")

            # If called via import/init-from wizard, use the original source path.
            # Otherwise use the local LDM project path.
            search_path = root_path
            source_arg = getattr(self.manager.args, "source_path", None)
            if source_arg:
                from pathlib import Path

                search_path = Path(source_arg).resolve()

            from ldm_core.utils import get_lcp_environment_variables

            envs = get_lcp_environment_variables(search_path, target_env)
            if envs is None:
                UI.warning(
                    "LCP.json not found in the workspace. Skipping environment variable sync."
                )
                return

            try:
                custom_env = json.loads(project_meta.get("custom_env", "{}"))
                for k, v in envs.items():
                    custom_env[k] = v
                    UI.info(f"  Synced {k}")

                project_meta["custom_env"] = json.dumps(custom_env)
                self.manager.write_meta(root_path, project_meta)
                UI.success("Metadata updated.")
            except Exception as e:
                UI.error(f"Failed to sync environment variables: {e}")
            return

        if getattr(self.manager.args, "download", False) or getattr(
            self.manager.args, "restore", False
        ):
            UI.heading(f"Downloading Cloud Backups: {cp_id} / {target_env}")
            with UI.spinner("Fetching backup list...") as s:
                data = self._run_lcp_cmd(
                    ["backup", "list"], project=cp_id, env=target_env, spinner=s
                )
            backups = parse_lcp_backups(data)

            if not backups:
                if data and self.manager.verbose:
                    UI.info("Raw LCP Output:")
                    print(repr(data))
                # Soft failure: just warn and skip download if no backups exist yet
                UI.warning(
                    f"No backups found in environment '{target_env}'. Skipping download."
                )
                return

            latest = backups[0]
            backup_id = latest.get("id")
            UI.info(f"Latest Backup: {backup_id} ({latest.get('created')})")

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

            # LDM-405: Post-Download Flattening
            # LCP CLI creates a nested directory: {backup_id}-{timestamp}/{database|doclib}/UUID.{gz|extension}
            # We need to flatten this so LDM's standard restore logic can find the files.
            import shutil

            UI.info("Organizing downloaded assets...")
            found_db = False
            found_vol = False

            for item in snapshot_dir.glob("**/database/*.gz"):
                shutil.move(str(item), str(snapshot_dir / "database.gz"))
                found_db = True
                break

            # For doclib, LCP downloads a folder structure.
            # LDM cmd_restore expects a volume.tgz or a volume/ directory.
            # We will move the nested UUID folder to snapshot_dir/volume/
            for item in snapshot_dir.glob("**/doclib/*"):
                if item.is_dir():
                    # Move the contents of the UUID folder to snapshot_dir/volume
                    dest_vol = snapshot_dir / "volume"
                    if dest_vol.exists():
                        shutil.rmtree(dest_vol)
                    shutil.move(str(item), str(dest_vol))
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
                    project_id, snapshot_dir, tag_for_seed=tag_for_seed
                )
            return

        UI.info(
            f"Environment '{target_env}' (Project: {cp_id}) selected. Use flags (--list-backups, --download, --logs, --sync-env) to perform actions."
        )

    def hydrate_cloud_backup(self, project_id, backup_dir_path, tag_for_seed=None):
        """Generic function to hydrate an LDM project from a cloud backup directory (local or remote)."""
        root_path = self.manager.detect_project_path(project_id, for_init=True)
        if not root_path:
            return False

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

        UI.info(f"Triggering local restore from {backup_dir_path}...")
        self.manager.cmd_restore(project_id=project_id, backup_dir=backup_dir_path)
        return True

    def cmd_hydrate(self, backup_path, project_id=None):
        """Creates or updates an LDM project from a local Liferay Cloud backup folder."""
        from pathlib import Path

        backup_dir = Path(backup_path).resolve()
        if not backup_dir.exists() or not backup_dir.is_dir():
            UI.die(f"Backup directory not found or is not a directory: {backup_dir}")

        if (
            not (backup_dir / "database.gz").exists()
            and not (backup_dir / "volume.tgz").exists()
        ):
            UI.die(
                f"Invalid cloud backup format in {backup_dir}. Missing database.gz or volume.tgz"
            )

        tag = getattr(self.manager.args, "tag", None)
        self.hydrate_cloud_backup(project_id, backup_dir, tag_for_seed=tag)

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
                UI.info(
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
