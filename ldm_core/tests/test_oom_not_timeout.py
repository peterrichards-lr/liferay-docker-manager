"""An OOM kill must not be reported as a health-check timeout (LDM-#1773).

Found while tracking down an OOM on an 8 GB machine running
`ldm quickstart aica`. LDM said:

```
⠼  Still waiting for Liferay (8m 20s)...
❌  Timed out waiting for Liferay to become healthy.
```

while the container said:

```
docker inspect -> OOMKilled: true, ExitCode: 0
/usr/local/bin/liferay_entrypoint.sh: line 87: 47 Killed  start_liferay.sh
```

Those are different problems. "Timed out" says *wait longer, or look at the
application*; `OOMKilled` says *this machine does not have enough memory*. The
operator was being pointed at the wrong one, eight minutes in.

## Why the existing fast-fail could not catch it

`ExitCode 0` is the entrypoint exiting cleanly after its child was SIGKILLed,
so nothing in the exit status contradicts the timeout message. The JVM is
killed while the entrypoint survives, so the container **stays up**, never goes
healthy, and falls out of the loop as a timeout -- `container_state == "exited"`
is never true. When the entrypoint does die with it, `exited unexpectedly` is
equally silent about the reason, so both endings are covered here.

## Why "could not ask" is not "not an OOM"

`container_was_oom_killed` returns `None` when the flag cannot be read, and the
caller must not turn that into a claim either way. There is nothing accurate to
say instead, so the original timeout wording stands -- but the OOM claim must
not be made on a reading that never happened.
"""

import itertools
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.docker_service import DockerService
from ldm_core.tests.test_readiness import MockRuntime

OOM_LINE = "killed by the kernel for running out of memory"
TIMEOUT_LINE = "Timed out waiting for Liferay to become healthy"
EXITED_LINE = "exited unexpectedly"


class TheFlagIsReadHonestly(unittest.TestCase):
    """`container_was_oom_killed` distinguishes true, false and unreadable."""

    def read_with(self, inspect_output):
        with patch("ldm_core.docker_service.run_command", return_value=inspect_output):
            return DockerService.container_was_oom_killed("liferay-x")

    def test_true_is_true(self):
        self.assertIs(self.read_with("true\n"), True)

    def test_false_is_false(self):
        """False must be False, not None -- the caller distinguishes them."""
        self.assertIs(self.read_with("false\n"), False)

    def test_case_and_whitespace_do_not_matter(self):
        self.assertIs(self.read_with("  True  "), True)

    def test_no_answer_is_none(self):
        """A container that cannot be inspected is unknown, not healthy."""
        self.assertIsNone(self.read_with(""))
        self.assertIsNone(self.read_with(None))

    def test_unexpected_output_is_none(self):
        """`docker inspect` on a missing container writes to stderr and may
        return anything at all on stdout. Only the two values it documents
        are treated as an answer."""
        self.assertIsNone(self.read_with("Error: No such object: liferay-x"))
        self.assertIsNone(self.read_with("<no value>"))

    def test_it_asks_the_node_the_project_runs_on(self):
        """A project on a remote node OOMs on the NODE. Asking the operator's
        laptop would read a container that is not there (LDM-#1727)."""
        with patch(
            "ldm_core.docker_service.run_command", return_value="true"
        ) as called:
            DockerService.container_was_oom_killed("liferay-x", target_name="aws-1")

        cmd = called.call_args[0][0]
        self.assertIn("aws-1", cmd)
        self.assertIn("{{.State.OOMKilled}}", cmd)


class TheWaitReportsTheRealCause(unittest.TestCase):
    """Drives the real `_wait_for_ready` to each of its two failure endings."""

    def setUp(self):
        self.runtime = MockRuntime()
        self.said: list[str] = []

    def _record(self, message, *_args, **_kwargs):
        self.said.append(str(message))

    def _drive(self, oom, container_state, clock):
        """Runs the real `_wait_for_ready` and returns everything it said."""
        self.runtime.manager.get_container_status = MagicMock(  # type: ignore[method-assign]
            return_value=container_state
        )
        with (
            patch(
                "ldm_core.handlers.base.BaseHandler.run_command",
                return_value="starting",
            ),
            patch("time.time", side_effect=clock),
            patch("time.sleep"),
            patch("requests.get", side_effect=OSError("no portal in a unit test")),
            patch(
                "ldm_core.docker_service.DockerService.container_was_oom_killed",
                return_value=oom,
            ),
            patch("ldm_core.ui.UI.error", side_effect=self._record),
            patch("ldm_core.ui.UI.detail", side_effect=self._record),
        ):
            result = self.runtime.handler._wait_for_ready({}, "localhost")

        self.assertFalse(result, "the wait must still fail")
        return "\n".join(self.said)

    def run_until_timeout(self, oom):
        """The reported ending: the container stays up and never goes healthy,
        so the loop runs out of budget."""

        def ticking():
            # start_time, then past the 600s budget on every check after it.
            yield 0
            while True:
                yield 700

        return self._drive(oom, "running", ticking())

    def run_until_exited(self, oom):
        """The other ending: the container is gone when the loop looks at it.
        The clock never advances, so the budget cannot end the loop -- only
        the `exited` branch can, which is what makes this test reach it."""
        return self._drive(oom, "exited", itertools.count(0, 1))

    def test_an_oom_is_named_as_an_oom(self):
        out = self.run_until_timeout(oom=True)

        self.assertIn(OOM_LINE, out)
        self.assertNotIn(TIMEOUT_LINE, out)

    def test_it_says_what_to_do_about_it(self):
        """ "It ran out of memory" is not actionable on its own; "give Docker
        more memory, and an imported package can pin a second Elasticsearch"
        is -- that is the stack that produced the report."""
        out = self.run_until_timeout(oom=True)

        self.assertIn("memory", out)
        self.assertIn("Elasticsearch", out)

    def test_a_genuine_timeout_still_reads_as_a_timeout(self):
        """The common case must not be relabelled. A guard that fires on
        everything says nothing."""
        out = self.run_until_timeout(oom=False)

        self.assertIn(TIMEOUT_LINE, out)
        self.assertNotIn(OOM_LINE, out)

    def test_an_unreadable_flag_does_not_become_an_oom_claim(self):
        """Could not ask != was killed. Reporting an OOM here would send the
        operator to buy memory for a problem that may not exist."""
        out = self.run_until_timeout(oom=None)

        self.assertIn(TIMEOUT_LINE, out)
        self.assertNotIn(OOM_LINE, out)

    def test_an_exited_container_reports_the_oom_too(self):
        """The other ending: when the entrypoint dies with the JVM, 'exited
        unexpectedly' is just as silent about the reason."""
        out = self.run_until_exited(oom=True)

        self.assertIn(OOM_LINE, out)
        self.assertNotIn(EXITED_LINE, out)

    def test_an_exited_container_without_an_oom_is_unchanged(self):
        out = self.run_until_exited(oom=False)

        self.assertIn(EXITED_LINE, out)
        self.assertNotIn(OOM_LINE, out)


if __name__ == "__main__":
    unittest.main()
