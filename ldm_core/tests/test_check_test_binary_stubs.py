"""The executable-stub lint must refuse what caused LDM-#1898, and nothing else.

Every case runs against a temp directory via ``--tests-dir`` (LDM-#1391): a
checker that can only scan the real tree cannot be tested without scanning it.

No file written here is ever made executable or run -- the lint reads source,
it does not execute it.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_test_binary_stubs",
    Path(__file__).parent.parent.parent / "scripts" / "check_test_binary_stubs.py",
)
assert _SPEC and _SPEC.loader
checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(checker)

ALLOW = "# lint: executable-stub -- bash execs it; name is not watched"


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _check(self, source: str) -> int:
        (self.dir / "test_probe.py").write_text(source)
        return checker.main(["--tests-dir", str(self.dir)])


class TestItRefusesAnUnexplainedExecutableStub(_Base):
    def test_an_octal_mode_with_the_exec_bit_is_refused(self):
        self.assertEqual(1, self._check("def f(p):\n    p.chmod(0o755)\n"))

    def test_a_stat_constant_is_refused(self):
        """LDM-#1898 used `st_mode | S_IEXEC`, not an octal literal."""
        source = (
            "import stat\n\n\n"
            "def f(p):\n"
            "    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)\n"
        )
        self.assertEqual(1, self._check(source))

    def test_a_bare_imported_constant_is_refused(self):
        source = "from stat import S_IXUSR\n\n\ndef f(p):\n    p.chmod(S_IXUSR)\n"
        self.assertEqual(1, self._check(source))


class TestItAllowsWhatIsExplained(_Base):
    def test_an_annotated_line_passes(self):
        self.assertEqual(
            0, self._check(f"def f(p):\n    {ALLOW}\n    p.chmod(0o755)\n")
        )

    def test_a_trailing_annotation_passes(self):
        self.assertEqual(0, self._check(f"def f(p):\n    p.chmod(0o755)  {ALLOW}\n"))

    def test_an_annotation_without_a_reason_does_not_count(self):
        """An empty excuse is not a reason; the lint exists to force the why."""
        source = "def f(p):\n    # lint: executable-stub --\n    p.chmod(0o755)\n"
        self.assertEqual(1, self._check(source))


class TestItStillRefusesAWatchedName(_Base):
    def test_an_annotated_stub_named_for_a_watched_binary_is_refused(self):
        """The annotation buys an executable file, not a dangerous name."""
        source = (
            "def f(d):\n"
            '    path = d / "lfr-tunnel"\n'
            f"    {ALLOW}\n"
            "    path.chmod(0o755)\n"
        )
        self.assertEqual(1, self._check(source))

    def test_a_safe_name_is_allowed(self):
        source = (
            "def f(d):\n"
            '    path = d / "ldm-stub"\n'
            f"    {ALLOW}\n"
            "    path.chmod(0o755)\n"
        )
        self.assertEqual(0, self._check(source))


class TestItDoesNotOverMatch(_Base):
    def test_a_non_executable_chmod_is_ignored(self):
        self.assertEqual(0, self._check("def f(p):\n    p.chmod(0o644)\n"))

    def test_a_file_merely_mentioning_a_watched_name_is_ignored(self):
        """No chmod, no finding -- the lint is about creating an executable."""
        source = 'MESSAGE = "run lfr-tunnel by hand"\nBIN = "ldm"\n'
        self.assertEqual(0, self._check(source))

    def test_an_unrelated_variable_named_for_a_binary_does_not_taint(self):
        """Rule 2 must trace the chmod target, not scan the whole file.

        Scanning the file would flag a module that merely mentions `ldm`, and a
        lint that cries wolf is a lint someone deletes.
        """
        source = (
            "def f(d):\n"
            '    other = d / "ldm"\n'
            '    path = d / "harmless"\n'
            f"    {ALLOW}\n"
            "    path.chmod(0o755)\n"
        )
        self.assertEqual(0, self._check(source))


if __name__ == "__main__":
    unittest.main()
