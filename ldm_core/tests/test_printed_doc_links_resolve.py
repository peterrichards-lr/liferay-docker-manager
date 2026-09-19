"""Every repo-file URL LDM prints must point at a file that exists.

LDM-#1825.

## What went wrong

Three places printed a link to `docs/INSTALLATION.md`:

    ldm_core/cli.py                   sudo/root failure
    ldm_core/diagnostics/doctor.py    x2, Docker resource alignment

That file was renamed to `docs/tutorials/quick_start.md` (git records the rename
at 69% similarity) and every one of those links has been a 404 since. Two of
them fire from `ldm doctor`, and the third from a permissions failure -- so a
user already having a bad time was handed a dead link.

Nothing caught it. `check-cli-drift` compares the parser against the CLI
reference, and `check-docs-review` checks markdown timestamps; neither looks at
URLs embedded in strings. A rename in `docs/` cannot see the Python files that
point into it.

## What this asserts, and what it deliberately does not

It checks the **path** resolves to a real file in the repo. It does NOT check
the anchor, and that omission is on purpose: these are `github.com/.../blob/`
URLs, so their fragments are produced by *GitHub's* slugifier, which differs
from MkDocs' for any heading containing an emoji or an ampersand. Asserting
against the built MkDocs ids would demand "fixes" that break the links on
GitHub, which is where they actually point.
"""

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

# github.com/<owner>/<repo>/blob/<ref>/<path>[#anchor]
_BLOB_URL = re.compile(
    r"https://github\.com/[^/\s]+/[^/\s]+/blob/[^/\s]+/"
    r"(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]+)"
)

_SEARCH_ROOTS = ["ldm_core"]
_SKIP_DIRS = {"tests", "__pycache__"}


def _printed_links():
    """Yields (source_file, lineno, repo_path) for every blob URL in the code."""
    for root in _SEARCH_ROOTS:
        for py in (REPO / root).rglob("*.py"):
            if _SKIP_DIRS & set(py.relative_to(REPO).parts):
                continue
            for n, line in enumerate(
                py.read_text(encoding="utf-8", errors="replace").split("\n"), 1
            ):
                for m in _BLOB_URL.finditer(line):
                    yield py.relative_to(REPO), n, m.group("path")


class EveryPrintedRepoLinkResolves(unittest.TestCase):
    def test_no_printed_link_points_at_a_missing_file(self):
        broken = [
            f"{src}:{line} -> {path}"
            for src, line, path in _printed_links()
            if not (REPO / path).is_file()
        ]

        self.assertEqual(
            broken,
            [],
            "these URLs are printed to users but the file no longer exists:\n  "
            + "\n  ".join(broken),
        )

    def test_the_scan_actually_finds_links(self):
        """A guard that silently matches nothing would pass forever. If the URL
        shape changes, this fails rather than the suite going quietly green."""
        found = list(_printed_links())

        self.assertGreater(
            len(found),
            0,
            "the blob-URL pattern matched nothing -- has the URL shape changed?",
        )


if __name__ == "__main__":
    unittest.main()
