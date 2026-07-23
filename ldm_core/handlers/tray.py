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
            UI.detail("Native UI tray is unsupported in this environment (Linux/WSL).")
            UI.detail("Falling back to Dashboard mode...")
            self.manager.dashboard.cmd_dashboard(
                port=19000, host="127.0.0.1", background=False, token=None
            )
            return

        UI.detail("Starting LDM System Tray Application...")
        # Lazy import GUI to prevent slowing down CLI and avoid ABI mismatches
        try:
            from ldm_core.plugin_manager import ensure_gui_installed

            ensure_gui_installed()
            from ldm_core.gui.tray import LdmTrayApp
        except ImportError as e:
            UI.detail(f"Native UI tray dependencies are missing or incompatible: {e}")
            UI.detail("Falling back to Dashboard mode...")
            self.manager.dashboard.cmd_dashboard(
                port=19000, host="127.0.0.1", background=False, token=None
            )
            return

        try:
            app = LdmTrayApp(self.manager)
            app.run()
        except Exception as e:
            UI.detail(f"Tray application crashed on startup: {e}")
            UI.detail("Falling back to Dashboard mode...")
            self.manager.dashboard.cmd_dashboard(
                port=19000, host="127.0.0.1", background=False, token=None
            )

    def _is_unsupported_gui_env(self) -> bool:
        """Detect if we are running in an environment where native UI tray isn't easily supported."""
        import sys

        # Native Linux (including WSL) often lacks consistent AppIndicator/Wayland support for pystray out of the box
        if sys.platform.startswith("linux"):
            return True
        return False

    def setup_autostart(self):  # noqa: PLR0915
        """Provisions native autostart / launch-on-login for LDM System Tray."""
        import contextlib
        import platform
        import shutil
        import subprocess
        import sys
        from pathlib import Path

        from ldm_core.utils import get_actual_home

        sys_type = platform.system()
        ldm_bin = shutil.which("ldm") or sys.executable

        if sys_type == "Darwin":
            home = get_actual_home()
            app_dir = home / "Applications" / "Liferay Docker Manager.app"
            app_dir.mkdir(parents=True, exist_ok=True)
            contents = app_dir / "Contents"
            macos_dir = contents / "MacOS"
            resources_dir = contents / "Resources"
            macos_dir.mkdir(parents=True, exist_ok=True)
            resources_dir.mkdir(parents=True, exist_ok=True)

            icon_src = Path(__file__).parent.parent / "resources" / "ldm_app_icon.jpg"
            if icon_src.exists():
                shutil.copy(icon_src, resources_dir / "AppIcon.jpg")

            info_plist = contents / "Info.plist"
            info_plist.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n'
                "<dict>\n"
                "    <key>CFBundleExecutable</key>\n"
                "    <string>ldm-tray</string>\n"
                "    <key>CFBundleIdentifier</key>\n"
                "    <string>com.liferay.ldm</string>\n"
                "    <key>CFBundleName</key>\n"
                "    <string>Liferay Docker Manager</string>\n"
                "    <key>CFBundleDisplayName</key>\n"
                "    <string>Liferay Docker Manager</string>\n"
                "    <key>CFBundleIconFile</key>\n"
                "    <string>AppIcon.jpg</string>\n"
                "    <key>LSUIElement</key>\n"
                "    <true/>\n"
                "</dict>\n"
                "</plist>\n"
            )

            launcher = macos_dir / "ldm-tray"
            launcher.write_text(f'#!/bin/sh\nexec "{ldm_bin}" tray\n')
            launcher.chmod(0o755)

            agents_dir = home / "Library" / "LaunchAgents"
            agents_dir.mkdir(parents=True, exist_ok=True)

            # Legacy plist cleanup
            old_plist_file = agents_dir / "com.liferay.dockermanager.plist"
            if old_plist_file.exists():
                try:
                    subprocess.run(
                        ["launchctl", "unload", str(old_plist_file)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                except Exception:
                    pass
                with contextlib.suppress(OSError):
                    old_plist_file.unlink()

            plist_file = agents_dir / "com.liferay.ldm.plist"
            launcher_path = str(macos_dir / "ldm-tray")
            plist_file.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n'
                "<dict>\n"
                "    <key>Label</key>\n"
                "    <string>com.liferay.ldm.gui</string>\n"
                "    <key>AssociatedBundleIdentifiers</key>\n"
                "    <array>\n"
                "        <string>com.liferay.ldm</string>\n"
                "    </array>\n"
                "    <key>ProgramArguments</key>\n"
                "    <array>\n"
                f"        <string>{launcher_path}</string>\n"
                "    </array>\n"
                "    <key>RunAtLoad</key>\n"
                "    <true/>\n"
                "    <key>KeepAlive</key>\n"
                "    <false/>\n"
                "</dict>\n"
                "</plist>\n"
            )

            try:
                subprocess.run(
                    ["launchctl", "unload", str(plist_file)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                subprocess.run(
                    ["launchctl", "load", str(plist_file)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass

            UI.success("System Tray autostart enabled for macOS.")
            UI.detail(f"App Bundle: {app_dir}")
            UI.detail(f"LaunchAgent: {plist_file}")

        elif sys_type == "Windows":
            home = get_actual_home()
            startup_dir = (
                home
                / "AppData"
                / "Roaming"
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
                / "Startup"
            )
            startup_dir.mkdir(parents=True, exist_ok=True)
            script_file = startup_dir / "Liferay Docker Manager.bat"
            script_file.write_text(f'@echo off\nstart "" "{ldm_bin}" tray\n')
            UI.success("System Tray autostart enabled for Windows.")
            UI.detail(f"Startup Script: {script_file}")

        else:
            home = get_actual_home()
            autostart_dir = home / ".config" / "autostart"
            autostart_dir.mkdir(parents=True, exist_ok=True)

            # Legacy cleanup
            old_desktop = autostart_dir / "ldm-tray.desktop"
            if old_desktop.exists():
                with contextlib.suppress(OSError):
                    old_desktop.unlink()

            icon_str = "ldm"

            desktop_file = autostart_dir / "ldm.desktop"
            desktop_file.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Liferay Docker Manager\n"
                "Comment=Liferay Docker Manager Service\n"
                f"Exec={ldm_bin} tray\n"
                f"Icon={icon_str}\n"
                "Terminal=false\n"
                "Categories=Utility;Development;\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
            UI.success("System Tray autostart enabled for Linux.")
            UI.detail(f"Desktop Entry: {desktop_file}")

    def remove_autostart(self):
        """Removes native autostart / launch-on-login entries."""
        import contextlib
        import platform
        import shutil
        import subprocess

        from ldm_core.utils import get_actual_home

        sys_type = platform.system()
        home = get_actual_home()

        if sys_type == "Darwin":
            plist_file = home / "Library" / "LaunchAgents" / "com.liferay.ldm.plist"
            old_plist_file = (
                home / "Library" / "LaunchAgents" / "com.liferay.dockermanager.plist"
            )
            app_dir = home / "Applications" / "Liferay Docker Manager.app"

            for pf in (plist_file, old_plist_file):
                if pf.exists():
                    try:
                        subprocess.run(
                            ["launchctl", "unload", str(pf)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    except Exception:
                        pass
                    with contextlib.suppress(OSError):
                        pf.unlink()

            if app_dir.exists():
                shutil.rmtree(app_dir, ignore_errors=True)

            UI.success("macOS LaunchAgent autostart removed.")

        elif sys_type == "Windows":
            startup_dir = (
                home
                / "AppData"
                / "Roaming"
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
                / "Startup"
            )
            script_file = startup_dir / "Liferay Docker Manager.bat"
            old_script_file = startup_dir / "ldm-tray-autostart.bat"

            removed = False
            if script_file.exists():
                script_file.unlink()
                removed = True
            if old_script_file.exists():
                old_script_file.unlink()
                removed = True

            if removed:
                UI.success("Windows Startup script autostart removed.")
            else:
                UI.detail("No Windows Startup script found.")

        else:
            desktop_file = home / ".config" / "autostart" / "ldm.desktop"
            old_desktop_file = home / ".config" / "autostart" / "ldm-tray.desktop"
            removed = False
            for df in (desktop_file, old_desktop_file):
                if df.exists():
                    df.unlink()
                    removed = True

            if removed:
                UI.success("Linux Desktop Entry autostart removed.")
            else:
                UI.detail("No Linux Desktop Entry found.")
