"""A release must say what it contains (LDM-#1663).

`_apply_version_update` (`ldm_core/handlers/dev.py:327-345`) prepends an empty
`### Added` / `-` stub on every version bump, and until now nothing ever
required it to be filled in. Measured 2026-09-11: **292 of 356 entries were
empty**, the newest populated one was `v2.15.16-pre.11` (2026-07-14), and 20
stable releases shipped blank -- v2.16.0 through v2.21.0 inclusive.

This is a RATCHET, not a backfill. History is grandfathered; new work is not.

Why a boundary rather than a 292-entry allowlist: `release.py` always
*prepends* (see `find_changelog_insert_index`), so the file is strictly
newest-first and a single mark expresses the same rule far more legibly.

## Why the entry being released right now is exempt

`scripts/release.py` writes the stub, commits it and pushes the tag in one
atomic run, so there is no moment at which the entry for the version being
released could already have been filled in. A check that demanded it would fail
the tag's own CI, and because `build: needs: [lint-and-test, smoke-test]` and
`release: needs: build`, that means **no release assets would ever publish** --
for pre-releases *and* for `--promote`.

That is not theoretical: it burned `v2.21.1-pre.3`. The first version of this
test omitted the exemption, the bump created its stub, and the tag run failed
before `build`.

So the rule is one release behind: the entry for the **current** `VERSION` is
exempt, and every other entry above the mark must be populated. You cannot cut
two releases in a row leaving the previous one undocumented, which is what
allowed 292 empty entries to accumulate.

**Residual gap, deliberately accepted**: the entry for the release being cut
can still ship empty, and is only caught when the *next* one is cut. Closing
that properly means teaching `--promote` to carry the accumulated `-pre.N`
entries forward into the stable entry, which is tracked on LDM-#1663 and is a
change to release machinery, not to a test.
"""

import re
import unittest
from pathlib import Path

from ldm_core.constants import VERSION

CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

# Move this DOWN as older entries are backfilled.
#
# NEVER move it UP. Doing so grandfathers a release that shipped with no
# record of what changed in it, which is the entire defect this guards.
GRANDFATHERED_THROUGH = "v2.21.0"

_HEADING = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)

# A real bullet: a hyphen, whitespace, then something. The stub release.py
# writes is a bare "-" on its own line, which this deliberately does not match.
_REAL_BULLET = re.compile(r"^-\s+\S", re.MULTILINE)


def parse_entries(text):
    """Return [(version, body)] in file order (newest first)."""
    matches = list(_HEADING.finditer(text))
    entries = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entries.append((m.group(1), text[m.end() : end]))
    return entries


def is_populated(body):
    """True if the entry body carries at least one non-empty bullet."""
    return bool(_REAL_BULLET.search(body))


class TestTheCheckerItselfWorks(unittest.TestCase):
    """Proves the logic, so the file check below cannot pass vacuously.

    On `master` there is currently nothing above the mark, so
    TestChangelogRatchet passes without examining anything. That is correct --
    no new release has been cut there yet -- but it means the parsing and
    bullet detection need proving independently, or a broken matcher would look
    identical to a clean CHANGELOG.
    """

    # Byte-for-byte what handlers/dev.py writes, including the "- \n" -> "-\n"
    # rewrite it performs on the way out.
    STUB = "## [v9.9.9] - 2026-01-01\n\n### Added\n\n-\n"
    REAL = "## [v9.9.8] - 2026-01-01\n\n### Fixed\n\n- **Thing**: it works now.\n"

    def test_the_real_stub_is_detected_as_empty(self):
        ((_, body),) = parse_entries(self.STUB)
        self.assertFalse(is_populated(body))

    def test_a_real_bullet_is_detected_as_populated(self):
        ((_, body),) = parse_entries(self.REAL)
        self.assertTrue(is_populated(body))

    def test_a_stub_with_trailing_whitespace_is_still_empty(self):
        """The pre-rewrite form, in case that replace() is ever removed."""
        ((_, body),) = parse_entries("## [v9.9.9] - 2026-01-01\n\n### Added\n\n- \n")
        self.assertFalse(is_populated(body))

    def test_entries_are_split_in_file_order(self):
        versions = [v for v, _ in parse_entries(self.STUB + "\n" + self.REAL)]
        self.assertEqual(versions, ["v9.9.9", "v9.9.8"])

    def test_a_heading_alone_is_not_populated(self):
        ((_, body),) = parse_entries("## [v9.9.9] - 2026-01-01\n")
        self.assertFalse(is_populated(body))


class TestChangelogRatchet(unittest.TestCase):
    def setUp(self):
        self.text = CHANGELOG_PATH.read_text(encoding="utf-8")
        self.entries = parse_entries(self.text)

    def test_the_changelog_parses_at_all(self):
        self.assertGreater(len(self.entries), 100, "CHANGELOG.md parsed as near-empty")

    def test_the_grandfather_mark_still_exists(self):
        """A typo'd or deleted mark would silently exempt the whole file."""
        versions = [v for v, _ in self.entries]
        self.assertIn(
            GRANDFATHERED_THROUGH,
            versions,
            f"GRANDFATHERED_THROUGH is {GRANDFATHERED_THROUGH!r}, which is not a "
            "heading in CHANGELOG.md. Every entry would be treated as historical.",
        )

    def test_every_entry_newer_than_the_mark_describes_its_release(self):
        versions = [v for v, _ in self.entries]
        cutoff = versions.index(GRANDFATHERED_THROUGH)

        # The entry for the version being released right now is exempt -- see
        # the module docstring. release.py stubs, commits and tags atomically,
        # so it cannot have been written yet, and failing here would stop the
        # release job publishing any assets at all.
        being_released = f"v{VERSION}"
        empty = [
            v
            for v, body in self.entries[:cutoff]
            if not is_populated(body) and v != being_released
        ]

        self.assertEqual(
            empty,
            [],
            "These CHANGELOG entries are empty stubs:\n  "
            + "\n  ".join(empty)
            + "\n\nAn empty entry ships a release with no record of what changed. "
            "Write the entry, then re-run.\n"
            "Note --promote is itself a version bump: it prepends a FRESH EMPTY "
            "block above the -pre.N entries, so populating those does not carry "
            "forward to the stable entry.",
        )


if __name__ == "__main__":
    unittest.main()
