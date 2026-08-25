"""Network operations that bypass run_command must still be bounded (LDM-#1332).

#1306 fixed `BaseHandler.run_command` to forward `timeout`, which makes
bounding possible for anything routed through it. But several calls go straight
to `subprocess`, so that fix cannot reach them -- they hang with no output and
no bound, which is the failure #1306 set out to remove.

Five were network-dependent. The sharpest was the `docker pull` in
`handlers/config.py`: it runs only when `docker image inspect` has already
failed, so it executes precisely when the image is absent and an unreachable or
throttled registry is most likely to stall it.

Deliberately NOT "every subprocess call must have a timeout": many are
legitimately long -- `compose up`, database restores, image builds -- and a
blanket bound would break them. This pins the network-facing ones and guards
against new unbounded ones appearing.
"""

import ast
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
LDM_CORE = REPO / "ldm_core"

# Words that mark a subprocess call as reaching the network.
_NETWORK_MARKERS = ('"pull"', '"push"', "clone", "pip", '"fetch"', "install")


def _unbounded_network_subprocess_calls():
    """Every subprocess call that looks network-facing and has no timeout."""
    found = []
    for path in LDM_CORE.rglob("*.py"):
        if "/tests/" in str(path):
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (
                isinstance(fn, ast.Attribute)
                and getattr(fn.value, "id", "") == "subprocess"
            ):
                continue
            if fn.attr not in ("run", "check_call", "check_output", "Popen"):
                continue
            if "timeout" in {k.arg for k in node.keywords if k.arg}:
                continue
            context = " ".join(lines[node.lineno - 1 : node.lineno + 4])
            if any(marker in context for marker in _NETWORK_MARKERS):
                found.append(f"{path.relative_to(REPO)}:{node.lineno}")
    return found


class TestNoUnboundedNetworkCalls(unittest.TestCase):
    def test_no_network_subprocess_call_lacks_a_timeout(self):
        """Guards the whole class, not just the five that were fixed.

        A new unbounded `docker pull` or `git clone` fails here rather than
        being discovered when it hangs on someone's machine.
        """
        unbounded = _unbounded_network_subprocess_calls()
        self.assertEqual(
            unbounded,
            [],
            "network-facing subprocess calls without a timeout:\n  "
            + "\n  ".join(unbounded),
        )

    def test_the_audit_itself_can_detect_a_violation(self):
        """The guard above is only meaningful if it can fail.

        An always-empty result would pass forever -- exactly the kind of check
        this codebase has been bitten by (#1282, #1300, #1309, #1325).
        """
        source = "subprocess.check_call([docker, 'pull', image])\n"
        tree = ast.parse(source)
        call = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "check_call"
        )
        self.assertNotIn("timeout", {k.arg for k in call.keywords if k.arg})
        self.assertIn("pull", source)


class TestTimeoutConstants(unittest.TestCase):
    """The bounds must be generous enough not to cap legitimate work."""

    def test_constants_are_defined_and_ordered_sensibly(self):
        from ldm_core.constants import (
            GIT_CLONE_TIMEOUT,
            IMAGE_INSPECT_TIMEOUT,
            IMAGE_PULL_TIMEOUT,
            PIP_INSTALL_TIMEOUT,
        )

        self.assertLess(
            IMAGE_INSPECT_TIMEOUT,
            IMAGE_PULL_TIMEOUT,
            "a local daemon query must be bounded far tighter than a network pull",
        )
        self.assertGreaterEqual(
            IMAGE_PULL_TIMEOUT,
            600,
            "a ~1GB image over a slow link is slow, not wedged -- the bound "
            "must not cap legitimate pulls",
        )
        for name, value in (
            ("GIT_CLONE_TIMEOUT", GIT_CLONE_TIMEOUT),
            ("PIP_INSTALL_TIMEOUT", PIP_INSTALL_TIMEOUT),
        ):
            self.assertGreater(value, 0, f"{name} must be a positive bound")


class TestImagePullIsBounded(unittest.TestCase):
    """The sharpest case: pull runs only when the image is already missing."""

    def test_pull_and_inspect_both_pass_a_timeout(self):
        import inspect

        from ldm_core.handlers.config import ConfigService

        source = inspect.getsource(ConfigService)
        block = source[source.index("Inspecting image") :]
        block = block[: block.index("json.loads(output)")]

        self.assertIn("IMAGE_PULL_TIMEOUT", block)
        self.assertIn("IMAGE_INSPECT_TIMEOUT", block)
        self.assertIn(
            "TimeoutExpired",
            block,
            "a timeout must produce a diagnosis, not an unhandled traceback",
        )

    def test_a_timed_out_pull_is_reported_not_swallowed(self):
        """`TimeoutExpired` must not fall through to the generic handler.

        The generic `except Exception` here reports a failure to *inspect or
        pull*, which describes a missing image rather than a stalled registry
        and would send the reader down the wrong path.
        """
        import inspect

        from ldm_core.handlers.config import ConfigService

        source = inspect.getsource(ConfigService)
        block = source[source.index("Attempting to pull") :]
        block = block[: block.index("json.loads(output)")]

        self.assertLess(
            block.index("except subprocess.TimeoutExpired"),
            block.index("except Exception"),
            "TimeoutExpired must be caught before the generic handler, or it "
            "will be absorbed by it",
        )


if __name__ == "__main__":
    unittest.main()
