import os
import sys
import platform
import shutil
import json
import hashlib
import subprocess
from pathlib import Path
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE, SCRIPT_DIR, VERSION
from ldm_core.utils import (
    run_command,
    get_actual_home,
    check_for_updates,
    version_to_tuple,
)


class DiagnosticsHandler:
    """Mixin for diagnostic and maintenance commands."""

    def cmd_update_check(self, force=True):
        UI.heading("LDM Update Check")
        latest, url = check_for_updates(VERSION, force=force)
        if not latest:
            UI.error("Could not reach GitHub to check for updates.")
            return

        if version_to_tuple(latest) <= version_to_tuple(VERSION):
            UI.success(f"You are up to date! (v{VERSION})")
        else:
            print(
                f"{UI.BYELLOW}[!] A new version is available: v{latest}{UI.COLOR_OFF}"
            )
            print(f"    Current version: v{VERSION}")
            print(f"    Download: {UI.CYAN}{url}{UI.COLOR_OFF}\n")

    def cmd_clear_cache(self):
        cache_path = get_actual_home() / ".liferay_docker_cache.json"
        if cache_path.exists():
            os.remove(cache_path)
            UI.success("Docker tag cache cleared.")
        else:
            UI.info("Cache is already empty.")

    def cmd_doctor(self, project_id=None):
        UI.heading("LDM Doctor - Environmental Health Check")

        results = []

        # 0. Version Check
        latest, _ = check_for_updates(VERSION, force=True)
        if latest and version_to_tuple(latest) > version_to_tuple(VERSION):
            results.append(("LDM Version", f"v{VERSION} (v{latest} available)", "warn"))
        else:
            results.append(("LDM Version", f"v{VERSION} (Latest)", True))

        # 0.1 Executable Checksum
        try:
            exe_path = Path(sys.argv[0]).resolve()
            if exe_path.exists() and exe_path.is_file():
                # Read the first few bytes to detect file type
                with open(exe_path, "rb") as f:
                    magic = f.read(4)

                # Check for known binary headers:
                # - b"PK\x03\x04" : ZIP / Shiv / Jar
                # - b"\x7fELF"    : Linux ELF Binary
                # - b"MZ"         : Windows Executable
                is_binary = magic.startswith((b"PK\x03\x04", b"\x7fELF", b"MZ"))

                # It's source if it has a .py extension AND isn't one of the binary types
                is_python_source = exe_path.suffix.lower() == ".py" and not is_binary

                if is_python_source:
                    results.append(("Executable Checksum", "Python Source (N/A)", True))
                else:
                    sha = hashlib.sha256()
                    with open(exe_path, "rb") as f:
                        for chunk in iter(lambda: f.read(4096), b""):
                            sha.update(chunk)
                    results.append(("Executable Checksum", sha.hexdigest()[:12], True))
        except Exception:
            pass

        # 1. System Info
        results.append(("Python Version", sys.version.split()[0], True))
        results.append(("Platform", platform.platform(), True))

        # 2. Docker Check
        # Perform a silent check first to avoid double error reporting in the UI
        docker_version = None
        try:
            docker_bin = shutil.which("docker")
            if docker_bin:
                res = subprocess.run(
                    [docker_bin, "version", "--format", "{{.Server.Version}}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode == 0:
                    docker_version = res.stdout.strip()
        except Exception:
            pass

        if docker_version:
            results.append(("Docker Engine", f"Running (v{docker_version})", True))

            # 2.0 Docker Context Check
            try:
                context = run_command(["docker", "context", "show"], check=False)
                if context:
                    results.append(("Docker Context", context.strip(), True))
            except Exception:
                pass

            # 2.1 Docker Credentials Check
            try:
                docker_config_path = get_actual_home() / ".docker" / "config.json"
                if docker_config_path.exists():
                    with open(docker_config_path, "r") as f:
                        config = json.loads(f.read())
                        creds_store = config.get("credsStore")
                        if creds_store:
                            helper_bin = f"docker-credential-{creds_store}"
                            if not shutil.which(helper_bin):
                                results.append(
                                    (
                                        "Docker Creds Store",
                                        f"Broken ({creds_store} helper missing)",
                                        False,
                                    )
                                )
                            else:
                                results.append(
                                    ("Docker Creds Store", f"OK ({creds_store})", True)
                                )
            except Exception:
                pass

            # 2.2 Docker Resources
            docker_info_raw = run_command(
                ["docker", "info", "--format", "{{json .}}"], check=False
            )
            if docker_info_raw:
                try:
                    info = json.loads(docker_info_raw)
                    cpus = info.get("NCPU", 0)
                    mem_bytes = info.get("MemTotal", 0)
                    mem_gb = mem_bytes / (1024**3)

                    results.append(("Docker CPUs", f"{cpus} Cores", cpus >= 4))
                    results.append(("Docker Memory", f"{mem_gb:.1f} GB", mem_gb >= 7.5))
                except Exception:
                    pass
        else:
            # Trigger the detailed error reporting from base.py
            self.check_docker()
            results.append(("Docker Engine", "Not reachable", False))

        # 3. mkcert Check
        try:
            mkcert_version = run_command(["mkcert", "-version"], check=False)
            if mkcert_version:
                ca_root = run_command(["mkcert", "-CAROOT"], check=False)
                if ca_root and os.path.exists(ca_root) and os.listdir(ca_root):
                    # Deep check for Root CA trust on macOS
                    is_trusted = True
                    if platform.system().lower() == "darwin":
                        trust_check = run_command(
                            ["security", "find-certificate", "-c", "mkcert"],
                            check=False,
                        )
                        if not trust_check:
                            is_trusted = False

                    if is_trusted:
                        results.append(("mkcert", "Installed (Root CA Trusted)", True))
                    else:
                        results.append(("mkcert", "Installed (NOT TRUSTED)", False))
                else:
                    results.append(("mkcert", "Installed (Root CA NOT FOUND)", False))
            else:
                results.append(("mkcert", "Not installed", False))
        except Exception:
            results.append(("mkcert", "Not found in PATH", False))

        # 4. OpenSSL Check
        try:
            openssl_version = run_command(["openssl", "version"], check=False)
            if openssl_version:
                results.append(("OpenSSL", openssl_version, True))
            else:
                if platform.system().lower() == "windows":
                    results.append(
                        (
                            "OpenSSL",
                            "Not found (Install Git for Windows, Scoop, or Chocolatey)",
                            False,
                        )
                    )
                else:
                    results.append(("OpenSSL", "Not found", False))
        except Exception:
            results.append(("OpenSSL", "Not found in PATH", False))

        # 4.1 Liferay Cloud Check
        try:
            lcp_bin = shutil.which("lcp")
            if lcp_bin:
                is_auth, _ = self._is_cloud_authenticated()
                if is_auth:
                    results.append(("Liferay Cloud Auth", "Logged In", True))
                else:
                    results.append(
                        (
                            "Liferay Cloud Auth",
                            "Not Logged In (Run 'lcp login')",
                            "warn",
                        )
                    )
            else:
                results.append(("Liferay Cloud Auth", "LCP CLI Not Installed", "warn"))
        except Exception:
            pass

        # 4.2 Global Config Check
        common_dir = Path.cwd() / "common"
        if not common_dir.exists():
            results.append(
                ("Global Config", "Missing ('ldm init-common' available)", "warn")
            )
        else:
            try:
                import importlib.resources as pkg_resources
                from ldm_core import resources

                # Check portal-ext.properties specifically
                pe_file = common_dir / "portal-ext.properties"
                if not pe_file.exists():
                    results.append(
                        ("Global Config", "Overrides Active (no baseline)", True)
                    )
                else:
                    baseline_content = (
                        pkg_resources.files(resources)
                        / "common_baseline"
                        / "portal-ext.properties"
                    ).read_text()
                    if pe_file.read_text().strip() == baseline_content.strip():
                        results.append(("Global Config", "Baseline (v1.5.5)", True))
                    else:
                        results.append(("Global Config", "Custom Overrides", True))
            except Exception:
                results.append(("Global Config", "Overrides Active", True))

        # 4.3 Traefik Config Check
        if project_id:
            project_path = self.detect_project_path(project_id)
            if project_path:
                meta = self.read_meta(project_path / PROJECT_META_FILE)
                host_name = meta.get("host_name", "localhost")
                if host_name != "localhost":
                    actual_home = get_actual_home()
                    traefik_conf = (
                        actual_home
                        / "liferay-docker-certs"
                        / f"traefik-{host_name}.yml"
                    )
                    if traefik_conf.exists():
                        results.append(
                            (
                                "Traefik Project SSL",
                                f"Config loaded ({host_name})",
                                True,
                            )
                        )
                    else:
                        results.append(
                            (
                                "Traefik Project SSL",
                                f"Config MISSING ({host_name})",
                                False,
                            )
                        )

        # 5. Network Check
        if docker_version:
            has_net = run_command(
                ["docker", "network", "inspect", "liferay-net"], check=False
            )
            if has_net:
                results.append(("Docker Network", "liferay-net exists", True))
            else:
                results.append(("Docker Network", "liferay-net missing", False))

            # 6. Global Services Check
            global_services = [
                ("liferay-proxy-global", "Global SSL Proxy"),
                ("liferay-search-global", "Global Search (ES8)"),
            ]

            # The bridge is only relevant for macOS (Darwin)
            if platform.system().lower() == "darwin":
                global_services.append(("docker-socket-proxy", "Docker Socket Bridge"))

            for container, label in global_services:
                is_running = run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{container}$"], check=False
                )
                if is_running:
                    results.append((label, "Running", True))
                else:
                    results.append((label, "Not running", "warn"))
        else:
            results.append(("Docker Network", "Skipped (Engine down)", "warn"))
            results.append(("Global Infrastructure", "Skipped (Engine down)", "warn"))

        # 7. Project-Specific Check (Optional)
        project_path = self.detect_project_path(project_id)
        if project_path:
            # 7.1 Compose File Check
            if self.require_compose(project_path, silent=True):
                results.append(("Project Config", "docker-compose.yml OK", True))
            else:
                results.append(("Project Config", "docker-compose.yml MISSING", False))

            # 7.2 DNS Check (Centralized)
            dns_ok, unresolved = self.validate_project_dns(project_path)
            meta = self.read_meta(project_path / PROJECT_META_FILE)
            host_name = meta.get("host_name", "localhost")
            if host_name != "localhost":
                if dns_ok:
                    results.append(
                        (f"Project DNS ({host_name})", "All domains resolve", True)
                    )
                else:
                    results.append(
                        (
                            f"Project DNS ({host_name})",
                            f"{len(unresolved)} domain(s) unresolved",
                            False,
                        )
                    )
                    for d in unresolved:
                        print(f"  {UI.RED}×{UI.COLOR_OFF} {d}")

            # 7.3 Environment Check (Centralized)
            try:
                # Note: verify_runtime_environment may exit if fatal,
                # but for doctor we want a report.
                # We skip the fatal check for now and just check if we can reach it.
                if platform.system().lower() == "darwin":
                    results.append(("Mount Verification", "Checked on run", "warn"))
            except Exception:
                pass
        # Print Results
        print(f"{'Component':<25} {'Status':<30}")
        print("-" * 60)

        all_ok, has_warnings = True, False
        for component, status, ok in results:
            if ok is True:
                color = UI.GREEN
                icon = "✅"
            elif ok == "warn":
                color = UI.YELLOW
                icon = "⚠️ "
                has_warnings = True
            else:
                color = UI.RED
                icon = "❌"
                all_ok = False
            print(f"{component:<25} {color}{icon} {status}{UI.COLOR_OFF}")

        if all_ok and not has_warnings:
            UI.success("Everything looks good! Your environment is ready.")
        elif all_ok and has_warnings:
            UI.warning("Some non-critical issues were detected. Check the items above.")
        else:
            UI.error("Critical issues were detected. Check the items above.")

    def cmd_list(self):
        UI.heading("LDM Sandbox Projects")
        roots = self.find_dxp_roots()
        if not roots:
            UI.info("No projects found.")
            return

        print(
            f"{UI.WHITE}{'Project':<25} {'Version':<15} {'Status':<12} {'URL'}{UI.COLOR_OFF}"
        )
        print("-" * 80)

        for r in roots:
            path = r["path"]
            meta = self.read_meta(path / PROJECT_META_FILE)
            name = meta.get("container_name") or path.name
            version = r["version"]

            # Check container status
            containers_status = run_command(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    f"label=com.liferay.ldm.project={name}",
                    "--format",
                    "{{.State}}",
                ],
                check=False,
            )
            if containers_status:
                states = containers_status.splitlines()
                running_count = states.count("running")
                total_count = len(states)
                if total_count > 1:
                    status = (
                        f"Running ({running_count}/{total_count})"
                        if running_count > 0
                        else f"Stopped (0/{total_count})"
                    )
                    status_color = UI.GREEN if running_count > 0 else UI.WHITE
                else:
                    status = states[0].capitalize()
                    status_color = UI.GREEN if status == "Running" else UI.WHITE
            else:
                status = "Stopped"
                status_color = UI.WHITE

            # Access URL
            host = meta.get("host_name", "localhost")
            port = meta.get("port", "8080")
            ssl = str(meta.get("ssl")).lower() == "true"
            ssl_port = meta.get("ssl_port", "443")

            proto = "https" if ssl else "http"
            access_port = (
                f":{ssl_port}"
                if (ssl and ssl_port != "443")
                else (f":{port}" if not ssl else "")
            )
            url = f"{proto}://{host}{access_port}"

            print(
                f"{name:<25} {version:<15} {status_color}{status:<12}{UI.COLOR_OFF} {UI.CYAN}{url}{UI.COLOR_OFF}"
            )

    def cmd_prune(self):
        UI.heading("LDM Global Maintenance - Pruning Orphaned Resources")

        roots = self.find_dxp_roots()
        active_projects = {
            self.read_meta(r["path"] / PROJECT_META_FILE).get("container_name")
            for r in roots
        }
        active_projects.discard(None)

        # 1. Orphaned Containers
        # We look for containers with our management label and check if their project folder still exists
        containers_raw = run_command(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "label=com.liferay.ldm.managed=true",
                "--format",
                '{{.Names}}|{{.Label "com.liferay.ldm.project"}}',
            ],
            check=False,
        )

        orphans = []
        if containers_raw:
            for line in containers_raw.splitlines():
                if "|" not in line:
                    continue
                name, project = line.split("|")
                if project not in active_projects:
                    orphans.append(name)

        if orphans:
            UI.info(f"Found {len(orphans)} orphaned containers from deleted projects:")
            for o in orphans:
                print(f"  - {o}")
            if UI.ask("Remove them? (y/n/q)", "N").upper() == "Y":
                for o in orphans:
                    run_command(["docker", "rm", "-f", o])
                UI.success("Orphaned containers removed.")
        else:
            UI.info("No orphaned containers found.")

        # 2. Clean up .tmp files
        tmp_files = list(SCRIPT_DIR.glob("**/.*.tmp"))
        if tmp_files:
            UI.info(f"Found {len(tmp_files)} temporary files.")
            if UI.ask("Remove them? (y/n/q)", "Y").upper() == "Y":
                for f in tmp_files:
                    f.unlink()
                UI.success("Temporary files removed.")

        UI.info("Prune complete.")
