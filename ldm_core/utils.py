import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

try:
    import keyring
except ImportError:
    keyring = None  # type: ignore[assignment]

from ldm_core.constants import SCRIPT_DIR, TAG_PATTERN
from ldm_core.ui import UI

# Global virtual file system for Dry-Run mode to support read-after-write operations
_DRY_RUN_VFS: dict[str, str] = {}


class Benchmarker:
    """A global timer to benchmark LDM operations, separating human vs machine time."""

    is_active = False
    _start_time = 0.0
    human_time = 0.0
    download_time = 0.0

    @classmethod
    def start(cls):
        cls.is_active = True
        cls._start_time = time.time()
        cls.human_time = 0.0
        cls.download_time = 0.0

    @classmethod
    def add_human_time(cls, duration):
        if cls.is_active:
            cls.human_time += duration

    @classmethod
    def add_download_time(cls, duration):
        if cls.is_active:
            cls.download_time += duration

    @classmethod
    def print_report(cls):
        if not cls.is_active or not cls._start_time:
            return

        total_time = time.time() - cls._start_time
        machine_time = total_time - cls.human_time - cls.download_time
        machine_time = max(machine_time, 0.0)

        UI.heading("Benchmark Report")
        UI._print(
            f"  ● {UI.WHITE}Total Elapsed Time: {UI.BOLD}{UI.format_duration(total_time)}{UI.COLOR_OFF}"
        )
        UI._print(
            f"  ● {UI.CYAN}Human Input Time:   {UI.format_duration(cls.human_time)}{UI.COLOR_OFF}"
        )
        UI._print(
            f"  ● {UI.CYAN}Download Time:      {UI.format_duration(cls.download_time)}{UI.COLOR_OFF}"
        )
        UI._print(
            f"  ● {UI.GREEN}LDM Processing:     {UI.format_duration(machine_time)}{UI.COLOR_OFF}\n"
        )

    @classmethod
    @contextlib.contextmanager
    def measure_human(cls):
        start = time.time()
        try:
            yield
        finally:
            cls.add_human_time(time.time() - start)

    @classmethod
    @contextlib.contextmanager
    def measure_download(cls):
        start = time.time()
        try:
            yield
        finally:
            cls.add_download_time(time.time() - start)


def get_resource_path(filename):
    """Resiliently locates internal resource files (supports source vs bundled)."""
    # 1. Check bundled package structure (site-packages/ldm_core/resources)
    path = SCRIPT_DIR / "ldm_core" / "resources" / filename
    if path.exists():
        return path

    # 2. Check source development structure (root/resources)
    path = SCRIPT_DIR / "resources" / filename
    if path.exists():
        return path

    return None


def strip_ansi(text):
    """Removes ANSI escape sequences (colors, formatting) from a string."""
    if not text:
        return text
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def is_lcp_workspace(path):
    """Detects if the given path is a Liferay Cloud Platform (LCP) workspace."""
    p = Path(path).resolve()
    return (
        (p / "liferay" / "LCP.json").exists()
        or (p / "liferay" / "lcp.json").exists()
        or (p / "LCP.json").exists()
        or (p / "lcp.json").exists()
    )


def parse_lcp_backups(data_in):
    """
    Robustly parses Liferay Cloud CLI backup table output using pattern recognition.
    Supports both JSON list and plain text table formats.
    """
    if not data_in:
        return []
    if isinstance(data_in, list):
        return data_in

    if isinstance(data_in, str):
        # 1. Clean output
        text = strip_ansi(data_in)

        if "No backups found" in text or "Failed to fetch" in text or "Error" in text:
            return []

        # 2. Regex for Liferay Cloud Backup IDs: dxpcloud-xxx-timestamp or bk123456
        id_pattern = re.compile(r"\b(dxpcloud-[a-z0-9-]+|bk[0-9]{6,})\b")

        parsed = []
        lines = text.strip().splitlines()
        for raw_line in lines:
            line = raw_line.strip()
            if not line or "Backup ID" in line or "Backup Id" in line or "----" in line:
                continue

            match = id_pattern.search(line)
            if match:
                backup_id = match.group(1)
                # Extract date heuristic
                parts = [p.strip() for p in re.split(r"\s{2,}|\|", line)]
                # Skip the ID itself (index 0) to avoid picking up year markers in the ID
                created_date = "unknown"
                for p in parts[1:]:
                    if any(
                        target in p for target in ["AM", "PM", "2024", "2025", "2026"]
                    ):
                        created_date = p
                        break

                parsed.append({"id": backup_id, "created": created_date})
        return parsed
    return []


def get_lcp_environment_variables(workspace_path, environment_id):
    """
    Parses a Liferay Cloud LCP.json file to extract environment variables.
    Returns a dictionary of merged global and environment-specific variables.
    """
    p = Path(workspace_path).resolve()

    # Heuristic for finding LCP.json
    candidates = [
        p / "liferay" / "LCP.json",
        p / "liferay" / "lcp.json",
        p / "LCP.json",
        p / "lcp.json",
    ]

    lcp_json = None
    for c in candidates:
        if c.exists():
            lcp_json = c
            break

    if not lcp_json:
        return None

    try:
        data = json.loads(lcp_json.read_text())
        envs = {}

        # 1. Global variables
        global_envs = data.get("env", {})
        envs.update(global_envs)

        # 2. Environment specific overrides
        env_configs = data.get("environments", {}).get(environment_id, {})
        env_specific = env_configs.get("env", {})
        envs.update(env_specific)

        return envs
    except Exception:
        return None


def download_file(url, destination):
    """Downloads a file securely from a URL to a destination path using atomic replacement."""
    destination = Path(destination)
    temp_dest = destination.with_suffix(".download_tmp")
    try:
        if not url.startswith("https://"):
            raise ValueError(f"Invalid URL scheme: {url}")

        response = requests.get(
            url, headers={"User-Agent": "ldm-cli"}, timeout=30, stream=True
        )

        response.raise_for_status()
        with Benchmarker.measure_download():
            temp_dest.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_dest, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

        # Atomic replacement ensures partial/corrupted files are never left in the final path
        temp_dest.replace(destination)
        return True
    except Exception as e:
        if temp_dest.exists():
            try:
                temp_dest.unlink()
            except OSError:
                pass
        UI.error(f"Download failed: {e}")
        return False


