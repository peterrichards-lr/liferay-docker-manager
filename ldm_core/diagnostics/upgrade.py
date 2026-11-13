import contextlib
import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from ldm_core.constants import GITHUB_REPO_URL, VERSION
from ldm_core.diagnostics.completions import is_completion_enabled
from ldm_core.ui import UI
from ldm_core.utils import (
    check_for_updates,
    version_to_tuple,
)

# rwxr-xr-x: everyone may run the binary, only its owner may change it.
_BINARY_MODE = 0o755


def _replace_binary(new_binary, exe_path):
    """Put `new_binary` in place of the running binary at `exe_path`, mode 0755.

    GHSA-cq82-rxm7-vw5c: this used `utils.safe_move`, whose copy step runs
    `_fixup_unix_permissions` -- written so Liferay's auto-deploy scanner can
    write files LDM drops into `deploy/`. It ORs 0o666 into the mode, and as
    root chowns the file to 1000, so every self-upgrade since v2.7.6 left the
    binary on the user's PATH world-writable: any local user could replace it
    and run code as whoever ran `ldm` next.

    The new binary is copied into a hidden file in `exe_path`'s own directory
    (created exclusively, so nothing else can be waiting at that name), given
    exactly 0755, and renamed over the old one. The rename is atomic because
    both are on one filesystem, which is also why the copy is needed: the
    download sits in the system temp directory, often a different device.

    Raises OSError (PermissionError included) if the directory isn't writable,
    leaving the old binary untouched; the caller then falls back to sudo.
    """
    exe_path = Path(exe_path)
    fd, staged_str = tempfile.mkstemp(
        dir=exe_path.parent, prefix=f".{exe_path.name}.upgrade-"
    )
    staged = Path(staged_str)
    try:
        with os.fdopen(fd, "wb") as out, open(new_binary, "rb") as src:
            shutil.copyfileobj(src, out)
        os.chmod(staged, _BINARY_MODE)  # nosec B103 -- executable, owner-writable only
        os.replace(staged, exe_path)
    except BaseException:
        with contextlib.suppress(OSError):
            staged.unlink()
        raise
    with contextlib.suppress(OSError):
        os.unlink(new_binary)


def _sudo_install_argv(sudo_prefix, new_binary, exe_path, owner="0", group="0"):
    """The privileged replacement, for a binary in a directory the user can't write.

    `cp` onto an existing file keeps its owner and mode, so a binary an affected
    release left 0777 -- or, after a root upgrade, owned by uid 1000 -- stayed
    that way (GHSA-cq82-rxm7-vw5c). `install` with explicit owner, group and
    mode sets all three. They have to be explicit: GNU and BSD `install`
    replace the file, but busybox writes into it and keeps its owner unless
    told. `owner`/`group` are parameters only so a test can run the real
    command without root.
    """
    return [
        *sudo_prefix,
        "install",
        "-o",
        str(owner),
        "-g",
        str(group),
        "-m",
        f"{_BINARY_MODE:o}",
        str(new_binary),
        str(exe_path),
    ]


def _warn(msg):
    """A warning on stderr, at every verbosity. The binary check runs before
    every command, including ones whose stdout a script parses (`--json`)."""
    UI._print(msg, UI.YELLOW, "⚠️", file=sys.stderr)


