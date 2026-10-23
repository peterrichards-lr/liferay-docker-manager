"""Prune the shiv extractions earlier LDM versions left behind (LDM-#2296).

The macOS and Linux binaries are shiv zipapps. On first run each build unpacks
its site-packages into `<shiv root>/ldm_<64-hex build id>/` (`~/.shiv` by
default) and reuses it afterwards. Nothing removed the previous build's
directory, so every upgrade -- every pre-release included -- left about 80 MB
behind: 152 extractions and 12 GB were measured on one machine.

Only LDM's own extractions are touched. The shiv root is shared with any other
shiv application, so an entry that is not `ldm_<build id>` (or its lock file)
is left alone, and the extraction this process is running from never is. A
build that is not a shiv extraction -- a source checkout, a pip install, the
Windows PyInstaller binary -- finds no extraction above it and does nothing.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_EXTRACTION = re.compile(r"^ldm_[0-9a-f]{64}$")


def current_extraction(module_file: str | None = None) -> Path | None:
    """The `ldm_<build id>` directory this code is running from, if any."""
    start = Path(module_file or __file__).resolve()
    for parent in start.parents:
        if _EXTRACTION.match(parent.name):
            return parent
    return None


def _size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def prune_stale_extractions(module_file: str | None = None) -> tuple[int, int]:
    """Remove every other LDM extraction beside the running one.

    Returns (directories removed, bytes freed). Best effort and never raises:
    this runs on the way into an unrelated command.
    """
    current = current_extraction(module_file)
    if current is None:
        return 0, 0
    removed, freed = 0, 0
    own_lock = f".{current.name}_lock"
    try:
        entries = list(current.parent.iterdir())
    except OSError:
        return 0, 0
    for entry in entries:
        name = entry.name
        if entry == current or name == own_lock:
            continue
        try:
            if _EXTRACTION.match(name) and entry.is_dir():
                size = _size(entry)
                shutil.rmtree(entry, ignore_errors=True)
                if not entry.exists():
                    removed += 1
                    freed += size
            elif (
                name.startswith(".ldm_")
                and name.endswith("_lock")
                and _EXTRACTION.match(name[1:-5])
            ):
                entry.unlink(missing_ok=True)
        except OSError:
            continue
    return removed, freed
