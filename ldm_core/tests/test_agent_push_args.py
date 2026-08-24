"""Argument handling in `scripts/agent_push.sh` (LDM-#1316).

The commit message is positional. Before #1316 a git-style `-m` was consumed
*as* the message: the script ran the full gate suite, committed, and pushed a
branch whose commit subject was the literal string "-m", reporting success
throughout because nothing had gone wrong from its point of view.

These tests exercise the real parsing code rather than grepping the source for
it, but they must never be able to reach the gates, commit, or push. So the
script is truncated immediately after `COMMIT_MSG=` and the parsed value is
echoed instead -- everything below that line, including the staging guard and
`git push`, is discarded before anything runs.
"""

import shutil
import subprocess  # nosec B404
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "agent_push.sh"


def _parser_only(tmp: Path) -> Path:
    """Return a copy of the script cut off after the COMMIT_MSG assignment.

    Keeping the real `case`/`if` blocks verbatim is the point: a rewritten
    imitation of the parser would pass while the shipped script stayed broken.
    """
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("COMMIT_MSG="):
            body = lines[: i + 1]
            break
    else:  # pragma: no cover - only reachable if the script is restructured
        raise AssertionError("no COMMIT_MSG= assignment found in agent_push.sh")

    body.append('printf "%s" "$COMMIT_MSG"')
    harness = tmp / "parser_only.sh"
    harness.write_text("\n".join(body) + "\n", encoding="utf-8")
    return harness


@unittest.skipIf(shutil.which("bash") is None, "bash is not available")
class TestAgentPushArgumentGuard(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.harness = _parser_only(Path(self._tmp.name))

    def _run(self, *args):
        return subprocess.run(  # nosec B603
            ["bash", str(self.harness), *args],
            capture_output=True,
            text=True,
            timeout=30,
            # Non-zero is the expected outcome for every rejection case; the
            # tests assert on returncode directly.
            check=False,
        )

    def test_dash_m_is_accepted_and_the_message_comes_from_the_next_argument(self):
        """The #1316 regression, asserted on the parsed value itself.

        Checking only the exit code would pass even if `-m` were still taken as
        the message, since that path also succeeds.
        """
        res = self._run("-m", "fix(runtime): a real commit message")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, "fix(runtime): a real commit message")

    def test_bare_message_still_works(self):
        """Every existing caller passes a single positional string."""
        res = self._run("chore: unchanged behaviour")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, "chore: unchanged behaviour")

    def test_a_message_that_looks_like_a_flag_is_refused(self):
        res = self._run("--no-verify")
        self.assertEqual(res.returncode, 1)
        self.assertIn("positional", res.stderr)
        self.assertIn("--no-verify", res.stderr)

    def test_dash_m_without_a_message_is_refused(self):
        res = self._run("-m")
        self.assertEqual(res.returncode, 1)
        self.assertIn("Commit message required", res.stderr)

    def test_unquoted_message_arriving_as_many_arguments_is_refused(self):
        """The same mistake from the other side.

        `agent_push.sh fix: a message` would previously commit just "fix:" and
        drop the rest.
        """
        res = self._run("fix:", "a", "message")
        self.assertEqual(res.returncode, 1)
        self.assertIn("single quoted commit message", res.stderr)

    def test_no_arguments_is_refused(self):
        res = self._run()
        self.assertEqual(res.returncode, 1)
        self.assertIn("Commit message required", res.stderr)

    def test_help_exits_zero_without_a_message(self):
        res = self._run("--help")
        self.assertEqual(res.returncode, 0)
        self.assertIn("Usage:", res.stdout)

    def test_multiline_messages_survive(self):
        """Commit bodies are the normal case here, not an edge case."""
        msg = "fix(x): subject\n\nBody line one.\nBody line two."
        res = self._run(msg)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, msg)


if __name__ == "__main__":
    unittest.main()
