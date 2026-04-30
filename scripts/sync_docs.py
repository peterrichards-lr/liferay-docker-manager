#!/usr/bin/env python3
import re
from pathlib import Path

from ldm_core.ui import UI


def sync_table():
    script_dir = Path(__file__).parent.parent
    source_file = script_dir / "docs" / "COMPATIBILITY_TABLE.md"

    if not source_file.exists():
        print(f"Error: Source table not found at {source_file}")
        return

    source_content = source_file.read_text()

    # 1. Extract content between markers from SOURCE
    # We want ONLY the stuff inside the markers
    marker_regex = re.compile(
        r"<!-- COMPATIBILITY_START -->(.*?)<!-- COMPATIBILITY_END -->", re.DOTALL
    )

    source_match = marker_regex.search(source_content)
    if not source_match:
        UI.error(f"Markers not found in source file: {source_file.name}")
        return

    # This is just the inner table content
    inner_content = source_match.group(1).strip()

    # 2. Update TARGET files
    targets = [script_dir / "docs" / "README.md", script_dir / "docs" / "TESTING.md"]

    for target in targets:
        if not target.exists():
            continue

        content = target.read_text()

        # We find the OUTERMOST markers in the target and replace the whole block
        # including markers to ensure we clean up any nesting.
        new_replacement = (
            f"<!-- COMPATIBILITY_START -->\n{inner_content}\n<!-- COMPATIBILITY_END -->"
        )

        if "<!-- COMPATIBILITY_START -->" in content:
            # First, collapse any nested or duplicate markers in the target content
            # by replacing the entire range from the FIRST start to the LAST end.
            first_start = content.find("<!-- COMPATIBILITY_START -->")
            last_end = content.rfind("<!-- COMPATIBILITY_END -->")

            if first_start != -1 and last_end != -1:
                last_end += len("<!-- COMPATIBILITY_END -->")
                new_content = (
                    content[:first_start] + new_replacement + content[last_end:]
                )

                if new_content != content:
                    target.write_text(new_content)
                    UI.success(f"Updated {target.name}")
                else:
                    UI.info(f"{target.name} already in sync.")
        else:
            UI.warning(f"Marker not found in {target.name}")


if __name__ == "__main__":
    sync_table()
