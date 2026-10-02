"""Guards for scripts/boot_timing.sh -- the boot-timing harness (LDM-#2050).

The script itself needs Docker and a real Liferay boot, so none of that is
exercised here. What IS testable without either is the part that silently
corrupts a measurement rather than failing it:

  * `iso_to_epoch` turning Docker's RFC3339 timestamp into an epoch. An
    earlier draft fell back to "the time we started the command" when
    `date -d` was unavailable -- a quantity that is not the one the script
    claims to report, printed with no indication anything had changed.
    macOS took that branch and Linux did not, so the two platforms
    disagreed about what the number meant.

  * `summarise`, which must exclude the non-numeric sentinels (TIMEOUT,
    NOHC, GONE, NA). A sentinel counted as a value would drag a median
    toward a run that never produced a measurement.

  * the two methodological commitments encoded in the `ldm` invocation,
    which a well-meaning edit can drop without anything looking wrong.

To check these tests can fail: delete the `return 1` at the end of
`iso_to_epoch` and have it echo `$(date +%s)` instead -- exactly the
regression described above -- and `test_a_malformed_timestamp_fails`
should go red.
"""

import argparse
import datetime
import re
import subprocess
import unittest
from pathlib import Path

from ldm_core.cli import get_parser

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
BOOT_TIMING = SCRIPTS_DIR / "boot_timing.sh"


def _extract_function(name):
    # Explicit encoding: the locale codec is cp1252 on Windows and cannot
    # decode UTF-8 shell scripts (LDM-#1309).
    text = BOOT_TIMING.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\s*\(\)\s*\{{.*?^\}}", text, re.M | re.S)
    if not match:
        raise AssertionError(f"Could not extract {name}() from {BOOT_TIMING}")
    return match.group(0)


def _run(func_names, call):
    """Runs one of the script's helpers in a real bash, nothing else loaded."""
    body = "\n".join(_extract_function(n) for n in func_names)
    script = f'error() {{ echo "$*" >&2; }}\n{body}\n{call}\n'
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )


def _subcommand_options(name):
    """Option strings declared on one subcommand of the real parser."""
    parser, _ = get_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub = action.choices.get(name)
            if sub is not None:
                return {opt for a in sub._actions for opt in a.option_strings}
    raise AssertionError(f"No '{name}' subcommand in the parser")


class TestIsoToEpoch(unittest.TestCase):
    """GNU date, BSD date and python3 must agree, or the script must stop."""

    def test_a_docker_timestamp_converts_exactly(self):
        # Docker emits RFC3339 with nanoseconds; the fraction is discarded.
        expected = int(
            datetime.datetime(
                2026, 10, 2, 9, 12, 33, tzinfo=datetime.timezone.utc
            ).timestamp()
        )
        res = _run(["iso_to_epoch"], 'iso_to_epoch "2026-10-02T09:12:33.123456789Z"')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(int(res.stdout.strip()), expected)

    def test_a_timestamp_without_a_fraction_converts_the_same(self):
        with_fraction = _run(
            ["iso_to_epoch"], 'iso_to_epoch "2026-10-02T09:12:33.123456789Z"'
        ).stdout.strip()
        without = _run(
            ["iso_to_epoch"], 'iso_to_epoch "2026-10-02T09:12:33Z"'
        ).stdout.strip()
        self.assertEqual(with_fraction, without)

    def test_a_malformed_timestamp_fails(self):
        """The regression this file exists for: no silent approximation.

        A non-zero exit with nothing on stdout is the whole requirement. A
        plausible number here is worse than an error, because the caller
        cannot tell it apart from a real measurement.
        """
        res = _run(["iso_to_epoch"], 'iso_to_epoch "not-a-timestamp"')
        self.assertNotEqual(res.returncode, 0, "a bad timestamp was accepted")
        self.assertEqual(res.stdout.strip(), "", "a value was invented")


class TestSummarise(unittest.TestCase):
    def test_min_median_max_of_an_odd_sample(self):
        res = _run(["summarise"], "summarise 137256 142155 137664")
        self.assertEqual(res.stdout.split(), ["137256", "137664", "142155"])

    def test_a_single_run_is_its_own_min_median_and_max(self):
        res = _run(["summarise"], "summarise 500")
        self.assertEqual(res.stdout.split(), ["500", "500", "500"])

    def test_sentinels_are_excluded_not_counted(self):
        # TIMEOUT and NOHC are outcomes, not durations. Counting either
        # would move the median toward a run that measured nothing.
        res = _run(["summarise"], "summarise 198 TIMEOUT 203 NOHC")
        self.assertEqual(res.stdout.split(), ["198", "203", "203"])

    def test_a_sweep_that_measured_nothing_reports_na(self):
        res = _run(["summarise"], "summarise NA TIMEOUT")
        self.assertEqual(res.stdout.split(), ["NA", "NA", "NA"])
        self.assertEqual(res.returncode, 0, "an empty sample must not abort")


class TestTheMeasurementProtocolIsNotQuietlyWeakened(unittest.TestCase):
    """Two commitments that an innocent-looking edit would dissolve."""

    def setUp(self):
        self.text = BOOT_TIMING.read_text(encoding="utf-8")

    def test_ldm_run_keeps_its_own_readiness_wait_out_of_the_measurement(self):
        # Without --no-wait the script measures Docker's health transition
        # THROUGH `ldm wait`, so a change to LDM's polling would read as a
        # change in Liferay's boot time.
        run_line = next(
            line for line in self.text.splitlines() if '"$LDM_CMD" run ' in line
        )
        self.assertIn("--no-wait", run_line)

    def test_each_run_is_torn_down_completely(self):
        # --delete, not a plain stop: a surviving database or OSGi state
        # makes the next run warm, and a warm run is a different experiment.
        self.assertIn('rm "$PROJECT" --delete -y', self.text)


class TestTheCliItDependsOnStillExists(unittest.TestCase):
    """scripts/verify_osgi_persistence.sh rotted this way; this is cheap."""

    def test_run_still_accepts_the_flags_the_harness_passes(self):
        options = _subcommand_options("run")
        for flag in ("-c", "-t", "--no-wait"):
            self.assertIn(flag, options, f"'ldm run {flag}' no longer exists")

    def test_rm_still_accepts_delete(self):
        self.assertIn("--delete", _subcommand_options("rm"))


if __name__ == "__main__":
    unittest.main()
