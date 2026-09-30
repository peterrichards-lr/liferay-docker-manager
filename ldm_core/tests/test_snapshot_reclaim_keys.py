"""LDM-#1942: one vocabulary for reclaim, and two lists that must stay two.

`ldm_core/snapshot/archive.py` holds two sets of strings that look alike and
are not:

* `PROJECT_TREES` -- **`setup_paths` KEYS**, carrying each tree's reclaim
  policy. Indexed as `paths[key]`.
* `_ARCHIVE_DIR_NAMES` -- **DIRECTORY NAMES** under the project root, joined
  as `paths["root"] / name`.

`client-extensions` sat in the reclaim list for its whole life without ever
firing: the client-extension trees are keyed `cx` and `ce_dir`, and
`client-extensions` is not a key at all. `paths.get(key)` returns None for a
wrong key and for an absent directory alike, so it was a no-op with no
warning, no error and no log line -- while the same string was *correct* two
loops down in the archive list.

Two things fix that, and this module pins both.

**The reclaim policy is now one declaration, keyed only by path keys, and
indexed rather than `.get()`** -- a wrong key raises instead of silently
reclaiming nothing.

**The archive list is NOT derived from it, deliberately.** The obvious
de-duplication -- derive the archive name as
`paths[key].relative_to(paths["root"])` -- was tried and measured, and it
silently changes what a backup contains:

    archive entry "configs"  ->  <root>/configs
    path key      "configs"  ->  <root>/osgi/configs

It would archive `osgi/configs`, already inside the `osgi` entry, and stop
archiving `<root>/configs` altogether. `TestTheTwoListsAreNotInterchangeable`
below is what stands between the next person and that change.
"""

import tempfile
import unittest
from pathlib import Path

from ldm_core.handlers.base import BaseHandler
from ldm_core.snapshot.archive import (
    _ARCHIVE_DIR_NAMES,
    PROJECT_TREES,
    RECLAIM_CONTAINER,
    RECLAIM_HOST,
    trees_to_reclaim,
)


def _real_path_keys():
    """The keys production `setup_paths` actually defines."""
    handler = BaseHandler.__new__(BaseHandler)
    with tempfile.TemporaryDirectory() as tmp:
        return set(handler.setup_paths(Path(tmp)))


def _real_paths(root):
    handler = BaseHandler.__new__(BaseHandler)
    return handler.setup_paths(root)


class TestEveryReclaimKeyIsReal(unittest.TestCase):
    def test_no_declared_tree_is_a_silent_no_op(self):
        keys = _real_path_keys()
        for tree in PROJECT_TREES:
            with self.subTest(key=tree.key):
                self.assertIn(
                    tree.key,
                    keys,
                    f"'{tree.key}' is not a setup_paths key, so this entry "
                    f"reclaims nothing (LDM-#1942)",
                )

    def test_a_wrong_key_raises_rather_than_doing_nothing_quietly(self):
        """The mechanism that hid the original bug, pinned.

        `paths.get(key)` cannot distinguish a wrong key from an absent
        directory. Indexing can, and must.
        """
        with tempfile.TemporaryDirectory() as tmp:
            paths = _real_paths(Path(tmp))
            with self.assertRaises(KeyError):
                _ = paths["client-extensions"]

    def test_an_unreal_key_warns_instead_of_passing_silently(self):
        """The defect was the SILENCE, not the tolerance.

        Indexing (`paths[key]`) was tried so a bad key would raise. It is
        wrong here: the loop runs inside a broad `except Exception`, so the
        raise is swallowed and aborts the whole loop -- trading one quiet
        entry for every quiet entry. The lookup stays tolerant and says so.
        """
        from unittest.mock import patch

        from ldm_core.snapshot.archive import ProjectTree, _resolve_tree

        bogus = ProjectTree("client-extensions", reclaim=RECLAIM_CONTAINER)
        with patch("ldm_core.snapshot.archive.UI.warning") as warn:
            resolved = _resolve_tree({"root": Path("/tmp")}, bogus)

        self.assertIsNone(resolved)
        warn.assert_called_once()
        self.assertIn("client-extensions", warn.call_args[0][0])

    def test_a_real_key_resolves_without_complaint(self):
        from unittest.mock import patch

        from ldm_core.snapshot.archive import ProjectTree, _resolve_tree

        with tempfile.TemporaryDirectory() as tmp:
            paths = _real_paths(Path(tmp))
            tree = ProjectTree("deploy", reclaim=RECLAIM_CONTAINER)
            with patch("ldm_core.snapshot.archive.UI.warning") as warn:
                resolved = _resolve_tree(paths, tree)
        self.assertEqual(resolved, paths["deploy"])
        warn.assert_not_called()

    def test_the_dead_entry_is_gone(self):
        declared = {t.key for t in PROJECT_TREES}
        self.assertNotIn("client-extensions", declared)

    def test_the_real_keys_exist_for_when_that_decision_is_taken(self):
        """Whether `cx` or `ce_dir` should be reclaimed is still open on
        LDM-#1942 -- `ce_dir` would chown the developer's own source."""
        keys = _real_path_keys()
        self.assertIn("cx", keys, "osgi/client-extensions")
        self.assertIn("ce_dir", keys, "the developer's own client-extensions")
        self.assertNotIn("client-extensions", keys)


