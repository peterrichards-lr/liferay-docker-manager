"""A `hasattr` guard must name something that exists (LDM-#1679).

`hasattr(obj, "name")` wrapped around a call turns a permanently-false branch
into a legitimate-looking conditional. There is no `AttributeError`, no failing
test, and nothing for a linter or type checker to object to -- the branch simply
never runs.

Two of them were found in `VolumeSyncStage` alone:

* `_restore_from_cloud_layout` -- never defined on the object called, and every
  definition that ever existed anywhere was an ellipsis stub. Genuinely dead;
  removed in LDM-#1679.
* `_is_lcp_workspace` -- never defined at all, while the real function
  `is_lcp_workspace` sits in `ldm_core/utils.py`. That one is a **regression**:
  LCP service directories have not been copied into imported projects since
  2026-07-10. Tracked as LDM-#1681.

Both survived a God Object decomposition, a pipeline refactor and a shim
cleanup. The first also misled LDM-#1677 and LDM-#1643, whose write-ups cited it
as an external write a rollback could not reach.

Scope: the import pipeline, where both were found. Deliberately not repo-wide --
`hasattr` against genuinely dynamic objects (argparse namespaces, third-party
clients) is legitimate, and a blanket rule would drown in those.
"""

import ast
import pathlib
import unittest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PIPELINE_SRC = _ROOT / "pipelines" / "import_pipeline.py"

# Names a `hasattr` may guard even though no `def` matches, with the reason.
#
# RATCHET, not approval. An entry needs a tracking issue; removing one is
# progress. Empty is the goal.
_KNOWN_PHANTOM = {
    # LDM-#1681: the real function is `is_lcp_workspace` in ldm_core/utils.py.
    # Fixing this ENABLES a code path dormant since 2026-07-10, so it is a
    # behaviour change needing its own tests rather than a tidy-up here.
    "_is_lcp_workspace",
}


def _hasattr_names(source: str):
    """Every string literal used as the attribute argument to `hasattr`."""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "hasattr":
            continue
        if len(node.args) < 2:
            continue
        target = node.args[1]
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            yield target.value


def _is_defined_anywhere(name: str) -> bool:
    """True if any module under ldm_core defines `name` as a function or attribute."""
    needle_def = f"def {name}("
    needle_attr = f"{name} ="
    for path in _ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if needle_def in text or needle_attr in text:
            return True
    return False


class TestHasattrGuardsNameRealMethods(unittest.TestCase):
    def test_the_parser_finds_the_guards(self):
        """Guards the check itself: a broken matcher would pass vacuously."""
        names = list(_hasattr_names(_PIPELINE_SRC.read_text(encoding="utf-8")))
        self.assertTrue(
            names,
            "no hasattr guards found in import_pipeline.py -- either they are all "
            "gone (remove this test) or the AST matcher is broken",
        )

    def test_every_guarded_name_exists(self):
        names = set(_hasattr_names(_PIPELINE_SRC.read_text(encoding="utf-8")))
        phantom = sorted(
            n for n in names if n not in _KNOWN_PHANTOM and not _is_defined_anywhere(n)
        )
        self.assertEqual(
            phantom,
            [],
            f"{phantom} are guarded by hasattr but defined nowhere in ldm_core. "
            "The branch can never execute, and hasattr hides that -- no "
            "AttributeError, no failing test. Either point the call at the real "
            "implementation, delete the branch, or add the name to "
            "_KNOWN_PHANTOM with a tracking issue (LDM-#1679).",
        )

    def test_the_allowlist_has_not_gone_stale(self):
        """An entry that now resolves is bookkeeping to remove."""
        stale = sorted(n for n in _KNOWN_PHANTOM if _is_defined_anywhere(n))
        self.assertEqual(
            stale,
            [],
            f"{stale} now resolve -- remove them from _KNOWN_PHANTOM so the "
            "ratchet keeps tightening (LDM-#1679).",
        )

    # No source-text check that `_restore_from_cloud_layout` has not returned.
    # The first draft had one, and it failed immediately -- on the explanatory
    # comment left at the removal site, because `assertNotIn` over source cannot
    # tell a call from a comment. It is also redundant: if the call came back it
    # would appear as a hasattr guard resolving to nothing, and
    # test_every_guarded_name_exists would fail. Asserting on the AST covers the
    # behaviour; asserting on the text covers the spelling.


if __name__ == "__main__":
    unittest.main()
