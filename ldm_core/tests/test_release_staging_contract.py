"""Every file the version bump rewrites must also be staged (LDM-#1491).

`_apply_version_update` (ldm_core/handlers/dev.py) rewrites a set of files on
every version change. `scripts/release.py` then stages an explicitly-listed set
before committing and tagging. Nothing tied the two together, so a file could
be added to the first and forgotten in the second -- rewritten in the working
tree, never committed, and absent from the tag.

That has now happened twice:

  LDM-#1011  the two verify scripts, caught before it bit a real promote
  LDM-#1491  ldm_core/resources/ldm.1, which was NOT caught -- v2.19.0-pre.2
             was tagged with a man page reading 2.19.0-pre.1

There are three staging lists (preview, promote, standard) and all three must
carry every stamped file, so this asserts against each independently rather
than against their union.
"""

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_RELEASE_PY = _ROOT / "scripts" / "release.py"
_DEV_PY = _ROOT / "ldm_core" / "handlers" / "dev.py"

# CHANGELOG.md is stamped via its own code path, and the .md sweep in
# release.py stages it separately.
_EXEMPT = {"CHANGELOG.md"}


def _stamped_files():
    """Paths rewritten by _apply_version_update's files_to_update mapping."""
    src = _DEV_PY.read_text(encoding="utf-8")
    start = src.index("files_to_update = {")
    depth, end = 0, None
    for i, ch in enumerate(src[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, "could not find the end of files_to_update"
    return set(re.findall(r'"([^"]+\.(?:py|toml|sh|ps1|1|md))":', src[start:end]))


def _staging_lists():
    """Each `git add [...]` argument list in release.py, as a list of sets."""
    src = _RELEASE_PY.read_text(encoding="utf-8")
    lists = []
    for m in re.finditer(r'"git",\s*\n\s*"add",(.*?)\]', src, re.DOTALL):
        lists.append(set(re.findall(r'"([^"]+)"', m.group(1))))
    return lists


class TestStampedFilesAreStaged(unittest.TestCase):
    def test_stamping_table_is_discoverable(self):
        """Guard the parsing itself -- a silent empty set would pass anything."""
        stamped = _stamped_files()
        self.assertIn("ldm_core/constants.py", stamped)
        self.assertIn("ldm_core/resources/ldm.1", stamped)
        self.assertGreaterEqual(len(stamped), 4)

    def test_three_staging_lists_are_found(self):
        lists = _staging_lists()
        self.assertEqual(
            3,
            len(lists),
            "expected preview, promote and standard staging lists in release.py",
        )

    def test_every_stamped_file_is_in_every_staging_list(self):
        stamped = _stamped_files() - _EXEMPT
        for index, staged in enumerate(_staging_lists()):
            missing = sorted(stamped - staged)
            self.assertEqual(
                [],
                missing,
                f"staging list #{index} in scripts/release.py omits {missing}. "
                "Files rewritten by _apply_version_update but never staged are "
                "written to the working tree and never reach the tag "
                "(LDM-#1011, LDM-#1491).",
            )


if __name__ == "__main__":
    unittest.main()
