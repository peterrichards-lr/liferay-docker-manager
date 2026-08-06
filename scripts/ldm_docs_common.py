"""Shared helpers for the documentation-timestamp-footer tooling.

Used by append_timestamps.py (injects a missing footer) and
check_docs_review.py (flags stale/missing footers). Kept in one place so the
footer regex and the ignore-list can't drift between the two scripts the way
they previously did.
"""

import re
from pathlib import Path

FOOTER_REGEX = re.compile(
    r"\*Last Updated: ([\d\-]+)\* \| \*Last Reviewed: ([\d\-]+)\*"
)

# Directories genuinely made of noise (virtual envs, build artifacts, caches,
# vendored deps) that should never be scanned for doc footers. This is an
# explicit denylist rather than "skip anything starting with a dot" -- the
# previous blanket dot-prefix skip also silently excluded .agents/skills/ and
# .gemini/, which are real, actively-maintained rule documents that need the
# same footer coverage as everything else.
IGNORE_DIRS = {
    ".venv",
    ".pytest_venv",
    ".temp_venv",
    ".smoke_venv",
    ".git",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".ldm_temp",
    "node_modules",
    "e2e-work-dir",
    "build",
    "dist",
    "site",
}


def is_ignored_path(file_path) -> bool:
    return any(part in IGNORE_DIRS for part in Path(file_path).parts)
