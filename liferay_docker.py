import os
import re
import sys
import platform
import json
import time
import shutil
import argparse
import subprocess
import tarfile
import gzip
import lzma
import socket
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

# --- Constants & Configuration ---
IMAGE_NAME_DXP = "liferay/dxp"
IMAGE_NAME_PORTAL = "liferay/portal"
API_BASE_DXP = "https://hub.docker.com/v2/repositories/liferay/dxp/tags?page_size=200&ordering=name"
API_BASE_PORTAL = "https://hub.docker.com/v2/repositories/liferay/portal/tags?page_size=200&ordering=name"
META_VERSION = "2"
MIN_META_VERSION = 2
PROJECT_META_FILE = ".liferay-docker.meta"
TAG_PATTERN = r'^\d{4}\.q[1-4]\.\d+(-u\d+|-lts)?$'
SCRIPT_DIR = Path(__file__).parent.resolve()

# --- UI Helpers ---
class UI:
    COLOR_OFF = '\033[0m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[0;33m'
    WHITE = '\033[0;37m'
    BYELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    BRED = '\033[1;31m'
    CYAN = '\033[0;36m'

    @staticmethod
    def info(msg):
        print(f"{UI.YELLOW}ℹ {msg}{UI.COLOR_OFF}")

    @staticmethod
    def success(msg):
        print(f"{UI.GREEN}✅ {msg}{UI.COLOR_OFF}")

    @staticmethod
    def error(msg):
        print(f"{UI.BRED}❌ Error:{UI.COLOR_OFF} {msg}", file=sys.stderr)

    @staticmethod
    def die(msg):
        UI.error(msg)
        sys.exit(1)

    @staticmethod
    def heading(msg):
        print(f"\n{UI.BYELLOW}=== {msg} ==={UI.COLOR_OFF}")

    @staticmethod
    def ask(prompt, default=None):
        try:
            if default:
                res = input(f"{UI.WHITE}❓ {prompt} [{UI.GREEN}{default}{UI.WHITE}]: {UI.COLOR_OFF}")
                return res if res else default
            return input(f"{UI.WHITE}❓ {prompt}: {UI.COLOR_OFF}")
        except KeyboardInterrupt:
            print("\n")
            UI.info("Interrupted by user. Exiting...")
            sys.exit(130)

    @staticmethod
    def format_size(size):
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024

