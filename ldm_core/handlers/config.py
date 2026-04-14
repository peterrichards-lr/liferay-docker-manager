import re
import json
import shutil
from datetime import datetime
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE, SCRIPT_DIR
from ldm_core.utils import run_command, get_actual_home


class ConfigHandler:
    """Mixin for configuration management (env, logging, browser)."""

    def _get_properties(self, content):
        """Robustly extracts properties from a string, handling multi-line values."""
        props = {}
        if not content:
            return props

        lines = content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # Skip comments and empty lines when looking for a new key
            if not stripped or stripped.startswith(("#", "!")):
                i += 1
                continue

            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                full_val = [val]

                # If the line ends in a backslash, it's a continuation.
                # We must consume the next line(s) regardless of their content
                # until we find a line that DOES NOT end a continuation from the previous.
                temp_val = val
                while temp_val.strip().endswith("\\") and i + 1 < len(lines):
                    i += 1
                    temp_val = lines[i]
                    full_val.append(temp_val)

                props[key] = "\n".join(full_val).strip()

            i += 1

        return props

    def update_portal_ext(self, path, updates):
        """Updates or adds properties in portal-ext.properties, handling multi-line values."""
        if not updates:
            return

        content = path.read_text() if path.exists() else ""
        props = self._get_properties(content)

        # Update the properties dictionary
        for k, v in updates.items():
            props[k] = v

        # Re-generate the entire file content to ensure clean structure
        # We try to preserve the order of the original file if possible
        new_lines = []
        original_keys_found = set()

        if content:
            lines = content.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i]
                stripped = line.strip()

                # Preserve comments and empty lines
                if not stripped or stripped.startswith(("#", "!")):
                    new_lines.append(line)
                    i += 1
                    continue

                if "=" in line:
                    key = line.split("=", 1)[0].strip()
                    if key in props:
                        # Replace the entire block
                        new_lines.append(f"{key}={props[key]}")
                        original_keys_found.add(key)

                        # Skip the original block's continuations
                        temp_val = line.split("=", 1)[1]
                        while temp_val.endswith("\\") and i + 1 < len(lines):
                            i += 1
                            temp_val = lines[i]
                    else:
                        # Keep original property
                        new_lines.append(line)
                else:
                    # Not a property line and not a comment? Keep it just in case
                    new_lines.append(line)

                i += 1

        # Add any new keys that weren't in the original file
        for k, v in props.items():
            if k not in original_keys_found:
                new_lines.append(f"{k}={v}")

        path.write_text("\n".join(new_lines).strip() + "\n")

    def sync_logging(self, paths):
        """Injects custom logging levels into the project's portal-log4j-ext.xml."""
        target = paths["portal_log4j"] / "portal-log4j-ext.xml"

        # Always ensure the directory exists
        paths["portal_log4j"].mkdir(parents=True, exist_ok=True)

        # 1. Ensure we have a valid baseline XML structure
        standard_template = '<?xml version="1.0"?>\n<Configuration strict="true">\n\t<Loggers>\n\t</Loggers>\n</Configuration>\n'
        if not target.exists() or target.stat().st_size < 10:
            target.write_text(standard_template)

        # 2. Inject custom levels if logging.json exists
        logging_file = paths["root"] / "logging.json"
        if not logging_file.exists():
            return

        try:
            log_data = json.loads(logging_file.read_text())
            if not log_data:
                return

            content = target.read_text()
            # Safety check: If the file was corrupted, restore template
            if "<Loggers>" not in content:
                content = standard_template

            for bundle, categories in log_data.items():
                for category, level in categories.items():
                    tag = f'<Logger name="{category}" level="{level}" />'
                    if category not in content:
                        content = content.replace(
                            "</Loggers>", f"\t\t{tag}\n\t</Loggers>"
                        )
                    else:
                        content = re.sub(
                            rf'<Logger name="{category}" level=".*?" />',
                            tag,
                            content,
                        )

            target.write_text(content)
        except Exception as e:
            UI.error(f"Failed to sync logging: {e}")

    def generate_log_template(self, content):
        """Adds a standard header to a generated XML log configuration."""
        if "<Configuration" in content:
            if "Loggers" not in content:
                # Add Loggers tag if missing
                if "</Configuration>" in content:
                    content = content.replace(
                        "</Configuration>",
                        "\t<Loggers>\n\t</Loggers>\n</Configuration>",
                    )

        return (
            f"<!-- Generated by LDM from template ({datetime.now().isoformat()}) -->\n"
            + content
        )

    def get_samples_root(self):
        """Locates or downloads the global samples directory."""
        from ldm_core.utils import download_samples
        from ldm_core.constants import VERSION

        # 1. Check Local (Source Checkout)
        samples_root = SCRIPT_DIR / "references" / "samples"

        # 2. Check Cache (~/.ldm/samples/vVERSION)
        if not samples_root.exists():
            home = get_actual_home()
            cache_path = home / ".ldm" / "references" / "samples" / f"v{VERSION}"
            if cache_path.exists():
                return cache_path
            else:
                # 3. Prompt & Download (Standalone Binary Mode)
                if not self.non_interactive:
                    UI.heading("On-Demand Sample Pack")
                    UI.info("Sample assets are not bundled with the standalone binary.")
                    if (
                        UI.ask(
                            f"Download sample pack for v{VERSION} (~50MB)?", "Y"
                        ).upper()
                        == "Y"
                    ):
                        if download_samples(VERSION, cache_path):
                            return cache_path
                        else:
                            UI.die("Failed to download samples.")
                    else:
                        UI.die("Samples required but download declined.")
                else:
                    UI.die("Sample assets missing and non-interactive mode is active.")
        return samples_root

    def get_samples_tag(self):
        """Extracts the reference Liferay tag from the samples metadata."""
        root = self.get_samples_root()
        meta_file = root / "metadata.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
                return meta.get("reference_tag")
            except Exception:
                pass
        return None

    def get_samples_db_type(self):
        """Extracts the database type from the samples metadata."""
        root = self.get_samples_root()
        meta_file = root / "metadata.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
                return meta.get("db_type")
            except Exception:
                pass
        return None

    def sync_samples(self, paths):
        """Sync global samples into the current project path with on-demand download support."""
        samples_root = self.get_samples_root()
        UI.info("Syncing project samples...")
        shutil.copytree(samples_root, paths["root"], dirs_exist_ok=True)

    def cmd_init_common(self):
        """Recreates the baseline common/ folder with standard development assets."""
        # Ensure we create this in the CURRENT directory, not the script directory
        common_dir = self.get_common_dir()
        common_dir.mkdir(parents=True, exist_ok=True)

        UI.heading("Initializing Baseline Common Assets")

        try:
            import importlib.resources as pkg_resources
            from ldm_core import resources

            # 1. env-blacklist.txt
            blacklist_file = common_dir / "env-blacklist.txt"
            if not blacklist_file.exists():
                content = (
                    pkg_resources.files(resources)
                    / "common_baseline"
                    / "env-blacklist.txt"
                ).read_text()
                blacklist_file.write_text(content)
                UI.info("  + Created env-blacklist.txt")

            # 2. portal-ext.properties
            pe_file = common_dir / "portal-ext.properties"
            if not pe_file.exists():
                content = (
                    pkg_resources.files(resources)
                    / "common_baseline"
                    / "portal-ext.properties"
                ).read_text()
                pe_file.write_text(content)
                UI.info("  + Created portal-ext.properties")

            # 3. Session Timeout Config
            timeout_config_name = "com.liferay.frontend.js.web.internal.session.timeout.configuration.SessionTimeoutConfiguration.scoped~3e124e46-69f0-4ebd-a3be-43b3de16f45a.config"
            timeout_file = common_dir / timeout_config_name
            if not timeout_file.exists():
                content = (
                    pkg_resources.files(resources)
                    / "common_baseline"
                    / timeout_config_name
                ).read_text()
                timeout_file.write_text(content)
                UI.info("  + Created SessionTimeout config")

            # 4. Elasticsearch Configs
            es_configs = [
                "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConfiguration.config",
                "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConnectionConfiguration-REMOTE.config",
                "com.liferay.portal.search.elasticsearch8.configuration.ElasticsearchConfiguration.config",
                "com.liferay.portal.search.elasticsearch8.configuration.ElasticsearchConnectionConfiguration-REMOTE.config",
            ]
            for es_conf in es_configs:
                target_file = common_dir / es_conf
                if not target_file.exists():
                    content = (
                        pkg_resources.files(resources) / "common_baseline" / es_conf
                    ).read_text()
                    target_file.write_text(content)
                    UI.info(f"  + Created {es_conf}")

            UI.success(f"Baseline common assets initialized in: {common_dir}")
        except Exception as e:
            UI.error(f"Failed to initialize common assets: {e}")
            if self.verbose:
                import traceback

                traceback.print_exc()

    def sync_common_assets(self, paths, host_updates=None, version=None):
        # Use the binary-aware 'common' path from setup_paths
        common_dir = paths.get("common")
        target_ext = paths["files"] / "portal-ext.properties"

        if common_dir and common_dir.exists():
            history_file = paths["root"] / ".liferay-docker.deployed"
            history = (
                set(history_file.read_text().splitlines())
                if history_file.exists()
                else set()
            )

            common_ext = common_dir / "portal-ext.properties"
            if common_ext.exists():
                if not target_ext.exists():
                    shutil.copy2(common_ext, target_ext)
                else:
                    # Robust extraction of project and common properties
                    project_props = self._get_properties(target_ext.read_text())
                    common_props = self._get_properties(common_ext.read_text())

                    # Identify keys from common that are missing in project
                    to_add = {
                        k: v for k, v in common_props.items() if k not in project_props
                    }
                    if to_add:
                        self.update_portal_ext(target_ext, to_add)

            patterns = [
                ("*.xml", paths["deploy"]),
                ("*.lpkg", paths["deploy"]),
                ("*.config", paths["configs"]),
                ("*.cfg", paths["configs"]),
            ]

            # Determine if global search is actually active and what version it is
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
            search_running = search_inspect is not None
            search_version = 8
            if search_inspect and ":7." in search_inspect:
                search_version = 7

            for pattern, target in patterns:
                for match in common_dir.glob(pattern):
                    # Skip ES-specific configs if the global search container isn't running
                    if "elasticsearch" in match.name.lower():
                        if not search_running:
                            # If the file already exists in project, remove it to allow sidecar to work correctly
                            dest = target / match.name
                            if dest.exists():
                                dest.unlink()
                            continue

                        # Cleanup: If this is a legacy config (no -REMOTE), ensure it's GONE from project
                        # to avoid Liferay defaulting to localhost.
                        if "-REMOTE" not in match.name and "Connection" in match.name:
                            dest = target / match.name
                            if dest.exists():
                                dest.unlink()
                            continue

                        # If search is running, only copy the ones matching the search version
                        is_es7_file = "elasticsearch7" in match.name
                        is_es8_file = "elasticsearch8" in match.name

                        # Logic:
                        # - If ES7 is running: ONLY ES7 files.
                        # - If ES8 is running: BOTH ES7 and ES8 files (for compatibility mode).
                        should_copy = False
                        if search_version == 7 and is_es7_file:
                            should_copy = True
                        elif search_version == 8:
                            if is_es7_file or is_es8_file:
                                should_copy = True

                        if not should_copy:
                            dest = target / match.name
                            if dest.exists():
                                dest.unlink()
                            continue

                    dest = target / match.name
                    # Always copy if it doesn't exist
                    if not dest.exists():
                        shutil.copy(match, dest)
                        history.add(match.name)
                    # For OSGi configs, overwrite if it's a managed file (to apply baseline updates)
                    elif match.name in history and (
                        pattern.endswith("config") or pattern.endswith("cfg")
                    ):
                        shutil.copy(match, dest)

            history_file.write_text("\n".join(sorted(list(history))))
        else:
            UI.warning(
                "Global 'common/' folder not found. Some baseline assets may be missing."
            )
            UI.info(
                f"You can recreate the baseline by running: {UI.CYAN}ldm init-common{UI.COLOR_OFF}"
            )

        if host_updates:
            self.update_portal_ext(target_ext, host_updates)

    def cmd_config(self, key=None, value=None):
        """View or set global LDM configuration."""
        config_path = get_actual_home() / ".ldmrc"
        config = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
            except Exception:
                pass

        if not key and not value:
            UI.heading("Global LDM Configuration")
            if not config:
                UI.info("No global configuration found.")
            else:
                for k, v in sorted(config.items()):
                    print(f"  {k} = {v}")
            return

        if key and value is None:
            # Get specific key
            val = config.get(key)
            if val is not None:
                print(val)
            else:
                UI.die(f"Configuration key '{key}' not found.")
            return

        if key and value:
            # Set specific key
            if getattr(self.args, "remove", False) or value.lower() == "unset":
                config.pop(key, None)
                UI.success(f"Configuration key '{key}' removed.")
            else:
                config[key] = value
                UI.success(f"Configuration key '{key}' set to '{value}'.")

            config_path.write_text(json.dumps(config, indent=4))

    def cmd_env(self, project_id=None):
        pid = project_id
        vars_to_apply = getattr(self.args, "vars", [])
        if vars_to_apply and "=" not in vars_to_apply[0] and not pid:
            test_path = self.detect_project_path(vars_to_apply[0])
            if test_path:
                pid = vars_to_apply.pop(0)

        root_path = self.detect_project_path(pid)
        if not root_path:
            return
        paths = self.setup_paths(root_path)
        project_meta = self.read_meta(paths["root"] / PROJECT_META_FILE)
        custom_env = json.loads(project_meta.get("custom_env", "{}"))

        if getattr(self.args, "import_env", False):
            for v in self.get_host_passthrough_env(paths):
                if "=" in v:
                    k, val = v.split("=", 1)
                    custom_env[k] = val
            vars_to_apply = []

        if (
            not vars_to_apply
            and self.non_interactive
            and not getattr(self.args, "import_env", False)
        ):
            UI.die(
                "No environment variables specified. In non-interactive mode, use: ldm env <pid> KEY=VALUE"
            )

        if not vars_to_apply and not self.non_interactive:
            UI.heading("Environment Variables")
            if custom_env:
                for k, v in sorted(custom_env.items()):
                    print(f"  {k}={v}")
            passthroughs = self.get_host_passthrough_env(paths)
            if passthroughs:
                UI.info("\nHost Passthrough:")
                for v in passthroughs:
                    print(f"  {v}")

            key = UI.ask("\nEnter Key (or Enter to apply current shell vars)")
            if not key:
                project_meta["custom_env"] = json.dumps(custom_env)
                self.write_meta(paths["root"] / PROJECT_META_FILE, project_meta)
                UI.success("Environment variables saved.")
                return

            if "=" in key:
                k, v = key.split("=", 1)
                custom_env[k] = v
            else:
                value = UI.ask(f"Enter Value for {key}")
                custom_env[key] = value

            project_meta["custom_env"] = json.dumps(custom_env)
            self.write_meta(paths["root"] / PROJECT_META_FILE, project_meta)
            UI.success(f"Variable '{key}' updated.")
        else:
            # Batch update from CLI
            for pair in vars_to_apply:
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if getattr(self.args, "remove", False):
                        custom_env.pop(k, None)
                    else:
                        custom_env[k] = v
                else:
                    if getattr(self.args, "remove", False):
                        custom_env.pop(pair, None)

            project_meta["custom_env"] = json.dumps(custom_env)
            self.write_meta(paths["root"] / PROJECT_META_FILE, project_meta)
            UI.success("Environment updated.")
