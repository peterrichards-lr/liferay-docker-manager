"""The one parser for LDM's reclaim loops (LDM-#2151).

Shared by `scripts/sync_reclaim_table.py`, which renders the per-site reclaim
matrix into the docs, and `ldm_core/tests/test_routes_is_reclaimed.py`, which
asserts the lists agree with each other. Kept in one module, as
`ldm_docs_common.py` is for the footer tooling, because a second parser is how
the drift this closes would simply move into the tooling.

A reclaim loop is a `for <var> in [...]` whose body calls
`reclaim_volume_permissions` directly -- not through a nested `for`, which is
that inner loop's business. Keying on the call rather than on the entries keeps
out the two directory-CREATION lists in `handlers/base.py`, which drive
`safe_mkdir` and are tracked separately.

**Anything this cannot read is an error, never a skipped row.** A reclaim call
inside a loop over a variable, a comprehension, or a list holding a non-string
entry raises `ReclaimParseError`. A generated table missing a site reads as
complete, which is worse than a stale one (LDM-#2151).
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The call that makes a loop a RECLAIM loop.
RECLAIM_CALL = "reclaim_volume_permissions"

#: Every site that reclaims a set of project subdirectories, in table-column
#: order, with the column heading each renders under. All loops in one file
#: render as ONE column and must therefore carry identical lists -- if they
#: ever diverge, the generator refuses rather than averaging them, and the
#: file needs a column per loop.
RECLAIM_SITES: tuple[tuple[str, str], ...] = (
    ("ldm_core/handlers/base.py", "pre-boot (`handlers/base.py`)"),
    ("ldm_core/pipelines/run.py", "run pipeline (`pipelines/run.py`)"),
    ("ldm_core/handlers/system.py", "repair (`ldm rescue`)"),
    # LDM-#2266: the node-side reclaim that runs before every remote sync.
    ("ldm_core/config.py", "node, before sync (`config.py`)"),
)


class ReclaimParseError(Exception):
    """A reclaim loop exists that the parser cannot turn into a list."""


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return getattr(call.func, "attr", "")


def _calls_reclaim_directly(loop: ast.For) -> bool:
    """True if the loop body calls the helper outside any nested `for`."""
    stack: list[ast.AST] = [*loop.body, *loop.orelse]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        if isinstance(node, ast.Call) and _call_name(node) == RECLAIM_CALL:
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def parse_reclaim_lists(source: str, label: str = "<source>") -> list[list[str]]:
    """Every reclaim loop's list, in source order.

    Raises `ReclaimParseError` for a reclaim loop whose iterable is not a
    literal list or tuple of strings.
    """
    tree = ast.parse(source)
    found: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        if not _calls_reclaim_directly(node):
            continue
        where = f"{label}:{node.lineno}"
        if not isinstance(node.iter, (ast.List, ast.Tuple)):
            raise ReclaimParseError(
                f"{where}: a loop calls {RECLAIM_CALL} but iterates over "
                f"`{ast.unparse(node.iter)}`, not a literal list. The reclaim "
                f"table cannot be generated from it; inline the list, or teach "
                f"scripts/reclaim_sites.py to read it."
            )
        entries: list[str] = []
        for elt in node.iter.elts:
            if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
                raise ReclaimParseError(
                    f"{where}: reclaim list entry `{ast.unparse(elt)}` is not a "
                    f"string literal, so its path key cannot be read from source."
                )
            entries.append(elt.value)
        found.append((node.lineno, entries))
    return [entries for _, entries in sorted(found, key=lambda f: f[0])]


def reclaim_lists(rel_path: str, root: Path = PROJECT_ROOT) -> list[list[str]]:
    """`parse_reclaim_lists` for a file, relative to `root`."""
    path = root / rel_path
    return parse_reclaim_lists(path.read_text(encoding="utf-8"), label=rel_path)


def site_columns(root: Path = PROJECT_ROOT) -> list[tuple[str, list[str]]]:
    """One `(heading, entries)` per site, the loops in each file merged.

    Raises `ReclaimParseError` if a site has no reclaim loop at all, or if the
    loops in one file disagree.
    """
    columns: list[tuple[str, list[str]]] = []
    for rel_path, heading in RECLAIM_SITES:
        lists = reclaim_lists(rel_path, root)
        if not lists:
            raise ReclaimParseError(
                f"{rel_path}: no reclaim loop found. Either it moved, or the "
                f"parser no longer recognises it -- fix RECLAIM_SITES or the "
                f"parser rather than dropping the column."
            )
        first = lists[0]
        for other in lists[1:]:
            if other != first:
                raise ReclaimParseError(
                    f"{rel_path}: its reclaim loops disagree ({first} vs "
                    f"{other}), so one column cannot represent them. Give the "
                    f"file a column per loop in scripts/reclaim_sites.py."
                )
        columns.append((heading, first))
    return columns
