import re
import time

from ldm_core.config import get_active_target
from ldm_core.utils import is_local_host, run_command

# LDM-#1805: how long to let the daemon settle before calling a `docker stop`
# that reported success a failure. `docker stop` defaults to a 10s SIGTERM
# grace before SIGKILL, so the ceiling sits just above it -- a container still
# listed after that has not merely been slow to be reaped.
STOP_SETTLE_TIMEOUT = 15.0
STOP_SETTLE_INTERVAL = 0.5


class DockerService:
    """
    Unified service for executing Docker CLI commands.
    Centralizes error handling, string formatting, context resolution, and raw process execution.
    """

    @staticmethod
    def get_docker_cmd_prefix(target_name: str | None = None) -> list[str]:
        """Returns the docker CLI command prefix, injecting --context for remote targets.

        Always resolves via `get_active_target()`, even when `target_name` is
        None/falsy -- this used to short-circuit straight to `["docker"]`
        before ever calling `get_active_target()`, which silently ignored a
        persisted default target (`ldm target use`) for every caller that
        didn't have an explicit target name in hand (most call sites that
        only know a possibly-unset project/CLI target). See
        docs/explanation/remote-node-architecture.md for the broader pattern
        this belongs to.
        """
        target = get_active_target(target_name)
        if target.name != "local" and not is_local_host(target.host):
            return ["docker", "--context", target.name]
        return ["docker"]

    @staticmethod
    def get_context_endpoint(context_name: str) -> str | None:
        """Returns the raw endpoint URL a Docker context dials, or None.

        The one place that asks Docker. `get_context_endpoint_host` and
        `get_context_endpoint_user` both parse what this returns, so a context
        is inspected the same way for both halves of the endpoint.
        """
        res = run_command(
            [
                "docker",
                "context",
                "inspect",
                context_name,
                "--format",
                "{{.Endpoints.docker.Host}}",
            ],
            check=False,
            capture_output=True,
            timeout=15,
        )
        if not res:
            return None

        endpoint = res.strip()
        return endpoint or None

    @staticmethod
    def get_context_endpoint_user(context_name: str) -> str | None:
        """Returns the SSH user a Docker context dials as.

        LDM-#1797: the user is stored twice -- `~/.ldmrc` holds `user`, the
        Docker context holds `ssh://<user>@<host>` -- and nothing keeps them in
        step. Measured on a real node: the context said `ldm-automation@...`
        while `~/.ldmrc` said `ec2-user`, so every context command failed with
        `Permission denied (publickey)` naming a user nobody had configured,
        while `ldm target ls` showed a correct configuration.

        Three outcomes, deliberately distinct:

        - `None`  -- nothing to compare. The context is missing or unreadable,
          or its endpoint is not SSH at all (a `unix://` context dials no user).
        - `""`    -- an SSH endpoint with no `user@`. That is not "unknown": SSH
          then falls back to the *local* username, which is itself a drift from
          a configured `user` and must be reportable.
        - a name  -- the user the context actually dials as.
        """
        endpoint = DockerService.get_context_endpoint(context_name)
        if not endpoint:
            return None

        scheme, separator, remainder = endpoint.partition("://")
        if not separator or scheme.lower() != "ssh":
            return None

        # An IPv6 literal contains colons but never an "@", so splitting on the
        # last "@" cannot be confused by the address.
        if "@" not in remainder:
            return ""
        return remainder.rsplit("@", 1)[0]

    @staticmethod
    def get_context_endpoint_host(context_name: str) -> str | None:
        """Returns the host a Docker context dials, or None if it has none.

        LDM-#1346: a context's endpoint is stored by Docker, not by LDM, so it
        can disagree with the `host` recorded in `~/.ldmrc` -- and when it does,
        LDM reports the stored host while dialling the context's. Reading it
        back is what makes that disagreement visible instead of silent.
        """
        endpoint = DockerService.get_context_endpoint(context_name)
        if not endpoint:
            return None

        # ssh://user@host:port -- strip scheme, any credentials, and any port.
        without_scheme = endpoint.split("://", 1)[-1]
        host = without_scheme.rsplit("@", 1)[-1]
        # An IPv6 literal is bracketed, so the port is whatever follows the
        # closing bracket -- splitting the whole string on ":" would truncate
        # the address itself. A bare host splits on its first colon.
        if host.startswith("["):
            closing = host.find("]")
            return host[: closing + 1] if closing != -1 else host
        return host.split(":", 1)[0] or None

    @staticmethod
    def get_compose_cmd_prefix(target_name: str | None = None) -> list[str]:
        """Returns the docker compose CLI command prefix for target execution."""
        prefix = DockerService.get_docker_cmd_prefix(target_name)
        return [*prefix, "compose"]

    @staticmethod
    def exists(container_name: str, target_name: str | None = None) -> bool:
        """Checks if a container exists (running or stopped)."""
        # Note: Using regex boundary ^...$ to avoid partial matches
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "ps",
            "-a",
            "-q",
            "-f",
            f"name=^{container_name}$",
        ]
        res = run_command(cmd, check=False)
        return bool(res and res.strip())

    @staticmethod
    def is_running(container_name: str, target_name: str | None = None) -> bool:
        """Checks if a container is currently running."""
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "ps",
            "-q",
            "-f",
            f"name=^{container_name}$",
        ]
        res = run_command(cmd, check=False)
        return bool(res and res.strip())

    @staticmethod
    def wait_until_stopped(
        container_name: str,
        target_name: str | None = None,
        timeout: float | None = None,
        interval: float | None = None,
    ) -> bool:
        """Waits for a container to stop being listed as running.

        LDM-#1805. `docker stop` is synchronous and succeeds, but `docker ps`
        can still list the container for a moment afterwards. Reading the state
        back with no tolerance for that turns a successful stop into a refusal.

        Measured on a real failure (v2.22.0, Fedora). In one run one engine
        settled instantly and the other did not:

            [CMD] docker stop liferay-db-global
            [STDOUT] liferay-db-global
            [CMD] docker ps -q -f name=^liferay-db-global$
                                               <- gone, correctly

            [CMD] docker stop liferay-db-mysql-global
            [STDOUT] liferay-db-mysql-global    <- the stop SUCCEEDED
            [CMD] docker ps -q -f name=^liferay-db-mysql-global$
            [STDOUT] 92e4917f4414               <- still listed

        MySQL loses that race more often because InnoDB shutdown makes it the
        slower of the two to be reaped. It has cost at least seven CI failures
        across distros and workflows (LDM-#1615, LDM-#1805), every one passing
        on a re-run with no code change.

        Returns True once the container is gone, False if it is still running
        when the deadline passes -- so a caller still refuses on a stop that
        genuinely did not happen. A bounded poll, deliberately, not a fixed
        sleep: a sleep would slow every correct stop and still guess wrong on a
        slow one. A correct stop pays one `docker ps`; only a stop that never
        settles pays the full `timeout`, and that path was already a failure.

        `timeout`/`interval` default to the module constants rather than to
        literals so a test can shrink the window -- driving the real call path
        with a container that never stops otherwise costs a real 15 seconds
        per test.
        """
        timeout = STOP_SETTLE_TIMEOUT if timeout is None else timeout
        interval = STOP_SETTLE_INTERVAL if interval is None else interval

        deadline = time.monotonic() + timeout
        while True:
            if not DockerService.is_running(container_name, target_name):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)

    @staticmethod
    def published_host_ports(target_name: str | None = None) -> set[int]:
        """Host ports currently published by running containers.

        Docker's port allocator -- not the host socket -- is the authority on
        whether a container can take a port. `docker compose up` fails with
        "port is already allocated" straight from that allocation table,
        before any host bind is attempted, so a socket probe is answering a
        different question and can disagree with it.

        LDM-#1417: on Windows the socket probes cannot answer it at all. A
        bind to 127.0.0.1:P succeeds while Docker Desktop holds 0.0.0.0:P, and
        the connect probe added to work around that was measured taking 1-3
        seconds to be accepted on a published port -- far longer than any
        timeout worth spending on every free-port check, so it timed out and
        reported the port free. One `docker ps` costs ~100ms and is
        deterministic, which is why the allocator is asked directly rather
        than inferred from a socket.

        Returns an empty set if Docker is unreachable: absence of evidence is
        not evidence the port is free, and the socket probes still run.
        """
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "ps",
            "--format",
            "{{.Ports}}",
        ]
        res = run_command(cmd, check=False)
        if not res:
            return set()
        # Rows look like "0.0.0.0:5601->80/tcp, [::]:5601->80/tcp". Only the
        # published host port (left of "->") matters; the container-side port
        # and any unpublished "80/tcp" entry must not be picked up.
        ports = set()
        for match in re.findall(r":(\d{1,5})->", res):
            try:
                ports.add(int(match))
            except ValueError:  # pragma: no cover - re guarantees digits
                continue
        return ports

    @staticmethod
    def container_mac_address(
        container_name: str, target_name: str | None = None
    ) -> str | None:
        """The MAC the container actually has, or None if it cannot be read.

        LDM-#1752: emitting `mac_address` into the compose file is not the same
        as the container having it. Measured with Compose v5.2.0 / CLI 29.7.2
        against daemon 25.0.14, both the service-level and network-level forms
        applied -- but that is ONE combination, and the combination is what
        matters here.

        LDM reaches a node through a docker **context**, so compose runs
        CLIENT-side; the node runs only the daemon and need not have compose
        installed at all. The compatibility surface is therefore
        `client compose version x node daemon version`, not a single version.
        Testing on a host where client and daemon are the same version
        exercises a combination LDM never uses. A version that ignores the form
        LDM writes would drop the MAC silently, and the symptom is not a
        container failure: it boots healthy, logs `License registered`, and
        then serves the Activation page instead of Sign In.

        So the emitted value is a request and this is the confirmation. It also
        catches a container created before the configured value changed, which
        cannot be corrected in place -- `docker network connect --mac-address`
        does not exist on 25.0.14.

        Returns None rather than raising: an unreadable MAC is a diagnosis
        problem, and the caller decides whether that is fatal.
        """
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.MacAddress}} {{end}}",
            container_name,
        ]
        try:
            out = run_command(cmd, check=False, capture_output=True)
        except Exception:
            return None
        if not out:
            return None
        # A container on several networks reports one MAC per network. They are
        # the same when pinned, so the first non-empty value answers the
        # question; taking them all would only make the comparison awkward.
        for token in str(out).split():
            token = token.strip()
            if token:
                return token.lower()
        return None

    @staticmethod
    def container_was_oom_killed(
        container_name: str, target_name: str | None = None
    ) -> bool | None:
        """Did the kernel OOM-kill something in this container?

        LDM-#1773. An out-of-memory kill was being reported as a health-check
        timeout: LDM printed "Timed out waiting for Liferay to become healthy"
        after eight minutes while `docker inspect` said `OOMKilled: true` and
        the log said `Killed  start_liferay.sh`. Those are very different
        problems -- one says "wait longer or check the app", the other says
        "this machine does not have enough memory" -- and the operator was
        being pointed at the wrong one.

        The existing `exited` fast-fail does not catch it. The JVM is killed
        while the entrypoint survives, so the container stays up and simply
        never becomes healthy.

        Returns None when the flag cannot be read: unreadable is not the same
        as false, and the caller must not turn "could not ask" into "not an
        OOM".
        """
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "inspect",
            "-f",
            "{{.State.OOMKilled}}",
            container_name,
        ]
        try:
            out = run_command(cmd, check=False, capture_output=True)
        except Exception:
            return None
        if not out:
            return None
        value = str(out).strip().lower()
        if value in ("true", "false"):
            return value == "true"
        return None

    @staticmethod
    def container_publishing_port(
        port: int, target_name: str | None = None
    ) -> str | None:
        """Name of the container publishing `port`, if any (LDM-#1479).

        A port conflict tells the user which service NEEDS the port but not
        what is holding it, leaving them to work that out per-OS. By far the
        commonest holder is another container -- a leftover stack, or another
        LDM project -- and `docker ps` already answers that.

        Best-effort: returns None when Docker is unreachable or nothing
        publishes the port. A conflict must still be reported if this cannot
        add detail to it.
        """
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "ps",
            "--format",
            "{{.Names}}\t{{.Ports}}",
        ]
        res = run_command(cmd, check=False)
        if not res:
            return None

        for line in res.splitlines():
            name, _, ports = line.partition("\t")
            if not name or not ports:
                continue
            # Same shape published_host_ports parses: only the published host
            # port, left of "->", counts.
            if port in {int(m) for m in re.findall(r":(\d{1,5})->", ports)}:
                return name.strip()
        return None

    @staticmethod
    def get_status(container_name: str, target_name: str | None = None) -> str:
        """Gets the state status (e.g. 'running', 'exited') of a container."""
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "inspect",
            "-f",
            "{{.State.Status}}",
            container_name,
        ]
        res = run_command(cmd, check=False)
        return res.strip().lower() if res else "unknown"

    @staticmethod
    def get_health(container_name: str, target_name: str | None = None) -> str:
        """Gets the health status of a container."""
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "inspect",
            "-f",
            "{{.State.Health.Status}}",
            container_name,
        ]
        res = run_command(cmd, check=False)
        return res.strip().lower() if res else "unknown"

    @staticmethod
    def stop(container_name: str, target_name: str | None = None):
        """Stops a container."""
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "stop",
            container_name,
        ]
        return run_command(cmd, check=False, capture_output=True)

    @staticmethod
    def rm(
        container_name: str,
        force: bool = False,
        target_name: str | None = None,
    ):
        """Removes a container."""
        cmd = [*DockerService.get_docker_cmd_prefix(target_name), "rm"]
        if force:
            cmd.append("-f")
        cmd.append(container_name)
        return run_command(cmd, check=False, capture_output=True)

    @staticmethod
    def start(container_name: str, target_name: str | None = None):
        """Starts a container."""
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "start",
            container_name,
        ]
        return run_command(cmd, check=False, capture_output=True)

    @staticmethod
    def restart(container_name: str, target_name: str | None = None):
        """Restarts a container."""
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "restart",
            container_name,
        ]
        return run_command(cmd, check=False, capture_output=True)

    @staticmethod
    def exec(
        container_name: str,
        command_list: list[str],
        check: bool = False,
        capture_output: bool = True,
        target_name: str | None = None,
    ):
        """Executes a command inside a container."""
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "exec",
            container_name,
            *command_list,
        ]
        return run_command(cmd, check=check, capture_output=capture_output)

    @staticmethod
    def gogo(
        container_name: str,
        command: str,
        settle_seconds: int = 6,
        target_name: str | None = None,
    ) -> tuple[str, str | None]:
        """Runs a command in Liferay's Gogo shell and returns (output, error).

        `error` is None when Gogo accepted the command, otherwise the Gogo
        rejection line.

        LDM-#1242: two subtleties make the naive `echo 'cmd' | telnet localhost
        11311` form silently useless, and both are handled here:

        1. Piping a bare `echo` closes stdin immediately, so telnet tears the
           socket down before Gogo has written its reply -- the caller always
           receives only telnet's own connection banner and never the command
           output. Keeping the pipe open for `settle_seconds` lets Gogo answer.
        2. telnet exits 0 whenever the *connection* succeeded, regardless of
           whether Gogo understood the command. Callers that trusted the exit
           code treated `gogo: IOException: no matches found: ...` as success.
           Gogo reports rejection on its own output as a `gogo: <Exception>`
           line, so that is what gets detected.
        """
        payload = f"(echo '{command}'; sleep {settle_seconds}) | telnet localhost 11311"
        res = (
            DockerService.exec(
                container_name,
                ["sh", "-c", payload],
                check=False,
                target_name=target_name,
            )
            or ""
        )

        # Gogo prefixes its own failures with "gogo: ", e.g.
        #   g! gogo: IOException: no matches found: <command>
        for line in res.splitlines():
            stripped = line.strip().removeprefix("g!").strip()
            if stripped.startswith("gogo:"):
                return res, stripped
        return res, None

    @staticmethod
    def get_logs(
        container_name: str,
        tail: int = 100,
        target_name: str | None = None,
    ):
        """Gets the recent logs for a container."""
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "logs",
            "--tail",
            str(tail),
            container_name,
        ]
        return run_command(cmd, check=False, capture_output=True)

    @staticmethod
    def inspect(
        container_name: str,
        fmt: str = "{{json .NetworkSettings.Ports}}",
        target_name: str | None = None,
    ) -> str:
        """Inspects a container on the target compute node."""
        cmd = [
            *DockerService.get_docker_cmd_prefix(target_name),
            "inspect",
            container_name,
            "--format",
            fmt,
        ]
        return run_command(cmd, check=False, capture_output=True) or ""
