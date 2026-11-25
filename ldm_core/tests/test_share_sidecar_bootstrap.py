"""The sidecar elects and fails over on images that can (LDM-#2536).

`LFT_CLIENT_SERVER` pins the lfr-tunnel client to one gateway. The image reads
`LFT_CLIENT_BOOTSTRAP` -- start here, then elect and fail over -- from
`lfr-tunnel:v1.52.12`; v1.52.11 ignores it, and the image has no gateway of
its own, so an older image given only the bootstrap variable never starts.
From v1.52.12 the client refuses the two together.

Driven through `ComposerService._build_lfr_tunnel_service` with a real
ShareService, as test_share_tunnel_audit.py does: the assertion is on the
environment the compose file gives the sidecar.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.constants import LFR_TUNNEL_IMAGE
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.share_sidecar import image_tag_version
from ldm_core.tests.test_share_tunnel_audit import _SidecarManager

GATEWAY = "https://tunnel.lfr-demo.se"


class TheSidecarGatewayVariableFollowsTheImageTag(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = _SidecarManager(self.root)
        self.composer = ComposerService(self.manager)

    def env(self, image=None, **meta):
        self.manager.args.share_image = image
        meta = {
            "share": "true",
            "share_provider": "lfr-tunnel-docker",
            "share_domain": "lfr-demo.se",
            **meta,
        }
        with (
            patch("ldm_core.handlers.composer.UI"),
            patch("ldm_core.handlers.share.UI"),
        ):
            svc = self.composer._build_lfr_tunnel_service(
                {"root": self.root}, meta, "proj"
            )
        gateway = [
            e
            for e in svc["environment"]
            if e.startswith(("LFT_CLIENT_SERVER=", "LFT_CLIENT_BOOTSTRAP="))
        ]
        # Never both, never neither -- on every image.
        self.assertEqual(len(gateway), 1, svc["environment"])
        return gateway[0]

    @patch.dict(os.environ, {}, clear=True)
    def test_v1_52_12_gets_the_bootstrap_variable(self):
        self.assertEqual(
            self.env(f"{LFR_TUNNEL_IMAGE}:v1.52.12"), f"LFT_CLIENT_BOOTSTRAP={GATEWAY}"
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_a_later_release_gets_it_too(self):
        self.assertEqual(
            self.env(f"{LFR_TUNNEL_IMAGE}:v1.53.0"), f"LFT_CLIENT_BOOTSTRAP={GATEWAY}"
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_the_image_stored_in_meta_counts(self):
        self.assertEqual(
            self.env(None, share_image=f"{LFR_TUNNEL_IMAGE}:v1.52.12"),
            f"LFT_CLIENT_BOOTSTRAP={GATEWAY}",
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_v1_52_11_keeps_the_server_variable(self):
        self.assertEqual(
            self.env(f"{LFR_TUNNEL_IMAGE}:v1.52.11"), f"LFT_CLIENT_SERVER={GATEWAY}"
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_latest_keeps_the_server_variable(self):
        # The default image. Its release is unknown when the compose file is
        # written, and the bootstrap variable on an older one never starts.
        self.assertEqual(self.env(None), f"LFT_CLIENT_SERVER={GATEWAY}")
        self.assertEqual(
            self.env(f"{LFR_TUNNEL_IMAGE}:latest"), f"LFT_CLIENT_SERVER={GATEWAY}"
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_an_explicit_pin_stays_a_pin_on_a_new_image(self):
        self.manager.args.share_domain = "lfr-demo.se"
        self.assertEqual(
            self.env(f"{LFR_TUNNEL_IMAGE}:v1.52.12"), f"LFT_CLIENT_SERVER={GATEWAY}"
        )

    @patch.dict(os.environ, {"LFT_SERVER_URL": "https://tunnel.example"}, clear=True)
    def test_a_pin_in_the_environment_stays_a_pin_on_a_new_image(self):
        self.assertEqual(
            self.env(f"{LFR_TUNNEL_IMAGE}:v1.52.12"),
            "LFT_CLIENT_SERVER=https://tunnel.example",
        )


class TheTagIsReadCarefully(unittest.TestCase):
    def test_tags(self):
        cases = {
            "peterjrichards/lfr-tunnel:v1.52.12": "1.52.12",
            "peterjrichards/lfr-tunnel:1.52.12": "1.52.12",
            "registry.local:5000/lfr-tunnel:v1.60.0": "1.60.0",
            "registry.local:5000/lfr-tunnel": None,
            "peterjrichards/lfr-tunnel": None,
            "peterjrichards/lfr-tunnel:latest": None,
            "peterjrichards/lfr-tunnel@sha256:" + "a" * 64: None,
            "peterjrichards/lfr-tunnel:dev-build": None,
        }
        for image, expected in cases.items():
            with self.subTest(image=image):
                self.assertEqual(image_tag_version(image), expected)


if __name__ == "__main__":
    unittest.main()
