"""`ldm doctor` reports the lfr-tunnel client (LDM-#1578).

Measured against a real client before this was written:

    $ lfr-tunnel -version
    lfr-tunnel version v1.48.12

Semver and nothing else -- no build date, and no flag exposes one. So the only
date obtainable is the file's mtime, which records the last install or
self-upgrade, and it is labelled "updated" accordingly.

    $ lfr-tunnel -check-version
    [Error] Failed to check server compatibility: Get "/api/version": ...
    $ echo $?
    0

`-check-version` needs a gateway URL and exits 0 while failing without one, so
the floor comparison is deliberately not attempted here.

The binary is never executed by these tests. `.agents/skills/testing-and-ci`
prohibits it: real invocations risk SentinelOne quarantining the binary and the
surrounding toolchain.
"""

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from ldm_core.diagnostics.doctor import (
    DoctorRunner,
    _check_lfr_tunnel,
    describe_tunnel_client,
)


class TestTheReportedLine(unittest.TestCase):
    """describe_tunnel_client is module-level so the rule is called, not restated."""

    def setUp(self):
        # 2026-09-01 11:35 local, the mtime of a real installed client
        self.mtime = datetime(2026, 9, 1, 11, 35).timestamp()

    def test_version_and_update_date(self):
        status, ok = describe_tunnel_client("1.48.12", self.mtime)
        self.assertEqual(status, "v1.48.12 (updated 2026-09-01)")
        self.assertTrue(ok)

    def test_an_already_prefixed_version_is_not_double_prefixed(self):
        status, _ = describe_tunnel_client("v1.48.12", self.mtime)
        self.assertEqual(status, "v1.48.12 (updated 2026-09-01)")
        self.assertNotIn("vv", status)

    def test_a_binary_whose_version_cannot_be_read_is_a_warning(self):
        status, ok = describe_tunnel_client(None, self.mtime)
        self.assertEqual(status, "Version unreadable")
        self.assertEqual(ok, "warn")

    def test_an_unreadable_mtime_still_reports_the_version(self):
        """A stat failure must not lose the version we did obtain."""
        status, ok = describe_tunnel_client("1.48.12", None)
        self.assertEqual(status, "v1.48.12")
        self.assertTrue(ok)

    def test_the_date_is_the_files_own_mtime(self):
        """Not today's date -- an old binary must read as old."""
        old = datetime(2025, 1, 15, 9, 0).timestamp()
        status, _ = describe_tunnel_client("1.2.3", old)
        self.assertIn("2025-01-15", status)
        self.assertNotIn(datetime.now().strftime("%Y-%m-%d"), status)


class TestResolution(unittest.TestCase):
    """Doctor must report the binary `ldm share` will actually use."""

    def _run(self, resolved, version="1.48.12", mtime=1_756_000_000.0):
        handler = MagicMock()
        service = MagicMock()
        service._resolve_existing_binary.return_value = resolved
        service._get_installed_version.return_value = version
        fake_path = MagicMock()
        fake_path.stat.return_value.st_mtime = mtime
        with patch("ldm_core.handlers.share.ShareService", return_value=service):
            with patch("ldm_core.diagnostics.doctor.Path", return_value=fake_path):
                return _check_lfr_tunnel(handler)

    def test_a_resolved_binary_is_reported(self):
        status, ok = self._run("/Users/x/runningpoc/bin/lfr-tunnel")
        self.assertIn("v1.48.12", status)
        self.assertTrue(ok)

    def test_no_binary_is_a_warning_not_a_failure(self):
        """Absent lfr-tunnel is not a broken environment -- sharing is optional."""
        status, ok = self._run(None)
        self.assertEqual(status, "Not installed")
        self.assertEqual(ok, "warn")

    def test_it_uses_the_share_resolver_not_a_bare_path_lookup(self):
        """A configured lfr_tunnel_bin must not be reported as missing.

        _resolve_existing_binary honours LDM_LFR_TUNNEL_BIN and the ldmrc
        setting as well as PATH; shutil.which sees only PATH.
        """
        handler = MagicMock()
        service = MagicMock()
        service._resolve_existing_binary.return_value = "/opt/custom/lfr-tunnel"
        service._get_installed_version.return_value = "1.48.12"
        with patch("ldm_core.handlers.share.ShareService", return_value=service):
            with patch("shutil.which", return_value=None) as which:
                status, ok = _check_lfr_tunnel(handler)
        self.assertTrue(ok, "a configured path must not report as missing")
        self.assertIn("v1.48.12", status)
        service._resolve_existing_binary.assert_called_once()
        which.assert_not_called()

    def test_a_broken_share_service_degrades_silently(self):
        """Doctor must never abort on one optional tool."""
        handler = MagicMock()
        with patch(
            "ldm_core.handlers.share.ShareService", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(_check_lfr_tunnel(handler), (None, None))


class TestTheRowReachesTheReport(unittest.TestCase):
    """The check being correct is worthless if doctor never calls it.

    This drives the real DoctorRunner section rather than asserting that the
    call appears in the source -- a source assertion passes whenever the text
    survives, including when the registration has been moved somewhere that
    never runs. Written after exactly that: the registration was first verified
    against _check_tooling_and_integrity and produced no row, because it
    actually lives in _check_global_config_and_network beside the LCP check.
    """

    def _rows(self, check_result):
        handler = MagicMock()
        runner = DoctorRunner(handler)
        # The section reaches Docker further down (the search-global inspect at
        # doctor.py:979), well past the registration under test. run_command is
        # module-scope in doctor.py, so patching it there is the call site --
        # patching the manager's copy would not reach it (#1365).
        with patch("ldm_core.diagnostics.doctor.run_command", return_value=None):
            with patch(
                "ldm_core.diagnostics.doctor._check_lfr_tunnel",
                return_value=check_result,
            ):
                runner._check_global_config_and_network()
        return runner

    def test_a_resolved_client_appears_as_a_row(self):
        runner = self._rows(("v9.9.9 (updated 2026-01-01)", True))
        rows = [r for r in runner.results if r[0] == "lfr-tunnel Client"]
        self.assertEqual(len(rows), 1, "the row never reached the report")
        self.assertEqual(rows[0][1], "v9.9.9 (updated 2026-01-01)")
        self.assertTrue(rows[0][2])

    def test_a_missing_client_adds_an_actionable_hint(self):
        runner = self._rows(("Not installed", "warn"))
        rows = [r for r in runner.results if r[0] == "lfr-tunnel Client"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "warn")

        hints = " ".join(h["text"] for h in runner.hints)
        self.assertIn("lfr-tunnel-docker", hints, "no containerised fallback offered")

    def test_a_degraded_check_adds_no_row_at_all(self):
        """(None, None) means 'could not look' -- it must not print a row."""
        runner = self._rows((None, None))
        self.assertEqual([r for r in runner.results if r[0] == "lfr-tunnel Client"], [])


if __name__ == "__main__":
    unittest.main()