def repair_binary_mode(exe_path=None):
    """Stop accounts other than the owner (or root) changing this binary.

    GHSA-cq82-rxm7-vw5c. The upgrade INTO a fixed release is carried out by the
    affected release's code, so the fixed binary arrives world-writable too,
    and only the next upgrade would correct it. This closes that gap. It runs
    on every command, not only the first run of a version, because a host
    driven by scripts or CI may never have an interactive first run; when
    nothing is wrong it costs two `stat` calls.

    What the current user is allowed to fix is fixed, and said once: the owner
    or root removes the write access; root also returns a binary in a
    root-owned directory to root. Anything left -- someone else's binary --
    is reported every run with the command to run, since only the user can
    judge whether it was tampered with.

    Returns True when the binary is (now) safe, False otherwise. Never raises.
    """
    if platform.system().lower() == "windows":
        return True
    try:
        exposed = _exposed_binary(exe_path)
        if exposed is None:
            return True
        exe, st, loose, misowned = exposed
        mode = stat.S_IMODE(st.st_mode)
        owner = st.st_uid
        euid = os.geteuid()
        # --dry-run promises no changes, so it only reports.
        may_fix = os.environ.get("LDM_DRY_RUN", "").lower() != "true"
        # Each fix is attempted on its own; one that fails (a read-only or
        # root-squashed filesystem) falls through to the warning below.
        if loose and may_fix and euid in (0, st.st_uid):
            with contextlib.suppress(OSError):
                os.chmod(exe, mode & ~loose)
                mode &= ~loose
                loose = 0
                _warn(
                    f"{exe} was writable by accounts other than its owner, so "
                    "one of them could have replaced it. That access is now "
                    "removed (GHSA-cq82-rxm7-vw5c)."
                )
        if misowned and may_fix and euid == 0:
            with contextlib.suppress(OSError):
                os.chown(exe, 0, 0)
                misowned = False
                owner = 0
                _warn(
                    f"{exe} is in a folder only root may change, but belonged "
                    f"to uid {st.st_uid}, who could replace it. It now belongs "
                    "to root (GHSA-cq82-rxm7-vw5c)."
                )
        if not loose and not misowned:
            return True
        quoted = shlex.quote(str(exe))
        who = "root" if misowned else "its owner"
        fix = (
            f"sudo chown root {quoted} && sudo chmod {_BINARY_MODE:o} {quoted}"
            if misowned
            else f"sudo chmod {_BINARY_MODE:o} {quoted}"
            if owner == 0
            else f"chmod {_BINARY_MODE:o} {quoted}  (as uid {owner})"
        )
        _warn(
            f"{exe} (mode {mode:o}, owner uid {owner}) can be changed by an "
            f"account other than {who}, so it could have been replaced "
            "(GHSA-cq82-rxm7-vw5c)."
        )
        _warn(
            "If you can't rule that out, reinstall LDM from the release assets. "
            f"Then run: {fix}"
        )
    except Exception:
        pass
    return False


def _exposed_binary(exe_path):
    """`(path, stat, loose_write_bits, misowned)` if an account that shouldn't
    can change the binary, else None.

    `misowned`: in a root-owned directory such as /usr/local/bin, owned by
    someone other than root -- an affected release run as root chowned the
    binary to uid 1000.
    """
    if exe_path is None:
        # A frozen (PyInstaller) binary started from PATH has a bare `ldm` in
        # argv[0], which would resolve against the current directory.
        exe_path = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    exe = Path(exe_path).resolve()
    if exe.suffix.lower() == ".py" or not exe.is_file():
        return None
    st = exe.stat()
    loose = _loose_write_bits(st)
    misowned = exe.parent.stat().st_uid == 0 and st.st_uid != 0
    if loose or misowned:
        return exe, st, loose, misowned
    return None


def _loose_write_bits(st):
    """The write bits that let an account other than the owner change the file.

    Group write counts only when the group is not the owner's private group.
    Under umask 002 (Ubuntu and Fedora give every user a group of their own) a
    pip or uv console script is 0775 with a group holding only its owner.
    """
    import grp
    import pwd

    bits = stat.S_IMODE(st.st_mode) & 0o022
    if bits & 0o020:
        try:
            owner = pwd.getpwuid(st.st_uid)
            group = grp.getgrgid(st.st_gid)
            private = (
                st.st_gid == owner.pw_gid
                and group.gr_name == owner.pw_name
                and all(member == owner.pw_name for member in group.gr_mem)
            )
        except KeyError:
            private = False
        if private:
            bits &= ~0o020
    return bits


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
    # Download to a fresh temp file, then `install` it: writing over the binary
    # with `curl -o` keeps the old file's mode and owner, so one an affected
    # release left 0777 would stay 0777 (GHSA-cq82-rxm7-vw5c).
    exe = shlex.quote(str(exe_path))
    return (
        f't="$(mktemp)" && curl -fL {shlex.quote(url)} -o "$t" && '
        f'{prefix}install -m {_BINARY_MODE:o} "$t" {exe} && rm -f "$t"'
    )


