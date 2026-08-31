import importlib.util
import subprocess
import sys

from ldm_core.constants import PIP_INSTALL_TIMEOUT
from ldm_core.utils import get_actual_home

# Must match the `mcp` pin in requirements.txt / pyproject.toml. Dependabot
# updates those two and not this one, so #1480 would have shipped a binary
# installing a different mcp than the source declared (LDM-#1483).
MCP_PIN = "mcp==2.1.1"


def ensure_mcp_installed():
    """
    Dynamically installs 'mcp' (and 'pydantic-core') into a predictable, S1-safe directory.
    This avoids compilation errors on ARM64 by building wheels locally and avoids S1 detection.
    """
    plugins_dir = get_actual_home() / ".ldm" / "plugins" / "ai"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    # Insert at the front of sys.path so it's prioritized
    plugins_dir_str = str(plugins_dir)
    if plugins_dir_str not in sys.path:
        sys.path.insert(0, plugins_dir_str)

    if importlib.util.find_spec("mcp") is None:
        print("🤖 Initializing AI Plugin dependencies (one-time setup)...")
        # Install the dependencies dynamically
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            MCP_PIN,
            "--target",
            plugins_dir_str,
            "--upgrade",
            "--break-system-packages",
        ]
        try:
            # LDM-#1332: bounded. A pip install against an unreachable or
            # throttled index otherwise hangs indefinitely, and stdout/stderr
            # are sent to DEVNULL here, so there is no progress output to
            # suggest anything is wrong.
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PIP_INSTALL_TIMEOUT,
            )
            print("✅ AI Plugin dependencies successfully installed.")
        except subprocess.TimeoutExpired:
            print(
                f"❌ Timed out installing AI dependencies after "
                f"{PIP_INSTALL_TIMEOUT}s. The package index may be unreachable "
                "or throttling; check your network and retry."
            )
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(
                f"❌ Failed to install AI dependencies. Please check your network and python environment: {e}"
            )
            sys.exit(1)


def ensure_gui_installed():
    """
    Dynamically installs GUI dependencies ('pystray', 'Pillow', 'pyobjc') into a predictable, S1-safe directory.
    This avoids compilation errors on ARM64 macOS across different python minor versions while avoiding S1 detection.
    """
    plugins_dir = get_actual_home() / ".ldm" / "plugins" / "gui"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    # Insert at the front of sys.path so it's prioritized
    plugins_dir_str = str(plugins_dir)
    if plugins_dir_str not in sys.path:
        sys.path.insert(0, plugins_dir_str)

    if (
        importlib.util.find_spec("pystray") is None
        or importlib.util.find_spec("PIL") is None
    ):
        print("🟢 Initializing GUI Plugin dependencies (one-time setup)...")
        # Install the dependencies dynamically
        packages = ["pystray>=0.19.0", "Pillow>=10.0.0"]
        if sys.platform == "darwin":
            packages.extend(
                [
                    "pyobjc-framework-FSEvents",
                    "pyobjc-framework-Quartz",
                    "pyobjc-framework-Cocoa",
                ]
            )

        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            *packages,
            "--target",
            plugins_dir_str,
            "--upgrade",
            "--break-system-packages",
        ]
        try:
            # LDM-#1332: bounded. A pip install against an unreachable or
            # throttled index otherwise hangs indefinitely, and stdout/stderr
            # are sent to DEVNULL here, so there is no progress output to
            # suggest anything is wrong.
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PIP_INSTALL_TIMEOUT,
            )
            print("✅ GUI Plugin dependencies successfully installed.")
        except subprocess.TimeoutExpired:
            print(
                f"❌ Timed out installing GUI dependencies after "
                f"{PIP_INSTALL_TIMEOUT}s. The package index may be unreachable "
                "or throttling; check your network and retry."
            )
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(
                f"❌ Failed to install GUI dependencies. Please check your network and python environment: {e}"
            )
            sys.exit(1)
