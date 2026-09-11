"""`--promote` must carry the cycle's pre-release entries forward (LDM-#1671).

`--promote` is itself a version bump, so it prepended the same empty
`### Added` / `-` stub every other bump writes. The pre-releases' content --
which is precisely what the stable release contains -- did not carry over, so
the stable entry shipped blank even when every `-pre.N` in the cycle had been
written up. Twenty stable releases went out that way (LDM-#1663).

This matters more since LDM-#1673: the CHANGELOG ratchet has to exempt the
entry for the version being released, because release.py stubs, commits and
tags atomically. The stable entry is therefore the one case the ratchet cannot
catch at the moment it is created, and carrying content forward is what closes
it.

Tests drive the pure collector rather than `_apply_version_update`, which
writes to `Path.cwd()`. Per LDM-#1391 a test must never mutate the real
`CHANGELOG.md`.
"""

import unittest

from ldm_core.handlers.dev import collect_prerelease_changelog_body

PREAMBLE = """# Changelog

All notable changes to this project will be documented in this file.
"""


def changelog(*entries):
    return PREAMBLE + "\n" + "\n".join(entries)


PRE_1 = """## [v9.9.9-pre.1] - 2026-01-01

### Fixed

- **First**: the earliest fix.

### Added

- **Feature**: something new.
"""

PRE_2 = """## [v9.9.9-pre.2] - 2026-01-02

### Fixed

- **First**: the earliest fix.
- **Second**: a later fix.
"""

PREVIOUS_STABLE = """## [v9.9.8] - 2025-12-01

### Fixed

- **Old**: belongs to the previous cycle and must not leak forward.
"""


class TestCarryForward(unittest.TestCase):
    def test_a_stable_release_collects_its_cycle(self):
        body = collect_prerelease_changelog_body(
            changelog(PRE_2, PRE_1, PREVIOUS_STABLE), "9.9.9"
        )
        self.assertIn("**First**", body)
        self.assertIn("**Second**", body)
        self.assertIn("**Feature**", body)

    def test_the_previous_cycle_does_not_leak_forward(self):
        """Collection must stop at the preceding stable heading."""
        body = collect_prerelease_changelog_body(
            changelog(PRE_2, PRE_1, PREVIOUS_STABLE), "9.9.9"
        )
        self.assertNotIn("**Old**", body)

    def test_a_repeated_bullet_appears_once(self):
        """A fix is usually restated in each later pre-release."""
        body = collect_prerelease_changelog_body(
            changelog(PRE_2, PRE_1, PREVIOUS_STABLE), "9.9.9"
        )
        self.assertEqual(body.count("**First**"), 1)

    def test_bullets_read_oldest_pre_release_first(self):
        body = collect_prerelease_changelog_body(
            changelog(PRE_2, PRE_1, PREVIOUS_STABLE), "9.9.9"
        )
        self.assertLess(body.index("**First**"), body.index("**Second**"))

    def test_sections_follow_keep_a_changelog_order(self):
        body = collect_prerelease_changelog_body(
            changelog(PRE_2, PRE_1, PREVIOUS_STABLE), "9.9.9"
        )
        self.assertLess(body.index("### Added"), body.index("### Fixed"))

    def test_an_unrecognised_section_is_kept_and_placed_last(self):
        custom = """## [v9.9.9-pre.1] - 2026-01-01

### Internal

- **Plumbing**: not a Keep a Changelog section.

### Fixed

- **Real**: a fix.
"""
        body = collect_prerelease_changelog_body(
            changelog(custom, PREVIOUS_STABLE), "9.9.9"
        )
        self.assertIn("**Plumbing**", body)
        self.assertLess(body.index("### Fixed"), body.index("### Internal"))

    def test_a_pre_release_bump_carries_nothing(self):
        """Only a stable bump merges; a -pre.N bump keeps the usual stub."""
        self.assertIsNone(
            collect_prerelease_changelog_body(
                changelog(PRE_2, PRE_1, PREVIOUS_STABLE), "9.9.9-pre.3"
            )
        )

    def test_empty_stubs_carry_nothing(self):
        """A cycle nobody documented must not produce a body of bare hyphens."""
        stub = """## [v9.9.9-pre.1] - 2026-01-01

### Added

-
"""
        self.assertIsNone(
            collect_prerelease_changelog_body(changelog(stub, PREVIOUS_STABLE), "9.9.9")
        )

    def test_a_cycle_with_no_pre_releases_carries_nothing(self):
        """A straight `--bump minor` has no pre-releases to merge."""
        self.assertIsNone(
            collect_prerelease_changelog_body(changelog(PREVIOUS_STABLE), "9.9.9")
        )

    def test_a_different_versions_pre_releases_are_not_collected(self):
        """Only THIS version's pre-releases count."""
        other = """## [v9.9.7-pre.1] - 2025-11-01

### Fixed

- **Unrelated**: a different cycle entirely.
"""
        self.assertIsNone(
            collect_prerelease_changelog_body(
                changelog(other, PREVIOUS_STABLE), "9.9.9"
            )
        )


if __name__ == "__main__":
    unittest.main()


class TestTheWriterActuallyUsesIt(unittest.TestCase):
    """The collector being correct is worthless if `--promote` never calls it.

    Drives the real `_apply_version_update` against a temporary working
    directory. Only `CHANGELOG.md` exists there; every other file in
    `files_to_update` is skipped by its own `if not p.exists(): continue`, so
    the CHANGELOG behaviour is exercised in isolation without touching the
    repository (LDM-#1391).
    """

    def _write_and_read(self, version, entries):
        import contextlib
        import os
        import tempfile
        from pathlib import Path

        from ldm_core.handlers.dev import DevService

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CHANGELOG.md"
            path.write_text(changelog(*entries), encoding="utf-8")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                # The CHANGELOG is written first; the method then refuses with
                # SystemExit because none of the OTHER version files exist here
                # to be updated ("changed no files ... refusing to report
                # success"). That guard is correct and deliberately not
                # defeated -- the file under test has already been written by
                # the time it fires, which the assertions below confirm.
                with contextlib.suppress(SystemExit):
                    DevService.__new__(DevService)._apply_version_update(version)
            finally:
                os.chdir(cwd)
            return path.read_text(encoding="utf-8")

    def test_promoting_writes_a_populated_stable_entry(self):
        written = self._write_and_read("9.9.9", [PRE_2, PRE_1, PREVIOUS_STABLE])

        self.assertIn("## [v9.9.9]", written)
        stable = written[
            written.index("## [v9.9.9]") : written.index("## [v9.9.9-pre.2]")
        ]
        self.assertIn("**First**", stable)
        self.assertIn("**Second**", stable)
        self.assertNotIn("**Old**", stable)

    def test_a_pre_release_bump_still_writes_the_stub(self):
        written = self._write_and_read("9.9.9-pre.3", [PRE_2, PRE_1, PREVIOUS_STABLE])

        stable = written[
            written.index("## [v9.9.9-pre.3]") : written.index("## [v9.9.9-pre.2]")
        ]
        self.assertIn("### Added", stable)
        self.assertNotIn("**First**", stable)
