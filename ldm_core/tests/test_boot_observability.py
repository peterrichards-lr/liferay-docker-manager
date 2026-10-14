"""A slow boot must be decomposable (LDM-#2169).

Measured on a real 2026.q3.7 boot that took 9m57s on a Windows 11 host with
7.6 GB allocated to Docker Desktop:

    14:01:34  container starts
    14:02:15  Starting Liferay Digital Experience Platform 2026.Q3.7
    14:03:35  Executing verify ...DocumentLibraryThumbnails...   <- last line
              ---- 3m30s with NO OUTPUT AT ALL ----
    14:07:05  Server startup in [329825] ms
    14:11:26  last line

Of the five milestones LDM tracked, four never appeared in that log and the
fifth matched the container entrypoint's shell banner 41 seconds before the
portal began starting. No phase durations were kept at all, and the error
counter deduped on raw lines that each carry their own timestamp, so one
recurring error was announced as new on every 30-second poll.

These assert against a VERBATIM excerpt of that log, not against strings
written to match the markers -- a marker list is exactly the thing that passes
a test built from the same assumption that made it wrong.
"""

import unittest
from pathlib import Path
from typing import ClassVar

from ldm_core.runtime.readiness import (
    ENTRYPOINT_PREFIX,
    STARTUP_MILESTONES,
    find_milestones,
    format_startup_phases,
    select_new_errors,
    strip_log_timestamps,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "liferay_boot_2026q3.log"
BOOT_LOG = FIXTURE.read_text(encoding="utf-8")

# Verbatim from the capture. The 41 seconds between them is the whole defect.
ENTRYPOINT_BANNER = (
    "2026-10-13T14:01:34.868694102Z [LIFERAY] Starting Liferay DXP. To stop "
    "the container with CTRL-C, run this container with the option"
)
PORTAL_OWN_LINE = (
    "2026-10-13T14:02:15.234043955Z Starting Liferay Digital Experience "
    "Platform 2026.Q3.7 (October 09, 2026)"
)


class TestTheMarkersMatchARealBootLog(unittest.TestCase):
    """Before this, exactly one of five markers matched this log."""

    def setUp(self):
        self.found = find_milestones(BOOT_LOG)

    def test_the_phases_that_occurred_are_all_detected(self):
        for title in (
            "Tomcat Initializing",
            "Database Schema Initializing",
            "OSGi Framework Starting",
            "OSGi Framework Started",
            "Portal Startup Progress",
            "Verify Processes Running",
            "Tomcat Server Ready",
            "Site Initializer Running",
        ):
            with self.subTest(milestone=title):
                self.assertIn(
                    title,
                    self.found,
                    f"'{title}' matched nothing in a real boot log, so it "
                    f"reports no progress on a boot that reached that phase",
                )

    def test_the_silent_verify_window_is_named(self):
        """The largest single block of the boot -- 3m30s, 35% of it -- used to
        produce no output and match no milestone, which is indistinguishable
        from a hang."""
        self.assertIn("Verify Processes Running", self.found)
        self.assertIn("Executing verify", self.found["Verify Processes Running"])

    def test_a_marker_kept_for_other_versions_is_not_claimed_as_reached(self):
        """`Spring Web Context Initializing` and `Available Contexts
        Registered` do not appear on this Liferay line. They are KEPT in the
        list because they match on others -- but they must not be reported as
        reached here, or the breakdown invents a phase."""
        self.assertNotIn("Spring Web Context Initializing", self.found)
        self.assertNotIn("Available Contexts Registered", self.found)


class TestTheEntrypointBannerIsNotPortalProgress(unittest.TestCase):
    """The measured false positive: LDM announced portal startup at 14:01:34
    against a line the container's shell printed, 41s before the portal said
    anything. It made the quiet period that followed look like a stall after
    progress rather than the first phase of the boot."""

    def _portal_milestone(self):
        return next(
            m for m in STARTUP_MILESTONES if m.title == "Portal Startup Progress"
        )

    def test_the_shell_banner_does_not_match(self):
        self.assertFalse(self._portal_milestone().matches(ENTRYPOINT_BANNER))

    def test_the_portals_own_line_still_matches(self):
        """The exclusion must not cost the real detection."""
        self.assertTrue(self._portal_milestone().matches(PORTAL_OWN_LINE))

    def test_the_banner_is_what_distinguishes_them(self):
        self.assertIn(ENTRYPOINT_PREFIX, ENTRYPOINT_BANNER)
        self.assertNotIn(ENTRYPOINT_PREFIX, PORTAL_OWN_LINE)

    def test_matching_is_per_line_not_over_the_whole_log(self):
        """A substring test over the concatenated log cannot express this:
        both lines are present, so it can only see that one of them is."""
        found = find_milestones(BOOT_LOG)
        self.assertIn("Portal Startup Progress", found)
        self.assertNotIn(ENTRYPOINT_PREFIX, found["Portal Startup Progress"])


class TestRecurringErrorsAreCountedOnce(unittest.TestCase):
    """Every Liferay line carries its own timestamp, so `line not in
    seen_errors` could never match a repeat."""

    def test_the_same_error_at_two_times_compares_equal(self):
        first = (
            "2026-10-13T14:09:38.330648061Z 2026-10-13 14:09:38.328 ERROR "
            "[http-nio-8080-exec-1][SomeClass:42] Connection refused"
        )
        later = (
            "2026-10-13T14:10:08.771222333Z 2026-10-13 14:10:08.769 ERROR "
            "[http-nio-8080-exec-1][SomeClass:42] Connection refused"
        )
        self.assertEqual(strip_log_timestamps(first), strip_log_timestamps(later))

    def test_every_timestamp_format_in_one_boot_log_is_stripped(self):
        """Four formats appear in a single capture."""
        for line in (
            "2026-10-13T14:01:34.768573367Z payload",
            "2026-10-13 14:09:38.328 payload",
            "13-Oct-2026 14:07:05.573 payload",
            "14:01:36,608 payload",
        ):
            with self.subTest(line=line):
                self.assertEqual(strip_log_timestamps(line), "payload")

    def test_genuinely_different_errors_still_differ(self):
        """Over-normalising would trade a counter that cries wolf for one that
        stays silent, which is worse."""
        a = "2026-10-13 14:09:38.328 ERROR [X:1] Connection refused"
        b = "2026-10-13 14:09:38.328 ERROR [X:1] Disk full"
        self.assertNotEqual(strip_log_timestamps(a), strip_log_timestamps(b))

    def test_a_line_with_no_timestamp_is_unchanged(self):
        self.assertEqual(strip_log_timestamps("  ERROR plain  "), "ERROR plain")


class TestThePhaseBreakdownAnswersWhereTheTimeWent(unittest.TestCase):
    """Reports the gap BETWEEN milestones. 'Tomcat Server Ready at 5m30s' does
    not say where those 5m30s went, which is the only question being asked."""

    # Offsets from the real capture, relative to container start at 14:01:34.
    REAL: ClassVar[dict[str, float]] = {
        "Tomcat Initializing": 1.0,
        "Database Schema Initializing": 6.0,
        "OSGi Framework Starting": 29.0,
        "OSGi Framework Started": 29.8,
        "Portal Startup Progress": 41.0,
        "Verify Processes Running": 104.0,
        "Tomcat Server Ready": 331.0,
        "Site Initializer Running": 337.0,
    }
    TOTAL = 592.0

    def _render(self):
        return "\n".join(format_startup_phases(self.REAL, self.TOTAL))

    def test_the_longest_phase_is_the_verify_window(self):
        """227s of the 592s boot sits between the verify marker and Tomcat
        being ready -- the block that produced no output at all."""
        gaps = {}
        previous = 0.0
        for title in self.REAL:
            gaps[title] = self.REAL[title] - previous
            previous = self.REAL[title]
        self.assertEqual(max(gaps, key=lambda k: gaps[k]), "Tomcat Server Ready")

    def test_every_reached_phase_appears(self):
        rendered = self._render()
        for title in self.REAL:
            with self.subTest(milestone=title):
                self.assertIn(title, rendered)

    def test_phases_are_reported_in_boot_order(self):
        """Dict order must not decide the timeline -- STARTUP_MILESTONES does."""
        shuffled = dict(reversed(list(self.REAL.items())))
        rendered = "\n".join(format_startup_phases(shuffled, self.TOTAL))
        positions = [rendered.index(title) for title in self.REAL]
        self.assertEqual(positions, sorted(positions))

    def test_time_after_the_last_milestone_is_not_silently_dropped(self):
        """255s of this boot happened after the last milestone. A breakdown
        that stops at the last marker hides them."""
        self.assertIn("after the last milestone", self._render())

    def test_nothing_is_printed_when_no_milestone_was_reached(self):
        """A boot that failed before any phase must not print an empty table."""
        self.assertEqual(format_startup_phases({}, 10.0), [])


def _error_at(time_text, message):
    """An error line exactly as Liferay emits it, at a given time."""
    return (
        f"2026-10-13T{time_text}.330648061Z 2026-10-13 {time_text}.328 ERROR "
        f"[http-nio-8080-exec-1][SomeClass:42] {message}"
    )


class TestTheCounterItselfStopsReCounting(unittest.TestCase):
    """The helper above is only half of it. The selector is tested here
    because the original logic lived inline in the readiness loop, where
    nothing could reach it without Docker -- a call site that exists is not a
    call site that runs (LDM-#2132)."""

    def test_one_recurring_error_is_reported_once_across_polls(self):
        """The reported symptom: '1 new error(s)' every 30 seconds, forever.
        Twenty polls of a boot that has one persistent error."""
        seen: set[str] = set()
        counts = [
            len(
                select_new_errors(
                    [_error_at(f"14:{m:02d}:38", "Connection refused")], seen
                )
            )
            for m in range(20)
        ]
        self.assertEqual(sum(counts), 1, f"reported {sum(counts)} errors for one")

    def test_the_same_error_repeated_within_one_read_counts_once(self):
        """`docker logs --tail 1000` returns every occurrence, so a single
        recurring error arrives many times in a single batch."""
        seen: set[str] = set()
        batch = [_error_at(f"14:09:{s:02d}", "Connection refused") for s in range(10)]
        self.assertEqual(len(select_new_errors(batch, seen)), 1)

    def test_a_genuinely_new_error_is_still_reported(self):
        """The counter must not be silenced -- that would be a worse defect
        than the one being fixed."""
        seen: set[str] = set()
        select_new_errors([_error_at("14:09:38", "Connection refused")], seen)
        later = select_new_errors(
            [
                _error_at("14:10:08", "Connection refused"),
                _error_at("14:10:09", "Disk full"),
            ],
            seen,
        )
        self.assertEqual(len(later), 1)
        self.assertIn("Disk full", later[0])

    def test_seen_is_updated_by_the_selector_itself(self):
        """The original recorded what it had reported in a separate statement
        from the comparison, which is how the two drifted apart."""
        seen: set[str] = set()
        select_new_errors([_error_at("14:09:38", "Connection refused")], seen)
        self.assertEqual(len(seen), 1)

    def test_the_line_returned_is_the_original_not_the_stripped_key(self):
        """The timestamp is what the user needs in order to find it in the
        logs; only the COMPARISON drops it."""
        seen: set[str] = set()
        line = _error_at("14:09:38", "Connection refused")
        self.assertEqual(select_new_errors([line], seen), [line])


if __name__ == "__main__":
    unittest.main()
