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
        self.config = MagicMock()
        self.get_resolved_ip = MagicMock(return_value="127.0.0.1")


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

    def test_build_liferay_service_volumes_and_jvm(self):
        paths = {
            "root": Path("/tmp/proj"),
            "deploy": Path("/tmp/proj/deploy"),
            "files": Path("/tmp/proj/files"),
            "data": Path("/tmp/proj/data"),
            "configs": Path("/tmp/proj/osgi/configs"),
            "modules": Path("/tmp/proj/osgi/modules"),
            "cx": Path("/tmp/proj/osgi/client-extensions"),
            "scripts": Path("/tmp/proj/scripts"),
            "state": Path("/tmp/proj/osgi/state"),
            "logs": Path("/tmp/proj/logs"),
            "portal_log4j": Path("/tmp/proj/osgi/log4j"),
        }
        meta = {"tag": "2026.q1.7-lts", "container_name": "proj"}

        service = self.composer._build_liferay_service(
            paths, meta, "localhost", "proj", False, None
        )

        # Verify volume mapping
        volumes = service["volumes"]
        # Use startswith to handle potential :z label on Linux, but check for named volumes now
        self.assertTrue(
            any(v.startswith("proj-data:/opt/liferay/data") for v in volumes)
        )
        self.assertTrue(
            any(v.startswith("proj-state:/opt/liferay/osgi/state") for v in volumes)
        )
        self.assertFalse(any("/storage/liferay/data" in v for v in volumes))

        # Verify JVM opts
        env = service["environment"]
        jvm_opts = next(
            (e.split("=", 1)[1] for e in env if e.startswith("LIFERAY_JVM_OPTS=")), ""
        )
        self.assertIn("-Djdk.util.zip.disableZip64ExtraFieldValidation=true", jvm_opts)

    def test_is_ssl_active_meta(self):
        self.manager.args.ssl = None
        # Use a non-localhost host name
        self.assertTrue(self.composer._is_ssl_active("myhost.local", {"ssl": "true"}))
        self.assertFalse(self.composer._is_ssl_active("myhost.local", {"ssl": "false"}))


if __name__ == "__main__":
    unittest.main()
