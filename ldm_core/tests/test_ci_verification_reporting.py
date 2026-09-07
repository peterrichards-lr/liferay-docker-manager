"""Guards on the `LDM Platform Verification (Multi-OS)` workflow's honesty.

Two issues, one premise: the compatibility matrix is built from this workflow's
output, so a job that misreports what it verified manufactures the evidence.
LDM-#1611 is a verification that failed and said it passed; LDM-#1625 is a
verification that passed and was never published.

LDM-#1611: a failed platform verification MUST be reported as a failure.

The `LDM Platform Verification (Multi-OS)` workflow reported `success` on the
`v2.21.0-pre.2` and `v2.21.0` tag runs while uploading three reports whose
filenames ended `-fail.txt` (both Windows shells and macOS/Colima). Two
independent defects produced that:

* `scripts/verify_e2e_refactor.ps1` caught its own `throw`, wrote the `-fail`
  report, and then fell off the end of the script -- and PowerShell exits 0
  when an exception has been handled.
* `.github/workflows/scheduled-verification.yml` marked the macOS verification
  step `continue-on-error: true`.

Both were invisible: the run was green and the compatibility record showed the
platforms as verified.

Scope: this module covers the **workflow** half only, and does so by parsing
the YAML and asserting on the resulting structure -- a workflow file cannot be
executed locally, so its shape is the strongest available signal.

The script half is NOT guarded here, and deliberately not by matching source
text: a text match passes whenever the text survives, including when the
behaviour is gone, and on an issue whose whole premise is that a green signal
could not be trusted that would repeat the mistake. The .ps1's exit status is
*executed* instead, in real PowerShell, by TestPowerShellSuiteExitStatus in
test_verify_scripts.py -- which is where this repository's pwsh-invocation and
skip-if-absent machinery already lives.
"""

import fnmatch
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "scheduled-verification.yml"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sync_compatibility  # noqa: E402

# The jobs whose whole purpose is to report a verification outcome.
VERIFY_JOBS = ("verify-linux", "verify-windows", "verify-macos")

# LDM-#1625: the `Platform:` line each verify-linux arm actually emitted, read
# out of the artifacts of run 34063433007 (the v2.21.0 stable tag run, the last
# one before the arms were labelled). These are the strings
# sync_compatibility.py has to turn into a row name, and they are nothing like
# the matrix key: `rockylinux` reports "Rocky Linux 9.3 (Blue Onyx)" and
# resolves to a `rocky-linux-` slug, while its raw report file is named
# `rocky-9.3`. Guessing any of the three from the other is how the two lists in
# the sync job drifted apart in the first place.
OBSERVED_PLATFORM_LINES = {
    "ubuntu": "Ubuntu 24.04.4 LTS",
    "fedora": "Fedora Linux 44 (Container Image)",
    "rockylinux": "Rocky Linux 9.3 (Blue Onyx)",
    "alpine": "Alpine Linux v3.24",
    "debian": "Debian GNU/Linux 12 (bookworm)",
}

# The raw report filenames from that same run, as uploaded. The Linux five must
# be selected for publication; the macOS and Windows three must not be -- those
# rows are curated by hand, and the sync job has never published them.
OBSERVED_LINUX_REPORT_FILENAMES = (
    "verify-linux-workstation-alpine-3.24.1-native-docker-20260906-221506-pass.txt",
    "verify-linux-workstation-debian-12-native-docker-20260906-221605-pass.txt",
    "verify-linux-workstation-fedora-44-native-docker-20260906-221618-pass.txt",
    "verify-linux-workstation-rocky-9.3-native-docker-20260906-221524-pass.txt",
    "verify-linux-workstation-ubuntu-24.04-native-docker-20260906-224249-pass.txt",
)
OBSERVED_NON_LINUX_REPORT_FILENAMES = (
    # From that run these were -fail reports, so the status half of the
    # selector already excluded them by accident. Stated as passes here
    # deliberately: the exclusion must hold on the day those platforms go
    # green, not only while they are broken.
    "verify-apple-silicon-macos-16-tahoe-colima-20260906-221533-pass.txt",
    "verify-windows-pc-windows-11-powershell-5.1-docker-desktop-20260906-221547-pass.txt",
    "verify-windows-pc-windows-11-powershell-7-docker-desktop-20260906-221528-pass.txt",
)


