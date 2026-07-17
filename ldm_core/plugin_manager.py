import importlib.util
import subprocess
import sys

from ldm_core.utils import get_actual_home


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
            "mcp==1.28.1",
            "--target",
            plugins_dir_str,
            "--upgrade",
        ]
        try:
            subprocess.run(
                cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            print("✅ AI Plugin dependencies successfully installed.")
        except subprocess.CalledProcessError as e:
            print(
                f"❌ Failed to install AI dependencies. Please check your network and python environment: {e}"
            )
            sys.exit(1)
