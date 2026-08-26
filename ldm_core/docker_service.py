from ldm_core.config import get_active_target
from ldm_core.utils import is_local_host, run_command


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
    def get_context_endpoint_host(context_name: str) -> str | None:
        """Returns the host a Docker context dials, or None if it has none.

        LDM-#1346: a context's endpoint is stored by Docker, not by LDM, so it
        can disagree with the `host` recorded in `~/.ldmrc` -- and when it does,
        LDM reports the stored host while dialling the context's. Reading it
        back is what makes that disagreement visible instead of silent.
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
