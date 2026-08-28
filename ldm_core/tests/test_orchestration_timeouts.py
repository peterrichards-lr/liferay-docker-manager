"""start/stop/restart/down must bound their Docker calls (LDM-#1421).

`ldm_core/runtime/orchestration.py` had nine unbounded `run_command` calls on
the commands users run most often, every one of which can contact a daemon that
may never answer. LDM-#1410 showed the cost: a stalled socket wedged
`ldm restore` for 84 minutes across three machines, unkillable with Ctrl+C.

The point is attribution as much as recovery. Unbounded, a stalled daemon is
indistinguishable from LDM being slow. Bounded, `run_command` reports
`Command timed out after Ns: docker ...` and names the step.

Observed against the unfixed code before these were written: all three fail.
"""

import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "runtime" / "orchestration.py"


def _run_command_calls():
    """Every `.run_command(...)` call in the module, as AST nodes."""
    tree = ast.parse(SOURCE.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run_command"
    ]


class TestOrchestrationCallsAreBounded(unittest.TestCase):
    def test_every_run_command_passes_a_timeout(self):
        """Parsed, not grepped: a comment mentioning timeout would fool a grep."""
        unbounded = [
            call.lineno
            for call in _run_command_calls()
            if not any(kw.arg == "timeout" for kw in call.keywords)
        ]
        self.assertEqual(
            [],
            unbounded,
            f"unbounded run_command calls at {SOURCE.name} lines {unbounded}; "
            "a stalled daemon there is indistinguishable from LDM being slow "
            "(LDM-#1421).",
        )

    def test_timeouts_are_named_constants_not_magic_numbers(self):
        """The reasoning lives on the constant, per #1410/#1413."""
        literals = []
        for call in _run_command_calls():
            for kw in call.keywords:
                if kw.arg == "timeout" and isinstance(kw.value, ast.Constant):
                    literals.append((call.lineno, kw.value.value))
        self.assertEqual(
            [],
            literals,
            f"magic timeout numbers at lines {literals}; use the module "
            "constants so the sizing reasoning stays with the value.",
        )

    def test_the_volume_probe_tolerates_a_timeout_returning_none(self):
        """`check=False` returns None on timeout -- .strip() would raise.

        The AttributeError is swallowed by the surrounding `except Exception`
        and surfaces as "'NoneType' object has no attribute 'strip'", naming
        neither the timeout nor the daemon.
        """
        src = SOURCE.read_text()
        self.assertNotIn(
            "if res.strip():",
            src,
            "an unguarded .strip() on a check=False result: a timeout returns "
            "None there and the real cause is lost (LDM-#1421).",
        )
        self.assertIn("if res and res.strip():", src)


if __name__ == "__main__":
    unittest.main()
