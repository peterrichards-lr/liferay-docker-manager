import contextlib
import os
import re
import time
from datetime import datetime
from typing import NamedTuple

from ldm_core.config import resolve_target_context
from ldm_core.docker_service import DockerService
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import resolve_access_url
from ldm_core.ui import UI
from ldm_core.utils import liferay_container_of, open_browser
from ldm_core.workspace.site_initializers import (
    deploy_pending as deploy_pending_site_initializers,
)


class Milestone(NamedTuple):
    """One recognisable phase of a Liferay boot (LDM-#2169).

    `markers` holds alternatives rather than one string because the log line
    that announces a phase differs between Liferay lines -- `OSGi run level`
    appears in some, `Starting initial bundles` in others -- and a single
    marker meant four of the five original milestones matched nothing at all
    on 2026.q3.7. Alternatives make the list additive: a marker that stops
    appearing costs nothing as long as a sibling still matches.

    `excludes` rejects a line that would otherwise match. It exists for one
    measured false positive, described on PORTAL_STARTING below.
    """

    title: str
    markers: tuple[str, ...]
    excludes: tuple[str, ...] = ()

    def matches(self, line: str) -> bool:
        if any(bad in line for bad in self.excludes):
            return False
        return any(marker in line for marker in self.markers)


#: The container entrypoint prefixes its own shell output with this. Liferay's
#: log lines never carry it, which is what makes it usable as a discriminator.
ENTRYPOINT_PREFIX = "[LIFERAY]"

#: Startup phases in the order they occur, so the reported breakdown reads as
#: a timeline. Verified against a captured 2026.q3.7 boot log; markers that did
#: not appear there are KEPT rather than removed, because they are what matches
#: on other Liferay lines and this list must not regress a version it was not
#: measured against.
STARTUP_MILESTONES: tuple[Milestone, ...] = (
    Milestone("Tomcat Initializing", ("Catalina.load Server initialization",)),
    Milestone(
        "Spring Web Context Initializing",
        ("Initializing Spring root WebApplicationContext",),
    ),
    Milestone("Database Schema Initializing", ("DBInitUtil", "DialectDetector")),
    Milestone(
        "OSGi Framework Starting", ("Starting initial bundles", "OSGi run level")
    ),
    Milestone("OSGi Framework Started", ("Started initial bundles",)),
    # The measured false positive. The entrypoint prints
    #   [LIFERAY] Starting Liferay DXP. To stop the container with CTRL-C...
    # 41 seconds before the portal prints
    #   Starting Liferay Digital Experience Platform 2026.Q3.7
    # so a bare "Starting Liferay" reported portal progress while the JVM was
    # still loading, and made the quiet period that followed look like a stall
    # after progress rather than the first phase.
    Milestone("Portal Startup Progress", ("Starting Liferay",), (ENTRYPOINT_PREFIX,)),
    # The largest single block of the boot that was measured -- 3m30s, 35% of
    # the total -- produced no output at all and matched no milestone. Naming
    # it is the difference between "LDM is stuck" and "Liferay is running
    # verify processes".
    Milestone("Verify Processes Running", ("Executing verify ",)),
    Milestone("Available Contexts Registered", ("Available contexts",)),
    Milestone("Tomcat Server Ready", ("Server startup in",)),
    Milestone("Site Initializer Running", ("BundleSiteInitializer",)),
)

#: Leading timestamps, stripped before an error line is compared with the ones
#: already seen. Four formats appear in a single boot log: the RFC3339 prefix
#: `docker logs -t` adds, Liferay's own `2026-10-13 14:09:38.328`, Tomcat's
#: `13-Oct-2026 14:07:05.573`, and the older bare `14:01:36,608`.
_TIMESTAMP_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?\s*"),
    re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s*"),
    re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}\s+\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s*"),
    re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d+\s*"),
)


