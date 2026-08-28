"""--no-jvm-verify and --no-tld-skip do nothing, and must say so (LDM-#1447).

`docs/reference/advanced_cli.md` described both as disabling defaults LDM
applied: a `-Xverify:none` bytecode-verification skip, and a Tomcat TLD scanning
skip. Neither default existed. Both values were read from the command line,
written to project metadata, and never read again -- the same inert shape as the
UUID labels in #1395.

They are kept rather than removed because AGENTS.md forbids breaking existing
flags: dropping them would fail any script that passes one. So the contract is
narrower -- they may do nothing, but nothing may claim they do something.

These tests fail if someone reinstates the false documentation, and also if
someone implements the behaviour without updating the docs, which is the outcome
we actually want.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs" / "reference" / "advanced_cli.md"
COMPOSER = REPO / "ldm_core" / "handlers" / "composer.py"
RUN_PIPELINE = REPO / "ldm_core" / "pipelines" / "run.py"


class TestInertJvmFlags(unittest.TestCase):
    def test_docs_do_not_claim_a_bytecode_verification_skip_default(self):
        """The claimed default does not exist, so the page must not promise it."""
        text = DOCS.read_text()
        self.assertNotRegex(
            text,
            r"skip is enabled by default",
            "advanced_cli.md claims LDM enables a bytecode-verification skip by "
            "default. It does not -- see test_no_xverify_none_anywhere below.",
        )

    def test_docs_do_not_claim_a_tld_skip_default(self):
        text = DOCS.read_text()
        self.assertNotRegex(
            text,
            r"LDM skips scanning non-Liferay jars by default",
            "advanced_cli.md claims a TLD skip default that does not exist.",
        )

    def test_no_xverify_none_anywhere_in_the_product(self):
        """The evidence behind the claim above.

        Also a guard: -Xverify:none is deprecated since JDK 13 and these images
        run Java 21 (compatibility.json, >=2025.q2.0), where it warns and does
        nothing. Adding it would cost a warning on every startup for no gain.
        """
        offenders = []
        for path in (REPO / "ldm_core").rglob("*.py"):
            if "/tests/" in path.as_posix():
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                # Comments explaining *why* the flag is absent are not usages,
                # and this file's own rationale would otherwise trip it.
                if line.lstrip().startswith("#"):
                    continue
                if re.search(r"-Xverify\s*:\s*none|-noverify", line):
                    offenders.append(f"{path.relative_to(REPO).as_posix()}:{lineno}")
        self.assertEqual(
            [],
            offenders,
            f"-Xverify:none reintroduced in {offenders}. It is obsolete on "
            "Java 21; if this is deliberate, update advanced_cli.md too.",
        )

    def test_jvm_args_is_documented_as_replacing_not_extending(self):
        """The property that makes --jvm-args limiting must be stated.

        A user reaching for it to change one value loses the adaptive sizing,
        which is the whole motivation for LDM-#1446.
        """
        text = DOCS.read_text()
        self.assertRegex(
            text,
            r"replaces LDM's defaults entirely|not additive",
            "advanced_cli.md must state that --jvm-args discards the adaptive "
            "defaults rather than adding to them (LDM-#1446).",
        )

    def test_passing_an_inert_flag_warns(self):
        """Silence is how this survived unnoticed across several releases."""
        src = RUN_PIPELINE.read_text()
        self.assertIn(
            "has no effect and is accepted only for",
            src,
            "passing --no-jvm-verify / --no-tld-skip must warn that it does "
            "nothing; a flag that silently no-ops is indistinguishable from one "
            "that works.",
        )


if __name__ == "__main__":
    unittest.main()
