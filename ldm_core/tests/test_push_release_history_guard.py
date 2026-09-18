"""`agent_push.sh` must refuse a branch cut from release history (LDM-#1801).

`git checkout -b <feature>` while standing on `release/*` inherits every release
commit -- the version bump, the CHANGELOG entry, the compatibility sync, the
verification reports. The PR then arrives as tens of files instead of the
handful actually touched, and conflicts on files the change never went near,
because `master` already has that content via the promotion squash.

## Why this is a check and not a note

It is already documented in `release-orchestration/SKILL.md`, which names the
tell and gives the two commands that catch it. It has happened twice anyway:

- a fix branch arrived as 33 files across 10 commits instead of 6 files in 1;
- LDM-#1798 reached `pr-sprawl-check` as 24 files, carrying the whole
  `v2.23.0-pre.5` release commit -- in a session where those two commands had
  been run before every other PR that day.

That is the same argument that turned LDM-#1754 into #1765 and the CHANGELOG
ratchet into #1758: a step that depends on remembering is not a control.

## Why the signal is commit reachability, not file names

A commit in `origin/master..HEAD` that is *also* reachable from a release branch
is on the release branch, not on master, and in your feature branch -- it can
only have arrived by branching off release history. Matching on file names would
misfire on a legitimate `CHANGELOG.md` edit.

`pr-sprawl-check` is not this check: it fires only for branches classified as
bugfixes, only above 10 files, and only after the PR exists and CI has run. A
release-history branch with 8 files passes it while still carrying the bump.
"""

import os
import subprocess  # nosec B404 - runs the wrapper under test
import tempfile
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().parent.parent.parent / "scripts" / "agent_push.sh"


class TheGuardRefusesReleaseHistory(unittest.TestCase):
    """Drives the real wrapper in a throwaway repository with a real `origin`.

    A real remote is required rather than convenient: the guard reasons about
    `origin/master` and `refs/remotes/origin/release/*`, which do not exist in a
    repository that has never had a remote -- and a guard that silently does
    nothing without one would be worse than no guard.
    """

    def _run(self, *, branch_from_release):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / "origin.git"
            repo = root / "work"

            def git(*args, cwd=repo):
                return subprocess.run(  # nosec B603 B607 - fixed argv, no shell
                    ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
                )

            subprocess.run(  # nosec B603 B607
                ["git", "init", "-q", "--bare", str(origin)],
                capture_output=True,
                check=False,
            )
            subprocess.run(  # nosec B603 B607
                ["git", "clone", "-q", str(origin), str(repo)],
                capture_output=True,
                check=False,
            )
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")

            (repo / "scripts").mkdir(parents=True, exist_ok=True)
            (repo / "scripts" / "agent_push.sh").write_text(
                WRAPPER.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (repo / "f.txt").write_text("x\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-qm", "init")
            git("branch", "-M", "master")
            git("push", "-q", "origin", "master")

            # A release branch with a release-shaped commit on it, exactly as
            # `release.py` would leave one.
            git("checkout", "-qb", "release/v9.9.9")
            (repo / "CHANGELOG.md").write_text("## [v9.9.9-pre.1]\n", encoding="utf-8")
            git("add", "-A")
            git(
                "commit",
                "-qm",
                "chore(release): bump version to v9.9.9-pre.1 [release]",
            )
            git("push", "-q", "origin", "release/v9.9.9")

            base = "release/v9.9.9" if branch_from_release else "master"
            git("checkout", "-q", base)
            git("checkout", "-qb", "fix/9999-something")

            (repo / "f.txt").write_text("y\n", encoding="utf-8")
            git("add", "f.txt")

            env = dict(os.environ)
            env.pop("LDM_ALLOW_PROTECTED_BRANCH", None)
            return subprocess.run(  # nosec B603 - fixed path, no shell
                ["bash", "scripts/agent_push.sh", "test message"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
                env=env,
                timeout=180,
            )

    def test_a_branch_cut_from_a_release_branch_is_refused(self):
        result = self._run(branch_from_release=True)

        self.assertIn("branched from release history", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_it_names_the_commits_that_came_along(self):
        """ "Your branch is wrong" is not actionable; naming the release commit
        that rode in with it is."""
        result = self._run(branch_from_release=True)

        self.assertIn("[release]", result.stdout)

    def test_it_says_rebuild_and_not_merge(self):
        """Resolving the conflicts merges the release commits a second time,
        which is the deeper trap the skill warns about."""
        result = self._run(branch_from_release=True)

        self.assertIn("git branch -f", result.stdout)
        self.assertIn("cherry-pick", result.stdout)

    def test_a_branch_cut_from_master_is_not_refused(self):
        """A guard that fires on a correct branch gets disabled within a week.

        Asserted on the guard's own message rather than the exit code: the
        wrapper goes on to run the full quality gate, which cannot pass in a
        throwaway repository with no pre-commit config.
        """
        result = self._run(branch_from_release=False)

        self.assertNotIn("branched from release history", result.stdout)


if __name__ == "__main__":
    unittest.main()