def get_seed_url(tag, db="postgresql", search="shared"):
    """Checks GitHub for a seeded state asset matching the given Liferay configuration."""
    from ldm_core.constants import SEED_VERSION

    tag_name = "seeded-states"

    # Construct asset names with the seed logic version
    target_name = f"seeded-{tag}-{db}-{search}-v{SEED_VERSION}.tar.gz"
    fallback_postgresql = f"seeded-{tag}-postgresql-shared-v{SEED_VERSION}.tar.gz"

    # Legacy asset names for backward compatibility
    legacy_patterns = [
        f"seeded-{tag}-{db}-{search}.tar.gz",
        f"seeded-{tag}-postgresql-shared.tar.gz",
        f"seeded-{tag}.tar.gz",
    ]

    # List of releases to check in priority order:
    # 1. Current LDM version (ensures logic-matched seeds)
    # 2. Permanent 'seeded-states' release (Living repository of seeds)
    target_tags = [tag_name, "seeded-states"]

    for t in target_tags:
        api_url = f"https://api.github.com/repos/peterrichards-lr/liferay-docker-manager/releases/tags/{t}"
        try:
            response = requests.get(
                api_url, headers={"User-Agent": "ldm-cli"}, timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                assets = data.get("assets", [])

                # Priority 1: Exact match with SEED_VERSION
                for asset in assets:
                    if asset["name"] == target_name:
                        return asset["browser_download_url"]

                # Priority 2: High-perf default (Postgres + Shared) with SEED_VERSION
                if db != "postgresql" or search != "shared":
                    for asset in assets:
                        if asset["name"] == fallback_postgresql:
                            return asset["browser_download_url"]

                # Priority 3: Legacy patterns (No SEED_VERSION)
                for pattern in legacy_patterns:
                    for asset in assets:
                        if asset["name"] == pattern:
                            return asset["browser_download_url"]
        except Exception:  # nosec B112
            continue
    return None


def load_env_blacklist(path):
    """Loads environment variable blacklist patterns from a file."""
    patterns: list[str] = []
    if not path or not path.exists():
        return patterns
    try:
        with Path(path).open() as f:
            for line in f:
                # LDM-423: Robustly strip whitespace and inline comments
                line = line.split("#")[0].strip()
                if line:
                    patterns.append(line)
    except Exception as e:
        UI.warning(f"Failed to load blacklist from {path}: {e}")
    return patterns


def sanitize_id(identifier):
    """
    Sanitizes a string to be used as a safe identifier (e.g. project ID, container name).
    Allows only alphanumeric characters, dashes, underscores, and dots.
    """
    if not identifier:
        return identifier
    import re

    ident = str(identifier).replace(" ", "-")
    return re.sub(r"[^a-zA-Z0-9\-_.]", "", ident)


def is_env_var_blacklisted(key, blacklist):
    """Checks if an environment variable key matches any pattern in the blacklist."""
    for pattern in blacklist:
        if pattern.endswith("*") and pattern.startswith("*"):
            if pattern[1:-1] in key:
                return True
        elif pattern.endswith("*"):
            if key.startswith(pattern[:-1]):
                return True
        elif pattern.startswith("*"):
            if key.endswith(pattern[1:]):
                return True
        elif key == pattern:
            return True
    return False


def _sanitize_shell_command(cmd):
    """
    Sanitizes a shell command string to prevent common injection attacks.
    Allows pipes (|) and redirections (<, >) but blocks dangerous metacharacters.
    """
    if not isinstance(cmd, str):
        return cmd

    # 1. Block obviously malicious sequences
    dangerous = [";", "&&", "||", "$(", "]]", "[[", "`"]
    for char in dangerous:
        if char in cmd:
            UI.die(
                f"Security Violation: Shell command contains forbidden character '{char}'"
            )

    # 2. Pattern Verification: If shell=True is used, it MUST match LDM usage patterns
    # (Docker operations, Compression, or Windows bridge)
    safe_patterns = [
        "docker",
        "gzip",
        "cmd.exe",
        "cat",
        "pg_dump",
        "mysql",
        "mariadb",
        "complete",
    ]
    is_safe = any(pattern in cmd.lower() for pattern in safe_patterns)

    if not is_safe:
        UI.die("Security Violation: Unrecognized shell command pattern.")

    return cmd


def run_command(
    cmd,
    shell=False,
    capture_output=True,
    check=True,
    env=None,
    cwd=None,
    verbose=False,
    stdout_file=None,
    timeout=None,
):
    env = os.environ.copy() if env is None else env.copy()

    env["DOCKER_CLI_HINTS"] = "false"

    # Hardening: Standardize on a modern API version for newer Docker engines (v29+)
    # while suppressing CLI hints for clean automation output.
    if "DOCKER_API_VERSION" not in env:
        env["DOCKER_API_VERSION"] = "1.44"

    # Hardening: Sanitize if shell is enabled
    if shell:
        cmd = _sanitize_shell_command(cmd)

    # Automatically resolve absolute paths for list-based commands (resolves Bandit B607)
    if isinstance(cmd, list) and len(cmd) > 0 and not shell:
        executable = shutil.which(cmd[0])
        if executable:
            cmd[0] = executable

    display_cmd = UI.redact(" ".join(cmd) if isinstance(cmd, list) else cmd)
    UI.trace(f"[CMD] {display_cmd}")
    if verbose:
        UI.debug(f"Executing: {display_cmd}")

    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        UI.info(f"{UI.BYELLOW}[DRY RUN] Would execute:{UI.COLOR_OFF} {display_cmd}")
        mock_output = ""
        if "MemTotal" in cmd_str:
            mock_output = "17179869184"
        elif "{{json .}}" in cmd_str or "info --format {{json" in cmd_str:
            mock_output = '{"MemTotal": 17179869184}'
        elif "context show" in cmd_str:
            mock_output = "default"
        elif "docker inspect" in cmd_str:
            if "{{.State.Status}}" in cmd_str or "{{.State.Health.Status}}" in cmd_str:
                mock_output = "running"
            else:
                mock_output = "[]"
        return mock_output

    try:
        # If stdout_file is provided, route stdout to the file descriptor and capture stderr separately.
        # Otherwise, we use capture_output.
        stdout_dest = (
            stdout_file
            if stdout_file
            else (subprocess.PIPE if capture_output else None)
        )
        stderr_dest = subprocess.PIPE if (capture_output or stdout_file) else None

        # Bandit: B602 (shell=True) is used for complex commands where needed,
        # B603 (subprocess_without_shell_equals_true) is safe as we now use absolute paths.
        result = subprocess.run(  # nosec B602 B603
            cmd,
            shell=shell,
            stdout=stdout_dest,
            stderr=stderr_dest,
            check=check,
            env=env,
            cwd=cwd,
            timeout=timeout,
        )

        if result.returncode != 0 and not check:
            return None

        if stdout_file:
            return ""

        stdout_str = ""
        if result.stdout:
            stdout_str = (
                result.stdout
                if isinstance(result.stdout, str)
                else result.stdout.decode("utf-8", errors="ignore")
            )
        stdout_str = stdout_str.strip()
        if stdout_str:
            UI.trace(f"[STDOUT] {stdout_str}")
        return stdout_str
    except subprocess.TimeoutExpired as e:
        if not check:
            return None
        cmd_str = UI.redact(" ".join(cmd) if isinstance(cmd, list) else cmd)
        UI.error(f"Command timed out after {e.timeout}s: {cmd_str}")
        UI.trace(f"[ERROR] Timeout after {e.timeout}s")
        sys.exit(124)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        if isinstance(e, subprocess.CalledProcessError) and e.returncode == 130:
            raise KeyboardInterrupt()

        if check:
            # Provide a clean, user-friendly error message instead of a stack trace
            # Use redaction to protect sensitive info in the error log
            cmd_str = UI.redact(" ".join(cmd) if isinstance(cmd, list) else cmd)

            if isinstance(e, FileNotFoundError):
                UI.error(
                    f"Command not found: {cmd[0] if isinstance(cmd, list) else cmd}"
                )
                sys.exit(127)

            UI.error(f"Command failed (Exit {e.returncode}): {cmd_str}")
            UI.trace(f"[ERROR] Exit {e.returncode}")
            if e.stderr:
                err_details = (
                    e.stderr
                    if isinstance(e.stderr, str)
                    else e.stderr.decode("utf-8", errors="ignore")
                )
                UI.trace(f"[STDERR] {err_details.strip()}")
                print(f"{UI.WHITE}Error Details:{UI.COLOR_OFF} {err_details.strip()}")
            sys.exit(e.returncode)
        return None
    except KeyboardInterrupt:
        # Standardize exit behavior on Ctrl+C to 130
        UI.info("\nExecution interrupted by user.")
        sys.exit(130)


def get_json(url):
    try:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid URL scheme: {url}")

        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        UI.error(f"Failed to fetch data: {e}")
        return None


def get_raw(url):
    """Fetches raw text from a URL."""
    try:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid URL scheme: {url}")

        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response.raise_for_status()
        return response.text
    except Exception as e:
        UI.error(f"Failed to fetch data: {e}")
        return None


def safe_write_text(path, content, encoding="utf-8", mode=None):
    """Atomically writes text to a file using a temporary file and robust replacement."""
    path = Path(path).resolve()
    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        UI.info(f"{UI.BYELLOW}[DRY RUN] Would write file:{UI.COLOR_OFF} {path}")
        _DRY_RUN_VFS[str(path)] = content
        return
    tmp_path = path.with_suffix(".tmp" + path.suffix)
    try:
        try:
            if mode is not None:
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                fd = os.open(tmp_path, flags, mode)
                with open(fd, "w", encoding=encoding) as f:
                    f.write(content)
            else:
                tmp_path.write_text(content, encoding=encoding)
        except (OSError, PermissionError) as e:
            # LDM-384: Fallback for root-owned directories in CI
            if platform.system().lower() != "windows":
                from ldm_core.utils import reclaim_volume_permissions

                if reclaim_volume_permissions(path.parent):
                    if mode is not None:
                        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                        fd = os.open(tmp_path, flags, mode)
                        with open(fd, "w", encoding=encoding) as f:
                            f.write(content)
                    else:
                        tmp_path.write_text(content, encoding=encoding)
                else:
                    raise e
            else:
                raise e

        # Robust replacement with retries (for Windows file locking)
        # and fallback (for permission nuances in CI)
        max_retries = 3
        for i in range(max_retries):
            try:
                os.replace(tmp_path, path)
                if (
                    mode is not None
                    and platform.system().lower() != "windows"
                    and path.exists()
                ):
                    path.chmod(mode)
                return
            except (OSError, PermissionError) as e:
                if i == max_retries - 1:
                    # Final attempt: manual copy and unlink (robust for cross-user permissions)
                    try:
                        shutil.copyfile(tmp_path, path)
                        tmp_path.unlink()
                        return
                    except Exception:
                        raise e
                time.sleep(0.1)
    except Exception as e:
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise e


def _fixup_unix_permissions(path):
    """
    Ensures a file is writable by Liferay (UID 1000) and world-readable/writable on Unix.
    This prevents 'Unable to write' errors in Liferay's AutoDeployScanner when LDM
    is running as root (e.g., in CI environments).
    """
    if platform.system().lower() == "windows":
        return

    path_obj = Path(path)
    if not path_obj.exists():
        return

    with contextlib.suppress(OSError):
        # 1. Broaden permissions: ensure rw-rw-rw- (666) while preserving execute bits
        current_mode = path_obj.stat().st_mode
        path_obj.chmod(current_mode | 0o666)

        # 2. Handover ownership: if running as root, proactively chown to Liferay UID 1000
        if hasattr(os, "getuid") and os.getuid() == 0:
            os.chown(path, 1000, 1000)


def safe_copy(src, dst):
    """Copies a file safely, ignoring metadata preservation errors (EPERM/OSError)."""
    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        UI.info(f"{UI.BYELLOW}[DRY RUN] Would copy:{UI.COLOR_OFF} {src} -> {dst}")
        return
    try:
        try:
            shutil.copyfile(src, dst)
        except (OSError, PermissionError) as e:
            # LDM-384: Fallback for root-owned directories in CI
            if platform.system().lower() != "windows":
                from ldm_core.utils import reclaim_volume_permissions

                if reclaim_volume_permissions(Path(dst).parent):
                    shutil.copyfile(src, dst)
                else:
                    raise e
            else:
                raise e

        # Try to copy permissions/mode, but ignore if it fails (common for cross-user files)
        with contextlib.suppress(OSError, PermissionError):
            shutil.copymode(src, dst)

        _fixup_unix_permissions(dst)
    except Exception as e:
        raise e


def atomic_copy(src, dst):
    """
    Copies a file to a temporary location and then moves it to the destination
    atomically within the same filesystem. This prevents 'partial file' reads
    by Liferay's auto-deployer.
    """
    dst_path = Path(dst).resolve()
    # Create a hidden temp file in the same directory to ensure same filesystem rename.
    # Liferay ignores files starting with a dot, allowing us to perform fixups
    # in-place without triggering the scanner.
    tmp_dst = dst_path.parent / f".{dst_path.name}.tmp"

    try:
        # 1. Perform safe copy to hidden temp file
        # This already calls _fixup_unix_permissions(tmp_dst)
        safe_copy(src, tmp_dst)

        # 2. Atomic rename to final destination with retries
        max_retries = 3
        for i in range(max_retries):
            try:
                # We rename the perfectly-permissioned file into its final place.
                # Since it's on the same filesystem, this is atomic.
                os.replace(tmp_dst, dst_path)
                return
            except (OSError, PermissionError):
                if i == max_retries - 1:
                    # Final attempt: direct copy if rename is blocked (e.g. cross-device)
                    # Note: This loses atomicity but ensures the file is delivered
                    safe_copy(tmp_dst, dst_path)
                    with contextlib.suppress(OSError):
                        tmp_dst.unlink()
                    return
                time.sleep(0.1)
    except Exception as e:
        if tmp_dst.exists():
            with contextlib.suppress(OSError):
                tmp_dst.unlink()
        raise e


def safe_move(src, dst):
    """Moves a file safely, handling cross-filesystem and permission nuances."""
    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        UI.info(f"{UI.BYELLOW}[DRY RUN] Would move:{UI.COLOR_OFF} {src} -> {dst}")
        return
    # We follow the same atomic pattern for moves to ensure permissions are fixed
    # BEFORE the file becomes visible to any scanners.
    try:
        atomic_copy(src, dst)
        with contextlib.suppress(OSError, PermissionError):
            os.unlink(src)
    except (OSError, PermissionError):
        # Fallback for simple renames if they are definitely on the same device
        # and we can tolerate the permission fixup happening slightly after.
        try:
            os.rename(src, dst)
            _fixup_unix_permissions(dst)
        except Exception as e:
            raise e


def get_github_token() -> str | None:
    """Retrieve GitHub token from environment variables or gh CLI."""
    token = os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token

    try:
        import subprocess

        res = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=False
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def get_actual_home():
    """Returns the home directory of the real user, even when running with sudo."""
    import getpass

    real_user = (
        os.environ.get("SUDO_USER") or os.environ.get("USER") or getpass.getuser()
    )
    if platform.system().lower() == "darwin" and real_user:
        home = Path(f"/Users/{real_user}")
        if home.exists():
            return home
    return Path.home()


def safe_cwd():
    """Returns the current working directory safely, returning None if deleted."""
    try:
        return Path.cwd()
    except FileNotFoundError:
        return None


def open_browser(url):
    """Launches the system browser, with special handling for WSL to use the host browser."""
    # Safety: Do not open browser tabs during automated tests
    if os.getenv("LDM_TEST_MODE") == "true":
        return True

    import webbrowser

    system = platform.system().lower()

    # 1. Detect WSL
    is_wsl = False
    if system == "linux":
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    is_wsl = True
        except Exception:
            pass

    if is_wsl:
        # On WSL, use the Windows host's 'start' command to open the browser
        # We try to find cmd.exe even if it's not in the current Linux PATH
        cmd_exe = shutil.which("cmd.exe") or "/mnt/c/Windows/System32/cmd.exe"
        if os.path.exists(cmd_exe):
            try:
                # Fix: Force cmd.exe to start from a safe Windows path (C:\) to avoid
                # "UNC paths are not supported" warnings when running from WSL.
                subprocess.run(
                    [cmd_exe, "/c", "start", url.replace("&", "^&")],
                    check=False,
                    cwd="/mnt/c",
                    stderr=subprocess.DEVNULL,
                )
                return True
            except Exception:
                pass

        # If we are in WSL and cmd.exe failed or wasn't found
        UI.info(
            f"Please open this URL in your Windows browser: {UI.CYAN}{url}{UI.COLOR_OFF}"
        )
        return False

    # 2. Standard Launch
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def validate_liferay_tag(tag):
    """
    Validates if a tag exists in Liferay's official releases.json.
    Returns True if valid, False if invalid, and True if the network request fails
    (to prevent blocking users during offline usage).
    """
    if not tag:
        return False

    url = "https://releases.liferay.com/releases.json"
    try:
        # Use a short timeout so we don't delay the CLI experience
        response = requests.get(url, headers={"User-Agent": "LDM-CLI"}, timeout=5)
        if response.status_code != 200:
            return True

        data = response.json()
        valid_tags = []
        for entry in data:
            entry_url = entry.get("url", "")
            if entry_url:
                # e.g., https://releases-cdn.liferay.com/dxp/2026.q1.7-lts -> 2026.q1.7-lts
                valid_tags.append(entry_url.split("/")[-1])

        # Some tags might not perfectly match the URL, so let's also check targetPlatformVersion
        for entry in data:
            target_plat = entry.get("targetPlatformVersion", "")
            if target_plat:
                valid_tags.append(target_plat)

        return tag in valid_tags
    except Exception:
        # Don't fail the tool if the network request fails (e.g. offline, rate limited)
        return True


def resolve_liferay_docker_tag(tag, manager=None):
    """
    Resolves a partial or user-supplied tag (e.g., '2026.q1.7', 'dxp-2026.q1.7')
    to the official Docker image tag (e.g., '2026.q1.7-lts') using releases.json.

    If offline or no match is found, falls back to applying configurable tag_heuristics.

    Returns (resolved_tag, is_portal) if resolved, otherwise (None, None).
    """
    if not tag:
        return None, None

    # Load tag heuristics rules
    heuristics = None
    if manager:
        heuristics = manager.defaults.get("tag_heuristics")
    else:
        # Avoid circular imports by loading config directly
        try:
            user_path = get_actual_home() / ".ldmrc"
            if user_path.exists():
                data = json.loads(user_path.read_text())
                defaults = data.get("defaults", {}) if "defaults" in data else data
                heuristics = defaults.get("tag_heuristics")
        except Exception:
            pass

    if heuristics is None:
        heuristics = {r"\.q1\.\d+$": "-lts"}

    def normalize(t):
        if not t:
            return ""
        return re.sub(r"^(dxp|portal)-", "", str(t)).lower().strip()

    normalized_query = normalize(tag)

    # 1. Check cache first
    cache_path = get_actual_home() / ".liferay_docker_cache.json"
    cache_key = f"resolve_tag_{tag}"
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                cache = json.load(f)
                if cache_key in cache:
                    entry = cache[cache_key]
                    if time.time() - entry.get("timestamp", 0) < 86400:
                        return entry.get("tag"), entry.get("is_portal")
        except Exception:
            pass

    # 2. Fetch releases.json online
    url = "https://releases.liferay.com/releases.json"
    online_resolved_tag = None
    is_portal = False

    try:
        response = requests.get(url, headers={"User-Agent": "LDM-CLI"}, timeout=5)
        if response.status_code == 200:
            data = response.json()
            for entry in data:
                entry_url = entry.get("url", "")
                entry_tag = entry_url.split("/")[-1] if entry_url else ""
                entry_release_key = entry.get("releaseKey", "")
                entry_target_platform = entry.get("targetPlatformVersion", "")

                # Check if matches normalized or exact
                matches = False
                if tag in [
                    entry_tag,
                    entry_release_key,
                    entry_target_platform,
                ] or normalized_query in [
                    normalize(entry_tag),
                    normalize(entry_release_key),
                    normalize(entry_target_platform),
                ]:
                    matches = True

                if matches and entry_tag:
                    product = entry.get("product", "")
                    is_portal = (
                        product.lower() == "portal"
                        or "portal" in entry_url.lower()
                        or entry_release_key.startswith("portal-")
                    )
                    online_resolved_tag = entry_tag
                    break
    except Exception:
        pass

    # 3. If online success and resolved, cache and return
    if online_resolved_tag:
        try:
            cache = {}
            if cache_path.exists():
                with open(cache_path) as f:
                    cache = json.load(f)
            cache[cache_key] = {
                "tag": online_resolved_tag,
                "is_portal": is_portal,
                "timestamp": time.time(),
            }
            with open(cache_path, "w") as f:
                json.dump(cache, f)
        except Exception:
            pass
        return online_resolved_tag, is_portal

    # 4. If online check failed / returned no match, apply heuristics
    for pattern, suffix in heuristics.items():
        try:
            if re.search(pattern, normalized_query, re.IGNORECASE):
                if not normalized_query.endswith(suffix.lower()):
                    resolved_tag = f"{normalized_query}{suffix}"
                    is_portal = tag.lower().startswith("portal-")
                    return resolved_tag, is_portal
        except Exception:
            # Handle potential invalid regex in config
            pass

    return None, None


def discover_latest_tag(
    api_url, release_type="any", prefix_filter=None, verbose=False, refresh=False
):
    cache_path = get_actual_home() / ".liferay_docker_cache.json"
    cache_key = f"{api_url}_{release_type}_{prefix_filter}"

    if not refresh and cache_path.exists():
        try:
            with open(cache_path) as f:
                cache = json.load(f)
                if cache_key in cache:
                    entry = cache[cache_key]
                    if time.time() - entry.get("timestamp", 0) < 86400:
                        val = entry.get("tag")
                        if val:
                            return val
        except Exception:
            pass

    if verbose:
        print("Initial tag discovery (this may take a some seconds)...")
    start_time = time.time()

    if prefix_filter:
        prefix_filter = prefix_filter.lower()

    # Strategy:
    # 1. Fetch from Liferay Product Info (CDN) as a robust secondary/fast source
    from ldm_core.constants import LIFERAY_PRODUCT_INFO_URL

    cdn_tags = []
    try:
        raw_cdn = get_raw(LIFERAY_PRODUCT_INFO_URL)
        if raw_cdn:
            cdn_data = json.loads(raw_cdn)
            for entry in cdn_data.values():
                image = entry.get("liferayDockerImage")
                if image and ":" in image:
                    cdn_tags.append(image.split(":", 1)[1])
    except Exception:
        pass

    # 2. Fetch from Primary API (Docker Hub or releases.liferay.com)
    url = api_url.replace("ordering=name", "ordering=-last_updated")

    api_filter = prefix_filter
    if not api_filter and release_type in ["lts", "u", "qr"]:
        api_filter = f"-{release_type}"

    if api_filter:
        url += f"&name={api_filter}"

    tags = []
    page = 0
    max_pages = 1 if prefix_filter else 3  # Depth for global search

    while url and page < max_pages:
        page += 1
        if verbose:
            sys.stdout.write(f"\rFetching page {page}...")
            sys.stdout.flush()

        raw_data = get_raw(url)
        if not raw_data:
            break

        current_page_tags = []
        next_url = None

        if raw_data.strip().startswith("{"):
            # 1. Handle JSON (Docker Hub)
            try:
                data = json.loads(raw_data)
                for result in data.get("results", []):
                    current_page_tags.append(result["name"])
                next_url = data.get("next")
            except Exception:
                break
        else:
            # 2. Handle HTML (releases.liferay.com)
            # Find all links that look like version tags (directories)
            # Example: <li><a href="/dxp/7.4.13-u103" ...
            matches = re.findall(r'href="[^"]*/([^"/]+)"', raw_data)
            for m in matches:
                current_page_tags.append(m)
            # HTML listings usually don't have "next" pages in the same way
            next_url = None

        if page == 1:
            current_page_tags.extend(cdn_tags)

        for name in current_page_tags:
            # 1. Local prefix check
            if prefix_filter and not name.startswith(prefix_filter):
                continue

            # 2. Local release type check
            if release_type == "lts" and "-lts" not in name:
                continue
            if release_type == "u" and "-u" not in name:
                continue
            if release_type == "qr" and "-qr" not in name:
                continue

            is_valid = bool(re.match(TAG_PATTERN, name))
            if is_valid:
                tags.append(name)

        url = next_url

    # Deduplicate tags
    tags = list(set(tags))

    duration = time.time() - start_time
    if verbose:
        print(f"\nFetched {page} pages in {duration:.1f}s")

    def natural_sort_key(s):
        return [
            int(text) if text.isdigit() else text.lower()
            for text in re.split("([0-9]+)", s)
        ]

    latest_tag = ""
    if tags:
        tags.sort(key=natural_sort_key)
        latest_tag = tags[-1]

    if latest_tag:
        try:
            cache = {}
            if cache_path.exists():
                with open(cache_path) as f:
                    cache = json.load(f)
            cache[cache_key] = {"tag": latest_tag, "timestamp": time.time()}
            with open(cache_path, "w") as f:
                json.dump(cache, f)
        except Exception:
            pass

    return latest_tag if latest_tag != "" else None


def yaml_to_dict(content):
    """Parses YAML content using PyYAML."""
    import yaml

    try:
        return yaml.safe_load(content) or {}
    except Exception as e:
        UI.warning(f"Could not parse YAML content: {e}")
        return {}


def dict_to_yaml(d: dict, indent: int = 0) -> str:
    import yaml

    class BlockStyleDumper(yaml.SafeDumper):
        def increase_indent(self, flow=False, indentless=False):
            return super().increase_indent(flow=False, indentless=False)

    return yaml.dump(
        d,
        Dumper=BlockStyleDumper,
        default_flow_style=False,
        sort_keys=False,
    )


def check_port(port):
    """Checks if a TCP port is currently in use on the host."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            # Try to connect to localhost on the given port
            if s.connect_ex(("127.0.0.1", int(port))) == 0:
                return True
    except Exception:
        pass
    return False


def is_within_root(path, root):
    try:
        path = Path(path).resolve()
        root = Path(root).resolve()
        return root in path.parents or path == root
    except Exception:
        return False


def read_meta(path):
    """Reads LDM project metadata from a file (supports JSON and Flat formats)."""
    meta: dict[str, Any] = {}
    path = Path(path)
    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    resolved_path = str(path.resolve())
    if is_dry_run and resolved_path in _DRY_RUN_VFS:
        content = _DRY_RUN_VFS[resolved_path].strip()
        try:
            if content.startswith("{"):
                return json.loads(content)
            for line in content.splitlines():
                stripped_line = line.strip()
                if (
                    stripped_line
                    and not stripped_line.startswith("#")
                    and "=" in stripped_line
                ):
                    k, v_str = stripped_line.split("=", 1)
                    k, v_str = k.strip(), v_str.strip()
                    dry_val: Any = v_str
                    if v_str == "None":
                        dry_val = None
                    elif v_str.lower() == "true":
                        dry_val = True
                    elif v_str.lower() == "false":
                        dry_val = False
                    meta[k] = dry_val
            return meta
        except Exception as e:
            UI.warning(f"Could not read dry-run metadata: {e}")
            return meta

    if not path.exists():
        return meta

    try:
        content = path.read_text(encoding="utf-8").strip()
        if content.startswith("{"):
            meta = json.loads(content)
        else:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    stripped_line = line.strip()
                    if (
                        stripped_line
                        and not stripped_line.startswith("#")
                        and "=" in stripped_line
                    ):
                        k, v_str = stripped_line.split("=", 1)
                        k, v_str = k.strip(), v_str.strip()
                        v: Any = v_str
                        if v_str == "None":
                            v = None
                        elif v_str.lower() == "true":
                            v = True
                        elif v_str.lower() == "false":
                            v = False
                        meta[k] = v
    except Exception as e:
        UI.warning(f"Could not read metadata at {path}: {e}")

    # Schema Validation (Hardening)
    # Ensure mandatory fields are present and valid to prevent runtime crashes
    required_keys = ["container_name", "tag", "db_type"]
    missing = [k for k in required_keys if k not in meta]
    if missing and path.name == ".liferay-docker.meta":
        # Don't warn for internal meta files or temporary ones
        UI.warning(f"Metadata in {path} is missing required keys: {', '.join(missing)}")

    # Type/Value validation for critical fields
    if meta.get("port") and not str(meta["port"]).isdigit():
        UI.warning(
            f"Invalid port value in {path}: {meta['port']}. Falling back to 8080."
        )
        meta["port"] = 8080

    return meta


def write_meta(path, meta):
    """Writes project metadata to a file (atomically)."""
    path = Path(path)
    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        content = f"# Generated by LDM ({time.strftime('%Y-%m-%dT%H:%M:%S')})\n"
        for k, v in sorted(meta.items()):
            if v is not None:
                content += f"{k}={v}\n"
        UI.info(
            f"{UI.BYELLOW}[DRY RUN] Would write metadata file:{UI.COLOR_OFF} {path}"
        )
        _DRY_RUN_VFS[str(path.resolve())] = content
        return
    try:
        # Use a more explicit tmp name to avoid issues with double suffixes
        tmp_path = path.parent / f"{path.name}.tmp"

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(f"# Generated by LDM ({time.strftime('%Y-%m-%dT%H:%M:%S')})\n")
            for k, v in sorted(meta.items()):
                if v is not None:
                    f.write(f"{k}={v}\n")

        # macOS filesystem sometimes has race conditions with renames in temp dirs
        max_retries = 3
        for i in range(max_retries):
            try:
                os.replace(tmp_path, path)
                break
            except (OSError, PermissionError) as e:
                if i == max_retries - 1:
                    # Final attempt: manual copy and unlink (robust for cross-user permissions)
                    try:
                        shutil.copyfile(tmp_path, path)
                        tmp_path.unlink()
                        break
                    except Exception:
                        raise e
                time.sleep(0.1)
    except Exception as e:
        UI.warning(f"Could not write metadata at {path}: {e}")


def find_dxp_roots(search_dir=None):
    """Discovers LDM projects in the target directory by looking for metadata or specific structure."""
    from ldm_core.constants import PROJECT_META_FILE, REGISTRY_FILE

    actual_home = get_actual_home()
    search_dirs = []

    # Check for LDM_WORKSPACE environment variable
    custom_workspace = os.environ.get("LDM_WORKSPACE")

    # Priority 1: Specific directory provided
    if search_dir:
        search_dirs.append(Path(search_dir))
    # Priority 2: Isolated workspace (Exclusive search)
    elif custom_workspace:
        # IMPORTANT: If LDM_WORKSPACE is set, we ONLY search that directory
        # to support true isolation in tests and dev environments.
        search_dirs = [Path(custom_workspace).expanduser().resolve()]
    # Priority 3: Default discovery logic (Multiple paths)
    else:
        cwd = safe_cwd()
        if cwd:
            search_dirs.append(cwd)

        # Common default locations
        for common in [actual_home / "ldm", Path("/Volumes/SanDisk/ldm")]:
            if common.exists() and common.is_dir():
                search_dirs.append(common)

    roots = []
    seen_paths = set()

    # Priority 4: Global Registry (Pruning & Inclusion)
    registry_path = actual_home / ".ldm" / REGISTRY_FILE
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text())
            dirty = False
            for name, data in list(registry.items()):
                path_str = data.get("path") if isinstance(data, dict) else data
                if path_str:
                    item = Path(path_str)
                    if item.exists() and item.is_dir():
                        abs_path = item.resolve()
                        if abs_path not in seen_paths:
                            # Verify it's still a valid project before adding
                            meta_file = item / PROJECT_META_FILE
                            if not meta_file.exists():
                                meta_file = item / ".liferay-docker.meta"

                            if meta_file.exists():
                                meta = read_meta(meta_file)
                                last_seen = (
                                    data.get("last_seen")
                                    if isinstance(data, dict)
                                    else None
                                )
                                roots.append(
                                    {
                                        "path": item,
                                        "version": meta.get("tag") or "unknown",
                                        "last_seen": last_seen,
                                    }
                                )
                                seen_paths.add(abs_path)
                    else:
                        # Path no longer exists, prune it
                        del registry[name]
                        dirty = True

            if dirty:
                safe_write_text(registry_path, json.dumps(registry, indent=4))
        except Exception:  # nosec B110
            pass

    for s_dir in search_dirs:
        if not s_dir.exists() or not s_dir.is_dir():
            continue

        is_home = s_dir.resolve() == actual_home.resolve()

        try:
            for item in s_dir.iterdir():
                if item.is_dir() and not item.name.startswith("."):
                    abs_path = item.resolve()
                    if abs_path in seen_paths:
                        continue

                    # Support multiple metadata filenames
                    found_meta: Any = None
                    for f in [PROJECT_META_FILE, ".liferay-docker.meta", ".ldm.meta"]:
                        if (item / f).exists():
                            found_meta = item / f
                            break

                    has_meta = found_meta is not None
                    has_structure = (item / "files").exists() and (
                        item / "deploy"
                    ).exists()

                    if has_meta or (not is_home and has_structure):
                        meta = read_meta(found_meta) if has_meta else {}
                        version = meta.get("tag") or "unknown"
                        roots.append({"path": item, "version": version})
                        seen_paths.add(abs_path)
        except Exception:  # nosec B112
            continue

    return sorted(roots, key=lambda x: x["path"])


def safe_extract(archive, target_path, members=None):
    """Safely extracts a Zip or Tar archive to a target path, preventing directory traversal."""
    target_path = Path(target_path).resolve()
    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        UI.info(
            f"{UI.BYELLOW}[DRY RUN] Would extract archive to:{UI.COLOR_OFF} {target_path}"
        )
        return

    if hasattr(archive, "namelist"):  # ZipFile
        safe_members = []
        archive_members = members if members is not None else archive.infolist()
        for info in archive_members:
            filename = info if isinstance(info, str) else info.filename
            # Check if ZipFile member is a symlink
            is_link = False
            if not isinstance(info, str):
                is_link = (info.external_attr >> 16) & 0o170000 == 0o120000
            link_target = None
            if is_link:
                try:
                    link_target = (
                        archive.read(filename).decode("utf-8", errors="ignore").strip()
                    )
                except Exception:
                    pass

            if not is_safe_path(target_path, filename, is_link, link_target):
                raise ValueError(
                    f"Security Block: Traversal detected in member {filename}"
                )
            safe_members.append(filename)
        archive.extractall(target_path, members=safe_members)

    elif hasattr(archive, "getmembers"):  # TarFile
        safe_members = []
        archive_members = members if members is not None else archive.getmembers()
        for member in archive_members:
            is_link = member.issym() or member.islnk()
            if not is_safe_path(target_path, member.name, is_link, member.linkname):
                raise ValueError(
                    f"Security Block: Traversal detected in member {member.name}"
                )
            safe_members.append(member)
        archive.extractall(target_path, members=safe_members)


def get_compose_cmd():
    """Returns the base command for Docker Compose v2 (Plugin). Legacy v1 is not supported."""
    docker_bin = shutil.which("docker")
    if docker_bin:
        try:
            # Verify the v2 plugin is installed and functional
            res = subprocess.run(
                [docker_bin, "compose", "version"],
                capture_output=True,
                encoding="utf-8",
                check=False,
            )
            if res.returncode == 0 and "Docker Compose version" in res.stdout:
                return ["docker", "compose"]
        except Exception:
            pass

    # Final: No working v2 Compose plugin found
    return []


def get_docker_socket_path():
    """Dynamically discovers the active Docker socket path."""
    system = platform.system().lower()
    if system in ["windows", "win32"]:
        return "//./pipe/docker_engine"

    # 1. Try to ask Docker for the current context's endpoint
    try:
        # We run this silently to avoid chicken-and-egg errors during initialization
        res = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            endpoint = res.stdout.strip()
            # Convert unix:///var/run/docker.sock to /var/run/docker.sock
            if endpoint.startswith("unix://"):
                path = endpoint.replace("unix://", "")
                if os.path.exists(path):
                    return path
    except Exception:
        pass

    # 2. Fallback to common platform defaults
    if system == "darwin":
        # Docker Desktop for Mac often uses this path even if not in the symlink
        real_socket = get_actual_home() / ".docker/run/docker.sock"
        if real_socket.exists():
            return str(real_socket)

    # Check for modern Linux socket path
    if os.path.exists("/run/docker.sock"):
        return "/run/docker.sock"

    return "/var/run/docker.sock"


def verify_executable_checksum(version):
    """Verifies the current binary against the official checksums.txt from GitHub."""
    exe_path = Path(sys.argv[0]).resolve()

    # Detection Logic:
    # 1. If it ends in .py, it's definitely source.
    # 2. If it's a frozen binary (PyInstaller), it's a binary.
    # 3. If it's a large binary data file (Shiv), it's a binary.
    is_source = exe_path.suffix.lower() == ".py"
    is_frozen = getattr(sys, "frozen", False)

    if is_source and not is_frozen:
        return "Source", True, version

    if not exe_path.exists():
        return "Source", True, version

    try:
        import hashlib
        import re

        # 1. Read content once for version extraction and hashing
        with open(exe_path, "rb") as f:
            content = f.read()

        # 1.1 "Magic Byte" Version Extraction
        # We look for the marker in the binary content to find the TRUE version.
        # If this marker exists, it's definitely a packaged binary.
        magic_match = re.search(b"LDM_MAGIC_VERSION: ([0-9.]+)", content)
        if not magic_match and is_source:
            return "Source", True, version

        if magic_match:
            extracted_version = magic_match.group(1).decode()
            # If we extracted a version that is different from what we think we are,
            # we should use the extracted one for fetching checksums.
            if extracted_version != version:
                version = extracted_version

        # 1.2 Calculate local hash
        sha = hashlib.sha256()
        sha.update(content)
        local_hash = sha.hexdigest()

        # 2. Fetch official checksums
        url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{version}/checksums.txt"
        official_data = ""
        try:
            response = requests.get(url, headers={"User-Agent": "ldm-cli"}, timeout=5)
            if response.status_code == 200:
                official_data = response.text
        except Exception:
            pass

        # 3. Identify binary name in checksum file
        # We prefer the unified 'ldm-macos' (universal2) asset
        system = platform.system().lower()
        machine = platform.machine().lower()

        candidates = []
        if system == "darwin":
            if machine == "arm64":
                candidates.append("ldm-macos-arm64")
            else:
                candidates.append("ldm-macos-x86_64")
            candidates.append("ldm-macos")  # Fallback for legacy unified binary
        elif system in ["win32", "windows"]:
            candidates.append("ldm-windows.exe")
        else:
            candidates.append("ldm-linux")

        expected_hash = None

        # Try to find the most specific asset first
        for cand in candidates:
            for line in official_data.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == cand:
                    expected_hash = parts[0]
                    break
            if expected_hash:
                break

        if expected_hash:
            if local_hash == expected_hash:
                return f"Verified ({local_hash[:12]})", True, version
            return f"TAMPERED / MISMATCH ({local_hash[:12]})", False, version

        return f"Unknown Build ({local_hash[:12]})", "warn", version
    except Exception:
        return None, "warn", version


def version_to_tuple(v):
    """Converts a SemVer-style version string to a numeric tuple for comparison.
    Ensures that stable releases (e.g. 2.4.26) rank higher than pre-releases
    (e.g. 2.4.26-beta.1) of the same base version.
    """
    if not v:
        return (0, 0, 0, 0)

    # Strip leading 'v'
    v = v.lstrip("v")

    import re

    # Safety: Return zeroed tuple if no numbers are present
    if not re.search(r"\d", v):
        return (0, 0, 0, 0)

    # Split base version from pre-release suffix

    parts = v.split("-", 1)
    base_part = parts[0]
    pre_part = parts[1] if len(parts) > 1 else None

    # Extract base numbers (major.minor.patch)
    base_nums = [int(n) for n in re.findall(r"\d+", base_part)]
    while len(base_nums) < 3:
        base_nums.append(0)

    if pre_part is None:
        # Stable Release Logic
        # If there's already a 4th numeric part (e.g. 1.2.3.4), use it.
        if len(base_nums) >= 4:
            return tuple(base_nums[:4])
        # Otherwise, use a high sentinel (999) so stable > any beta.
        return (base_nums[0], base_nums[1], base_nums[2], 999)
    # Pre-release Logic
    # We assign a weight to the prefix so 'pre' ranks higher than 'beta'
    weight = 0
    if "beta" in pre_part.lower():
        weight = 1
    elif "pre" in pre_part.lower():
        weight = 2

    # Extract the first number from the pre-release string (e.g. 'beta.1' -> 1)
    pre_nums = [int(n) for n in re.findall(r"\d+", pre_part)]
    beta_num = pre_nums[0] if pre_nums else 0
    # Use the actual number, which is naturally < 999
    return (base_nums[0], base_nums[1], base_nums[2], weight, beta_num)


def _check_updates_fallback(cache_file, now):
    """Fallback check using HTML redirect to bypass GitHub API rate limiting."""
    try:
        headers = {"User-Agent": "ldm-cli", "Cache-Control": "no-cache"}
        url = (
            "https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest"
        )
        response = requests.head(url, headers=headers, timeout=5, allow_redirects=False)
        if response.status_code in [301, 302]:
            location = response.headers.get("Location", "")
            if "/releases/tag/" in location:
                latest_version = location.split("/releases/tag/")[-1].lstrip("v")

                # Architecture-aware asset resolution
                system = sys.platform
                machine = platform.machine().lower()

                if system == "darwin":
                    if machine == "arm64":
                        target_asset = "ldm-macos-arm64"
                    else:
                        target_asset = "ldm-macos-x86_64"
                elif system in ["win32", "windows"]:
                    target_asset = "ldm-windows.exe"
                else:
                    target_asset = "ldm-linux"

                release_url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{latest_version}/{target_asset}"

                # Update cache
                try:
                    cache_file.write_text(
                        json.dumps(
                            {
                                "last_check": now,
                                "latest_version": latest_version,
                                "url": release_url,
                            }
                        )
                    )
                except Exception:
                    pass

                return latest_version, release_url
    except Exception:
        pass
    return None, None


def check_for_updates(current_version, force=False, pre_release=False, tag=None):
    """Checks GitHub for the latest release of LDM."""
    cache_suffix = "_pre" if pre_release else ""
    cache_file = Path.home() / f".ldm_update_cache{cache_suffix}"
    cache_duration = 86400  # 24 hours
    now = time.time()

    if not tag and not force and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            if now - data.get("last_check", 0) < cache_duration:
                return data.get("latest_version"), data.get("url")
        except Exception:
            pass

    try:
        headers = {"User-Agent": "ldm-cli"}
        if force or tag:
            headers["Cache-Control"] = "no-cache"

        if tag:
            # Specific release tag lookup
            if not tag.startswith("v"):
                tag = f"v{tag}"
            url = f"https://api.github.com/repos/peterrichards-lr/liferay-docker-manager/releases/tags/{tag}"
        elif pre_release:
            # Get more releases to ensure we find the latest SemVer even with re-tagging
            url = "https://api.github.com/repos/peterrichards-lr/liferay-docker-manager/releases?per_page=100"
        else:
            # Latest stable release only
            url = "https://api.github.com/repos/peterrichards-lr/liferay-docker-manager/releases/latest"

        if not url.startswith("https://"):
            raise ValueError(f"Invalid URL scheme: {url}")

        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            res_data = response.json()

            # If pre_release, we get a list of releases. We must find the highest SemVer.
            if isinstance(res_data, list):
                if not res_data:
                    return None, None

                # Find the highest version in the list
                highest_data = None
                highest_version_tuple = (0, 0, 0, 0)

                for release in res_data:
                    v_str = release.get("tag_name", "").lstrip("v")
                    v_tuple = version_to_tuple(v_str)
                    if v_tuple > highest_version_tuple:
                        highest_version_tuple = v_tuple
                        highest_data = release

                if not highest_data:
                    return None, None
                res_data = highest_data

            latest_version = res_data.get("tag_name", "").lstrip("v")

            # Architecture-aware asset resolution
            release_url = res_data.get("html_url")  # Fallback to release page
            assets = res_data.get("assets", [])

            system = sys.platform
            machine = platform.machine().lower()

            candidates = []
            if system == "darwin":
                candidates.append("ldm-macos")  # Unified/Universal2 binary
                if machine == "arm64":
                    candidates.append("ldm-macos-arm64")
                else:
                    candidates.append("ldm-macos-x86_64")
            elif system in ["win32", "windows"]:
                candidates.append("ldm-windows.exe")
            else:
                candidates.append("ldm-linux")
            # Search for the best match in assets
            found_url = None
            for cand in candidates:
                for asset in assets:
                    if asset.get("name") == cand:
                        found_url = asset.get("browser_download_url")
                        break
                if found_url:
                    break

            if found_url:
                release_url = found_url

            # Update cache (only for general update checks, not tag lookups)
            if not tag:
                cache_file.write_text(
                    json.dumps(
                        {
                            "last_check": now,
                            "latest_version": latest_version,
                            "url": release_url,
                        }
                    )
                )

            return latest_version, release_url

        if tag:
            return None, None

        # Fallback if API status code is not 200 (e.g. 403 rate limited)
        if not pre_release:
            fallback_version, fallback_url = _check_updates_fallback(cache_file, now)
            if fallback_version:
                return fallback_version, fallback_url

    except Exception:
        if tag:
            return None, None
        # Fallback if request failed completely (e.g. API DNS/connection error)
        if not pre_release:
            try:
                fallback_version, fallback_url = _check_updates_fallback(
                    cache_file, now
                )
                if fallback_version:
                    return fallback_version, fallback_url
            except Exception:
                pass
        return None, None
    return None, None


def calculate_sha256(file_path):
    """Calculates the SHA-256 hash of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha.update(chunk)
    return sha.hexdigest()


def fetch_compatibility_metadata(force=False):
    """Fetches and caches the project compatibility matrix from GitHub."""
    cache_dir = get_actual_home() / ".ldm" / "cache"
    cache_file = cache_dir / "compatibility.json"
    cache_duration = 86400  # 24 hours

    # 1. Load bundled version as baseline
    bundled_file = Path(__file__).parent.parent / "compatibility.json"
    baseline = {}
    if bundled_file.exists():
        with contextlib.suppress(Exception):
            baseline = json.loads(bundled_file.read_text())

    # 2. Check cache
    if not force and cache_file.exists():
        # Check file age
        if time.time() - cache_file.stat().st_mtime < cache_duration:
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass

    # 3. Fetch from Master (Evergreen source)
    url = "https://raw.githubusercontent.com/peterrichards-lr/liferay-docker-manager/master/compatibility.json"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if download_file(url, cache_file):
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            return baseline
    return baseline


def resolve_dependency_version(liferay_tag, dependency_name):
    """Resolves the best matching version for a dependency based on the Liferay tag."""
    matrix = fetch_compatibility_metadata()
    if not matrix or "mappings" not in matrix:
        # Fallback to hardcoded constants if metadata fails
        from ldm_core.constants import ELASTICSEARCH7_VERSION, ELASTICSEARCH_VERSION

        if dependency_name == "elasticsearch":
            v_tuple = version_to_tuple(liferay_tag)
            return (
                ELASTICSEARCH_VERSION
                if v_tuple >= (2024, 1, 0)
                else ELASTICSEARCH7_VERSION
            )
        return None

    # Sort mappings by tag precedence (highest first)
    # This allows us to find the first >= match in descending order
    def get_range_val(entry):
        r = entry.get("tag_range", "").replace(">=", "")
        return version_to_tuple(r)

    mappings = sorted(matrix["mappings"], key=get_range_val, reverse=True)

    tag_tuple = version_to_tuple(liferay_tag)

    for entry in mappings:
        range_str = entry.get("tag_range", "")
        if range_str.startswith(">="):
            range_val = range_str.replace(">=", "")
            if tag_tuple >= version_to_tuple(range_val):
                return entry.get("dependencies", {}).get(dependency_name)

    return None


def safe_mkdir(path, parents=True, exist_ok=True):
    """Creates a directory safely, with JIT permission reclamation if needed."""
    path_obj = Path(path).resolve()
    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        UI.info(
            f"{UI.BYELLOW}[DRY RUN] Would create directory:{UI.COLOR_OFF} {path_obj}"
        )
        return
    try:
        path_obj.mkdir(parents=parents, exist_ok=exist_ok)
    except (OSError, PermissionError) as e:
        if platform.system().lower() != "windows":
            # Attempt to reclaim parent
            if reclaim_volume_permissions(path_obj.parent):
                path_obj.mkdir(parents=parents, exist_ok=exist_ok)
            else:
                raise e
        else:
            raise e


def verify_safe_to_delete(path):
    """Raises ValueError if the path matches safety-critical directories (home, system roots, active CWD, LDM source, or git repos)."""
    import os

    path_obj = Path(path).resolve()

    # 1. Block home directory and system roots
    actual_home = get_actual_home().resolve()
    if path_obj == actual_home or path_obj == Path.home().resolve():
        raise ValueError(f"Safety Violation: Cannot delete home directory: {path_obj}")

    system_roots = [
        Path("/"),
        Path("/usr"),
        Path("/var"),
        Path("/etc"),
        Path("/bin"),
        Path("/lib"),
        Path("/private"),
        Path("/Users"),
        Path("/Volumes"),
    ]
    if path_obj in system_roots or any(
        path_obj == r.resolve() for r in system_roots if os.path.exists(r)
    ):
        raise ValueError(
            f"Safety Violation: Cannot delete system directory: {path_obj}"
        )

    # 2. Block the current working directory or any of its parent folders
    cwd = safe_cwd()
    if cwd:
        cwd_resolved = cwd.resolve()
        if cwd_resolved == path_obj or path_obj in cwd_resolved.parents:
            raise ValueError(
                f"Safety Violation: Cannot delete current working directory or its parent: {path_obj}"
            )

    # 3. Block deletion of the LDM installation/source directory or any of its parents
    try:
        pkg_dir = Path(__file__).parent.parent.resolve()
        if path_obj == pkg_dir or path_obj in pkg_dir.parents:
            raise ValueError(
                f"Safety Violation: Cannot delete LDM installation/source directory: {path_obj}"
            )
    except Exception as e:
        if isinstance(e, ValueError):
            raise e

    # 4. Block directory if it contains .git
    git_dir = path_obj / ".git"
    if os.path.exists(git_dir):
        raise ValueError(
            f"Safety Violation: Cannot delete a git repository: {path_obj}"
        )


def safe_rmtree(path):
    """Deletes a directory tree safely, with JIT permission reclamation if needed."""
    path_obj = Path(path).resolve()
    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        UI.info(
            f"{UI.BYELLOW}[DRY RUN] Would delete directory tree:{UI.COLOR_OFF} {path_obj}"
        )
        return
    verify_safe_to_delete(path_obj)
    if not path_obj.exists():
        return

    import stat

    def remove_readonly(func, p, excinfo):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            if excinfo and len(excinfo) > 1 and excinfo[1] is not None:
                raise excinfo[1]
            raise

    try:
        shutil.rmtree(path_obj, onerror=remove_readonly)
    except (OSError, PermissionError) as e:
        if platform.system().lower() != "windows":
            if reclaim_volume_permissions(path_obj):
                shutil.rmtree(path_obj, onerror=remove_readonly)
            else:
                raise e
        else:
            raise e


def reclaim_volume_permissions(path, uid=None, gid=None, chmod_val="777"):
    """Forces ownership and permissions of a directory via Docker (Linux/macOS)."""
    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        return True
    import platform
    from pathlib import Path

    system_type = platform.system().lower()
    if system_type not in ["darwin", "linux"]:
        return True

    if not Path(path).exists():
        return True

    if uid is None:
        uid = str(os.getuid()) if hasattr(os, "getuid") else "1000"
    if gid is None:
        gid = str(os.getgid()) if hasattr(os, "getgid") else "1000"

    from ldm_core.ui import UI

    UI.detail(f"Reclaiming permissions for: {path}")

    docker_cmd = f"chown -R {uid}:{gid} /workspace; chmod -R {chmod_val} /workspace; "

    try:
        res = run_command(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{Path(path).as_posix()}:/workspace",
                "alpine",
                "sh",
                "-c",
                docker_cmd,
            ],
            check=False,
            capture_output=True,
            timeout=15,
        )
        return res is not None
    except Exception:
        return False


