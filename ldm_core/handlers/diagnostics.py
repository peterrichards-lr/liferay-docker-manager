import os
import sys
import platform
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE, SCRIPT_DIR
from ldm_core.utils import run_command, get_actual_home


class DiagnosticsHandler:
    """Mixin for diagnostic and maintenance commands."""

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

        # 1. System Info
        results.append(("Python Version", sys.version.split()[0], True))
        results.append(("Platform", platform.platform(), True))

        # 2. Docker Check
        docker_version = run_command(
            ["docker", "version", "--format", "{{.Server.Version}}"], check=False
        )
        if docker_version:
            results.append(("Docker Engine", f"Running (v{docker_version})", True))

            # 2.1 Docker Resources
            docker_info_raw = run_command(
                ["docker", "info", "--format", "{{json .}}"], check=False
            )
            if docker_info_raw:
                try:
                    import json

                    info = json.loads(docker_info_raw)
                    cpus = info.get("NCPU", 0)
                    mem_bytes = info.get("MemTotal", 0)
                    mem_gb = mem_bytes / (1024**3)

                    results.append(("Docker CPUs", f"{cpus} Cores", cpus >= 4))
                    results.append(("Docker Memory", f"{mem_gb:.1f} GB", mem_gb >= 8))
                except Exception:
                    pass
        else:
            results.append(("Docker Engine", "Not reachable", False))

        # 3. mkcert Check
        try:
            mkcert_version = run_command(["mkcert", "-version"], check=False)
            if mkcert_version:
                ca_root = run_command(["mkcert", "-CAROOT"], check=False)
                if ca_root and os.path.exists(ca_root) and os.listdir(ca_root):
                    results.append(("mkcert", "Installed (Root CA Trusted)", True))
                else:
                    results.append(("mkcert", "Installed (Root CA NOT TRUSTED)", False))
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
                results.append(("OpenSSL", "Not found", False))
        except Exception:
            results.append(("OpenSSL", "Not found in PATH", False))

        # 5. Network Check
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
            ("docker-socket-proxy", "Docker Socket Bridge"),
        ]

        for container, label in global_services:
            is_running = run_command(
                ["docker", "ps", "-q", "-f", f"name=^{container}$"], check=False
            )
            if is_running:
                results.append((label, "Running", True))
            else:
                results.append((label, "Not running", "warn"))

        # 7. Project-Specific Check (Optional)
        project_path = self.detect_project_path(project_id)
        if project_path:
            meta = self.read_meta(project_path / PROJECT_META_FILE)
            host_name = meta.get("host_name")
            if host_name and host_name != "localhost":
                ip = self.get_resolved_ip(host_name)
                if ip and (ip.startswith("127.") or ip in ["::1", "0:0:0:0:0:0:0:1"]):
                    results.append(
                        (f"Project Host ({host_name})", f"Resolves to {ip}", True)
                    )
                else:
                    results.append(
                        (f"Project Host ({host_name})", "Resolution Failed", False)
                    )

        # Print Results
        print(f"{'Component':<25} {'Status':<30}")
        print("-" * 60)

        all_ok = True
        for component, status, ok in results:
            if ok is True:
                color = UI.GREEN
                icon = "✅"
            elif ok == "warn":
                color = UI.YELLOW
                icon = "⚠️ "
            else:
                color = UI.RED
                icon = "❌"
                all_ok = False
            print(f"{component:<25} {color}{icon} {status}{UI.COLOR_OFF}")

        if all_ok:
            UI.success("Everything looks good! Your environment is ready.")
        else:
            UI.warning("Some issues were detected. Check the items above.")

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
            status_raw = run_command(
                ["docker", "inspect", "-f", "{{.State.Status}}", name], check=False
            )
            status = status_raw.capitalize() if status_raw else "Stopped"
            status_color = UI.GREEN if status == "Running" else UI.WHITE

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
