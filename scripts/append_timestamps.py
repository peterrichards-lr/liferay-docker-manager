from datetime import datetime
from pathlib import Path


def append():
    now_str = datetime.now().strftime("%Y-%m-%d")
    md_files = [str(p) for p in Path().rglob("*.md")]

    count = 0
    for file_path in md_files:
        if any(
            ignored in file_path
            for ignored in [
                "/.venv/",
                "/node_modules/",
                "/e2e-work-dir/",
                "/.smoke_venv/",
            ]
        ) or file_path.startswith("."):
            continue

        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        if "Last Updated:" not in content and "Last Reviewed:" not in content:
            content = content.rstrip()
            footer = f"\n\n<!-- markdownlint-disable MD049 -->\n---\n*Last Updated: {now_str}* | *Last Reviewed: {now_str}*\n"

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content + footer)
            count += 1

    print(f"Appended footer to {count} files.")


if __name__ == "__main__":
    append()