def _synthetic_report(directory, platform_line, env_label):
    """A report shaped like the ones verify_e2e_refactor.sh --ci writes.

    Only the header fields sync_compatibility.py reads are reproduced, with the
    real `Platform:` string and the `Env Label:` line LDM-#1614 added. Written
    to a temporary directory: the committed reports under
    references/verification-results/ are immutable evidence, and this must
    neither read nor rewrite them.
    """
    path = directory / "verify-raw-20260906-000000-pass.txt"
    path.write_text(
        "\n".join(
            [
                "=== LDM BINARY VERIFICATION REPORT ===",
                "Timestamp:    Sun Sep  6 22:16:18 UTC 2026",
                "Hostname:     runner",
                f"Platform:     {platform_line}",
                f"Env Label:    {env_label}",
                "Binary:       /usr/local/bin/ldm",
                f"Version:      ldm {sync_compatibility.VERSION}",
                f"Script Ver:   {sync_compatibility.VERSION}",
                "Docker:       28.0.4",
                "",
                "ALL E2E VERIFICATIONS PASSED!",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


class TestVerificationFailuresAreReported(unittest.TestCase):
    """LDM-#1611: the verification jobs must not be able to hide a failure."""

    workflow: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    def _steps(self, job_name):
        self.assertIn(
            job_name,
            self.workflow["jobs"],
            f"Job '{job_name}' has disappeared from {WORKFLOW.name}; this guard "
            "needs updating rather than deleting.",
        )
        return self.workflow["jobs"][job_name].get("steps", [])

    def test_verification_steps_do_not_continue_on_error(self):
        """A step that runs the suite must let its failure fail the job.

        `continue-on-error: true` on the macOS suite step is the entirety of
        LDM-#1611 on that platform: the script propagates its own failure
        correctly, and the workflow discarded it.
        """
        for job_name in VERIFY_JOBS:
            for step in self._steps(job_name):
                name = step.get("name", "<unnamed>")
                if "Verification Suite" not in name:
                    continue
                self.assertNotEqual(
                    step.get("continue-on-error"),
                    True,
                    f"LDM-#1611 regression: step '{name}' in job '{job_name}' "
                    "carries continue-on-error: true, so a failed verification "
                    "would report success again.",
                )

    def test_every_verify_job_asserts_its_own_report(self):
        """Defence in depth: cross-check the report, not just the exit status.

        The exit status has already been lost once on two platforms. The report
        names its own outcome in its filename and carries a completion marker,
        so each job re-checks that independently.
        """
        for job_name in VERIFY_JOBS:
            steps = self._steps(job_name)
            asserts = [
                s
                for s in steps
                if "ALL E2E VERIFICATIONS PASSED" in (s.get("run") or "")
                and "-fail" in (s.get("run") or "")
            ]
            self.assertTrue(
                asserts,
                f"Job '{job_name}' has no step cross-checking the verification "
                "report for a '-fail' filename and the 'ALL E2E VERIFICATIONS "
                "PASSED' marker (LDM-#1611).",
            )
            for step in asserts:
                self.assertEqual(
                    step.get("if"),
                    "always()",
                    f"The report cross-check in '{job_name}' must run with "
                    "if: always(), or it is skipped on exactly the runs it "
                    "exists to catch.",
                )

    def test_windows_step_propagates_the_child_shell_exit_code(self):
        """The Windows step launches a *second* shell; its status must be forwarded.

        Relying on the runner's implicit `exit $LASTEXITCODE` epilogue leaves
        the whole chain resting on an undeclared default.
        """
        steps = self._steps("verify-windows")
        suite = [s for s in steps if "Verification Suite" in (s.get("name") or "")]
        self.assertTrue(suite, "verify-windows no longer runs the suite at all.")
        for step in suite:
            run = step.get("run") or ""
            self.assertIn(
                "$LASTEXITCODE",
                run,
                "The Windows suite step must capture and re-raise the exit code "
                "of the shell it launches (LDM-#1611).",
            )


class TestEveryLinuxArmReachesTheCompatibilityMatrix(unittest.TestCase):
    """LDM-#1625: the sync job must publish every arm verify-linux verifies.

    `verify-linux` runs five distros. `sync-compatibility` downloaded two
    artifacts -- ubuntu and fedora -- and its pre-copy cleanup knew the same
    two slugs. Debian, Rocky and Alpine therefore ran on every stable tag,
    passed, uploaded a report, and had it discarded by the job whose purpose is
    to publish it. Nothing went red: a list that has drifted out of step with
    another list looks exactly like a list that has not.

    Both halves are asserted against the *parsed* YAML and against real data,
    never against the file's text. A text match passes whenever the string
    survives, which is the mistake this repository keeps being caught by. The
    canonical report name each arm produces is therefore derived by running
    `sync_compatibility.get_report_metadata` over the `Platform:` line that arm
    actually emitted, rather than being written down next to the glob that has
    to match it -- the drift the issue describes is precisely two hand-written
    lists disagreeing.
    """

    workflow: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    def _job(self, name):
        self.assertIn(
            name,
            self.workflow["jobs"],
            f"Job '{name}' has disappeared from {WORKFLOW.name}; this guard "
            "needs updating rather than deleting.",
        )
        return self.workflow["jobs"][name]

    def _distros(self):
        matrix = self._job("verify-linux")["strategy"]["matrix"]
        return list(matrix["distro"])

    def _uploaded_artifact_names(self):
        """The artifact name each matrix arm actually uploads under."""
        uploads = [
            s
            for s in self._job("verify-linux")["steps"]
            if str(s.get("uses", "")).startswith("actions/upload-artifact")
        ]
        self.assertTrue(uploads, "verify-linux no longer uploads its reports.")
        names: list[str] = []
        for step in uploads:
            template = (step.get("with") or {}).get("name", "")
            self.assertIn(
                "${{ matrix.distro }}",
                template,
                f"Artifact name '{template}' no longer varies by matrix arm, so "
                "this guard can no longer tell what the sync job must fetch.",
            )
            names.extend(
                template.replace("${{ matrix.distro }}", distro)
                for distro in self._distros()
            )
        return names

    def _download_selectors(self):
        """Every artifact name or glob the sync job fetches."""
        selectors = []
        for step in self._job("sync-compatibility")["steps"]:
            if not str(step.get("uses", "")).startswith("actions/download-artifact"):
                continue
            with_ = step.get("with") or {}
            # Neither `name` nor `pattern` means "every artifact in the run".
            selectors.append(with_.get("pattern") or with_.get("name") or "*")
        self.assertTrue(
            selectors, "sync-compatibility downloads no verification artifacts at all."
        )
        return selectors

    def _publish_step(self):
        steps = [
            s
            for s in self._job("sync-compatibility")["steps"]
            if "references/verification-results/" in (s.get("run") or "")
            and "rm -f" in (s.get("run") or "")
        ]
        self.assertEqual(
            len(steps),
            1,
            "Expected exactly one step in sync-compatibility that clears and "
            "replaces the committed Linux reports.",
        )
        return steps[0]

    def test_the_observed_platform_data_still_covers_the_matrix(self):
        """A sixth arm must not silently fall outside this guard's reach.

        The next distro added to `matrix.distro` needs its observed `Platform:`
        line recorded here, or the two tests below would keep passing while
        covering one arm fewer than the workflow runs -- the same shape of
        omission as the bug itself.
        """
        self.assertEqual(
            sorted(self._distros()),
            sorted(OBSERVED_PLATFORM_LINES),
            "verify-linux's matrix and the recorded platform lines have "
            "diverged. Add the new arm's real `Platform:` string (read it out "
            "of that arm's verification-results artifact) to "
            "OBSERVED_PLATFORM_LINES.",
        )

    def test_every_arms_artifact_is_downloaded_by_the_sync_job(self):
        """The first half of LDM-#1625: three artifacts were never fetched."""
        selectors = self._download_selectors()
        for artifact in self._uploaded_artifact_names():
            self.assertTrue(
                any(fnmatch.fnmatchcase(artifact, s) for s in selectors),
                f"Artifact '{artifact}' is uploaded by a verify-linux arm but "
                f"matches none of the sync job's download selectors "
                f"{selectors!r}, so that arm's pass is discarded (LDM-#1625).",
            )

    def test_the_cleanup_covers_the_report_every_arm_produces(self):
        """The second half: the `rm -f` set knew only two of the five slugs.

        A slug left out is not merely un-cleaned -- the stale copy stays and
        the fresh one lands beside it, so one environment yields two rows, one
        of which asserts an older run than it appears to.
        """
        run = self._publish_step()["run"]
        globs = [
            Path(g).name
            for g in re.findall(r"rm -f\s+(references/verification-results/\S+)", run)
        ]
        self.assertTrue(
            globs,
            "The publish step no longer clears any committed Linux report; a "
            "distro whose version moved would leave two rows.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            for distro, platform_line in sorted(OBSERVED_PLATFORM_LINES.items()):
                report = _synthetic_report(Path(tmp), platform_line, distro)
                meta = sync_compatibility.get_report_metadata(report)
                canonical = f"verify-{meta['internal_slug']}-{meta['status_slug']}.txt"
                self.assertTrue(
                    any(fnmatch.fnmatchcase(canonical, g) for g in globs),
                    f"The '{distro}' arm publishes '{canonical}', which matches "
                    f"none of the cleanup globs {globs!r}. Its previous row is "
                    "left behind next to the fresh one (LDM-#1625).",
                )

    def test_the_publish_step_selects_linux_reports_and_only_those(self):
        """macOS and Windows rows are curated by hand and must stay that way.

        The download step fetches every `verification-results-*` artifact, so
        the Linux-only invariant now rests entirely on this selector. It held
        by accident on the runs observed so far, because those two platforms
        were producing `-fail` reports; it has to hold on the day they pass.
        """
        run = self._publish_step()["run"]
        selectors = re.findall(r"-name\s+['\"]([^'\"]+)['\"]", run)
        self.assertTrue(
            selectors,
            "The publish step no longer selects reports by filename, so this "
            "guard cannot tell which platforms it would publish.",
        )
        for filename in OBSERVED_LINUX_REPORT_FILENAMES:
            self.assertTrue(
                any(fnmatch.fnmatchcase(filename, s) for s in selectors),
                f"Real verify-linux report '{filename}' matches none of "
                f"{selectors!r}, so its pass would not be published.",
            )
        for filename in OBSERVED_NON_LINUX_REPORT_FILENAMES:
            self.assertFalse(
                any(fnmatch.fnmatchcase(filename, s) for s in selectors),
                f"'{filename}' is a manually curated platform's report and "
                f"matches {selectors!r}; this job must not publish it.",
            )


if __name__ == "__main__":
    unittest.main()
