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

# LDM-#1833: roff has no markdown emphasis, so a man page cannot carry the
# footer above -- `*Last Updated*` would render as literal asterisks. It
# carries the same two dates in a roff comment (`.\"`), which renders nowhere:
#
#     .\" Last Updated: 2026-09-19 | Last Reviewed: 2026-09-19
#
# `ldm system version --bump` rewrites only the `.TH` line (handlers/dev.py),
# so unlike that stamp this date moves only when a human actually reviews the
# page.
ROFF_FOOTER_REGEX = re.compile(
    r'^\.\\"\s*Last Updated: ([\d\-]+) \| Last Reviewed: ([\d\-]+)\s*$',
    re.MULTILINE,
)

# Suffixes the review gate scans, mapped to the footer form each one can
# express. Keeping this here rather than in check_docs_review.py is the same
# reasoning that put FOOTER_REGEX here: two scripts consume it.
FOOTER_REGEX_BY_SUFFIX = {
    ".md": FOOTER_REGEX,
    ".1": ROFF_FOOTER_REGEX,
}


def footer_regex_for(file_path):
    """The footer regex appropriate to a file's extension.

    Falls back to the markdown form, which is what every caller predating
    LDM-#1833 assumed unconditionally.
    """
    return FOOTER_REGEX_BY_SUFFIX.get(Path(file_path).suffix, FOOTER_REGEX)


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
    # .claude/worktrees holds full checkouts of the repository, so scanning it
    # walks every markdown file again once per worktree. .claude/skills is a
    # symlink to .agents/skills, which is scanned at its canonical path.
    ".claude",
    "node_modules",
    "e2e-work-dir",
    "build",
    "dist",
    "site",
}


def is_ignored_path(file_path) -> bool:
    return any(part in IGNORE_DIRS for part in Path(file_path).parts)
