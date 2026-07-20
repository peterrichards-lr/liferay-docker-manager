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
        # Check if running under unsupported GUI environments (WSL, Linux Wayland/headless)
        if self._is_unsupported_gui_env():
            UI.info("Native UI tray is unsupported in this environment (Linux/WSL).")
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
            import sys

            if sys.platform.startswith("linux"):
                UI.info(f"Tray initialization failed: {e}")
                UI.info(
                    "Native UI tray may lack dependencies on this Linux distribution."
                )
                UI.info("Falling back to Dashboard mode...")
                self.manager.dashboard.cmd_dashboard(
                    port=19000, host="127.0.0.1", background=False, token=None
                )
                return
            UI.die(f"Tray application crashed: {e}")

    def _is_unsupported_gui_env(self) -> bool:
        """Detect if we are running in an environment where native UI tray isn't easily supported."""
        import sys

        # Native Linux (including WSL) often lacks consistent AppIndicator/Wayland support for pystray out of the box
        if sys.platform.startswith("linux"):
            return True

        return False
