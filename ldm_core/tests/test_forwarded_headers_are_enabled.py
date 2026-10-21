"""Liferay honours the proxy's forwarded headers (AICA#1283).

Traefik terminates TLS and forwards `X-Forwarded-Proto: https`. Liferay reads
that header only when `web.server.forwarded.protocol.enabled=true`, and the
port header only with `web.server.forwarded.port.enabled=true`; both default
to false. LDM wrote the header NAMES and never the flags -- and spelled the
protocol key `proto`, which Liferay does not read. So `portal.isSecure()` was
false behind the proxy, OAuth redirect URIs were registered as `http://` for
an `https://` origin, and browser sign-in failed on every SSL project.

Written through the real ConfigService into a real portal-ext.properties and
read back, rather than asserted on the dict handed to a mock.
"""

import tempfile
import unittest
from pathlib import Path

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.config import ConfigService
from ldm_core.tests.test_composer import MockComposerManager
from ldm_core.tests.test_workspace import MockWorkspaceManager


class TheForwardedHeadersAreLive(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name) / "proj"
        root.mkdir()
        self.paths = BaseHandler.setup_paths(MockWorkspaceManager(), root)
        self.paths["files"].mkdir(parents=True, exist_ok=True)
        manager = MockComposerManager()
        from ldm_core.defaults import DefaultsManager

        manager.defaults = DefaultsManager()  # type: ignore[assignment]
        manager.args.share = False
        manager.config = ConfigService(manager)  # type: ignore[assignment]
        self.composer = ComposerService(manager)

    def tearDown(self):
        self._td.cleanup()

    def _portal_ext(self):
        self.composer._build_liferay_service(
            self.paths,
            {"tag": "2026.q1.0-lts", "container_name": "proj"},
            "aica.demo",
            "proj",
            True,
            [],
            self.paths,
        )
        text = (self.paths["files"] / "portal-ext.properties").read_text()
        return dict(
            line.split("=", 1)
            for line in text.splitlines()
            if "=" in line and not line.lstrip().startswith(("#", "!"))
        )

    def test_the_protocol_and_port_headers_are_enabled(self):
        props = self._portal_ext()
        self.assertEqual(props.get("web.server.forwarded.protocol.enabled"), "true")
        self.assertEqual(props.get("web.server.forwarded.port.enabled"), "true")

    def test_the_protocol_header_key_is_the_one_liferay_reads(self):
        props = self._portal_ext()
        self.assertEqual(
            props.get("web.server.forwarded.protocol.header"), "X-Forwarded-Proto"
        )
        self.assertNotIn("web.server.forwarded.proto.header", props)


if __name__ == "__main__":
    unittest.main()