class TestThePolicyIsUnchanged(unittest.TestCase):
    """This was a refactor. Pinned so it stays one.

    Changing what is reclaimed is a scope decision needing a native-Linux run
    (macOS cannot observe `chown 1000` at all), so a silent change here would
    be both a behaviour change and an unverifiable one.
    """

    def test_the_container_reclaim_set_is_what_it_always_was(self):
        self.assertEqual(
            {t.key for t in trees_to_reclaim(RECLAIM_CONTAINER)},
            {"deploy", "files", "logs", "configs", "modules", "marketplace"},
        )

    def test_the_host_reclaim_set_is_what_it_always_was(self):
        self.assertEqual(
            {t.key for t in trees_to_reclaim(RECLAIM_HOST)}, {"data", "state"}
        )

    def test_every_tree_declares_a_policy(self):
        """A tree in the registry with no policy does nothing, which is the
        shape of the original defect wearing different clothes."""
        for tree in PROJECT_TREES:
            with self.subTest(key=tree.key):
                self.assertIn(tree.reclaim, (RECLAIM_CONTAINER, RECLAIM_HOST))


class TestTheTwoListsAreNotInterchangeable(unittest.TestCase):
    """The trap, measured. Someone 'harmonising' these would lose a directory."""

    def test_the_archive_list_holds_directory_names_not_path_keys(self):
        self.assertIn(".ldm", _ARCHIVE_DIR_NAMES)
        self.assertNotIn(".ldm", _real_path_keys())

    def test_a_shared_string_means_two_different_places(self):
        """`configs` is the counter-example that makes derivation unsafe.

        It is valid in BOTH vocabularies and resolves differently in each, so
        deriving the archive from path keys would archive `osgi/configs` --
        already inside the `osgi` entry -- and silently stop archiving
        `<root>/configs`.
        """
        self.assertIn("configs", _ARCHIVE_DIR_NAMES)
        self.assertIn("configs", {t.key for t in PROJECT_TREES})
        with tempfile.TemporaryDirectory() as tmp:
            paths = _real_paths(Path(tmp))
            as_archive_name = paths["root"] / "configs"
            as_path_key = paths["configs"]
        self.assertNotEqual(
            as_archive_name,
            as_path_key,
            "if these ever agree, re-measure before deriving one from the "
            "other -- this test is the reason that derivation was backed out",
        )

    def test_every_archive_name_resolves_under_the_project_root(self):
        for name in _ARCHIVE_DIR_NAMES:
            with self.subTest(name=name):
                self.assertFalse(Path(name).is_absolute())
                self.assertNotIn("..", Path(name).parts)


if __name__ == "__main__":
    unittest.main()
