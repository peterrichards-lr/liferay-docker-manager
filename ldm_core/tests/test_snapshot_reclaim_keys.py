"""LDM-#1942: a reclaim key that is not a real path key is a silent no-op.

`ldm_core/snapshot/archive.py` reclaims permissions on a hand-maintained list of
`setup_paths` KEYS:

    for d in _RECLAIM_PATH_KEYS:
        if paths.get(d) and paths[d].exists():
            reclaim_volume_permissions(paths[d], uid="1000", gid="1000", chmod_val="777")

**`paths.get(d)` treats a missing key and a missing directory identically**, so a
wrong key degrades to a no-op with no warning, no error and no log line. That is
how `client-extensions` sat in the list without ever firing: the client-extension
trees are keyed `cx` (`osgi/client-extensions`) and `ce_dir`
(`client-extensions`), and `client-extensions` is not a key at all.

What made it invisible is that the SAME STRING is correct in the archive list two
loops down, which iterates directory NAMES under the project root
(`paths["root"] / name`). Two namespaces, strings that look alike, and nothing
distinguishing them.

This module asserts the reclaim list is made of real keys. It would have caught
that entry on the day it was introduced.

**It deliberately does NOT assert the archive list against `setup_paths`** --
those are directory names, and requiring them to be path keys would be the same
category error in the other direction.
"""

import tempfile
import unittest
from pathlib import Path

from ldm_core.handlers.base import BaseHandler
from ldm_core.snapshot.archive import _ARCHIVE_DIR_NAMES, _RECLAIM_PATH_KEYS


def _real_path_keys():
    """The keys production `setup_paths` actually defines."""
    handler = BaseHandler.__new__(BaseHandler)
    with tempfile.TemporaryDirectory() as tmp:
        return set(handler.setup_paths(Path(tmp)))


class TestEveryReclaimKeyIsReal(unittest.TestCase):
    def test_no_reclaim_key_is_a_silent_no_op(self):
        keys = _real_path_keys()
        for key in _RECLAIM_PATH_KEYS:
            with self.subTest(key=key):
                self.assertIn(
                    key,
                    keys,
                    f"'{key}' is not a setup_paths key, so `paths.get('{key}')` "
                    f"is always None and this entry never reclaims anything -- "
                    f"silently, because a missing key and a missing directory "
                    f"are indistinguishable there (LDM-#1942)",
                )

    def test_the_dead_entry_is_gone(self):
        """`client-extensions` is out of the reclaim list.

        Removing it was a **no-op**: it resolved to None on every run, so
        nothing it would have done was ever done. Whether a live key should
        replace it is a separate decision, tracked on LDM-#1942 -- adding `cx`
        would newly reclaim a tree, and adding `ce_dir` would chown the
        developer's own source to uid 1000 at mode 777.
        """
        self.assertNotIn("client-extensions", _RECLAIM_PATH_KEYS)

    def test_the_real_keys_exist_for_when_that_decision_is_taken(self):
        """So whoever actions LDM-#1942 does not have to re-derive them."""
        keys = _real_path_keys()
        self.assertIn("cx", keys, "osgi/client-extensions")
        self.assertIn("ce_dir", keys, "the developer's own client-extensions")
        self.assertNotIn("client-extensions", keys)


class TestTheTwoListsAreNotInterchangeable(unittest.TestCase):
    """The trap, pinned. Someone 'harmonising' these would reintroduce #1942."""

    def test_the_archive_list_holds_directory_names_not_path_keys(self):
        """`.ldm` and `osgi` are directory names; `.ldm` is no key at all."""
        self.assertIn(".ldm", _ARCHIVE_DIR_NAMES)
        self.assertNotIn(".ldm", _real_path_keys())

    def test_the_two_lists_are_genuinely_different_sets(self):
        """If they ever become equal, one of them is wrong -- they answer
        different questions."""
        self.assertNotEqual(set(_RECLAIM_PATH_KEYS), set(_ARCHIVE_DIR_NAMES))

    def test_every_archive_name_resolves_under_the_project_root(self):
        """The archive list's real contract: a name joinable to the root.

        Asserted as a shape rather than by existence -- a project legitimately
        may not have every one of these directories.
        """
        for name in _ARCHIVE_DIR_NAMES:
            with self.subTest(name=name):
                self.assertFalse(Path(name).is_absolute())
                self.assertNotIn("..", Path(name).parts)


if __name__ == "__main__":
    unittest.main()