def _announce_post_upgrade_actions(upgraded_from, upgraded_to):
    """Tell the user what this upgrade needs them to DO, not just that it worked.

    LDM-#1974: `ldm system doctor --bundle` was documented as producing a
    "sanitized" zip for attaching to support tickets, and wrote `~/.ldmrc`, every
    project's meta and the lfr-tunnel token into it. Two went through a
    redaction step that is a no-op on JSON; the token was copied verbatim with
    none attempted. Anyone who shared such a bundle disclosed their
    `ngrok_authtoken`, any stored API key, and project `admin_password` /
    `db_password`.

    A CHANGELOG entry is not enough for that. Someone upgrading past this
    version has to be told at the moment they upgrade, because the action is
    theirs and it is time-sensitive -- the credentials stay valid until rotated.

    Version-gated deliberately: it fires only when crossing the release that
    fixed it, so it does not nag on every subsequent upgrade. Anyone already on
    2.26.0 or later has either seen it or was never exposed.
    """
    from ldm_core.utils import version_to_tuple

    try:
        was_before = version_to_tuple(str(upgraded_from)) < (2, 26, 0)
        now_at_or_after = version_to_tuple(str(upgraded_to)) >= (2, 26, 0)
    except Exception:
        # Defensive only: `version_to_tuple` returns (0, 0, 0, 0) for anything
        # it cannot parse rather than raising, so this is unreachable today.
        # Kept because this runs immediately after the binary is replaced, and a
        # traceback here would make a SUCCESSFUL upgrade look like a failure.
        #
        # Note the (0, 0, 0, 0) fallback means an UNKNOWN starting version
        # compares as older than the fix and the notice fires. That is the right
        # direction: an unnecessary rotation reminder costs a minute, a missed
        # one leaves a live credential in a shared artifact.
        return

    if not (was_before and now_at_or_after):
        return

    UI.warning(
        "Action may be required: 'ldm system doctor --bundle' used to include "
        "credentials in a zip it described as sanitized (LDM-#1974)."
    )
    UI.info(
        "If you ever attached one of those bundles to a support ticket, a "
        "GitHub issue or a chat, treat these as disclosed and rotate them:\n"
        "  - ngrok_authtoken and any API key in ~/.ldmrc\n"
        "  - admin_password and db_password in each project's meta\n"
        "  - the lfr-tunnel token (~/.ldm/lfr-tunnel/token)\n"
        "If you have never run that command with --bundle, nothing was exposed."
    )


def _windows_updater_script(temp_new, exe_path):
    """The batch file that swaps the binary once this ldm.exe has exited.

    LDM-#2212. Windows cannot replace a running executable, so the swap
    happens after ldm exits, retrying while the image is still locked. Three
    things made it read as a hang:

    - it printed nothing until a retry failed, and nothing on success, so the
      first word it ever said was "Access denied" even when it then worked;
    - it ran in the caller's PowerShell console (see _launch_windows_updater),
      so after ldm exited the prompt and the batch shared one console;
    - every attempt ran `taskkill /F /IM ldm.exe /T`, killing EVERY ldm.exe
      on the machine -- including a command typed while it retried. It is
      gone rather than narrowed to a PID: ldm exits on its own straight after
      staging (sys.exit(0)), and once it has, its PID can be reused by an
      unrelated process.
    """
    return f"""@echo off
setlocal enabledelayedexpansion
set "RETRIES=0"
set "MAX_RETRIES=10"
title LDM upgrade
echo Replacing "{exe_path}" ...

:RETRY
timeout /t 2 /nobreak > nul

move /y "{temp_new}" "{exe_path}" >nul 2>&1
if !errorlevel! equ 0 (
echo [OK] LDM upgraded. Starting it to finish setting up...
start "" "{exe_path}" doctor --skip-project
goto :CLEANUP
)

set /a "RETRIES+=1"
if !RETRIES! lss !MAX_RETRIES! (
echo [!RETRIES!/!MAX_RETRIES!] The old ldm.exe is still in use, retrying in 2 seconds...
goto :RETRY
)

echo [ERROR] Failed to apply update after !MAX_RETRIES! attempts.
echo [ERROR] Please try running the following command manually in an elevated terminal:
echo move /y "{temp_new}" "{exe_path}"
pause

:CLEANUP
(goto) 2>nul & del "%~f0"
"""


