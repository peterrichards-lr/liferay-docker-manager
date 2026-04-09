#!/usr/bin/env python3
import re
from pathlib import Path


def sync_table():
    script_dir = Path(__file__).parent.parent
    source_file = script_dir / "docs" / "COMPATIBILITY_TABLE.md"

    if not source_file.exists():
        print(f"Error: Source table not found at {source_file}")
        return

    table_content = source_file.read_text().strip()
    # Skip the header line if present
    if table_content.startswith("#"):
        table_content = "\n".join(table_content.splitlines()[1:]).strip()

    # Files to update
    targets = [script_dir / "docs" / "README.md", script_dir / "docs" / "TESTING.md"]

    marker_regex = re.compile(
        r"<!-- COMPATIBILITY_START -->.*?<!-- COMPATIBILITY_END -->", re.DOTALL
    )

    replacement = (
        f"<!-- COMPATIBILITY_START -->\n{table_content}\n<!-- COMPATIBILITY_END -->"
    )

    for target in targets:
        if not target.exists():
            continue

        content = target.read_text()
        if "<!-- COMPATIBILITY_START -->" in content:
            new_content = marker_regex.sub(replacement, content)
            if new_content != content:
                target.write_text(new_content)
                print(f"✅ Updated {target.name}")
            else:
                print(f"ℹ {target.name} already in sync.")
        else:
            print(f"⚠️ Marker not found in {target.name}")


if __name__ == "__main__":
    sync_table()
