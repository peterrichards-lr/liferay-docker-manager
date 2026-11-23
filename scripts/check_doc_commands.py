#!/usr/bin/env python3
"""Every documented `ldm` command must be one the CLI accepts (LDM-#2149).

`check-cli-drift` compares option *tokens* between the parser and five
reference files. It cannot see a command that uses no flags, a flag used on a
subcommand that does not take it, or any document outside those five. This
check closes that gap with two passes over every tracked Markdown file:

1. **Parse.** Each line beginning `ldm` inside a fenced code block is run
   through the real parser from `ldm_core.cli.get_parser()`, the same way
   `main()` runs `sys.argv`. Nothing is executed: argparse only parses, and its
   `SystemExit` is caught. An unknown subcommand, an unknown option, an option
   on the wrong subcommand or a surplus positional fails, naming file:line.

2. **Write-statement guard.** `ldm db query` accepts only read statements
   (`DatabaseService.is_query_safe` in `ldm_core/handlers/database.py`), but a
   `--sql "UPDATE ..."` value parses cleanly. A line regex catches that.

Before parsing, a line is normalised the way a reader would type it: a prompt
(`$ `, `% `, `> `, `PS> `), `sudo` and leading `VAR=value` assignments are
dropped; `\\` (and PowerShell backtick) continuations are joined; and a
pipe, `&&`, `||`, `;`, redirection or trailing comment ends the command.
A `<project>` style placeholder, a shell variable (`$PROJECT`, `${PROJECT}`)
and a `$(...)` substitution each become the value `1` -- a word that is valid
both as a string and as an `int`, so `--port $PORT` parses as it would for a
reader. An option restricted to `choices` needs a real choice, or the
`<a | b>` form, whose first alternative is used.

Opt-out, for an example that is MEANT to be refused (an error demonstration),
is explicit and never silent -- the marker is the recorded decision:

* a trailing comment on the line:          ldm run --bogus  # ldm-check: skip
* an HTML comment on the line immediately
  before the opening fence (whole block):  <!-- ldm-check: skip -->

Both forms take an optional reason after the marker. The number of skipped
commands is printed on every run.

Exit codes: 0 every command parses, 1 at least one failure.
"""

from __future__ import annotations

import contextlib
import io
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_MARKER = "ldm-check: skip"

#: CHANGELOG.md records commands as they were when each release shipped; a flag
#: later renamed is history, not a defect. Excluded by name, and announced.
EXCLUDED_FILES = {"CHANGELOG.md"}

