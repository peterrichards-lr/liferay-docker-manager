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
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from ldm_core.ui import UI
from ldm_core.constants import TAG_PATTERN


def download_samples(version, destination):
    """Downloads and extracts the samples pack from GitHub."""
    url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{version}/samples.zip"
    temp_zip = destination.parent / f"samples_{version}.zip"

    try:
        UI.info(f"Downloading sample pack v{version}...")
        if not url.startswith("https://"):
            raise ValueError(f"Invalid URL scheme: {url}")

        req = Request(url, headers={"User-Agent": "ldm-cli"})
        with urlopen(req, timeout=15) as response:  # nosec B310
            with open(temp_zip, "wb") as f:
                shutil.copyfileobj(response, f)

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

            # Move content from temp/samples/* to destination/*
            inner_samples = extract_temp / "samples"
            if inner_samples.exists():
                for item in inner_samples.iterdir():
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


def run_command(cmd, shell=False, capture_output=True, check=True, env=None, cwd=None):
    if env is None:
        env = os.environ.copy()
    else:
        env = env.copy()

    env["DOCKER_CLI_HINTS"] = "false"

    # Automatically resolve absolute paths for list-based commands (resolves Bandit B607)
    if isinstance(cmd, list) and len(cmd) > 0 and not shell:
        executable = shutil.which(cmd[0])
        if executable:
            cmd[0] = executable

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
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd

            if isinstance(e, FileNotFoundError):
                UI.error(
                    f"Command not found: {cmd[0] if isinstance(cmd, list) else cmd}"
                )
                import sys

                sys.exit(127)

            UI.error(f"Command failed (Exit {e.returncode}): {cmd_str}")
            if e.stderr:
                print(f"{UI.WHITE}Error Details:{UI.COLOR_OFF} {e.stderr.strip()}")
            import sys

            sys.exit(e.returncode)
        return None
    except KeyboardInterrupt:
        raise


def get_json(url):
    try:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid URL scheme: {url}")

        # Bandit: B310 (urllib-urlopen) is safe as we are fetching from trusted Liferay APIs.
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req) as response:  # nosec B310
            return json.loads(response.read().decode())
    except Exception as e:
        UI.error(f"Failed to fetch data: {e}")
        return None


def get_actual_home():
    """Returns the home directory of the real user, even when running with sudo."""
    import getpass

    real_user = (
        os.environ.get("SUDO_USER") or os.environ.get("USER") or getpass.getuser()
    )
    if platform.system() == "darwin" and real_user:
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

    url = api_url
    if release_type == "lts":
        url += "&name=-lts"
    elif release_type == "u":
        url += "&name=-u"

    tags = []
    page = 0
    while url:
        page += 1
        if verbose:
            sys.stdout.write(f"\rFetching page {page}...")
            sys.stdout.flush()

        data = get_json(url)
        if not data:
            break

        for result in data.get("results", []):
            name = result["name"]
            if prefix_filter and not name.startswith(prefix_filter):
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


def is_within_root(path, root):
    try:
        path = Path(path).resolve()
        root = Path(root).resolve()
        return root in path.parents or path == root
    except Exception:
        return False


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
    """Returns the correct base command for Docker Compose (Modern v2 vs Legacy v1)."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    # 0. The Intel Exception: On older Intel Macs (Monterey and below),
    # the v2 plugin is often broken or misidentified. Prefer standalone v1.
    if system == "darwin" and ("x86" in machine or "i386" in machine):
        legacy_bin = shutil.which("docker-compose")
        if legacy_bin:
            return ["docker-compose"]

    # 1. Try modern 'docker compose' (v2 plugin)
    docker_bin = shutil.which("docker")
    if docker_bin:
        try:
            # We must check the output as some environments return 0 for unknown subcommands
            res = subprocess.run(
                [docker_bin, "compose", "version"],
                capture_output=True,
                text=True,
                check=False,
            )
            # Verify it actually reported a version and didn't just error out
            if res.returncode == 0 and "Docker Compose version" in res.stdout:
                return ["docker", "compose"]
        except Exception:
            pass

    # 2. Fallback to legacy 'docker-compose' (v1 standalone)
    legacy_bin = shutil.which("docker-compose")
    if legacy_bin:
        return ["docker-compose"]

    # Final: No working Compose found
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
    if not exe_path.exists() or exe_path.suffix.lower() == ".py":
        return "Source", True

    try:
        import hashlib

        # 1. Calculate local hash
        sha = hashlib.sha256()
        with open(exe_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha.update(chunk)
        local_hash = sha.hexdigest()

        # 2. Fetch official checksums
        url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{version}/checksums.txt"
        req = Request(url, headers={"User-Agent": "ldm-cli"})
        official_data = ""
        with urlopen(req, timeout=5) as response:  # nosec B310
            official_data = response.read().decode()

        # 3. Identify binary name in checksum file
        # We check common release names
        system = platform.system().lower()
        target_name = "ldm-linux"
        if system == "darwin":
            target_name = "ldm-macos"
        elif system == "windows":
            target_name = "ldm-windows.exe"

        for line in official_data.splitlines():
            if target_name in line:
                expected_hash = line.split()[0]
                if local_hash == expected_hash:
                    return f"Verified ({local_hash[:12]})", True
                else:
                    return f"TAMPERED / MISMATCH ({local_hash[:12]})", False

        return f"Unknown Build ({local_hash[:12]})", "warn"
    except Exception:
        return None, "warn"


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

        req = Request(url, headers={"User-Agent": "ldm-cli"})
        with urlopen(req, timeout=3) as response:  # nosec B310
            res_data = json.loads(response.read().decode())
            latest_version = res_data.get("tag_name", "").lstrip("v")

            # Architecture-aware asset resolution
            release_url = res_data.get("html_url")  # Fallback to release page
            assets = res_data.get("assets", [])

            system = sys.platform
            target_asset = "ldm-linux"
            if system == "darwin":
                target_asset = "ldm-macos"
            elif system in ["win32", "windows"]:
                target_asset = "ldm-windows.exe"

            for asset in assets:
                if asset.get("name") == target_asset:
                    release_url = asset.get("browser_download_url")
                    break

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
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        # Fail gracefully for background checks
        return None, None
    except Exception:
        return None, None
