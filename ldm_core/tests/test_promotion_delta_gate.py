"""Stable must be the software that was verified (LDM-#1765).

Every other gate in the release path guards the road TO a pre-release --
Backport, Feature Verification Script, Behaviour Coverage, Pre-Flight Quality,
and the pre-tag contract gate from LDM-#1758. Nothing guarded the gap between
*verifying* a pre-release and *promoting* it, and work keeps landing on
`master` during the days a verification takes.

Hit promoting v2.22.0: four commits merged after the `-pre.9` verification and
shipped in stable, one of them 91 lines of `ldm_core/diagnostics/info.py`. It
was judged acceptable -- the only behavioural surface was
`ldm system doctor --slug`, which names the verification report file and is
nothing the E2E suite asserts -- but it was found by the maintainer asking
afterwards rather than by any check. And the answer was not obvious: three of
the four commits genuinely were tooling and docs, so "it's all tooling" was a
reasonable belief right up until someone ran the diff.

LDM-#1754 recorded it as a rule in the release skill. A rule that fires once
per release, months apart, is not one anyone retains -- which is the same
argument that made LDM-#1758 a check rather than a note.

## Why this file was rewritten (LDM-#1770)

The first version never called `check_promotion_delta`. It re-implemented the
computation in a local `shipped_delta()` helper -- commented, in as many words,
"the gate's own computation" -- and checked the wiring with `self.src.index()`
on the source text.

That is precisely the shape that made LDM-#1759's first regression test
worthless: a test that re-implements the link it claims to be testing passes
whether or not the real one works. An early `return` in the gate, or a typo in
its exclusion set, would not have failed anything here.

So the helper is gone and the real function is driven directly, with `run_cmd`
stubbed to supply git's answers. The structural assertions are kept only for the
genuinely structural property -- that the call sits between the promote guard
and the version bump -- which is a fact about source order and cannot be
observed by calling the function.
"""

import io
import subprocess  # nosec B404 - reads real git history to feed the gate
import unittest
from contextlib import redirect_stdout

from ldm_core.tests.release_script_loader import RELEASE_PY, REPO_ROOT, load_release


class FakeResult:
    """Stands in for `run_cmd`'s return value."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def git_stub(tags, changed):
    """A `run_cmd` that answers the two git questions the gate asks."""

    def _run(argv, **kwargs):
        if argv[:2] == ["git", "tag"]:
            return FakeResult(stdout="\n".join(tags))
        if argv[:2] == ["git", "diff"]:
            return FakeResult(stdout="\n".join(changed))
        return FakeResult()

    return _run


class GateHarness(unittest.TestCase):
    """Runs the real `check_promotion_delta` against scripted git output."""

    def setUp(self):
        # RELEASE_PY explicitly, read at call time: a probe that repoints
        # this module's RELEASE_PY must actually change what is loaded,
        # otherwise 'do these tests notice a disabled gate?' silently
        # measures the real file and always answers yes.
        self.release = load_release(RELEASE_PY)

    def run_gate(self, changed, tags=("v2.22.0-pre.9",), allow_delta=False):
        """Returns (refused, output). `refused` is True when the gate exits."""
        self.release.run_cmd = git_stub(list(tags), list(changed))
        buffer = io.StringIO()
        refused = False
        try:
            with redirect_stdout(buffer):
                self.release.check_promotion_delta(allow_delta=allow_delta)
        except SystemExit as exc:
            refused = exc.code != 0
        return refused, buffer.getvalue()


class TheGateRefuses(GateHarness):
    """The behaviour. None of these can pass against a disabled gate."""

    def test_a_shipped_file_is_refused(self):
        refused, out = self.run_gate(["ldm_core/diagnostics/info.py"])

        self.assertTrue(refused, "the gate allowed an unverified shipped file")
        self.assertIn("ldm_core/diagnostics/info.py", out)

    def test_version_stamps_alone_are_allowed(self):
        """constants.py and ldm.1 always differ; refusing on them would make
        the gate fire on every promotion and be disabled within a week."""
        refused, out = self.run_gate(
            ["ldm_core/constants.py", "ldm_core/resources/ldm.1"]
        )

        self.assertFalse(refused)
        self.assertIn("only version stamps changed", out)

    def test_tests_are_not_counted_as_shipped(self):
        """Test files do not go into the binary.

        This is the property that let LDM-#1770 and LDM-#1745 land during an
        in-flight verification without invalidating it.
        """
        refused, _ = self.run_gate(
            ["ldm_core/tests/test_anything.py", "ldm_core/constants.py"]
        )

        self.assertFalse(refused)

    def test_a_shipped_file_beside_excluded_ones_still_refuses(self):
        """The exclusions must not swallow a real change sitting next to them."""
        refused, out = self.run_gate(
            [
                "ldm_core/constants.py",
                "ldm_core/tests/test_anything.py",
                "ldm_core/pipelines/run.py",
            ]
        )

        self.assertTrue(refused)
        self.assertIn("ldm_core/pipelines/run.py", out)

    def test_the_override_lets_it_through(self):
        """Accepting a delta is legitimate -- it has to be deliberate."""
        refused, out = self.run_gate(["ldm_core/pipelines/run.py"], allow_delta=True)

        self.assertFalse(refused)
        self.assertIn("CHANGELOG", out)

    def test_no_pre_release_tag_is_not_a_refusal(self):
        """Nothing to compare against is not the same as a delta."""
        refused, out = self.run_gate(["ldm_core/pipelines/run.py"], tags=())

        self.assertFalse(refused)
        self.assertIn("No pre-release tag", out)

    def test_it_names_the_verified_tag_it_compared_against(self):
        """An operator's first question is "since when?"."""
        _, out = self.run_gate(
            ["ldm_core/constants.py"], tags=("v2.22.0-pre.9", "v2.21.0-pre.1")
        )

        self.assertIn("v2.22.0-pre.9", out)


