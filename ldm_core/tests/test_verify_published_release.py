"""The published release must be checked, not the build that made it (LDM-#1768).

CI proves the commit builds. Four defects during the v2.22.0 cycle were
invisible to it by construction, and every one was found by a human downloading
the artifact:

  * `v2.22.0-pre.7` published with no installer -- two commits lost to a
    merged-PR push meant `ci.yml` never gained the step, and CI was green
    because there was nothing new to test
  * the bundle shipped without `common/`, so the suite ran degraded and
    reported success anyway (LDM-#1718)
  * `MANIFEST.txt` then claimed it carried the activation key while listing
    contents that showed none (LDM-#1733) -- the test that should have caught
    that passed, because `collect()` walks the filesystem and a developer's
    checkout has the key sitting there untracked
  * the installer was missing from `checksums.txt`, so the first file a
    verifier runs was itself unverifiable

The checker is exercised below against the **real releases that carried those
defects**. A fixture would only prove it reads its own inputs; the historical
tags prove it catches what actually happened.
"""

import subprocess  # nosec B404 - runs the checker under test
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CHECKER = ROOT / "scripts" / "verify_published_release.py"
WORKFLOW = ROOT / ".github" / "workflows" / "verify-published-release.yml"


class TheCheckerDeclaresWhatMatters(unittest.TestCase):
    def setUp(self):
        self.src = CHECKER.read_text(encoding="utf-8")

    def test_it_requires_every_asset_ci_publishes(self):
        """A name here that the release lacks is the pre.7 failure."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        # Search for the end anchor FROM the start, not from the top: the
        # first "make_latest" in ci.yml is inside a comment 70 lines earlier,
        # so an unanchored index() yields an empty slice and every assertion
        # below then fails against "" for the wrong reason.
        start = ci.index("files: |")
        files_block = ci[start : ci.index("make_latest", start)]

        for asset in (
            "ldm-linux",
            "ldm-windows.exe",
            "verification-bundle.zip",
            "install_verification.sh",
            "install_verification.ps1",
        ):
            with self.subTest(asset=asset):
                self.assertIn(asset, files_block, "ci.yml no longer publishes this")
                self.assertIn(asset, self.src, "the checker does not require it")

    def test_it_reports_every_problem_not_just_the_first(self):
        """Three faults should cost one loop, not three."""
        self.assertIn("class Findings", self.src)
        self.assertIn("self.failures.append", self.src)

    def test_it_exits_non_zero_so_it_can_gate(self):
        self.assertIn("return 0 if findings.ok else 1", self.src)


class ItCatchesTheRealDefects(unittest.TestCase):
    """Against published history. Needs network, so it is skipped when the
    release cannot be reached rather than failing for the wrong reason."""

    def _run(self, tag):
        return subprocess.run(  # nosec B603 - fixed argv, no shell
            ["python3", str(CHECKER), "--tag", tag, "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

    def _findings(self, tag):
        import json

        result = self._run(tag)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            self.skipTest(f"could not reach release {tag}")
        if payload["failures"] and "could not download" in payload["failures"][0]:
            self.skipTest(f"could not download release {tag}")
        return payload

    def test_the_installerless_release_is_caught(self):
        """v2.22.0-pre.7 shipped with no installer and CI stayed green."""
        payload = self._findings("v2.22.0-pre.7")

        self.assertFalse(payload["ok"])
        self.assertTrue(
            any("install_verification.sh" in f for f in payload["failures"]),
            f"did not flag the missing installer: {payload['failures']}",
        )

    def test_the_dishonest_manifest_is_caught(self):
        """v2.22.0-pre.5 predates the LDM-#1733 fix."""
        payload = self._findings("v2.22.0-pre.5")

        self.assertFalse(payload["ok"])
        self.assertTrue(
            any("activation key" in f for f in payload["failures"]),
            f"did not flag the manifest: {payload['failures']}",
        )

    def test_a_good_release_passes(self):
        """A checker that fails everything would be useless and ignored."""
        payload = self._findings("v2.23.0-pre.2")

        self.assertTrue(payload["ok"], f"unexpected failures: {payload['failures']}")


class TheWorkflowRunsIt(unittest.TestCase):
    """A checker nobody runs is the same as no checker."""

    def setUp(self):
        self.src = WORKFLOW.read_text(encoding="utf-8")

    def test_it_fires_when_a_release_is_published(self):
        """Not during the release job: the point is to check what GitHub
        actually serves, which cannot be known until it serves it."""
        self.assertIn("release:", self.src)
        self.assertIn("published", self.src)

    def test_it_can_be_run_by_hand_against_any_tag(self):
        self.assertIn("workflow_dispatch", self.src)
        self.assertIn("inputs.tag", self.src)

    def test_it_invokes_the_checker(self):
        self.assertIn("verify_published_release.py", self.src)


if __name__ == "__main__":
    unittest.main()
