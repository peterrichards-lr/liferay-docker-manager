"""Target Node Power & Cost Control Management Service Handler."""

import subprocess
import sys
from pathlib import Path

from ldm_core.constants import SCRIPT_DIR
from ldm_core.ui import UI


class NodeService:
    """Service handler for 'ldm node power' commands."""

    def __init__(self, manager) -> None:
        self.manager = manager
        self.script_path = SCRIPT_DIR / "scripts" / "manage_target_nodes.py"
        if not self.script_path.exists():
            # Fallback to current repository root if running in source tree
            self.script_path = (
                Path(__file__).resolve().parents[2]
                / "scripts"
                / "manage_target_nodes.py"
            )

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
        if not self.script_path.exists():
            UI.error(
                f"Target node power management script not found: {self.script_path}"
            )
            return 1
        cmd = [sys.executable, str(self.script_path), *args]
        res = subprocess.run(cmd, check=False)
        return res.returncode