def get_all_options(parser):
    """Recursively extract all option strings from an argparse parser."""
    import argparse

    options = set()
    for action in parser._actions:
        for opt in action.option_strings:
            options.add(opt)
    # Walk subparsers
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for choices in action.choices.values():
                options.update(get_all_options(choices))
    return options


def verify_cli_drift():
    """Verify that all CLI options in cli.py are documented in the guides.

    Returns:
        int: 0 if check passes, 1 if drift is found.
    """
    import sys
    from pathlib import Path

    from ldm_core.cli import get_parser

    # Get all options from the parser
    parser, _ = get_parser()
    options = get_all_options(parser)

    # Ignore standard help options
    options.discard("-h")
    options.discard("--help")

    # Read documentation files
    project_root = Path(__file__).parent.parent
    doc_paths = [
        project_root / "docs" / "reference" / "cli" / "core.md",
        project_root / "docs" / "reference" / "cli" / "data.md",
        project_root / "docs" / "reference" / "cli" / "system.md",
        project_root / "docs" / "reference" / "advanced_cli.md",
    ]

    doc_text = ""
    for path in doc_paths:
        if path.exists():
            doc_text += "\n" + path.read_text(encoding="utf-8")

    # 1. Check for options in parser but missing in docs (undocumented options)
    missing_docs = []
    for opt in sorted(options):
        # Check if option string is documented
        if opt not in doc_text:
            missing_docs.append(opt)

    # 2. Check for options in docs but missing in parser (stale/removed options)
    import re

    documented_opts = set(re.findall(r"\`(-[a-zA-Z0-9-]+|--[a-zA-Z0-9-]+)\`", doc_text))
    # Discard non-ldm documented strings (like JVM exports or standard help)
    for external in ["--add-opens", "-h", "--help"]:
        documented_opts.discard(external)

    missing_parser = []
    for opt in sorted(documented_opts):
        if opt not in options:
            missing_parser.append(opt)

    if missing_docs or missing_parser:
        if missing_docs:
            print(
                "❌ CLI Documentation Drift Detected (Undocumented options)!",
                file=sys.stderr,
            )
            print(
                "The following CLI options exist in parser but are not documented in docs/guides/:",
                file=sys.stderr,
            )
            for opt in missing_docs:
                print(f"  - {opt}", file=sys.stderr)
        if missing_parser:
            print(
                "❌ CLI Documentation Drift Detected (Stale/removed options)!",
                file=sys.stderr,
            )
            print(
                "The following CLI options are documented in docs/guides/ but no longer exist in parser:",
                file=sys.stderr,
            )
            for opt in missing_parser:
                print(f"  - {opt}", file=sys.stderr)
        print(
            "\nPlease sync ldm_core/cli.py and docs/reference/cli/*.md or docs/reference/advanced_cli.md.",
            file=sys.stderr,
        )
        return 1

    print("✅ No CLI documentation drift detected.")
    return 0


