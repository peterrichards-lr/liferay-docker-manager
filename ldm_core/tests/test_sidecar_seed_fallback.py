"""A sidecar project must still find a seed (LDM-#2164).

The seed's search mode is part of its filename, and only `-shared-` has ever
been published -- 21 assets on the `seeded-states` release, none sidecar. So
`--sidecar` asked for a file that does not exist, was told "No seed found for
tag", and built its schema from scratch.

The reach is narrower than the issue first claimed. `_ensure_seeded` derives
the seed's mode from `args.sidecar` and the tag age, not from the mode the
project resolves to, so a no-flag project on a current tag asks for the shared
name and finds it. Only an explicit `--sidecar`, or a pre-2025 tag, hits the
name that was never published.

The 9m57s/9m31s figures this docstring first carried are removed rather than
restated: those runs DID bootstrap from the shared seed, on a Windows host with
7.6 GB allocated to Docker Desktop, and the 2m37s was a different machine. They
measured the host, not this defect.

The shared seed works in a sidecar project -- four of them took it and booted
normally -- and LDM already strips search settings that do not match the
resolved mode (LDM-#1773/#1786).

These assert which filenames are REQUESTED and which is used, because that is
where the defect lived. The network is mocked at `_resolve_seed_url`, the seam
that answers "does this asset exist".
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.handlers.assets import AssetService

TAG = "2026.q3.7"
DB = "postgresql"
SHARED = f"seeded-{TAG}-{DB}-shared-v2.tar.gz"
SIDECAR = f"seeded-{TAG}-{DB}-sidecar-v2.tar.gz"


class _Manager:
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True
        self.parse_version = MagicMock(return_value=(2026, 3, 7))
        # Reached only on the cached path, which goes on to extract. The
        # extraction is not what these tests are about -- which filename was
        # asked for is.
        self.snapshot = MagicMock()
        self.run_command = MagicMock(return_value="")
        self.verify_runtime_environment = MagicMock()
        # Set explicitly: an absent attribute on a MagicMock is TRUTHY, so
        # leaving these to the mock would silently choose the opt-out branch.
        self.args.no_osgi_seed = False
        self.args.sidecar = False


class SeedFallbackTestCase(unittest.TestCase):
    """Drives `_fetch_seed` with only the shared asset 'published'."""

    def _fetch(self, search_mode, *, published=(SHARED,), cached=()):
        self.requested: list[str] = []
        service = AssetService(_Manager())

        def _resolve(download_url, tag, tag_name, seed_filename, headers, warn=True):
            self.requested.append(seed_filename)
            if seed_filename in published:
                return download_url
            return None

        with TemporaryDirectory() as home:
            cache = Path(home) / ".ldm" / "seeds"
            cache.mkdir(parents=True)
            for name in cached:
                (cache / name).write_bytes(b"seed")

            with (
                patch.object(service, "_resolve_seed_url", side_effect=_resolve),
                patch(
                    "ldm_core.handlers.assets.get_actual_home", return_value=Path(home)
                ),
                patch("ldm_core.handlers.assets.UI.detail"),
                patch("ldm_core.handlers.assets.UI.warning"),
                patch("ldm_core.handlers.assets.UI.success"),
                patch("ldm_core.handlers.assets.UI.confirm", return_value=False),
            ):
                result = service._fetch_seed(TAG, DB, search_mode, {}, assume_yes=False)
        return result


class TestSidecarFallsBackToTheSharedSeed(SeedFallbackTestCase):
    def test_the_mode_specific_name_is_tried_first(self):
        """Publishing sidecar seeds later must need no change here."""
        self._fetch("sidecar")
        self.assertEqual(self.requested[0], SIDECAR)

    def test_the_shared_seed_is_tried_when_the_sidecar_one_is_absent(self):
        self._fetch("sidecar")
        self.assertIn(SHARED, self.requested)

    def test_a_shared_project_does_not_ask_twice(self):
        """The fallback is the same file -- requesting it twice would be a
        wasted round trip on the common path."""
        self._fetch("shared")
        self.assertEqual(self.requested, [SHARED])


class TestTheCacheIsHonouredUnderEitherName(SeedFallbackTestCase):
    def test_a_cached_shared_seed_satisfies_a_sidecar_request(self):
        """Without this, a machine that already downloaded the shared variant
        would resolve and download it again under the sidecar name."""
        self._fetch("sidecar", cached=(SHARED,))
        self.assertEqual(
            self.requested,
            [],
            "a cached seed must not be re-resolved over the network",
        )


class TestItStillReportsAGenuinelyAbsentSeed(SeedFallbackTestCase):
    def test_no_seed_at_all_is_still_a_failure(self):
        """The fallback must not turn 'this tag has no seed' into a pass."""
        result = self._fetch("sidecar", published=())
        self.assertFalse(result)

    def test_both_names_are_tried_before_giving_up(self):
        self._fetch("sidecar", published=())
        self.assertEqual(self.requested, [SIDECAR, SHARED])


if __name__ == "__main__":
    unittest.main()
