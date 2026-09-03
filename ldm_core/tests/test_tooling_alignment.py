"""Release tooling and integrity classification fixes.

Three separate defects, each one a case of a check looking for the wrong
shape and then acting confidently on the miss.
"""

import re
import sys
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

from ldm_core.handlers.dev import find_changelog_insert_index
from ldm_core.utils import verify_executable_checksum

REAL_CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"


class TestChangelogInsertPoint(unittest.TestCase):
    """A new release heading belongs above every existing one.

    The scan matched only "## [v", so the pre-v2.8.0 block written
    "## [2.7.28] - ..." was invisible to it and new releases were filed
    beneath ~170 lines of 2.7.x history.
    """

    def test_the_v_prefixed_form_is_found(self):
        lines = ["# Changelog", "", "## [v2.20.0] - 2026-09-02", "### Fixed"]
        self.assertEqual(find_changelog_insert_index(lines), 2)

    def test_the_unprefixed_form_is_found(self):
        """The shape that was invisible before."""
        lines = ["# Changelog", "", "## [2.7.28] - 2026-05-21", "### Fixed"]
        self.assertEqual(find_changelog_insert_index(lines), 2)

    def test_the_older_block_wins_when_it_sits_on_top(self):
        """The real file's shape: stale unprefixed block above the v entries."""
        lines = [
            "# Changelog",
            "",
            "## [2.7.28] - 2026-05-21",
            "### Fixed",
            "- something old",
            "",
            "## [v2.20.0] - 2026-09-02",
        ]
        self.assertEqual(
            find_changelog_insert_index(lines),
            2,
            "a new release must be filed above the 2.7.x block, not below it",
        )

    def test_no_headings_yet_returns_zero(self):
        lines = ["# Changelog", "", "All notable changes..."]
        self.assertEqual(find_changelog_insert_index(lines), 0)

    def test_against_the_real_changelog(self):
        """Not a fixture -- the actual file this runs against in production."""
        lines = REAL_CHANGELOG.read_text(encoding="utf-8").splitlines()
        idx = find_changelog_insert_index(lines)
        self.assertTrue(lines[idx].startswith("## ["))
        for earlier in lines[:idx]:
            self.assertFalse(
                earlier.startswith("## ["),
                "the chosen line is not the first release heading in the file",
            )


class TestChangelogIsChronological(unittest.TestCase):
    """Entries never get older going UP the file.

    LDM-#1585: 24 entries (2.7.28 down to 2.7.3, all 2026-05-21) sat wedged at
    the very top, above v2.20.0. Fixing the insert-point scan alone would have
    made this worse, not better -- matching "## [" puts the next release at the
    true top, i.e. above that stale block, giving v2.20.1 / 170 lines of 2.7.x
    / v2.20.0.

    This invariant was checked before being relied on: across all 351 headings
    there was exactly ONE violation, and it was this defect. So the rule is a
    genuine property of the file, not a shape that merely happens to hold.
    """

    def _headings(self):
        text = REAL_CHANGELOG.read_text(encoding="utf-8")
        return [
            (m.group(1), m.group(2))
            for m in re.finditer(r"^## \[(.+?)\] - (\d{4}-\d{2}-\d{2})", text, re.M)
        ]

    def test_dates_never_increase_going_down_the_file(self):
        headings = self._headings()
        self.assertGreater(len(headings), 300, "the changelog did not parse")
        violations = [
            (above, below) for above, below in pairwise(headings) if above[1] < below[1]
        ]
        self.assertEqual(
            violations,
            [],
            "an entry is older than the one below it: " + str(violations[:3]),
        )

    def test_the_newest_release_is_at_the_top(self):
        first = self._headings()[0]
        latest = max(h[1] for h in self._headings())
        self.assertEqual(
            first[1], latest, f"{first[0]} heads the file but is not the newest"
        )


class TestShebangScriptsAreSource(unittest.TestCase):
    """`ldm` installed as a console-script wrapper is not a tampered binary.

    It has no .py suffix and is not frozen, so the classifier called it a
    compiled binary and compared its hash to checksums.txt for the real
    binary -- a comparison it can never pass.
    """

    def _classify(self, content, suffix=""):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / f"ldm{suffix}"
            exe.write_bytes(content)
            with patch.object(sys, "argv", [str(exe)]):
                return verify_executable_checksum("2.20.0")

    def test_a_shebang_wrapper_is_reported_as_source(self):
        label, ok, _ = self._classify(b"#!/usr/bin/env python3\nprint('ldm')\n")
        self.assertEqual(label, "Source")
        self.assertTrue(ok, "a console-script wrapper must not be flagged")

    def test_a_bash_wrapper_is_reported_as_source(self):
        label, ok, _ = self._classify(b'#!/bin/bash\nexec python -m ldm_core "$@"\n')
        self.assertEqual(label, "Source")
        self.assertTrue(ok)

    def test_a_non_shebang_payload_is_not_claimed_as_source(self):
        """The guard must not swallow everything -- real binaries still verify."""
        label, _, _ = self._classify(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 512)
        self.assertNotEqual(label, "Source", "an ELF payload is not a shebang script")


class TestDocsScanIgnoresClaudeDir(unittest.TestCase):
    """.claude/worktrees holds full repo checkouts; scanning it re-walks
    every markdown file once per worktree, and .claude/skills is a symlink
    to .agents/skills which is already scanned at its canonical path."""

    def test_claude_paths_are_ignored(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        try:
            from ldm_docs_common import is_ignored_path
        finally:
            sys.path.pop(0)

        self.assertTrue(is_ignored_path(Path(".claude/worktrees/x/docs/a.md")))
        self.assertTrue(is_ignored_path(Path(".claude/skills/foo/SKILL.md")))
        self.assertFalse(
            is_ignored_path(Path(".agents/skills/foo/SKILL.md")),
            ".agents is the canonical location and must still be scanned",
        )
        self.assertFalse(is_ignored_path(Path("docs/how-to/install_macos.md")))


class TestEveryDocIsInTheNav(unittest.TestCase):
    """A page absent from mkdocs.yml ships in the repo and not on the site."""

    def test_no_orphaned_pages(self):
        root = Path(__file__).resolve().parents[2]
        nav = (root / "mkdocs.yml").read_text(encoding="utf-8")
        orphans = [
            str(p.relative_to(root / "docs"))
            for p in (root / "docs").rglob("*.md")
            if str(p.relative_to(root / "docs")) not in nav
        ]
        self.assertEqual(orphans, [], f"pages missing from mkdocs.yml nav: {orphans}")


if __name__ == "__main__":
    unittest.main()
