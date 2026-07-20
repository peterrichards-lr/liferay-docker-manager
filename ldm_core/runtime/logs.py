import json
import os
import shutil
import sys
import time

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import (
    get_compose_cmd,
    strip_ansi,
)


class LogsService(BaseHandler):
    """Logs service for runtime operations."""

    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager

    def cmd_logs(  # noqa: C901, PLR0912, PLR0913, PLR0915
        self,
        project_id=None,
        service=None,
        all_projects=False,
        infra=False,
        follow=False,
        no_wait=False,
        tail="100",
        timestamps=False,
        since=None,
        until=None,
        instance=None,
        grep=None,
        grep_i=False,
        grep_v=False,
        level=None,
        export=False,
        include_infra=False,
    ):
        """Shows logs for a project or global infrastructure."""
        if include_infra and export and not infra:
            UI.info("Including infrastructure logs in export...")
            self.cmd_logs(
                project_id=project_id,
                service=service,
                all_projects=all_projects,
                infra=True,
                follow=follow,
                no_wait=no_wait,
                tail=tail,
                timestamps=timestamps,
                since=since,
                until=until,
                instance=instance,
                grep=grep,
                grep_i=grep_i,
                grep_v=grep_v,
                level=level,
                export=True,
                include_infra=False,
            )

        if instance is not None:
            self._cmd_logs_instance(
                project_id=project_id,
                service=service,
                instance=instance,
                follow=follow,
                tail=tail,
                timestamps=timestamps,
                since=since,
                until=until,
                grep=grep,
                grep_i=grep_i,
                grep_v=grep_v,
                level=level,
                export=export,
            )
            return

        if infra:
            UI.info("Showing infrastructure logs...")
            containers = []
            if not service or "proxy" in service:
                containers.append("liferay-proxy-global")
            if not service or "es" in service:
                containers.append("liferay-search-global")

            for container in containers:
                self.manager.run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{container}$"]
                )

            infra_compose = self.manager.get_resource_path("infra-compose.yml")
            if not infra_compose:
                UI.die("Infrastructure compose file 'infra-compose.yml' not found.")

            cmd = [*get_compose_cmd(), "-f", str(infra_compose), "logs"]
            if follow:
                cmd.append("-f")

            if tail:
                cmd.extend(["--tail", str(tail)])

            if timestamps:
                cmd.append("-t")

            if since:
                cmd.extend(["--since", str(since)])

            if until:
                cmd.extend(["--until", str(until)])

            env = self.manager.infra._get_infra_env()
            self._run_log_command(
                cmd,
                env=env,
                grep=grep,
                grep_i=grep_i,
                grep_v=grep_v,
                level=level,
                follow=follow,
                export=export,
                export_prefix="infra",
            )
        else:
            targets = []
            if all_projects:
                targets = [r["path"] for r in self.manager.find_dxp_roots()]
            else:
                root = self.manager.detect_project_path(project_id)
                if root:
                    targets = [root]

            if not targets:
                UI.info("No running projects found to show logs.")
                return

            for root in targets:
                if self.manager.verbose:
                    UI.debug(f"Processing logs for project: {root.name} in {root}")

                meta = self.manager.read_meta(root)
                meta.get("container_name") or root.name
                target_service = (
                    service if service and not isinstance(service, list) else "liferay"
                )

                # LDM-381: Resolve the actual container name using labels
                actual_container = self.manager.resolve_container(
                    root.name, target_service
                )

                # Check if it exists
                check_cmd = [
                    "docker",
                    "ps",
                    "-a",
                    "-q",
                    "-f",
                    f"name=^{actual_container}$",
                ]
                if not self.manager.run_command(check_cmd, check=False):
                    if no_wait:
                        UI.error(
                            f"Service '{target_service}' in project '{root.name}' does not exist. Skipping."
                        )
                        continue

                    UI.info(
                        f"Waiting for container {UI.CYAN}{root.name}{UI.COLOR_OFF} (service: {target_service})..."
                    )
                    start_wait = time.time()
                    found = False
                    while time.time() - start_wait < 60:
                        elapsed = int(time.time() - start_wait)
                        if elapsed > 0 and elapsed % 10 == 0:
                            UI.info(
                                f"  ... still waiting for '{root.name}' ({elapsed}s)"
                            )

                        # Re-resolve in case it was created during wait
                        actual_container = self.manager.resolve_container(
                            root.name, target_service
                        )
                        if self.manager.run_command(check_cmd, check=False):
                            found = True
                            break
                        time.sleep(2)

                    if not found:
                        UI.error(f"Container '{root.name}' did not appear within 60s.")
                        continue

                if follow:
                    log_dir = root / "logs"
                    if not log_dir.exists():
                        if no_wait:
                            UI.error(
                                f"Logs directory missing in {root.name}. Skipping."
                            )
                            continue

                        UI.info(f"Waiting for logs directory in {root.name}...")
                        start_wait = time.time()
                        while not log_dir.exists() and time.time() - start_wait < 30:
                            time.sleep(1)

                cmd = [*get_compose_cmd(), "logs"]
                if follow:
                    cmd.append("-f")

                if tail:
                    cmd.extend(["--tail", str(tail)])

                if timestamps:
                    cmd.append("-t")

                if since:
                    cmd.extend(["--since", str(since)])

                if until:
                    cmd.extend(["--until", str(until)])

                if service:
                    if isinstance(service, list):
                        cmd.extend(service)
                    else:
                        cmd.append(service)
                self._run_log_command(
                    cmd,
                    cwd=str(root),
                    grep=grep,
                    grep_i=grep_i,
                    grep_v=grep_v,
                    level=level,
                    follow=follow,
                    export=export,
                    export_prefix=f"{root.name}-{actual_container}",
                )

    def _cmd_logs_instance(  # noqa: PLR0913
        self,
        project_id=None,
        service=None,
        instance=1,
        follow=False,
        tail="100",
        timestamps=False,
        since=None,
        until=None,
        grep=None,
        grep_i=False,
        grep_v=False,
        level=None,
        export=False,
    ):
        """Stream logs from a single scaled replica via 'docker logs'.

        Container name is resolved using the pattern stored in project metadata
        (written by cmd_scale). Falls back to the Docker Compose v2 naming
        convention: {project}-{service}-{index}.
        """
        root = self.manager.detect_project_path(project_id)
        if not root:
            UI.die("Project not found.")

        meta = self.manager.read_meta(root)
        from ldm_core.utils import sanitize_id

        project_name = sanitize_id(meta.get("container_name") or root.name)

        # Default service to 'liferay' when not specified
        svc = (
            service[0]
            if isinstance(service, list) and service
            else (service or "liferay")
        )

        # Validate instance index against stored scale
        scale_key = f"scale_{svc}"
        max_instances = int(meta.get(scale_key, 1))
        if instance < 1 or instance > max_instances:
            if max_instances == 1:
                UI.error(
                    f"Service '{svc}' is not scaled (only 1 instance). "
                    f"Use 'ldm logs' without --instance to view its logs."
                )
            else:
                UI.error(
                    f"Invalid instance index {instance} for service '{svc}'. "
                    f"Valid range: 1–{max_instances} (current scale={max_instances})."
                )
            return

        # Fast path: use pattern stored in metadata by cmd_scale
        pattern_key = f"container_name_pattern_{svc}"
        pattern = meta.get(pattern_key)
        if pattern:
            container_name = pattern.replace("{index}", str(instance))
        else:
            # Fallback: Docker Compose v2 standard naming convention
            container_name = f"{project_name}-{svc}-{instance}"

        # Confirm the container exists
        check = self.manager.run_command(
            ["docker", "ps", "-a", "-q", "-f", f"name=^{container_name}$"],
            check=False,
        )
        if not check:
            UI.error(
                f"Container '{container_name}' not found. "
                f"Is '{project_name}' running with {max_instances} replica(s)?"
            )
            return

        UI.info(
            f"Streaming logs for {UI.CYAN}{container_name}{UI.COLOR_OFF} "
            f"(instance {instance} of {max_instances})..."
        )

        cmd = ["docker", "logs"]
        if follow:
            cmd.append("-f")
        if tail:
            cmd.extend(["--tail", str(tail)])
        if timestamps:
            cmd.append("-t")
        if since:
            cmd.extend(["--since", str(since)])
        if until:
            cmd.extend(["--until", str(until)])
        cmd.append(container_name)

        self._run_log_command(
            cmd,
            cwd=str(root),
            grep=grep,
            grep_i=grep_i,
            grep_v=grep_v,
            level=level,
            follow=follow,
            export=export,
            export_prefix=f"{root.name}-{container_name}",
        )

    def _run_log_command(  # noqa: C901, PLR0912, PLR0913, PLR0915
        self,
        cmd,
        env=None,
        cwd=None,
        grep=None,
        grep_i=False,
        grep_v=False,
        level=None,
        follow=False,
        export=False,
        export_prefix="logs",
    ):
        """Runs the log command, streaming, filtering, and performing troubleshooting diagnostics."""
        if not grep and not level and not follow and not export:
            self.manager.run_command(
                cmd, env=env, cwd=cwd, capture_output=False, check=False
            )
            return

        import re
        import subprocess

        from ldm_core.utils import check_troubleshooting_signatures

        seen_troubleshooting = set()

        # Build regex pattern if grep is specified
        pattern = None
        if grep:
            flags = re.IGNORECASE if grep_i else 0
            try:
                pattern = re.compile(grep, flags)
            except re.error as e:
                UI.die(f"Invalid grep regular expression: {e}")

        # Severity level configuration
        SEVERITY_LEVELS = {
            "DEBUG": 10,
            "INFO": 20,
            "WARN": 30,
            "WARNING": 30,
            "ERROR": 40,
            "FATAL": 50,
        }

        target_severity = None
        if level:
            norm_level = level.upper()
            target_severity = SEVERITY_LEVELS.get(norm_level)
            if target_severity is None:
                UI.die(f"Invalid log level: {level}")

        LEVEL_PATTERNS = {
            "FATAL": re.compile(r"\bFATAL\b|\[FATAL\]"),
            "ERROR": re.compile(r"\bERROR\b|\[ERROR\]"),
            "WARN": re.compile(r"\bWARN(?:ING)?\b|\[WARN(?:ING)?\]"),
            "INFO": re.compile(r"\bINFO\b|\[INFO\]"),
            "DEBUG": re.compile(r"\bDEBUG\b|\[DEBUG\]"),
        }

        def get_line_level(line):
            for lvl in ["FATAL", "ERROR", "WARN", "INFO", "DEBUG"]:
                if LEVEL_PATTERNS[lvl].search(line):
                    return lvl
            return None

        # Resolve path to command executable (Bandit B607)
        if isinstance(cmd, list) and len(cmd) > 0:
            executable = shutil.which(cmd[0])
            if executable:
                cmd[0] = executable

        display_cmd = UI.redact(" ".join(cmd) if isinstance(cmd, list) else cmd)
        if self.manager.verbose:
            UI.debug(f"Executing log command: {display_cmd}")

        if getattr(self.manager, "dry_run", False):
            UI.info(
                f"{UI.BYELLOW}[DRY RUN] Would execute log command:{UI.COLOR_OFF} {display_cmd}"
            )
            return

        run_env = os.environ.copy() if env is None else env.copy()
        run_env["DOCKER_CLI_HINTS"] = "false"
        if "DOCKER_API_VERSION" not in run_env:
            run_env["DOCKER_API_VERSION"] = "1.44"

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                env=run_env,
                cwd=cwd,
                bufsize=1,
            )

            # Default print_subsequent state.
            # If level filtering is active, default to False to hide startup noise.
            print_subsequent = level is None
            export_file = None

            try:
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        stripped_line = line.rstrip("\r\n")
                        clean_line = strip_ansi(stripped_line)

                        # 1. Level Filter evaluation
                        if target_severity is not None:
                            line_level = get_line_level(clean_line)
                            if line_level is not None:
                                level_severity = SEVERITY_LEVELS[line_level]
                                print_subsequent = level_severity >= target_severity
                                match_level = print_subsequent
                            else:
                                match_level = print_subsequent
                        else:
                            match_level = True

                        # 2. Grep Filter evaluation
                        if match_level:
                            if pattern is not None:
                                match_grep = bool(pattern.search(clean_line))
                                if grep_v:
                                    match_grep = not match_grep
                            else:
                                match_grep = True
                        else:
                            match_grep = False

                        advice = (
                            check_troubleshooting_signatures(clean_line)
                            if follow
                            else None
                        )
                        if advice and advice not in seen_troubleshooting:
                            seen_troubleshooting.add(advice)
                            print(
                                f"\n{UI.BYELLOW}⚠️  LDM TROUBLESHOOTING ADVICE:{UI.COLOR_OFF}"
                            )
                            print(f"👉 {UI.BWHITE}{advice}{UI.COLOR_OFF}\n")
                            sys.stdout.flush()

                        if match_grep:
                            if export:
                                if export_file is None:
                                    from datetime import datetime

                                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                                    export_filename = f"{export_prefix}_{ts}.log"
                                    export_file = open(  # noqa: SIM115
                                        export_filename, "w", encoding="utf-8"
                                    )
                                    UI.info(
                                        f"Exporting logs to: {UI.CYAN}{export_filename}{UI.COLOR_OFF}"
                                    )
                                export_file.write(stripped_line + "\n")
                            else:
                                print(stripped_line)
                                sys.stdout.flush()
            finally:
                if export_file is not None:
                    export_file.close()
                if process.stdout:
                    process.stdout.close()
            process.wait()
        except KeyboardInterrupt:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

    def _print_ngrok_url(self, project_id):
        """Fetches and prints the public ngrok URL from the running container."""

        from ldm_core.ui import UI

        container_name = f"{project_id}-ngrok-1"
        try:
            result = self.manager.run_command(
                [
                    "docker",
                    "exec",
                    container_name,
                    "curl",
                    "-s",
                    "http://localhost:4040/api/tunnels",
                ],
                capture_output=True,
                check=False,
            )
            if result:
                data = json.loads(result)
                for tunnel in data.get("tunnels", []):
                    if tunnel.get("public_url", "").startswith("https://"):
                        public_url = tunnel["public_url"]
                        UI.success(
                            f"🌍 Public ngrok Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
                        )
                        return
        except Exception as e:
            UI.debug(f"Could not retrieve ngrok public URL: {e}")
        UI.warning("ngrok container is running, but failed to retrieve public URL.")
