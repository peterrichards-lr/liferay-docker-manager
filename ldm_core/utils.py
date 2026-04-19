import os
import sys
import platform
import json
import time
import subprocess
import re
import shutil
import zipfile
from pathlib import Path
import requests
from ldm_core.ui import UI
from ldm_core.constants import TAG_PATTERN


def download_file(url, destination):
    """Downloads a file from a URL to a destination path."""
    try:
        if not url.startswith("https://"):
            raise ValueError(f"Invalid URL scheme: {url}")

        response = requests.get(
            url, headers={"User-Agent": "ldm-cli"}, timeout=30, stream=True
        )
        response.raise_for_status()
        with open(destination, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        UI.error(f"Download failed: {e}")
        return False


def get_seed_url(tag, db="postgresql", search="shared"):
    """Checks GitHub for a seeded state asset matching the given Liferay configuration."""
    from ldm_core.constants import SEED_VERSION, VERSION

    tag_name = f"v{VERSION}"

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
        except Exception:
            continue
    return None


def download_samples(version, destination):
    """Downloads and extracts the samples pack from GitHub."""
    url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{version}/samples.zip"

    # Ensure parent directory exists for the temp zip
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_zip = destination.parent / f"samples_{version}.zip"

    try:
        UI.info(f"Downloading sample pack v{version}...")
        if not url.startswith("https://"):
            raise ValueError(f"Invalid URL scheme: {url}")

        response = requests.get(
            url, headers={"User-Agent": "ldm-cli"}, timeout=15, stream=True
        )
        response.raise_for_status()
        with open(temp_zip, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        UI.info("Extracting samples...")
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(temp_zip, "r") as zip_ref:
            # We assume the zip contains a 'samples/' folder at the root
            # We extract to a temporary dir and then move content to destination
            extract_temp = destination.parent / f"temp_samples_{version}"
            if extract_temp.exists():
                shutil.rmtree(extract_temp)
            extract_temp.mkdir(parents=True)
            zip_ref.extractall(extract_temp)

            # Move content from temp to destination
            # Structure might be 'samples/*' or just '*'
            source_root = extract_temp
            inner_samples = extract_temp / "samples"
            if inner_samples.exists() and inner_samples.is_dir():
                source_root = inner_samples

            for item in source_root.iterdir():
                target = destination / item.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        os.remove(target)
                shutil.move(str(item), str(target))

            shutil.rmtree(extract_temp)

        if temp_zip.exists():
            os.remove(temp_zip)

        UI.success("Sample pack ready.")
        return True
    except Exception as e:
        UI.error(f"Failed to download samples: {e}")
        if temp_zip.exists():
            os.remove(temp_zip)
        return False


def load_env_blacklist(path):
    """Loads environment variable blacklist patterns from a file."""
    patterns = []
    if not path or not path.exists():
        return patterns
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
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

    return re.sub(r"[^a-zA-Z0-9\-_.]", "", str(identifier))


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
    cmd, shell=False, capture_output=True, check=True, env=None, cwd=None, verbose=False
):
    if env is None:
        env = os.environ.copy()
    else:
        env = env.copy()

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

    # Redact sensitive info for logging/display
    display_cmd = UI.redact(" ".join(cmd) if isinstance(cmd, list) else cmd)
    if verbose:
        UI.debug(f"Executing: {display_cmd}")

    try:
        # Bandit: B602 (shell=True) is used for complex commands where needed,
        # B603 (subprocess_without_shell_equals_true) is safe as we now use absolute paths.
        result = subprocess.run(  # nosec B602 B603
            cmd,
            shell=shell,
            capture_output=capture_output,
            text=True,
            check=check,
            env=env,
            cwd=cwd,
        )

        if result.returncode != 0 and not check:
            return None
        return result.stdout.strip() if result.stdout else ""
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
            if e.stderr:
                print(f"{UI.WHITE}Error Details:{UI.COLOR_OFF} {e.stderr.strip()}")
            sys.exit(e.returncode)
        return None
    except KeyboardInterrupt:
        raise


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


def safe_write_text(path, content, encoding="utf-8"):
    """Atomically writes text to a file using a temporary file and rename."""
    path = Path(path).resolve()
    tmp_path = path.with_suffix(".tmp" + path.suffix)
    try:
        tmp_path.write_text(content, encoding=encoding)
        os.replace(tmp_path, path)
    except Exception as e:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise e


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


def open_browser(url):
    """Launches the system browser, with special handling for WSL to use the host browser."""
    import webbrowser

    system = platform.system().lower()

    # 1. Detect WSL
    is_wsl = False
    if system == "linux":
        try:
            with open("/proc/version", "r") as f:
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


def discover_latest_tag(
    api_url, release_type="any", prefix_filter=None, verbose=False, refresh=False
):
    cache_path = get_actual_home() / ".liferay_docker_cache.json"
    cache_key = f"{api_url}_{release_type}_{prefix_filter}"

    if not refresh and cache_path.exists():
        try:
            with open(cache_path, "r") as f:
                cache = json.load(f)
                if cache_key in cache:
                    entry = cache[cache_key]
                    if time.time() - entry.get("timestamp", 0) < 86400:
                        val = entry.get("tag")
                        return val if val != "" else ""
        except Exception:
            pass

    if verbose:
        print("Initial tag discovery (this may take a some seconds)...")
    start_time = time.time()

    # Strategy:
    # 1. If we have a prefix, we use '-last_updated' + name filter. This is fast and correct
    #    because all results share the prefix.
    # 2. If no prefix, we use '-last_updated' but fetch the first 3 pages (600 tags).
    #    The true latest release is almost certainly in the last 600 updates.
    #    We then sort these by version name locally.

    url = api_url.replace("ordering=name", "ordering=-last_updated")

    api_filter = prefix_filter
    if not api_filter:
        if release_type == "lts" or release_type == "u":
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

        data = get_json(url)
        if not data:
            break

        for result in data.get("results", []):
            name = result["name"]

            # 1. Local prefix check
            if prefix_filter and not name.startswith(prefix_filter):
                continue

            # 2. Local release type check
            if release_type == "lts" and "-lts" not in name:
                continue
            if release_type == "u" and "-u" not in name:
                continue

            is_valid = bool(re.match(TAG_PATTERN, name))
            if is_valid:
                tags.append(name)

        url = data.get("next")

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

    try:
        cache = {}
        if cache_path.exists():
            with open(cache_path, "r") as f:
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


def dict_to_yaml(d, indent=0):
    lines = []
    spaces = "  " * indent
    if isinstance(d, dict):
        for k, v in d.items():
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                if not v:
                    continue
                lines.append(f"{spaces}{k}:")
                lines.append(dict_to_yaml(v, indent + 1))
            else:
                if isinstance(v, bool):
                    v = str(v).lower()
                elif isinstance(v, (int, float)):
                    v = str(v)
                elif isinstance(v, str):
                    escaped = v.replace('"', '\\"')
                    v = f'"{escaped}"'
                lines.append(f"{spaces}{k}: {v}")
    elif isinstance(d, list):
        for item in d:
            if item is None:
                continue
            if isinstance(item, (dict, list)):
                lines.append(f"{spaces}-")
                lines.append(dict_to_yaml(item, indent + 1))
            else:
                if isinstance(item, bool):
                    item = str(item).lower()
                elif isinstance(item, (int, float)):
                    item = str(item)
                elif isinstance(item, str):
                    escaped = item.replace('"', '\\"')
                    item = f'"{escaped}"'
                lines.append(f"{spaces}- {item}")
    return "\n".join(lines)


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
    """Reads LDM project metadata from a file."""
    meta = {}
    path = Path(path)
    if not path.exists():
        return meta
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if v == "None":
                    v = None
                elif v == "True" or v == "true":
                    v = True
                elif v == "False" or v == "false":
                    v = False
                meta[k] = v

    # Schema Validation
    required_keys = ["container_name", "tag"]
    missing = [k for k in required_keys if k not in meta]
    if missing:
        UI.warning(f"Metadata in {path} is missing required keys: {', '.join(missing)}")

    return meta


def find_dxp_roots(search_dir=None):
    """Discovers LDM projects in the target directory by looking for metadata or specific structure."""
    from ldm_core.constants import PROJECT_META_FILE

    search_dirs = []
    if search_dir:
        search_dirs.append(Path(search_dir))
    else:
        search_dirs.append(Path.cwd())
        # Include custom workspace if set
        custom_workspace = os.environ.get("LDM_WORKSPACE")
        if custom_workspace:
            search_dirs.append(Path(custom_workspace))

        # Common default locations
        actual_home = get_actual_home()
        for common in [actual_home / "ldm", Path("/Volumes/SanDisk/ldm")]:
            if common.exists() and common.is_dir():
                search_dirs.append(common)

    roots = []
    seen_paths = set()

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

                    has_meta = (item / PROJECT_META_FILE).exists()
                    has_structure = (item / "files").exists() and (
                        item / "deploy"
                    ).exists()

                    if has_meta or (not is_home and has_structure):
                        meta = read_meta(item / PROJECT_META_FILE)
                        version = meta.get("tag") or "unknown"
                        roots.append({"path": item, "version": version})
                        seen_paths.add(abs_path)
        except Exception:  # nosec B112
            continue

    return sorted(roots, key=lambda x: x["path"])


def safe_extract(archive, target_path):
    """Safely extracts a Zip or Tar archive to a target path, preventing Zip Slip."""
    target_path = Path(target_path).resolve()

    if hasattr(archive, "namelist"):  # ZipFile
        for member in archive.namelist():
            member_path = (target_path / member).resolve()
            if not is_within_root(member_path, target_path):
                raise Exception(f"Potential Zip Slip attempt: {member}")
        archive.extractall(target_path)
    elif hasattr(archive, "getmembers"):  # TarFile
        for member in archive.getmembers():
            member_path = (target_path / member.name).resolve()
            if not is_within_root(member_path, target_path):
                raise Exception(f"Potential Zip Slip attempt: {member.name}")
        archive.extractall(target_path)


def get_compose_cmd():
    """Returns the base command for Docker Compose v2 (Plugin). Legacy v1 is not supported."""
    docker_bin = shutil.which("docker")
    if docker_bin:
        try:
            # Verify the v2 plugin is installed and functional
            res = subprocess.run(
                [docker_bin, "compose", "version"],
                capture_output=True,
                text=True,
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
            text=True,
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
            candidates.append("ldm-macos")  # Unified/Universal2 binary
            if machine == "arm64":
                candidates.append("ldm-macos-arm64")
            else:
                candidates.append("ldm-macos-x86_64")
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
            else:
                return f"TAMPERED / MISMATCH ({local_hash[:12]})", False, version

        return f"Unknown Build ({local_hash[:12]})", "warn", version
    except Exception:
        return None, "warn", version


def version_to_tuple(v):
    """Converts a version string (e.g. '1.5.4') to a numeric tuple for comparison."""
    if not v:
        return (0, 0, 0)
    # Strip leading 'v' if present
    v = v.lstrip("v")
    try:
        return tuple(map(int, (v.split(".") + ["0", "0"])[:3]))
    except (ValueError, TypeError):
        return (0, 0, 0)


def check_for_updates(current_version, force=False):
    """Checks GitHub for the latest release of LDM."""
    cache_file = Path.home() / ".ldm_update_cache"
    cache_duration = 86400  # 24 hours
    now = time.time()

    if not force and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            if now - data.get("last_check", 0) < cache_duration:
                return data.get("latest_version"), data.get("url")
        except Exception:
            pass

    try:
        url = "https://api.github.com/repos/peterrichards-lr/liferay-docker-manager/releases/latest"
        if not url.startswith("https://"):
            raise ValueError(f"Invalid URL scheme: {url}")

        response = requests.get(url, headers={"User-Agent": "ldm-cli"}, timeout=5)
        if response.status_code == 200:
            res_data = response.json()
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

            # Update cache
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
    except Exception:
        # Fail gracefully for background checks
        return None, None
    return None, None
