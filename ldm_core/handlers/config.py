import contextlib
import json
import os
import platform
import re
import shutil
from datetime import datetime
from pathlib import Path

from ldm_core.constants import PROJECT_META_FILE, SCRIPT_DIR
from ldm_core.ui import UI
from ldm_core.utils import (
    atomic_copy,
    get_actual_home,
    run_command,
    safe_write_text,
)


class ConfigService:
    """Service for configuration management (env, logging, browser)."""

    def __init__(self, manager):
        self.manager = manager

    def get_global_config(self):
        """Helper to load global LDM configuration from ~/.ldmrc."""
        from ldm_core.utils import get_actual_home

        config_path = get_actual_home() / ".ldmrc"
        if config_path.exists():
            with contextlib.suppress(Exception):
                return json.loads(config_path.read_text())
        return {}

    def get_ngrok_auth_token(self):
        """Retrieves the NGROK_AUTHTOKEN from env vars or global config."""
        token = os.environ.get("NGROK_AUTHTOKEN")
        if token:
            return token
        config = self.get_global_config()
        return config.get("ngrok_authtoken")

    def set_global_config(self, key, value):
        """Saves a key-value pair to the global config (~/.ldmrc)."""
        from ldm_core.utils import get_actual_home

        config = self.get_global_config()
        config[key] = value
        config_path = get_actual_home() / ".ldmrc"
        config_path.write_text(json.dumps(config, indent=4))

    def set_ngrok_auth_token(self, token):
        """Saves the NGROK_AUTHTOKEN to global config."""
        self.set_global_config("ngrok_authtoken", token)

    def _get_properties(self, content):
        """Robustly extracts properties from a string, handling multi-line values."""
        props: dict[str, str] = {}
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

    def _get_properties_with_metadata(self, content):
        """Robustly extracts properties and their metadata (like !important) from a string.
        Returns a tuple: (properties_dict, important_keys_set)
        """
        props: dict[str, str] = {}
        important_keys: set[str] = set()
        if not content:
            return props, important_keys

        lines = content.splitlines()
        i = 0
        last_comment_was_important = False

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                i += 1
                continue

            if stripped.startswith(("#", "!")):
                comment_text = stripped[1:].strip().lower()
                if comment_text == "!important" or comment_text.startswith(
                    "!important"
                ):
                    last_comment_was_important = True
                else:
                    last_comment_was_important = False
                i += 1
                continue

            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                full_val = [val]

                is_important = last_comment_was_important
                # Reset comment state
                last_comment_was_important = False

                # Handle continuation lines
                temp_val = val
                while temp_val.strip().endswith("\\") and i + 1 < len(lines):
                    i += 1
                    temp_val = lines[i]
                    full_val.append(temp_val)

                # Check if the last line has an inline comment with !important
                last_line_stripped = temp_val.strip()
                if " #" in last_line_stripped:
                    parts = last_line_stripped.split(" #", 1)
                    inline_comment = parts[1].strip().lower()
                    if inline_comment == "!important" or inline_comment.startswith(
                        "!important"
                    ):
                        is_important = True
                        # Strip inline comment from value representation
                        idx = full_val[-1].rfind(" #")
                        full_val[-1] = full_val[-1][:idx]

                value = "\n".join(full_val).strip()
                props[key] = value
                if is_important:
                    important_keys.add(key)
            else:
                last_comment_was_important = False

            i += 1

        return props, important_keys

    def validate_properties(
        self, paths, properties_dict, project_meta, is_dry_run=False
    ):
        """Statically verifies properties for quotes, JDBC structure, database compatibility, and mount paths."""
        errors = []
        project_meta = project_meta or {}

        # 1. Unclosed quotes check
        for k, v in properties_dict.items():
            if not isinstance(v, str):
                continue
            if v.startswith(('"', "'")):
                quote_char = v[0]
                if len(v) < 2 or not v.endswith(quote_char):
                    errors.append(f"Property '{k}' has an unclosed quote: {v}")

        # 2. Malformed JDBC URLs check
        jdbc_url = properties_dict.get("jdbc.default.url")
        if jdbc_url:
            if not jdbc_url.startswith("jdbc:"):
                errors.append(
                    f"Property 'jdbc.default.url' is malformed (must start with 'jdbc:'): {jdbc_url}"
                )
            else:
                parts = jdbc_url.split(":", 2)
                if len(parts) >= 2:
                    subprotocol = parts[1]
                    if subprotocol in ["postgresql", "mysql", "mariadb"]:
                        if "//" not in jdbc_url:
                            errors.append(
                                f"Property 'jdbc.default.url' coordinates are malformed (missing '//'): {jdbc_url}"
                            )
                        if jdbc_url.count("[") != jdbc_url.count("]"):
                            errors.append(
                                f"Property 'jdbc.default.url' has mismatched bracket symbols: {jdbc_url}"
                            )
                        if jdbc_url.count("{") != jdbc_url.count("}"):
                            errors.append(
                                f"Property 'jdbc.default.url' has mismatched braces symbols: {jdbc_url}"
                            )

        # 3. Conflicting Database Configuration check
        driver_class = properties_dict.get("jdbc.default.driverClassName")
        db_type = project_meta.get("db_type")

        if db_type:
            db_type_lower = db_type.lower()
            if db_type_lower == "hypersonic":
                if driver_class and not any(
                    x in driver_class.lower() for x in ["hsqldb", "hypersonic"]
                ):
                    errors.append(
                        f"Database conflict: project is configured for Hypersonic in meta.json, "
                        f"but portal-ext.properties driver class is '{driver_class}'"
                    )
                if jdbc_url and "hsqldb" not in jdbc_url.lower():
                    errors.append(
                        f"Database conflict: project is configured for Hypersonic in meta.json, "
                        f"but portal-ext.properties jdbc.default.url is '{jdbc_url}'"
                    )
            elif db_type_lower == "postgresql":
                if driver_class and "postgresql" not in driver_class.lower():
                    errors.append(
                        f"Database conflict: project is configured for PostgreSQL in meta.json, "
                        f"but portal-ext.properties driver class is '{driver_class}'"
                    )
                if jdbc_url and "postgresql" not in jdbc_url.lower():
                    errors.append(
                        f"Database conflict: project is configured for PostgreSQL in meta.json, "
                        f"but portal-ext.properties jdbc.default.url is '{jdbc_url}'"
                    )
            elif db_type_lower in ["mysql", "mariadb"]:
                if driver_class and not any(
                    x in driver_class.lower() for x in ["mysql", "mariadb"]
                ):
                    errors.append(
                        f"Database conflict: project is configured for {db_type} in meta.json, "
                        f"but portal-ext.properties driver class is '{driver_class}'"
                    )
                if jdbc_url and not any(
                    x in jdbc_url.lower() for x in ["mysql", "mariadb"]
                ):
                    errors.append(
                        f"Database conflict: project is configured for {db_type} in meta.json, "
                        f"but portal-ext.properties jdbc.default.url is '{jdbc_url}'"
                    )

        # Internal consistency check within properties
        if driver_class and jdbc_url:
            dc_lower = driver_class.lower()
            ju_lower = jdbc_url.lower()
            if "postgresql" in dc_lower and "postgresql" not in ju_lower:
                errors.append(
                    f"Driver-URL mismatch: driver is PostgreSQL ('{driver_class}') "
                    f"but jdbc.default.url is '{jdbc_url}'"
                )
            elif any(x in dc_lower for x in ["mysql", "mariadb"]) and not any(
                x in ju_lower for x in ["mysql", "mariadb"]
            ):
                errors.append(
                    f"Driver-URL mismatch: driver is MySQL/MariaDB ('{driver_class}') "
                    f"but jdbc.default.url is '{jdbc_url}'"
                )
            elif "hsqldb" in dc_lower and "hsqldb" not in ju_lower:
                errors.append(
                    f"Driver-URL mismatch: driver is HSQLDB ('{driver_class}') "
                    f"but jdbc.default.url is '{jdbc_url}'"
                )

        # 4. Missing Mount Paths check
        mount_dirs = [
            ("deploy", paths.get("deploy")),
            ("files", paths.get("files")),
            ("configs", paths.get("configs")),
            ("modules", paths.get("modules")),
            ("cx", paths.get("cx")),
            ("scripts", paths.get("scripts")),
            ("log4j", paths.get("log4j")),
            ("portal_log4j", paths.get("portal_log4j")),
        ]

        for name, path in mount_dirs:
            if not path:
                continue
            if not path.exists():
                if is_dry_run:
                    UI.warning(f"Mount directory '{name}' is missing at: {path}")
                else:
                    try:
                        path.mkdir(parents=True, exist_ok=True)
                        UI.info(f"Created missing mount directory: {path}")
                    except Exception as e:
                        UI.warning(
                            f"Could not create missing mount directory '{name}' at {path}: {e}"
                        )

        if errors:
            for err in errors:
                UI.error(err)
            UI.die(
                "Config Integrity check failed: properties validation errors detected."
            )

    def update_portal_ext(self, paths, updates, important_keys=None):
        """Updates or adds properties in portal-ext.properties, handling multi-line values."""
        if not updates:
            return

        if isinstance(paths, dict):
            pe_path = paths["files"] / "portal-ext.properties"
        else:
            pe_path = Path(paths)
            if pe_path.is_dir():
                pe_path = pe_path / "portal-ext.properties"

        content = pe_path.read_text() if pe_path.exists() else ""
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

                # Clean up existing preceding !important comments if the key is in updates
                if stripped.startswith(("#", "!")):
                    comment_text = stripped[1:].strip().lower()
                    if comment_text == "!important" or comment_text.startswith(
                        "!important"
                    ):
                        # Peek ahead to see if the next property is being updated
                        next_i = i + 1
                        while next_i < len(lines) and lines[next_i].strip().startswith(
                            ("#", "!")
                        ):
                            next_i += 1
                        if next_i < len(lines) and "=" in lines[next_i]:
                            next_key = lines[next_i].split("=", 1)[0].strip()
                            if next_key in updates:
                                # Skip this comment line, it will be regenerated or discarded
                                i += 1
                                continue
                    new_lines.append(line)
                    i += 1
                    continue

                if "=" in line:
                    key = line.split("=", 1)[0].strip()
                    if key in props:
                        # Write preceding !important if key is important
                        if important_keys and key in important_keys:
                            new_lines.append("# !important")

                        # Replace the entire block
                        new_lines.append(f"{key}={props[key]}")
                        original_keys_found.add(key)

                        # Skip the original block's continuations
                        temp_val = line.split("=", 1)[1]
                        while temp_val.strip().endswith("\\") and i + 1 < len(lines):
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
                if important_keys and k in important_keys:
                    new_lines.append("# !important")
                new_lines.append(f"{k}={v}")

        safe_write_text(pe_path, "\n".join(new_lines).strip() + "\n")

    def remove_portal_ext(self, paths, keys_to_remove):
        """Surgically removes specific properties from portal-ext.properties."""
        pe_path = paths["files"] / "portal-ext.properties"
        if not pe_path.exists():
            return

        from ldm_core.utils import safe_write_text

        lines = pe_path.read_text().splitlines()
        new_lines: list[str] = []
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if "=" in stripped and not stripped.startswith(("#", "!")):
                key = stripped.split("=", 1)[0].strip()
                if key in keys_to_remove:
                    # Skip the entire block
                    temp_val = stripped.split("=", 1)[1]
                    while temp_val.endswith("\\") and i + 1 < len(lines):
                        i += 1
                        temp_val = lines[i]
                    i += 1
                    if new_lines and new_lines[-1].strip().lower() in (
                        "# !important",
                        "!important",
                    ):
                        new_lines.pop()
                    continue

            new_lines.append(line)
            i += 1

        safe_write_text(pe_path, "\n".join(new_lines).strip() + "\n")

    def sync_logging(self, paths):
        """Injects custom logging levels into the project's portal-log4j-ext.xml."""
        target = paths["portal_log4j"] / "portal-log4j-ext.xml"

        # Always ensure the directory exists
        with contextlib.suppress(PermissionError, OSError):
            paths["portal_log4j"].mkdir(parents=True, exist_ok=True)

        # 1. Ensure we have a valid baseline XML structure with hot-reload enabled
        standard_template = '<?xml version="1.0"?>\n<Configuration strict="true" monitorInterval="5">\n\t<Loggers>\n\t</Loggers>\n</Configuration>\n'

        if not target.exists() or target.stat().st_size < 10:
            safe_write_text(target, standard_template)

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

            for _bundle, categories in log_data.items():
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

            safe_write_text(target, content)
        except Exception as e:
            UI.error(f"Failed to sync logging: {e}")

    def generate_log_template(self, content):
        """Adds a standard header to a generated XML log configuration."""
        if "<Configuration" in content and "Loggers" not in content:
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
        from ldm_core.constants import VERSION

        # 1. Priority: Environment Variable (Development Override)
        env_path = os.environ.get("LDM_SAMPLES_PATH")
        if env_path:
            p = Path(env_path).resolve()
            if p.exists():
                return p

        # 2. Check Cache (~/.ldm/references/samples/vVERSION)
        home = get_actual_home()
        cache_base = home / ".ldm" / "references" / "samples"

        cache_versioned = cache_base / f"v{VERSION}"
        if cache_versioned.exists():
            return cache_versioned

        # Backwards compatibility for older caches
        cache_current = cache_base / "current"

        # 3. Check Local (Source Checkout)
        # We check relative to SCRIPT_DIR and also CWD for convenience during dev
        # But we do this BEFORE prompting for download so developers don't have to download
        # if they are actively working in the LDM repo.
        samples_root = SCRIPT_DIR / "references" / "samples"
        if not samples_root.exists():
            samples_root = Path.cwd() / "references" / "samples"

        if samples_root.exists():
            return samples_root

        # 4. Prompt & Download
        if self.manager.non_interactive:
            UI.info(f"Auto-downloading sample pack for v{VERSION}...")
            if self.manager.assets.download_samples(VERSION, cache_versioned):
                return cache_versioned
            UI.die("Failed to download sample pack.")
        else:
            UI.heading("On-Demand Sample Pack")
            UI.info("Sample assets are not bundled with the standalone binary.")
            if UI.confirm(f"Download sample pack for v{VERSION} (~50MB)?", "Y"):
                if self.manager.assets.download_samples(VERSION, cache_versioned):
                    UI.success("Sample pack ready.")
                    return cache_versioned
                UI.die("Failed to download sample pack.")
            else:
                UI.die("Sample pack required for --samples mode.")
        return None

    def get_samples_tag(self):
        """Extracts the reference Liferay tag from the samples metadata."""
        root = self.manager.get_samples_root()
        meta_file = root / "meta"
        if meta_file.exists():
            try:
                for line in meta_file.read_text().splitlines():
                    if line.startswith("tag="):
                        return line.split("=", 1)[1].strip()
            except Exception:
                pass
        return None

    def get_samples_db_type(self):
        """Extracts the database type from the samples metadata."""
        root = self.manager.get_samples_root()
        meta_file = root / "meta"
        if meta_file.exists():
            try:
                for line in meta_file.read_text().splitlines():
                    if line.startswith("db_type="):
                        return line.split("=", 1)[1].strip()
            except Exception:
                pass
        return None

    def sync_samples(self, paths):
        """Sync global samples into the current project path with on-demand download support."""
        samples_root = self.manager.get_samples_root()
        UI.info("Syncing project samples...")
        shutil.copytree(
            samples_root, paths["root"], dirs_exist_ok=True, copy_function=atomic_copy
        )

    def cmd_init_common(self):
        """Recreates the baseline common/ folder with standard development assets."""
        # Ensure we create this in the CURRENT directory, not the script directory
        common_dir = self.manager.get_common_dir()
        with contextlib.suppress(PermissionError, OSError):
            common_dir.mkdir(parents=True, exist_ok=True)

        UI.heading("Initializing Baseline Common Assets")

        from ldm_core.utils import safe_write_text

        try:
            import importlib.resources as pkg_resources

            from ldm_core import resources

            baseline_path = pkg_resources.files(resources) / "common_baseline"

            created_count = 0
            for resource_file in baseline_path.iterdir():
                # Skip non-files or directories
                if not resource_file.is_file():
                    continue

                target_file = common_dir / resource_file.name
                if not target_file.exists():
                    content = resource_file.read_text()
                    safe_write_text(target_file, content)
                    UI.info(f"  + Created {resource_file.name}")
                    created_count += 1

            if created_count == 0:
                UI.info("  (All baseline assets already present)")

            UI.success(f"Baseline common assets initialized in: {common_dir}")
        except Exception as e:
            UI.error(f"Failed to initialize common assets: {e}")
            if self.manager.verbose:
                import traceback

                traceback.print_exc()

    def sync_common_assets(
        self, paths, host_updates=None, version=None, project_meta=None
    ):
        # Safety: If passed a direct path (common error in refactored handlers), initialize paths dict
        if not isinstance(paths, dict):
            paths = self.manager.setup_paths(paths)

        # Backward compatibility: populate common_dirs if missing
        if "common_dirs" not in paths:
            if paths.get("common"):
                paths["common_dirs"] = [paths["common"]]
            else:
                paths["common_dirs"] = []

        # Handle Captcha Configuration
        if project_meta:
            no_captcha = str(project_meta.get("no_captcha", "false")).lower() == "true"
            captcha_cfg = (
                paths["configs"]
                / "com.liferay.captcha.configuration.CaptchaConfiguration.config"
            )
            if no_captcha:
                if host_updates is None:
                    host_updates = {}
                host_updates["captcha.enforce.disabled"] = "true"

                if not captcha_cfg.exists():
                    with contextlib.suppress(PermissionError, OSError):
                        captcha_cfg.parent.mkdir(parents=True, exist_ok=True)
                    from ldm_core.utils import safe_write_text

                    safe_write_text(captcha_cfg, 'maxChallenges=I"-1"\n')
            else:
                # LDM-369: If not explicitly disabled, ensure it's enabled (reversible)
                if captcha_cfg.exists():
                    with contextlib.suppress(PermissionError, OSError):
                        captcha_cfg.unlink()

                if host_updates is None:
                    host_updates = {}
                host_updates["captcha.enforce.disabled"] = "false"

        # Handle Fast-Login Configuration
        if project_meta:
            fast_login = str(project_meta.get("fast_login", "false")).lower() == "true"
            if fast_login:
                db_type = project_meta.get("db_type", "postgresql")
                if db_type == "hypersonic":
                    UI.warning(
                        "The '--fast-login' feature (specifically password policy bypass) does not fully work with the default Hypersonic database. "
                        "For best results, use an external database like PostgreSQL or MySQL."
                    )

                if host_updates is None:
                    host_updates = {}

                host_updates.update(
                    {
                        "captcha.check.portal.create_account": "false",
                        "captcha.check.portal.send_password": "false",
                        "company.security.strangers.verify": "false",
                        "enterprise.product.notification.enabled": "false",
                        "live.users.enabled": "true",
                        "passwords.default.policy.change.required": "false",
                        "passwords.passwordpolicytoolkit.generator": "static",
                        "passwords.passwordpolicytoolkit.static": "test",
                        "setup.wizard.enabled": "false",
                        "terms.of.use.required": "false",
                        "users.last.name.required": "false",
                        "users.reminder.queries.custom.question.enabled": "false",
                        "users.reminder.queries.enabled": "false",
                    }
                )

        # Handle Feature Flags (Global Defaults + Project Specific)
        global_config = self.get_global_config()
        global_features = global_config.get("features", "")
        project_features = project_meta.get("features", "") if project_meta else ""

        # Merge global and project features
        all_features = set()
        for f_list in [global_features, project_features]:
            if f_list:
                for f in f_list.split(","):
                    if f.strip():
                        all_features.add(f.strip())

        if all_features:
            if host_updates is None:
                host_updates = {}
            for f in sorted(all_features):
                if f.lower() in ["dev", "beta", "release"]:
                    host_updates[f"feature.flag.ui.visible[{f.lower()}]"] = "true"
                else:
                    host_updates[f"feature.flag.{f}"] = "true"

        # Handle Preferred Admin User Details from Global Configuration
        admin_mappings = {
            "admin_password": "default.admin.password",  # pragma: allowlist secret
            "admin_screen_name": "default.admin.screen.name",
            "admin_email_prefix": "default.admin.email.address.prefix",
            "admin_first_name": "default.admin.first.name",
            "admin_middle_name": "default.admin.middle.name",
            "admin_last_name": "default.admin.last.name",
        }
        for config_key, portal_key in admin_mappings.items():
            val = global_config.get(config_key)
            if val is not None:
                if host_updates is None:
                    host_updates = {}
                host_updates[portal_key] = val

        target_ext = paths["files"] / "portal-ext.properties"

        # 1. Load Pre-warmed Seed properties (Layer 1)
        seed_ext = (
            Path(__file__).parent.parent
            / "resources"
            / "common_baseline"
            / "portal-ext.properties"
        )
        seed_props, seed_imp = {}, set()
        if seed_ext.exists():
            try:
                seed_props, seed_imp = self._get_properties_with_metadata(
                    seed_ext.read_text()
                )
            except (FileNotFoundError, OSError):
                pass

        # 2. Load LDMP overrides (Layer 2)
        ldmp_ext = paths["root"] / ".liferay-docker" / "ldmp-portal-ext.properties"
        ldmp_props, ldmp_imp = {}, set()
        if ldmp_ext.exists():
            try:
                ldmp_props, ldmp_imp = self._get_properties_with_metadata(
                    ldmp_ext.read_text()
                )
            except (FileNotFoundError, OSError):
                pass

        # 3. Load Global & Local Common properties (Layers 3 & 4)
        global_props, global_imp = {}, set()
        local_props, local_imp = {}, set()

        for cd in paths.get("common_dirs", []):
            cd_ext = cd / "portal-ext.properties"
            if cd_ext.exists():
                try:
                    c_props, c_imp = self._get_properties_with_metadata(
                        cd_ext.read_text()
                    )
                    if (
                        ".ldm" in str(cd.resolve())
                        or "home" in str(cd.resolve())
                        or "global" in str(cd.resolve()).lower()
                    ):
                        global_props, global_imp = c_props, c_imp
                    else:
                        local_props, local_imp = c_props, c_imp
                except (FileNotFoundError, OSError):
                    pass

        # 4. Load Project-level customizations (Layer 5)
        project_props, project_imp = {}, set()
        if target_ext.exists():
            try:
                project_props, project_imp = self._get_properties_with_metadata(
                    target_ext.read_text()
                )
            except (FileNotFoundError, OSError):
                pass

        # Compute Project Baseline = Seed + LDMP
        baseline_props = dict(seed_props)
        baseline_props.update(ldmp_props)
        baseline_imp = set(seed_imp)
        for k in ldmp_props:
            if k in ldmp_imp:
                baseline_imp.add(k)
            else:
                baseline_imp.discard(k)

        # Compute Project Customizations (Layer 5 effective)
        project_custom_props = {}
        project_custom_imp = set()
        for k, v in project_props.items():
            if k not in baseline_props:
                project_custom_props[k] = v
                if k in project_imp:
                    project_custom_imp.add(k)
            else:
                val_differs = v != baseline_props[k]
                imp_differs = (k in project_imp) != (k in baseline_imp)
                if val_differs or imp_differs:
                    project_custom_props[k] = v
                    if k in project_imp:
                        project_custom_imp.add(k)

        # 5. Resolve cascade winner using CSS-style !important rule
        all_keys = (
            set(seed_props.keys())
            | set(ldmp_props.keys())
            | set(global_props.keys())
            | set(local_props.keys())
            | set(project_custom_props.keys())
        )

        winning_props = {}
        winning_imp = set()

        for k in all_keys:
            entries = []
            if k in seed_props:
                entries.append((1, "seed", seed_props[k], k in seed_imp))
            if k in ldmp_props:
                entries.append((2, "ldmp", ldmp_props[k], k in ldmp_imp))
            if k in global_props:
                entries.append((3, "global", global_props[k], k in global_imp))
            if k in local_props:
                entries.append((4, "local", local_props[k], k in local_imp))
            if k in project_custom_props:
                entries.append(
                    (5, "project", project_custom_props[k], k in project_custom_imp)
                )

            important_entries = [e for e in entries if e[3]]
            if important_entries:
                winner = max(important_entries, key=lambda x: x[0])
            else:
                winner = max(entries, key=lambda x: x[0])

            winning_props[k] = winner[2]
            if winner[3]:
                winning_imp.add(k)

        # Merge System/Runtime Injections (host_updates) as highest priority normal overrides
        if host_updates:
            for k, v in host_updates.items():
                winning_props[k] = v
                winning_imp.discard(k)

        is_dry_run = (
            getattr(self.manager.args, "dry_run_properties", False)
            or getattr(self.manager.args, "dry_run", False)
            or os.environ.get("LDM_DRY_RUN", "").lower() == "true"
        )

        # Pre-Flight static properties verification
        self.validate_properties(
            paths, winning_props, project_meta, is_dry_run=is_dry_run
        )

        # Diff and apply changes to target_ext
        current_props, current_imp = {}, set()
        if target_ext.exists():
            try:
                current_props, current_imp = self._get_properties_with_metadata(
                    target_ext.read_text()
                )
            except (FileNotFoundError, OSError):
                pass

        to_update = {}
        for k, v in winning_props.items():
            if current_props.get(k) != v or (k in current_imp) != (k in winning_imp):
                to_update[k] = v

        if to_update:
            if is_dry_run:
                UI.info("[DRY RUN] Would update portal-ext.properties with:")
                for k, v in to_update.items():
                    imp_str = " # !important" if k in winning_imp else ""
                    UI.info(f"  {k}={v}{imp_str}")
            else:
                if not target_ext.exists():
                    with contextlib.suppress(PermissionError, OSError):
                        target_ext.parent.mkdir(parents=True, exist_ok=True)
                self.manager.update_portal_ext(
                    target_ext, to_update, important_keys=winning_imp
                )

        # Save copy as original-portal-ext.properties if not exists
        if not is_dry_run:
            ldm_dir = paths["root"] / ".liferay-docker"
            orig_pe = ldm_dir / "original-portal-ext.properties"
            if not orig_pe.exists():
                ldm_dir.mkdir(parents=True, exist_ok=True)
                import shutil

                if target_ext.exists():
                    shutil.copy2(target_ext, orig_pe)

        # Copy other common assets (license, configs, xml etc.) from multiple common dirs
        history_file = paths["root"] / ".liferay-docker.deployed"
        history = set()
        if history_file.exists():
            try:
                history = set(history_file.read_text().splitlines())
            except (FileNotFoundError, OSError):
                pass

        has_any_common = False
        for common_dir in paths.get("common_dirs", []):
            if not common_dir.exists():
                continue
            has_any_common = True
            if self.manager.verbose:
                UI.info(f"Checking global assets in: {common_dir}")

            patterns = [
                ("*.xml", paths["deploy"]),
                ("*.lpkg", paths["deploy"]),
                ("*.config", paths["configs"]),
                ("*.cfg", paths["configs"]),
            ]

            use_sidecar = False
            if project_meta:
                use_shared_search = (
                    str(project_meta.get("use_shared_search", "true")).lower() == "true"
                )
                use_sidecar = not use_shared_search

            search_inspect = None
            if not use_sidecar:
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
                    if match.name == "portal-log4j-ext.xml":
                        continue

                    if "elasticsearch" in match.name.lower():
                        if not search_running:
                            dest = target / match.name
                            if dest.exists():
                                dest.unlink()
                            continue

                        if "-REMOTE" not in match.name and "Connection" in match.name:
                            dest = target / match.name
                            if dest.exists():
                                dest.unlink()
                            continue

                        is_es7_file = "elasticsearch7" in match.name
                        is_es8_file = "elasticsearch8" in match.name

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

                    dest_name = match.name.replace("-REMOTE", "")
                    dest = target / dest_name

                    sidecar_conflicts = [
                        "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConfiguration.config",
                        "com.liferay.portal.search.elasticsearch8.configuration.ElasticsearchConfiguration.config",
                        "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConnectionConfiguration.config",
                        "com.liferay.portal.search.elasticsearch8.configuration.ElasticsearchConnectionConfiguration.config",
                    ]

                    if dest_name in sidecar_conflicts:
                        project_id = paths["root"].name
                        use_sidecar = (
                            project_meta
                            and str(
                                project_meta.get("use_shared_search", "true")
                            ).lower()
                            == "false"
                        )

                        if use_sidecar:
                            if dest.exists():
                                dest.unlink()
                            continue

                        content = match.read_text()
                        if not dest.exists() or dest.read_text() != content:
                            from ldm_core.utils import safe_write_text

                            safe_write_text(dest, content)
                            if not dest.exists():
                                history.add(match.name)
                        continue

                    if pattern == "*.xml":
                        new_lic_info = self.manager.license._parse_license_xml(match)
                        if new_lic_info:
                            project_licenses = self.manager.license.find_license(paths)
                            should_copy_license = True
                            if project_licenses:
                                for old_lic in project_licenses:
                                    if "Global" in old_lic.get("location", ""):
                                        continue
                                    if not self.manager.license.is_better_license(
                                        new_lic_info, old_lic
                                    ):
                                        should_copy_license = False
                                        break

                            if should_copy_license:
                                if project_licenses:
                                    for old_lic in project_licenses:
                                        if "Project" in old_lic.get("location", ""):
                                            old_path = Path(old_lic["path"])
                                            if old_path.exists() and old_path != dest:
                                                with contextlib.suppress(OSError):
                                                    old_path.unlink()
                                                    UI.info(
                                                        f"  - Removed conflicting project license: {old_path.name}"
                                                    )
                                atomic_copy(match, dest)
                                history.add(match.name)
                                UI.info(f"  + Synced license from Common: {match.name}")
                            continue

                    if not dest.exists():
                        atomic_copy(match, dest)
                        history.add(match.name)
                    elif match.name in history and (
                        pattern.endswith("config") or pattern.endswith("cfg")
                    ):
                        atomic_copy(match, dest)

        if has_any_common:
            from ldm_core.utils import safe_write_text

            safe_write_text(history_file, "\n".join(sorted(history)))
        else:
            UI.warning(
                "Global or local 'common/' folder not found. Some baseline assets may be missing."
            )
            UI.info(
                f"You can recreate the baseline by running: {UI.CYAN}ldm init-common{UI.COLOR_OFF}"
            )

    def cmd_log_level(self, project_id=None):
        """Manage Liferay internal logging levels via file-based hot-reloading."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return

        # 1. Determine action from args
        bundle = getattr(self.manager.args, "bundle", None)
        category = getattr(self.manager.args, "category", None)
        level = getattr(self.manager.args, "level", None)
        remove = getattr(self.manager.args, "remove", False)
        list_levels = getattr(self.manager.args, "list", False)

        if list_levels:
            logging_json = root / "logging.json"
            if not logging_json.exists():
                UI.info("No custom log levels defined.")
                return
            UI.heading(f"Log Levels for {root.name}")
            print(logging_json.read_text())
            return

        if not category:
            UI.die("Log-level requires --category.")

        # Default bundle to 'portal' if not provided
        if not bundle:
            bundle = "portal"

        if not remove and not level:
            UI.die("Log-level requires --level.")

        # 2. Persistence: Update logging.json
        logging_json = root / "logging.json"
        log_data = {}
        if logging_json.exists():
            with contextlib.suppress(Exception):
                log_data = json.loads(logging_json.read_text())

        if remove:
            if bundle in log_data and category in log_data[bundle]:
                del log_data[bundle][category]
                if not log_data[bundle]:
                    del log_data[bundle]
        else:
            if bundle not in log_data:
                log_data[bundle] = {}
            log_data[bundle][category] = level

        from ldm_core.utils import safe_write_text

        safe_write_text(logging_json, json.dumps(log_data, indent=4))

        # 3. Synchronize to portal-log4j-ext.xml
        paths = self.manager.setup_paths(root)
        self.sync_logging(paths)

        UI.success(
            "Log level updated and persisted. Liferay will hot-reload the changes within 5 seconds."
        )

    def cmd_config(self, key=None, value=None):
        """View or set global LDM configuration."""
        config_path = get_actual_home() / ".ldmrc"
        config = {}
        if config_path.exists():
            with contextlib.suppress(Exception):
                config = json.loads(config_path.read_text())

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
            if getattr(self.manager.args, "remove", False) or value.lower() == "unset":
                config.pop(key, None)
                UI.success(f"Configuration key '{key}' removed.")
            else:
                config[key] = value
                UI.success(f"Configuration key '{key}' set to '{value}'.")

            from ldm_core.utils import safe_write_text

            safe_write_text(config_path, json.dumps(config, indent=4))

    def cmd_defaults(self, key=None, value=None):
        """View or manage cascading configuration defaults."""
        global_level = getattr(self.manager.args, "global_level", False)
        remove = getattr(self.manager.args, "remove", False)
        defaults_mgr = self.manager.defaults

        if remove:
            if not key:
                UI.die("Key required to remove a default.")
            if global_level:
                defaults_mgr.remove_global_default(key)
                UI.success(f"Removed global default '{key}'.")
            else:
                defaults_mgr.remove_user_default(key)
                UI.success(f"Removed user default '{key}'.")
            return

        if key and value:
            if global_level:
                defaults_mgr.set_global_default(key, value)
                UI.success(f"Set global default '{key}' to '{value}'.")
            else:
                defaults_mgr.set_user_default(key, value)
                UI.success(f"Set user default '{key}' to '{value}'.")
            return

        # View defaults
        resolved = defaults_mgr.get_resolved()

        UI.info("\nLDM Cascading Defaults")
        UI.info("======================")
        for k in sorted(resolved.keys()):
            v = resolved[k]
            display_v = str(v)
            if k == "tag" and not v:
                display_v = "<auto-discover latest>"

            if k in defaults_mgr.user_defaults:
                source = f"{UI.CYAN}User{UI.COLOR_OFF}"
            elif k in defaults_mgr.global_defaults:
                source = f"{UI.YELLOW}Global{UI.COLOR_OFF}"
            else:
                source = f"{UI.DIM}Convention{UI.COLOR_OFF}"
            UI.raw(f"  {k.ljust(15)}: {display_v.ljust(20)} [{source}]")
        UI.raw("")

    def cmd_database_mode(self, mode=None):
        """View or change the active database profile (isolated or shared)."""
        global_level = getattr(self.manager.args, "global_level", False)

        if mode is None:
            # Viewing the active database mode
            if global_level:
                db_mode = self.manager.defaults.user_defaults.get("database_mode")
                if db_mode is None:
                    db_mode = self.manager.defaults.global_defaults.get("database_mode")
                if db_mode is None:
                    db_mode = "isolated"
                UI.info(
                    f"Global default database mode is set to: {UI.CYAN}{db_mode}{UI.COLOR_OFF}"
                )
            else:
                # Try to detect local project first
                project_path = self.manager.detect_project_path(None)
                db_mode = None
                if project_path:
                    meta = self.manager.read_meta(project_path)
                    if meta:
                        db_mode = meta.get("database_mode")

                if db_mode is not None:
                    UI.info(
                        f"Project database mode is set to: {UI.CYAN}{db_mode}{UI.COLOR_OFF} (local)"
                    )
                else:
                    # Fallback to resolved defaults
                    db_mode = self.manager.defaults.get("database_mode", "isolated")
                    UI.info(
                        f"Active database mode is: {UI.CYAN}{db_mode}{UI.COLOR_OFF} (from defaults)"
                    )
            return

        # Setting the database mode
        if global_level:
            self.manager.defaults.set_user_default("database_mode", mode)
            UI.success(
                f"Global default database mode set to: {UI.CYAN}{mode}{UI.COLOR_OFF}"
            )
        else:
            project_path = self.manager.detect_project_path(None)
            if not project_path:
                UI.die(
                    "No project context found. Run this within an LDM project or use --global."
                )
            meta = self.manager.read_meta(project_path)
            if not meta:
                meta = {}
            meta["database_mode"] = mode
            self.manager.write_meta(project_path, meta)
            UI.success(f"Project database mode set to: {UI.CYAN}{mode}{UI.COLOR_OFF}")

    def cmd_edit(self, project_id=None, target="meta", tui=False):
        root_path = self.manager.detect_project_path(project_id)
        if not root_path:
            return

        if tui:
            if target != "properties":
                UI.warning(
                    "Interactive TUI is only supported for editing properties. Switching target to 'properties'."
                )
                target = "properties"

            paths = self.manager.setup_paths(root_path)
            pe_path = paths["files"] / "portal-ext.properties"
            with contextlib.suppress(PermissionError, OSError):
                paths["files"].mkdir(exist_ok=True)
            if not pe_path.exists():
                from ldm_core.utils import safe_write_text

                safe_write_text(pe_path, "")

            # Run TUI loop
            while True:
                UI.raw("\n" + "=" * 60)
                UI.raw(
                    f" LDM Interactive Properties Editor (TUI) - Project: {project_id or root_path.name}"
                )
                UI.raw("=" * 60)

                # Load current overrides
                content = pe_path.read_text() if pe_path.exists() else ""
                props, important_keys = self._get_properties_with_metadata(content)

                sorted_keys = sorted(props.keys())

                if sorted_keys:
                    UI.raw("\nCurrent Custom Overrides:")
                    for idx, k in enumerate(sorted_keys, 1):
                        imp_str = " [!important]" if k in important_keys else ""
                        UI.raw(f"  {idx:2d}. {k} = {props[k]}{imp_str}")
                else:
                    UI.raw("\nNo custom property overrides defined yet.")

                UI.raw("\nOptions:")
                UI.raw("  [A] Add a new override")
                if sorted_keys:
                    UI.raw("  [E] Edit an override (by index)")
                    UI.raw("  [D] Delete an override (by index)")
                    UI.raw("  [T] Toggle !important on an override")
                UI.raw("  [R] Rebuild & Sync properties to containers")
                UI.raw("  [Q] Quit / Exit")

                try:
                    choice = input("\nSelect an option: ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    UI.raw("\nExiting TUI.")
                    break

                if choice == "q":
                    break
                if choice == "a":
                    try:
                        key = input("Enter property key: ").strip()
                        if not key or not re.match(r"^[a-zA-Z0-9_.-]+$", key):
                            UI.error("Invalid property key format.")
                            continue
                        val = input("Enter property value: ").strip()
                        imp_choice = (
                            input("Mark as !important? (y/N): ").strip().lower()
                        )
                        is_important = imp_choice in ("y", "yes")

                        important_set = {key} if is_important else None
                        self.update_portal_ext(
                            paths, {key: val}, important_keys=important_set
                        )
                        UI.success(f"Added override: {key}={val}")
                    except Exception as e:
                        UI.error(f"Error adding override: {e}")
                elif choice == "e" and sorted_keys:
                    try:
                        idx_str = input("Enter override index to edit: ").strip()
                        idx = int(idx_str) - 1
                        if idx < 0 or idx >= len(sorted_keys):
                            UI.error("Invalid index.")
                            continue
                        key = sorted_keys[idx]
                        old_val = props[key]
                        old_imp = key in important_keys

                        UI.raw(f"Editing: {key} (current value: {old_val})")
                        val = input(
                            f"Enter new value (press Enter to keep '{old_val}'): "
                        ).strip()
                        if not val:
                            val = old_val

                        imp_choice = (
                            input(f"Mark as !important? (current: {old_imp}) (y/N): ")
                            .strip()
                            .lower()
                        )
                        is_important = (
                            imp_choice in ("y", "yes") if imp_choice else old_imp
                        )

                        important_set = {key} if is_important else None
                        self.update_portal_ext(
                            paths, {key: val}, important_keys=important_set
                        )
                        UI.success(f"Updated override: {key}={val}")
                    except Exception as e:
                        UI.error(f"Error editing override: {e}")
                elif choice == "d" and sorted_keys:
                    try:
                        idx_str = input("Enter override index to delete: ").strip()
                        idx = int(idx_str) - 1
                        if idx < 0 or idx >= len(sorted_keys):
                            UI.error("Invalid index.")
                            continue
                        key = sorted_keys[idx]
                        confirm = (
                            input(
                                f"Are you sure you want to delete override '{key}'? (y/N): "
                            )
                            .strip()
                            .lower()
                        )
                        if confirm in ("y", "yes"):
                            self.remove_portal_ext(paths, {key})
                            UI.success(f"Removed override '{key}'")
                    except Exception as e:
                        UI.error(f"Error deleting override: {e}")
                elif choice == "t" and sorted_keys:
                    try:
                        idx_str = input(
                            "Enter override index to toggle !important: "
                        ).strip()
                        idx = int(idx_str) - 1
                        if idx < 0 or idx >= len(sorted_keys):
                            UI.error("Invalid index.")
                            continue
                        key = sorted_keys[idx]
                        is_important = key not in important_keys

                        # Load all current important keys and toggle this one
                        new_important_keys = set(important_keys)
                        if is_important:
                            new_important_keys.add(key)
                        else:
                            new_important_keys.discard(key)

                        self.update_portal_ext(
                            paths, {key: props[key]}, important_keys=new_important_keys
                        )
                        status_str = "important" if is_important else "not important"
                        UI.success(f"Toggled '{key}' to {status_str}")
                    except Exception as e:
                        UI.error(f"Error toggling !important: {e}")
                elif choice == "r":
                    try:
                        self.cmd_rebuild_properties(project_id)
                        UI.success("Properties rebuilt and synced successfully.")
                    except Exception as e:
                        UI.error(f"Error rebuilding properties: {e}")
                else:
                    UI.error("Invalid option selection.")
            return

        import subprocess

        if target == "meta":
            file_to_edit = root_path / PROJECT_META_FILE
        else:
            file_to_edit = root_path / "files" / "portal-ext.properties"
            with contextlib.suppress(PermissionError, OSError):
                (root_path / "files").mkdir(exist_ok=True)
            if not file_to_edit.exists():
                from ldm_core.utils import safe_write_text

                safe_write_text(file_to_edit, "")

        editor = os.environ.get(
            "EDITOR", "vi" if platform.system() != "Windows" else "notepad"
        )
        try:
            subprocess.run([editor, str(file_to_edit)])
        except Exception as e:
            UI.error(f"Failed to open editor '{editor}': {e}")

    def cmd_rebuild_properties(self, project_id=None):
        """Reconstruct/sync properties cleanly, preserving project customizations."""
        root_path = self.manager.detect_project_path(project_id)
        if not root_path:
            return

        paths = self.manager.setup_paths(root_path)
        project_meta = self.manager.read_meta(root_path) or {}

        # Verify environment first (required to construct runtime/DB host updates properly)
        self.manager.verify_runtime_environment(paths)

        is_dry_run = getattr(self.manager.args, "dry_run", False)

        UI.heading(f"Rebuilding Properties for project: {root_path.name}")
        if is_dry_run:
            UI.info("Running in DRY RUN mode. Showing properties changes:")

        self.sync_common_assets(paths, project_meta=project_meta)

        if not is_dry_run:
            UI.success("Properties successfully rebuilt.")

    def cmd_revert_properties(self, project_id=None):
        """Restore files/portal-ext.properties from original-portal-ext.properties."""
        root_path = self.manager.detect_project_path(project_id)
        if not root_path:
            return

        paths = self.manager.setup_paths(root_path)
        ldm_dir = paths["root"] / ".liferay-docker"
        orig_pe = ldm_dir / "original-portal-ext.properties"
        target_pe = paths["files"] / "portal-ext.properties"

        if not orig_pe.exists():
            UI.die(f"Revert failed: No original properties backup found at {orig_pe}.")
            return

        UI.heading(f"Reverting Properties for project: {root_path.name}")

        import shutil

        try:
            target_pe.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(orig_pe, target_pe)
            UI.success(f"Properties reverted to original version: {target_pe}")
        except Exception as e:
            UI.die(f"Failed to revert properties: {e}")

    def cmd_reset_properties(self, project_id=None):
        """Discard project customizations and rebuild properties purely from layers."""
        root_path = self.manager.detect_project_path(project_id)
        if not root_path:
            return

        paths = self.manager.setup_paths(root_path)
        project_meta = self.manager.read_meta(root_path) or {}

        # Verify environment first
        self.manager.verify_runtime_environment(paths)

        target_pe = paths["files"] / "portal-ext.properties"
        is_dry_run = getattr(self.manager.args, "dry_run", False)

        UI.heading(f"Resetting Properties for project: {root_path.name}")
        if is_dry_run:
            UI.info("Running in DRY RUN mode. Showing properties reset cascade:")

        temp_backup = None
        if target_pe.exists() and not is_dry_run:
            temp_backup = target_pe.with_suffix(".properties.reset_tmp")
            try:
                target_pe.rename(temp_backup)
            except Exception as e:
                UI.die(f"Failed to start properties reset: {e}")
                return

        try:
            if is_dry_run:
                # In dry run, temporarily move out project customizations and run dry-run sync
                os.environ["LDM_DRY_RUN"] = "true"
                if target_pe.exists():
                    temp_backup = target_pe.with_suffix(".properties.reset_tmp")
                    target_pe.rename(temp_backup)
                try:
                    self.sync_common_assets(paths, project_meta=project_meta)
                finally:
                    if temp_backup and temp_backup.exists():
                        temp_backup.rename(target_pe)
                    os.environ.pop("LDM_DRY_RUN", None)
            else:
                self.sync_common_assets(paths, project_meta=project_meta)
                UI.success("Properties successfully reset.")
        finally:
            if temp_backup and temp_backup.exists() and not is_dry_run:
                try:
                    temp_backup.unlink()
                except Exception:
                    pass

    def cmd_env(self, project_id=None):
        pid = project_id
        vars_to_apply = getattr(self.manager.args, "vars", [])
        if vars_to_apply and "=" not in vars_to_apply[0] and not pid:
            test_path = self.manager.detect_project_path(vars_to_apply[0])
            if test_path:
                pid = vars_to_apply.pop(0)

        root_path = self.manager.detect_project_path(pid)
        if not root_path:
            return
        paths = self.manager.setup_paths(root_path)
        project_meta = self.manager.read_meta(paths["root"])

        custom_env_str = project_meta.get("custom_env", "{}")
        try:
            custom_env = json.loads(custom_env_str or "{}")
        except Exception:
            # Fallback for legacy comma-separated string format
            custom_env = {}
            if custom_env_str:
                for pair in custom_env_str.split(","):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        custom_env[k] = v

        if getattr(self.manager.args, "import_env", False):
            for v in self.manager.get_host_passthrough_env(paths):
                if "=" in v:
                    k, val = v.split("=", 1)
                    custom_env[k] = val
            vars_to_apply = []

        if not vars_to_apply:
            if self.manager.non_interactive:
                # In non-interactive mode, if no vars are provided, we default to --import
                # This enables 'ldm env <pid> -y' to sync all shell vars automatically.
                UI.info(
                    "No specific variables provided. Syncing all passthrough shell vars..."
                )
                for v in self.manager.get_host_passthrough_env(paths):
                    if "=" in v:
                        k, val = v.split("=", 1)
                        custom_env[k] = val
            else:
                UI.heading("Environment Variables")
                if custom_env:
                    for k, v in sorted(custom_env.items()):
                        print(f"  {k}={v}")
                passthroughs = self.manager.get_host_passthrough_env(paths)
                if passthroughs:
                    UI.info("\nHost Passthrough:")
                    for v in passthroughs:
                        print(f"  {v}")

                key = UI.ask("\nEnter Key (or Enter to apply current shell vars)")
                if not key:
                    # Apply shell vars as promised by the prompt
                    for v in self.manager.get_host_passthrough_env(paths):
                        if "=" in v:
                            k, val = v.split("=", 1)
                            custom_env[k] = val
                    project_meta["custom_env"] = json.dumps(custom_env)
                    self.manager.write_meta(paths["root"], project_meta)
                    self.manager.sync_stack(
                        paths, project_meta, no_up=True, no_wait=True
                    )
                    UI.success("Environment variables synchronized from shell.")
                    return

                if "=" in key:
                    k, v = key.split("=", 1)
                    custom_env[k] = v
                else:
                    value = UI.ask(f"Enter Value for {key}")
                    custom_env[key] = value

        if vars_to_apply:
            # Batch update from CLI
            for pair in vars_to_apply:
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if getattr(self.manager.args, "remove", False):
                        custom_env.pop(k, None)
                    else:
                        custom_env[k] = v
                elif getattr(self.manager.args, "remove", False):
                    custom_env.pop(pair, None)

        # Final Commit
        project_meta["custom_env"] = json.dumps(custom_env)
        self.manager.write_meta(paths["root"], project_meta)
        self.manager.sync_stack(paths, project_meta, no_up=True, no_wait=True)
        UI.success("Environment updated.")

    def cmd_feature(self, project_id=None, enable=None, disable=None):
        """View or manage enabled feature flags for a project."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            UI.die(f"Project '{project_id}' not found.")

        meta = self.manager.read_meta(root)
        current_features = meta.get("features", "").split(",")
        current_features = [f.strip() for f in current_features if f.strip()]

        if enable or disable:
            features_set = set(current_features)
            if enable:
                for f in enable:
                    # Flatten comma-separated input
                    for x in f.split(","):
                        if x.strip():
                            features_set.add(x.strip())

            if disable:
                for f in disable:
                    for x in f.split(","):
                        if x.strip() in features_set:
                            features_set.remove(x.strip())

            meta["features"] = ",".join(sorted(features_set))
            self.manager.write_meta(root, meta)
            UI.success("Project metadata updated with new feature flags.")
            UI.info(
                "Run 'ldm run' or 'ldm deploy' to apply changes to portal-ext.properties."
            )
            current_features = list(features_set)

        UI.heading(f"Feature Flags for '{root.name}'")
        if not current_features:
            UI.info("No feature flags explicitly enabled for this project.")
        else:
            for f in sorted(current_features):
                if f.lower() in ["dev", "beta", "release"]:
                    UI.raw(
                        f"  {UI.GREEN}✓{UI.COLOR_OFF} {UI.WHITE}{f}{UI.COLOR_OFF} (UI Visibility)"
                    )
                else:
                    UI.raw(f"  {UI.GREEN}✓{UI.COLOR_OFF} {UI.WHITE}{f}{UI.COLOR_OFF}")

        UI.raw("")

    def track_roi(self, seconds_saved: int, activity: str) -> tuple[int, int]:
        """Tracks the ROI by adding the saved time to the global metrics."""
        config = self.get_global_config()
        cumulative = int(config.get("roi_seconds_saved", 0))
        cumulative += seconds_saved
        self.set_global_config("roi_seconds_saved", cumulative)

        from ldm_core.ui import UI

        run_formatted = UI.format_duration(seconds_saved)
        total_formatted = UI.format_duration(cumulative)
        UI.success(
            f"✨ LDM {activity} saved you {UI.BOLD}{run_formatted}{UI.COLOR_OFF} of manual work!"
        )
        UI.detail(
            f"📈 Cumulative developer time saved: {UI.CYAN}{total_formatted}{UI.COLOR_OFF}"
        )

        return seconds_saved, cumulative

    def cmd_roi(self):
        """Displays cumulative time saved using LDM."""
        from ldm_core.ui import UI

        if getattr(self.manager.args, "reset", False):
            self.set_global_config("roi_seconds_saved", 0)
            UI.success("ROI metrics reset successfully.")
            return

        config = self.get_global_config()
        cumulative = int(config.get("roi_seconds_saved", 0))
        formatted_time = UI.format_duration(cumulative)

        UI.heading("LDM Developer Productivity ROI")
        UI.raw(
            f"  ● {UI.WHITE}Cumulative Time Saved: {UI.GREEN}{UI.BOLD}{formatted_time}{UI.COLOR_OFF}"
        )
        UI.raw("")
        UI.detail("This metric is aggregated automatically from high-value actions:")
        UI.detail("  - Workspace Seeding / First Boot (saves 14 minutes)")
        UI.detail("  - Snapshot Restore / Import (saves 5 minutes)")
        UI.detail("  - Local Tunnel sharing (saves 3 minutes)")
        UI.raw("")

    def cmd_ssl_mode(self, mode, project_id=None, subdomain=None, domain=None):
        """Switch project network routing between hosts-based SSL and share tunnel."""
        from ldm_core.ui import UI

        root_path = self.manager.detect_project_path(project_id)
        if not root_path:
            return

        project_name = root_path.name
        paths = self.manager.setup_paths(root_path)
        project_meta = self.manager.read_meta(root_path) or {}

        # 1. Determine running state
        is_running = False
        container_name = (
            project_meta.get("liferay_container_name")
            or project_meta.get("container_name")
            or project_name
        )
        try:
            import subprocess

            inspect_res = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
                capture_output=True,
                text=True,
                check=False,
            )
            is_running = inspect_res.stdout.strip() == "true"
        except Exception:
            is_running = False

        UI.heading(f"Switching SSL Mode to '{mode}' for project: {project_name}")

        # If running, stop (unless --no-restart is passed)
        no_restart = getattr(self.manager.args, "no_restart", False)
        if is_running and not no_restart:
            UI.warning(
                f"Stopping running project container stack '{project_name}' (downtime initiated)..."
            )
            self.manager.cmd_stop(project_id=project_name)

        if mode == "hosts":
            # Switch to local hosts-based SSL
            project_meta["ssl"] = "true"
            # Keep existing host_name if it is not localhost/empty, otherwise default
            existing_host = project_meta.get("host_name", "localhost")
            if existing_host == "localhost":
                project_meta["host_name"] = f"{project_name}.local"

            self.manager.write_meta(root_path, project_meta)

            # Synchronize environment files (.env)
            target_url = f"https://{project_meta['host_name']}"
            self._sync_env_files(root_path, target_url)
            UI.info(
                "Tip: You can run 'ldm system fix-hosts' to update your local /etc/hosts file if needed."
            )

        elif mode == "share":
            # Switch to public share tunnel
            project_meta["ssl"] = "false"
            project_meta["host_name"] = "localhost"

            if subdomain:
                project_meta["share_subdomain"] = subdomain
            if domain:
                project_meta["share_domain"] = domain

            self.manager.write_meta(root_path, project_meta)

            # Determine public URL
            sub = subdomain or project_meta.get("share_subdomain") or project_name
            dom = domain or project_meta.get("share_domain") or "lfr-demo.online"
            public_url = f"https://{sub}.{dom}"

            # Synchronize environment files (.env)
            self._sync_env_files(root_path, public_url)

        # 2. Rebuild properties
        self.cmd_rebuild_properties(project_name)

        # 3. Start back up if it was running and not --no-restart
        if is_running and not no_restart:
            UI.info(
                f"Starting project container stack '{project_name}' in mode '{mode}'..."
            )
            self.manager.cmd_run(project_id=project_name)

    def _sync_env_files(self, project_path, target_url):
        """Find and update LIFERAY_URL/LIFERAY_PORTAL_URL/AICA_LIFERAY_URL in client extension .env files."""
        from ldm_core.ui import UI

        env_files = list(project_path.rglob(".env"))
        if not env_files:
            return

        import re

        url_patterns = [
            re.compile(r"^(LIFERAY_URL\s*=\s*).*$", re.MULTILINE),
            re.compile(r"^(LIFERAY_PORTAL_URL\s*=\s*).*$", re.MULTILINE),
            re.compile(r"^(AICA_LIFERAY_URL\s*=\s*).*$", re.MULTILINE),
        ]

        updated_count = 0
        for env_file in env_files:
            try:
                content = env_file.read_text(encoding="utf-8")
                new_content = content
                for pattern in url_patterns:
                    new_content = pattern.sub(rf"\g<1>{target_url}", new_content)
                if new_content != content:
                    env_file.write_text(new_content, encoding="utf-8")
                    updated_count += 1
            except Exception as e:
                UI.debug(f"Failed to update env file {env_file}: {e}")

        if updated_count > 0:
            UI.success(
                f"Synchronized Liferay URL configuration in {updated_count} local environment file(s) to: {target_url}"
            )
