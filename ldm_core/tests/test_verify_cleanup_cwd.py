"""Cleanup must leave the project directory before deleting it (LDM-#1436).

Both verification scripts change into the project directory during the run and
do not return. Cleanup then asked LDM to delete the directory the shell was
sitting in, and LDM refused -- correctly:

    Safety Violation: Cannot delete current working directory or its parent:
    .../e2e-work-dir-59746/ldm-smoke-test-59746

That guard must not be worked around. Deleting the shell's own cwd leaves the
caller in a directory that no longer exists; the scripts were what was wrong.

Confirmed on two platforms independently, both on v2.18.0-pre.11 runs that
otherwise reported ALL E2E VERIFICATIONS PASSED:

  - macOS 16 / Colima      .../e2e-work-dir-59746/ldm-smoke-test-59746
  - Windows 11 / WSL2      /home/prichards/ldm-verify/e2e-work-dir-48113/...

It failed on pre.8, pre.9 and pre.10 too. The cause stayed unknown for three
release cycles because the output was discarded -- #1255 recovered the exit
code, #1440 the message, and the message identified it on the first run that
printed one.
"""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SH = REPO / "scripts" / "verify_e2e_refactor.sh"
PS1 = REPO / "scripts" / "verify_e2e_refactor.ps1"


class TestCleanupLeavesProjectDir(unittest.TestCase):
    def test_bash_cleanup_returns_to_original_pwd_before_removing(self):
        """The `cd` must precede the removal, not merely exist somewhere."""
        src = SH.read_text()
        body = src[src.index("cleanup_test_projects() {") :]

        cd_at = body.find('cd "$ORIGINAL_PWD"')
        rm_at = body.find("--delete")

        self.assertNotEqual(-1, cd_at, "cleanup never returns to $ORIGINAL_PWD")
        self.assertNotEqual(-1, rm_at, "cleanup no longer removes the project")
        self.assertLess(
            cd_at,
            rm_at,
            "cleanup removes the project before leaving its directory; LDM will "
            "refuse with a Safety Violation (LDM-#1436).",
        )

    def test_powershell_cleanup_returns_before_removing(self):
        """The PS1 restore lived in `finally`, which runs *after* cleanup."""
        src = PS1.read_text()
        body = src[src.index("function Finalize-Verification {") :]

        setloc_at = body.find("Set-Location $ORIGINAL_PWD")
        rm_at = body.find("--delete")

        self.assertNotEqual(-1, setloc_at, "Finalize-Verification never returns")
        self.assertNotEqual(-1, rm_at, "cleanup no longer removes the project")
        self.assertLess(
            setloc_at,
            rm_at,
            "Finalize-Verification removes the project before leaving its "
            "directory (LDM-#1436).",
        )

    def test_the_scripts_still_enter_the_project_directory(self):
        """Guards the premise.

        If a refactor stops entering the project directory, these assertions
        would pass while testing nothing. Pinning the `cd` keeps the reason for
        the fix visible.
        """
        self.assertRegex(
            SH.read_text(),
            r'cd "\$LDM_WORKSPACE/\$\{PROJECT_NAME\}"',
            "the bash script no longer enters the project directory; if that is "
            "deliberate, the LDM-#1436 guard above is now moot and should be "
            "revisited rather than left asserting a condition that cannot occur.",
        )
        self.assertRegex(
            PS1.read_text(),
            r"Set-Location \$projectDir",
            "the ps1 script no longer enters the project directory; see above.",
        )


if __name__ == "__main__":
    unittest.main()