_FENCE_RE = re.compile(r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_PROMPT_RE = re.compile(r"^(?:\$|%|>|PS>|PS [^>]*>)\s+")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s+")
_LDM_RE = re.compile(r"^ldm(?:\s|$)")
# `<project>`, `<your-host>`, `<a|b>`: starts with a letter, no space before `>`
# so `< in.sql > out.txt` stays a pair of redirections.
_PLACEHOLDER_RE = re.compile(r"<[A-Za-z_](?:[^<>\n]*[^\s<>])?>")
# `<hosts | share>`: a choice between literal words; the first is a real value.
_CHOICE_PLACEHOLDER_RE = re.compile(r"<\s*([\w.-]+)\s*(?:\|\s*[\w.-]+\s*)+>")
# `[--dry-run]`, `[project]`: a whole-word optional group, no quotes inside.
_OPTIONAL_RE = re.compile(r"(?<!\S)\[([^\[\]'\"]*)\](?!\S)")
_ALTERNATION_RE = re.compile(r"([\w.-]+)(?:\|[\w.-]+)+")
_ELLIPSIS_RE = re.compile(r"(?<!\S)\.\.\.(?!\S)")
_SUBST_RE = re.compile(r"\$\([^()]*\)|`[^`]*`|\$\{[^}]*\}|\$[A-Za-z_]\w*")
#: Stands in for a placeholder or variable: valid as a string and as an int.
VALUE = "1"
# `>`, `>>`, `2>`, `2>&1`, `&>`, `<` and their target.
_REDIRECT_RE = re.compile(r"\s(?:\d*|&)(?:>>?|<)(?:&\d+|\s*[^\s|;&]+)")

#: Restrictions the parser cannot express. `--sql "UPDATE ..."` parses; it is
#: refused at runtime by DatabaseService.is_query_safe, which permits only
#: statements starting SELECT, WITH, EXPLAIN or SHOW.
WRITE_SQL_RE = re.compile(
    r"\bldm\s+(?:.*\s)?(?:db\s+)?query\b.*?(?:\s-s|\s--?sql)(?:\s+|=)[\"']?\s*"
    r"(?:UPDATE|INSERT|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|MERGE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
WRITE_SQL_WHY = (
    "`ldm db query` is read-only: DatabaseService.is_query_safe "
    "(ldm_core/handlers/database.py) permits only SELECT, WITH, EXPLAIN and "
    "SHOW, so a write is refused at runtime"
)
WRITE_SQL_INSTEAD = (
    "show the write via `docker exec <container> psql|mysql ...`, or point at "
    "the ldm command that performs the change properly"
)


@dataclass
class Command:
    path: str
    line: int
    text: str
    skipped: bool


@dataclass
class Failure:
    path: str
    line: int
    text: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.text}\n    -> {self.reason}"


def _strip_prompt(line: str) -> str:
    line = _PROMPT_RE.sub("", line.strip())
    if line.startswith("sudo "):
        line = line[5:].lstrip()
    while _ASSIGN_RE.match(line):
        line = _ASSIGN_RE.sub("", line, count=1)
    return line


def extract_commands(path: str, text: str) -> list[Command]:
    """Every `ldm ...` command inside a fenced block, continuations joined."""
    commands: list[Command] = []
    lines = text.splitlines()
    fence: str | None = None
    skip_block = False
    prev_nonblank = ""
    i = 0
    while i < len(lines):
        raw = lines[i]
        m = _FENCE_RE.match(raw)
        if fence is None:
            if m:
                fence = m.group("fence")
                skip_block = SKIP_MARKER in prev_nonblank
            if raw.strip():
                prev_nonblank = raw
            i += 1
            continue
        if m and m.group("fence").startswith(fence) and not m.group("info").strip():
            fence = None
            skip_block = False
            prev_nonblank = raw
            i += 1
            continue

        start = i
        joined = raw.rstrip()
        while (joined.endswith("\\") or joined.endswith(" `")) and i + 1 < len(lines):
            i += 1
            joined = joined[:-1].rstrip() + " " + lines[i].strip()
        i += 1

        cmd = _strip_prompt(joined)
        if _LDM_RE.match(cmd):
            commands.append(
                Command(path, start + 1, cmd, skip_block or SKIP_MARKER in cmd)
            )
    return commands


def _unbracket(match: re.Match[str]) -> str:
    return _ALTERNATION_RE.sub(r"\1", match.group(1))


def tokenize(command: str) -> list[str]:
    """The argv a shell would hand `ldm`, up to the first shell operator.

    Usage synopses are read as the fullest command they describe: an optional
    `[--flag <value>]` group is kept (so its flag is checked too), the first of
    `a|b` alternatives is taken, and a `...` repetition marker is dropped.
    """
    text = _CHOICE_PLACEHOLDER_RE.sub(r"\1", command)
    text = _PLACEHOLDER_RE.sub(VALUE, text)
    previous = None
    while previous != text:
        previous, text = text, _OPTIONAL_RE.sub(_unbracket, text)
    text = _ELLIPSIS_RE.sub("", text)
    text = _SUBST_RE.sub(VALUE, text)
    text = _REDIRECT_RE.sub(" ", text)
    lex = shlex.shlex(text, posix=True, punctuation_chars="|&;")
    lex.whitespace_split = True
    tokens: list[str] = []
    for tok in lex:
        if tok and set(tok) <= set("|&;"):
            break
        tokens.append(tok)
    return tokens[1:]  # drop the leading `ldm`


def parse_error(parser, preprocess, argv: list[str]) -> str | None:
    """None if argparse accepts `argv`, else its error message. Never executes."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            parser.parse_args(preprocess(["ldm", *argv])[1:])
    except SystemExit as exc:
        if exc.code not in (0, None):
            lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
            return lines[-1] if lines else f"argparse exited {exc.code}"
    return None


def check_text(
    path: str, text: str, parser, preprocess
) -> tuple[list[Failure], int, int]:
    """(failures, commands checked, commands skipped) for one document."""
    failures: list[Failure] = []
    checked = skipped = 0
    for cmd in extract_commands(path, text):
        if cmd.skipped:
            skipped += 1
            continue
        checked += 1
        try:
            argv = tokenize(cmd.text)
        except ValueError as exc:  # unbalanced quotes
            failures.append(
                Failure(path, cmd.line, cmd.text, f"cannot tokenize: {exc}")
            )
            continue
        reason = parse_error(parser, preprocess, argv)
        if reason:
            failures.append(Failure(path, cmd.line, cmd.text, reason))

    for lineno, line in enumerate(text.splitlines(), 1):
        if SKIP_MARKER not in line and WRITE_SQL_RE.search(line):
            failures.append(
                Failure(
                    path,
                    lineno,
                    line.strip(),
                    f"{WRITE_SQL_WHY}. Instead: {WRITE_SQL_INSTEAD}",
                )
            )
    return failures, checked, skipped


def markdown_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(REPO_ROOT / p for p in out.split("\0") if p)


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from ldm_core.cli import get_parser, preprocess_args

    parser, _ = get_parser()
    args = sys.argv[1:] if argv is None else argv
    files = [Path(a).resolve() for a in args] if args else markdown_files()

    failures: list[Failure] = []
    checked = skipped = scanned = 0
    for f in files:
        rel = (
            f.relative_to(REPO_ROOT).as_posix()
            if f.is_relative_to(REPO_ROOT)
            else str(f)
        )
        if rel in EXCLUDED_FILES:
            print(f"Doc command check: {rel} excluded (historical record).")
            continue
        scanned += 1
        fails, c, s = check_text(
            rel, f.read_text(encoding="utf-8"), parser, preprocess_args
        )
        failures += fails
        checked += c
        skipped += s

    for failure in failures:
        print(failure)
    print(
        f"Doc command check: {checked} command(s) parsed across {scanned} "
        f"file(s), {skipped} marked `{SKIP_MARKER}`, {len(failures)} failure(s)."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
