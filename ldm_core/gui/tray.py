import os
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw
from pystray import MenuItem

from ldm_core.ui import UI


class LdmTrayApp:
    def __init__(self, manager):
        self.manager = manager
        self.icon = None
        self.running = False

        # Load base icon
        self.base_image_path = os.path.join(
            os.path.dirname(__file__), "..", "resources", "ldm_app_icon.jpg"
        )
        try:
            self.base_image = Image.open(self.base_image_path).convert("RGBA")
        except Exception as e:
            UI.die(f"Failed to load icon from {self.base_image_path}: {e}")

        self.current_state = "stopped"  # stopped, running

    def get_dynamic_icon(self, state):
        """Draws a status dot on the base icon."""
        img = self.base_image.copy()
        draw = ImageDraw.Draw(img)
        w, h = img.size
        r = int(w * 0.15)

        colors = {
            "running": (40, 200, 40, 255),
            "stopped": (200, 40, 40, 255),
        }
        color = colors.get(state, (100, 100, 100, 255))

        # Draw circle in bottom right
        draw.ellipse((w - r * 2, h - r * 2, w, h), fill=color, outline=(0, 0, 0, 255))
        return img

    def update_state(self):
        """Background thread to poll LDM state."""
        while self.running:
            try:
                if not self.manager.check_docker():
                    new_state = "stopped"
                else:
                    running_containers = self.manager.docker.get_running_containers()
                    if any("liferay" in c for c in running_containers):
                        new_state = "running"
                    else:
                        new_state = "stopped"

                if new_state != self.current_state:
                    self.current_state = new_state
                    if self.icon:
                        self.icon.icon = self.get_dynamic_icon(self.current_state)
            except Exception:
                pass
            time.sleep(5)

    def _start_project_thread(self):
        try:
            # We bypass the foreground log tailing by temporarily patching args
            original_no_logs = getattr(self.manager.args, "no_logs", False)
            self.manager.args.no_logs = True

            project_path = self.manager.detect_project_path()
            if project_path:
                project_id = Path(project_path).name
                self.manager.runtime.cmd_run(project_id=project_id)
            else:
                UI.warn("Not inside an LDM workspace.")

            self.manager.args.no_logs = original_no_logs
        except Exception as e:
            UI.warn(f"Failed to start: {e}")

    def on_start(self, icon, item):
        t = threading.Thread(target=self._start_project_thread, daemon=True)
        t.start()

    def _stop_project_thread(self):
        try:
            project_path = self.manager.detect_project_path()
            if project_path:
                project_id = Path(project_path).name
                self.manager.runtime.cmd_stop(project_id=project_id)
            else:
                self.manager.runtime.cmd_stop(all_projects=True)
        except Exception as e:
            UI.warn(f"Failed to stop: {e}")

    def on_stop(self, icon, item):
        t = threading.Thread(target=self._stop_project_thread, daemon=True)
        t.start()

    def on_dashboard(self, icon, item):
        webbrowser.open("http://127.0.0.1:19000")
        # Also start dashboard server in background if not running
        t = threading.Thread(target=self._run_dashboard, daemon=True)
        t.start()

    def _run_dashboard(self):
        try:
            # We don't want to block, dashboard runs its own Flask server
            self.manager.dashboard.cmd_dashboard(
                port=19000, host="127.0.0.1", background=False, token=None
            )
        except Exception:
            pass

    def on_quit(self, icon, item):
        self.running = False
        icon.stop()

    def get_menu(self):
        return pystray.Menu(
            MenuItem("Start Project", self.on_start),
            MenuItem("Stop Project", self.on_stop),
            MenuItem("Open Dashboard", self.on_dashboard),
            MenuItem("Quit LDM Tray", self.on_quit),
        )

    def run(self):
        self.running = True
        self.icon = pystray.Icon(
            "LDM",
            self.get_dynamic_icon(self.current_state),
            "Liferay Docker Manager",
            self.get_menu(),
        )

        # Start poller thread
        t = threading.Thread(target=self.update_state, daemon=True)
        t.start()

        self.icon.run()
