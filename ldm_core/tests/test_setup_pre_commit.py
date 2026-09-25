"""LDM-#1950: `scripts/setup_pre_commit.sh` did not install the git hook.

Two independent defects in one `if`:

1. The guard asked `command -v pre-commit`, which searches the WHOLE PATH. A
   global Homebrew `pre-commit` satisfied it, so `requirements-dev.txt` was
   never installed into the fresh venv and the script failed at its own
   `python3 -m pre_commit run` step. It also probes a console script by name,
   which this repo already knows is unreliable -- see LDM-#1244/#1245 and
   `test_venv_console_scripts.py`.

2. `pre_commit install` was nested INSIDE that guard, so once the dev
   requirements were present the hook was never (re)installed. That is exactly
   when it matters: `.git/hooks/pre-commit` carries an ABSOLUTE interpreter
   path and lives in the common `.git` directory shared by every worktree, so
   whichever checkout installed last owns the hook for all of them. Delete that
   checkout and the hook points at a missing interpreter.

The consequence is silent: commits stop being gated, and nothing says so.

These are text assertions because the script's failure mode is structural --
which command runs, and whether it sits inside a conditional. Executing it
would need a real venv and would install a hook into this repo's .git.
"""

import re
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_pre_commit.sh"


class TestSetupPreCommitScript(unittest.TestCase):
    text: str
    lines: list[str]

    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.lines = cls.text.splitlines()

    def _code_lines(self):
        """Source lines with comments and blanks dropped.

        The fix is described at length in comments that necessarily quote the
        old command, so a naive `assertNotIn` over the whole file would fail on
        its own documentation.
        """
        out = []
        for line in self.lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                out.append(line)
        return out

    def test_it_does_not_probe_the_console_script_on_path(self):
        code = "\n".join(self._code_lines())
        self.assertNotIn(
            "command -v pre-commit",
            code,
            "a global pre-commit on PATH satisfies this guard while the venv "
            "has none (LDM-#1950)",
        )

    def test_it_asks_the_venv_interpreter_instead(self):
        code = "\n".join(self._code_lines())
        self.assertIn("python3 -m pre_commit --version", code)

    def test_the_hook_install_is_not_inside_the_dependency_guard(self):
        """The core of #1950.

        `python3 -m pre_commit install` must run every time, not only on the
        branch where dependencies were missing.
        """
        code = self._code_lines()
        install_idx = next(
            (
                i
                for i, line in enumerate(code)
                if re.search(r"python3 -m pre_commit install\b", line)
            ),
            None,
        )
        self.assertIsNotNone(install_idx, "the hook install has gone entirely")

        self.assertFalse(
            code[install_idx].startswith((" ", "\t")),
            "`pre_commit install` is indented, i.e. nested inside a "
            "conditional again -- it must run unconditionally (LDM-#1950)",
        )

    def test_the_install_runs_after_the_dependency_guard_closes(self):
        """Ordering, not just nesting: installing before the deps are present
        would fail on a fresh clone."""
        code = self._code_lines()

        def _find(pred, what):
            idx = next((i for i, ln in enumerate(code) if pred(i, ln)), None)
            self.assertIsNotNone(idx, f"{what} not found in the script")
            return idx

        guard = _find(
            lambda _i, ln: "python3 -m pre_commit --version" in ln,
            "the venv dependency guard",
        )
        closes = _find(
            lambda i, ln: i > guard and ln.strip() == "fi",
            "the `fi` closing the dependency guard",
        )
        install = _find(
            lambda _i, ln: "python3 -m pre_commit install" in ln,
            "the hook install",
        )
        self.assertGreater(
            install, closes, "hook install must follow the dependency guard"
        )

    def test_it_still_installs_the_requirements(self):
        code = "\n".join(self._code_lines())
        self.assertIn("pip install -r requirements-dev.txt", code)


if __name__ == "__main__":
    unittest.main()
