"""The published DXP domain must be ASSERTED, not merely found (LDM-#2154).

Three existing assertions walk `routes/default/dxp` -- scaffolding
(LDM-#1928), mount anchoring (LDM-#1944), readability (LDM-#1946) -- and none
reads a byte of what the files contain. A tree holding the wrong domain passed
all three, which is how a project created with `--host-name aica-e2e.demo`
could publish `localhost:8080` and be called a pass.

`scripts/check_published_dxp_domain.py` is the missing content assertion, and
is shared by both halves of the E2E suite so they cannot drift (LDM-#1982).

These run the script as a subprocess rather than importing it, because the exit
code IS the contract the shell halves consume: 0 asserted, 1 disagreement,
2 nothing to evaluate -- and 2 must never be reported as a pass.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
CHECKER = SCRIPTS_DIR / "check_published_dxp_domain.py"

ASSERTED, DISAGREES, CHECK_DID_NOT_RUN, NOTHING_TO_EVALUATE = 0, 1, 2, 3


class _Project:
    """A project tree with just the two things the checker reads."""

    def __init__(self, tmp, *, host_name=None, published=None):
        self.root = Path(tmp) / "proj"
        (self.root / "routes" / "default" / "dxp").mkdir(parents=True)
        if host_name is not None:
            (self.root / "meta").write_text(
                json.dumps({"host_name": host_name, "tag": "2026.q1.7-lts"}),
                encoding="utf-8",
            )
        for name, value in (published or {}).items():
            (self.root / "routes" / "default" / "dxp" / name).write_text(
                value, encoding="utf-8"
            )

    def check(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER), str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, result.stdout + result.stderr


def _published(domain):
    return {
        "com.liferay.lxc.dxp.main.domain": domain,
        "com.liferay.lxc.dxp.mainDomain": domain,
        "com.liferay.lxc.dxp.domains": domain,
        "com.liferay.lxc.dxp.server.protocol": "https",
    }


class TestTheDomainIsAsserted(unittest.TestCase):
    def test_the_configured_host_name_is_what_was_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, host_name="localhost", published=_published("localhost")
            ).check()
        self.assertEqual(code, ASSERTED, out)

    def test_a_published_domain_that_is_not_the_host_name_fails(self):
        """The measured LDM-#2154 shape: configured right, published wrong."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp,
                host_name="aica-e2e.demo",
                published=_published("localhost:8080"),
            ).check()
        self.assertEqual(code, DISAGREES, out)
        # Both values must be in the message: a failure naming only one of them
        # sends the reader back to the machine to find the other.
        self.assertIn("aica-e2e.demo", out)
        self.assertIn("localhost:8080", out)

    def test_a_port_suffix_on_the_right_host_is_not_a_failure(self):
        """Liferay appends the port for a host it does not treat as virtual.

        A bare string comparison would fail here for a reason that is not the
        defect, and a suite that cries wolf gets its assertions deleted.
        """
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, host_name="localhost", published=_published("localhost:8080")
            ).check()
        self.assertEqual(code, ASSERTED, out)

    def test_the_comparison_ignores_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, host_name="Demo.Local", published=_published("demo.local")
            ).check()
        self.assertEqual(code, ASSERTED, out)


class TestEveryPublishedValueReachesTheReport(unittest.TestCase):
    """The reporting team had to infer this value from a failure.

    Recording all four means a future disagreement is readable from the report
    without re-running anything.
    """

    def test_all_four_values_are_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, host_name="localhost", published=_published("localhost")
            ).check()
        self.assertEqual(code, ASSERTED, out)
        for name in _published("localhost"):
            self.assertIn(name, out)
        self.assertIn("https", out)

    def test_the_values_are_printed_even_when_the_assertion_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, host_name="demo.test", published=_published("localhost:8080")
            ).check()
        self.assertEqual(code, DISAGREES, out)
        self.assertIn("com.liferay.lxc.dxp.server.protocol", out)


class TestWhatCannotBeEvaluatedIsNotAPass(unittest.TestCase):
    """Exit 2, never 0. LDM-#2042: on some backends the tree is not published
    within the window, and a silent pass there is the failure mode this suite
    has been bitten by repeatedly."""

    def test_an_empty_tree_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(tmp, host_name="localhost", published={}).check()
        self.assertEqual(code, NOTHING_TO_EVALUATE, out)
        self.assertIn("SKIPPED", out)

    def test_no_tree_at_all_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bare"
            root.mkdir()
            result = subprocess.run(
                [sys.executable, str(CHECKER), str(root)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, NOTHING_TO_EVALUATE, result.stdout)

    def test_a_project_with_no_host_name_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(tmp, published=_published("localhost")).check()
        self.assertEqual(code, NOTHING_TO_EVALUATE, out)
        self.assertIn("SKIPPED", out)

    def test_bad_usage_is_an_error_not_a_skip(self):
        """2, not 3. `python` exits 2 when it cannot open the script at all,
        and the first execution of the PowerShell call site reported that as
        SKIPPED -- so the skip code must be one python never produces."""
        result = subprocess.run(
            [sys.executable, str(CHECKER)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, CHECK_DID_NOT_RUN)
        self.assertIn("usage:", result.stdout)


class TestAMissingOrEmptyDomainIsAFailure(unittest.TestCase):
    def test_a_tree_without_the_main_domain_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp,
                host_name="localhost",
                published={"com.liferay.lxc.dxp.server.protocol": "https"},
            ).check()
        self.assertEqual(code, DISAGREES, out)

    def test_an_empty_main_domain_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _Project(
                tmp, host_name="localhost", published=_published("")
            ).check()
        self.assertEqual(code, DISAGREES, out)


class TestBothHalvesCallIt(unittest.TestCase):
    """A source-text parity check, deliberately (LDM-#1982).

    The behaviour needs Docker, an `ldm` binary and minutes of runtime. What
    this CAN catch is the failure that has actually happened in this repo: a
    check living in one script and not the other, after a `.ps1` edit silently
    dropped one the `.sh` kept.

    Both call sites were additionally EXECUTED against fixtures when they were
    written -- match, mismatch, empty tree and a missing checker -- because a
    name that appears in a file is not a call site that runs (LDM-#2132).
    """

    SCRIPTS = (
        SCRIPTS_DIR / "verify_e2e_refactor.sh",
        SCRIPTS_DIR / "verify_e2e_refactor.ps1",
    )

    def test_both_halves_invoke_the_shared_checker(self):
        for script in self.SCRIPTS:
            with self.subTest(script=script.name):
                self.assertIn(
                    CHECKER.name,
                    script.read_text(encoding="utf-8"),
                    f"{script.name} never runs {CHECKER.name}, so on that "
                    f"platform nothing reads what Liferay published into the "
                    f"dxp tree (LDM-#2154)",
                )

    def test_both_halves_treat_the_skip_code_as_a_skip_and_not_a_pass(self):
        """3, not 2: `python` exits 2 when it cannot open the checker."""
        for script in self.SCRIPTS:
            text = script.read_text(encoding="utf-8")
            with self.subTest(script=script.name):
                self.assertTrue(
                    "-eq 3" in text or "-eq 3)" in text,
                    f"{script.name} does not branch on exit 3, so a tree that "
                    f"was never published reads as something other than "
                    f"SKIPPED (LDM-#2154)",
                )


if __name__ == "__main__":
    unittest.main()
