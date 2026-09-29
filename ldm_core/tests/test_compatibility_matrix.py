"""The dependency matrix must survive a failed refresh (LDM-#2007).

`fetch_compatibility_metadata()` has three sources, and before LDM-#2007 two of
them were unreachable in exactly the situation they exist for:

* the **cache** was consulted only while under 24h old, so a stale but
  perfectly valid copy was discarded rather than used when the refresh failed;
* the **bundled baseline** was read from the repository root, which neither
  packager ships -- `pyproject.toml` includes `ldm_core*` only, and
  `ldm-macos.spec` declares `ldm_core/resources`. An installed build therefore
  had no baseline at all, verified against a shiv install whose
  `site-packages/` held `ldm_core` and nothing else.

With both unavailable every dependency fell back to its hardcoded literal, and
`pipelines/run.py`'s `or "16"` then read as a PostgreSQL downgrade from any
genuine `16.x` pin -- refusing to start a project whose version had not
changed.

The root copy stays where it is: `master/compatibility.json` is the URL every
released client fetches, and it ships as a release asset
(`.github/workflows/ci.yml`). The packaged copy is a second file by necessity,
so `test_the_packaged_copy_matches_the_published_root_copy` is what keeps the
two honest.
"""

import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import ldm_core
from ldm_core.utils import fetch_compatibility_metadata, packaged_compatibility_path

#: A matrix that cannot be confused with the real one, so a test asserting it
#: was returned cannot pass on the shipped baseline by coincidence.
SENTINEL_MATRIX = {
    "mappings": [
        {
            "tag_range": ">=1999.q1.0",
            "dependencies": {"postgresql": "42.1", "elasticsearch": "9.9.9"},
        }
    ]
}

_REPO_ROOT_COPY = Path(ldm_core.__file__).parent.parent / "compatibility.json"


class TestTheBaselineShips(unittest.TestCase):
    def test_the_baseline_matrix_ships_inside_the_package(self):
        """The baseline must live where both packagers already look.

        Reading it from the repository root made it a developer-only file:
        present in a checkout, absent from every build a user installs.
        """
        path = packaged_compatibility_path()
        self.assertTrue(
            path.is_file(),
            f"the packaged baseline is missing from {path} -- an installed "
            "build has no matrix to fall back on",
        )
        matrix = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(
            matrix.get("mappings"),
            "the packaged baseline carries no mappings, so falling back to it "
            "is indistinguishable from having no matrix at all",
        )

    def test_the_packaged_copy_matches_the_published_root_copy(self):
        """Two files, one truth.

        The root copy is a published web artefact (the raw URL and a release
        asset) and cannot move; the packaged copy is what ships. Nothing stops
        them drifting except this.
        """
        if not _REPO_ROOT_COPY.is_file():
            self.skipTest("running from an installed build; no repository root")
        root = json.loads(_REPO_ROOT_COPY.read_text(encoding="utf-8"))
        packaged = json.loads(packaged_compatibility_path().read_text(encoding="utf-8"))
        self.assertEqual(
            root,
            packaged,
            "compatibility.json and ldm_core/resources/compatibility.json have "
            "drifted -- update both",
        )


class TestAFailedRefreshFallsBack(unittest.TestCase):
    """What `fetch_compatibility_metadata()` returns when the network is gone."""

    def _home_with_cache(self, tmp, matrix, age_seconds):
        cache = Path(tmp) / ".ldm" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        cache_file = cache / "compatibility.json"
        cache_file.write_text(json.dumps(matrix), encoding="utf-8")
        stamp = time.time() - age_seconds
        import os

        os.utime(cache_file, (stamp, stamp))
        return Path(tmp)

    def test_a_stale_cache_is_used_when_the_refresh_fails(self):
        """The reported failure: a two-day-old cache sat unread on disk."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            home = self._home_with_cache(tmp, SENTINEL_MATRIX, 48 * 3600)
            with (
                patch("ldm_core.utils.get_actual_home", return_value=home),
                patch("ldm_core.utils.download_file", return_value=False),
            ):
                matrix = fetch_compatibility_metadata()
        self.assertEqual(
            matrix,
            SENTINEL_MATRIX,
            "a stale cache was discarded even though the refresh that would "
            "have replaced it failed",
        )

    def test_a_fresh_cache_is_still_preferred_without_downloading(self):
        """The existing behaviour, pinned so the fallback cannot subsume it."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            home = self._home_with_cache(tmp, SENTINEL_MATRIX, 60)
            with (
                patch("ldm_core.utils.get_actual_home", return_value=home),
                patch("ldm_core.utils.download_file") as mock_download,
            ):
                matrix = fetch_compatibility_metadata()
        self.assertEqual(matrix, SENTINEL_MATRIX)
        mock_download.assert_not_called()

    def test_the_packaged_baseline_is_the_last_resort(self):
        """No cache at all, and the refresh fails: the shipped matrix answers.

        This is a first run on a machine with no network -- the case the
        baseline exists for, and the one it could not serve.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("ldm_core.utils.get_actual_home", return_value=Path(tmp)),
                patch("ldm_core.utils.download_file", return_value=False),
            ):
                matrix = fetch_compatibility_metadata()
        self.assertTrue(
            matrix.get("mappings"),
            "with no cache and no network the matrix came back empty, which is "
            "what makes run.py invent a PostgreSQL downgrade",
        )


if __name__ == "__main__":
    unittest.main()
