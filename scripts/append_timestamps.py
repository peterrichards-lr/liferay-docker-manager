import sys
from datetime import datetime
from pathlib import Path

from ldm_docs_common import FOOTER_REGEX, is_ignored_path

# LDM-#986: this hook is deliberately append-only. It used to rewrite an
# EXISTING footer's "Last Updated" date to today on every --all-files run,
# which meant a commit touching one unrelated file would stamp "reviewed
# today" onto dozens of untouched docs, masking real staleness in "Last
# Reviewed". Its only job now is to make sure a footer EXISTS; bumping the
# date on a real edit is the editing agent's job (per the Active
# Documentation Maintenance Rule in .agents/AGENTS.md), not this script's.


def process_file(file_path, now_str):
    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    if FOOTER_REGEX.search(content):
        return False

    content = content.rstrip()
    footer = f"\n\n<!-- markdownlint-disable MD049 -->\n---\n*Last Updated: {now_str}* | *Last Reviewed: {now_str}*\n"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content + footer)
    return True


def main():
    now_str = datetime.now().strftime("%Y-%m-%d")
    files_to_process = sys.argv[1:]

    if not files_to_process:
        # If no arguments, fallback to rglob
        files_to_process = [str(p) for p in Path().rglob("*.md")]

    count = 0
    for file_path in files_to_process:
        if is_ignored_path(file_path):
            continue

        if Path(file_path).suffix == ".md":
            if process_file(file_path, now_str):
                count += 1

    print(f"Injected a missing timestamp footer into {count} file(s).")


if __name__ == "__main__":
    main()
