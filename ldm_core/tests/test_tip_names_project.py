"""Tips must name the project they refer to (LDM-#1508).

`ldm run` finished with:

    (password masked -- use 'ldm info --credentials' to reveal it)

Following that literally opens the project-selection prompt, so the user has to
scroll back for a generated name like `ldm-1788250086` and retype it:

    $ ldm info --credentials
    === Select Project ===
    ❓  Select index, type to filter, 'n' for new, 's' to skip, or 'q' to quit [1]: ^C

The same block already prints `Next: ldm logs ldm-1788250086`, so one tip was
inconsistent with its immediate neighbours -- which is what made it read as
broken rather than merely terse.

These assert the SOURCE rather than rendered output: the tips sit deep inside
the run pipeline and readiness loop, and reaching them in a unit test would
need a booted project. A source assertion still catches the regression, which
is a hardcoded command string losing its project argument.
"""

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# (file, the command a tip mentions) -- each must interpolate the project.
TIP_SITES = [
    ("runtime/readiness.py", "ldm info"),
    ("pipelines/run.py", "ldm logs -f"),
    ("workspace/importer.py", "ldm logs -f"),
]


class TestTipsNameTheProject(unittest.TestCase):
    def _contains(self, rel, needle):
        """True if `needle` appears in `rel`.

        Deliberately not assertIn: its failure message prints the entire
        haystack, and these haystacks are whole source files -- an 80KB dump
        that buries the one line that matters.
        """
        return needle in (_ROOT / rel).read_text(encoding="utf-8")

    def test_credentials_tip_carries_the_project(self):
        self.assertTrue(
            self._contains(
                "runtime/readiness.py", "ldm info {project_id} --credentials"
            ),
            "readiness.py: the credentials tip must name the project, or "
            "following it opens the selection prompt (LDM-#1508)",
        )

    def test_no_tip_mentions_a_bare_credentials_command(self):
        self.assertFalse(
            self._contains("runtime/readiness.py", "'ldm info --credentials'"),
            "readiness.py: tip omits the project (LDM-#1508)",
        )

    def test_run_hint_names_the_project(self):
        self.assertTrue(
            self._contains("pipelines/run.py", "ldm logs -f {project_id}"),
            "run.py: post-run hint omits the project (LDM-#1508)",
        )

    def test_importer_hint_names_the_project(self):
        self.assertTrue(
            self._contains("workspace/importer.py", "ldm logs -f {project_name}"),
            "importer.py: hint omits the project (LDM-#1508)",
        )

    def test_no_bare_logs_follow_tip_remains(self):
        """`'ldm logs -f'` with nothing after it sends the user to the prompt."""
        offenders = []
        for rel, _cmd in TIP_SITES:
            src = (_ROOT / rel).read_text(encoding="utf-8")
            for match in re.finditer(r"'ldm logs -f'", src):
                offenders.append(f"{rel}:{src[: match.start()].count(chr(10)) + 1}")
        self.assertEqual([], offenders, f"tip omits the project: {offenders}")


if __name__ == "__main__":
    unittest.main()