def strip_log_timestamps(line: str) -> str:
    """A log line with every leading timestamp removed (LDM-#2169).

    The error counter deduped on the raw line, and every Liferay line begins
    with its own timestamp, so no two occurrences of the same error were ever
    equal: a single recurring error was announced as "1 new error(s)" on every
    30-second poll for the whole boot.

    Only LEADING timestamps are removed, and nothing else is normalised. A
    message that genuinely differs must still compare as different -- the
    counter's job is to report new errors, and over-normalising would trade a
    counter that cries wolf for one that stays silent.
    """
    text = line.strip()
    while True:
        for pattern in _TIMESTAMP_PATTERNS:
            stripped = pattern.sub("", text, count=1)
            if stripped != text:
                text = stripped
                break
        else:
            return text.strip()


def select_new_errors(error_lines: list[str], seen_errors: set[str]) -> list[str]:
    """The error lines not already reported, deduped by message (LDM-#2169).

    Extracted from the readiness loop so it is reachable without Docker. The
    inline version was never exercised by anything: a call site that exists is
    not a call site that runs (LDM-#2132).

    `seen_errors` is updated in place with the keys of what is returned, so a
    caller cannot report a batch and then forget to record it -- which is the
    shape the original had, with the update written separately from the
    comparison.

    Deduped within the batch as well as against previous polls, because
    `docker logs --tail 1000` returns the same recurring error many times in
    one read.
    """
    selected = []
    for line in error_lines:
        key = strip_log_timestamps(line)
        if key in seen_errors:
            continue
        seen_errors.add(key)
        selected.append(line)
    return selected


def log_window_args(last_poll_at: float | None, now: float) -> list[str]:
    """The `docker logs` window to read on this poll (LDM-#2169).

    `--tail 1000` was a sliding window, and `BundleSiteInitializer` emits
    hundreds of lines in a burst -- so between two polls the
    `Server startup in` line was pushed out of it and never seen. The E2E
    check caught exactly that: six phases recorded, properly spread, and
    `Tomcat Server Ready` missing while the marker that comes AFTER it was
    found. Recording the later marker and missing the earlier one is only
    possible if the window moved past it.

    A relative duration rather than a timestamp, deliberately: `--since 45s`
    is resolved by the daemon against its own clock, so a remote node whose
    clock differs from this host's cannot silently return the wrong window.

    The +5s overlap re-reads a few lines on purpose. Both consumers are
    idempotent -- a milestone map keyed by title and a sticky flag -- so
    re-reading costs nothing and a gap is not recoverable.
    """
    if last_poll_at is None:
        return ["--tail", "2000"]
    return ["--since", f"{max(int(now - last_poll_at) + 5, 5)}s"]


def find_milestones(logs: str, milestones=STARTUP_MILESTONES) -> dict[str, str]:
    """Each milestone reached in `logs`, mapped to the line that announced it.

    Matched line by line rather than against the whole blob, because the
    `excludes` rule cannot be expressed on a concatenation -- the entrypoint
    banner and the portal's own line both contain "Starting Liferay", and a
    substring test over the full log can only see that one of them is present.
    """
    found: dict[str, str] = {}
    for line in logs.splitlines():
        for milestone in milestones:
            if milestone.title not in found and milestone.matches(line):
                found[milestone.title] = line.strip()
    return found


def format_startup_phases(
    milestone_seconds: dict[str, float], total: float
) -> list[str]:
    """The boot as a timeline of phases, longest-pole visible.

    Reports the gap BETWEEN milestones, not the time at which each was
    reached: "Tomcat Server Ready at 5m30s" does not say where those 5m30s
    went, which is the only question being asked of it.
    """
    reached = [
        (m.title, milestone_seconds[m.title])
        for m in STARTUP_MILESTONES
        if m.title in milestone_seconds
    ]
    if not reached:
        return []

    trailing_label = "after the last milestone"
    rows: list[tuple[str, str, float | None]] = []
    previous = 0.0
    for title, at in reached:
        rows.append((title, UI.format_duration(max(at - previous, 0.0)), at))
        previous = at
    trailing = total - previous
    if trailing > 0:
        rows.append((trailing_label, UI.format_duration(trailing), None))

    title_width = max(len(title) for title, _, _ in rows)
    gap_width = max(len(gap) for _, gap, _ in rows)

    lines = ["Startup phases:"]
    for title, gap, reached_seconds in rows:
        reached_at = (
            f"  (at {UI.format_duration(reached_seconds)})"
            if reached_seconds is not None
            else ""
        )
        lines.append(
            f"  {title.ljust(title_width)}  +{gap.rjust(gap_width)}{reached_at}"
        )
    return lines


