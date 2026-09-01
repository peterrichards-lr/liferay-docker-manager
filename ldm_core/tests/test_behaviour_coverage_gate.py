"""The LDM-#1516 gate must actually print, and must never block a cut.

Written deliberately as a behavioural test rather than a source grep, because
the gate exists to stop exactly that: asserting a string appears in a file
while nothing runs it. Neutering print_behaviour_coverage_gate to a no-op must
fail these.
"""

import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_RELEASE_PY = _ROOT / "scripts" / "release.py"


def _load_release_module():
    spec = importlib.util.spec_from_file_location("_release_under_test", _RELEASE_PY)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {_RELEASE_PY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBehaviourCoverageGatePrints(unittest.TestCase):
    def setUp(self):
        self.release = _load_release_module()

    def _run(self, previous_tag):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.release.print_behaviour_coverage_gate(previous_tag)
        return buf.getvalue()

    def test_it_asks_the_question(self):
        out = self._run("v2.19.0")
        self.assertIn("Behaviour Coverage Gate", out)
        self.assertIn("EXERCISES", out)
        # the three answers must all be offered -- "config-only" being absent
        # would quietly turn a recorded choice back into a silent default
        for answer in ("exercised", "config-only", "not covered"):
            with self.subTest(answer=answer):
                self.assertIn(answer, out)

    def test_it_lists_what_landed(self):
        # v2.19.0 is a real tag with real commits after it.
        out = self._run("v2.19.0")
        self.assertIn("Landed since v2.19.0", out)

    def test_an_unknown_tag_does_not_raise(self):
        # A bad ref must degrade to "nothing found", never abort a release:
        # the gate is a reminder, and a reminder that can kill the cut is
        # worse than the omission it guards against.
        out = self._run("v0.0.0-does-not-exist")
        self.assertIn("Behaviour Coverage Gate", out)

    def test_no_previous_tag_is_survivable(self):
        out = self._run(None)
        self.assertIn("Behaviour Coverage Gate", out)

    def test_it_does_not_read_stdin(self):
        # Non-blocking by design (chosen deliberately over a confirmation
        # prompt): an unattended run must not hang. If this ever grows an
        # input() call, this test is the thing that should stop it.
        import builtins

        called: list = []
        original = builtins.input

        def _recording_input(*args, **kwargs):
            called.append(args)
            return ""

        builtins.input = _recording_input
        try:
            self._run("v2.19.0")
        finally:
            builtins.input = original
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
