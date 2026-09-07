"""Guards for LDM-#1611: a failed platform verification MUST be reported as a failure.

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

import unittest
from pathlib import Path
from typing import Any, ClassVar

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "scheduled-verification.yml"

# The jobs whose whole purpose is to report a verification outcome.
VERIFY_JOBS = ("verify-linux", "verify-windows", "verify-macos")


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


if __name__ == "__main__":
    unittest.main()
