import os
import sys
import platform
import json
import time
import subprocess
import re
import shutil
from pathlib import Path
from urllib.request import urlopen, Request
from ldm_core.ui import UI
from ldm_core.constants import TAG_PATTERN


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
    except subprocess.CalledProcessError as e:
        if e.returncode == 130:
            raise KeyboardInterrupt()
        if check:
            raise e
        return None
    except KeyboardInterrupt:
        raise


def get_json(url):
    try:
        # Bandit: B310 (urllib-urlopen) is safe as we are fetching from trusted Liferay APIs.
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req) as response:  # nosec B310
            return json.loads(response.read().decode())
    except Exception as e:
        UI.error(f"Failed to fetch data: {e}")
        return None


def get_actual_home():
    """Returns the home directory of the real user, even when running with sudo."""
    real_user = os.environ.get("SUDO_USER") or os.environ.get("USER")
    if platform.system() == "darwin" and real_user:
        home = Path(f"/Users/{real_user}")
        if home.exists():
            return home
    return Path.home()


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


def get_docker_socket_path():
    system = platform.system().lower()
    if system == "darwin":
        real_socket = get_actual_home() / ".docker/run/docker.sock"
        if real_socket.exists():
            return str(real_socket)
        return "/var/run/docker.sock"
    if system in ["windows", "win32"]:
        return "//./pipe/docker_engine"
    return "/var/run/docker.sock"