def check_troubleshooting_signatures(line):
    """Checks a log line against known error signatures and returns advice.

    Args:
        line (str): The log line to analyze.

    Returns:
        str | None: The troubleshooting advice, or None if no match.
    """
    import re

    SIGNATURES = {
        r"Unable to create lock manager|access_denied_exception|LockManager": (
            "Host POSIX filesystem lock conflict detected. macOS/Windows hypervisors sometimes deadlock volume directories. Run 'ldm rescue' to release stale POSIX locks."
        ),
        r"Connection to .* refused|Connection refused|psycopg2\.OperationalError": (
            "Database connection refused. The database container might still be starting up or unhealthy. Verify its status via 'ldm status'."
        ),
        r"database .* does not exist|FATAL:\s+database .* does not exist": (
            "Target database does not exist. Your LDM stack is running in PostgreSQL mode but the schema has not been initialized. Run 'ldm hydrate' to import a snapshot."
        ),
        r"ReservedCodeCacheSize|CodeCache|OutOfMemory": (
            "JVM CodeCache or heap space exhausted. Set 'ReservedCodeCacheSize=512m' in portal properties or upgrade LDM JVM self-tuning configurations."
        ),
        r"ClusterBlockException|index\.blocks\.read_only": (
            "Elasticsearch write block detected due to low disk space threshold. LDM will attempt to auto-thaw or you can clear disk space."
        ),
    }

    for pattern, advice in SIGNATURES.items():
        if re.search(pattern, line, re.IGNORECASE):
            return advice
    return None


