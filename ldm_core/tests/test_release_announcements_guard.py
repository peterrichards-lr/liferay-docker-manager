"""The release-announcements guard must check the series being CUT (LDM-#1477).

`scripts/release.py` warns when `RELEASE_ANNOUNCEMENTS` has no key for the
minor series being released. It ran that check against the version read
*before* the bump, so a `preminor` cut validated the series that had already
shipped:

    $ release.py --bump preminor --issue 1472     # 2.18.0 -> 2.19.0-pre.1
    ✅ Verified release announcements key for '2.18' series

For `beta`/`patch` the old and new series coincide and the bug is invisible.
It only surfaces on the bumps that open a NEW series -- the only ones that can
be missing a key at all -- so the guard could not fail in the one situation it
exists for.

These tests pin the resolution to the target version. They are written against
a pure helper for the reason `classify_stable_promotion` records in its own
docstring: the path that carried the bug was unreachable without a live cut.
"""

import importlib.util
import unittest
from pathlib import Path
from typing import ClassVar

_SPEC = importlib.util.spec_from_file_location(
    "ldm_release",
    Path(__file__).resolve().parent.parent.parent / "scripts" / "release.py",
)
# `scripts/` is not an importable package, so release.py is loaded by path.
assert _SPEC is not None, "could not locate scripts/release.py"
assert _SPEC.loader is not None, "no loader for scripts/release.py"
_release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_release)


class TestMinorSeriesOf(unittest.TestCase):
    def test_pre_release_resolves_to_its_own_series(self):
        """The suffix must not leak into the key -- '2.19.0-pre.1' is '2.19'."""
        self.assertEqual(_release.minor_series_of("2.19.0-pre.1"), "2.19")

    def test_stable_resolves_to_its_series(self):
        self.assertEqual(_release.minor_series_of("2.19.0"), "2.19")

    def test_patch_does_not_change_the_series(self):
        self.assertEqual(_release.minor_series_of("2.19.4"), "2.19")

    def test_major_bump_resolves_to_the_new_major(self):
        self.assertEqual(_release.minor_series_of("3.0.0-pre.1"), "3.0")


class TestCheckReleaseAnnouncements(unittest.TestCase):
    """The guard reports on the series being cut, not the one being left."""

    ANNOUNCEMENTS: ClassVar[dict[str, list[str]]] = {
        "2.18": ["a", "b"],
        "2.19": ["a", "b", "c", "d", "e"],
    }

    def test_present_key_is_confirmed_with_its_own_count(self):
        ok, msg = _release.check_release_announcements(
            "2.19.0-pre.1", self.ANNOUNCEMENTS
        )
        self.assertTrue(ok)
        self.assertIn("2.19", msg)
        self.assertIn("5", msg)

    def test_missing_key_warns_and_names_the_target_series(self):
        """The regression: cutting 2.20 must not pass on 2.19's key."""
        ok, msg = _release.check_release_announcements(
            "2.20.0-pre.1", self.ANNOUNCEMENTS
        )
        self.assertFalse(ok)
        self.assertIn("2.20", msg)

    def test_preminor_does_not_report_the_outgoing_series(self):
        """The exact v2.19.0-pre.1 symptom: a green tick naming '2.18'."""
        ok, msg = _release.check_release_announcements("2.20.0-pre.1", {"2.19": ["x"]})
        self.assertFalse(ok)
        self.assertNotIn("2.19", msg)


if __name__ == "__main__":
    unittest.main()
