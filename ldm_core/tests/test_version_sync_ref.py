"""check_version_sync must be able to read a git ref, not just the tree (#1498).

`release.py` runs its quality gate BEFORE the bump, so nothing re-checked
between the version rewrite and `git tag`. That is how v2.19.0-pre.2 was tagged
with constants.py at pre.2 and ldm.1 at pre.1: the man page was rewritten but
never staged (#1491), so the COMMIT was wrong while the working tree was
entirely consistent.

A working-tree check could not have caught it, and did not. The guard therefore
has to read what is being tagged.
"""

import subprocess  # nosec B404 - fixed argv, no shell
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _ROOT / "scripts" / "check_version_sync.py"


def _run(*args, cwd=None):
    return subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd or _ROOT,
    )


class TestRefMode(unittest.TestCase):
    def test_working_tree_mode_still_works(self):
        res = _run()
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertIn("working tree", res.stdout)

    def test_ref_mode_reads_the_committed_tree(self):
        res = _run("--ref", "HEAD")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertIn("HEAD", res.stdout)

    def test_the_two_modes_are_distinguishable_in_output(self):
        """A reader must be able to tell which was checked.

        If both printed the same thing, a release log could not show whether
        the tagged commit or merely the disk was verified.
        """
        self.assertNotEqual(_run().stdout, _run("--ref", "HEAD").stdout)

    def test_unknown_ref_fails_rather_than_passing_vacuously(self):
        """Missing files must not read as 'nothing to disagree about'."""
        res = _run("--ref", "refs/heads/definitely-not-a-branch-1498")
        self.assertNotEqual(
            0,
            res.returncode,
            "an unresolvable ref must fail, not silently report success",
        )


class TestReleaseGuardIsWired(unittest.TestCase):
    """Every tag site must call the guard (#1498).

    release.py creates tags in three places -- preview, pre-release on the
    release branch, and create_and_push_tag for stable. The pre-release one is
    what burnt v2.19.0-pre.2, so covering only the stable path would leave the
    proven failure unguarded.
    """

    def test_every_tag_creation_is_preceded_by_the_guard(self):
        src = (_ROOT / "scripts" / "release.py").read_text(encoding="utf-8")
        tag_sites = src.count('"git", "tag", "-a"')
        guards = src.count("assert_versions_consistent_at(")
        self.assertGreaterEqual(tag_sites, 3, "expected three tag creation sites")
        # One definition plus one call per site.
        self.assertGreaterEqual(
            guards,
            tag_sites + 1,
            f"{tag_sites} tag sites but only {guards - 1} guard calls -- "
            "a tag can be created without checking the commit it points at",
        )


if __name__ == "__main__":
    unittest.main()
