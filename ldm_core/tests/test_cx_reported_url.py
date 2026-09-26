"""LDM-#1981: LDM reported the wrong address for a client extension.

Both reporting sites appended a port that belongs to something else:

* `diagnostics/info.py` used `meta["port"]` -- the port LIFERAY is published
  on -- so `ldm info` sent the user to Liferay, which answers 404.
* `dashboard/server.py` hardcoded `8080`, Liferay's CONTAINER port, so every
  extension link in the dashboard pointed at the same wrong place.

The extension is published on `port_<ext_id>`, assigned per extension.

This is the address a user is most likely to follow, so a wrong one costs
exactly the debugging these commands exist to save. An external team spent a
probe cycle on a "proxy fault" that was this.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestBothSitesUseTheExtensionsOwnPort(unittest.TestCase):
    """Asserted against the source rather than by driving the renderers.

    Both sites sit inside long report-building functions that need a live
    project, a docker daemon and a populated meta to reach. What went wrong was
    a one-token choice of variable, so that is what is pinned -- and pinning it
    here is honest about being a structural check rather than a behavioural one.
    """

    def test_info_uses_the_extension_port(self):
        src = _source("ldm_core/diagnostics/info.py")
        self.assertIn(
            'ext_port = meta.get(f"port_{ext_id}") or port',
            src,
            "ldm info must resolve the EXTENSION's port; meta['port'] is "
            "Liferay's and reaches Liferay instead (LDM-#1981)",
        )
        self.assertIn('f"http://{ext_id}.{host_name}:{ext_port}"', src)

    def test_info_no_longer_appends_liferays_port(self):
        src = _source("ldm_core/diagnostics/info.py")
        self.assertNotIn(
            'local_url = f"http://{ext_id}.{host_name}:{port}"',
            src,
            "the bare `port` here is Liferay's (LDM-#1981)",
        )

    def test_dashboard_uses_the_extension_port(self):
        src = _source("ldm_core/dashboard/server.py")
        self.assertIn('ext_port = meta.get(f"port_{ext_id}") or port', src)
        self.assertNotIn(
            'f"http://{ext_id}.{host_name}:8080"',
            src,
            "8080 is Liferay's CONTAINER port, hardcoded for every extension "
            "(LDM-#1981)",
        )

    def test_neither_site_hardcodes_a_port_for_an_extension_url(self):
        """The guard against the repeat: any literal port in a CX URL is wrong,
        because the value is per-extension and assigned at run time."""
        for rel in ("ldm_core/diagnostics/info.py", "ldm_core/dashboard/server.py"):
            with self.subTest(site=rel):
                for line in _source(rel).splitlines():
                    if "{ext_id}.{host_name}" not in line:
                        continue
                    self.assertIsNone(
                        re.search(r"\{host_name\}:\d+", line),
                        f"{rel} hardcodes a port in a client-extension URL: "
                        f"{line.strip()} (LDM-#1981)",
                    )

    def test_the_ssl_url_carries_no_port(self):
        """With SSL the global proxy fronts the extension on 443, so a port
        would be wrong in the other direction."""
        for rel in ("ldm_core/diagnostics/info.py", "ldm_core/dashboard/server.py"):
            with self.subTest(site=rel):
                src = _source(rel)
                self.assertNotIn('f"https://{ext_id}.{host_name}:', src)


if __name__ == "__main__":
    unittest.main()