class ReadinessService(BaseHandler):
    """Readiness service for runtime operations."""

    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager

    def _derive_probe_url(self, meta, host_name, ssl_enabled, target_name, root):
        """The readiness URL when --probe-url is not given (LDM-#1223/#1568).

        Extracted unchanged in LDM-#1891 so the override is a branch rather
        than an edit to this logic -- the default path is what must not
        change, and keeping it whole is how that stays reviewable.
        """
        proxy_ports = None
        try:
            proxy_ports = self.manager.infra.get_proxy_ports()
        except Exception:
            pass

        # LDM-388 / Issue #1223: Resolve active compute target context to use target_node host for remote nodes
        target_ctx = resolve_target_context(
            explicit_target=target_name,
            meta=meta,
            project_root=root,
            pin=False,
        )
        effective_host = (
            target_ctx.target.host
            if (
                target_ctx
                and target_ctx.is_remote
                and target_ctx.target
                and target_ctx.target.host
            )
            else host_name
        )
        target_host = "127.0.0.1" if effective_host == "localhost" else effective_host
        # LDM-#1568: the probe dials whatever the shared resolver says serves,
        # so a green readiness check and the banner printed after it can never
        # name different URLs. `host_override` swaps the address dialled without
        # disturbing the SSL decision, which stays keyed on the project's own
        # host name.
        return resolve_access_url(
            host_name,
            meta,
            ssl_enabled,
            proxy_ports=proxy_ports,
            host_override=target_host,
        )

    def cmd_wait(  # noqa: C901, PLR0912, PLR0913, PLR0915
        self,
        project_id=None,
        timeout=None,
        wait_for_deployables=False,
        wait_for_bundles=None,
        stream_status=False,
        stream_logs=False,
        cpu_idle_threshold=None,
        cpu_idle_checks=None,
        fragment_patch_timeout=None,
        probe_url=None,
    ):
        """Block execution until project is fully ready (HTTP 200/302)."""
        if timeout is None:
            timeout = 900

        if cpu_idle_threshold is None:
            try:
                cpu_idle_threshold = float(os.getenv("LDM_CPU_IDLE_THRESHOLD", "15.0"))
            except ValueError:
                UI.warning("Malformed LDM_CPU_IDLE_THRESHOLD; falling back to 15.0")
                cpu_idle_threshold = 15.0

        if cpu_idle_checks is None:
            try:
                cpu_idle_checks = int(os.getenv("LDM_CPU_IDLE_CHECKS", "3"))
            except ValueError:
                UI.warning("Malformed LDM_CPU_IDLE_CHECKS; falling back to 3")
                cpu_idle_checks = 3

        root = self.manager.detect_project_path(project_id)
        if not root:
            return None
        meta = self.manager.read_meta(root)
        host_name = meta.get("host_name", "localhost")

        target_name = getattr(self.manager, "target", None) or meta.get("target")
        docker_prefix = DockerService.get_docker_cmd_prefix(target_name)

        overall_start = time.time()
        overall_timeout = timeout

        container_name = liferay_container_of(meta) or root.name

        log_proc = None
        if stream_logs:
            # Streams Liferay logs to stdout in background
            import subprocess
            import sys

            log_proc = subprocess.Popen(
                [*docker_prefix, "logs", "-f", container_name],
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
                [*docker_prefix, "logs", "--tail", "200", container_name],
                stdout=sys.stderr,
                stderr=sys.stderr,
                check=False,
            )
            UI.die(msg)

        # 1. Wait for Container/Log Readiness
        if not self._wait_for_ready(
            meta,
            host_name,
            timeout=overall_timeout,
            stream_status=stream_status,
            stream_logs=stream_logs,
            fragment_patch_timeout=fragment_patch_timeout,
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

        # Retrieve ports dynamically from the running proxy container if available
        # LDM-#1891: an explicit --probe-url replaces the derived one verbatim,
        # and nothing below runs. LDM cannot infer the right target in every
        # topology: with SSL on a remote node the certificate is issued for the
        # project's host name while the derived URL dials the node's address, so
        # the probe and the certificate disagree by construction. Rather than
        # teach `wait` about tunnels and hostnames it cannot see, let the caller
        # name the URL it knows is correct.
        #
        # Deliberately no rewriting of scheme, host or port: a caller that has
        # gone to the trouble of naming a URL means that URL. Everything below
        # -- the target context, the remote-host override from LDM-#1223 -- is
        # skipped rather than computed and discarded, because it feeds nothing
        # else in this method.
        if probe_url:
            url = probe_url
            UI.detail(f"Probing the URL supplied with --probe-url: {url}")
        else:
            url = self._derive_probe_url(
                meta, host_name, ssl_enabled, target_name, root
            )

        import requests

        http_ready = False
        while (time.time() - overall_start) < overall_timeout:
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
            container_name = liferay_container_of(meta) or root.name

            # Wait for deploy directory inside container to clear
            UI.detail("Checking deploy directory queue status...")
            deploy_clear = False
            while (time.time() - overall_start) < overall_timeout:
                try:
                    res = DockerService.exec(
                        container_name,
                        ["ls", "/opt/liferay/deploy"],
                        check=False,
                        target_name=target_name,
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
            while (time.time() - overall_start) < overall_timeout:
                try:
                    # LDM-#1242: must go through DockerService.gogo(), which
                    # holds the telnet pipe open long enough for Gogo to write
                    # its reply. The previous `echo 'lb -s' | telnet` form
                    # returned only telnet's connection banner (157 bytes, no
                    # bundle table), so the `"|" in res` test below never
                    # matched and the `elif res: break` path fired on the very
                    # first iteration -- meaning bundle and Client Extension
                    # state was never actually verified on any run.
                    res, gogo_err = DockerService.gogo(
                        container_name,
                        "lb -s",
                        target_name=target_name,
                    )
                    if gogo_err:
                        UI.debug(f"Gogo rejected 'lb -s': {gogo_err}")
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
        consecutive_required = cpu_idle_checks
        cpu_threshold = (
            cpu_idle_threshold  # Consider < threshold CPU to be "idle" for Liferay
        )

        while (time.time() - overall_start) < overall_timeout:
            try:
                result = self.manager.run_command(
                    [
                        *docker_prefix,
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{.CPUPerc}}",
                        liferay_container_of(meta),
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

    def _report_oom_kill(
        self, container_name: str, target_name: str | None = None
    ) -> bool:
        """Say so when the kernel OOM-killed this container. LDM-#1773.

        An out-of-memory kill was being reported as a health-check timeout:
        LDM printed "Timed out waiting for Liferay to become healthy" after
        eight minutes while `docker inspect` said `OOMKilled: true` and the log
        said `Killed  start_liferay.sh`. Those are very different problems --
        one says "wait longer or check the application", the other says "this
        machine does not have enough memory" -- and the operator was being
        pointed at the wrong one.

        Neither of the two ways the wait ends could report it. The JVM is
        killed while the entrypoint survives, so the container stays up, never
        goes healthy, and falls out of the loop as a timeout; and when the
        entrypoint does die too, `exited unexpectedly` is equally silent about
        the reason. Both call this.

        Returns True when it reported an OOM, so the caller can suppress its
        own, less accurate message. A container that cannot be inspected
        returns False -- "could not ask" must not become "not an OOM" in the
        message, but there is nothing accurate to say instead, so the caller's
        original wording stands.
        """
        try:
            from ldm_core.docker_service import DockerService

            killed = DockerService.container_was_oom_killed(
                container_name, target_name=target_name
            )
        except Exception:
            return False

        if not killed:
            return False

        UI.error(
            "\nLiferay was killed by the kernel for running out of memory "
            "-- it did not simply take too long."
        )
        UI.detail(
            "Docker reports OOMKilled on this container. Waiting longer will "
            "not help; the container needs more memory than this machine is "
            "giving it."
        )
        UI.detail(
            "Raise Docker's memory allocation, or reduce what the stack asks "
            "for -- an imported package can pin an embedded Elasticsearch "
            "alongside the shared one, which doubles the heap ceiling "
            "(LDM-#1773)."
        )
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
        fragment_patch_timeout=None,
    ):
        """Wait for Liferay to become healthy and provide access information."""
        # LDM-#1564: must be the name compose CREATED, not the verbatim
        # project id -- see liferay_container_of.
        container_name = liferay_container_of(project_meta)
        project_id = project_meta.get("project_name") or container_name
        target_name = getattr(self.manager, "target", None) or project_meta.get(
            "target"
        )
        docker_prefix = DockerService.get_docker_cmd_prefix(target_name)
        root_path = (
            self.manager.detect_project_path(project_id, for_init=True)
            if project_id
            else None
        )
        status_file = (
            root_path / ".liferay-docker" / "startup-status.json" if root_path else None
        )

        # LDM-#2169: the elapsed time at which each milestone was FIRST seen.
        # This used to be a set, and the status file carried a single
        # `elapsed_seconds` for the latest milestone, overwritten on every
        # poll -- so by the time a boot finished, every intermediate timing
        # had been discarded and "ready in 9m57s" could not be decomposed.
        reached_milestones: dict[str, float] = {}

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
            seen_errors: set[str] = set()
            # LDM-#2169: both outlive one poll on purpose. The log window is
            # now incremental, so a marker seen in an earlier window must stay
            # seen -- recomputing either from the current window is what let a
            # busy boot lose `Server startup in` entirely.
            last_log_poll_at: float | None = None
            seen_server_startup = False
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
                            [*docker_prefix, "logs", "--tail", "1000", container_name],
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

                            # LDM-#2169: compared with the leading timestamp
                            # removed. `seen_errors` held raw lines, and every
                            # Liferay line starts with its own timestamp, so
                            # two occurrences of one error were never equal and
                            # a single recurring error was reported as new on
                            # every 30-second poll for the whole boot.
                            new_error_lines = select_new_errors(
                                error_lines, seen_errors
                            )

                            if new_error_lines:
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
                now_poll = time.time()
                log_window = log_window_args(last_log_poll_at, now_poll)
                try:
                    logs = self.manager.run_command(
                        [*docker_prefix, "logs", *log_window, container_name],
                        check=False,
                        capture_output=True,
                    )
                    last_log_poll_at = now_poll
                    if logs:
                        if (
                            "org.apache.catalina.startup.Catalina.start Server startup in"
                            in logs
                        ):
                            # Sticky. Previously recomputed from each window,
                            # so once the line scrolled out of `--tail 1000`
                            # this flipped back to False and the readiness
                            # shortcut silently stopped working too.
                            seen_server_startup = True

                        # Milestone tracking
                        latest_milestone = None
                        for title in find_milestones(logs):
                            if title not in reached_milestones:
                                reached_milestones[title] = elapsed
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
                                    # LDM-#2169: kept per milestone, so a slow
                                    # boot can be decomposed after the fact
                                    # without re-running it.
                                    "milestone_seconds": {
                                        title: round(at, 1)
                                        for title, at in reached_milestones.items()
                                    },
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

                # Derived from the sticky flag, so a window that happens not to
                # contain the line cannot un-ready a portal that already
                # started (LDM-#2169).
                ready_by_logs = seen_server_startup

                status = self.manager.run_command(
                    [
                        *docker_prefix,
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
                        reindex_timeout = 180  # 3 minutes max cap
                        found_start = False

                        while time.time() - reindex_start < reindex_timeout:
                            try:
                                # Fetch logs to catch the transition
                                reindex_logs = self.manager.run_command(
                                    [
                                        *docker_prefix,
                                        "logs",
                                        "--tail",
                                        "500",
                                        container_name,
                                    ],
                                    check=False,
                                    capture_output=True,
                                )

                                if reindex_logs:
                                    logs_lower = reindex_logs.lower()
                                    # Phase 1: Detect Start
                                    if not found_start and (
                                        "reindexing" in logs_lower
                                        or "reindex" in logs_lower
                                    ):
                                        spinner.update(
                                            "Reindexing all search indexes..."
                                        )
                                        found_start = True

                                    # Phase 2: Detect Completion
                                    completion_keywords = [
                                        "completed in",
                                        "reindex completed",
                                        "reindexing completed",
                                        "reindex finished",
                                        "reindexing finished",
                                        "finished reindexing",
                                    ]
                                    if any(
                                        kw in logs_lower for kw in completion_keywords
                                    ):
                                        break

                                # Fallback: Idle CPU check after 45s
                                if time.time() - reindex_start > 45:
                                    stats = self.manager.run_command(
                                        [
                                            *docker_prefix,
                                            "stats",
                                            "--no-stream",
                                            "--format",
                                            "{{.CPUPerc}}",
                                            container_name,
                                        ],
                                        check=False,
                                        capture_output=True,
                                    )
                                    if stats:
                                        import re

                                        clean_stats = re.sub(
                                            r"[^\d.]", "", stats.strip()
                                        )
                                        if clean_stats:
                                            try:
                                                if float(clean_stats) < 10.0:
                                                    break
                                            except ValueError:
                                                pass

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

                    if hasattr(spinner, "stop") and callable(
                        getattr(spinner, "stop", None)
                    ):
                        try:
                            spinner.stop()
                        except Exception:
                            pass

                    # Execute Headless API patcher for fragment overrides
                    root_path = self.manager.detect_project_path(
                        project_id=project_id, for_init=True
                    )
                    paths = self.manager.setup_paths(root_path)

                    # LDM-#1779: a site-initializer client extension held back
                    # by `ldm import` is deployed here, now that the portal is
                    # healthy. Deliberately BEFORE the fragment-override
                    # patcher, which waits on the Site Initializer having
                    # populated the page specifications it patches.
                    if root_path:
                        deploy_pending_site_initializers(
                            self.manager, root_path, paths, project_meta
                        )

                    self.manager.runtime.fragments._patch_fragment_overrides(
                        project_meta, paths, timeout=fragment_patch_timeout
                    )

                    share_enabled = (
                        str(project_meta.get("share", "false")).lower() == "true"
                        or str(project_meta.get("expose", "false")).lower() == "true"
                        or getattr(self.manager.args, "share", False)
                    )
                    proxy_ports = None
                    try:
                        proxy_ports = self.manager.infra.get_proxy_ports()
                    except Exception:
                        pass

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
                        # LDM-#1568: this block used to claim it "respects the
                        # ssl property from args/metadata" while reading neither
                        # -- it took `args.ssl`, and with no --ssl flag fell back
                        # to `host_name != "localhost"`. For a package declaring
                        # a custom domain with `ssl` "false" that guess prints
                        # https:// for a project served over http on :8080. The
                        # scheme now comes from the same `_is_ssl_active` rule
                        # `ldm info` uses, through the shared resolver.
                        access_url = resolve_access_url(
                            host_name,
                            project_meta,
                            self.manager.composer._is_ssl_active(
                                host_name, project_meta
                            ),
                            proxy_ports=proxy_ports,
                        )

                    # LDM-#2169: printed under --benchmark rather than always,
                    # so the default output is unchanged and no new CLI flag is
                    # introduced (which would mean a man page and CLI reference
                    # update to stay out of check-cli-drift). The timings are
                    # persisted to startup-status.json on every boot either
                    # way, so a slow start can still be decomposed afterwards.
                    from ldm_core.utils import Benchmarker

                    if Benchmarker.is_active:
                        for phase_line in format_startup_phases(
                            reached_milestones, duration_total
                        ):
                            # UI.raw, NOT UI.detail. `detail` prints only under
                            # `--info`/`--verbose` (LDM-#1036), so the first
                            # shipped version of this computed the breakdown
                            # correctly and printed it at a verbosity nobody
                            # running `--benchmark` has. Measured on Windows
                            # against v2.26.6-pre.2: the Benchmark Report
                            # appeared and the phases did not.
                            #
                            # LDM-#1790 fixed exactly this for the pin
                            # announcement, and `test_compatibility_claim.py`
                            # carries a named test for it on another message.
                            # The user has explicitly asked for this output by
                            # passing the flag; hiding it behind a second flag
                            # is never what they meant.
                            UI.raw(phase_line)

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

                        from ldm_core.constants import (
                            DEFAULT_ADMIN_EMAIL,
                            DEFAULT_ADMIN_PASSWORD,
                        )

                        credentials = project_meta.get("credentials", [])

                        # Fallback for backwards compatibility
                        if not credentials:
                            credentials.append(
                                {
                                    "type": "admin",
                                    "email": project_meta.get(
                                        "admin_email", DEFAULT_ADMIN_EMAIL
                                    ),
                                    "password": DEFAULT_ADMIN_PASSWORD,
                                }
                            )

                        for cred in credentials:
                            ident = cred.get("email") or cred.get("username", "Unknown")
                            cred_type = cred.get("type", "user")
                            desc = cred.get("description", "")

                            # LDM-#1161: this used to interpolate the real
                            # password wrapped in UI.HIDDEN (the ANSI
                            # "conceal" escape, \033[8m), relying on the
                            # terminal to render it invisibly. Many common
                            # terminals (iTerm2, Terminal.app, VS Code,
                            # Windows Terminal) don't implement conceal at
                            # all and just print the raw text -- so the
                            # password was shown in clear text precisely
                            # where users would least expect it. UI.HIDDEN
                            # was also hardcoded outside the NO_COLOR/
                            # non-interactive handling every other color
                            # goes through, so a piped/CI log could capture
                            # the raw escape sequence around a fully visible
                            # password too. A fixed-width placeholder never
                            # carries the real value into the rendered
                            # output at all, so there's nothing for any
                            # terminal to fail to conceal.
                            mask = (
                                "********"
                                if getattr(UI, "NO_UNICODE", False)
                                else "••••••••"
                            )
                            UI.raw(
                                f"  👤  [{cred_type.upper()}] {ident} / [ {UI.DIM}{mask}{UI.COLOR_OFF} ]"
                            )
                            if desc:
                                UI.raw(f"      {desc}")

                        # LDM-#1508: name the project. Without it the tip
                        # sends the user into the project-selection prompt,
                        # and the generated name (ldm-1788250086) has to be
                        # scrolled back for and retyped. project_id is the
                        # same value the `Next:` hints above already use.
                        UI.raw(
                            f"      {UI.DIM}(password masked -- use "
                            f"'ldm info {project_id} --credentials' to reveal it)"
                            f"{UI.COLOR_OFF}"
                        )
                        UI.raw("")

                        # Print legacy banner notes if any (just in case they were using it for other things)
                        banner_notes = project_meta.get("banner_notes")
                        if banner_notes and isinstance(banner_notes, list):
                            for line in banner_notes:
                                UI.raw(f"  📝  {line}")
                            UI.raw("")

                    is_legacy_expose = (
                        str(project_meta.get("expose", "false")).lower() == "true"
                        and str(project_meta.get("share", "false")).lower() != "true"
                    )
                    if is_legacy_expose:
                        self.manager.runtime.logs._print_ngrok_url(
                            project_meta.get("container_name"), target_name=target_name
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
                container_state = self.manager.get_container_status(
                    container_name, target_name=target_name
                )
                if container_state == "exited":
                    if self._report_oom_kill(container_name, target_name):
                        return False
                    UI.error(
                        f"Liferay container '{container_name}' exited unexpectedly."
                    )
                    return False

                time.sleep(5)  # Shorter sleep for more responsive status checks

        if self._report_oom_kill(container_name, target_name):
            return False

        UI.error("\nTimed out waiting for Liferay to become healthy.")
        return False