def _launch_windows_updater(bat_path, has_access):
    """Start the updater in its OWN console window (LDM-#2212).

    It used to run as `cmd.exe /c` under shell=True, inheriting the caller's
    console. ldm then exited, PowerShell printed its prompt, and the batch
    kept running in the same window: two programs reading one keyboard and
    interleaving output, which read as a frozen terminal -- and on failure its
    `pause` waited there too. CREATE_NEW_CONSOLE gives it a window of its own.
    """
    if has_access:
        # getattr: the flag exists only on Windows, and the tests run elsewhere.
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
        subprocess.Popen(["cmd.exe", "/c", str(bat_path)], creationflags=flags)
    else:
        UI.detail(
            "\nRequesting administrative privileges to replace the binary in system path..."
        )
        # Start-Process opens its own (elevated) window already.
        ps_cmd = f"Start-Process cmd -ArgumentList '/c \"{bat_path}\"' -Verb RunAs"
        subprocess.Popen(["powershell.exe", "-Command", ps_cmd])


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
        url = f"{GITHUB_REPO_URL}/releases/download/v{VERSION}/{target_asset}"
    else:
        # 1. Check for updates / specific version tag
        if target_version_str:
            latest, url = check_for_updates(VERSION, force=True, tag=target_version_str)
            if not latest:
                UI.die(
                    f"Version '{target_version_str}' not found on GitHub Releases.",
                    tip=(
                        "Check the available tags at "
                        f"{GITHUB_REPO_URL}/releases. Pre-release builds are "
                        "only offered with '--pre-release'."
                    ),
                )
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
            # LDM-#1912: the label describes the INSTALLED version, not the
            # channel that was searched. `pre_release` is the --beta flag, so
            # using it told a user on stable v2.25.0 they were on a
            # pre-release purely because they passed --beta -- and told a user
            # genuinely on 2.26.0-pre.1 they were "(stable)" when they did
            # not. `is_beta` was already in scope, correctly derived from
            # VERSION, and used two branches below.
            tier = " (pre-release)" if is_beta else " (stable)"
            UI.success(f"LDM is already up to date v{VERSION}{tier}.")
            return

        # LDM-#2358: the outcome lines below are the whole answer to the
        # command, so they use UI.info, not UI.detail. UI.detail is hidden at
        # default verbosity, which made `--check` print only its heading
        # whenever an update existed, and `-y` on a pre-release return without
        # a word.
        if check_only:
            if target_version_str:
                UI.info(f"Target version v{latest} is available.")
            else:
                UI.info(
                    f"A new version of LDM is available: {UI.GREEN}v{latest}{UI.COLOR_OFF}"
                )
            UI.info(f"Run {UI.CYAN}ldm upgrade{UI.COLOR_OFF} to install it.")
            return

        if is_beta and not pre_release and not target_version_str:
            # User is on beta but wants stable (Switching Tiers)
            UI.info(
                f"You are currently on a beta build ({UI.BYELLOW}v{VERSION}{UI.COLOR_OFF})."
            )
            UI.info(f"The latest stable version is {UI.GREEN}v{latest}{UI.COLOR_OFF}.")
            if not UI.confirm("Switch back to the stable release tier?", "N"):
                if handler.manager.non_interactive:
                    # The prompt defaults to N, so `-y` declines it without
                    # asking. Say so, and how to take the stable release.
                    UI.warning(
                        f"Not upgraded: staying on v{VERSION}. '-y' does not "
                        "switch from a pre-release to the stable tier."
                    )
                    UI.info(
                        f"To upgrade, re-run with '--beta' or '-v v{latest}', "
                        "or without '-y' and answer the prompt."
                    )
                else:
                    UI.info(f"Upgrade cancelled: staying on v{VERSION}.")
                return

    is_downgrade = version_to_tuple(latest) < version_to_tuple(VERSION)

    if is_repair:
        UI.detail(f"Repairing current version: v{latest}")
    elif is_downgrade:
        UI.detail(f"Target version (downgrade): v{latest}")
    else:
        UI.detail(f"New version found: v{latest}")

    if not url or not url.startswith("http"):
        UI.die(
            "Download URL not found for your architecture.",
            details=f"{platform.system()} {platform.machine()}",
            tip=(
                "That release may not ship a binary for this platform. Check "
                f"the assets at {GITHUB_REPO_URL}/releases, or install from "
                "PyPI with 'pip install --upgrade liferay-docker-manager'."
            ),
        )

    if is_downgrade:
        UI.warning(
            f"Downgrading from v{VERSION} to v{latest} may not support properties hierarchy features or metadata formats of your current LDM setups."
        )
        UI.warning("This could cause project registry or schema incompatibility.")
        if handler.manager.non_interactive:
            if not getattr(handler.manager.args, "force", False):
                UI.die(
                    "Downgrade aborted: --force is required in non-interactive mode.",
                    tip=(
                        "Re-run with '-f' to confirm the downgrade, or drop "
                        "'-y' to be asked interactively."
                    ),
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
            UI.die(
                "Download failed.",
                e,
                tip=(
                    "Check your network or proxy and try again. If you are "
                    "behind a corporate proxy, ensure HTTPS_PROXY is set."
                ),
            )
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
    checksum_url = f"{GITHUB_REPO_URL}/releases/download/v{latest}/checksums.txt"
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
                UI.error(
                    "Integrity verification failed! The hash does not match.",
                    tip=(
                        "The download was corrupted or tampered with, and has "
                        "NOT been applied. Re-run 'ldm upgrade' to fetch it "
                        f"again; if it fails twice, report it at "
                        f"{GITHUB_REPO_URL}/issues rather than bypassing the "
                        "check."
                    ),
                )
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
            UI.die(
                f"Failed to fetch checksums (HTTP {response.status_code})",
                tip=(
                    "GitHub rate-limits unauthenticated requests. Wait a few "
                    "minutes and retry, or set LDM_BOT_PAT to a token."
                    if response.status_code in (403, 429)
                    else "Check your network and retry with 'ldm upgrade'."
                ),
            )
    except Exception as e:
        UI.warning(f"Could not verify hash remotely ({e}). Proceeding with caution...")

    # 5. Atomic Swap
    UI.detail("Applying update...")

    try:
        if platform.system().lower() == "windows":
            # Windows replacement logic via temporary batch file with retry loop
            # We need to ensure the calling process has exited before we can move the file.
            bat_path = temp_new.with_suffix(".update.bat")
            bat_path.write_text(_windows_updater_script(temp_new, exe_path))
            UI.success(
                "Update staged. It completes in a separate window, which "
                "reports the result and then starts LDM."
            )

            # Check if we have write access to the target directory
            try:
                test_file = exe_path.parent / f".test_write_{os.getpid()}"
                test_file.touch()
                test_file.unlink()
                has_access = True
            except (PermissionError, OSError):
                has_access = False

            _launch_windows_updater(bat_path, has_access)

            sys.exit(0)
        else:
            try:
                _replace_binary(temp_new, exe_path)
                UI.success(f"Successfully upgraded to v{latest}!")
                _announce_post_upgrade_actions(VERSION, latest)
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
                        cmd = shlex.join(
                            _sudo_install_argv(["sudo"], temp_new, exe_path)
                        )
                        ret = os.system(cmd)  # nosec B605
                        if ret != 0:
                            raise subprocess.CalledProcessError(ret, cmd)
                        rm_argv = ["sudo", "rm", "-f", str(temp_new)]
                    else:
                        sudo_prefix = (
                            ["sudo", "-n"]
                            if getattr(handler.manager.args, "non_interactive", False)
                            else ["sudo"]
                        )
                        # Non-interactive (`sudo -n`), so nothing waits on a
                        # password; bounded so a wedged sudo can't hang a script.
                        subprocess.run(
                            _sudo_install_argv(sudo_prefix, temp_new, exe_path),
                            check=True,
                            timeout=300,
                        )
                        rm_argv = [*sudo_prefix, "rm", "-f", str(temp_new)]

                    # The binary is replaced: failing to tidy the download must
                    # not be reported as a failed upgrade.
                    with contextlib.suppress(Exception):
                        subprocess.run(rm_argv, check=False)
                    UI.success(f"Successfully upgraded to v{latest}!")
                    _announce_post_upgrade_actions(VERSION, latest)
                except Exception as e:
                    UI.error(
                        "Failed to replace binary. Elevated privileges were denied or incorrect.",
                        tip=(
                            "The running binary is unchanged, so nothing is "
                            "broken. Re-run with sufficient privileges, or use "
                            "'ldm upgrade --repair' once elevated."
                        ),
                    )
                    UI.debug(f"Details: {e}")
                    UI.detail(
                        f"Please run manually: {UI.CYAN}{shlex.join(_sudo_install_argv(['sudo'], temp_new, exe_path))} && sudo rm -f {shlex.quote(str(temp_new))}{UI.COLOR_OFF}"
                    )
                    return

    except Exception as e:
        if temp_new.exists():
            temp_new.unlink()
        manual_cmd = _get_manual_upgrade_cmd(handler, url, exe_path)
        UI.error(f"Failed to apply update: {e}")
        # LDM-#1971: the self-upgrade has failed and `sys.exit(1)` follows;
        # the manual command is the only thing the user can act on.
        UI.info(
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