def resolve_infrastructure_mode(mode_key, meta, defaults, args_override=None):
    """
    Resolves the infrastructure mode (database_mode or search_mode), respecting legacy fallbacks.
    Prioritizes: 1. CLI Override, 2. Project Meta, 3. Defaults (with version-aware fallbacks).
    """
    from packaging.version import parse as parse_version

    if args_override:
        return args_override

    meta_val = meta.get(mode_key)
    if meta_val:
        return meta_val

    # Handle legacy boolean flags
    if (
        mode_key == "search_mode"
        and str(meta.get("use_shared_search")).lower() == "true"
    ):
        return "shared"

    ldm_version = meta.get("ldm_version", "0.0.0")
    # Projects created before v2.14.0 must fallback to legacy isolated/sidecar modes
    # to prevent accidentally hijacking a local sidecar into shared infrastructure.
    if parse_version(ldm_version) < parse_version("2.14.0"):
        if mode_key == "database_mode":
            return "isolated"
        if mode_key == "search_mode":
            return "sidecar"

    # Modern defaults (v2.14.0+)
    if mode_key == "database_mode":
        return defaults.get("database_mode", "shared")
    if mode_key == "search_mode":
        return defaults.get("search_mode", "shared")

    return defaults.get(mode_key)


def has_shared_projects(manager):
    """Returns True if any registered project utilizes shared database or search infrastructure."""
    if not hasattr(manager, "registry"):
        return False

    projects = manager.registry.get_projects()
    for _project_id, project_data in projects.items():
        from pathlib import Path

        path = Path(project_data.get("path", ""))
        if not path.exists():
            continue
        meta = manager.read_meta(path) or {}
        db_mode = resolve_infrastructure_mode("database_mode", meta, manager.defaults)
        search_mode = resolve_infrastructure_mode("search_mode", meta, manager.defaults)
        if db_mode == "shared" or search_mode == "shared":
            return True
    return False


