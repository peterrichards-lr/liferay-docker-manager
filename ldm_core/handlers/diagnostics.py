import os
import sys
import platform
import shutil
import json
import subprocess
from pathlib import Path
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE, SCRIPT_DIR, VERSION, BUILD_INFO
from ldm_core.utils import (
    run_command,
    get_actual_home,
    check_for_updates,
    version_to_tuple,
    verify_executable_checksum,
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

    def cmd_upgrade(self):
        """Self-upgrade the LDM binary to the latest version."""
        UI.heading("LDM Self-Upgrade")
        is_repair = getattr(self.args, "repair", False)

        # 1. Check for updates
        latest, url = check_for_updates(VERSION, force=True)

        # In repair mode, if we are already latest, we use the current VERSION as target
        if is_repair and (
            not latest or version_to_tuple(latest) <= version_to_tuple(VERSION)
        ):
            latest = VERSION
            # Fetch URL for current version if latest check didn't provide one
            if not url:
                # Construct official asset URL for current version
                system = platform.system().lower()
                target_asset = "ldm-linux"
                if system == "darwin":
                    target_asset = "ldm-macos"
                elif system in ["win32", "windows"]:
                    target_asset = "ldm-windows.exe"
                url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{VERSION}/{target_asset}"

        if not latest or (
            version_to_tuple(latest) <= version_to_tuple(VERSION) and not is_repair
        ):
            UI.success(f"LDM is already up to date (v{VERSION}).")
            return

        if is_repair:
            UI.info(f"Repairing current version: v{latest}")
        else:
            UI.info(f"New version found: v{latest}")

        if not url or not url.startswith("http"):
            UI.die("Download URL not found for your architecture.")

        prompt = (
            f"Repair v{latest}?"
            if is_repair
            else f"Upgrade from v{VERSION} to v{latest}?"
        )
        if not self.non_interactive and not UI.ask(prompt, "Y").upper() == "Y":
            UI.info("Operation aborted.")
            return

        # 2. Preparation
        exe_path = Path(sys.argv[0]).resolve()
        if exe_path.suffix.lower() == ".py":
            UI.die(
                "Self-upgrade is only supported for standalone binaries. Please use 'git pull' for source installations."
            )

        # Check for write permissions to both the parent directory and the binary itself
        if not os.access(exe_path.parent, os.W_OK) or not os.access(exe_path, os.W_OK):
            if platform.system().lower() == "windows":
                UI.die(
                    "Permission denied. Please run your terminal as an Administrator to upgrade."
                )
            else:
                UI.die(
                    f"Permission denied. Please run: {UI.CYAN}sudo ldm upgrade{UI.COLOR_OFF}"
                )

        temp_new = exe_path.with_suffix(".new")

        # 3. Download
        UI.info(f"Downloading v{latest}...")
        try:
            from urllib.request import Request, urlopen

            req = Request(url, headers={"User-Agent": "ldm-cli"})
            with urlopen(req, timeout=30) as response:  # nosec B310
                with open(temp_new, "wb") as f:
                    shutil.copyfileobj(response, f)
        except Exception as e:
            UI.die("Download failed.", e)

        # 4. Verify Integrity
        UI.info("Verifying integrity...")
        status, ok, _ = verify_executable_checksum(
            latest
        )  # Verify the NEW binary version
        # Note: verify_executable_checksum uses sys.argv[0], so we need a manual check for the .new file
        import hashlib

        sha = hashlib.sha256()
        with open(temp_new, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha.update(chunk)
        new_hash = sha.hexdigest()

        # Fetch official checksums.txt
        checksum_url = f"https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{latest}/checksums.txt"
        try:
            req_check = Request(checksum_url, headers={"User-Agent": "ldm-cli"})
            with urlopen(req_check, timeout=10) as resp:  # nosec B310
                official_data = resp.read().decode()

                system = platform.system().lower()
                target_name = "ldm-linux"
                if system == "darwin":
                    target_name = "ldm-macos"
                elif system == "windows":
                    target_name = "ldm-windows.exe"

                verified = False
                for line in official_data.splitlines():
                    if target_name in line and new_hash == line.split()[0]:
                        verified = True
                        break

                if not verified:
                    if temp_new.exists():
                        temp_new.unlink()
                    UI.die(
                        "Integrity verification failed! The downloaded binary does not match the official hash."
                    )
        except Exception as e:
            UI.warning(
                f"Could not verify hash remotely ({e}). Proceeding with caution..."
            )

        # 5. Atomic Swap
        UI.info("Applying update...")
        try:
            if platform.system().lower() == "windows":
                # Windows replacement logic via temporary batch file
                bat_path = exe_path.with_suffix(".update.bat")
                bat_content = f"""@echo off
timeout /t 1 /nobreak > nul
move /y "{temp_new}" "{exe_path}"
start "" "{exe_path}" doctor
del "%~f0"
"""
                bat_path.write_text(bat_content)
                UI.success(
                    "Update staged. LDM will restart in a new window to complete."
                )
                import subprocess

                # Bandit: B602 (shell=True) is necessary here to launch the independent Windows batch updater.
                # The path is internally generated and sanitized.
                subprocess.Popen(["cmd.exe", "/c", str(bat_path)], shell=True)  # nosec B602
                sys.exit(0)
            else:
                # Unix atomic rename
                # Bandit: B103 (chmod 0o755) is necessary to make the newly downloaded binary executable.
                os.chmod(temp_new, 0o755)  # nosec B103
                os.replace(temp_new, exe_path)
                UI.success(f"Successfully upgraded to v{latest}!")
        except Exception as e:
            if temp_new.exists():
                temp_new.unlink()
            UI.die("Failed to apply update.", e)

    def cmd_doctor(self, project_id=None):
        UI.heading("LDM Doctor - Environmental Health Check")

        # 0. Early Project Resolve (Optional skip allowed)
        skip_project = getattr(self.args, "skip_project", False)
        project_path = None
        if not skip_project:
            project_path = self.detect_project_path(project_id)

        results = []

        # 0. Version Check
        v_display = f"v{VERSION}"
        if BUILD_INFO:
            v_display += f" ({BUILD_INFO})"

        latest, _ = check_for_updates(VERSION, force=True)
        if latest and version_to_tuple(latest) > version_to_tuple(VERSION):
            results.append(
                (
                    "LDM Version",
                    f"{v_display} (v{latest} available - Run 'ldm upgrade')",
                    "warn",
                )
            )
        else:
            results.append(("LDM Version", f"{v_display} (Latest)", True))

        # 0.1 Executable Integrity
        status, ok, detected_version = verify_executable_checksum(VERSION)
        if not status:
            status, ok = "Verification Unavailable", "warn"

        # If the version in memory differs from the one detected in the binary,
        # it means shadowing is occurring. We update the version reporting to reflect this.
        if detected_version != VERSION:
            # Re-check for updates using the ACTUAL binary version
            latest, _ = check_for_updates(detected_version, force=True)
            if latest and version_to_tuple(latest) > version_to_tuple(detected_version):
                results[0] = (
                    "LDM Version",
                    f"v{detected_version} (v{latest} available - Run 'ldm upgrade')",
                    "warn",
                )
            else:
                results[0] = ("LDM Version", f"v{detected_version} (Latest)", True)

            # Add a shadow warning to the integrity status
            status = f"{status} (Shadowed by {VERSION})"
            ok = "warn" if ok is True else ok

        results.append(("Executable Integrity", status, ok))

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

            # 2.0 Docker Context & Provider Check
            try:
                context = run_command(["docker", "context", "show"], check=False)
                if context:
                    context = context.strip()
                    provider = "Unknown"

                    # Inspect the host endpoint for more accurate provider detection
                    endpoint = run_command(
                        [
                            "docker",
                            "context",
                            "inspect",
                            "--format",
                            "{{.Endpoints.docker.Host}}",
                            context,
                        ],
                        check=False,
                    )

                    if endpoint:
                        if ".colima" in endpoint:
                            provider = "Colima"
                        elif "orbstack" in endpoint:
                            provider = "OrbStack"
                        elif "docker.sock" in endpoint or "docker_engine" in endpoint:
                            provider = "Docker Desktop"

                    # Fallback to name-based if endpoint check was inconclusive
                    if provider == "Unknown":
                        if context == "colima":
                            provider = "Colima"
                        elif context == "orbstack":
                            provider = "OrbStack"

                    results.append(("Docker Context", context, True))
                    results.append(("Docker Provider", provider, True))
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
            mkcert_bin = shutil.which("mkcert")
            if mkcert_bin:
                ca_root = run_command([mkcert_bin, "-CAROOT"], check=False)
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
                        results.append(("mkcert", "Installed (NOT TRUSTED)", "warn"))
                else:
                    results.append(("mkcert", "Installed (Root CA NOT FOUND)", "warn"))
            else:
                results.append(("mkcert", "Not installed", "warn"))
        except Exception:
            results.append(("mkcert", "Not found in PATH", "warn"))

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
        common_dir = self.get_common_dir(project_path)

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
                        results.append(
                            ("Global Config", f"Baseline (v{VERSION})", True)
                        )
                    else:
                        prop_status, prop_ok, prop_details = (
                            self.validate_properties_file(pe_file)
                        )
                        if prop_ok is True:
                            results.append(("Global Config", "Custom Overrides", True))
                        else:
                            results.append(
                                (
                                    "Global Config",
                                    f"Invalid Format ({prop_status})",
                                    prop_ok,
                                )
                            )
                            if prop_details:
                                for detail in prop_details:
                                    print(
                                        f"  {UI.YELLOW}⚠{UI.COLOR_OFF} [Global] {detail}"
                                    )

                # Check for Global Search Configs
                es_main = (
                    common_dir
                    / "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConfiguration.config"
                )
                es_conn = (
                    common_dir
                    / "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConnectionConfiguration.config"
                )
                if not es_main.exists() or not es_conn.exists():
                    results.append(
                        (
                            "Global Search Config",
                            "Missing (Run 'ldm init-common')",
                            "warn",
                        )
                    )
                else:
                    results.append(("Global Search Config", "REMOTE mode ready", True))
            except Exception:
                results.append(("Global Config", "Overrides Active", True))

        # 5. Network Check
        if docker_version:
            has_net = run_command(
                ["docker", "network", "inspect", "liferay-net"], check=False
            )
            if has_net:
                results.append(("Docker Network", "liferay-net exists", True))
            else:
                results.append(
                    ("Docker Network", "missing (will be created on run)", "warn")
                )

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
                    status = "Running"
                    ok = True

                    # Deep Check: Bridge Network Connectivity (macOS)
                    if container == "docker-socket-proxy":
                        inspect = run_command(
                            [
                                "docker",
                                "network",
                                "inspect",
                                "liferay-net",
                                "-f",
                                "{{range .Containers}}{{.Name}} {{end}}",
                            ],
                            check=False,
                        )
                        if container not in (inspect or ""):
                            status = "Isolated (Connect with ldm infra-setup)"
                            ok = "warn"

                    # Deep Check: Search Cluster API
                    if container == "liferay-search-global":
                        # Perform a quick API ping
                        try:
                            # We use curl -I to check for a response from the ES8 cluster
                            search_res = run_command(
                                [
                                    "curl",
                                    "-s",
                                    "-o",
                                    "/dev/null",
                                    "-w",
                                    "%{http_code}",
                                    "http://localhost:9200",
                                ],
                                check=False,
                            )
                            if search_res != "200" and search_res != "401":
                                status = f"Unreachable (HTTP {search_res})"
                                ok = "warn"
                        except Exception:
                            pass

                    # Deep Check: Log Health (Generic for all infrastructure)
                    log_status, log_ok = self._check_container_health_logs(container)
                    if log_status:
                        status = log_status
                        ok = log_ok

                    results.append((label, status, ok))
                else:
                    cmd_hint = "ldm infra-setup"
                    if container == "liferay-search-global":
                        cmd_hint = "ldm infra-setup --search"
                        results.append(
                            (
                                label,
                                f"Not running (Run '{cmd_hint}') {UI.WHITE}(Sidecar will be used){UI.COLOR_OFF}",
                                "warn",
                            )
                        )
                    else:
                        results.append(
                            (label, f"Not running (Run '{cmd_hint}')", "warn")
                        )
        else:
            results.append(("Docker Network", "Skipped (Engine down)", "warn"))
            results.append(("Global Infrastructure", "Skipped (Engine down)", "warn"))

        # 7. Project-Specific Check (Optional)
        if project_path:
            # 7.1 Metadata Health Check
            meta = self.read_meta(project_path / PROJECT_META_FILE)
            env_args = meta.get("env_args", [])
            blacklisted_prefixes = [
                "LIFERAY_ELASTICSEARCH",
                "LIFERAY_WEB_SERVER",
                "LIFERAY_VIRTUAL_HOSTS",
                "LIFERAY_CLUSTER",
                "LIFERAY_LUCENE",
                "COM_LIFERAY_LXC_DXP",
            ]
            poisoned = [
                arg
                for arg in env_args
                if any(arg.startswith(p) for p in blacklisted_prefixes)
            ]
            if poisoned:
                results.append(
                    (
                        "Project Metadata",
                        f"Poisoned ({len(poisoned)} legacy vars)",
                        "warn",
                    )
                )
            else:
                results.append(("Project Metadata", "Healthy", True))

            # 7.2 Compose File Check
            if self.require_compose(project_path, silent=True):
                results.append(("Project Config", "docker-compose.yml OK", True))
            else:
                results.append(("Project Config", "docker-compose.yml MISSING", False))

            # 7.2.1 Portal Properties Validation
            pe_file = project_path / "files" / "portal-ext.properties"
            if pe_file.exists():
                prop_status, prop_ok, prop_details = self.validate_properties_file(
                    pe_file
                )
                results.append(("Portal Properties", prop_status, prop_ok))
                if prop_details:
                    for detail in prop_details:
                        print(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {detail}")
            else:
                results.append(
                    ("Portal Properties", "portal-ext.properties MISSING", "warn")
                )

            # 7.2.2 OSGi Search Config Check
            osgi_config_dir = project_path / "osgi" / "configs"
            es_main_conf = (
                osgi_config_dir
                / "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConfiguration.config"
            )
            es_conn_conf = (
                osgi_config_dir
                / "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConnectionConfiguration.config"
            )

            if es_main_conf.exists() and es_conn_conf.exists():
                results.append(("OSGi Search Config", "REMOTE mode detected", True))
            elif es_main_conf.exists() or es_conn_conf.exists():
                results.append(("OSGi Search Config", "Partial / Incomplete", "warn"))
            else:
                results.append(
                    (
                        "OSGi Search Config",
                        "Missing (Liferay will start sidecar)",
                        "warn",
                    )
                )

            # 7.2.3 License Check
            lic_status, lic_ok, lic_details = self.check_license_health(
                {"common": common_dir, **self.setup_paths(project_path)},
                image_tag=meta.get("tag"),
            )
            results.append(("Project License", lic_status, lic_ok))
            if lic_details:
                for detail in lic_details:
                    print(f"  {UI.CYAN}ℹ{UI.COLOR_OFF} {detail}")

            # 7.3 SSL Certificate Check
            host_name = meta.get("host_name", "localhost")
            ssl_enabled = str(meta.get("ssl", "false")).lower() == "true"
            ssl_cert_name = meta.get("ssl_cert")

            if ssl_enabled and host_name != "localhost":
                actual_home = get_actual_home()
                cert_dir = actual_home / "liferay-docker-certs"
                cert_file = cert_dir / (ssl_cert_name or f"{host_name}.pem")
                key_file = cert_dir / cert_file.name.replace(".pem", "-key.pem")
                traefik_conf = cert_dir / f"traefik-{host_name}.yml"

                # Check .pem and -key.pem
                if cert_file.exists() and key_file.exists():
                    cert_status = "Cert & Key OK"
                    # Try to get expiry info if openssl is available
                    openssl_bin = shutil.which("openssl")
                    if openssl_bin:
                        try:
                            expiry_res = run_command(
                                [
                                    openssl_bin,
                                    "x509",
                                    "-enddate",
                                    "-noout",
                                    "-in",
                                    str(cert_file),
                                ],
                                check=False,
                            )
                            if expiry_res and "notAfter=" in expiry_res:
                                expiry_date = expiry_res.split("=", 1)[1].strip()
                                cert_status = f"Valid until {expiry_date}"
                        except Exception:
                            pass
                    results.append(("Project SSL Cert", cert_status, True))
                else:
                    results.append(
                        ("Project SSL Cert", "Missing (.pem or -key.pem)", False)
                    )

                # Check Traefik YAML
                if traefik_conf.exists():
                    conf_content = traefik_conf.read_text()
                    expected_cert = f"certFile: /etc/traefik/certs/{host_name}.pem"
                    expected_key = f"keyFile: /etc/traefik/certs/{host_name}-key.pem"

                    if expected_cert in conf_content and expected_key in conf_content:
                        results.append(("Traefik Project SSL", "Config OK", True))
                    else:
                        results.append(
                            ("Traefik Project SSL", "Invalid Content", "warn")
                        )
                else:
                    results.append(("Traefik Project SSL", "Config MISSING", False))

            # 7.3.1 Traefik Label Validation
            compose_file = project_path / "docker-compose.yml"
            if compose_file.exists():
                try:
                    import yaml

                    with open(compose_file, "r") as f:
                        compose_data = yaml.safe_load(f)

                    liferay_service = compose_data.get("services", {}).get(
                        "liferay", {}
                    )
                    labels = liferay_service.get("labels", [])

                    # Convert labels list/dict to a flat list of strings for easier checking
                    label_list = []
                    if isinstance(labels, list):
                        label_list = labels
                    elif isinstance(labels, dict):
                        label_list = [f"{k}={v}" for k, v in labels.items()]

                    p_id = meta.get("container_name") or project_path.name
                    has_net_label = any(
                        "traefik.docker.network=liferay-net" in label
                        for label in label_list
                    )

                    # Detect double-prefixing: search for labels containing the p_id twice in the name part
                    double_prefixed = []
                    for label in label_list:
                        if "=" in label:
                            key = label.split("=", 1)[0]
                            if key.count(p_id) > 1:
                                double_prefixed.append(key)

                    if not has_net_label:
                        results.append(
                            ("Traefik Labels", "Missing Network Label", False)
                        )
                    elif double_prefixed:
                        results.append(
                            (
                                "Traefik Labels",
                                f"Double Prefixed ({len(double_prefixed)} labels)",
                                "warn",
                            )
                        )
                        for dp in double_prefixed:
                            print(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {dp}")
                    else:
                        results.append(("Traefik Labels", "Standardized OK", True))
                except Exception as e:
                    results.append(("Traefik Labels", f"Check Failed ({e})", "warn"))

            # 7.4 DNS Check (Centralized)
            dns_ok, unresolved = self.validate_project_dns(project_path)
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

            # 7.5 Environment Check (Centralized)
            try:
                if platform.system().lower() == "darwin":
                    # Determine if the Liferay container is running for this project
                    p_id = meta.get("container_name") or project_path.name

                    # Try LDM-standard name first, then fall back to Compose's default
                    liferay_container = None
                    possible_names = [f"{p_id}-liferay", f"{p_id}-liferay-1", p_id]
                    for name in possible_names:
                        if run_command(
                            ["docker", "ps", "-q", "-f", f"name=^{name}$"], check=False
                        ):
                            liferay_container = name
                            break

                    if liferay_container:
                        # Perform a LIVE check inside the running container
                        # 1. Create a fresh token in a mounted subdirectory (e.g., /deploy)
                        # We use /deploy because the project root itself is not fully mounted.
                        import uuid

                        token_val = f"DOCTOR_LIVE_{uuid.uuid4().hex[:8]}"
                        deploy_dir = project_path / "deploy"
                        token_file = deploy_dir / ".ldm_doctor_check"

                        try:
                            deploy_dir.mkdir(parents=True, exist_ok=True)
                            token_file.write_text(token_val)

                            # 2. Try to read it from INSIDE the container
                            # In Liferay containers, 'deploy' is always at /opt/liferay/deploy
                            verify_res = run_command(
                                [
                                    "docker",
                                    "exec",
                                    liferay_container,
                                    "cat",
                                    "/opt/liferay/deploy/.ldm_doctor_check",
                                ],
                                check=False,
                            )

                            if token_val in (verify_res or ""):
                                results.append(
                                    ("Mount Verification", "Live (OK)", True)
                                )
                            else:
                                results.append(
                                    (
                                        "Mount Verification",
                                        "BROKEN (Not visible in container)",
                                        False,
                                    )
                                )
                        finally:
                            # Cleanup
                            if token_file.exists():
                                token_file.unlink()
                    else:
                        results.append(
                            ("Mount Verification", "Verified on start", True)
                        )
            except Exception:
                pass

        # Print Results
        print(f"{'Component':<25} {'Status':<30}")
        print("-" * 60)

        all_ok, has_warnings = True, False
        for component, status, ok in results:
            if ok is True:
                color = UI.GREEN
                icon = "✅ "
            elif ok == "warn":
                color = UI.YELLOW
                icon = "⚠️ "
                has_warnings = True
            else:
                color = UI.RED
                icon = "❌ "
                all_ok = False
            print(f"{component:<25} {color}{icon} {status}{UI.COLOR_OFF}")

        if all_ok and not has_warnings:
            UI.success("Everything looks good! Your environment is ready.")
        elif all_ok and has_warnings:
            UI.warning("Some non-critical issues were detected. Check the items above.")
        else:
            UI.error("Critical issues were detected. Check the items above.")
            sys.exit(1)

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
        active_projects = set()
        active_hostnames = set()
        for r in roots:
            meta = self.read_meta(r["path"] / PROJECT_META_FILE)
            # Use container_name from meta, or fall back to folder name
            name = meta.get("container_name") or r["path"].name
            active_projects.add(name)
            host = meta.get("host_name")
            if host and host != "localhost":
                active_hostnames.add(host)

        if self.verbose:
            UI.debug(
                f"Active projects identified: {', '.join(active_projects) if active_projects else 'None'}"
            )

        # 1. Orphaned Containers
        # We look for containers with our management label
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
                line = line.strip()
                if not line or "|" not in line:
                    continue

                # Docker names can sometimes have a leading slash
                name, project = line.split("|", 1)
                name = name.lstrip("/")

                if not project or project not in active_projects:
                    orphans.append(name)

        if orphans:
            UI.info(f"Found {len(orphans)} orphaned containers from deleted projects:")
            for o in orphans:
                print(f"  - {o}")
            if (
                self.non_interactive
                or UI.ask("Remove them? (y/n/q)", "N").upper() == "Y"
            ):
                for o in orphans:
                    run_command(["docker", "rm", "-f", o])
                UI.success("Orphaned containers removed.")
        else:
            UI.info("No orphaned containers found.")

        # 2. Orphaned Search Snapshots
        search_name = "liferay-search-global"
        if run_command(["docker", "ps", "-q", "-f", f"name={search_name}"]):
            snaps_raw = run_command(
                [
                    "docker",
                    "exec",
                    search_name,
                    "curl",
                    "-s",
                    "localhost:9200/_snapshot/liferay_backup/_all",
                ],
                check=False,
            )
            if snaps_raw:
                try:
                    data = json.loads(snaps_raw)
                    all_snaps = data.get("snapshots", [])
                    orphaned_snaps = []
                    for s in all_snaps:
                        s_name = s.get("snapshot", "")
                        # LDM search snapshots follow the pattern [project-name]-[timestamp]
                        if "-" in s_name:
                            project_id = s_name.rsplit("-", 2)[0]
                            if project_id not in active_projects:
                                orphaned_snaps.append(s_name)
                        elif s_name == "initial_snapshot":
                            # Special case for legacy manual snapshots
                            orphaned_snaps.append(s_name)

                    if orphaned_snaps:
                        UI.info(
                            f"Found {len(orphaned_snaps)} orphaned search snapshots:"
                        )
                        for s in orphaned_snaps:
                            print(f"  - {s}")
                        if (
                            self.non_interactive
                            or UI.ask("Remove them from global vault?", "N").upper()
                            == "Y"
                        ):
                            for s in orphaned_snaps:
                                run_command(
                                    [
                                        "docker",
                                        "exec",
                                        search_name,
                                        "curl",
                                        "-s",
                                        "-X",
                                        "DELETE",
                                        f"localhost:9200/_snapshot/liferay_backup/{s}",
                                    ],
                                    check=False,
                                )
                            UI.success("Orphaned search snapshots removed.")
                    else:
                        UI.info("No orphaned search snapshots found.")
                except Exception:
                    pass

        # 3. Clean up .tmp files
        tmp_files = list(SCRIPT_DIR.glob("**/.*.tmp"))
        if tmp_files:
            UI.info(f"Found {len(tmp_files)} temporary files.")
            if (
                self.non_interactive
                or UI.ask("Remove them? (y/n/q)", "Y").upper() == "Y"
            ):
                for f in tmp_files:
                    f.unlink()
                UI.success("Temporary files removed.")

        # 4. Orphaned SSL Certificates
        cert_dir = get_actual_home() / "liferay-docker-certs"
        if cert_dir.exists():
            orphaned_certs = []
            # Patterns to look for: {host}.pem, {host}-key.pem, traefik-{host}.yml
            for f in cert_dir.iterdir():
                if not f.is_file():
                    continue

                host = None
                if f.name.startswith("traefik-") and f.suffix == ".yml":
                    host = f.name[8:-4]
                elif f.name.endswith("-key.pem"):
                    host = f.name[:-8]
                elif f.suffix == ".pem":
                    host = f.name[:-4]

                if host and host not in active_hostnames:
                    orphaned_certs.append(f)

            if orphaned_certs:
                UI.info(f"Found {len(orphaned_certs)} orphaned SSL artifacts:")
                for c in orphaned_certs:
                    print(f"  - {c.name}")
                if (
                    self.non_interactive
                    or UI.ask("Remove them from global cert store?", "N").upper() == "Y"
                ):
                    for c in orphaned_certs:
                        c.unlink()
                    UI.success("Orphaned SSL artifacts removed.")
            else:
                UI.info("No orphaned SSL artifacts found.")

        UI.info("Prune complete.")

    def validate_properties_file(self, file_path):
        """Checks for structural errors in a .properties file."""
        errors = []
        try:
            lines = file_path.read_text().splitlines()
            if not lines:
                return "Empty File", "warn", []

            last_line_continued = False
            for i, line in enumerate(lines):
                line_num = i + 1
                stripped = line.strip()

                # If the previous line ended in '\', this line MUST be a continuation
                if last_line_continued:
                    if not stripped:
                        errors.append(
                            f"Broken continuation (L{line_num}): Backslash followed by empty line"
                        )
                    elif "=" in stripped and not stripped.startswith(("#", "!")):
                        errors.append(
                            f"Merge collision (L{line_num}): Backslash followed by new property '{stripped[:15]}...'"
                        )

                # Update state for next line
                if not stripped or stripped.startswith(("#", "!")):
                    last_line_continued = False
                    continue

                # Normal orphaned line check (no '=' and not a continuation)
                if not last_line_continued and "=" not in stripped:
                    errors.append(f"Orphaned line (L{line_num}): '{stripped[:20]}...'")

                last_line_continued = stripped.endswith("\\")

            # Final line check: if the last line ends in '\', it's an invalid continuation
            if last_line_continued:
                errors.append(
                    "File ends with a trailing backslash (broken continuation)."
                )

            if errors:
                return f"Inconsistent ({len(errors)} issues)", "warn", errors
            return "Valid Structure", True, []
        except Exception as e:
            return f"Check Failed ({e})", "warn", []

    def _check_container_health_logs(self, container_name, tail=20):
        """Checks the last N lines of container logs for errors or warnings."""
        try:
            # We capture BOTH stdout and stderr
            import subprocess

            res = subprocess.run(
                ["docker", "logs", "--tail", str(tail), container_name],
                capture_output=True,
                text=True,
                check=False,
            )
            logs = (res.stdout or "") + (res.stderr or "")
            if not logs:
                return None, None

            lines = logs.splitlines()

            # 1. Critical Errors (Hard failure keywords)
            critical_keywords = [
                "ERROR",
                "ERR",
                "FATAL",
                "CRITICAL",
                "Exit Code: 1",
                "exception",
            ]
            for line in lines:
                if any(k in line.upper() for k in critical_keywords):
                    # Special case: ignore known non-fatal "operation not supported" errors in some providers
                    if "operation not supported" in line.lower():
                        continue
                    return f"Critical (Error in logs: {line.strip()[:40]}...)", False

            # 2. Warnings
            warning_keywords = [
                "WARN",
                "WARNING",
                "retrying",
                "client version .* is too old",
            ]
            for line in lines:
                if any(k in line.upper() for k in warning_keywords):
                    return f"Warning (Issue in logs: {line.strip()[:40]}...)", "warn"

            return None, None
        except Exception:
            return None, None
