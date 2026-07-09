import subprocess
import sys

from ldm_core.ui import UI


class DashboardService:
    """Handler for the Visual Health Dashboard."""

    def __init__(self, manager):
        self.manager = manager

    def cmd_dashboard(self, port=19000, host="127.0.0.1", background=False, token=None):
        """Starts the local web dashboard for LDM."""
        import secrets

        UI.heading("LDM Visual Health Dashboard")

        secret_key = token or secrets.token_hex(32)

        if background:
            # Re-launch this command without --background, but detached
            cmd = [
                sys.executable,
                sys.argv[0],
                "dashboard",
                "--port",
                str(port),
                "--host",
                host,
                "--token",
                secret_key,
            ]
            UI.info(
                f"Starting dashboard in background on http://{host}:{port}/?token={secret_key}..."
            )

            # Use subprocess.Popen to launch detached
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
                subprocess.Popen(
                    cmd,
                    creationflags=creationflags,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            UI.success("Dashboard launched successfully.")
            return

        # Start the Flask app
        try:
            from ldm_core.dashboard.server import start_server
        except ImportError:
            UI.die(
                "Failed to import the dashboard server. Ensure 'flask' is installed."
            )

        UI.info(f"Server starting on http://{host}:{port}/?token={secret_key}")
        UI.detail("Press Ctrl+C to stop the dashboard.")

        start_server(self.manager, host, port, secret_key=secret_key)
