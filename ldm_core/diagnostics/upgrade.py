import os
import platform
import subprocess
import sys
from pathlib import Path

from ldm_core.constants import VERSION
from ldm_core.diagnostics.completions import is_completion_enabled
from ldm_core.ui import UI
from ldm_core.utils import (
    check_for_updates,
    safe_move,
    version_to_tuple,
)


def run_update_check(handler, force=True):
    UI.heading("LDM Update Check")
    latest, url = check_for_updates(VERSION, force=force)
    if not latest:
        UI.error("Could not reach GitHub to check for updates.")
        return

    if version_to_tuple(latest) <= version_to_tuple(VERSION):
        UI.success(f"You are up to date! (v{VERSION})")
    else:
        print(f"{UI.BYELLOW}[!] A new version is available: v{latest}{UI.COLOR_OFF}")
        print(f"    Current version: v{VERSION}")
        print(f"    Download: {UI.CYAN}{url}{UI.COLOR_OFF}\n")


def _get_manual_upgrade_cmd(handler, url, exe_path):
    """Generates a platform-appropriate manual download and install command."""
    system = platform.system().lower()
    if system in ["win32", "windows"]:
        return f'Invoke-WebRequest -Uri "{url}" -OutFile "{exe_path}"'
    # Check if parent directory is writable to decide on sudo
    try:
        parent_writable = os.access(exe_path.parent, os.W_OK)
    except Exception:
        parent_writable = False
    prefix = "sudo " if not parent_writable else ""
    return f'{prefix}curl -L "{url}" -o "{exe_path}" && {prefix}chmod +x "{exe_path}"'


