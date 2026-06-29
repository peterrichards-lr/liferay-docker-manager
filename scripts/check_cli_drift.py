#!/usr/bin/env python
import sys
from pathlib import Path

# Ensure ldm_core is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ldm_core.utils import verify_cli_drift

if __name__ == "__main__":
    sys.exit(verify_cli_drift())
