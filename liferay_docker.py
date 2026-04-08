import sys
from ldm_core.cli import main
from ldm_core.ui import UI

if __name__ == "__main__":
    if "-v" in sys.argv or "--verbose" in sys.argv:
        UI.info("LDM: Initializing core (Hardened Edition 2026.04.08)")
    main()
