#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path


def check_links():
    root = Path(__file__).parent.parent.resolve()
    link_pattern = re.compile(r"\[([^\]]+)\]\(([^) \n]+)\)")

    broken_count = 0
    total_links = 0
    ignore_dirs = {
        ".venv",
        ".pytest_venv",
        ".verify-venv",
        ".temp_venv",
        "node_modules",
        ".git",
        ".smoke_venv",
        "e2e-work-dir",
        ".github",
    }

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored directories in-place to prevent os.walk recursion.
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs]

        for filename in filenames:
            if not filename.endswith(".md") or filename == "scratch.md":
                continue

            file_path = Path(dirpath) / filename
            rel_file_path = file_path.relative_to(root)

            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"Error reading {rel_file_path}: {e}")
                sys.exit(1)

            for match in link_pattern.finditer(content):
                text, url = match.groups()

                url_clean = url.split("#")[0].split("?")[0]
                if not url_clean or url_clean.startswith(
                    ("http://", "https://", "mailto:", "tel:")
                ):
                    continue

                total_links += 1

                if url_clean.startswith("file://"):
                    path_str = url_clean.replace("file://", "")
                    target_path = Path(path_str)
                else:
                    target_path = (file_path.parent / url_clean).resolve()

                if not target_path.exists():
                    print(
                        f"Broken Link in {rel_file_path}: [{text}]({url}) -> Target does not exist: {target_path}"
                    )
                    broken_count += 1

    print(f"Markdown Link Checker: Scanned {total_links} local links.")
    if broken_count > 0:
        print(f"Error: Found {broken_count} broken local markdown link(s).")
        sys.exit(1)
    else:
        print("Success: All local markdown links are valid.")
        sys.exit(0)


if __name__ == "__main__":
    check_links()
