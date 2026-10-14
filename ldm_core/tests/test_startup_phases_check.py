"""The suite must read what LDM recorded about the boot (LDM-#2169).

The unit tests for #2169 assert the matcher and the renderer against a
captured log. Nothing asserted that a REAL boot on a REAL Liferay produces a
usable breakdown -- which is the only claim a user cares about, and the exact
gap the Feature Verification Script Gate exists to catch. It was missed when
v2.26.6-pre.2 was cut.

That gap is the same shape as LDM-#2154: three assertions walked
`routes/default/dxp` without reading a byte of it. Here the suite booted
Liferay many times and never read what LDM recorded about the boot.

These run the checker as a subprocess rather than importing it, because the
exit code IS the contract the shell halves consume: 0 asserted, 1 recorded
nothing usable, 3 nothing to evaluate -- and 3 must never be reported as a
pass.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
CHECKER = SCRIPTS_DIR / "check_startup_phases.py"

ASSERTED, NOT_RECORDED, CHECK_DID_NOT_RUN, NOTHING_TO_EVALUATE = 0, 1, 2, 3

# Offsets from a real 2026.q3.7 boot, relative to container start.
#: The checker now also asserts the boot's outcome (LDM-#2187), so a fixture
#: that only carries phases is an incomplete boot by definition.
READY = "ready"

REAL_BOOT = {
    "Tomcat Initializing": 1.0,
    "Database Schema Initializing": 6.0,
    "OSGi Framework Starting": 29.0,
    "Portal Startup Progress": 41.0,
    "Verify Processes Running": 104.0,
    "Tomcat Server Ready": 331.0,
}


class _Project:
    """A project tree holding only the file the checker reads."""

    def __init__(self, tmp, status=None):
        self.root = Path(tmp) / "proj"
        (self.root / ".liferay-docker").mkdir(parents=True)
        if status is not None:
            (self.root / ".liferay-docker" / "startup-status.json").write_text(
                json.dumps(status), encoding="utf-8"
            )

    def check(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER), str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, result.stdout + result.stderr


class TestARealBootIsAccepted(unittest.TestCase):
    def test_the_phases_of_a_real_boot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, {"status": READY, "milestone_seconds": REAL_BOOT}
            ).check()
        self.assertEqual(code, ASSERTED, out)

    def test_every_recorded_phase_reaches_the_report(self):
        """A failure elsewhere in the run should be readable from the report
        without re-running the boot, which takes minutes."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, {"status": READY, "milestone_seconds": REAL_BOOT}
            ).check()
        self.assertEqual(code, ASSERTED, out)
        for name in REAL_BOOT:
            self.assertIn(name, out)

    def test_phases_are_reported_in_time_order(self):
        """Recorded order is dict order, which is insertion order; the report
        must read as a timeline regardless."""
        shuffled = dict(reversed(list(REAL_BOOT.items())))
        with tempfile.TemporaryDirectory() as tmp:
            _, out = _Project(
                tmp, {"status": READY, "milestone_seconds": shuffled}
            ).check()
        positions = [out.index(name) for name in REAL_BOOT]
        self.assertEqual(positions, sorted(positions))


class TestTheDefectThisGuardsIsCaught(unittest.TestCase):
    """Before LDM-#2169, four of five markers matched nothing on a real log.
    These are what that produced."""

    def test_no_recorded_timings_is_a_failure(self):
        """The pre-#2169 shape exactly: the status file exists, the boot
        happened, and `milestone_seconds` was never written."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, {"status": "starting", "elapsed_seconds": 340}
            ).check()
        self.assertEqual(code, NOT_RECORDED, out)

    def test_an_empty_mapping_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(tmp, {"milestone_seconds": {}}).check()
        self.assertEqual(code, NOT_RECORDED, out)

    def test_too_few_phases_to_locate_the_time_is_a_failure(self):
        """One milestone is not a breakdown. It cannot answer the question the
        feature exists to answer."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, {"milestone_seconds": {"Tomcat Server Ready": 331.0}}
            ).check()
        self.assertEqual(code, NOT_RECORDED, out)

    def test_missing_tomcat_ready_is_a_failure_even_with_many_phases(self):
        """Every successful boot passes through it. A set that misses it is
        not tracking this Liferay line, however full it looks."""
        without = {k: v for k, v in REAL_BOOT.items() if k != "Tomcat Server Ready"}
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(tmp, {"milestone_seconds": without}).check()
        self.assertEqual(code, NOT_RECORDED, out)
        self.assertIn("Tomcat Server Ready", out)