def is_continuation_line(line_str: str) -> bool:
    """Checks if a properties line ends in an active continuation backslash.

    Count trailing backslashes:
    - Odd count = active continuation.
    - Even count = escaped backslash (not continuation).
    """
    stripped = line_str.rstrip(" \t\r\n")
    backslash_count = 0
    for char in reversed(stripped):
        if char == "\\":
            backslash_count += 1
        else:
            break
    return (backslash_count % 2) != 0


class FileLock:
    """Cross-platform non-blocking file lock."""

    def __init__(self, lock_file_path):
        self.lock_file = Path(lock_file_path)
        self.fd = None

    def acquire(self, mode="w+", exclusive=True):
        """Acquires a non-blocking flock. Employs fcntl.flock on Unix systems and msvcrt.locking (locking the first byte of the file) on Windows."""
        import atexit

        self.lock_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._try_acquire(mode, exclusive)
            atexit.register(self.release)
        except RuntimeError:
            stale = False
            try:
                with open(self.lock_file) as f:
                    content = f.read().strip()
                    if content.isdigit():
                        pid = int(content)
                        try:
                            import psutil

                            if not psutil.pid_exists(pid):
                                stale = True
                        except ImportError:
                            if os.name == "nt":
                                import ctypes

                                process = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                                    0x00100000, 0, pid
                                )
                                if process != 0:
                                    ctypes.windll.kernel32.CloseHandle(process)  # type: ignore[attr-defined]
                                else:
                                    stale = True
                            else:
                                try:
                                    os.kill(pid, 0)
                                except ProcessLookupError:
                                    stale = True
                                except PermissionError:
                                    pass
            except Exception:
                pass

            if stale:
                try:
                    self.lock_file.unlink()
                except OSError:
                    pass
                self._try_acquire(mode, exclusive)
                atexit.register(self.release)
            else:
                raise RuntimeError(f"Could not acquire lock on file: {self.lock_file}")

    def _try_acquire(self, mode: str, exclusive: bool):
        self.fd = open(self.lock_file, mode)  # noqa: SIM115
        try:
            if os.name != "nt":
                import fcntl

                lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(self.fd, lock_type | fcntl.LOCK_NB)
            else:
                import msvcrt

                self.fd.seek(0)
                lock_type = msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK  # type: ignore[attr-defined]
                msvcrt.locking(self.fd.fileno(), lock_type, 1)  # type: ignore[attr-defined]

            if exclusive and ("w" in mode or "+" in mode):
                self.fd.truncate(0)
                self.fd.seek(0)
                self.fd.write(str(os.getpid()))
                self.fd.flush()

        except (BlockingIOError, PermissionError, OSError):
            if self.fd:
                self.fd.close()
                self.fd = None
            raise RuntimeError(f"Could not acquire lock on file: {self.lock_file}")

    def release(self):
        """Releases the active file lock and unregisters the exit handler."""
        import atexit

        atexit.unregister(self.release)
        if self.fd:
            try:
                if os.name != "nt":
                    import fcntl

                    fcntl.flock(self.fd, fcntl.LOCK_UN)
                else:
                    import msvcrt

                    self.fd.seek(0)
                    msvcrt.locking(self.fd.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            except Exception:
                pass
            self.fd.close()
            self.fd = None
            with contextlib.suppress(OSError):
                if self.lock_file.exists():
                    self.lock_file.unlink()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class ProjectLock:
    """Application-level concurrency lock for workspace projects."""

    def __init__(self, project_path):
        self.lock_file = Path(project_path) / ".liferay-docker" / ".ldm_lock"
        self.fd = None

    def acquire(self):
        """Acquires a non-blocking exclusive file lock on the project."""
        import atexit

        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.lock_file, "w")  # noqa: SIM115
        try:
            if os.name != "nt":
                import fcntl

                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt

                self.fd.seek(0)
                msvcrt.locking(self.fd.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]

            self.fd.write(f"PID: {os.getpid()}\n")
            self.fd.flush()
            atexit.register(self.release)
        except (BlockingIOError, PermissionError, OSError):
            self.fd.close()
            self.fd = None
            raise RuntimeError(
                "Concurrency Violation: Another instance of LDM is running on this project."
            )

    def release(self):
        """Releases the lock and deletes the lock file."""
        import atexit

        atexit.unregister(self.release)
        if self.fd:
            try:
                if os.name != "nt":
                    import fcntl

                    fcntl.flock(self.fd, fcntl.LOCK_UN)
                else:
                    import msvcrt

                    self.fd.seek(0)
                    msvcrt.locking(self.fd.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            except Exception:
                pass
            self.fd.close()
            self.fd = None
            with contextlib.suppress(OSError):
                if self.lock_file.exists():
                    self.lock_file.unlink()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def load_global_config_safe(config_path: Path) -> dict:
    """Loads global JSON config file with shared/exclusive locking and validation."""
    if not config_path.exists():
        return {}

    lock_file = config_path.with_suffix(config_path.suffix + ".lock")
    lock = FileLock(lock_file)
    try:
        lock.acquire(exclusive=False)
        content = config_path.read_text(encoding="utf-8")
        if not content.strip():
            return {}
        return json.loads(content)
    except json.JSONDecodeError as e:
        UI.warning(
            f"Configuration file '{config_path}' contains invalid JSON syntax:\n"
            f"  {e.msg} at line {e.lineno}, column {e.colno}"
        )
        return {}
    except Exception as e:
        UI.warning(f"Failed to read configuration file '{config_path}': {e}")
        return {}
    finally:
        lock.release()


def save_global_config_safe(config_path: Path, data: dict) -> bool:
    """Saves global JSON config file securely and atomically with exclusive locking."""
    lock_file = config_path.with_suffix(config_path.suffix + ".lock")
    lock = FileLock(lock_file)
    try:
        lock.acquire(exclusive=True)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if platform.system().lower() != "windows":
            try:
                config_path.parent.chmod(0o700)
            except OSError:
                pass
        temp_file = config_path.with_suffix(config_path.suffix + ".tmp")

        # Write securely with 0600 permissions
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(temp_file, flags, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=4))

        temp_file.replace(config_path)
        if platform.system().lower() != "windows" and config_path.exists():
            config_path.chmod(0o600)
        return True
    except Exception as e:
        UI.warning(f"Failed to write configuration file '{config_path}': {e}")
        return False
    finally:
        lock.release()


