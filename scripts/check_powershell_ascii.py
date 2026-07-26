#!/usr/bin/env python3
"""Enforces pure ASCII encoding on all PowerShell (.ps1) files to prevent
Windows PowerShell 5.1 parse errors on ANSI code pages.
"""

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).parent.parent
    failed = False

    for ps_file in root.rglob("*.ps1"):
        if ".venv" in ps_file.parts or "node_modules" in ps_file.parts:
            continue

        raw = ps_file.read_bytes()
        non_ascii = [(idx, byte) for idx, byte in enumerate(raw) if byte > 127]
        if non_ascii:
            print(
                f"[ERROR] {ps_file.relative_to(root)} contains {len(non_ascii)} non-ASCII bytes!"
            )
            failed = True

    if failed:
        print(
            "\n[FAIL] PowerShell scripts must use pure ASCII characters to be compatible with Windows PowerShell 5.1."
        )
        return 1

    print("[SUCCESS] All PowerShell .ps1 files are 100% clean ASCII.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
