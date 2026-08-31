import os
import platform
import re
import shutil
import sys
from pathlib import Path

from ldm_core.constants import VERSION
from ldm_core.ui import UI


class DevService:
    """Service for development-only utilities (versioning, internal tools)."""

    def __init__(self, manager=None):
        self.manager = manager

    def cmd_dev_setup(self):
        """Initializes the local development environment (venv, dependencies, hooks)."""
        self._ensure_dev_env()

        UI.heading("LDM Developer Environment Setup")
        root = Path.cwd()

        # 1. Create Virtual Environment
        venv_dir = root / ".venv"
        if not venv_dir.exists():
            UI.detail("Creating virtual environment (.venv)...")
            from ldm_core.utils import run_command

            run_command([sys.executable, "-m", "venv", ".venv"])
            UI.success("Virtual environment created.")
        else:
            UI.detail("Virtual environment already exists.")

        # 2. Identify venv python
        if platform.system().lower() == "windows":
            venv_python = venv_dir / "Scripts" / "python.exe"
        else:
            venv_python = venv_dir / "bin" / "python3"

        if not venv_python.exists():
            UI.die(f"Could not find python in venv: {venv_python}")

        # 3. Install Dependencies
        UI.detail("Installing dependencies...")
        from ldm_core.utils import run_command

        # LDM-#1245: invoke pip as a module rather than via the generated
        # `.venv/bin/pip` console script. That wrapper is not reliably present
        # -- endpoint protection removes it by name after installation, leaving
        # pip itself intact -- so calling it made `ldm dev-setup` fail at its
        # first step on exactly the machines whose environment needed repairing.
        # `python -m pip` is also the form pip itself documents for upgrades,
        # since replacing pip's own wrapper mid-run is unreliable on Windows.
        pip = [str(venv_python), "-m", "pip"]
        run_command([*pip, "install", "--upgrade", "pip"])
        run_command([*pip, "install", "-r", "requirements.txt"])
        run_command([*pip, "install", "-r", "requirements-dev.txt"])
        run_command([*pip, "install", "-e", "."])
        UI.success("Dependencies installed.")

        # 4. Install pre-commit hooks
        UI.detail("Registering pre-commit and pre-push hooks...")
        run_command(
            [
                str(venv_python),
                "-m",
                "pre_commit",
                "install",
                "--hook-type",
                "pre-commit",
                "--hook-type",
                "pre-push",
            ]
        )
        UI.success("Pre-commit and pre-push hooks registered.")

        UI.success("Development environment is ready!")
        if platform.system().lower() == "windows":
            UI.detail(
                f"To activate, run: {UI.CYAN}.\\.venv\\Scripts\\activate{UI.COLOR_OFF}"
            )
        else:
            UI.detail(
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

        if os.getenv("LDM_DEV_MODE") != "true":
            # If -y/--non-interactive was explicitly passed, we allow it.
            if getattr(self.manager.args, "non_interactive", False):
                pass
            elif self.manager.non_interactive:
                UI.die(
                    "Error: Developer utility requires LDM_DEV_MODE=true or -y/--non-interactive."
                )
            else:
                UI.warning("Internal Developer Utility detected.")
                if not UI.confirm("Continue in Developer Mode?", "N"):
                    sys.exit(0)

    def _version_from_disk(self):
        """Reads VERSION from constants.py on disk, falling back to the import.

        LDM-#1290: reading the imported `VERSION` is not safe here. Python
        validates cached bytecode on *(source mtime in whole seconds, source
        size)*, and a bump such as `2.16.0-pre.1` -> `2.16.0-pre.2` changes
        neither -- so a second bump within the same second reuses stale
        bytecode and the imported constant lags what is actually on disk. The
        bump then computes a replacement the file already contains, rewrites
        nothing, and reports success.

        `scripts/release.py` reads this same value to decide what to **tag**,
        and tags are immutable (the Burn Rule), so a stale read there burns a
        version number permanently. The writer in `_apply_version_update`
        already works against the file, so parsing it here makes reader and
        writer agree by construction -- the same approach
        `scripts/check_version_sync.py` takes.

        Falls back to the imported constant when the source is unavailable,
        e.g. in a PyInstaller build where there is no `constants.py` on disk.
        """
        path = Path.cwd() / "ldm_core" / "constants.py"
        try:
            match = re.search(
                r'^VERSION = "([^"]+)"', path.read_text(encoding="utf-8"), re.MULTILINE
            )
            if match:
                return match.group(1)
        except OSError:
            pass
        return VERSION

    def cmd_version(  # noqa: C901, PLR0912
        self,
        bump_type=None,
        promote=False,
        set_version=None,
        build_info=None,
        check=False,
        print_only=False,
    ):
        """Manages LDM versioning and release tiers."""
        if print_only or (
            not any([bump_type, set_version, build_info, promote, check])
        ):
            print(self._version_from_disk())
            return

        self._ensure_dev_env()

        current_version = self._version_from_disk()

        if check:
            UI.detail("Checking version synchronization...")
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
            UI.detail(f"Setting version to: {UI.GREEN}v{new_version}{UI.COLOR_OFF}")
            self._apply_version_update(new_version, build_info)
            return

        if not bump_type and not promote:
            UI.detail(
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
            UI.detail(
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
        # LDM-#1291: `beta` only ever opens the *next patch* cycle, so a minor
        # or major release had no pre-release path at all -- it could only be
        # cut straight to stable. That conflicts with the mandate that every
        # release is exercised as a pre-release before the wider user community
        # sees it, so features warranting a minor bump were forced to choose
        # between the correct version number and being tested first.
        #
        # These open the cycle; subsequent increments use `beta` as usual,
        # which matches on the `-pre.N` suffix and bumps N regardless of which
        # component started the cycle.
        elif bump_type == "preminor":
            new_version = f"{major}.{minor + 1}.0-pre.1"
        elif bump_type == "premajor":
            new_version = f"{major + 1}.0.0-pre.1"
        else:
            UI.die(f"Invalid bump type: {bump_type}")

        UI.detail(
            f"Target Version:  {UI.GREEN}v{new_version}{UI.COLOR_OFF}{UI.get_beta_label(new_version)}"
        )

        if not UI.confirm(f"Update all source files to v{new_version}?", "Y"):
            UI.info("Aborted.")
            return

        self._apply_version_update(new_version, build_info)

    def _apply_version_update(self, new_version, build_info=None):
        """Atomicly updates all files containing the version string."""
        from datetime import datetime

        # roff convention for .TH is a month-and-year, not an ISO date.
        man_date = datetime.now().strftime("%B %Y")

        files_to_update = {
            "ldm_core/constants.py": [
                (r'^VERSION = ".*?"', f'VERSION = "{new_version}"'),
                (r"LDM_MAGIC_VERSION: .*", f"LDM_MAGIC_VERSION: {new_version}"),
            ],
            "pyproject.toml": [
                (r'^version = ".*?"', f'version = "{new_version}"'),
                (r"LDM_MAGIC_VERSION: .*", f"LDM_MAGIC_VERSION: {new_version}"),
            ],
            "scripts/verify_e2e_refactor.sh": [
                (r'^SCRIPT_VERSION=".*?"', f'SCRIPT_VERSION="{new_version}"'),
                (r"LDM_MAGIC_VERSION: .*", f"LDM_MAGIC_VERSION: {new_version}"),
            ],
            "scripts/verify_e2e_refactor.ps1": [
                (
                    r'^\$SCRIPT_VERSION = ".*?"',
                    f'$SCRIPT_VERSION = "{new_version}"',
                ),
                (r"LDM_MAGIC_VERSION: .*", f"LDM_MAGIC_VERSION: {new_version}"),
            ],
            # LDM-#1482: the man page ships inside the binary and is installed
            # into the user's man directory, but nothing stamped it -- it sat
            # at 2.15.22 while we shipped 2.19, because no guard knew it
            # existed.
            "ldm_core/resources/ldm.1": [
                (
                    r'^\.TH LDM 1 "[^"]*" "[^"]*"',
                    f'.TH LDM 1 "{man_date}" "{new_version}"',
                ),
            ],
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
                UI.detail("Prepending version header to CHANGELOG.md...")
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
                    new_block = f"{header}\n\n### Added\n\n- \n"
                    lines.insert(insert_idx, new_block)
                    content = "\n".join(lines).strip() + "\n"
                    # Final safety: remove trailing spaces from the empty list item
                    content = content.replace("- \n", "-\n")

                changelog_path.write_text(content)
                UI.success("Updated CHANGELOG.md")

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
                shutil.move(str(temp_file), str(p))
                updated_paths.append(rel_path)
                UI.success(f"Updated {rel_path}")

            if not updated_paths:
                # Every target file was already at this value, so nothing
                # happened. Previously this only emitted per-file warnings and
                # still exited 0, which is how a stale read (LDM-#1290) could
                # pass for a completed bump.
                UI.die(
                    f"Version update to v{new_version} changed no files. "
                    "Every target already contained this value -- the version "
                    "was most likely read stale. Refusing to report success.",
                    exit_code=1,
                )

            UI.detail(
                f"\n✅ Successfully updated to {UI.BOLD}v{new_version}{UI.COLOR_OFF}"
            )
            UI.detail("Note: Don't forget to commit and tag this change.")
        except Exception as e:
            UI.error(f"Failed to update versions: {e}")
            sys.exit(1)
