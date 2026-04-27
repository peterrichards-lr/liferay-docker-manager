import os
import re
import signal
import sys
from pathlib import Path
from ldm_core.ui import UI
from ldm_core.handlers.base import BaseHandler
from ldm_core.constants import VERSION


class DevHandler(BaseHandler):
    """Handler for development-only utilities (versioning, internal tools)."""

    def _ensure_dev_env(self):
        """Verifies that we are running in a git clone with source files available."""
        if (
            not (Path.cwd() / ".git").exists()
            or not (Path.cwd() / "pyproject.toml").exists()
        ):
            UI.die(
                "Action restricted: This command can only be run from the root of a git clone."
            )

        if os.getenv("LDM_DEV_MODE") != "true" and not self.args.yes:
            UI.warning("Internal Developer Utility detected.")
            if not UI.confirm("Continue in Developer Mode?", "N"):
                sys.exit(0)

    def cmd_version(self, bump_type=None, promote=False):
        """Manages LDM versioning and release tiers."""
        self._ensure_dev_env()

        current_version = VERSION
        UI.info(
            f"Current Version: {UI.CYAN}v{current_version}{UI.COLOR_OFF}{UI.get_beta_label(current_version)}"
        )

        if not bump_type and not promote:
            return

        # 1. Parse Version
        # SemVer: major.minor.patch[-beta.x]
        parts = current_version.split("-", 1)
        base_version = parts[0]
        pre_release = parts[1] if len(parts) > 1 else None

        base_parts = list(map(int, base_version.split(".")))
        while len(base_parts) < 3:
            base_parts.append(0)

        major, minor, patch = base_parts

        # 2. Logic for Bumping
        if promote:
            if not pre_release:
                UI.die("Cannot promote: Current version is already a stable release.")
            UI.info(
                f"Promoting {UI.YELLOW}{current_version}{UI.COLOR_OFF} to stable..."
            )
            new_version = base_version
        elif bump_type == "major":
            new_version = f"{major + 1}.0.0"
        elif bump_type == "minor":
            new_version = f"{major}.{minor + 1}.0"
        elif bump_type == "patch":
            new_version = f"{major}.{minor}.{patch + 1}"
        elif bump_type == "beta":
            if pre_release and "beta" in pre_release:
                # Increment beta number
                beta_num = int(re.search(r"\d+", pre_release).group())
                new_version = f"{base_version}-beta.{beta_num + 1}"
            else:
                # Start new beta cycle for next patch
                new_version = f"{major}.{minor}.{patch + 1}-beta.1"
        else:
            UI.die(f"Invalid bump type: {bump_type}")

        UI.info(
            f"Target Version:  {UI.GREEN}v{new_version}{UI.COLOR_OFF}{UI.get_beta_label(new_version)}"
        )

        if not UI.confirm(f"Update all source files to v{new_version}?", "N"):
            UI.info("Aborted.")
            return

        self._apply_version_update(new_version)

    def _apply_version_update(self, new_version):
        """Atomicly updates all files containing the version string."""
        files_to_update = {
            "ldm_core/constants.py": [
                (r'VERSION = ".*?"', f'VERSION = "{new_version}"'),
                (r"LDM_MAGIC_VERSION: .*", f"LDM_MAGIC_VERSION: {new_version}"),
            ],
            "pyproject.toml": [(r'version = ".*?"', f'version = "{new_version}"')],
        }

        # Setup Signal Handling for Atomicity
        def signal_handler(sig, frame):
            UI.error("\nInterrupted! Cleaning up...")
            sys.exit(1)

        signal.signal(signal.SIGINT, signal_handler)

        updated_paths = []
        try:
            for rel_path, patterns in files_to_update.items():
                p = Path.cwd() / rel_path
                if not p.exists():
                    continue

                content = p.read_text()
                new_content = content
                for pattern, replacement in patterns:
                    new_content = re.sub(pattern, replacement, new_content)

                if new_content == content:
                    UI.warning(f"No changes made to {rel_path} (Pattern mismatch?)")
                    continue

                # Atomic Write
                temp_file = p.with_suffix(".tmp")
                temp_file.write_text(new_content)
                os.replace(temp_file, p)
                updated_paths.append(rel_path)
                UI.success(f"Updated {rel_path}")

            UI.info(
                f"\n✅ Successfully updated to {UI.BOLD}v{new_version}{UI.COLOR_OFF}"
            )
            UI.info("Note: Don't forget to commit and tag this change.")
        except Exception as e:
            UI.error(f"Failed to update versions: {e}")
            sys.exit(1)