def run_upgrade(handler):  # noqa: C901, PLR0911, PLR0912, PLR0915
    """Self-upgrade the LDM binary to the latest version."""
    UI.heading("LDM Self-Upgrade")
    is_repair = getattr(handler.manager.args, "repair", False)
    pre_release = getattr(handler.manager.args, "pre_release", False)
    target_version_str = getattr(handler.manager.args, "version", None)

    if is_repair and target_version_str:
        UI.die("Cannot specify both --repair and --version. Please choose one.")

    if target_version_str:
        import re

        semver_regex = re.compile(r"^v?\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")
        if not semver_regex.match(target_version_str):
            UI.die(
                f"Invalid version format: '{target_version_str}'. Must be a valid semantic version (e.g. v2.11.53 or 2.11.53)."
            )

    if is_repair:
        latest = VERSION
        system = platform.system().lower()
        machine = platform.machine().lower()
        target_asset = "ldm-linux"
        if system == "darwin":
            # Architecture-aware naming
            if machine == "arm64":
                target_asset = "ldm-macos-arm64"
            else:
                target_asset = "ldm-macos-x86_64"
        elif system in ["win32", "windows"]:
            target_asset = "ldm-windows.exe"
        url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{VERSION}/{target_asset}"
    else:
        # 1. Check for updates / specific version tag
        if target_version_str:
            latest, url = check_for_updates(VERSION, force=True, tag=target_version_str)
            if not latest:
                UI.die(f"Version '{target_version_str}' not found on GitHub Releases.")
        else:
            latest, url = check_for_updates(
                VERSION, force=True, pre_release=pre_release
            )

            if not latest:
                UI.error("Failed to check for updates.")
                UI.detail("Please check your internet connection or try again later.")
                return

        is_beta = "-" in VERSION
        check_only = getattr(handler.manager.args, "check_only", False)

        # Only check "up to date" if NOT specifically targeting a version
        if not target_version_str and version_to_tuple(latest) <= version_to_tuple(
            VERSION
        ):
            tier = " (stable)" if not pre_release else " (pre-release)"
            UI.success(f"LDM is already up to date v{VERSION}{tier}.")
            return

        if check_only:
            if target_version_str:
                UI.detail(f"Target version v{latest} is available.")
            else:
                UI.detail(
                    f"A new version of LDM is available: {UI.GREEN}v{latest}{UI.COLOR_OFF}"
                )
            UI.detail(f"Run {UI.CYAN}ldm upgrade{UI.COLOR_OFF} to install it.")
            return

        if is_beta and not pre_release and not target_version_str:
            # User is on beta but wants stable (Switching Tiers)
            UI.detail(
                f"You are currently on a beta build ({UI.BYELLOW}v{VERSION}{UI.COLOR_OFF})."
            )
            UI.detail(
                f"The latest stable version is {UI.GREEN}v{latest}{UI.COLOR_OFF}."
            )
            if not UI.confirm("Switch back to the stable release tier?", "N"):
                return

    is_downgrade = version_to_tuple(latest) < version_to_tuple(VERSION)

    if is_repair:
        UI.detail(f"Repairing current version: v{latest}")
    elif is_downgrade:
        UI.detail(f"Target version (downgrade): v{latest}")
    else:
        UI.detail(f"New version found: v{latest}")

    if not url or not url.startswith("http"):
        UI.die("Download URL not found for your architecture.")

    if is_downgrade:
        UI.warning(
            f"Downgrading from v{VERSION} to v{latest} may not support properties hierarchy features or metadata formats of your current LDM setups."
        )
        UI.warning("This could cause project registry or schema incompatibility.")
        if handler.manager.non_interactive:
            if not getattr(handler.manager.args, "force", False):
                UI.die(
                    "Downgrade aborted: --force is required in non-interactive mode."
                )
        elif not UI.confirm(
            "Are you sure you want to proceed with the downgrade?", "N"
        ):
            UI.info("Operation aborted.")
            return
    else:
        prompt = (
            f"Repair v{latest}?"
            if is_repair
            else f"Upgrade from v{VERSION} to v{latest}?"
        )
        if not handler.manager.non_interactive and not UI.confirm(prompt, "Y"):
            UI.info("Operation aborted.")
            return

    # 2. Preparation
    exe_path = Path(sys.argv[0]).resolve()
    if exe_path.suffix.lower() == ".py":
        UI.die(
            "Self-upgrade is only supported for standalone binaries. Please use 'git pull' for source installations."
        )

    import tempfile

    # Download to a system temporary directory that is always writable
    tmp_fd, tmp_path_str = tempfile.mkstemp(prefix="ldm-upgrade-")
    temp_new = Path(tmp_path_str)
    os.close(tmp_fd)

    # 3. Download
    UI.detail(f"Downloading v{latest}...")
    try:
        import requests

        response = requests.get(
            url, headers={"User-Agent": "ldm-cli"}, timeout=30, stream=True
        )
        response.raise_for_status()
        from ldm_core.utils import Benchmarker

        with Benchmarker.measure_download():
            with open(temp_new, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
    except requests.exceptions.HTTPError as e:
        if temp_new.exists():
            temp_new.unlink()
        if e.response is not None and e.response.status_code == 404:
            UI.die(
                "A release build may be in progress. Please try again later. (HTTP 404: File not found)"
            )
        else:
            UI.die("Download failed.", e)
    except Exception as e:
        if temp_new.exists():
            temp_new.unlink()
        manual_cmd = _get_manual_upgrade_cmd(handler, url, exe_path)
        UI.error(f"Download failed: {e}")
        UI.detail(
            f"You can upgrade manually by running:\n\n    {UI.CYAN}{manual_cmd}{UI.COLOR_OFF}\n"
        )
        sys.exit(1)

    # 4. Verify Integrity
    UI.detail("Verifying integrity...")
    import hashlib

    sha = hashlib.sha256()
    with open(temp_new, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha.update(chunk)
    new_hash = sha.hexdigest()

    # Fetch official checksums.txt
    checksum_url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{latest}/checksums.txt"
    try:
        import requests

        response = requests.get(
            checksum_url, headers={"User-Agent": "ldm-cli"}, timeout=10
        )
        if response.status_code == 200:
            official_data = response.text

            # Extract filename from URL to match exact hash in checksums.txt
            target_name = url.split("/")[-1]

            verified = False
            for line in official_data.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    hash_val = parts[0]
                    file_name = parts[1]
                    if file_name == target_name and new_hash == hash_val:
                        verified = True
                        break

            if not verified:
                if temp_new.exists():
                    temp_new.unlink()
                manual_cmd = _get_manual_upgrade_cmd(handler, url, exe_path)
                UI.error("Integrity verification failed! The hash does not match.")
                UI.detail(
                    f"If you trust this build, install manually:\n\n    {UI.CYAN}{manual_cmd}{UI.COLOR_OFF}\n"
                )
                sys.exit(1)
        elif response.status_code == 404:
            if temp_new.exists():
                temp_new.unlink()
            UI.die(
                "A release build may be in progress. Please try again later. (HTTP 404: Failed to fetch checksums)"
            )
        else:
            if temp_new.exists():
                temp_new.unlink()
            UI.die(f"Failed to fetch checksums (HTTP {response.status_code})")
    except Exception as e:
        UI.warning(f"Could not verify hash remotely ({e}). Proceeding with caution...")

    # 5. Atomic Swap
    UI.detail("Applying update...")

    try:
        if platform.system().lower() == "windows":
            # Windows replacement logic via temporary batch file with retry loop
            # We need to ensure the calling process has exited before we can move the file.
            bat_path = temp_new.with_suffix(".update.bat")
            bat_content = f"""@echo off
setlocal enabledelayedexpansion
set "RETRIES=0"
set "MAX_RETRIES=10"

:RETRY
timeout /t 2 /nobreak > nul
taskkill /F /IM ldm.exe /T >nul 2>&1

move /y "{temp_new}" "{exe_path}" >nul 2>&1
if !errorlevel! equ 0 (
start "" "{exe_path}" doctor
goto :CLEANUP
)

set /a "RETRIES+=1"
if !RETRIES! lss !MAX_RETRIES! (
echo [!RETRIES!/!MAX_RETRIES!] Access denied, retrying in 2 seconds...
goto :RETRY
)

echo [ERROR] Failed to apply update after !MAX_RETRIES! attempts.
echo [ERROR] Please try running the following command manually in an elevated terminal:
echo move /y "{temp_new}" "{exe_path}"
pause

:CLEANUP
(goto) 2>nul & del "%~f0"
"""
            bat_path.write_text(bat_content)
            UI.success("Update staged. LDM will restart in a new window to complete.")

            # Check if we have write access to the target directory
            try:
                test_file = exe_path.parent / f".test_write_{os.getpid()}"
                test_file.touch()
                test_file.unlink()
                has_access = True
            except (PermissionError, OSError):
                has_access = False

            if has_access:
                # Bandit: B602 (shell=True) is necessary here to launch the independent Windows batch updater.
                # The path is internally generated and sanitized.
                subprocess.Popen(["cmd.exe", "/c", str(bat_path)], shell=True)  # nosec B602
            else:
                UI.detail(
                    "\nRequesting administrative privileges to replace the binary in system path..."
                )
                ps_cmd = (
                    f"Start-Process cmd -ArgumentList '/c \"{bat_path}\"' -Verb RunAs"
                )
                subprocess.Popen(["powershell.exe", "-Command", ps_cmd])

            sys.exit(0)
        else:
            # Unix atomic rename
            # Bandit: B103 (chmod 0o755) is necessary to make the newly downloaded binary executable.
            try:
                os.chmod(temp_new, 0o755)  # nosec B103
            except Exception:
                pass

            try:
                # Use safe_move instead of os.replace because it handles
                # 'Invalid cross-device link' (Errno 18) by falling back to copy+unlink.
                safe_move(str(temp_new), str(exe_path))
                UI.success(f"Successfully upgraded to v{latest}!")
            except (PermissionError, OSError):
                UI.detail(
                    "\nRequesting permission to replace the binary in system path..."
                )
                try:
                    # Use sudo to copy the file from /tmp to system path
                    # LDM-412: On Unix, use os.system for the sudo command to ensure
                    # a proper TTY is available for the interactive password prompt.
                    # subprocess.run can sometimes fail with 'unable to allocate pty'.
                    if platform.system() != "Windows" and not getattr(
                        handler.manager.args, "non_interactive", False
                    ):
                        cmd = (
                            f'sudo cp "{temp_new}" "{exe_path}" && sudo rm "{temp_new}"'
                        )
                        ret = os.system(cmd)  # nosec B605
                        if ret != 0:
                            raise subprocess.CalledProcessError(ret, cmd)
                    else:
                        sudo_prefix = (
                            ["sudo", "-n"]
                            if getattr(handler.manager.args, "non_interactive", False)
                            else ["sudo"]
                        )
                        subprocess.run(
                            [*sudo_prefix, "cp", str(temp_new), str(exe_path)],
                            check=True,
                        )
                        subprocess.run([*sudo_prefix, "rm", str(temp_new)], check=True)

                    UI.success(f"Successfully upgraded to v{latest}!")
                except Exception as e:
                    UI.error(
                        "Failed to replace binary. Elevated privileges were denied or incorrect."
                    )
                    UI.debug(f"Details: {e}")
                    UI.detail(
                        f'Please run manually: {UI.CYAN}sudo cp "{temp_new}" "{exe_path}" && sudo rm "{temp_new}"{UI.COLOR_OFF}'
                    )
                    return

    except Exception as e:
        if temp_new.exists():
            temp_new.unlink()
        manual_cmd = _get_manual_upgrade_cmd(handler, url, exe_path)
        UI.error(f"Failed to apply update: {e}")
        UI.detail(
            f"Please try the manual installation command:\n\n    {UI.CYAN}{manual_cmd}{UI.COLOR_OFF}\n"
        )
        sys.exit(1)

    # 7. Post-Upgrade: Shell Completion Check
    UI.detail("\nChecking shell completion status...")
    if not is_completion_enabled(handler):
        UI.warning("Shell completion is not enabled for 'ldm' in this session.")
        UI.detail("To enable tab-completion for commands and projects, run:")
        print(f"\n    {UI.CYAN}ldm completion{UI.COLOR_OFF}\n")
    else:
        UI.success("Shell completion is active.")
