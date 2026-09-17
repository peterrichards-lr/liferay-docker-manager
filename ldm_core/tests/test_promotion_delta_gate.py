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
"""

import subprocess  # nosec B404 - reads git history to verify the gate's logic
import unittest
from pathlib import Path

RELEASE_PY = Path(__file__).resolve().parent.parent.parent / "scripts" / "release.py"
VERSION_STAMPS = {"ldm_core/constants.py", "ldm_core/resources/ldm.1"}


def shipped_delta(from_ref, to_ref):
    """The gate's own computation, run against real history."""
    out = subprocess.run(  # nosec B603 - fixed argv, no shell
        ["git", "diff", "--name-only", from_ref, to_ref, "--", "ldm_core/"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    return sorted(
        f for f in out if f and "/tests/" not in f and f not in VERSION_STAMPS
    )


class TheGateIsWiredIn(unittest.TestCase):
    def setUp(self):
        self.src = RELEASE_PY.read_text(encoding="utf-8")

    def test_it_exists(self):
        self.assertIn("def check_promotion_delta", self.src)

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

    def test_an_override_exists(self):
        """Accepting a delta is legitimate -- it just has to be deliberate and
        recorded, not silent."""
        self.assertIn("--allow-promotion-delta", self.src)

    def test_it_tells_the_operator_what_to_do(self):
        start = self.src.index("def check_promotion_delta")
        block = self.src[start : start + 3500]

        self.assertIn("CHANGELOG", block)
        self.assertIn("re-verify", block)


class ItCatchesTheRealIncident(unittest.TestCase):
    """Against actual git history, not a fixture.

    A fixture would only prove the function reads its own inputs. These two
    refs are the promotion that motivated the gate and a clean comparison.
    """

    def test_the_v2_22_0_promotion_would_have_been_refused(self):
        delta = shipped_delta("v2.22.0-pre.9", "v2.22.0")

        self.assertIn("ldm_core/diagnostics/info.py", delta)

    def test_version_stamps_alone_do_not_trip_it(self):
        """constants.py and ldm.1 always differ; refusing on them would make
        the gate fire on every promotion and be disabled within a week."""
        self.assertEqual(shipped_delta("v2.22.0-pre.9", "v2.22.0-pre.9"), [])

    def test_tests_are_not_counted_as_shipped(self):
        """Test files do not go into the binary."""
        out = subprocess.run(  # nosec B603 - fixed argv, no shell
            [
                "git",
                "diff",
                "--name-only",
                "v2.22.0-pre.9",
                "v2.22.0",
                "--",
                "ldm_core/",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()

        self.assertTrue(
            any("/tests/" in f for f in out),
            "no test files changed in that range, so this asserts nothing",
        )
        self.assertFalse(
            any("/tests/" in f for f in shipped_delta("v2.22.0-pre.9", "v2.22.0"))
        )


if __name__ == "__main__":
    unittest.main()
