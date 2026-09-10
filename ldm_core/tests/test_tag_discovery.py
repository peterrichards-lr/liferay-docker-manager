"""Tag discovery contract tests (LDM-#1647).

Every assertion here was written against real Docker Hub responses captured on
2026-09-10 and observed failing against the pre-fix implementation. The shapes
matter more than the values: Docker Hub's ``ordering`` sign is inverted
relative to the DRF convention (bare ``last_updated`` is newest-first,
``-last_updated`` is *oldest*-first), so a window that looks like "the newest
300 tags" was actually the 300 oldest -- all of them 2018-2020 ``7.x`` builds
that no discovery pattern accepts.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.constants import API_BASE_DXP, API_BASE_PORTAL
from ldm_core.utils import discover_latest_tag


def _page(names, next_url=None):
    return json.dumps(
        {"results": [{"name": n} for n in names], "next": next_url},
    )


class FakeHub:
    """Minimal Docker Hub stand-in that records every URL it is asked for."""

    def __init__(self, pages_by_filter, cdn=""):
        # {name filter or None: [ [page 1 names], [page 2 names], ... ]}
        self.pages_by_filter = pages_by_filter
        self.cdn = cdn
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if "product_info" in url:
            return self.cdn

        page = 1
        if "&page=" in url:
            page = int(url.split("&page=")[1].split("&")[0])

        name_filter = None
        if "&name=" in url:
            name_filter = url.split("&name=")[1].split("&")[0]

        pages = self.pages_by_filter.get(name_filter)
        if pages is None:
            return _page([])
        if page > len(pages):
            return _page([])

        base = url.split("&page=")[0]
        next_url = f"{base}&page={page + 1}" if page < len(pages) else None
        return _page(pages[page - 1], next_url)

    @property
    def hub_urls(self):
        return [u for u in self.urls if "product_info" not in u]


class TagDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        patcher = patch(
            "ldm_core.utils.get_actual_home", return_value=Path(self._tmp.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _discover(self, hub, **kwargs):
        with patch("ldm_core.utils.get_raw", hub):
            return discover_latest_tag(API_BASE_DXP, refresh=True, **kwargs)

    def test_requests_newest_first_ordering(self):
        """Docker Hub returns OLDEST first for `-last_updated` (LDM-#1647)."""
        hub = FakeHub({None: [["2026.q3.2"]], ".q": [["2026.q3.2"]]})
        self._discover(hub)

        self.assertTrue(hub.hub_urls, "no Docker Hub request was made")
        for url in hub.hub_urls:
            self.assertNotIn(
                "ordering=-last_updated",
                url,
                "requested oldest-first: this window is 2018 tags only",
            )
            self.assertIn("ordering=last_updated", url)

    def test_any_resolves_quarterly_over_legacy(self):
        """`latest`/`--tag-latest` maps to release_type='any' and must resolve."""
        hub = FakeHub(
            {
                ".q": [["2026.q3.2", "2026.q3.1", "2026.q1.12-lts"]],
                None: [["7.4.13-u152", "latest"]],
            }
        )
        self.assertEqual(self._discover(hub, release_type="any"), "2026.q3.2")

    def test_any_ignores_decorated_variants(self):
        hub = FakeHub(
            {
                ".q": [
                    [
                        "2026.q3.2-slim",
                        "2026.q3.2-d10.0.84-20260903131313",
                        "2026.q3.2",
                        "7.4.13-2023.q3.1.rc",
                    ]
                ]
            }
        )
        self.assertEqual(self._discover(hub, release_type="any"), "2026.q3.2")

    def test_qr_resolves_quarterly_release(self):
        """`qr` means Quarterly Release, not a literal `-qr` substring.

        `liferay/dxp` has zero tags containing `-qr`, so the old
        `&name=-qr` sweep plus a `"-qr" not in name` skip could never match
        anything: the option was unsatisfiable by construction.
        """
        hub = FakeHub({".q": [["2026.q3.2", "2026.q1.12-lts"]], "-qr": [[]]})
        self.assertEqual(self._discover(hub, release_type="qr"), "2026.q3.2")

    def test_lts_resolves_latest_patch_not_alphabetical(self):
        hub = FakeHub({"-lts": [["2026.q1.9-lts", "2026.q1.12-lts", "2026.q3.2"]]})
        self.assertEqual(self._discover(hub, release_type="lts"), "2026.q1.12-lts")

    def test_u_accepts_legacy_update_releases(self):
        """The `-u` family is `7.4.13-uNNN`, which TAG_PATTERN rejects."""
        hub = FakeHub({"-u": [["7.4.13-u152", "7.4.13-u151", "7.4.13-u9"]]})
        self.assertEqual(self._discover(hub, release_type="u"), "7.4.13-u152")

    def test_portal_resolves_ga_releases(self):
        """`liferay/portal` has no quarterly, lts, u, qr or nightly tags at all."""
        hub = FakeHub(
            {
                ".q": [[]],
                None: [["latest", "7.4.3.99-ga99", "7.4.3.132-ga132"]],
            }
        )
        with patch("ldm_core.utils.get_raw", hub):
            tag = discover_latest_tag(API_BASE_PORTAL, release_type="any", refresh=True)
        self.assertEqual(tag, "7.4.3.132-ga132")

    def test_nightly_prefers_the_floating_tag(self):
        hub = FakeHub(
            {
                "nightly": [
                    [
                        "7.4.13.nightly-slim-d10.0.85-20260909193502",
                        "7.4.13.nightly",
                        "7.4.13.u2-nightly-d2.0.10-20211229084926",
                    ]
                ]
            }
        )
        self.assertEqual(self._discover(hub, release_type="nightly"), "7.4.13.nightly")
        self.assertEqual(self._discover(hub, release_type="master"), "7.4.13.nightly")

    def test_prefix_search_pages_past_the_first_page(self):
        """`&name=` is a substring match, so page 1 fills with build-dated junk.

        `name=2026` matches 2179 `liferay/dxp` tags, nearly all of them
        `7.4.13-uNNN-d10.0.x-2026...` builds that fail the local
        `startswith` check. Capping a prefix search at one page discarded
        all 100 of them and reported nothing found.
        """
        hub = FakeHub(
            {
                "2026": [
                    ["7.4.13-u152-d10.0.78-20260813133259"],
                    ["7.4.13-u151-d10.0.65-snapshot-20260714181406"],
                    ["2026.q3.2"],
                ]
            }
        )
        self.assertEqual(self._discover(hub, prefix_filter="2026"), "2026.q3.2")

    def test_cdn_fallback_survives_a_dead_primary_api(self):
        """The CDN list was merged inside the loop, after the `break`.

        A primary API returning nothing therefore discarded the secondary
        source entirely -- the one situation it exists for.
        """
        hub = FakeHub(
            {},
            cdn=json.dumps(
                {
                    "dxp-2026.q3.2": {"liferayDockerImage": "liferay/dxp:2026.q3.2"},
                    "dxp-7.4-u112": {"liferayDockerImage": "liferay/dxp:7.4.13-u112"},
                }
            ),
        )
        hub.pages_by_filter = {}

        def dead(url):
            if "product_info" in url:
                return hub.cdn
            return None

        with patch("ldm_core.utils.get_raw", dead):
            tag = discover_latest_tag(API_BASE_DXP, release_type="any", refresh=True)
        self.assertEqual(tag, "2026.q3.2")

    def test_cdn_fallback_does_not_leak_across_repositories(self):
        """`.product_info.json` lists dxp and portal images in one document.

        `liferay/portal` has no `-u` tags, so the sweep finds nothing and the
        CDN fallback runs -- it must not answer a portal query with a dxp
        image's tag.
        """
        cdn = json.dumps(
            {
                "dxp-7.4-u112": {"liferayDockerImage": "liferay/dxp:7.4.13-u112"},
                "portal-7.4-ga112": {
                    "liferayDockerImage": "liferay/portal:7.4.3.112-ga112"
                },
            }
        )
        hub = FakeHub({}, cdn=cdn)

        with patch("ldm_core.utils.get_raw", hub):
            self.assertIsNone(
                discover_latest_tag(API_BASE_PORTAL, release_type="u", refresh=True)
            )
            self.assertEqual(
                discover_latest_tag(API_BASE_PORTAL, release_type="any", refresh=True),
                "7.4.3.112-ga112",
            )
            self.assertEqual(
                discover_latest_tag(API_BASE_DXP, release_type="u", refresh=True),
                "7.4.13-u112",
            )

    def test_no_candidates_returns_none(self):
        hub = FakeHub({None: [["latest", "no-such-tag"]], ".q": [[]]})
        self.assertIsNone(self._discover(hub, release_type="any"))


if __name__ == "__main__":
    unittest.main()
