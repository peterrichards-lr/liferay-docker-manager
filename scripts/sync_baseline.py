#!/usr/bin/env python3
import shutil
from pathlib import Path

# --- Configuration ---
SOURCE_DIR = Path("common")
DEST_DIR = Path("ldm_core/resources/common_baseline")
EXCLUDE_FILES = {"README.md"}


def sync_baseline():
    """Syncs the top-level common/ directory to the internal resources/common_baseline folder."""
    print(f"Syncing baseline assets: {SOURCE_DIR} -> {DEST_DIR}")

    if not SOURCE_DIR.exists():
        print(f"Error: Source directory {SOURCE_DIR} not found.")
        return

    DEST_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Clean destination (remove files that no longer exist in source)
    for dest_file in DEST_DIR.iterdir():
        if dest_file.is_file() and dest_file.name not in EXCLUDE_FILES:
            # Also clean up any accidental XML files
            if dest_file.suffix.lower() == ".xml":
                print(f"  - Removing restricted: {dest_file.name}")
                dest_file.unlink()
                continue

            source_file = SOURCE_DIR / dest_file.name
            if not source_file.exists():
                print(f"  - Removing obsolete: {dest_file.name}")
                dest_file.unlink()

    # 2. Copy source to destination
    count = 0
    for source_file in SOURCE_DIR.iterdir():
        if source_file.is_file() and source_file.name not in EXCLUDE_FILES:
            # Skip XML files (activation keys)
            if source_file.suffix.lower() == ".xml":
                continue

            dest_file = DEST_DIR / source_file.name

            # Check if sync is needed (different content)
            if dest_file.exists():
                with open(source_file, "rb") as s, open(dest_file, "rb") as d:
                    if s.read() == d.read():
                        continue

            print(f"  + Syncing: {source_file.name}")
            shutil.copy2(source_file, dest_file)
            count += 1

    print(f"Sync complete. {count} files updated.")


if __name__ == "__main__":
    sync_baseline()
