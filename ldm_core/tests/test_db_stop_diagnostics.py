"""A failed `ldm db start`/`db stop` reports what docker said (LDM-#1603, #1615).

LDM-#1547 added guards that re-check `is_running` after the command, because
`DockerService.start`/`.stop` discard failure. The guards detect that something
went wrong; LDM-#1603 was meant to report *why*.

That was not academic. `ldm db stop` failed once on debian during v2.21.0-pre.2
platform verification, then again on the v2.21.0 tag on ubuntu, and passed on a
re-run both times with no code change.

LDM-#1615 found two reasons the second occurrence was no more diagnosable than
the first, and both are covered here:

1. `scripts/verify_e2e_refactor.{sh,ps1}` invoked `ldm db start`/`db stop` with
   `>/dev/null 2>&1` (`*> $null` on Windows). `UI.error` writes to **stderr**,
   so every message, `Details:` line and `Tip:` line -- including all of
   LDM-#1603's -- was discarded before it could reach the report.
2. `_docker_failure_detail`'s `docker said:` branches were unreachable at every
   real call site. They read `.stderr`/`.stdout` off a `CompletedProcess`, but
   `DockerService.stop` returns `run_command(...)`, whose type is `str | None`.
   Observed: a mocked `docker stop` exiting 1 with "cannot stop container xyz:
   permission denied" produced the detail "no output was captured", because
   `run_command(check=False)` had already thrown stderr away. Only the tests,
   which passed a `MagicMock` carrying a `.stderr` attribute, ever reached the
   branch.

The tests below therefore exercise the real code paths -- real `run_command`,
real `UI.die`, real stderr -- with only `subprocess.run` mocked, and assert on
the text that actually lands on the stream. Nothing here reads the source of
the thing under test: a diagnostic that survives as *text* while no longer
being *emitted* is exactly the failure mode LDM-#1615 is about.
"""

import contextlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import ldm_core.utils as utils_module
from ldm_core.handlers.database import (
    DatabaseService,
    _docker_failure_detail,
    _report_swallowed_docker_failure,
)
from ldm_core.ui import UI
from ldm_core.utils import last_command_failure, run_command

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
BASH_SCRIPT = SCRIPTS_DIR / "verify_e2e_refactor.sh"
PS1_SCRIPT = SCRIPTS_DIR / "verify_e2e_refactor.ps1"

DB_GLOBAL = "liferay-db-global"


def _completed(returncode=0, stdout=b"", stderr=b""):
    """A stand-in for what `subprocess.run` really hands back."""
    res = MagicMock()
    res.returncode = returncode
    res.stdout = stdout
    res.stderr = stderr
    return res


class _DockerDouble:
    """Answers the docker calls `cmd_start`/`cmd_stop` make, and nothing else.

    Keyed on the docker sub-command rather than on call order, so a change in
    how many times the guards probe `is_running` does not silently repurpose a
    canned answer. `run_command` rewrites argv[0] to an absolute path via
    `shutil.which`, so the executable name is never matched on.
    """

    def __init__(self, stop=None, ps=None, ps_all=None, start=None):
        self.stop = stop if stop is not None else _completed(0, DB_GLOBAL.encode())
        self.start = start if start is not None else _completed(0, DB_GLOBAL.encode())
        self.ps = ps if ps is not None else _completed(0, b"deadbeef\n")
        self.ps_all = ps_all if ps_all is not None else _completed(0, b"deadbeef\n")
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(list(cmd))
        if "stop" in cmd:
            return self._answer(self.stop)
        if "start" in cmd:
            return self._answer(self.start)
        if "ps" in cmd:
            return self._answer(self.ps_all if "-a" in cmd else self.ps)
        raise AssertionError(f"unexpected docker invocation in test: {cmd}")

    @staticmethod
    def _answer(answer):
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer) and not isinstance(answer, MagicMock):
            return answer()
        return answer


