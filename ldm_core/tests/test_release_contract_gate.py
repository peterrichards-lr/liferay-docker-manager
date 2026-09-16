"""`release.py` must refuse before tagging, not after (LDM-#1758).

Two tags were burnt in two cycles to the same shape -- a release-metadata
precondition that CI enforces, discovered only *after* the tag was pushed and
made immutable by the Burn Rule:

    v2.22.0  -pre.4   CHANGELOG ratchet: the PREVIOUS release must be described
                      before the next cut
    v2.23.0  -pre.1   RELEASE_ANNOUNCEMENTS must carry an entry for the active
                      minor -- reachable only via --bump preminor/--premajor,
                      since beta reuses a minor that already has one

`release.py` bumps, commits, tags and pushes, and only then does the tag's CI
run. Both preconditions were already enforced by fast tests that simply were
not run at the point the decision was made.

Documentation alone would not have helped: the announcements contract fires on
a path taken once every few months, so the next person to open a minor is the
next person to burn a tag, having never seen the failure. This project checks
rather than reminds -- the Backport Gate, the version-sync check and the
pre-commit gate are all checks -- and this closes the same hole in the same way.
"""

import unittest
from pathlib import Path

RELEASE_PY = Path(__file__).resolve().parent.parent.parent / "scripts" / "release.py"


class TheGateExists(unittest.TestCase):
    def setUp(self):
        self.src = RELEASE_PY.read_text(encoding="utf-8")

    def test_it_runs_the_contract_tests(self):
        self.assertIn("run_release_contract_checks", self.src)

    def test_it_covers_both_burns(self):
        """One test per tag burnt; either alone would have missed the other."""
        self.assertIn("test_changelog_is_populated.py", self.src)
        self.assertIn("test_architectural_contracts.py", self.src)

    def test_it_does_not_run_the_whole_suite(self):
        """That is what the tag's CI is for. Running it here would make every
        cut materially slower without protecting against this failure any
        better -- and a slow gate is one people route around."""
        start = self.src.index("RELEASE_CONTRACT_TESTS")
        block = self.src[start : start + 400]

        self.assertNotIn('ldm_core/tests/"', block)
        self.assertEqual(block.count("ldm_core/tests/"), 2)


class ItRunsBeforeTheTagIsCreated(unittest.TestCase):
    """The entire point. Running it after the tag exists would report the
    problem while the version number is already spent."""

    def setUp(self):
        self.src = RELEASE_PY.read_text(encoding="utf-8")

    def test_the_gate_precedes_every_tag_creation(self):
        definition_at = self.src.index("def run_release_contract_checks")
        call_at = self.src.index("    run_release_contract_checks(")

        tag_positions = [
            self.src.index(marker)
            for marker in (
                "Creating release tag:",
                "Tagging directly on the release branch",
            )
            if marker in self.src
        ]
        self.assertTrue(tag_positions, "no tag-creation site found to compare against")

        for pos in tag_positions:
            with self.subTest(tag_site=pos):
                self.assertLess(
                    call_at,
                    pos,
                    "the contract gate runs after a tag is created, so the "
                    "number is already burnt by the time it complains",
                )
        self.assertLess(definition_at, max(tag_positions))

    def test_it_is_wired_into_the_existing_quality_gate(self):
        """Every cut path already calls run_pre_commit_checks; hanging the new
        gate off it means no caller can forget one and not the other."""
        gate_at = self.src.index("Pre-commit quality gate checks passed.")
        call_at = self.src.index("    run_release_contract_checks(")

        self.assertGreater(call_at, gate_at)
        self.assertLess(call_at - gate_at, 200, "not adjacent to the existing gate")


class ItRefusesRatherThanWarns(unittest.TestCase):
    def setUp(self):
        self.src = RELEASE_PY.read_text(encoding="utf-8")

    def test_a_failure_aborts_the_release(self):
        start = self.src.index("def run_release_contract_checks")
        block = self.src[start : start + 3000]

        self.assertIn("abort_release", block)

    def test_it_says_the_number_is_still_available(self):
        """The operator's first question on a failed cut is whether they have
        burnt another version."""
        # Matched on a fragment, not the whole sentence: the formatter wraps
        # long strings across literals, so a contiguous search fails on
        # formatting rather than on meaning.
        self.assertIn("still available", self.src)
        self.assertIn("NOT tagging", self.src)

    def test_it_names_the_two_causes_it_guards(self):
        """A bare pytest dump leaves the reader to work out which contract
        broke and why it matters at release time."""
        start = self.src.index("def run_release_contract_checks")
        block = self.src[start : start + 3000]

        self.assertIn("CHANGELOG stub", block)
        self.assertIn("RELEASE_ANNOUNCEMENTS", block)


if __name__ == "__main__":
    unittest.main()
