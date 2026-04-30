import os
import platform
import re
import signal
import sys
from pathlib import Path

from ldm_core.constants import VERSION
from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI


class DevHandler(BaseHandler):
    """Handler for development-only utilities (versioning, internal tools)."""

    def cmd_dev_setup(self):
        """Initializes the local development environment (venv, dependencies, hooks)."""
        self._ensure_dev_env()

        UI.heading("LDM Developer Environment Setup")
        root = Path.cwd()

        # 1. Create Virtual Environment
        venv_dir = root / ".venv"
        if not venv_dir.exists():
            UI.info("Creating virtual environment (.venv)...")
            from ldm_core.utils import run_command

            run_command([sys.executable, "-m", "venv", ".venv"])
            UI.success("Virtual environment created.")
        else:
            UI.info("Virtual environment already exists.")

        # 2. Identify venv python/pip
        if platform.system().lower() == "windows":
            venv_python = venv_dir / "Scripts" / "python.exe"
            venv_pip = venv_dir / "Scripts" / "pip.exe"
        else:
            venv_python = venv_dir / "bin" / "python3"
            venv_pip = venv_dir / "bin" / "pip"

        if not venv_python.exists():
            UI.die(f"Could not find python in venv: {venv_python}")

        # 3. Install Dependencies
        UI.info("Installing dependencies...")
        from ldm_core.utils import run_command

        run_command([str(venv_pip), "install", "--upgrade", "pip"])
        run_command([str(venv_pip), "install", "-r", "requirements.txt"])
        run_command([str(venv_pip), "install", "-r", "requirements-dev.txt"])
        run_command([str(venv_pip), "install", "-e", "."])
        UI.success("Dependencies installed.")

        # 4. Install pre-commit hooks
        UI.info("Registering pre-commit hooks...")
        run_command([str(venv_python), "-m", "pre-commit", "install"])
        UI.success("Pre-commit hooks registered.")

        UI.success("\n✅ Development environment is ready!")
        if platform.system().lower() == "windows":
            UI.info(
                f"To activate, run: {UI.CYAN}.\\.venv\\Scripts\\activate{UI.COLOR_OFF}"
            )
        else:
            UI.info(
                f"To activate, run: {UI.CYAN}source .venv/bin/activate{UI.COLOR_OFF}"
            )

    def _ensure_dev_env(self):
        """Verifies that we are running in a git clone with source files available."""
        if (
            not (Path.cwd() / ".git").exists()
            or not (Path.cwd() / "pyproject.toml").exists()
        ):
            UI.die(
                "Action restricted: This command can only be run from the root of a git clone."
            )

        if os.getenv("LDM_DEV_MODE") != "true" and not getattr(self.args, "yes", False):
            UI.warning("Internal Developer Utility detected.")
            if not UI.confirm("Continue in Developer Mode?", "N"):
                sys.exit(0)

    def cmd_version(
        self,
        bump_type=None,
        promote=False,
        set_version=None,
        build_info=None,
        check=False,
        print_only=False,
    ):
        """Manages LDM versioning and release tiers."""
        if print_only:
            print(VERSION)
            return

        self._ensure_dev_env()

        current_version = VERSION

        if check:
            UI.info("Checking version synchronization...")
            p_toml = Path.cwd() / "pyproject.toml"
            if p_toml.exists():
                match = re.search(r'version = "(.*?)"', p_toml.read_text())
                if match and match.group(1) != current_version:
                    UI.die(
                        f"Version Mismatch: constants.py ({current_version}) != pyproject.toml ({match.group(1)})"
                    )
            UI.success("Versions are synchronized.")
            return

        if set_version:
            new_version = set_version.lstrip("v")
            UI.info(f"Setting version to: {UI.GREEN}v{new_version}{UI.COLOR_OFF}")
            self._apply_version_update(new_version, build_info)
            return

        if not bump_type and not promote:
            UI.info(
                f"Current Version: {UI.CYAN}v{current_version}{UI.COLOR_OFF}{UI.get_beta_label(current_version)}"
            )
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
        elif bump_type in ["beta", "pre"]:
            if pre_release and re.search(r"(beta|pre)", pre_release):
                # Increment pre-release number
                pre_match = re.search(r"(\d+)", pre_release)
                pre_num = int(pre_match.group(1)) if pre_match else 0
                # Preserve the existing prefix (beta or pre)
                prefix_match = re.search(r"(beta|pre)", pre_release)
                prefix = prefix_match.group(1) if prefix_match else "pre"
                new_version = f"{base_version}-{prefix}.{pre_num + 1}"
            else:
                # Start new pre-release cycle for next patch
                new_version = f"{major}.{minor}.{patch + 1}-pre.1"
        else:
            UI.die(f"Invalid bump type: {bump_type}")

        UI.info(
            f"Target Version:  {UI.GREEN}v{new_version}{UI.COLOR_OFF}{UI.get_beta_label(new_version)}"
        )

        if not UI.confirm(f"Update all source files to v{new_version}?", "Y"):
            UI.info("Aborted.")
            return

        self._apply_version_update(new_version, build_info)

    def _apply_version_update(self, new_version, build_info=None):
        """Atomicly updates all files containing the version string."""
        files_to_update = {
            "ldm_core/constants.py": [
                (r'^VERSION = ".*?"', f'VERSION = "{new_version}"'),
                (r"LDM_MAGIC_VERSION: .*", f"LDM_MAGIC_VERSION: {new_version}"),
            ],
            "pyproject.toml": [(r'^version = ".*?"', f'version = "{new_version}"')],
        }

        if build_info:
            files_to_update["ldm_core/constants.py"].append(
                (r"BUILD_INFO = .*", f'BUILD_INFO = "{build_info}"')
            )

        # CHANGELOG Management
        changelog_path = Path.cwd() / "CHANGELOG.md"
        if changelog_path.exists():
            from datetime import datetime

            today = datetime.now().strftime("%Y-%m-%d")
            content = changelog_path.read_text()
            header = f"## [v{new_version}] - {today}"

            if header not in content:
                UI.info("Prepending version header to CHANGELOG.md...")
                # Insert after the initial boilerplate (first few lines)
                lines = content.splitlines()
                insert_idx = 0
                for i, line in enumerate(lines):
                    if line.startswith("## [v"):
                        insert_idx = i
                        break

                if insert_idx == 0:
                    # Fallback: append after the intro text
                    new_block = f"\n{header}\n\n### Added\n\n- \n"
                    content = content.replace(
                        "Semantic Versioning](https://semver.org/spec/v2.0.0.html).",
                        f"Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n{new_block}",
                    )
                else:
                    new_block = f"{header}\n\n### Added\n\n- \n\n"
                    lines.insert(insert_idx, new_block)
                    content = "\n".join(lines).strip() + "\n"
                    # Final safety: remove trailing spaces from the empty list item
                    content = content.replace("- \n", "-\n")

                changelog_path.write_text(content)
                UI.success("Updated CHANGELOG.md")

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
                    new_content = re.sub(
                        pattern, replacement, new_content, flags=re.MULTILINE
                    )

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