class _RealPathsIsolated(unittest.TestCase):
    """Base class: no test here may touch the real ~/.ldmrc, ~/.ldm or docker.

    `DockerService.get_docker_cmd_prefix` resolves the active target through
    `get_active_target` -> `load_targets` -> `get_actual_home()/.ldmrc`, and
    `get_actual_home` ignores `HOME` (it reconstructs the path from
    `SUDO_USER`/`USER` on macOS). `LDM_HOME` is the only override that reaches
    it -- see the docstring on `get_actual_home` and LDM-#1349.
    """

    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        patcher = patch.dict(os.environ, {"LDM_HOME": self._home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

        # Module-global diagnostic breadcrumb: reset so no test can pass on a
        # record another test left behind.
        utils_module._LAST_COMMAND_FAILURE = None
        self.addCleanup(setattr, utils_module, "_LAST_COMMAND_FAILURE", None)

        # The CI verification script passes neither --info nor --verbose, so
        # anything gated behind them is invisible there. Pin that.
        for flag in ("VERBOSE", "INFO_MODE", "QUIET_MODE"):
            self.addCleanup(setattr, UI, flag, getattr(UI, flag, False))
            setattr(UI, flag, False)

    @contextlib.contextmanager
    def captured(self):
        """Both streams, separately -- which stream a diagnostic lands on is
        the entire subject of LDM-#1615."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            yield out, err


class TestTheDetailMessage(unittest.TestCase):
    """_docker_failure_detail is module-level so the rule is called, not restated."""

    def setUp(self):
        utils_module._LAST_COMMAND_FAILURE = None
        self.addCleanup(setattr, utils_module, "_LAST_COMMAND_FAILURE", None)

    def _res(self, stderr="", stdout=""):
        res = MagicMock()
        res.stderr = stderr
        res.stdout = stdout
        return res

    def test_stderr_is_surfaced(self):
        detail = _docker_failure_detail(self._res(stderr="Error: No such container: x"))
        self.assertIn("No such container: x", detail)

    def test_none_is_distinguished_from_silence(self):
        """run_command returns None for a non-zero exit AND for a timeout.

        Reporting that narrows the cause; reporting 'no output' would not.
        """
        none_detail = _docker_failure_detail(None)
        silent_detail = _docker_failure_detail(self._res())
        self.assertNotEqual(none_detail, silent_detail)
        self.assertIn("timed out", none_detail)

    def test_stdout_is_used_when_stderr_is_empty(self):
        detail = _docker_failure_detail(self._res(stdout="only-on-stdout"))
        self.assertIn("only-on-stdout", detail)

    def test_stderr_wins_over_stdout(self):
        detail = _docker_failure_detail(
            self._res(stderr="the real error", stdout="noise")
        )
        self.assertIn("the real error", detail)
        self.assertNotIn("noise", detail)

    def test_it_never_returns_empty(self):
        """UI.die(details=...) with an empty string is worse than no details."""
        for res in (None, self._res(), self._res(stderr="   "), self._res(stdout="  ")):
            with self.subTest(res=res):
                self.assertTrue(_docker_failure_detail(res).strip())


class TestTheGuardsReportIt(unittest.TestCase):
    """The detail must actually reach UI.die, not merely be computable."""

    def setUp(self):
        self.manager = MagicMock()
        self.manager.target = None
        self.service = DatabaseService(self.manager)

    def _run(self, command, running_before, running_after, res):
        """Drive cmd_start/cmd_stop with a container that refuses to change state."""
        with patch(
            "ldm_core.handlers.database._shared_db_engines", return_value=["postgresql"]
        ):
            with patch("ldm_core.docker_service.DockerService") as docker:
                docker.exists.return_value = True
                docker.is_running.side_effect = [running_before, running_after]
                docker.start.return_value = res
                docker.stop.return_value = res
                with patch("ldm_core.handlers.database.UI") as ui:
                    ui.die.side_effect = SystemExit(3)
                    with self.assertRaises(SystemExit):
                        command()
                    return ui.die.call_args

    def test_stop_reports_dockers_error(self):
        res = MagicMock()
        res.stderr = "Error response from daemon: cannot stop container"
        res.stdout = ""
        call = self._run(self.service.cmd_stop, True, True, res)
        self.assertIn(
            "cannot stop container",
            call.kwargs.get("details", ""),
            "the stop guard fired without saying what docker reported",
        )
        self.assertEqual(call.kwargs.get("exit_code"), 3)

    def test_start_reports_dockers_error(self):
        res = MagicMock()
        res.stderr = "Error response from daemon: port is already allocated"
        res.stdout = ""
        call = self._run(self.service.cmd_start, False, False, res)
        self.assertIn(
            "port is already allocated",
            call.kwargs.get("details", ""),
            "the start guard fired without saying what docker reported",
        )

    def test_a_none_result_still_produces_a_detail(self):
        """The debian case: no captured output, but say so rather than nothing."""
        call = self._run(self.service.cmd_stop, True, True, None)
        self.assertTrue(call.kwargs.get("details", "").strip())


class TestRunCommandKeepsFailureEvidence(_RealPathsIsolated):
    """`run_command(check=False)` used to discard stderr outright (LDM-#1615).

    That single `return None` is why `_docker_failure_detail` could never work:
    by the time the caller saw the result, docker's explanation was gone.
    """

    def test_a_non_zero_exit_records_the_stderr(self):
        fail = _completed(1, b"", b"Error response from daemon: no such container")
        with patch("ldm_core.utils.subprocess.run", return_value=fail):
            self.assertIsNone(run_command(["docker", "stop", DB_GLOBAL], check=False))

        recorded = last_command_failure()
        if recorded is None:
            self.fail("run_command(check=False) recorded nothing about the failure")
        failed_cmd, failure = recorded
        self.assertIn("stop", failed_cmd)
        self.assertEqual(failure.returncode, 1)
        self.assertIn("no such container", failure.stderr)

    def test_a_timeout_is_recorded_and_distinguishable(self):
        boom = subprocess.TimeoutExpired(cmd="docker stop", timeout=7)
        with patch("ldm_core.utils.subprocess.run", side_effect=boom):
            self.assertIsNone(run_command(["docker", "stop", DB_GLOBAL], check=False))

        recorded = last_command_failure()
        if recorded is None:
            self.fail("a check=False timeout recorded nothing")
        _failed_cmd, failure = recorded
        self.assertEqual(failure.returncode, 124)
        self.assertIn("timed out", failure.stderr)

    def test_a_success_records_nothing(self):
        with patch("ldm_core.utils.subprocess.run", return_value=_completed(0, b"ok")):
            self.assertEqual(run_command(["docker", "ps"], check=False), "ok")
        self.assertIsNone(last_command_failure())

    def test_an_interrupt_is_not_a_silent_exit(self):
        """Exit 130 out of `run_command` used to print nothing at all.

        The message was behind `UI.detail`, which is gated on --info/--verbose
        (neither of which the CI verification script passes). A non-zero exit
        with both streams empty is undiagnosable by construction, and it is one
        of the paths `ldm db stop` can leave through.
        """
        with patch("ldm_core.utils.subprocess.run", side_effect=KeyboardInterrupt()):
            with self.captured() as (out, err):
                with self.assertRaises(SystemExit) as ctx:
                    run_command(["docker", "stop", DB_GLOBAL], check=False)
        self.assertEqual(ctx.exception.code, 130)
        self.assertTrue(
            (out.getvalue() + err.getvalue()).strip(),
            "exit 130 produced no output on either stream",
        )


class TestStopGuardOnTheRealCallPath(_RealPathsIsolated):
    """End-to-end through `cmd_stop`: only `subprocess.run` is mocked.

    This is the test the LDM-#1603 ones could not be: they injected a
    `MagicMock` with a `.stderr` attribute, a shape `DockerService.stop` never
    returns, so they passed while the production path reported nothing useful.
    """

    def setUp(self):
        super().setUp()
        self.manager = MagicMock()
        self.manager.target = None
        self.service = DatabaseService(self.manager)

    def _stop(self, double):
        # conftest's autouse `stub_docker_environment_probes` replaces
        # `ldm_core.docker_service.run_command` with a stub returning "" -- the
        # right default for the suite at large, but it short-circuits the very
        # layer under test here (`exists` would report no container and
        # `cmd_stop` would exit 0 having done nothing). Restore the real
        # function and cut the wire one level lower instead, at
        # `subprocess.run`, which is also where conftest's `block_real_docker`
        # guard sits -- so no docker CLI can be reached either way.
        with patch(
            "ldm_core.handlers.database._shared_db_engines", return_value=["postgresql"]
        ):
            with (
                patch("ldm_core.docker_service.run_command", utils_module.run_command),
                patch("ldm_core.utils.subprocess.run", side_effect=double),
            ):
                with self.captured() as (out, err):
                    try:
                        self.service.cmd_stop()
                    except SystemExit as exc:
                        return exc.code, out.getvalue(), err.getvalue()
                    return 0, out.getvalue(), err.getvalue()

    def test_the_guard_reports_what_docker_actually_said(self):
        double = _DockerDouble(
            stop=_completed(
                1,
                b"",
                b"Error response from daemon: cannot stop container "
                b"liferay-db-global: permission denied",
            )
        )
        code, _out, err = self._stop(double)

        self.assertEqual(code, 3, "the stop guard must exit 3 (infrastructure)")
        self.assertIn("is still running", err)
        self.assertIn(
            "permission denied",
            err,
            "the guard fired but docker's own explanation never reached stderr",
        )
        self.assertIn("docker said:", err)

    def test_the_guard_names_the_command_that_failed(self):
        """`last_command_failure()` holds the LAST failure, not necessarily
        this caller's, so the detail has to say which command it describes."""
        double = _DockerDouble(
            stop=_completed(1, b"", b"Error response from daemon: boom")
        )
        _code, _out, err = self._stop(double)
        self.assertIn("stop", err)
        self.assertIn("exited 1", err)

    def test_a_stop_timeout_is_reported_as_a_timeout(self):
        """A timeout and a non-zero exit both return None from run_command.

        The old fallback line said "failed or timed out" -- true of either, and
        therefore evidence of neither. It has to say which, and how long.
        """
        double = _DockerDouble(
            stop=subprocess.TimeoutExpired(cmd="docker stop", timeout=30)
        )
        code, _out, err = self._stop(double)
        self.assertEqual(code, 3)
        self.assertIn("timed out after 30s", err)
        self.assertIn("stop", err)
        self.assertNotIn(
            "failed or timed out",
            err,
            "an either/or message is not a diagnosis",
        )

    def test_the_diagnostic_lands_on_stderr_not_stdout(self):
        """The verification script discarded stderr; that is only recoverable
        if we know which stream the evidence is on."""
        double = _DockerDouble(
            stop=_completed(1, b"", b"Error response from daemon: marker-9f2a")
        )
        _code, out, err = self._stop(double)
        self.assertIn("marker-9f2a", err)
        self.assertNotIn("marker-9f2a", out)

    def test_a_stop_failure_the_guard_forgives_is_still_reported(self):
        """docker stop failed, yet the container is gone: outcome fine,
        evidence previously discarded in full.

        This exits 0 -- deliberately. LDM-#1615 is a diagnosis task; changing
        which invocations fail is not in scope.
        """
        double = _DockerDouble(
            stop=_completed(1, b"", b"Error response from daemon: marker-abc123"),
            ps=_completed(0, b"deadbeef\n"),
        )
        # is_running: True before the stop, False after it.
        answers = [_completed(0, b"deadbeef\n"), _completed(0, b"")]

        def ps_answer():
            return answers.pop(0) if answers else _completed(0, b"")

        double.ps = ps_answer
        code, out, err = self._stop(double)

        self.assertEqual(code, 0, "the container did stop; this is not a failure")
        combined = out + err
        self.assertIn(
            "marker-abc123",
            combined,
            "docker reported a failure and it was discarded entirely",
        )


class TestTheCatchAllExitPath(_RealPathsIsolated):
    """`cli.py`'s `except Exception` is the other non-zero exit out of any
    command, `db stop` included -- and it printed `details=e`.

    `str(e)` is empty for a bare `MemoryError` or `OSError`, so that produced
    "An unexpected error occurred." followed by an empty `Details:` line: exit
    1 with no indication of what went wrong, let alone where.
    """

    def _dispatch(self, exc):
        from argparse import Namespace

        from ldm_core.cli import _execute_command

        args = Namespace(command="db", subcommand="stop", benchmark=False)

        def boom():
            raise exc

        # The update check would otherwise reach the network on a background
        # thread for a test about exception reporting.
        with patch("ldm_core.cli.check_for_updates", return_value=(None, None)):
            with self.captured() as (out, err):
                with self.assertRaises(SystemExit) as ctx:
                    _execute_command(args, ("db", "stop"), {("db", "stop"): boom})
        return ctx.exception.code, out.getvalue(), err.getvalue()

    def test_an_exception_with_no_message_still_names_its_type(self):
        code, _out, err = self._dispatch(MemoryError())
        self.assertEqual(code, 1, "the catch-all must keep exiting 1")
        self.assertIn(
            "MemoryError",
            err,
            "exit 1 with neither a message nor a type is undiagnosable",
        )

    def test_the_message_is_kept_when_there_is_one(self):
        _code, _out, err = self._dispatch(RuntimeError("docker socket vanished"))
        self.assertIn("docker socket vanished", err)
        self.assertIn("RuntimeError", err)


class TestSwallowedFailureHelper(unittest.TestCase):
    def setUp(self):
        utils_module._LAST_COMMAND_FAILURE = None
        self.addCleanup(setattr, utils_module, "_LAST_COMMAND_FAILURE", None)

    def test_a_successful_result_is_not_reported(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _report_swallowed_docker_failure("stop", DB_GLOBAL, "liferay-db-global")
        self.assertEqual(buf.getvalue().strip(), "")

    def test_a_failed_result_is_reported_unconditionally(self):
        """Not behind --info/--verbose: the CI script passes neither."""
        self.addCleanup(setattr, UI, "VERBOSE", UI.VERBOSE)
        self.addCleanup(setattr, UI, "INFO_MODE", UI.INFO_MODE)
        UI.VERBOSE = False
        UI.INFO_MODE = False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _report_swallowed_docker_failure("stop", DB_GLOBAL, None)
        self.assertIn(DB_GLOBAL, buf.getvalue())


def _extract_block(script_path, pattern):
    # LDM-#1309: encoding must be explicit -- read_text() would use cp1252 on
    # Windows and choke on the UTF-8 in verify_e2e_refactor.sh.
    text = script_path.read_text(encoding="utf-8")
    match = pattern.search(text)
    if match is None:
        raise AssertionError(
            f"Could not extract block from {script_path} using {pattern.pattern!r}"
        )
    return match.group(0)


class TestTheVerificationScriptSurfacesIt(unittest.TestCase):
    """The gating blind spot: the script threw the evidence away (LDM-#1615).

    `ldm db start`/`db stop` ran under `>/dev/null 2>&1`, so LDM's stderr --
    the only stream `UI.error` writes to -- never reached the report. The
    diagnostic dump is extracted here and executed for real in an isolated
    bash subprocess, the pattern LDM-#1058 established in
    `test_verify_scripts.py`: no Docker and no `ldm` binary are involved.
    """

    def _run_dump(self, ldm_output):
        func_text = _extract_block(
            BASH_SCRIPT, re.compile(r"^db_cmd_failed\s*\(\)\s*\{.*?^\}", re.M | re.S)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "db-cmd.log"
            log.write_text(ldm_output, encoding="utf-8")
            results = Path(tmp) / "results.txt"
            fake_home = Path(tmp) / "home"
            (fake_home / ".ldm").mkdir(parents=True)
            (fake_home / ".ldm" / "last-command.log").write_text(
                "[CMD] docker stop liferay-db-global\n"
                "[EXIT 1] docker stop liferay-db-global\n"
                "[STDERR] Error response from daemon: trace-marker\n",
                encoding="utf-8",
            )
            script = (
                "docker() { echo 'docker-double invoked'; }\n"
                f'DB_CMD_LOG="{log}"\n'
                f'RESULTS_FILE_TMP="{results}"\n'
                'DB_GLOBAL="liferay-db-global"\n'
                f'LDM_HOME="{fake_home}"\n'
                f"{func_text}\n"
                'db_cmd_failed "ldm db stop" 3\n'
            )
            res = subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True, check=False
            )
            self.assertEqual(res.returncode, 0, f"bash failed: {res.stderr}")
            return res.stdout, results.read_text(encoding="utf-8")

    def test_ldms_stderr_reaches_the_report(self):
        stdout, report = self._run_dump(
            "❌ Global shared database 'liferay-db-global' is still running.\n"
            "Details:  `docker stop liferay-db-global` exited 1; "
            "docker said: Error response from daemon: needle-77\n"
        )
        for where, text in (("console", stdout), ("report file", report)):
            with self.subTest(where=where):
                self.assertIn("needle-77", text)
                self.assertIn("is still running", text)

    def test_the_exit_code_is_reported(self):
        """3 means the guard tripped; 1 means cli.py's catch-all. A report that
        says only "exited non-zero" cannot tell them apart."""
        stdout, report = self._run_dump("some output\n")
        self.assertIn("exit code: 3", stdout)
        self.assertIn("exit code: 3", report)

    def test_no_output_at_all_is_said_out_loud(self):
        """The three real occurrences produced nothing. 'nothing' is a finding
        and must be recorded as one, not left as an absence."""
        stdout, _report = self._run_dump("")
        self.assertIn("no output on either stream", stdout)

    def test_the_trace_log_is_dumped(self):
        """`run_command(check=False)` failures now trace their stderr there,
        which is the only place docker's own words survive."""
        stdout, _report = self._run_dump("anything\n")
        self.assertIn("trace-marker", stdout)

    def test_docker_is_asked_for_its_own_view(self):
        stdout, _report = self._run_dump("anything\n")
        self.assertIn("docker-double invoked", stdout)


class TestThePowerShellHalfSurfacesIt(unittest.TestCase):
    """Cross-platform parity is a hard rule: a Windows developer must not get a
    green report that checked less than the Unix one."""

    @staticmethod
    def _pwsh():
        return shutil.which("pwsh") or shutil.which("powershell")

    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell not available on this host",
    )
    def test_ldms_output_and_exit_code_reach_the_report(self):
        func_text = _extract_block(
            PS1_SCRIPT,
            re.compile(r"^    function Write-DbCmdFailure\s*\{.*?^    \}", re.M | re.S),
        )
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "db-cmd.log"
            log.write_text("Details:  docker said: needle-88\n", encoding="utf-8")
            results = Path(tmp) / "results.txt"
            script = (
                "function Write-Verdict { param([string]$Message) "
                "Write-Output $Message; "
                f"$Message | Out-File -FilePath '{results}' -Append -Encoding utf8 }}\n"
                "function docker { 'docker-double invoked' }\n"
                f"{func_text}\n"
                "Write-DbCmdFailure -Label 'ldm db stop' -ExitCode 3 "
                f"-LogPath '{log}' -GlobalContainer 'liferay-db-global'\n"
            )
            res = subprocess.run(
                [self._pwsh(), "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, f"pwsh failed: {res.stderr}")
            report = results.read_text(encoding="utf-8")

        for where, text in (("console", res.stdout), ("report file", report)):
            with self.subTest(where=where):
                self.assertIn("needle-88", text)
                self.assertIn("exit code: 3", text)


def _extract_block_tail(script_path, window_start, window_end, region_start):
    """Extracts the tail of the shared-database block from either script.

    Deliberately indentation-agnostic about *where* the LDM-#1419 cleanup sits.
    The point of the tests below is that the cleanup runs on the failure path,
    and a pattern that only matched the arrangement which does that would fail
    to extract the arrangement which does not -- proving nothing about
    behaviour. Anchoring on a window and taking the first structural boundary
    inside it yields a syntactically complete region either way, so the two
    arrangements can be run and compared.
    """
    text = script_path.read_text(encoding="utf-8")
    start = text.index(window_start)
    end = text.index(window_end, start) + len(window_end)
    window = text[start:end]
    match = region_start.search(window)
    if match is None:
        raise AssertionError(
            f"Could not locate the block tail in {script_path} using "
            f"{region_start.pattern!r}"
        )
    return window[match.start() :]


class TestCleanupRunsOnTheFailurePath(unittest.TestCase):
    """A failed verification must still put the machine back (LDM-#1615).

    The LDM-#1419 cleanup removes the global database container and its volume
    when the check provisioned them. The bash half runs it unconditionally; the
    PowerShell half had it nested inside `if ($dbCmdOk)`, so a **failed**
    Windows run leaked both -- and a failed run is exactly when the operator is
    about to re-run and most needs the machine clean.

    Cross-platform parity is a hard rule (.agents/skills/testing-and-ci), and
    it had drifted here. These tests run the real region from each script in an
    isolated bash/pwsh subprocess with `docker` shadowed, so neither the daemon
    nor any binary is touched.
    """

    _SH_MARKER = "docker rm -f"
    _PS_MARKER = "docker rm -f"

    def _run_bash_tail(self, db_cmd_ok):
        tail = _extract_block_tail(
            BASH_SCRIPT,
            '-y db stop > "$DB_CMD_LOG"',
            "    exit 1\nfi",
            re.compile(
                r"^(?:# LDM-#1419: leave the machine|if \[ \"\$DB_CMD_OK\" = true \]; then)",
                re.M,
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp) / "docker-calls.txt"
            results = Path(tmp) / "results.txt"
            script = (
                f'docker() {{ echo "docker $*" >> "{calls}"; }}\n'
                f'report_ok() {{ echo "$1" >> "{results}"; }}\n'
                f'RESULTS_FILE_TMP="{results}"\n'
                'DB_GLOBAL="liferay-db-global"\n'
                "DB_GLOBAL_PREEXISTED=false\n"
                f"DB_CMD_OK={'true' if db_cmd_ok else 'false'}\n"
                f"{tail}\n"
            )
            res = subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True, check=False
            )
            recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
            return res.returncode, recorded

    def test_bash_cleans_up_when_the_check_failed(self):
        code, calls = self._run_bash_tail(db_cmd_ok=False)
        self.assertEqual(code, 1, "a failed shared-database check must still exit 1")
        self.assertIn(
            "rm -f liferay-db-global",
            calls,
            "the container this check provisioned was left behind on failure",
        )
        self.assertIn("volume rm liferay-db-global-data", calls)

    def test_bash_cleans_up_when_the_check_passed(self):
        code, calls = self._run_bash_tail(db_cmd_ok=True)
        self.assertEqual(code, 0)
        self.assertIn("rm -f liferay-db-global", calls)

    @staticmethod
    def _pwsh():
        return shutil.which("pwsh") or shutil.which("powershell")

    def _run_pwsh_tail(self, db_cmd_ok):
        tail = _extract_block_tail(
            PS1_SCRIPT,
            "-y db stop *> $dbCmdLog",
            'throw "Shared database start/stop verification failed."\n    }',
            re.compile(
                r"^    (?:# LDM-#1419: leave the machine|if \(\$dbCmdOk\) \{)", re.M
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp) / "docker-calls.txt"
            script = (
                f"function docker {{ $args -join ' ' | "
                f"Out-File -FilePath '{calls}' -Append -Encoding utf8 }}\n"
                "function Write-Verdict { param([string]$Message) "
                "Write-Output $Message }\n"
                "$dbGlobal = 'liferay-db-global'\n"
                "$dbGlobalPreexisted = $false\n"
                f"$dbCmdOk = ${'true' if db_cmd_ok else 'false'}\n"
                "$threw = $false\n"
                f"try {{\n{tail}\n}} catch {{ $threw = $true }}\n"
                'Write-Output "threw=$threw"\n'
            )
            res = subprocess.run(
                [self._pwsh(), "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, f"pwsh failed: {res.stderr}")
            recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
            return res.stdout, recorded

    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell not available on this host",
    )
    def test_powershell_cleans_up_when_the_check_failed(self):
        stdout, calls = self._run_pwsh_tail(db_cmd_ok=False)
        self.assertIn("threw=True", stdout, "a failed check must still throw")
        self.assertIn(
            "rm -f liferay-db-global",
            calls,
            "the .ps1 half leaked the container and volume on the failure path, "
            "while the .sh half cleaned up -- a parity violation",
        )
        self.assertIn("volume rm liferay-db-global-data", calls)

    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell not available on this host",
    )
    def test_powershell_cleans_up_when_the_check_passed(self):
        stdout, calls = self._run_pwsh_tail(db_cmd_ok=True)
        self.assertIn("threw=False", stdout)
        self.assertIn("rm -f liferay-db-global", calls)

    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell not available on this host",
    )
    def test_neither_half_touches_a_pre_existing_global(self):
        """LDM-#1419's actual contract: only remove what this check created."""
        tail = _extract_block_tail(
            PS1_SCRIPT,
            "-y db stop *> $dbCmdLog",
            'throw "Shared database start/stop verification failed."\n    }',
            re.compile(
                r"^    (?:# LDM-#1419: leave the machine|if \(\$dbCmdOk\) \{)", re.M
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp) / "docker-calls.txt"
            script = (
                f"function docker {{ $args -join ' ' | "
                f"Out-File -FilePath '{calls}' -Append -Encoding utf8 }}\n"
                "function Write-Verdict { param([string]$Message) "
                "Write-Output $Message }\n"
                "$dbGlobal = 'liferay-db-global'\n"
                "$dbGlobalPreexisted = $true\n"
                "$dbCmdOk = $false\n"
                f"try {{\n{tail}\n}} catch {{ }}\n"
            )
            subprocess.run(
                [self._pwsh(), "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertFalse(
                calls.exists(),
                "a global database the operator already had must not be removed",
            )


if __name__ == "__main__":
    unittest.main()
