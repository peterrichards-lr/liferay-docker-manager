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

## Why this file was rewritten (LDM-#1770)

Every assertion here used to be structural: `assertIn` on `release.py`'s source
text, plus `index()` comparisons for ordering. Applying those same assertions to
a copy of `release.py` with a bare `return` inserted after the gate's first
`print` -- function name, docstring, `RELEASE_CONTRACT_TESTS`, every message and
the call site all left in place -- produced:

    real release.py    : ALL PASS
    NEUTERED release.py: ALL PASS

So the suite proved the gate was *wired*, never that it *refused*. For a gate
whose entire value is the refusal path, and which exists because two tags were
already burnt, that is the wrong thing to have verified.

The gate is now driven directly, with `run_cmd` stubbed to supply pytest's
verdict and `abort_release` stubbed to record rather than exit. The ordering
assertions are kept, because "the call precedes tag creation" is a fact about
source order that calling the function cannot show.
"""

import io
import unittest
from contextlib import redirect_stdout

from ldm_core.tests.release_script_loader import RELEASE_PY, load_release


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class GateHarness(unittest.TestCase):
    """Runs the real `run_release_contract_checks` against a scripted pytest."""

    def setUp(self):
        # RELEASE_PY explicitly, read at call time: a probe that repoints
        # this module's RELEASE_PY must actually change what is loaded,
        # otherwise 'do these tests notice a disabled gate?' silently
        # measures the real file and always answers yes.
        self.release = load_release(RELEASE_PY)
        self.aborted: list = []
        self.commands: list = []

        def record_abort(*args, **kwargs):
            self.aborted.append((args, kwargs))

        self.release.abort_release = record_abort

    def run_gate(self, pytest_returncode, stdout="", stderr=""):
        """Returns the gate's printed output. Records any abort_release call."""

        def fake_run_cmd(argv, **kwargs):
            self.commands.append(argv)
            return FakeResult(pytest_returncode, stdout, stderr)

        self.release.run_cmd = fake_run_cmd
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.release.run_release_contract_checks("release/v9.9.9")
        return buffer.getvalue()


class TheGateRefuses(GateHarness):
    """The behaviour. None of these pass against a disabled gate."""

    def test_a_failing_contract_aborts_the_release(self):
        self.run_gate(pytest_returncode=1, stdout="1 failed")

        self.assertTrue(
            self.aborted, "contract checks failed and the release was not aborted"
        )

    def test_a_passing_contract_does_not_abort(self):
        """A gate that refused everything would be removed within a week."""
        out = self.run_gate(pytest_returncode=0)

        self.assertFalse(self.aborted, f"aborted on a passing run: {self.aborted}")
        self.assertIn("passed", out)

    def test_the_abort_carries_the_branch_and_the_exit_code(self):
        """`abort_release` deletes the branch it is given; passing the wrong one
        would delete someone else's work."""
        self.run_gate(pytest_returncode=3)

        args, _ = self.aborted[0]
        self.assertIn("release/v9.9.9", args)
        self.assertIn(3, args)

    def test_the_operator_is_told_the_number_is_not_burnt(self):
        """The first question on a failed cut is whether another version is
        gone. Burning two tags is what produced this gate."""
        out = self.run_gate(pytest_returncode=1)

        self.assertIn("still available", out)
        self.assertIn("NOT tagging", out)

    def test_it_names_both_causes_it_guards(self):
        """A bare pytest dump leaves the reader to work out which contract broke
        and why it matters at release time."""
        out = self.run_gate(pytest_returncode=1)

        self.assertIn("CHANGELOG stub", out)
        self.assertIn("RELEASE_ANNOUNCEMENTS", out)

    def test_the_failure_output_reaches_the_operator(self):
        """Swallowing pytest's own report would make the refusal undiagnosable."""
        out = self.run_gate(
            pytest_returncode=1,
            stdout="FAILED test_changelog_is_populated.py::test_ratchet",
            stderr="some stderr",
        )

        self.assertIn("test_changelog_is_populated.py::test_ratchet", out)
        self.assertIn("some stderr", out)


class ItRunsTheRightTests(GateHarness):
    """Observed from the argv the gate actually builds, not from source text."""

    def setUp(self):
        super().setUp()
        self.run_gate(pytest_returncode=0)
        self.argv = self.commands[0]

    def test_it_covers_both_burns(self):
        """One test per tag burnt; either alone would have missed the other."""
        joined = " ".join(self.argv)

        self.assertIn("test_changelog_is_populated.py", joined)
        self.assertIn("test_architectural_contracts.py", joined)

    def test_it_does_not_run_the_whole_suite(self):
        """That is what the tag's CI is for. A slow gate is one people route
        around, and this runs on every single cut."""
        targets = [a for a in self.argv if "ldm_core/tests/" in a]

        self.assertEqual(len(targets), 2, f"expected two targets, got {targets}")

    def test_it_runs_pytest_without_coverage(self):
        """Coverage on a two-file run fails `fail_under` and would refuse every
        release for the wrong reason."""
        self.assertIn("--no-cov", self.argv)


class ItRunsBeforeTheTagIsCreated(unittest.TestCase):
    """Source-order facts -- what calling the function cannot show.

    Running the gate after the tag exists would report the problem while the
    version number is already spent, which is the entire defect.
    """

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


if __name__ == "__main__":
    unittest.main()
