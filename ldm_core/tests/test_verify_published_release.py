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


class AMissingGhIsAFindingNotACrash(unittest.TestCase):
    """LDM-#1869: a missing `gh` must not be an uncaught `FileNotFoundError`.

    Runs the real checker as a subprocess against a PATH that cannot resolve
    `gh`, rather than relying on the host machine happening to lack it (or
    happening to have it, which is what CI does) -- so this is deterministic
    on both a developer's machine and GitHub Actions, per the same PATH
    argument that makes environment-dependent probes untrustworthy elsewhere
    in this repo.
    """

    def test_a_missing_gh_is_reported_as_a_finding_and_exits_non_zero(self):
        import json
        import os
        import sys

        env = dict(os.environ)
        # Only the directory holding the interpreter we are about to invoke.
        # `gh` cannot live there, so subprocess.run(["gh", ...]) inside the
        # checker is guaranteed to raise FileNotFoundError regardless of
        # whether this host actually has `gh` installed.
        env["PATH"] = str(Path(sys.executable).resolve().parent)

        result = subprocess.run(  # nosec B603 - fixed argv, no shell
            [sys.executable, str(CHECKER), "--tag", "v0.0.0-unreachable", "--json"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=60,
        )

        self.assertNotEqual(
            result.returncode,
            0,
            f"checker exited 0 with gh unresolvable: stdout={result.stdout!r}",
        )
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            self.fail(
                f"checker did not emit JSON when gh was unresolvable: "
                f"stderr={result.stderr!r} stdout={result.stdout!r}"
            )
        self.assertFalse(payload["ok"])
        self.assertTrue(
            any(
                "required tool not found" in f and "gh" in f
                for f in payload["failures"]
            ),
            f"missing gh was not reported as a finding: {payload['failures']}",
        )


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
            # The checker crashed before it could emit JSON at all -- a bug in
            # the checker, not an unreachable network. This must FAIL and name
            # the cause, never disappear as a skip (LDM-#1869).
            self.fail(
                f"the checker produced no JSON for {tag} (exit "
                f"{result.returncode}): stderr={result.stderr.strip()!r} "
                f"stdout={result.stdout.strip()!r}"
            )
        first_failure = payload["failures"][0] if payload["failures"] else ""
        if first_failure.startswith("required tool not found"):
            # A missing `gh` means this environment cannot run the checker at
            # all. That is a real failure of this test's ability to verify
            # anything -- not "the network is unreachable" -- so it must FAIL,
            # not skip (LDM-#1869).
            self.fail(f"the checker could not run against {tag}: {first_failure}")
        if "could not download" in first_failure:
            self.skipTest(f"could not download release {tag}: {first_failure}")
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
    """A checker nobody runs is the same as no checker.

    These assert the standalone workflow's shape. They do NOT establish that
    published releases get checked -- see TheCheckerActuallyRuns below, and
    LDM-#1774 for why this class alone was misleading for several releases.
    """

    def setUp(self):
        self.src = WORKFLOW.read_text(encoding="utf-8")

    def test_it_fires_when_a_release_is_published(self):
        """Kept because it still fires for a release published by a human or a
        PAT. It can never fire for one CI published: GitHub does not start a
        workflow run from an event triggered by GITHUB_TOKEN (LDM-#1774)."""
        self.assertIn("release:", self.src)
        self.assertIn("published", self.src)

    def test_it_can_be_run_by_hand_against_any_tag(self):
        self.assertIn("workflow_dispatch", self.src)
        self.assertIn("inputs.tag", self.src)

    def test_it_invokes_the_checker(self):
        self.assertIn("verify_published_release.py", self.src)


class TheCheckerActuallyRuns(unittest.TestCase):
    """LDM-#1774: the standalone workflow could never fire.

    GitHub does not start a workflow run from an event triggered by
    GITHUB_TOKEN, and `ci.yml` publishes the release with exactly that token,
    so `on: release: [published]` never reached it. Measured: zero runs of that
    workflow had ever existed, across every release since it was added, while
    it read as though published releases were being verified automatically.

    That is the third "wired, green, never executes" defect of this cycle, after
    the #1618 module rung and the #1770 gates. So the property worth asserting
    is not that a workflow exists -- it is that the release job itself runs the
    checker.
    """

    def setUp(self):
        self.ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

    def test_the_release_job_runs_the_checker(self):
        self.assertIn("verify_published_release.py", self.ci)

    def test_it_runs_after_the_release_is_created(self):
        """Before publication there is nothing to download."""
        created_at = self.ci.index("Create GitHub Release")
        checked_at = self.ci.index("verify_published_release.py")
        self.assertLess(
            created_at,
            checked_at,
            "the checker runs before the release exists, so it can only fail",
        )

    def test_the_checker_needs_no_pip_install(self):
        """The release job installs nothing.

        This is the constraint that actually broke: the first version imported
        `ldm_core.utils` for `safe_extract`, which pulls in `requests`, so the
        step would have died on import in that job. Asserted by parsing the
        imports rather than by reading the source, because a comment saying
        "standard library only" is not a check.
        """
        import ast
        import sys

        tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])

        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib is None:  # pragma: no cover - Python < 3.10
            self.skipTest("sys.stdlib_module_names unavailable")

        third_party = sorted(
            m for m in modules if m not in stdlib and m != "__future__"
        )
        self.assertEqual(
            third_party,
            [],
            f"the checker imports {third_party}, which the release job does not install",
        )

    def test_it_does_not_extract_the_downloaded_archive(self):
        """The safest extraction is the one that does not happen.

        Members are read straight out of the zip, so a tampered release has no
        path to traverse. This also keeps the script dependency-free, since the
        repo's `enforce-safe-extract` rule would otherwise require importing
        `ldm_core.utils`.
        """
        src = CHECKER.read_text(encoding="utf-8")
        self.assertNotIn("extractall(", src)
        self.assertNotIn("safe_extract(", src)
        self.assertIn("archive.read(name)", src)


if __name__ == "__main__":
    unittest.main()
