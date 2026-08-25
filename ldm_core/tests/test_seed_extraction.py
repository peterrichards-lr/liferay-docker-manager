"""First-boot seeding must actually extract, or report that it did not (LDM-#1322).

`ldm init` downloaded the ~1GB seed archive, failed to extract it *every* time,
swallowed the error into a warning, fell back to a vanilla initialization, and
then printed that seeding had saved the user 14 minutes.

The cause was a call routed to the wrong object. `SnapshotService` composes its
sub-services rather than inheriting them:

    self.archive = ArchiveSnapshotService(self)

and `_extract_snapshot_archive` is defined only on `ArchiveSnapshotService`.
With no `__getattr__` fallback, calling it on the parent raised `AttributeError`
unconditionally -- this could never have worked. A bare `except Exception`
absorbed it and `return True` told the caller the project had been seeded.

There was no test asserting a seeded init produces a seeded project, which is
why it survived.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core.handlers.assets import AssetService
from ldm_core.handlers.snapshot import SnapshotService
from ldm_core.snapshot.archive import ArchiveSnapshotService


class TestExtractionIsRoutedToTheComposedService(unittest.TestCase):
    """The structural fact the bug depended on."""

    def test_the_parent_service_does_not_define_the_extract_method(self):
        """Guards against a future refactor making this test vacuous.

        If `_extract_snapshot_archive` ever appears on `SnapshotService`, the
        routing assertion below stops proving anything, because both spellings
        would then work.
        """
        self.assertFalse(
            hasattr(SnapshotService, "_extract_snapshot_archive"),
            "SnapshotService now defines _extract_snapshot_archive; the "
            "routing test below no longer distinguishes correct from broken",
        )
        self.assertTrue(hasattr(ArchiveSnapshotService, "_extract_snapshot_archive"))
        self.assertFalse(
            hasattr(SnapshotService, "__getattr__"),
            "a __getattr__ fallback would forward the wrong call silently",
        )

    def test_extraction_goes_through_the_archive_sub_service(self):
        import inspect

        source = inspect.getsource(AssetService._fetch_seed)
        self.assertIn(
            "archive._extract_snapshot_archive",
            source,
            "extraction must be routed through the composed archive service",
        )


class TestSeedingReportsFailureHonestly(unittest.TestCase):
    """`pipelines/run.py` reads the return value as 'the project WAS seeded'.

    Driven through the real `_fetch_seed`, entered via its cached-seed branch:
    when the archive is already in `<home>/.ldm/seeds/`, the method skips the
    download entirely and goes straight to extraction.
    """

    TAG = "2026.q1.12-lts"
    DB = "postgresql"
    SEARCH = "shared"

    def _run(self, extract_side_effect=None):
        import tempfile
        from pathlib import Path

        from ldm_core.constants import SEED_VERSION

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            seeds = home / ".ldm" / "seeds"
            seeds.mkdir(parents=True)
            name = f"seeded-{self.TAG}-{self.DB}-{self.SEARCH}-v{SEED_VERSION}.tar.gz"
            (seeds / name).write_bytes(b"pretend archive")

            manager = MagicMock()
            manager.args = MagicMock(no_osgi_seed=False)
            manager.snapshot.archive._extract_snapshot_archive = MagicMock(
                side_effect=extract_side_effect
            )
            service = AssetService(manager)

            with (
                patch("ldm_core.handlers.assets.get_actual_home", return_value=home),
                patch("ldm_core.ui.UI.interruptible_pause"),
            ):
                result = service._fetch_seed(
                    self.TAG, self.DB, self.SEARCH, {"root": home / "proj"}
                )
            return result, manager

    def test_a_failed_extraction_returns_false(self):
        """Returning True marked an unseeded project as seeded.

        The caller then wrote `seeded = "true"` into meta and called
        `track_roi(840, "first-boot seeding")`, so a failure was
        indistinguishable from success in both the metadata and the output.
        """
        result, _ = self._run(extract_side_effect=OSError("corrupt archive"))
        self.assertFalse(
            result, "a failed extraction must not report the project as seeded"
        )

    def test_a_successful_extraction_returns_true(self):
        result, manager = self._run()
        self.assertTrue(result)
        manager.snapshot.archive._extract_snapshot_archive.assert_called_once()

    def test_a_programming_error_is_not_absorbed_as_a_runtime_condition(self):
        """AttributeError/TypeError must propagate, not become a warning.

        The generic handler exists to tolerate a corrupt or unreadable archive.
        Letting it also swallow a wrong method name is exactly what hid #1322
        for the lifetime of the feature.
        """
        with self.assertRaises(AttributeError):
            self._run(extract_side_effect=AttributeError("no such method"))


if __name__ == "__main__":
    unittest.main()
