import os
import sys

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
