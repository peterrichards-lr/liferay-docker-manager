#!/usr/bin/env python3
"""Refuses a test that creates an executable file without saying why.

LDM-#1898: a test wrote a ``chmod +x`` shell script named ``lfr-tunnel`` into a
``TemporaryDirectory`` and let production code run it. Every ``pytest`` run --
and so every ``scripts/agent_push.sh`` -- spawned a process named
``lfr-tunnel`` from a non-whitelisted path, which is the signature endpoint
protection acts on. Two developer sessions were killed before anyone connected
the two.

``conftest``'s runtime guard (LDM-#1899) refuses such a spawn, but only when it
is issued **from Python**. These tests also run ``subprocess.run(["bash", "-c",
script])`` and let the shell exec the stub -- the argv crossing the Python seam
is ``bash``, so the guard never sees it. There is no runtime seam to place a
check on.

What both shapes share is earlier and static: a test made a file executable.
That is the precondition for any spawn, by any route, so this is where the
class can be caught at commit time rather than at the next dead terminal.

Making a stub executable is sometimes genuinely necessary -- the verification
scripts must actually run one. It has to be a deliberate, reviewed choice
rather than the default nobody noticed, so annotate the line::

    path.chmod(0o755)  # lint: executable-stub -- bash must exec it; name is not watched

The reason is mandatory, and writing one forces the question that matters:
**is this file's name one an endpoint-protection agent watches for?**
"""

import argparse
import ast
import re
import sys
from pathlib import Path

DEFAULT_TESTS_DIR = Path(__file__).parent.parent / "ldm_core" / "tests"

#: Kept in step with ``_PROTECTED_BINARIES`` in ``ldm_core/tests/conftest.py``.
PROTECTED = frozenset(
    {"lfr-tunnel", "lfr-tunnel.exe", "lfr-tunneld", "ldm", "ldm.exe", "ldm.cmd"}
)

EXEC_BIT_NAMES = {"S_IEXEC", "S_IXUSR", "S_IXGRP", "S_IXOTH", "S_IXALL"}
ALLOW = re.compile(r"#\s*lint:\s*executable-stub\s*--\s*(?P<reason>\S.*)")


def _grants_execute(arg: ast.expr) -> bool:
    """True when the mode argument sets any execute bit."""
    for node in ast.walk(arg):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            if node.value & 0o111:
                return True
        if isinstance(node, ast.Name) and node.id in EXEC_BIT_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in EXEC_BIT_NAMES:
            return True
    return False


def _chmod_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "chmod" and node.args:
            yield node


def _annotation(lines: list[str], lineno: int) -> str | None:
    """The allow-comment on this line or the one above it, if any."""
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(lines):
            found = ALLOW.search(lines[idx])
            if found:
                return found.group("reason").strip()
    return None


def _target_name(call: ast.Call) -> str | None:
    """The variable ``chmod`` was called on, e.g. ``path`` in ``path.chmod()``."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return None


def _literals_assigned_to(scope: ast.AST, name: str) -> set[str]:
    """String literals appearing in assignments to ``name`` within ``scope``.

    Precise on purpose. Scanning the whole file for a watched name flags a
    module that merely *mentions* ``ldm`` in a message, and a lint that cries
    wolf is a lint someone deletes.
    """
    found: set[str] = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                found.add(sub.value)
    return found


def _enclosing_scopes(tree: ast.AST, call: ast.Call) -> list[ast.AST]:
    """Function/class bodies containing ``call``, innermost last."""
    scopes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if any(child is call for child in ast.walk(node)):
                scopes.append(node)
    return scopes


def _display(path: Path, tests_dir: Path) -> str:
    """Repo-relative where possible, else relative to the scanned directory."""
    for base in (DEFAULT_TESTS_DIR.parent.parent, tests_dir):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=DEFAULT_TESTS_DIR,
        help=(
            "Directory to scan (default: ldm_core/tests). Tests MUST pass this "
            "-- a checker that can only run against the real tree cannot be "
            "tested without touching it (LDM-#1391)."
        ),
    )
    args = parser.parse_args(argv)
    tests_dir: Path = args.tests_dir

    failures = []

    for path in sorted(tests_dir.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - a broken file fails elsewhere
            failures.append(f"{path}: could not parse: {exc}")
            continue

        for call in _chmod_calls(tree):
            if not _grants_execute(call.args[-1]):
                continue
            rel = _display(path, tests_dir)
            reason = _annotation(lines, call.lineno)
            if reason is None:
                failures.append(
                    f"{rel}:{call.lineno}: makes a file executable without an "
                    "explanation.\n"
                    "    A test that creates an executable file can spawn it, "
                    "by Python or by a shell (LDM-#1898).\n"
                    "    If it is genuinely needed, annotate the line:\n"
                    "        # lint: executable-stub -- <why, and why the name "
                    "is safe>"
                )
                continue

            target = _target_name(call)
            watched = set()
            if target:
                for scope in _enclosing_scopes(tree, call):
                    for literal in _literals_assigned_to(scope, target):
                        base = Path(literal).name
                        if base in PROTECTED:
                            watched.add(base)
            if watched:
                failures.append(
                    f"{rel}:{call.lineno}: allowed as an executable stub, but "
                    f"this file also names a watched binary: "
                    f"{', '.join(sorted(watched))}.\n"
                    "    Endpoint protection acts on the NAME. Rename the stub "
                    "(the scripts take a path argument, so the basename is "
                    "free) -- see LDM-#1899."
                )

    if failures:
        print("Executable stubs in tests need a stated reason (LDM-#1898):\n")
        for failure in failures:
            print(f"[ERROR] {failure}\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
