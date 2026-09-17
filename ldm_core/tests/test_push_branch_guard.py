"""`agent_push.sh` must refuse to commit onto a protected branch (LDM-#1764).

On 2026-09-16 a commit landed on `master` through this wrapper. Only the
server-side ruleset stopped the push, and recovery took a checkpoint tag, a
branch move and a hard reset.

`release/*` is the worse case, because it is push-able: work committed there
merges into the next tag with nobody reviewing it. That is precisely what the
release skill's "fix on master first, then backport" rule exists to prevent,
and until now that rule was memory-only.

The guard runs FIRST, before the staging checks, because a refusal is cheaper
the earlier it happens -- and because the staging checks would otherwise
succeed and leave a commit on the protected branch that has to be unwound.

There is a deliberate escape hatch. Two legitimate commits are made directly
on a release branch -- the backport merge (which `agent_push.sh` cannot express
anyway) and the CHANGELOG entry for the release being cut -- so the guard is a
refusal with an override, not a prohibition.
"""

import subprocess  # nosec B404 - runs the wrapper under test
import tempfile
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().parent.parent.parent / "scripts" / "agent_push.sh"


class TheGuardIsDeclared(unittest.TestCase):
    def setUp(self):
        self.src = WRAPPER.read_text(encoding="utf-8")

    def test_it_covers_master_and_release_branches(self):
        self.assertIn("master|main|release/*", self.src)

    def test_it_runs_before_the_staging_checks(self):
        """A refusal after staging leaves a commit to unwind."""
        guard_at = self.src.index("Refusing to commit on")
        staging_at = self.src.index('STAGED_FILES="$(git diff --cached')

        self.assertLess(guard_at, staging_at)

    def test_it_names_the_way_out(self):
        """A refusal that does not say what to do instead is an obstacle."""
        self.assertIn("git checkout -B", self.src)

    def test_the_override_exists_for_deliberate_release_commits(self):
        self.assertIn("LDM_ALLOW_PROTECTED_BRANCH", self.src)


class TheGuardBehaves(unittest.TestCase):
    """Driven in a throwaway repo -- reading the script proves nothing about
    what it does when run."""

    def _run(self, branch, env_override=None):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run = lambda *a: subprocess.run(  # noqa: E731
                a, cwd=repo, capture_output=True, text=True, check=False
            )
            run("git", "init", "-q", ".")
            run("git", "config", "user.email", "t@t")
            run("git", "config", "user.name", "t")
            (repo / "scripts").mkdir()
            (repo / "scripts" / "agent_push.sh").write_text(
                WRAPPER.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (repo / "f.txt").write_text("x\n", encoding="utf-8")
            run("git", "add", "-A")
            run("git", "commit", "-qm", "init")
            run("git", "branch", "-M", branch)
            (repo / "f.txt").write_text("y\n", encoding="utf-8")
            run("git", "add", "f.txt")

            import os

            env = dict(os.environ)
            env.pop("LDM_ALLOW_PROTECTED_BRANCH", None)
            if env_override:
                env.update(env_override)
            return subprocess.run(  # nosec B603 - fixed path, no shell
                ["bash", "scripts/agent_push.sh", "test message"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
                env=env,
                timeout=120,
            )

    def test_master_is_refused(self):
        result = self._run("master")

        self.assertIn("Refusing to commit on 'master'", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_a_release_branch_is_refused(self):
        """The push-able one, so the more dangerous of the two."""
        result = self._run("release/v9.9.9")

        self.assertIn("Refusing to commit on 'release/v9.9.9'", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_a_feature_branch_is_not_refused(self):
        """The guard must not block ordinary work."""
        result = self._run("fix/123-thing")

        self.assertNotIn("Refusing to commit on", result.stdout)

    def test_the_override_downgrades_it_to_a_warning(self):
        result = self._run("master", {"LDM_ALLOW_PROTECTED_BRANCH": "1"})

        self.assertNotIn("Refusing to commit on", result.stdout)
        self.assertIn("protected branch", result.stdout)


if __name__ == "__main__":
    unittest.main()
