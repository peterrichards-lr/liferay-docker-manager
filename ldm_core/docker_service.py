from ldm_core.config import get_active_target
from ldm_core.utils import run_command


class DockerService:
    """
    Unified service for executing Docker CLI commands.
    Centralizes error handling, string formatting, context resolution, and raw process execution.
    """

    @staticmethod
    def get_docker_cmd_prefix(target_name: str | None = None) -> list[str]:
        """Returns the docker CLI command prefix, injecting --context for remote targets."""
        if not target_name or target_name == "local":
            return ["docker"]
        target = get_active_target(target_name)
        if (
            target
            and target.name != "local"
            and target.host not in ("localhost", "127.0.0.1", "")
        ):
            return ["docker", "--context", target.name]
        if target_name and target_name != "local":
            return ["docker", "--context", target_name]
        return ["docker"]

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
