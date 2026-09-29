"""`ldm system doctor`'s summary said less than it appeared to.

Two defects in the default (non-`--detailed`) view, both found by an external
consumer running the tool against their own deployment.

**A passing check is invisible, so it cannot be told apart from one that never
ran.** They went looking for the filesystem mode check, did not find it, and
reasonably wrote: *"Either it only reports on a non-enforcing filesystem, or it
did not run -- a check that says nothing on the good path cannot be
distinguished from one that did not execute."* It had run, and had passed. The
view lists only problems, which is a reasonable default and an unreasonable
silence.

**One missing tool produced two warnings.** `telnet` and `lcp` were each
checked twice -- once by a generic `Path:` loop, once by a dedicated `Tool:`
check -- so a machine missing both showed four warnings for two causes, which
overstates how much is wrong.

These assert the RENDERED OUTPUT, not the source. A test that greps the module
for a string would pass against a line that is never reached, which is the same
mistake as the silence it is checking for.
"""

import io
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from ldm_core.diagnostics.doctor import DoctorRunner


class DoctorSummaryTests(unittest.TestCase):
    def _render(self, results):
        manager = MagicMock()
        for flag in ("system", "docker", "project", "detailed", "verbose"):
            setattr(manager.args, flag, False)
        diagnostics = MagicMock()
        diagnostics.manager = manager
        runner = DoctorRunner(diagnostics)
        runner.results = list(results)
        runner.args = manager.args

        out = io.StringIO()
        with (
            redirect_stdout(out),
            patch("sys.exit"),
            patch.object(runner, "_check_absolute_disk_space"),
            patch("ldm_core.diagnostics.doctor.run_command", return_value=""),
            # Without this the renderer writes a real ldm-debug-bundle-*.zip
            # into the working directory -- a test that leaves files behind in
            # the developer's repo, which the testing skill forbids outright.
            patch("ldm_core.diagnostics.doctor._generate_debug_bundle"),
        ):
            runner._check_dangling_and_print()
        return out.getvalue()

    def test_it_says_how_many_checks_passed_and_are_hidden(self):
        output = self._render(
            [
                ("Python Version", "3.14.0", True),
                ("Filesystem Modes", "Enforced (/tmp)", True),
                ("Docker Engine", "29.0.0", True),
                ("Project Initialization", "Vanilla", "warn"),
            ]
        )
        # The renderer contributes checks of its own, so the totals are
        # asserted for CONSISTENCY rather than pinned to a literal -- a pinned
        # number would break whenever an unrelated check is added, and would
        # teach the next person to update the number rather than read it.
        match = re.search(r"(\d+) of (\d+) checks passed", output)
        if match is None:
            self.fail(f"no pass-count line in the summary:\n{output}")
        passed, total = int(match.group(1)), int(match.group(2))
        self.assertGreaterEqual(total, 4, "our four results must be counted")
        self.assertEqual(
            total - passed,
            1,
            "exactly one of our results was not a pass, so one must be unpassed",
        )
        self.assertIn("--detailed", output, "must say how to see the hidden ones")

    def test_a_passing_check_is_still_not_listed_individually(self):
        """The fix is to ACKNOWLEDGE the hidden checks, not to stop hiding them.

        Listing every passing check would bury the problems, which is what the
        summary view exists to avoid.
        """
        output = self._render(
            [
                ("Filesystem Modes", "Enforced (/tmp)", True),
                ("Project Initialization", "Vanilla", "warn"),
            ]
        )
        self.assertNotIn("Enforced (/tmp)", output)
        self.assertIn("Project Initialization", output)


class ToolsAreCheckedOnceTests(unittest.TestCase):
    """One missing tool, one row.

    This runs the REAL tool-discovery pass. An earlier version of this test
    rendered a results list handed to it by the test, which could not fail
    when the duplicate was reinstated -- it bracketed the seam without
    crossing it. Verified by reinstating the duplicate and watching it pass.
    """

    def _discovered(self):
        manager = MagicMock()
        diagnostics = MagicMock()
        diagnostics.manager = manager
        runner = DoctorRunner(diagnostics)
        runner.results = []
        runner.hints = []
        with (
            patch("ldm_core.diagnostics.doctor.run_command", return_value=""),
            patch("ldm_core.diagnostics.doctor.shutil.which", return_value=None),
        ):
            try:
                runner._check_global_config_and_network()
            except Exception:  # pragma: no cover - later stages need a project
                pass
        return runner.results

    def test_a_tool_with_a_dedicated_check_is_not_also_listed_by_path(self):
        rows = [name for name, _status, _ok in self._discovered()]
        for tool in ("telnet", "lcp"):
            matching = [r for r in rows if tool in r.lower()]
            self.assertEqual(
                len(matching),
                1,
                f"{tool} reported {len(matching)} times ({matching}); it has a "
                "dedicated 'Tool:' check, so listing it in the generic 'Path:' "
                "loop as well reports one cause twice -- and for telnet and "
                "lcp that was two WARNINGS each",
            )

    def test_the_dedicated_check_is_the_one_that_survived(self):
        """The `Tool:` row says WHY it matters; the `Path:` row did not."""
        rows = {name: status for name, status, _ok in self._discovered()}
        self.assertIn("Tool: telnet", rows)
        self.assertIn("Gogo Shell", rows["Tool: telnet"])
        self.assertNotIn("Path: telnet", rows)


if __name__ == "__main__":
    unittest.main()