# --- Utilities ---
def run_command(cmd, shell=False, capture_output=True, check=True, env=None):
    if env is None:
        env = os.environ.copy()
    else:
        env = env.copy()
    
    # Suppress Docker CLI "What's next" hints and non-essential messages
    env["DOCKER_CLI_HINTS"] = "false"
    
    try:
        result = subprocess.run(
            cmd, shell=shell, capture_output=capture_output, text=True, check=check, env=env
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
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        UI.error(f"Failed to fetch data: {e}")
        return None

def discover_latest_tag(api_url, release_type="any", year_filter=None, verbose=False, refresh=False):
    cache_path = Path.home() / ".liferay_docker_cache.json"
    cache_key = f"{api_url}_{release_type}_{year_filter}"
    
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

    print("Initial tag discovery (this may take a few seconds)...")
    start_time = time.time()
    
    url = api_url
    if release_type == "lts": url += "&name=-lts"
    elif release_type == "u": url += "&name=-u"

    tags = []
    page = 0
    while url:
        page += 1
        sys.stdout.write(f"\rFetching page {page}...")
        sys.stdout.flush()
        
        data = get_json(url)
        if not data: break
        
        for result in data.get('results', []):
            name = result['name']
            if year_filter and not name.startswith(year_filter):
                continue
            
            is_valid = bool(re.match(TAG_PATTERN, name))
            if is_valid:
                tags.append(name)
        
        url = data.get('next')

    duration = time.time() - start_time
    print(f"\nFetched {page} pages in {duration:.1f}s")

    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

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

# --- Core Functionality ---

class LiferayManager:
    def __init__(self, args):
        self.args = args
        self.verbose = getattr(args, 'verbose', False)
        self.non_interactive = getattr(args, 'non_interactive', False)
        
        run_attrs = [
            'tag', 'root', 'container', 'follow', 'release_type', 'db', 
            'jdbc_username', 'jdbc_password', 'recreate_db', 'port', 
            'host_network', 'host_name', 'es_port', 'disable_zip64', 'delete_state', 'remove_after', 'portal', 'refresh', 'ssl'
        ]
        for attr in run_attrs:
            if not hasattr(self.args, attr):
                setattr(self.args, attr, None)

    def detect_root(self):
        if getattr(self.args, 'root', None):
            return Path(self.args.root).resolve()
        cwd = Path.cwd()
        if (cwd / "files" / "portal-ext.properties").exists() or (cwd / "deploy").exists():
            return cwd
        return None

    def find_dxp_roots(self, search_dir=None):
        search_dir = Path(search_dir or Path.cwd())
        roots = []
        if not search_dir.exists(): return roots
        for item in search_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                if (item / "files").exists() or (item / "deploy").exists() or (item / PROJECT_META_FILE).exists():
                    meta = self.read_meta(item / PROJECT_META_FILE)
                    version = meta.get("tag")
                    if not version:
                        if re.match(TAG_PATTERN, item.name):
                            version = item.name
                        else:
                            version = "unknown"
                    roots.append({"path": item, "version": version})
        return sorted(roots, key=lambda x: x["path"].name)

    def setup_paths(self, root_path):
        root = Path(root_path).resolve()
        paths = {
            "root": root,
            "deploy": root / "deploy",
            "data": root / "data",
            "scripts": root / "scripts",
            "files": root / "files",
            "cx": root / "osgi" / "client-extensions",
            "state": root / "osgi" / "state",
            "modules": root / "modules",
            "backups": root / "backups",
            "certs": root / ".certs"
        }
        
        legacy_modules = root / "osgi" / "modules"
        if legacy_modules.exists() and legacy_modules.is_dir():
            UI.info(f"Detected legacy 'osgi/modules' folder. Moving contents to root 'modules'...")
            paths["modules"].mkdir(parents=True, exist_ok=True)
            for item in legacy_modules.iterdir():
                dest = paths["modules"] / item.name
                if not dest.exists():
                    shutil.move(str(item), str(dest))
            self.safe_rmtree(legacy_modules, root=root)
            
        return paths

    def get_jdbc_params(self, files_dir):
        portal_ext = Path(files_dir) / "portal-ext.properties"
        params = {}
        if portal_ext.exists():
            with open(portal_ext, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, val = line.split('=', 1)
                        params[key.strip()] = val.strip()
        return params

    def read_meta(self, path):
        meta = {}
        if not path.exists(): return meta
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    meta[k.strip()] = v.strip()
        return meta

    def write_meta(self, path, meta):
        path = Path(path)
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, 'w') as f:
            f.write(f"# Generated by Liferay Docker Manager ({datetime.now().isoformat()})\n")
            for k, v in sorted(meta.items()):
                if v is not None:
                    f.write(f"{k}={v}\n")
        os.replace(tmp_path, path)

    def verify_archive(self, file_path):
        UI.info(f"Verifying {file_path.name} integrity...")
        try:
            if file_path.suffix == ".gz":
                with gzip.open(file_path, 'rb') as f:
                    while f.read(1024*1024): pass
            elif file_path.suffix == ".xz":
                with lzma.open(file_path, 'rb') as f:
                    while f.read(1024*1024): pass
            
            if ".tar" in file_path.name or file_path.suffix in [".tgz", ".tar"]:
                with tarfile.open(file_path, 'r:*') as tar:
                    tar.getmembers()
            return True
        except Exception as e:
            UI.error(f"Integrity check failed: {e}")
            return False

    def check_docker(self):
        try:
            run_command(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True)
            return True
        except Exception:
            return False

    def get_resolved_ip(self, host_name):
        if not host_name or host_name == "localhost":
            return "127.0.0.1"
        try:
            return socket.gethostbyname(host_name)
        except socket.gaierror:
            return None

    def is_bindable(self, ip):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((ip, 0))
            return True
        except Exception:
            return False

    def is_port_available(self, port, ip="127.0.0.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.bind((ip, int(port)))
            return True
        except Exception:
            return False

    def check_mkcert(self):
        try:
            subprocess.run(["mkcert", "-version"], capture_output=True, check=True)
        except Exception:
            UI.error("mkcert is not installed. Fast-failing SSL setup.")
            UI.info("Installation Guide: https://github.com/FiloSottile/mkcert#installation")
            UI.die("Please install mkcert and try again.")

        try:
            ca_root = subprocess.run(["mkcert", "-CAROOT"], capture_output=True, text=True, check=True).stdout.strip()
            if not ca_root or not os.path.exists(ca_root) or not os.listdir(ca_root):
                raise ValueError("Root CA not found")
        except Exception:
            UI.error("mkcert Root CA is not installed on this host.")
            print(f"\n{UI.BYELLOW}ACTION REQUIRED:{UI.COLOR_OFF} Please run the following command to trust mkcert:")
            print(f"{UI.CYAN}mkcert -install{UI.COLOR_OFF}\n")
            UI.die("Root CA trust is required for automated SSL.")
    def setup_ssl(self, paths, host_name):
        if not host_name or host_name == "localhost":
            return False
        
        real_user = os.environ.get("SUDO_USER") or os.environ.get("USER")
        actual_home = Path(f"/Users/{real_user}") if platform.system() == "darwin" else Path.home()
        cert_dir = actual_home / ".liferay_docker_certs"
        
        # Ensure directory exists and is owned by the real user
        cert_dir.mkdir(parents=True, exist_ok=True)
        
        cert_file = cert_dir / f"{host_name}.pem"
        key_file = cert_dir / f"{host_name}-key.pem"
        
        if not cert_file.exists():
            UI.info(f"Generating SSL certificate for {host_name}...")
            # Use 'sudo -u' to drop privileges back to the real user for mkcert
            cmd = ["sudo", "-u", real_user, "mkcert", "-cert-file", str(cert_file), "-key-file", str(key_file), host_name]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                UI.error(f"mkcert failed: {e.stderr.decode().strip()}")
                return False

        # Set permissions so the global Traefik container can read them
        os.chmod(cert_file, 0o644)
        os.chmod(key_file, 0o644)

        config_path = cert_dir / f"traefik-{host_name}.yml"
        config_content = f"""
tls:
  certificates:
    - certFile: /etc/traefik/certs/{host_name}.pem
      keyFile: /etc/traefik/certs/{host_name}-key.pem
"""
        with open(config_path, "w") as f:
            f.write(config_content)
        os.chmod(config_path, 0o644)
            
        return True

    def get_docker_socket_params(self):
        """Returns (mount_args_list, traefik_endpoint_flag) based on OS."""
        system = sys.platform
        
        if system == "darwin":  # macOS
            real_socket = Path.home() / ".docker/run/docker.sock"
            path = str(real_socket) if real_socket.exists() else "/var/run/docker.sock"
            return ["-v", f"{path}:/var/run/docker.sock:ro"], "--providers.docker.endpoint=unix:///var/run/docker.sock"
        
        elif system == "win32":  # Windows
            return ["-v", "//./pipe/docker_engine://./pipe/docker_engine"], "--providers.docker.endpoint=npipe:////./pipe/docker_engine"
        
        else:  # Linux
            return ["-v", "/var/run/docker.sock:/var/run/docker.sock:ro"], "--providers.docker.endpoint=unix:///var/run/docker.sock"

    def setup_infrastructure(self, resolved_ip, ssl_port, paths):
        # 1. Create the shared network
        run_command(["docker", "network", "create", "liferay-net"], check=False)

        real_user = os.environ.get("SUDO_USER") or os.environ.get("USER")
        if platform.system() == "darwin":
            actual_home = Path(f"/Users/{real_user}")
        else:
            actual_home = Path.home()

        global_cert_dir = actual_home / ".liferay_docker_certs"
        global_cert_dir.mkdir(parents=True, exist_ok=True)

        # 3. Start the Socket Proxy
        api_proxy = "docker-socket-proxy"
        run_command(["docker", "rm", "-f", api_proxy], check=False)
        
        real_socket_path = f"{Path.home()}/.docker/run/docker.sock"
        
        UI.info("Starting Docker Socket Proxy bridge...")
        run_command([
            "docker", "run", "-d", "--name", api_proxy,
            "--network", "liferay-net",
            "-v", f"{real_socket_path}:/var/run/docker.sock:ro",
            "alpine/socat", 
            "TCP-LISTEN:2375,fork,reuseaddr", "UNIX-CONNECT:/var/run/docker.sock"
        ])

        # 4. Start Traefik
        proxy_name = "liferay-proxy-global"
        run_command(["docker", "rm", "-f", proxy_name], check=False)

        traefik_cmd = [
            "docker", "run", "-d", "--rm",
            "--name", proxy_name,
            "--network", "liferay-net",
            "-p", f"{resolved_ip}:{ssl_port}:443",
            "-e", "DOCKER_API_VERSION=1.44", 
            "-v", f"{global_cert_dir.as_posix()}:/etc/traefik/certs:ro", # Now defined!
            "traefik:v3.6.1", 
            "--providers.docker=true",
            f"--providers.docker.endpoint=tcp://{api_proxy}:2375",
            "--providers.docker.exposedbydefault=false",
            "--entrypoints.websecure.address=:443",
            "--providers.file.directory=/etc/traefik/certs",
            "--providers.file.watch=true"
        ]
        run_command(traefik_cmd)
        return True

    def check_hostname(self, host_name):
        if not host_name or host_name == "localhost":
            return True
        
        UI.info(f"Verifying resolution for '{host_name}'...")
        ip = self.get_resolved_ip(host_name)
        
        if not ip:
            UI.error(f"Hostname '{host_name}' could not be resolved.")
            print(f"\n{UI.BRED}IMPORTANT:{UI.COLOR_OFF} You must map '{host_name}' to a loopback address (e.g. 127.0.0.1) in your OS hosts file.")
            print(f"Edit {UI.CYAN}/etc/hosts{UI.COLOR_OFF} (macOS/Linux) or {UI.CYAN}C:\\Windows\\System32\\drivers\\etc\\hosts{UI.COLOR_OFF} (Windows).")
            print(f"Add the following line:\n{UI.GREEN}127.0.0.1  {host_name}{UI.COLOR_OFF}")
            if self.non_interactive: sys.exit(1)
            return UI.ask("Continue anyway?", "N").upper() == "Y"

        if not (ip.startswith("127.") or ip in ["::1", "0:0:0:0:0:0:0:1"]):
            UI.error(f"Hostname '{host_name}' resolves to {ip}, which is not a local loopback address.")
            if self.non_interactive: sys.exit(1)
            return UI.ask("Continue anyway?", "N").upper() == "Y"

        UI.success(f"Resolved '{host_name}' to loopback address {ip}")
        
        if not self.is_bindable(ip):
            UI.error(f"IP address {ip} is not available for binding on this host.")
            self.print_macos_alias_advice(ip)
            if self.non_interactive:
                sys.exit(1)
            if not UI.ask("Continue anyway?", "N").upper() == "Y":
                sys.exit(1)
        return True

    def print_macos_alias_advice(self, ip):
        if sys.platform == "darwin":
            print(f"\n{UI.BRED}OSX DETECTED:{UI.COLOR_OFF} You must alias this IP to your loopback interface.")
            print(f"Run the following command in your terminal:")
            print(f"{UI.CYAN}sudo ifconfig lo0 alias {ip} up{UI.COLOR_OFF}\n")
        else:
            print(f"\nEnsure your network interface is configured to handle {ip}.")

    def normalize_jdbc_url(self, url):
        if not url: return None
        pattern = r'^jdbc:(postgresql|mysql|mariadb)://([^:/]+)(?::(\d+))?/([^/?]+)'
        match = re.match(pattern, url)
        if match:
            driver, host, port, db_name = match.groups()
            if host in ['localhost', 'host.docker.internal']: host = '127.0.0.1'
            if not port:
                port = '5432' if driver == 'postgresql' else '3306'
            return (driver, host, port, db_name)
        return url

    def update_portal_ext(self, path, updates):
        content = ""
        if path.exists():
            with open(path, 'r') as f:
                content = f.read()
        
        for key, value in updates.items():
            pattern = re.compile(rf"^\s*{re.escape(key)}\s*=.*$", re.MULTILINE)
            if pattern.search(content):
                content = pattern.sub(f"{key}={value}", content)
            else:
                if content and not content.endswith('\n'):
                    content += '\n'
                content += f"{key}={value}\n"
                
        with open(path, 'w') as f:
            f.write(content)

    def is_within_root(self, path, root):
        try:
            path = Path(path).resolve()
            root = Path(root).resolve()
            return root in path.parents or path == root
        except Exception:
            return False

    def wait_for_container_stop(self, container_name, timeout=30):
        start_time = time.time()
        while time.time() - start_time < timeout:
            inspect_raw = run_command(["docker", "inspect", "-f", "{{.State.Status}} {{.State.Running}}", container_name], check=False)
            if not inspect_raw:
                return True 
            
            parts = inspect_raw.split()
            status = parts[0]
            running = parts[1].lower() == "true"
            
            if status == "exited" and not running:
                return True
            time.sleep(1)
        return False

    def safe_rmtree(self, path, root=None):
        path = Path(path).resolve()
        if not path.exists():
            return True
            
        if root:
            root = Path(root).resolve()
            if not self.is_within_root(path, root):
                UI.error(f"Safety violation: Attempted to delete {path} which is outside root {root}")
                return False
        
        for i in range(5):
            try:
                shutil.rmtree(path)
                return True
            except Exception as e:
                if i == 4:
                    UI.error(f"Failed to delete {path}: {e}")
                    return False
                time.sleep(2)
        return False

    def safe_extract(self, tar, path):
        target_path = Path(path).resolve()
        for member in tar.getmembers():
            member_path = (target_path / member.name).resolve()
            try:
                member_path.relative_to(target_path)
            except ValueError:
                UI.die(f"Security Alert: Attempted path traversal in archive: {member.name}")
        
        if sys.version_info >= (3, 12):
            tar.extractall(path=target_path, filter='data')
        else:
            tar.extractall(path=target_path)

    def cmd_run(self):
        if not self.check_docker():
            UI.die("Docker is not running or not accessible. Please start Docker and try again.")

        # Ensure global proxy network exists
        run_command(["docker", "network", "create", "liferay-net"], check=False)

        if getattr(self.args, 'select', False):
            roots = self.find_dxp_roots()
            UI.heading("Available DXP Folders")
            for i, root in enumerate(roots):
                print(f"[{i+1}] {root['path'].name} [{UI.CYAN}{root['version']}{UI.COLOR_OFF}]")
            print(f"[{len(roots)+1}] Create New...")
            
            choice = UI.ask("Select folder index", "1")
            try:
                idx = int(choice) - 1
                if idx == len(roots):
                    self.args.root = None
                    self.args.tag = None
                elif 0 <= idx < len(roots):
                    self.args.root = str(roots[idx]['path'])
                    if not self.args.tag and roots[idx]['version'] != "unknown":
                        self.args.tag = roots[idx]['version']
                else:
                    UI.die("Invalid selection.")
            except ValueError:
                UI.die("Invalid input. Please enter a number.")

        root_path = self.args.root or self.detect_root()
        project_meta = {}
        if root_path and Path(root_path).exists():
            project_meta = self.read_meta(Path(root_path) / PROJECT_META_FILE)

        # --- PRE-FLIGHT / FAST-FAIL CHECKS ---
        port = self.args.port or project_meta.get("port")
        host_name = self.args.host_name or project_meta.get("host_name")
        es_port = self.args.es_port or project_meta.get("es_port")
        container_name = self.args.container or project_meta.get("container_name") or (Path(root_path).name.replace(".", "-") if root_path else None)
        db_kind = getattr(self.args, 'db', None) or project_meta.get("db_type")
        disable_zip64 = getattr(self.args, 'disable_zip64', False)

        # Assigned early to fix UnboundLocalError
        use_host_net = getattr(self.args, 'host_network', None)
        if use_host_net is None:
            host_net_meta = project_meta.get("host_network")
            if host_net_meta is not None: 
                use_host_net = (host_net_meta == "True")
            else:
                use_host_net = False

        if host_name and not self.check_hostname(host_name):
            sys.exit(1)

        resolved_ip = self.get_resolved_ip(host_name) or "127.0.0.1"

        use_ssl = getattr(self.args, 'ssl', None)
        if use_ssl is None:
            use_ssl = bool(host_name and host_name != "localhost")
        
        ssl_port = 443
        if use_ssl:
            if use_host_net:
                UI.die("SSL with Traefik is not supported in --host-network mode.")
            
            # Silent network management
            run_command(["docker", "network", "inspect", "liferay-net"], check=False) or \
            run_command(["docker", "network", "create", "liferay-net"], check=False)

            if not self.is_port_available(443, resolved_ip):
                # Singleton Proxy Detection
                proxy_running = run_command(["docker", "ps", "-q", "-f", "name=liferay-proxy-global"])
                if not proxy_running:
                    if platform.system() == "Darwin":
                        UI.info("Port 443 check failed, but Docker Desktop for Mac may still allow it.")
                        # On macOS, we proceed with 443 as the default despite the socket check
                        ssl_port = 443 
                    else:
                        UI.error(f"Host port 443 is blocked on {resolved_ip}.")
                        UI.info("HINT: To use the standard HTTPS port, run this script with sudo.")
                        
                        if not self.non_interactive:
                            if UI.ask("Would you like to continue using port 8443 instead?", "Y").upper() == "Y":
                                if not self.is_port_available(8443, resolved_ip):
                                    UI.die(f"Host port 8443 is also in use on {resolved_ip}. Aborting SSL setup.")
                                ssl_port = 8443
                            else:
                                sys.exit(0)
                        else:
                            UI.die("Port 443 unavailable in non-interactive mode. Aborting.")

            self.check_mkcert()

        # 3. Collision Scan & Port Auto-Increment
        port_assigned = False
        if not port: port = 8080
        while not port_assigned:
            collision_found = False
            search_dir = Path(root_path).parent if root_path else Path.cwd()
            other_projects = self.find_dxp_roots(search_dir)
            
            for proj in other_projects:
                if root_path and proj["path"].resolve() == Path(root_path).resolve():
                    continue
                meta = self.read_meta(proj["path"] / PROJECT_META_FILE)
                
                # DB Collision Check (only once)
                if not collision_found:
                    curr_jdbc = self.get_jdbc_params(Path(root_path) / "files") if root_path else {}
                    curr_norm = self.normalize_jdbc_url(curr_jdbc.get("jdbc.default.url"))
                    proj_jdbc = meta.get("jdbc_url") or self.read_meta(proj["path"] / "files/portal-ext.properties").get("jdbc.default.url")
                    proj_norm = self.normalize_jdbc_url(proj_jdbc)
                    
                    if curr_norm and proj_norm and curr_norm == proj_norm:
                        UI.error(f"Database collision with project '{proj['path'].name}'")
                        if self.non_interactive: sys.exit(1)
                        if UI.ask("Continue anyway?", "N").upper() != "Y": sys.exit(1)

                # Port Conflict Check
                check_cmd = ["docker", "ps", "--filter", f"name=^{meta.get('container_name')}$", "--format", "{{.Names}}"]
                if run_command(check_cmd, check=False):
                    m_ip = self.get_resolved_ip(meta.get("host_name")) or "127.0.0.1"
                    r_ip = resolved_ip
                    if str(port) == meta.get("port") and m_ip == r_ip:
                        port = int(port) + 1
                        collision_found = True
                        break
            
            if not collision_found:
                if not self.is_port_available(port, resolved_ip):
                    port = int(port) + 1
                    continue
                port_assigned = True

        # --- END PRE-FLIGHT CHECKS ---

        tag = self.args.tag or project_meta.get("tag")
        use_portal = self.args.portal or (project_meta.get("image_type") == "portal")
        image_name = IMAGE_NAME_PORTAL if use_portal else IMAGE_NAME_DXP
        api_base = API_BASE_PORTAL if use_portal else API_BASE_DXP

        if not tag:
            if root_path and re.match(TAG_PATTERN, Path(root_path).name):
                tag = Path(root_path).name
            else:
                ans = self.args.release_type or (UI.ask("Release type (any|u|lts|qr) or Enter Tag", "any") if not self.non_interactive else "any")
                if re.match(TAG_PATTERN, ans):
                    tag = ans
                else:
                    release_type = ans
                    year = datetime.now().strftime("%Y")
                    tag = discover_latest_tag(api_base, release_type, year, self.verbose, getattr(self.args, 'refresh', False))
                    if not tag:
                        tag = discover_latest_tag(api_base, release_type, None, self.verbose, getattr(self.args, 'refresh', False))
                    if not self.non_interactive:
                        tag = UI.ask("Enter Liferay Docker Tag", tag)
                    elif not tag:
                        UI.die("Could not auto-detect tag. Please provide --tag.")

        root_default = root_path or f"./{tag}"
        root_path = self.args.root or (UI.ask("Liferay Root", root_default) if not self.non_interactive else root_default)
        paths = self.setup_paths(root_path)

        if not container_name:
            container_name = self.args.container or project_meta.get("container_name") or Path(root_path).name.replace(".", "-")
        
        inspect = run_command(["docker", "ps", "-a", "--filter", f"name=^{container_name}$", "--format", "{{.Names}}"], check=False)
        
        force_init = False
        if inspect and container_name in inspect.split("\n"):
            if not self.non_interactive:
                if UI.ask(f"Container '{container_name}' already exists. Would you like to remove it and re-initialize?", "N").upper() == "Y":
                    UI.info(f"Removing existing container {container_name}...")
                    run_command(["docker", "rm", "-f", container_name])
                    if paths["certs"].exists():
                        self.safe_rmtree(paths["certs"], root=paths["root"])
                    force_init = True

        if use_ssl:
            if not self.setup_ssl(paths, host_name):
                use_ssl = False
            else:
                self.setup_infrastructure(resolved_ip, ssl_port, paths)

        if force_init or not inspect or container_name not in inspect.split("\n"):
            UI.heading(f"Initializing {container_name}")
            for p in paths.values():
                p.mkdir(parents=True, exist_ok=True)
            
            common_dir = SCRIPT_DIR / "common"
            if common_dir.exists():
                for f in common_dir.glob("*activationkeys.xml"):
                    if not use_portal: shutil.copy(f, paths["deploy"])
                for f in common_dir.glob("*.properties"):
                    shutil.copy(f, paths["files"])

            # Smart 'Remove' Logic
            rm_container = getattr(self.args, 'remove_after', False)
            if not rm_container and getattr(self.args, 'follow', False) and not self.non_interactive:
                rm_container = UI.ask("Remove container afterwards?", "N") == "Y"
            rm_arg = ["--rm"] if rm_container else []

            if not db_kind and not self.non_interactive:
                db_kind = UI.ask("Use Hypersonic database?", "Y")
                db_kind = "hypersonic" if db_kind.upper() == "Y" else None
                if not db_kind:
                    db_kind = UI.ask("Liferay Root - postgresql or mysql", "postgresql")
            elif not db_kind:
                db_kind = "hypersonic"

            if db_kind in ["postgresql", "mysql"]:
                db_name = container_name.replace("-", "")
                user = self.args.jdbc_username or UI.ask("Username", "liferay")
                pw = self.args.jdbc_password or (UI.ask("Password") if db_kind == "mysql" else None)
                recreate = getattr(self.args, 'recreate_db', False)
                if db_kind == "postgresql":
                    exists = run_command(["psql", "-lqt", "-c", f"SELECT 1 FROM pg_database WHERE datname='{db_name}'"], check=False)
                    if exists and not recreate and not self.non_interactive:
                        recreate = UI.ask("Recreate database?", "N") == "Y"
                    if recreate or not exists:
                        if recreate: run_command(["dropdb", "-f", "-h", "localhost", "-U", user, db_name], check=False)
                        run_command(["createdb", "-h", "localhost", "-p", "5432", "-U", user, "-O", user, db_name])
                    jdbc_updates = {
                        "jdbc.default.driverClassName": "org.postgresql.Driver",
                        "jdbc.default.url": f"jdbc:postgresql://host.docker.internal:5432/{db_name}",
                        "jdbc.default.username": user
                    }
                else:
                    pw_arg = f"-p{pw}" if pw else ""
                    exists = run_command(["mysql", "-u", user, pw_arg, "-e", f"use {db_name}"], check=False)
                    if exists is not None and not recreate and not self.non_interactive:
                        recreate = UI.ask("Recreate database?", "N") == "Y"
                    if recreate or exists is None:
                        if recreate: run_command(["mysql", "-u", user, pw_arg, "-e", f"DROP DATABASE {db_name};"], check=False)
                        run_command(["mysql", "-u", user, pw_arg, "-e", f"CREATE DATABASE {db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"])
                    jdbc_updates = {
                        "jdbc.default.driverClassName": "com.mysql.cj.jdbc.Driver",
                        "jdbc.default.url": f"jdbc:mysql://host.docker.internal:3306/{db_name}",
                        "jdbc.default.username": user
                    }
                    if pw: jdbc_updates["jdbc.default.password"] = pw
                self.update_portal_ext(paths["files"] / "portal-ext.properties", jdbc_updates)

            # Network Args Construction
            net_args = ["-p", f"{resolved_ip}:{port}:8080"]
            if use_ssl:
                net_args = ["--network", "liferay-net"] + net_args

            # ES Port: Only expose if explicitly requested via CLI
            if not use_host_net and getattr(self.args, 'es_port', None):
                es_port = self.args.es_port
                if self.is_port_available(es_port, resolved_ip):
                    net_args += ["-p", f"{resolved_ip}:{es_port}:9200"]
                else:
                    UI.info(f"Note: Requested ES port {es_port} is busy. ES sidecar not exposed to host.")

            env_args = []
            if disable_zip64:
                env_args += ["-e", "LIFERAY_JVM_OPTS=-Djdk.util.zip.disableZip64ExtraFieldValidation=true"]
            
            host_updates = {}
            if host_name and host_name != "localhost":
                safe_host = host_name.replace(".", "_").replace("-", "_")
                cookie_name = f"LFR_SESSION_ID_{safe_host}"
                jvm_opts = f"-Dorg.apache.catalina.SESSION_COOKIE_NAME={cookie_name}"
                found = False
                for i, arg in enumerate(env_args):
                    if "LIFERAY_JVM_OPTS=" in arg:
                        env_args[i] = arg.replace("LIFERAY_JVM_OPTS=", f"LIFERAY_JVM_OPTS={jvm_opts} ")
                        found = True
                        break
                if not found:
                    env_args += ["-e", f"LIFERAY_JVM_OPTS={jvm_opts}"]
                host_updates["session.cookie.domain"] = host_name
                host_updates["session.cookie.use.full.hostname"] = "true"
                host_updates["virtual.hosts.valid.hosts"] = f"localhost,127.0.0.1,[::1],{host_name},{resolved_ip}"

            if es_port and str(es_port).isdigit() and str(es_port) != "9200":
                es_transport = str(int(es_port) + 100)
                host_updates["module.framework.properties.com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConfiguration.sidecarHttpPort"] = str(es_port)
                host_updates["module.framework.properties.com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConfiguration.transportTcpPort"] = es_transport
            if host_updates:
                self.update_portal_ext(paths["files"] / "portal-ext.properties", host_updates)

            image_tag = f"{image_name}:{tag}"
            UI.info(f"Pulling {image_tag}...")
            run_command(["docker", "pull", image_tag], capture_output=False)
            
            labels = []
            if use_ssl:
                labels = [
                    "--label", "traefik.enable=true",
                    # Changed single quotes to backticks for Traefik v3 compatibility
                    "--label", f"traefik.http.routers.{container_name}.rule=Host(`{host_name}`)",
                    "--label", f"traefik.http.routers.{container_name}.entrypoints=websecure",
                    "--label", f"traefik.http.routers.{container_name}.tls=true",
                    "--label", f"traefik.http.services.{container_name}.loadbalancer.server.port=8080",
                    "--label", "traefik.docker.network=liferay-net"
                ]

            docker_cmd = [
                "docker", "create", "-it", 
                "--name", container_name] + rm_arg + net_args + env_args + labels + [
                "-v", f"{paths['files'].as_posix()}:/mnt/liferay/files",
                "-v", f"{paths['scripts'].as_posix()}:/mnt/liferay/scripts",
                "-v", f"{paths['state'].as_posix()}:/opt/liferay/osgi/state",
                "-v", f"{paths['modules'].as_posix()}:/opt/liferay/modules",
                "-v", f"{paths['data'].as_posix()}:/opt/liferay/data",
                "-v", f"{paths['deploy'].as_posix()}:/mnt/liferay/deploy",
                "-v", f"{paths['cx'].as_posix()}:/opt/liferay/osgi/client-extensions",
                image_tag
            ]
            run_command(docker_cmd)
            UI.success(f"Container created: {container_name}")
        else:
            UI.heading(f"Resuming {container_name}")
            delete_state = getattr(self.args, 'delete_state', False)
            if not delete_state and not self.non_interactive:
                delete_state = UI.ask("Delete OSGi state folder?", "Y") == "Y"
            if delete_state:
                is_running = run_command(["docker", "ps", "-q", "-f", f"name={container_name}"])
                if is_running:
                    try:
                        run_command(["docker", "stop", container_name], check=True)
                    except subprocess.CalledProcessError as e:
                        UI.error(f"Failed to stop container: {e.stderr.decode().strip()}")
                        UI.error("Aborting OSGi state deletion to prevent volume corruption.")
                        delete_state = False
                    
                    if delete_state:
                        if not self.wait_for_container_stop(container_name):
                            UI.error(f"Container {container_name} failed to stop within timeout.")
                            UI.error("Aborting OSGi state deletion to prevent volume corruption.")
                            delete_state = False
                        else:
                            time.sleep(2)
                if delete_state:
                    if self.safe_rmtree(paths["state"], root=paths["root"]):
                        paths["state"].mkdir(parents=True)

        jdbc_params = self.get_jdbc_params(paths["files"])
        project_meta.update({
            "tag": tag,
            "image_type": "portal" if use_portal else "dxp",
            "container_name": container_name,
            "db_type": db_kind or "hypersonic",
            "jdbc_url": jdbc_params.get("jdbc.default.url"),
            "port": str(port),
            "host_network": str(use_host_net),
            "host_name": host_name or "localhost",
            "es_port": str(es_port),
            "ssl": str(use_ssl),
            "last_run": datetime.now().isoformat()
        })
        self.write_meta(paths["root"] / PROJECT_META_FILE, project_meta)

        try:
            run_command(["docker", "start", container_name], capture_output=False)
            if getattr(self.args, 'follow', False):
                run_command(["docker", "logs", "-f", container_name], capture_output=False)
            else:
                proto = "https" if use_ssl else "http"
                access_port = ""
                if use_ssl:
                    if ssl_port != 443: access_port = f":{ssl_port}"
                else:
                    access_port = f":{port}"
                
                access_url = f"{proto}://{host_name or 'localhost'}{access_port}"
                
                # --- Wait for Health Check ---
                UI.info("Waiting for Liferay to start (this may take 2-5 minutes)...")
                start_time = time.time()
                timeout = 600  # 10 minute maximum wait
                is_ready = False

                while time.time() - start_time < timeout:
                    try:
                        # -k to ignore self-signed certs during wait
                        # -I to fetch headers only
                        check_cmd = ["curl", "-k", "-I", access_url]
                        result = run_command(check_cmd, check=False)
                        
                        if result and ("200" in result or "302" in result):
                            is_ready = True
                            break
                    except Exception:
                        pass
                    
                    sys.stdout.write(".")
                    sys.stdout.flush()
                    time.sleep(10)
                
                print("\n")

                if is_ready:
                    UI.success(f"Container {container_name} is READY!")
                else:
                    UI.error("Liferay is taking longer than expected to respond. Check logs for details.")

                # Final Success Block
                print(f"  {UI.WHITE}🌐 URL:            {UI.CYAN}{access_url}{UI.COLOR_OFF}")
                print(f"  {UI.WHITE}📄 Logs:           {UI.CYAN}docker logs -f {container_name}{UI.COLOR_OFF}")
                
                cleanup_main = f"docker rm -f {container_name}"
                print(f"  {UI.WHITE}🛑 To stop and delete this demo: {UI.CYAN}{cleanup_main}{UI.COLOR_OFF}")
                if use_ssl:
                    print(f"  {UI.WHITE}🛑 To stop everything (including proxy): {UI.CYAN}{cleanup_main} liferay-proxy-global{UI.COLOR_OFF}")
                
                print(f"\n{UI.WHITE}Notice a bug or have a feature request? Please report it on GitHub at:")
                print(f"{UI.CYAN}https://github.com/peterrichards-lr/liferay-docker-scripts{UI.COLOR_OFF}")
        except KeyboardInterrupt:
            run_command(["docker", "stop", container_name], check=False)

    def cmd_snapshots(self, paths=None):
        if not paths:
            root_path = self.detect_root() or os.getcwd()
            paths = self.setup_paths(root_path)
        if not paths["backups"].exists():
            return []
        backups = sorted([d for d in paths["backups"].iterdir() if d.is_dir()], key=lambda x: x.name, reverse=True)
        if backups:
            UI.heading(f"Snapshots in {paths['backups']}")
            for i, b in enumerate(backups):
                meta = self.read_meta(b / "meta")
                size = UI.format_size(sum(f.stat().st_size for f in b.glob('*') if f.is_file()))
                print(f"[{i+1}] {meta.get('name', '(unnamed)')[:18]} - {size}")
        return backups

    def cmd_snapshot(self):
        root_path = self.detect_root() or UI.die("Liferay Root is required.")
        paths = self.setup_paths(root_path)
        project_meta = self.read_meta(root_path / PROJECT_META_FILE)
        container_name = self.args.container or project_meta.get("container_name")
        if not container_name:
            container_name = Path(root_path).name.replace(".", "-")

        # --- PRE-FLIGHT CONNECTIVITY CHECK ---
        jdbc = self.get_jdbc_params(paths["files"])
        url = jdbc.get("jdbc.default.url")
        
        if url and not getattr(self.args, 'files_only', False):
            user = jdbc.get("jdbc.default.username", "")
            pw = jdbc.get("jdbc.default.password", "")
            
            if "postgresql" in url.lower():
                host = self.args.pg_host or "localhost"
                port = self.args.pg_port or "5432"
                UI.info(f"Verifying PostgreSQL connectivity & auth ({host}:{port})...")
                env = os.environ.copy()
                env["DOCKER_CLI_HINTS"] = "false"
                if pw: env["PGPASSWORD"] = pw
                check_res = run_command(["psql", "-h", host, "-p", port, "-U", user, "-d", "postgres", "-c", "SELECT 1"], check=False, env=env)
                if check_res is None:
                    UI.die(f"PostgreSQL database is not reachable or authentication failed on {host}:{port}. Aborting snapshot.")
            
            elif "mysql" in url.lower():
                host = self.args.my_host or "localhost"
                port = self.args.my_port or "3306"
                UI.info(f"Verifying MySQL connectivity & auth ({host}:{port})...")
                pw_arg = f"-p{pw}" if pw else ""
                check_res = run_command(["mysql", "-h", host, "-P", port, "-u", user, pw_arg, "-e", "SELECT 1"], check=False)
                if check_res is None:
                    UI.die(f"MySQL database is not reachable or authentication failed on {host}:{port}. Aborting snapshot.")

        # --- CONTINUE TO INTERACTIVE FLOW ---
        is_running = run_command(["docker", "ps", "-q", "-f", f"name={container_name}"])
        stop_needed = (not getattr(self.args, 'no_stop', False) and is_running)
        
        if stop_needed and not self.non_interactive:
            stop_needed = UI.ask("Stop container during backup?", "Y") == "Y"

        if stop_needed and is_running:
            UI.info(f"Stopping {container_name}...")
            try:
                run_command(["docker", "stop", container_name], check=True)
            except subprocess.CalledProcessError as e:
                UI.die(f"Failed to stop container: {e.stderr.decode().strip()}")
            
            UI.info("Waiting for container to release volumes...")
            if not self.wait_for_container_stop(container_name):
                UI.die(f"Container {container_name} failed to stop within timeout. Aborting snapshot.")
            time.sleep(2)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        snap_dir = paths["backups"] / timestamp
        snap_dir.mkdir(parents=True)
        
        with tarfile.open(snap_dir / "files.tar.gz", "w:gz") as tar:
            for f in ["files", "scripts", "osgi", "data", "deploy", "modules"]:
                if (paths["root"] / f).exists(): tar.add(paths["root"] / f, arcname=f)
        
        self.write_meta(snap_dir / "meta", {"meta_version": META_VERSION, "name": self.args.name or ""})
        UI.success(f"Snapshot saved: {snap_dir}")
        print(f"\n{UI.WHITE}Notice a bug or have a feature request? Please report it on GitHub at:")
        print(f"{UI.CYAN}https://github.com/peterrichards-lr/liferay-docker-scripts{UI.COLOR_OFF}")

    def cmd_restore(self):
        root_path = self.detect_root()
        if not root_path:
            roots = self.find_dxp_roots()
            if roots and not self.non_interactive:
                UI.heading("Select Managed Folder for Restore")
                for i, root in enumerate(roots):
                    print(f"[{i+1}] {root['path'].name} [{UI.CYAN}{root['version']}{UI.COLOR_OFF}]")
                choice = UI.ask("Select folder index", "1")
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(roots): root_path = roots[idx]['path']
                except ValueError: pass
            if not root_path:
                root_path = UI.ask("Liferay Root path")
                if not root_path: UI.die("Liferay Root is required.")

        paths = self.setup_paths(root_path)
        project_meta = self.read_meta(root_path / PROJECT_META_FILE)
        container_name = self.args.container or project_meta.get("container_name")
        if not container_name:
            container_name = Path(root_path).name.replace(".", "-")
        
        if self.args.index:
            backups = sorted([d for d in paths["backups"].iterdir() if d.is_dir()], key=lambda x: x.name, reverse=True)
            if not backups: UI.die("No snapshots available.")
            choice = backups[self.args.index - 1]
        elif getattr(self.args, 'checkpoint', None):
            choice = paths["backups"] / self.args.checkpoint
            if not choice.exists(): UI.die(f"Snapshot {self.args.checkpoint} not found.")
        else:
            backups = self.cmd_snapshots(paths)
            if not backups: UI.die("No snapshots available.")
            choice = backups[int(UI.ask("Select snapshot index", "1")) - 1]

        is_running = run_command(["docker", "ps", "-q", "-f", f"name={container_name}"])
        stop_needed = True if is_running else False
        if stop_needed and not self.non_interactive:
            stop_needed = UI.ask("Stop container during restore?", "Y") == "Y"
            
        if stop_needed and is_running:
            UI.info(f"Stopping {container_name}...")
            try:
                run_command(["docker", "stop", container_name], check=True)
            except subprocess.CalledProcessError as e:
                UI.die(f"Failed to stop container: {e.stderr.decode().strip()}")
            UI.info("Waiting for container to release volumes...")
            if not self.wait_for_container_stop(container_name):
                UI.die(f"Container {container_name} failed to stop within timeout. Aborting restore.")
            time.sleep(2)

        with tarfile.open(choice / "files.tar.gz", "r:gz") as tar:
            self.safe_extract(tar, paths["root"])
        UI.success("Restore complete.")

def main():
    parser = argparse.ArgumentParser(description="Liferay Docker Manager")
    parser.add_argument("--select", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    
    run = subparsers.add_parser("run")
    run.add_argument("-t", "--tag")
    run.add_argument("-r", "--root")
    run.add_argument("-c", "--container")
    run.add_argument("--host-name")
    run.add_argument("--ssl", action="store_true", default=None)
    run.add_argument("--no-ssl", action="store_false", dest="ssl")
    run.add_argument("--port", type=int)
    run.add_argument("--es-port", type=int)
    run.add_argument("--db", choices=["postgresql", "mysql", "hypersonic"])
    run.add_argument("--jdbc-username")
    run.add_argument("--jdbc-password")
    run.add_argument("--recreate-db", action="store_true")
    run.add_argument("--host-network", action="store_true", default=None)
    run.add_argument("--no-host-network", action="store_false", dest="host_network")
    run.add_argument("--release-type", choices=["any", "u", "lts", "qr"])
    run.add_argument("--portal", action="store_true")
    run.add_argument("--disable-zip64", action="store_true")
    run.add_argument("--delete-state", action="store_true")
    run.add_argument("--remove-after", action="store_true")
    run.add_argument("--refresh", action="store_true")
    run.add_argument("-f", "--follow", action="store_true")

    subparsers.add_parser("snapshots")
    
    snap = subparsers.add_parser("snapshot")
    snap.add_argument("-n", "--name")
    snap.add_argument("--files-only", action="store_true")
    snap.add_argument("--no-stop", action="store_true")
    snap.add_argument("--pg-host")
    snap.add_argument("--pg-port")
    snap.add_argument("--my-host")
    snap.add_argument("--my-port")

    rest = subparsers.add_parser("restore")
    rest.add_argument("-i", "--index", type=int)
    rest.add_argument("--checkpoint")
    
    args = parser.parse_args()
    if not args.command: 
        parser.print_help()
        return
    
    manager = LiferayManager(args)
    if args.command == "run": manager.cmd_run()
    elif args.command == "snapshots": manager.cmd_snapshots()
    elif args.command == "snapshot": manager.cmd_snapshot()
    elif args.command == "restore": manager.cmd_restore()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
