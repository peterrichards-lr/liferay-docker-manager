"""Target Node Power & Cost Control Management Service Handler."""


class NodeService:
    """Service handler for 'ldm node power' commands."""

    def __init__(self, manager) -> None:
        self.manager = manager

    def cmd_node_power_status(self) -> int:
        """Handler for 'ldm node power status'."""
        return self._run_node_script(["status"])

    def cmd_node_power_wake(self, name: str, ttl: str = "2h") -> int:
        """Handler for 'ldm node power wake <node> [--ttl <ttl>]'."""
        return self._run_node_script(["wake", name, "--ttl", ttl])

    def cmd_node_power_sleep(self, name: str) -> int:
        """Handler for 'ldm node power sleep <node>'."""
        return self._run_node_script(["sleep", name])

    def cmd_node_power_enforce(self) -> int:
        """Handler for 'ldm node power enforce'."""
        return self._run_node_script(["enforce"])

    def cmd_node_power_sync_dns(self) -> int:
        """Handler for 'ldm node power sync-dns'."""
        return self._run_node_script(["sync-dns"])

    def _run_node_script(self, args: list[str]) -> int:
        """Run the node-power utility in-process (LDM-#2291).

        It used to be launched as `[sys.executable, scripts/manage_target_nodes.py]`.
        No build ships that `scripts/` directory, and in a PyInstaller binary
        `sys.executable` is ldm itself rather than Python, so it could not have
        worked even with the file present. The utility now lives in ldm_core and
        exits through `sys.exit` on failure, which is caught here so the exit
        code is returned rather than raised.
        """
        from ldm_core import node_power

        try:
            node_power.main(args)
        except SystemExit as exc:
            code = exc.code
            return code if isinstance(code, int) else (0 if code is None else 1)
        return 0
