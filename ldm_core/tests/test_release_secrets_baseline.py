"""`.secrets.baseline` may ride a release commit only as line-number drift (LDM-#2512).

A patch release adds a RELEASE_ANNOUNCEMENTS entry to `ldm_core/constants.py`,
which moves the line numbers `.secrets.baseline` records. The bump's
clean-workspace check refused the rewritten baseline outright and neither
staging list carried it, so the banner, CHANGELOG and baseline had to go
through a separate PR first (v2.26.16-pre.1).

The gate is driven through `main()` for the workspace check, and directly for
the decision itself. `run_cmd` is replaced, so no git command, pre-commit or
push ever runs.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.tests.release_script_loader import load_release

BASELINE = {
    "version": "1.5.0",
    "plugins_used": [{"name": "KeywordDetector"}],
    "results": {
        "ldm_core/constants.py": [
            {
                "type": "Hex High Entropy String",
                "filename": "ldm_core/constants.py",
                "hashed_secret": "aaa",  # pragma: allowlist secret
                "is_verified": False,
                "line_number": 888,
            },
            {
                "type": "Hex High Entropy String",
                "filename": "ldm_core/constants.py",
                "hashed_secret": "bbb",  # pragma: allowlist secret
                "is_verified": False,
                "line_number": 889,
            },
        ]
    },
    "generated_at": "2026-11-23T16:22:04Z",
}


def _shifted(by=7):
    data = json.loads(json.dumps(BASELINE))
    for entry in data["results"]["ldm_core/constants.py"]:
        entry["line_number"] += by
    data["generated_at"] = "2026-11-24T09:00:00Z"
    return data


def _with_new_secret():
    data = _shifted()
    data["results"]["ldm_core/new.py"] = [
        {
            "type": "Secret Keyword",
            "filename": "ldm_core/new.py",
            "hashed_secret": "ccc",  # pragma: allowlist secret
            "is_verified": False,
            "line_number": 3,
        }
    ]
    return data


class _ReachedPullError(Exception):
    """Raised by the fake `run_cmd` once main() is past the workspace check."""


class _FakeGit:
    def __init__(
        self,
        porcelain,
        head_text,
        stop_at=("git", "pull"),
        branch="master",
        version="2.26.16",
    ):
        self.branch = branch
        self.version = version
        self.porcelain = porcelain
        self.head_text = head_text
        self.stop_at = list(stop_at)
        self.calls = []

    def __call__(self, cmd, cwd=None, check=True, capture=False, env=None):
        self.calls.append(list(cmd))

        class Res:
            returncode = 0
            stdout = ""
            stderr = ""

        res = Res()
        if cmd[: len(self.stop_at)] == self.stop_at:
            raise _ReachedPullError
        if "--print" in cmd:
            res.stdout = f"{self.version}\n"
        elif cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            res.stdout = f"{self.branch}\n"
        elif cmd[:3] == ["git", "status", "--porcelain"]:
            if "--" in cmd:
                res.stdout = "".join(
                    line + "\n"
                    for line in self.porcelain
                    if line[3:] == cmd[cmd.index("--") + 1]
                )
            else:
                res.stdout = "".join(line + "\n" for line in self.porcelain)
        elif cmd[:2] == ["git", "show"]:
            res.stdout = self.head_text
        return res


class TheDecision(unittest.TestCase):
    def setUp(self):
        self.release = load_release()

    def test_line_number_and_timestamp_drift_is_allowed(self):
        self.assertTrue(
            self.release.secrets_baseline_drift_only(
                json.dumps(BASELINE), json.dumps(_shifted())
            )
        )

    def test_a_new_finding_is_refused(self):
        self.assertFalse(
            self.release.secrets_baseline_drift_only(
                json.dumps(BASELINE), json.dumps(_with_new_secret())
            )
        )

    def test_a_changed_hash_is_refused(self):
        data = _shifted()
        data["results"]["ldm_core/constants.py"][0]["hashed_secret"] = (
            "zzz"  # pragma: allowlist secret
        )
        self.assertFalse(
            self.release.secrets_baseline_drift_only(
                json.dumps(BASELINE), json.dumps(data)
            )
        )

    def test_a_removed_finding_is_refused(self):
        data = _shifted()
        data["results"]["ldm_core/constants.py"].pop()
        self.assertFalse(
            self.release.secrets_baseline_drift_only(
                json.dumps(BASELINE), json.dumps(data)
            )
        )

    def test_an_unparsable_baseline_is_refused(self):
        self.assertFalse(
            self.release.secrets_baseline_drift_only(json.dumps(BASELINE), "{oops")
        )


class TheWorkspaceCheck(unittest.TestCase):
    """main() with `--bump patch` on master, up to the `git pull` after the
    workspace check."""

    def setUp(self):
        self.release = load_release()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _main(self, working, extra_porcelain=()):
        (self.root / ".secrets.baseline").write_text(
            json.dumps(working, indent=2), encoding="utf-8"
        )
        fake = _FakeGit(
            [" M .secrets.baseline", *extra_porcelain], json.dumps(BASELINE, indent=2)
        )
        with (
            patch.object(self.release, "run_cmd", fake),
            patch.object(self.release, "project_root", self.root),
            patch.object(sys, "argv", ["release.py", "--bump", "patch"]),
            patch("builtins.print") as printed,
        ):
            try:
                self.release.main()
                outcome = "returned"
            except _ReachedPullError:
                outcome = "reached"
            except SystemExit as e:
                outcome = f"exit {e.code}"
        out = "\n".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        return outcome, out

    def test_drift_passes_the_workspace_check(self):
        outcome, _out = self._main(_shifted())
        self.assertEqual(outcome, "reached")

    def test_a_new_secret_refuses_with_a_clear_message(self):
        outcome, out = self._main(_with_new_secret())
        self.assertEqual(outcome, "exit 1")
        self.assertIn(".secrets.baseline has changes beyond line-number drift", out)
        self.assertIn("must never ride a release commit", out)

    def test_other_files_are_still_refused(self):
        """Control: allowing the baseline did not loosen the rest of the list."""
        outcome, out = self._main(_shifted(), extra_porcelain=[" M ldm_core/cli.py"])
        self.assertEqual(outcome, "exit 1")
        self.assertIn("ldm_core/cli.py", out)


class TheBumpCommitCarriesIt(unittest.TestCase):
    """main() `--bump patch` through to the commit: the call site, not the helper."""

    def setUp(self):
        self.release = load_release()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _staged_by_bump(self, working):
        (self.root / ".secrets.baseline").write_text(
            json.dumps(working), encoding="utf-8"
        )
        porcelain = [] if working == BASELINE else [" M .secrets.baseline"]
        fake = _FakeGit(porcelain, json.dumps(BASELINE), stop_at=("git", "commit"))
        with (
            patch.object(self.release, "run_cmd", fake),
            patch.object(self.release, "project_root", self.root),
            patch.object(self.release, "run_pre_commit_checks"),
            patch.object(self.release, "print_behaviour_coverage_gate"),
            patch.object(
                self.release, "check_release_announcements", return_value=(True, "")
            ),
            patch.object(sys, "argv", ["release.py", "--bump", "patch"]),
            patch("builtins.print"),
            self.assertRaises(_ReachedPullError),
        ):
            self.release.main()
        return [
            path
            for call in fake.calls
            if call[:2] == ["git", "add"]
            for path in call[2:]
        ]

    def test_drift_is_staged_with_the_bump(self):
        self.assertIn(".secrets.baseline", self._staged_by_bump(_shifted()))

    def test_an_unchanged_baseline_is_not_staged(self):
        self.assertNotIn(".secrets.baseline", self._staged_by_bump(BASELINE))


class ThePromoteCommitCarriesIt(unittest.TestCase):
    """main() `--promote` from a release branch through to the commit."""

    def setUp(self):
        self.release = load_release()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _staged_by_promote(self, working):
        (self.root / ".secrets.baseline").write_text(
            json.dumps(working), encoding="utf-8"
        )
        fake = _FakeGit(
            [" M .secrets.baseline"],
            json.dumps(BASELINE),
            stop_at=("git", "commit"),
            branch="release/v2.26.16",
            version="2.26.16-pre.1",
        )
        with (
            patch.object(self.release, "run_cmd", fake),
            patch.object(self.release, "project_root", self.root),
            patch.object(self.release, "check_promotion_delta"),
            patch.object(
                self.release,
                "check_patch_announcement_for_promotion",
                return_value=(True, ""),
            ),
            patch.object(sys, "argv", ["release.py", "--promote"]),
            patch("builtins.print"),
            self.assertRaises(_ReachedPullError),
        ):
            self.release.main()
        return [
            path
            for call in fake.calls
            if call[:2] == ["git", "add"]
            for path in call[2:]
        ]

    def test_drift_is_staged_with_the_promotion(self):
        self.assertIn(".secrets.baseline", self._staged_by_promote(_shifted()))

    def test_a_new_secret_stops_the_promotion(self):
        with self.assertRaises(SystemExit):
            self._staged_by_promote(_with_new_secret())


class TheStagingDecision(unittest.TestCase):
    def setUp(self):
        self.release = load_release()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _check(self, porcelain, working):
        (self.root / ".secrets.baseline").write_text(
            json.dumps(working), encoding="utf-8"
        )
        fake = _FakeGit(porcelain, json.dumps(BASELINE))
        with (
            patch.object(self.release, "run_cmd", fake),
            patch.object(self.release, "project_root", self.root),
            patch("builtins.print"),
        ):
            return self.release.check_secrets_baseline()

    def test_unchanged_is_not_staged(self):
        self.assertFalse(self._check([], BASELINE))

    def test_drift_is_staged(self):
        self.assertTrue(self._check([" M .secrets.baseline"], _shifted()))

    def test_a_new_secret_stops_the_release(self):
        with self.assertRaises(SystemExit):
            self._check([" M .secrets.baseline"], _with_new_secret())


if __name__ == "__main__":
    unittest.main()
