"""A too-old tunnel client must be explained, not dumped raw (LDM-#1575).

`ensure_installed()` returns as soon as ANY version is present, so the copy
LDM downloads into ~/.ldm/bin is never revisited -- pinned by neglect. The
gateway enforces its min_version client-side and fatally, so when that floor
rises the user sees a raw client fatal with nothing saying LDM owns the binary
or how to replace it.

The matched wording comes from the client's own log.Fatalf, which this project
does not control and has not observed firing, so the detection deliberately
accepts several markers rather than one exact string. These tests assert the
BRANCH, not the client's phrasing.
"""

import unittest

from ldm_core.handlers.share import is_min_version_failure

# The REAL predicate, not a restatement of it. An earlier draft reimplemented
# the rule here, which would have passed no matter what cmd_start did.
_matches = is_min_version_failure


class TestMinVersionDetection(unittest.TestCase):
    def test_the_documented_fatal_is_recognised(self):
        self.assertTrue(_matches("FATAL: client is too old to connect"))

    def test_wording_variants_are_recognised(self):
        for line in (
            "min_version not satisfied",
            "MinVersion check failed",
            "Client Is Too Old To Connect",
        ):
            with self.subTest(line=line):
                self.assertTrue(_matches(line))

    def test_it_also_reads_stdout(self):
        # The client is not guaranteed to use stderr.
        self.assertTrue(_matches("", "client is too old to connect"))

    def test_unrelated_failures_are_not_claimed(self):
        """Must not swallow every failure into a version explanation."""
        for line in (
            "connection refused",
            "tunnel is already running for this subdomain",
            "invalid token",
            "no such host",
        ):
            with self.subTest(line=line):
                self.assertFalse(_matches(line))


class TestDetectionIsWiredIn(unittest.TestCase):
    def test_cmd_start_checks_before_the_generic_failure(self):
        """Order matters: the generic branch would otherwise absorb it."""
        import inspect

        from ldm_core.handlers.share import ShareService

        src = inspect.getsource(ShareService.cmd_start)
        min_at = src.find("too old")
        generic_at = src.find("Failed to start tunnel")
        self.assertNotEqual(min_at, -1, "the min_version branch is missing")
        self.assertNotEqual(generic_at, -1)
        self.assertLess(
            min_at,
            generic_at,
            "the generic failure branch runs first and the user never sees "
            "the version explanation",
        )


if __name__ == "__main__":
    unittest.main()
