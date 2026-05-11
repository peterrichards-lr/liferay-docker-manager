import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ldm_core.handlers.composer import ComposerService


class MockComposerManager:
    def __init__(self):
        self.args = MagicMock()
        self.args.ssl = None
        self.verbose = False
        self.non_interactive = True
        self.workspace = MagicMock()


class TestComposerService(unittest.TestCase):
    def setUp(self):
        self.manager = MockComposerManager()
        self.composer = ComposerService(self.manager)

    def test_build_extensions_services_basic(self):
        paths = {"root": Path("/tmp"), "cx": Path("/tmp/cx"), "ce_dir": Path("/tmp/ce")}
        meta: dict[str, str] = {}
        # Mock workspace scan
        self.manager.workspace.scan_client_extensions.return_value = [
            {"id": "ms1", "deploy": True, "is_service": True, "path": "/tmp/ms1"}
        ]

        services = self.composer._build_extensions_services(
            paths, meta, "localhost", "proj", False
        )
        self.assertIn("proj-ms1", services)
        self.assertEqual(services["proj-ms1"]["image"], "proj-ms1:latest")

    def test_is_ssl_active_meta(self):
        self.manager.args.ssl = None
        # Use a non-localhost host name
        self.assertTrue(self.composer._is_ssl_active("myhost.local", {"ssl": "true"}))
        self.assertFalse(self.composer._is_ssl_active("myhost.local", {"ssl": "false"}))


if __name__ == "__main__":
    unittest.main()
