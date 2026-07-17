import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw
from pystray import MenuItem

from ldm_core.constants import REGISTRY_FILE
from ldm_core.ui import UI
from ldm_core.utils import get_actual_home


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

    def _get_registry(self):
        registry_path = get_actual_home() / ".ldm" / REGISTRY_FILE
        if registry_path.exists():
            try:
                return json.loads(registry_path.read_text())
            except Exception:
                return {}
        return {}

    def _open_file_or_dir(self, path):
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)

    def on_open_portal(self, host_name):
        webbrowser.open(f"http://{host_name}")

    def on_copy_tunnel(self, tunnel_url):
        webbrowser.open(tunnel_url)

    def on_edit_properties(self, project_path):
        props_path = os.path.join(project_path, "portal-ext.properties")
        if not os.path.exists(props_path):
            Path(props_path).touch()
        self._open_file_or_dir(props_path)

    def on_view_compose(self, project_path):
        compose_path = os.path.join(project_path, "docker-compose.yml")
        if os.path.exists(compose_path):
            self._open_file_or_dir(compose_path)

    def _stop_project_thread(self, project_id):
        try:
            self.manager.runtime.cmd_stop(project_id=project_id)
        except Exception as e:
            UI.warning(f"Failed to stop {project_id}: {e}")

    def on_stop_project(self, project_id):
        t = threading.Thread(
            target=self._stop_project_thread, args=(project_id,), daemon=True
        )
        t.start()

    def _stop_all_thread(self):
        try:
            self.manager.runtime.cmd_stop(all_projects=True)
        except Exception as e:
            UI.warning(f"Failed to stop all projects: {e}")

    def on_stop_all(self, icon, item):
        t = threading.Thread(target=self._stop_all_thread, daemon=True)
        t.start()

    def on_dashboard(self, icon, item):
        webbrowser.open("http://127.0.0.1:19000")
        t = threading.Thread(target=self._run_dashboard, daemon=True)
        t.start()

    def _run_dashboard(self):
        try:
            self.manager.dashboard.cmd_dashboard(
                port=19000, host="127.0.0.1", background=False, token=None
            )
        except Exception:
            pass

    def on_quit(self, icon, item):
        self.running = False
        icon.stop()

    def _menu_generator(self):
        registry = self._get_registry()

        if registry:
            for project_id, project_data in registry.items():
                project_path = project_data.get("path")
                host_name = project_data.get("host_name", f"{project_id}.local")

                sub_items = [
                    MenuItem(
                        "Open Local Portal",
                        lambda _icon, _item, h=host_name: self.on_open_portal(h),
                    )
                ]

                tunnel_url = project_data.get("tunnel_url")
                if tunnel_url:
                    sub_items.append(
                        MenuItem(
                            "Copy Public URL",
                            lambda _icon, _item, t=tunnel_url: self.on_copy_tunnel(t),
                        )
                    )

                sub_items.append(pystray.Menu.SEPARATOR)
                sub_items.append(
                    MenuItem(
                        "Edit portal-ext.properties",
                        lambda _icon, _item, path=project_path: self.on_edit_properties(
                            path
                        ),
                    )
                )
                sub_items.append(
                    MenuItem(
                        "View docker-compose.yml",
                        lambda _icon, _item, path=project_path: self.on_view_compose(
                            path
                        ),
                    )
                )
                sub_items.append(
                    MenuItem(
                        "Stop Project",
                        lambda _icon, _item, p=project_id: self.on_stop_project(p),
                    )
                )

                yield MenuItem(f"🟢 {project_id}", pystray.Menu(*sub_items))

            yield pystray.Menu.SEPARATOR

        yield MenuItem("Open Diagnostics Dashboard", self.on_dashboard)

        if registry:
            yield MenuItem("Stop All Running Projects", self.on_stop_all)

        yield pystray.Menu.SEPARATOR
        yield MenuItem("Quit LDM Tray", self.on_quit)

    def get_menu(self):
        return pystray.Menu(self._menu_generator)

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
