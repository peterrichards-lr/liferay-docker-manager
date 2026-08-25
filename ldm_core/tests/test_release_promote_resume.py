"""An interrupted --promote must be resumable (LDM-#1329).

`scripts/release.py --promote` does its work in order: bump VERSION to stable,
push the release branch, merge the tracking PR, **wait** for that merge, then
create and push the tag.

If the process dies during the wait, the bump and the merge have already
happened -- GitHub completes the merge on its own -- so `master` ends up
carrying a stable VERSION with no tag. No release is built and no assets are
published, while `constants.py` claims the version shipped.

Re-running used to be impossible: the guard refused any already-stable version,
which is right for preventing a double bump (that would burn a version number
under the Burn Rule) but left no supported way to finish the job. Recovery
required tagging by hand, which the release-orchestration skill prohibits for
agents and which this script exists to avoid.

Observed on 2026-08-25 promoting v2.17.0.

The decision is a pure function precisely so all three cases can be tested
without performing a live promotion -- being untestable is why the gap
survived.
"""

import importlib.util
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ldm_release",
    Path(__file__).resolve().parent.parent.parent / "scripts" / "release.py",
)
# `scripts/` is not an importable package, so release.py is loaded by path.
# The asserts narrow the Optional types for mypy and would fail loudly here
# rather than as an obscure AttributeError inside a test.
assert _SPEC is not None, "could not locate scripts/release.py"
assert _SPEC.loader is not None, "no loader for scripts/release.py"
_release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_release)


class TestClassifyStablePromotion(unittest.TestCase):
    def test_tag_already_exists_means_nothing_to_do(self):
        """A completed promotion must be a no-op, not an error.

        Re-running after success should exit cleanly rather than implying
        something is wrong.
        """
        self.assertEqual(
            _release.classify_stable_promotion(
                tag_exists=True, master_has_version=True
            ),
            "already_released",
        )

    def test_merged_but_untagged_resumes_at_tagging(self):
        """The #1329 case: interrupted after the merge, before the tag."""
        self.assertEqual(
            _release.classify_stable_promotion(
                tag_exists=False, master_has_version=True
            ),
            "resume_tag",
        )

    def test_stable_version_absent_from_master_is_still_an_error(self):
        """The guard that matters must survive.

        A stable version that master does not carry is not an interrupted
        promotion -- it is a branch in a state promotion cannot reason about.
        Resuming there would tag a commit master has never seen.
        """
        self.assertEqual(
            _release.classify_stable_promotion(
                tag_exists=False, master_has_version=False
            ),
            "not_promotable",
        )

    def test_tag_wins_even_if_master_lacks_the_version(self):
        """An existing tag is decisive.

        Whatever master looks like, the tag is immutable under the Burn Rule
        and re-tagging must never be attempted.
        """
        self.assertEqual(
            _release.classify_stable_promotion(
                tag_exists=True, master_has_version=False
            ),
            "already_released",
        )

    def test_every_combination_is_classified(self):
        """No input falls through to an implicit None."""
        valid = {"already_released", "resume_tag", "not_promotable"}
        for tag in (True, False):
            for master in (True, False):
                self.assertIn(
                    _release.classify_stable_promotion(tag, master),
                    valid,
                    f"unclassified: tag_exists={tag} master_has_version={master}",
                )


class TestResumeNeverRebumps(unittest.TestCase):
    """The burn-protection the original guard provided must not be lost."""

    def test_resume_path_does_not_reach_the_version_bump(self):
        import inspect

        source = inspect.getsource(_release.main)
        resume_block = source[source.index("classify_stable_promotion") :]
        resume_block = resume_block[: resume_block.index("Promoting pre-release")]

        # Checked against the bump's INVOCATION, not the bare flag: the resume
        # message legitimately mentions "--promote" in prose, and matching that
        # would pass or fail on wording rather than on behaviour.
        self.assertNotIn(
            "liferay_docker.py",
            resume_block,
            "the resume path must not shell out to the version bump again -- a "
            "second bump would burn a version number, which is exactly what the "
            "original refusal protected against",
        )
        self.assertIn("create_and_push_tag", resume_block)
        self.assertIn("return", resume_block)


if __name__ == "__main__":
    unittest.main()
