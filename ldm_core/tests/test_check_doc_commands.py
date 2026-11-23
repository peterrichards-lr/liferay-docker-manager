"""Documented `ldm` commands must parse with the real CLI parser (LDM-#2149).

The checker lives in scripts/check_doc_commands.py. These tests feed it small
Markdown fixtures through `check_text`, with the real `get_parser()`, so a
regression in either the extraction or the parse call fails here. One test
also runs the script as a subprocess over a planted file, because the exit
code and the `file:line` naming are what the pre-commit hook relies on.
"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ldm_core.cli import get_parser, preprocess_args

SCRIPT = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "check_doc_commands.py"
)

_spec = importlib.util.spec_from_file_location("check_doc_commands", SCRIPT)
assert _spec is not None and _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = checker  # @dataclass resolves its module by name
_spec.loader.exec_module(checker)

PARSER, _ = get_parser()


def _check(markdown: str):
    return checker.check_text("doc.md", markdown, PARSER, preprocess_args)


def _block(*lines: str, lang: str = "bash") -> str:
    return "\n".join([f"```{lang}", *lines, "```", ""])


class TestValidCommandsPass(unittest.TestCase):
    def test_real_commands_parse(self):
        failures, checked, skipped = _check(
            _block(
                "ldm run demo --tag-latest --release-type qr",
                'ldm db query demo --sql "SELECT 1"',
                "ldm config env demo KEY --remove",
                "ldm restore --list",
                "ldm --version",
            )
        )
        self.assertEqual(failures, [])
        self.assertEqual((checked, skipped), (5, 0))

    def test_lines_outside_fences_are_not_parsed(self):
        failures, checked, _ = _check("Run `ldm frobnicate` now.\n\nldm frobnicate\n")
        self.assertEqual((failures, checked), ([], 0))


class TestInvalidCommandsFail(unittest.TestCase):
    def test_unknown_flag_fails_naming_file_and_line(self):
        failures, _, _ = _check(
            "# Title\n\n" + _block("ldm run demo", "ldm run demo --bogus-flag")
        )
        self.assertEqual(len(failures), 1)
        self.assertEqual((failures[0].path, failures[0].line), ("doc.md", 5))
        self.assertIn("--bogus-flag", failures[0].reason)
        self.assertTrue(
            str(failures[0]).startswith("doc.md:5: ldm run demo --bogus-flag")
        )

    def test_flag_on_the_wrong_subcommand_fails(self):
        # --ssl-port exists, but on `infra setup`, not on `run`: the token-level
        # cli-drift check cannot see this.
        failures, _, _ = _check(_block("ldm run --ssl-port 8443"))
        self.assertEqual(len(failures), 1)

    def test_unknown_subcommand_and_surplus_positional_fail(self):
        failures, _, _ = _check(
            _block("ldm reseed demo", 'ldm db query demo "SELECT 1"')
        )
        self.assertEqual([f.line for f in failures], [2, 3])

    def test_script_exits_one_and_names_the_planted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "planted.md"
            doc.write_text(_block("ldm stop demo", "ldm stop demo --no-such-option"))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(doc)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("planted.md:3: ldm stop demo --no-such-option", result.stdout)
        self.assertIn("1 failure(s)", result.stdout)


class TestOptOutMarker(unittest.TestCase):
    def test_line_marker_skips_and_is_counted(self):
        failures, checked, skipped = _check(
            _block("ldm run --bogus  # ldm-check: skip -- shows the error")
        )
        self.assertEqual((failures, checked, skipped), ([], 0, 1))

    def test_block_marker_skips_only_the_next_block(self):
        md = (
            "<!-- ldm-check: skip -- error demonstration -->\n\n"
            + _block("ldm run --bogus", "ldm stop --bogus")
            + "\n"
            + _block("ldm run --bogus")
        )
        failures, checked, skipped = _check(md)
        self.assertEqual((checked, skipped), (1, 2))
        self.assertEqual(len(failures), 1)

    def test_unmarked_invalid_example_is_not_skipped(self):
        failures, _, skipped = _check(_block("ldm run --bogus  # this fails"))
        self.assertEqual((len(failures), skipped), (1, 0))


class TestNormalisation(unittest.TestCase):
    def assert_parses(self, *lines, lang="bash"):
        failures, checked, _ = _check(_block(*lines, lang=lang))
        self.assertEqual(failures, [], [str(f) for f in failures])
        self.assertEqual(checked, len(lines))

    def test_placeholders(self):
        self.assert_parses("ldm run <project> --host-name <your-host>")

    def test_prompts_sudo_and_assignments(self):
        self.assert_parses(
            "$ ldm status demo",
            "% ldm logs demo",
            "PS> ldm doctor",
            "sudo ldm system upgrade",
            "LDM_HOME=/tmp/x ldm list",
            lang="console",
        )

    def test_pipes_operators_redirections_and_comments(self):
        self.assert_parses(
            "ldm list --json | jq '.[]'",
            "ldm status demo && ldm logs demo",
            "ldm logs demo > out.txt 2>&1",
            'ldm db query demo --sql "SELECT 1" < /dev/null',
            "ldm stop demo   # stops it",
        )

    def test_continuations_and_shell_variables(self):
        failures, checked, _ = _check(
            _block(
                'ldm run "$PROJECT" \\',
                "  --host-name ${HOST} \\",
                "  --port $(free_port)",
            )
        )
        self.assertEqual((failures, checked), ([], 1))

    def test_usage_synopsis_is_checked_in_full(self):
        self.assert_parses(
            "ldm config ssl-mode <hosts | share> [project] [--subdomain <subdomain>]",
            "ldm system init-ci [project] [--trigger release|tag|push|manual] [--setup-step CMD ...]",
        )
        # A bad flag inside an optional group is still a bad flag.
        failures, _, _ = _check(_block("ldm stop [project] [--no-such-flag]"))
        self.assertEqual(len(failures), 1)

    def test_pipe_does_not_hide_an_error_before_it(self):
        failures, _, _ = _check(_block("ldm list --bogus | jq ."))
        self.assertEqual(len(failures), 1)


class TestWriteStatementGuard(unittest.TestCase):
    def test_writes_are_refused_with_why_and_instead(self):
        for line in (
            "ldm db query demo --sql \"UPDATE virtualhost SET hostname = 'x';\"",
            "ldm db query demo -s 'delete from user_'",
            'ldm db query --sql="DROP TABLE x"',
            'Never run `ldm db query demo --sql "INSERT INTO t VALUES (1)"`.',
        ):
            with self.subTest(line=line):
                failures, _, _ = _check(line + "\n")
                self.assertEqual(len(failures), 1)
                self.assertIn("SELECT, WITH, EXPLAIN and SHOW", failures[0].reason)
                self.assertIn("docker exec", failures[0].reason)

    def test_reads_are_allowed(self):
        for line in (
            'ldm db query demo --sql "SELECT * FROM user_"',
            "ldm db query demo -s 'WITH x AS (SELECT 1) SELECT * FROM x'",
            'ldm db query demo --sql "EXPLAIN SELECT 1"',
            'ldm db query demo --sql "SHOW TABLES"',
            'ldm db query demo --sql "SELECT updated_at FROM t"',
        ):
            with self.subTest(line=line):
                failures, _, _ = _check(_block(line))
                self.assertEqual(failures, [])

    def test_write_guard_honours_the_marker(self):
        failures, _, _ = _check(
            'ldm db query --sql "DELETE FROM x"  <!-- ldm-check: skip -->\n'
        )
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
