from ldm_core.utils import run_command


class DockerService:
    """
    Unified service for executing Docker CLI commands.
    Centralizes error handling, string formatting, and raw process execution.
    """

    @staticmethod
    def exists(container_name: str) -> bool:
        """Checks if a container exists (running or stopped)."""
        # Note: Using regex boundary ^...$ to avoid partial matches
        res = run_command(
            ["docker", "ps", "-a", "-q", "-f", f"name=^{container_name}$"], check=False
        )
        return bool(res and res.strip())

    @staticmethod
    def is_running(container_name: str) -> bool:
        """Checks if a container is currently running."""
        res = run_command(
            ["docker", "ps", "-q", "-f", f"name=^{container_name}$"], check=False
        )
        return bool(res and res.strip())

    @staticmethod
    def get_status(container_name: str) -> str:
        """Gets the state status (e.g. 'running', 'exited') of a container."""
        res = run_command(
            ["docker", "inspect", "-f", "{{.State.Status}}", container_name],
            check=False,
        )
        return res.strip().lower() if res else "unknown"

    @staticmethod
    def get_health(container_name: str) -> str:
        """Gets the health status of a container."""
        res = run_command(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", container_name],
            check=False,
        )
        return res.strip().lower() if res else "unknown"

    @staticmethod
    def stop(container_name: str):
        """Stops a container."""
        return run_command(
            ["docker", "stop", container_name], check=False, capture_output=True
        )

    @staticmethod
    def rm(container_name: str, force: bool = False):
        """Removes a container."""
        cmd = ["docker", "rm"]
        if force:
            cmd.append("-f")
        cmd.append(container_name)
        return run_command(cmd, check=False, capture_output=True)

    @staticmethod
    def start(container_name: str):
        """Starts a container."""
        return run_command(
            ["docker", "start", container_name], check=False, capture_output=True
        )

    @staticmethod
    def restart(container_name: str):
        """Restarts a container."""
        return run_command(
            ["docker", "restart", container_name], check=False, capture_output=True
        )

    @staticmethod
    def exec(
        container_name: str,
        command_list: list[str],
        check: bool = False,
        capture_output: bool = True,
    ):
        """Executes a command inside a container."""
        return run_command(
            ["docker", "exec", container_name, *command_list],
            check=check,
            capture_output=capture_output,
        )

    @staticmethod
    def get_logs(container_name: str, tail: int = 100):
        """Gets the recent logs for a container."""
        return run_command(
            ["docker", "logs", "--tail", str(tail), container_name],
            check=False,
            capture_output=True,
        )
