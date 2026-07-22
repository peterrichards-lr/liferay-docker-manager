import contextlib
import time
from datetime import datetime

from ldm_core.docker_service import DockerService
from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import open_browser


class ReadinessService(BaseHandler):
    """Readiness service for runtime operations."""

    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager

    def cmd_wait(  # noqa: C901, PLR0912, PLR0915
        self,
        project_id=None,
        timeout=None,
        wait_for_deployables=False,
        wait_for_bundles=None,
        stream_status=False,
        stream_logs=False,
    ):
        """Block execution until project is fully ready (HTTP 200/302)."""
        if timeout is None:
            timeout = 900

        root = self.manager.detect_project_path(project_id)
        if not root:
            return None
        meta = self.manager.read_meta(root)
        host_name = meta.get("host_name", "localhost")

        container_name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or root.name
        )

        log_proc = None
        if stream_logs:
            import subprocess
            import sys

            log_proc = subprocess.Popen(
                ["docker", "logs", "-f", container_name],
                stdout=sys.stdout,
                stderr=sys.stderr,
            )

        def _die_with_logs(msg):
            if log_proc:
                log_proc.terminate()
            import subprocess
            import sys

            UI.error(
                f"Timeout exhausted. Dumping last 200 lines of logs for {container_name}:"
            )
            subprocess.run(
                ["docker", "logs", "--tail", "200", container_name],
                stdout=sys.stderr,
                stderr=sys.stderr,
                check=False,
            )
            UI.die(msg)

        # 1. Wait for Container/Log Readiness
        if not self._wait_for_ready(
            meta,
            host_name,
            timeout=timeout,
            stream_status=stream_status,
            stream_logs=stream_logs,
        ):
            _die_with_logs(
                f"Project '{project_id}' failed to become ready within {timeout}s."
            )

        # Determine target expected deployables
        expected_targets = {}
        if wait_for_deployables:
            expected_targets.update(
                self.manager.runtime._scan_for_expected_deployables(root)
            )
        if wait_for_bundles:
            for b in wait_for_bundles.split(","):
                expected_targets[b.strip()] = "Active"

        # 2. Wait for HTTP Availability
        UI.detail(
            f"Verifying HTTP accessibility for {UI.CYAN}{host_name}{UI.COLOR_OFF}..."
        )
        ssl_enabled = self.manager.composer._is_ssl_active(host_name, meta)
        port = meta.get("port", 8080)
        protocol = "https" if ssl_enabled else "http"

        # LDM-388: Use explicit IP for local checks to avoid CI IPv6 quirks
        target_host = "127.0.0.1" if host_name == "localhost" else host_name
        url = f"{protocol}://{target_host}"
        if not ssl_enabled and port != 80:
            url += f":{port}"

        phase_start = time.time()
        import requests

        http_ready = False
        while time.time() - phase_start < timeout:
            try:
                # Use a short timeout for the request itself
                response = requests.get(url, timeout=5, verify=False)  # nosec B501
                if response.status_code in [200, 302]:
                    UI.success(
                        f"Project '{project_id}' is responding to HTTP ({response.status_code})."
                    )
                    http_ready = True
                    break
            except Exception as e:
                UI.debug(f"HTTP readiness check failed (will retry): {e}")
            time.sleep(2)

        if not http_ready:
            _die_with_logs(
                f"Project '{project_id}' is running but HTTP {url} is not responding correctly."
            )

        # 2b. Wait for Deployables (OSGi & Client Extensions) if any targets exist
        if expected_targets:
            UI.detail(
                f"Waiting for {len(expected_targets)} deployable targets to be fully active..."
            )
            container_name = (
                meta.get("liferay_container_name")
                or meta.get("container_name")
                or root.name
            )

            # Wait for deploy directory inside container to clear
            UI.detail("Checking deploy directory queue status...")
            deploy_clear = False
            deploy_start = time.time()
            while time.time() - deploy_start < timeout:
                try:
                    res = DockerService.exec(
                        container_name,
                        ["ls", "/opt/liferay/deploy"],
                        check=False,
                    )
                    if res:
                        files = [f.strip() for f in res.splitlines() if f.strip()]
                        deployables = [
                            f for f in files if f.endswith((".jar", ".zip", ".war"))
                        ]
                        if not deployables:
                            deploy_clear = True
                            break
                    else:
                        deploy_clear = True
                        break
                except Exception as e:
                    UI.debug(f"Deploy directory check failed (will retry): {e}")
                time.sleep(2)

            if not deploy_clear:
                UI.warning(
                    "Deploy directory queue did not clear, proceeding to Gogo console verification..."
                )

            # Wait for targets via Gogo Shell
            UI.detail("Verifying target OSGi bundle and Client Extension states...")
            gogo_ready = False
            gogo_start = time.time()
            while time.time() - gogo_start < timeout:
                try:
                    res = DockerService.exec(
                        container_name,
                        ["sh", "-c", "echo 'lb -s' | telnet localhost 11311"],
                        check=False,
                    )
                    if res and "|" in res:
                        # Parse lb -s output
                        bundles = {}
                        for line in res.splitlines():
                            parts = [p.strip() for p in line.split("|")]
                            if len(parts) >= 4:
                                state = parts[1]
                                sym_name = parts[3]
                                bundles[sym_name] = state

                        satisfied = True
                        stalled_bundles = {}
                        missing_bundles = set()

                        for target, expected in expected_targets.items():
                            # Direct match
                            if target in bundles:
                                if bundles[target] != expected:
                                    satisfied = False
                                    stalled_bundles[target] = bundles[target]
                            else:
                                # Client Extension match: symbolic name contains the target ID and "client.extension"
                                cx_bundle_found = False
                                for sym_name, state in bundles.items():
                                    if (
                                        target in sym_name
                                        and "client.extension" in sym_name
                                    ):
                                        cx_bundle_found = True
                                        if state != expected:
                                            satisfied = False
                                            stalled_bundles[target] = state
                                        break
                                if not cx_bundle_found:
                                    satisfied = False
                                    missing_bundles.add(target)

                        if satisfied:
                            UI.success(
                                "All deployables and client extensions are fully started."
                            )
                            gogo_ready = True
                            break
                        # Periodically identify stalled deployables
                        if time.time() - getattr(self, "_last_stalled_print", 0) > 30:
                            if stalled_bundles:
                                warning_msg = "Still waiting for the following local deployables to become ACTIVE:\n"
                                for t, s in stalled_bundles.items():
                                    warning_msg += f"  - {t} (Currently: {s})\n"
                                UI.warning(warning_msg.strip())
                            self._last_stalled_print = time.time()

                        # Fail-Fast for completely missing bundles after 120s
                        if missing_bundles and (time.time() - gogo_start > 120):
                            err_msg = "Fail-Fast: The following required bundles never appeared in the OSGi container (missing from deploy/osgi folders):\n"
                            for t in missing_bundles:
                                err_msg += f"  - {t}\n"
                            _die_with_logs(err_msg.strip())

                    elif res:
                        # Gogo console responded but not with the bundle table (e.g. error/command not found)
                        break
                except Exception as e:
                    UI.debug(f"Gogo shell query failed: {e}")
                time.sleep(3)

            if not gogo_ready:
                UI.warning(
                    "Some deployable targets did not reach active state via Gogo console verification."
                )

        # 3. Wait for System to become Idle (CPU Drop)
        UI.detail("Waiting for background initialization to complete (CPU Idle)...")
        idle_checks = 0
        consecutive_required = 3
        cpu_threshold = 15.0  # Consider < 15% CPU to be "idle" for Liferay

        phase_start = time.time()
        while time.time() - phase_start < timeout:
            try:
                result = self.manager.run_command(
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{.CPUPerc}}",
                        meta.get("container_name"),
                    ],
                    capture_output=True,
                    check=False,
                )
                if result:
                    cpu_str = result.strip().replace("%", "")
                    try:
                        cpu = float(cpu_str)
                        if cpu < cpu_threshold:
                            idle_checks += 1
                            if idle_checks >= consecutive_required:
                                UI.success(
                                    f"Project '{project_id}' is fully initialized and idle."
                                )
                                if log_proc:
                                    log_proc.terminate()
                                return True
                        else:
                            idle_checks = 0
                    except ValueError:
                        pass
            except Exception as e:
                UI.debug(f"Log milestone scan failed (will retry): {e}")
            time.sleep(2)

        UI.warning(
            f"Project '{project_id}' did not reach an idle state within the timeout, but is responding to HTTP."
        )
        if log_proc:
            log_proc.terminate()
        return True

    def _wait_for_ready(  # noqa: C901, PLR0912, PLR0915
        self,
        project_meta,
        host_name,
        total_start=None,
        timeout=600,
        stream_status=False,
        stream_logs=False,
        browser=None,
    ):
        """Wait for Liferay to become healthy and provide access information."""
        container_name = project_meta.get("container_name")
        project_id = project_meta.get("project_name") or container_name
        root_path = (
            self.manager.detect_project_path(project_id, for_init=True)
            if project_id
            else None
        )
        status_file = (
            root_path / ".liferay-docker" / "startup-status.json" if root_path else None
        )

        milestones = [
            ("OSGi Framework Starting", "OSGi run level"),
            (
                "Spring Web Context Initializing",
                "Initializing Spring root WebApplicationContext",
            ),
            ("Portal Startup Progress", "Starting Liferay"),
            ("Available Contexts Registered", "Available contexts"),
            ("Tomcat Server Ready", "Server startup in"),
        ]
        reached_milestones = set()

        @contextlib.contextmanager
        def null_spinner(msg):
            UI.detail(f"[LDM] {msg}")

            class NullSpinner:
                def update(self, m):
                    pass

            yield NullSpinner()

        spinner_ctx = null_spinner if (stream_status or stream_logs) else UI.spinner
        start_time = time.time()
        with spinner_ctx(
            f"Waiting for Liferay to become healthy ({container_name})..."
        ) as spinner:
            last_notified_time = 0.0
            seen_errors = set()
            while time.time() - start_time < timeout:
                elapsed = time.time() - start_time
                # Notify every 30 seconds (Robust timestamp check)
                if elapsed - last_notified_time >= 30:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    duration_str = UI.format_duration(elapsed)
                    spinner.update(f"Still waiting for Liferay ({duration_str})...")
                    last_notified_time = elapsed
                    UI.detail(
                        f"[{timestamp}] Still waiting for Liferay to become healthy... ({duration_str})"
                    )

                    # Proactive Log Monitoring: Look for ERRORS
                    try:
                        logs = self.manager.run_command(
                            ["docker", "logs", "--tail", "100", container_name],
                            check=False,
                            capture_output=True,
                        )
                        if logs:
                            error_lines = [
                                line.strip()
                                for line in logs.splitlines()
                                if "ERROR" in line.upper()
                                or "FATAL" in line.upper()
                                or "CRITICAL" in line.upper()
                            ]

                            new_error_lines = [
                                line for line in error_lines if line not in seen_errors
                            ]

                            if new_error_lines:
                                seen_errors.update(new_error_lines)
                                UI.warning(
                                    f"LDM detected {len(new_error_lines)} new error(s) in the logs."
                                )
                                # Display the most recent unique error
                                last_unique_error = list(
                                    dict.fromkeys(new_error_lines)
                                )[-1]
                                UI.detail(
                                    f"Recent log error: {UI.YELLOW}{last_unique_error[:120]}...{UI.COLOR_OFF}"
                                )

                                # --- Auto-Thaw & Hints Win ---
                                from ldm_core.utils import (
                                    check_troubleshooting_signatures,
                                )

                                advice = None
                                for err_line in reversed(new_error_lines):
                                    advice = check_troubleshooting_signatures(err_line)
                                    if advice:
                                        break

                                if advice:
                                    UI.warning(f"Troubleshooting Advice:\n  {advice}")

                                if (
                                    "ClusterBlockException" in last_unique_error
                                    or "index.blocks.read_only" in last_unique_error
                                ):
                                    UI.warning(
                                        "Detected Elasticsearch disk pressure blocking Liferay startup."
                                    )
                                    if self.manager.infra.thaw_elasticsearch():
                                        UI.success(
                                            "Auto-Thaw successful. Liferay should now proceed."
                                        )
                                    else:
                                        UI.detail(
                                            f"💡 {UI.CYAN}Hint:{UI.COLOR_OFF} Your disk is likely full. Run '{UI.WHITE}ldm prune --seeds --samples{UI.COLOR_OFF}' to free space."
                                        )

                                UI.detail(
                                    f"Check full logs: {UI.WHITE}ldm logs -f {container_name}{UI.COLOR_OFF}"
                                )
                    except Exception as e:
                        UI.detail(f"Warning checking startup logs context: {e}")

                    last_notified_time = elapsed

                # LDM-385: Enhanced readiness check
                # We look for the Tomcat 'Server startup' log marker as it's often
                # faster/more reliable than the Docker healthcheck in CI.
                ready_by_logs = False
                try:
                    logs = self.manager.run_command(
                        ["docker", "logs", "--tail", "100", container_name],
                        check=False,
                        capture_output=True,
                    )
                    if logs:
                        if (
                            "org.apache.catalina.startup.Catalina.start Server startup in"
                            in logs
                        ):
                            ready_by_logs = True

                        # Milestone tracking
                        latest_milestone = None
                        for title, marker in milestones:
                            if marker in logs:
                                if title not in reached_milestones:
                                    reached_milestones.add(title)
                                    if stream_status:
                                        UI.detail(f"[LDM] ⏳ Phase reached: {title}")
                                    else:
                                        UI.detail(
                                            f"Startup Milestone Reached: {UI.CYAN}{title}{UI.COLOR_OFF}"
                                        )
                                        spinner.update(f"Liferay Startup: {title}...")
                                latest_milestone = title

                        if status_file and latest_milestone:
                            try:
                                status_file.parent.mkdir(parents=True, exist_ok=True)
                                status_data = {
                                    "status": "starting",
                                    "latest_milestone": latest_milestone,
                                    "milestones_reached": list(reached_milestones),
                                    "elapsed_seconds": int(elapsed),
                                }
                                with open(status_file, "w") as f:
                                    import json

                                    json.dump(status_data, f, indent=2)
                            except Exception as e:
                                UI.detail(
                                    f"Warning writing milestone status tracking file: {e}"
                                )
                except Exception as e:
                    UI.detail(f"Warning checking log milestones: {e}")

                status = self.manager.run_command(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.State.Health.Status}}",
                        container_name,
                    ],
                    check=False,
                )

                if status == "healthy" or ready_by_logs:
                    if stream_status:
                        UI.success("[LDM] Liferay is healthy!")

                    # LDM-422: Proactive Search Reindex Monitoring (UX Win)
                    if (
                        str(project_meta.get("reindex_required", "false")).lower()
                        == "true"
                    ):
                        spinner.update("Search reindexing in progress...")
                        reindex_start = time.time()
                        reindex_timeout = 900  # 15 minutes max
                        found_start = False

                        while time.time() - reindex_start < reindex_timeout:
                            try:
                                # Fetch logs to catch the transition
                                reindex_logs = self.manager.run_command(
                                    ["docker", "logs", "--tail", "200", container_name],
                                    check=False,
                                    capture_output=True,
                                )

                                # Phase 1: Detect Start
                                if not found_start and (
                                    "reindexing all" in reindex_logs.lower()
                                ):
                                    spinner.update("Reindexing all search indexes...")
                                    found_start = True

                                # Phase 2: Detect Completion
                                if "reindexing all" in reindex_logs.lower() and (
                                    "completed in" in reindex_logs.lower()
                                    or "finished" in reindex_logs.lower()
                                ):
                                    break

                                # Fallback: Idle CPU check
                                if time.time() - reindex_start > 120:
                                    stats = self.manager.run_command(
                                        [
                                            "docker",
                                            "stats",
                                            "--no-stream",
                                            "--format",
                                            "{{.CPUPerc}}",
                                            container_name,
                                        ],
                                        check=False,
                                        capture_output=True,
                                    )
                                    if (
                                        stats
                                        and float(stats.strip().replace("%", "")) < 5.0
                                    ):
                                        break

                            except Exception as e:
                                UI.detail(f"Warning tracking search reindex: {e}")
                            time.sleep(5)

                        # Clear the flag so we don't wait on future boots
                        project_meta["reindex_required"] = "false"
                        root_path = self.manager.detect_project_path(
                            project_id=project_id, for_init=True
                        )
                        if root_path:
                            self.manager.write_meta(root_path, project_meta)
                    # If we bypassed by logs, wait a tiny bit to ensure the port is truly bound
                    if status != "healthy":
                        time.sleep(2)

                    ts = getattr(self.manager.args, "total_start", None)
                    duration_total = (
                        time.time() - float(ts) if ts else time.time() - start_time
                    )

                    duration_str = UI.format_duration(duration_total)

                    # Execute Headless API patcher for fragment overrides
                    root_path = self.manager.detect_project_path(
                        project_id=project_id, for_init=True
                    )
                    paths = self.manager.setup_paths(root_path)
                    self.manager.runtime.fragments._patch_fragment_overrides(
                        project_meta, paths
                    )

                    share_enabled = (
                        str(project_meta.get("share", "false")).lower() == "true"
                        or str(project_meta.get("expose", "false")).lower() == "true"
                        or getattr(self.manager.args, "share", False)
                    )
                    proxy_ports = self.manager.infra.get_proxy_ports()
                    active_ssl_port = proxy_ports["https"]

                    access_url = None
                    if share_enabled:
                        share_provider = (
                            project_meta.get("share_provider")
                            or getattr(self.manager.args, "share_provider", None)
                            or "lfr-tunnel"
                        )
                        share_subdomain = (
                            project_meta.get("share_subdomain")
                            or getattr(self.manager.args, "share_subdomain", None)
                            or project_meta.get("project_name")
                            or host_name
                        )
                        if share_provider in ["lfr-tunnel", "lfr-tunnel-docker"]:
                            access_url = self.manager.share.resolve_public_tunnel_url(
                                share_subdomain, project_meta.get("project_name")
                            )

                    if not access_url:
                        # Respect ssl property from args/metadata, defaulting to True if not localhost and ssl not explicitly disabled
                        ssl_arg = getattr(self.manager.args, "ssl", None)
                        use_ssl = (
                            ssl_arg if ssl_arg is not None else host_name != "localhost"
                        )
                        scheme = "https" if use_ssl else "http"

                        access_url = (
                            f"{scheme}://{host_name}"
                            if host_name != "localhost"
                            else f"http://localhost:{project_meta.get('port', 8080)}"
                        )
                        if (
                            host_name != "localhost"
                            and use_ssl
                            and active_ssl_port != 443
                        ):
                            access_url = f"https://{host_name}:{active_ssl_port}"
                        elif (
                            host_name != "localhost"
                            and not use_ssl
                            and project_meta.get("port", 8080) != 80
                        ):
                            access_url = (
                                f"http://{host_name}:{project_meta.get('port', 8080)}"
                            )

                    if UI.NON_INTERACTIVE:
                        UI.raw(f"✅  Liferay ready: {access_url}  ({duration_str})")
                    else:
                        UI.raw("")
                        UI.success(f"Liferay is ready  ({duration_str})")
                        UI.raw("")
                        UI.raw(f"  🌐  {UI.CYAN}{UI.BOLD}{access_url}{UI.COLOR_OFF}")
                        root_path_str = self.manager.detect_project_path(
                            project_id, for_init=True
                        )
                        if root_path_str:
                            UI.raw(f"  📁  {root_path_str}")
                        UI.raw("")
                        UI.raw(f"  Next:  ldm logs {project_id:<12}  View live logs")
                        UI.raw(f"         ldm snapshot {project_id:<8}  Take a backup")

                        # Show credentials hint for quickstart explicitly?
                        # Actually we can just always show it or check if it's quickstart.
                        # The plan said: For `ldm quickstart` specifically, also include credentials hint
                        # Since readiness doesn't know if it's quickstart, we will just show it if `project_meta.get("is_quickstart")` isn't available, or we just always show it. Let's just show it.
                        UI.raw("")
                        UI.raw(
                            "  👤  admin@liferay.com / test  (change after first login)"
                        )
                        UI.raw("")

                    is_legacy_expose = (
                        str(project_meta.get("expose", "false")).lower() == "true"
                        and str(project_meta.get("share", "false")).lower() != "true"
                    )
                    if is_legacy_expose:
                        self.manager.runtime.logs._print_ngrok_url(
                            project_meta.get("container_name")
                        )

                    if str(project_meta.get("share", "false")).lower() == "true":
                        share_subdomain = project_meta.get(
                            "share_subdomain"
                        ) or project_meta.get("project_name")
                        share_port = project_meta.get("port", 8080)
                        share_provider = (
                            project_meta.get("share_provider") or "lfr-tunnel"
                        )
                        self.manager.share.cmd_start(
                            project_id=project_meta.get("project_name"),
                            subdomain=share_subdomain,
                            ports=str(share_port),
                            provider=share_provider,
                            image=project_meta.get("share_image"),
                            inspector=str(
                                project_meta.get("share_inspector", "false")
                            ).lower()
                            == "true",
                        )

                    should_open_browser = (
                        browser
                        if browser is not None
                        else getattr(self.manager.args, "browser", False)
                    )
                    if should_open_browser:
                        UI.detail(f"Launching browser: {access_url}/web/guest/home")
                        open_browser(f"{access_url}/web/guest/home")
                    return True

                # Fail fast if container exited
                container_state = self.manager.get_container_status(container_name)
                if container_state == "exited":
                    UI.error(
                        f"Liferay container '{container_name}' exited unexpectedly."
                    )
                    return False

                time.sleep(5)  # Shorter sleep for more responsive status checks

        UI.error("\nTimed out waiting for Liferay to become healthy.")
        return False
