import os
import sys

# --- Dev Environment Bootstrap Interceptor ---
# Allow `python liferay_docker.py dev-setup` to run even if third-party
# dependencies (like requests) or generated files (like ui_colors) are missing.
if len(sys.argv) > 1 and sys.argv[1] == "dev-setup":
    import subprocess
    from pathlib import Path

    print("LDM Developer Environment Setup (Bootstrap)")
    root = Path.cwd()
    venv_dir = root / ".venv"

    if not venv_dir.exists():
        print("Creating virtual environment (.venv)...")
        subprocess.run([sys.executable, "-m", "venv", ".venv"], check=True)
        print("Virtual environment created.")

    if os.name == "nt":
        venv_python = venv_dir / "Scripts" / "python.exe"
        venv_pip = venv_dir / "Scripts" / "pip.exe"
    else:
        venv_python = venv_dir / "bin" / "python3"
        venv_pip = venv_dir / "bin" / "pip"

    if not venv_python.exists():
        print(f"Error: Could not find python in venv: {venv_python}")
        sys.exit(1)

    print("Installing dependencies...")
    subprocess.run([str(venv_pip), "install", "--upgrade", "pip"], check=True)
    if (root / "requirements.txt").exists():
        subprocess.run([str(venv_pip), "install", "-r", "requirements.txt"], check=True)
    if (root / "requirements-dev.txt").exists():
        subprocess.run(
            [str(venv_pip), "install", "-r", "requirements-dev.txt"], check=True
        )
    subprocess.run([str(venv_pip), "install", "-e", "."], check=True)

    print("Generating UI Colors...")
    if (root / "scripts" / "sync_colors.py").exists():
        subprocess.run([str(venv_python), "scripts/sync_colors.py"], check=True)

    print("Registering pre-commit hooks...")
    subprocess.run([str(venv_python), "-m", "pre_commit", "install"], check=False)

    print("\n✅ Development environment is ready!")
    if os.name == "nt":
        print("To activate, run: .\\.venv\\Scripts\\activate")
    else:
        print("To activate, run: source .venv/bin/activate")
    sys.exit(0)

# --- Anti-Shadowing Logic ---
# If we are running as a standalone binary (frozen), we must ensure that
# we do not import modules from the current directory (shadowing), as
# this leads to incorrect version reporting and checksum failures.
if getattr(sys, "frozen", False):
    # Get the internal bundle path (where the real ldm_core is)
    bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))

    # Remove any paths that point to the current working directory or external source
    # We want to prioritize the internal bundle
    sys.path = [p for p in sys.path if p != "" and p != os.getcwd()]

    # Ensure bundle_dir is at the very front
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)

from ldm_core.cli import main
from ldm_core.ui import UI

if __name__ == "__main__":
    if "-v" in sys.argv or "--verbose" in sys.argv:
        UI.info("LDM: Initializing core (Hardened Edition 2026.04.08)")
    main()