class ItCatchesTheRealIncident(GateHarness):
    """Real git history, fed to the real function.

    A fixture would only show the gate reads its own inputs. These refs are the
    promotion that motivated it.
    """

    def real_delta(self, from_ref, to_ref):
        # cwd comes from REPO_ROOT, not from RELEASE_PY: deriving it from the
        # script under test couples this to wherever that file happens to live,
        # and a `git diff` run outside the repository returns nothing -- which
        # reads as skipTest("tags unavailable") rather than as a broken probe.
        return subprocess.run(  # nosec B603 - fixed argv, no shell
            ["git", "diff", "--name-only", from_ref, to_ref, "--", "ldm_core/"],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO_ROOT,
        ).stdout.split()

    def test_the_v2_22_0_promotion_would_have_been_refused(self):
        changed = self.real_delta("v2.22.0-pre.9", "v2.22.0")
        if not changed:
            self.skipTest("tags unavailable in this checkout")

        refused, out = self.run_gate(changed)

        self.assertTrue(refused, "the promotion that motivated the gate passes it")
        self.assertIn("ldm_core/diagnostics/info.py", out)

    def test_that_range_really_did_contain_test_files(self):
        """Otherwise the exclusion assertion above proves nothing."""
        changed = self.real_delta("v2.22.0-pre.9", "v2.22.0")
        if not changed:
            self.skipTest("tags unavailable in this checkout")

        self.assertTrue(
            any("/tests/" in f for f in changed),
            "no test files in that range, so exclusion is untested by it",
        )


class TheGateIsWiredIn(unittest.TestCase):
    """Source-order facts only -- what calling the function cannot show."""

    def setUp(self):
        self.src = RELEASE_PY.read_text(encoding="utf-8")

    def test_it_runs_before_anything_is_bumped(self):
        """Refusing after the bump would leave the branch half-promoted, which
        is the LDM-#1329 recovery problem this must not create."""
        call_at = self.src.index("        check_promotion_delta(allow_delta=")
        bump_at = self.src.index("Bumping version with logic:")

        self.assertLess(call_at, bump_at)

    def test_it_is_reached_only_on_the_promote_path(self):
        """A pre-release cut has nothing to compare against yet."""
        call_at = self.src.index("        check_promotion_delta(allow_delta=")
        promote_guard_at = self.src.index(
            "Promotion must be run from a 'release/' branch"
        )

        self.assertGreater(call_at, promote_guard_at)

    def test_an_override_exists_on_the_command_line(self):
        self.assertIn("--allow-promotion-delta", self.src)


class ImportingReleaseIsSafe(unittest.TestCase):
    """The loader above executes the module, so this must stay true.

    If someone adds top-level work to `release.py`, importing it here would run
    that work during the test suite -- against the developer's real repository.
    """

    def test_it_has_no_top_level_side_effects(self):
        import ast

        tree = ast.parse(RELEASE_PY.read_text(encoding="utf-8"))
        allowed = (
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.Import,
            ast.ImportFrom,
            ast.Assign,
            ast.AnnAssign,
            ast.Expr,  # docstring
            ast.If,  # the __main__ guard
        )
        offenders = [
            type(node).__name__ for node in tree.body if not isinstance(node, allowed)
        ]

        self.assertEqual(offenders, [], "release.py gained top-level side effects")

    def test_the_only_top_level_if_is_the_main_guard(self):
        import ast

        tree = ast.parse(RELEASE_PY.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.If):
                self.assertIn(
                    "__main__",
                    ast.dump(node.test),
                    "a top-level `if` that is not the __main__ guard would run on import",
                )


if __name__ == "__main__":
    unittest.main()