class TestWhatCannotBeEvaluatedIsNotAPass(unittest.TestCase):
    def test_no_status_file_is_a_skip_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(tmp).check()
        self.assertEqual(code, NOTHING_TO_EVALUATE, out)
        self.assertIn("SKIPPED", out)

    def test_unreadable_json_is_a_skip_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _Project(tmp)
            (project.root / ".liferay-docker" / "startup-status.json").write_text(
                "{not json", encoding="utf-8"
            )
            code, out = project.check()
        self.assertEqual(code, NOTHING_TO_EVALUATE, out)

    def test_bad_usage_is_an_error_not_a_skip(self):
        """2, not 3. `python` exits 2 when it cannot open the script at all,
        so the skip code must be one python never produces (LDM-#2154)."""
        result = subprocess.run(
            [sys.executable, str(CHECKER)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, CHECK_DID_NOT_RUN)
        self.assertIn("usage:", result.stdout)


class TestBothHalvesCallIt(unittest.TestCase):
    """A source-text parity check, deliberately (LDM-#1982).

    The behaviour needs Docker, an `ldm` binary and a full boot. What this CAN
    catch is the failure this repo has actually had: a check living in one
    script and not the other.

    Both call sites were additionally EXECUTED against fixtures when they were
    written -- a real boot, a one-phase boot, an empty mapping and a missing
    file -- under bash and under pwsh 7.6.5, with identical verdicts, because a
    name that appears in a file is not a call site that runs (LDM-#2132).
    """

    SCRIPTS = (
        SCRIPTS_DIR / "verify_e2e_refactor.sh",
        SCRIPTS_DIR / "verify_e2e_refactor.ps1",
    )

    def test_both_halves_invoke_the_shared_checker(self):
        for script in self.SCRIPTS:
            with self.subTest(script=script.name):
                self.assertIn(
                    CHECKER.name,
                    script.read_text(encoding="utf-8"),
                    f"{script.name} never runs {CHECKER.name}, so on that "
                    f"platform nothing reads what LDM recorded about the boot "
                    f"(LDM-#2169)",
                )

    def test_both_halves_treat_the_skip_code_as_a_skip_and_not_a_pass(self):
        for script in self.SCRIPTS:
            text = script.read_text(encoding="utf-8")
            with self.subTest(script=script.name):
                self.assertIn(
                    "check_startup_phases",
                    text,
                    f"{script.name} does not reference the checker at all",
                )
                self.assertTrue(
                    "-eq 3" in text,
                    f"{script.name} does not branch on exit 3, so a boot whose "
                    f"phases were never recorded reads as something other than "
                    f"SKIPPED (LDM-#2169)",
                )

    def test_the_bash_half_invokes_an_interpreter_that_is_actually_assigned(self):
        """The shipped first version used `$E2E_PYTHON`, which is assigned
        nowhere. It expanded to empty, the shell tried to execute the .py
        directly, and the run died with exit 127 -- reported by the suite as
        "the check could not run", correctly, after a full CI boundary slice.

        The local execution probe could not catch it: it ran the checker with
        a hardcoded interpreter path instead of the script's own variable, so
        it proved the checker and the branching and not the invocation.

        shellcheck does not flag it either -- an unassigned variable may
        legitimately come from the environment.
        """
        import re

        text = (SCRIPTS_DIR / "verify_e2e_refactor.sh").read_text(encoding="utf-8")
        call = re.search(
            r'\$\("\$(\w+)"\s*\\?\s*\n?\s*"\$\{E2E_SCRIPT_DIR\}/' + CHECKER.name,
            text,
        )
        if call is None:
            self.fail(f"could not find the bash invocation of {CHECKER.name}")
        var = call.group(1)
        self.assertRegex(
            text,
            rf"(?m)^\s*{var}=",
            f"the bash half invokes {CHECKER.name} through ${var}, which is "
            f"never assigned in the script -- it expands to empty and the run "
            f"fails with exit 127",
        )


class TestTheBootsOutcomeIsAsserted(unittest.TestCase):
    """LDM-#2187: `"starting"` was the only status ever written, and only from
    inside the poll loop -- so a boot that succeeded left a file
    indistinguishable from one that stalled. The suite always boots, so this
    assertion costs nothing and catches a state the dashboard and the MCP tools
    would read as "still starting" forever."""

    def test_a_boot_still_marked_starting_is_a_failure(self):
        """The defect exactly: phases recorded, portal serving, status frozen."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, {"status": "starting", "milestone_seconds": REAL_BOOT}
            ).check()
        self.assertEqual(code, NOT_RECORDED, out)
        self.assertIn("starting", out)

    def test_a_missing_status_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(tmp, {"milestone_seconds": REAL_BOOT}).check()
        self.assertEqual(code, NOT_RECORDED, out)

    def test_ready_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, {"status": "ready", "milestone_seconds": REAL_BOOT}
            ).check()
        self.assertEqual(code, ASSERTED, out)

    def test_a_terminal_failure_is_still_a_recorded_verdict(self):
        """`timeout` and `failed` pass this check deliberately. It asserts that
        an outcome was RECORDED, not which one -- a boot that genuinely timed
        out fails the suite long before this runs."""
        for status in ("timeout", "failed"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                code, out = _Project(
                    tmp, {"status": status, "milestone_seconds": REAL_BOOT}
                ).check()
                self.assertEqual(code, ASSERTED, out)


if __name__ == "__main__":
    unittest.main()