def get_keyring_token(service_name: str, username: str) -> str | None:
    """Retrieves the token from the OS-native credential vault."""
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        or os.environ.get("LDM_NO_KEYRING") == "1"
    ):
        return None

    if keyring is not None:
        try:
            return keyring.get_password(service_name, username)
        except Exception as e:
            UI.debug(f"Failed to retrieve password from keyring: {e}")
    return None


def set_keyring_token(service_name: str, username: str, token: str) -> bool:
    """Saves the token to the OS-native credential vault."""
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        or os.environ.get("LDM_NO_KEYRING") == "1"
    ):
        return False

    if keyring is not None:
        try:
            keyring.set_password(service_name, username, token)
            return True
        except Exception as e:
            UI.debug(f"Failed to store password in keyring: {e}")
    return False


def is_safe_path(
    target_path: Path,
    member_name: str,
    is_link: bool = False,
    link_target: str | None = None,
) -> bool:
    """Verifies that a member name and optional link target resolve inside target_path."""
    try:
        target_path = Path(target_path).resolve()

        # Prevent Zip Slip / Tar Slip directory traversal vulnerabilities by rejecting traversal segments or absolute paths
        if Path(member_name).is_absolute() or ".." in member_name.split("/"):
            return False

        member_path = (target_path / member_name).resolve()
        if target_path not in member_path.parents and member_path != target_path:
            return False

        # Enforce that symbolic link destinations also resolve within the extraction root
        if is_link and link_target:
            if Path(link_target).is_absolute() or link_target.startswith("/"):
                return False

            link_dir = member_path.parent
            link_dest = (link_dir / link_target).resolve()
            if target_path not in link_dest.parents and link_dest != target_path:
                return False

        return True
    except Exception:
        return False
