import os

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI


class TrayService(BaseHandler):
    """Handler for the System Tray GUI application."""

    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager

    def cmd_tray(self):
        """Entry point for ldm tray command."""
        # Check if running under headless WSL
        if self._is_wsl():
            UI.info("WSL environment detected. System tray UI is unsupported.")
            UI.info("Falling back to Dashboard mode...")
            self.manager.dashboard.cmd_dashboard(
                port=19000, host="127.0.0.1", background=False, token=None
            )
            return

        UI.info("Starting LDM System Tray Application...")
        # Lazy import GUI to prevent slowing down CLI
        try:
            from ldm_core.gui.tray import LdmTrayApp
        except ImportError as e:
            UI.die(
                f"Failed to load GUI dependencies: {e}\nPlease ensure pystray and Pillow are installed."
            )

        try:
            app = LdmTrayApp(self.manager)
            app.run()
        except Exception as e:
            UI.die(f"Tray application crashed: {e}")

    def _is_wsl(self) -> bool:
        """Detect if we are running under WSL where native UI tray isn't easily supported."""
        if os.name == "nt":
            return False
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    return True
        except Exception:
            pass
        return False
