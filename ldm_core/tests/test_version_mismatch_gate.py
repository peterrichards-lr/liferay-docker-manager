"""A version mismatch must stop the run, not warn and continue (LDM-#1529).

The verify scripts printed:

    ⚠️  WARNING: this script (v2.19.0) does not match the installed ldm binary (v2.20.0-pre.1).

and carried on. The resulting report is headed

    Version:      ldm 2.20.0-pre.1
    Script Ver:   2.19.0

and is committed to references/verification-results/ as a permanent record
under the Honesty Rule. A warning 200 lines up the scroll does not survive into
the file the way the version headers do.

A mismatched run also answers a question nobody asked: it exercises THIS binary
with THAT version's assertions, so a check added for the new version is absent
while the report looks complete, and a check removed in it still runs and can
fail for a reason that no longer applies.
"""

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SH = _ROOT / "scripts" / "verify_e2e_refactor.sh"
_PS1 = _ROOT / "scripts" / "verify_e2e_refactor.ps1"


class TestBothScriptsRefuse(unittest.TestCase):
    def test_sh_exits_on_mismatch(self):
        src = _SH.read_text(encoding="utf-8")
        self.assertIn(
            "Refusing to run",
            src,
            "verify_e2e_refactor.sh must refuse, not warn (LDM-#1529)",
        )

    def test_ps1_exits_on_mismatch(self):
        src = _PS1.read_text(encoding="utf-8")
        self.assertIn("Refusing to run", src)

    def test_both_offer_a_declared_opt_out(self):
        """Verifying a deliberately different binary must stay possible."""
        self.assertIn("--allow-version-mismatch", _SH.read_text(encoding="utf-8"))
        self.assertIn("AllowVersionMismatch", _PS1.read_text(encoding="utf-8"))


class TestGateAndBannerAgree(unittest.TestCase):
    """Two parsers for one value is how a gate disagrees with what it prints."""

    def test_sh_gate_reuses_the_banner_regex(self):
        src = _SH.read_text(encoding="utf-8")
        occurrences = len(re.findall(re.escape("[0-9]+\\.[0-9]+\\.[0-9]+"), src))
        self.assertGreaterEqual(
            occurrences,
            2,
            "the gate must use the same extraction as the banner, so the two "
            "can never report different versions",
        )

    def test_ps1_gate_reuses_the_banner_regex(self):
        src = _PS1.read_text(encoding="utf-8")
        occurrences = src.count(r"(\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?)")
        self.assertGreaterEqual(occurrences, 2)


class TestPowerShellParamPlacement(unittest.TestCase):
    """`param` must be the FIRST statement, not merely present.

    PowerShell's parser accepts a later param block and PSScriptAnalyzer passes
    it, but the runtime rejects it -- so a misplacement is invisible until the
    script runs on Windows. Confirmed empirically:

        param: /tmp/paramtest.ps1:3
        Line |
           3 | param([switch]$Flag)
    """

    def test_param_precedes_any_code(self):
        lines = _PS1.read_text(encoding="utf-8").splitlines()
        param_at = next(
            (i for i, ln in enumerate(lines) if ln.strip().startswith("param(")), None
        )
        if param_at is None:
            # self.fail is NoReturn, which narrows for mypy where
            # assertIsNotNone does not.
            self.fail("no param block found")

        for i, ln in enumerate(lines[:param_at]):
            stripped = ln.strip()
            if not stripped or stripped.startswith("#"):
                continue
            self.fail(
                f"code at line {i + 1} precedes the param block "
                f"(line {param_at + 1}): {stripped[:60]!r} -- PowerShell "
                "rejects this at runtime even though it parses"
            )


if __name__ == "__main__":
    unittest.main()
