import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.composer import ComposerService


class MockComposerManager:
    def __init__(self):
        self.args = MagicMock()
        self.args.ssl = None
        self.verbose = False
        self.non_interactive = True
        self.workspace = MagicMock()
        self.config = MagicMock()
        self.share = MagicMock()
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

    def test_tag_sanitization_and_image_determination(self):
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

        # Case 1: DXP prefix
        meta = {"tag": "dxp-2026.q1.7-lts", "container_name": "proj"}
        service = self.composer._build_liferay_service(
            paths, meta, "localhost", "proj", False, None
        )
        self.assertEqual(service["image"], "liferay/dxp:2026.q1.7-lts")

        # Case 2: Portal prefix
        meta = {"tag": "portal-7.4.13-u102", "container_name": "proj"}
        service = self.composer._build_liferay_service(
            paths, meta, "localhost", "proj", False, None
        )
        self.assertEqual(service["image"], "liferay/portal:7.4.13-u102")

        # Case 3: Legacy portal u-tag without prefix
        meta = {"tag": "7.4.13-u102", "container_name": "proj"}
        service = self.composer._build_liferay_service(
            paths, meta, "localhost", "proj", False, None
        )
        self.assertEqual(service["image"], "liferay/portal:7.4.13-u102")

        # Case 4: Modern tag without prefix (defaults to DXP)
        meta = {"tag": "2026.q1.4-lts", "container_name": "proj"}
        service = self.composer._build_liferay_service(
            paths, meta, "localhost", "proj", False, None
        )
        self.assertEqual(service["image"], "liferay/dxp:2026.q1.4-lts")

    def test_explicit_volume_naming(self):
        """Verify that named volumes have an explicit 'name' property to prevent prefixing."""
        paths = {"root": Path("/tmp/proj"), "compose": Path("/tmp/proj/compose.yml")}
        meta = {"container_name": "proj"}

        with (
            patch(
                "ldm_core.handlers.composer.dict_to_yaml", return_value="yaml"
            ) as mock_yaml,
            patch("ldm_core.utils.safe_write_text"),
            patch.object(
                self.composer,
                "_build_liferay_service",
                return_value={
                    "volumes": [
                        "proj-data:/opt/liferay/data",
                        "proj-state:/opt/liferay/osgi/state",
                    ]
                },
            ),
            patch.object(self.composer, "_build_db_service", return_value={}),
            patch.object(self.composer, "_build_search_service", return_value={}),
            patch.object(self.composer, "_build_extensions_services", return_value={}),
        ):
            self.composer.write_docker_compose(paths, meta)

            compose_dict = mock_yaml.call_args[0][0]
            self.assertIn("volumes", compose_dict)
            self.assertEqual(compose_dict["volumes"]["proj-data"]["name"], "proj-data")
            self.assertEqual(
                compose_dict["volumes"]["proj-state"]["name"], "proj-state"
            )

    @patch("ldm_core.handlers.composer.dict_to_yaml")
    @patch("ldm_core.utils.safe_write_text")
    def test_generate_compose_with_ngrok(self, mock_write, mock_yaml):
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
            "compose": Path("/tmp/proj/docker-compose.yml"),
        }
        meta = {"tag": "2026.q1.7-lts", "container_name": "proj", "expose": "true"}

        self.manager.args.expose = True
        self.manager.config.get_ngrok_auth_token.return_value = "my-token"

        with (
            patch.object(
                self.composer, "_build_liferay_service", return_value={"volumes": []}
            ),
            patch.object(self.composer, "_build_db_service", return_value=None),
            patch.object(self.composer, "_build_search_service", return_value=None),
            patch.object(self.composer, "_build_extensions_services", return_value={}),
        ):
            self.composer.write_docker_compose(paths, meta)
            self.assertTrue(mock_yaml.called)
            compose = mock_yaml.call_args[0][0]
            self.assertIn("ngrok", compose["services"])
            ngrok_service = compose["services"]["ngrok"]
            self.assertEqual(ngrok_service["image"], "ngrok/ngrok:latest")
            self.assertIn("NGROK_AUTHTOKEN=my-token", ngrok_service["environment"])

    @patch("ldm_core.handlers.composer.dict_to_yaml")
    @patch("ldm_core.utils.safe_write_text")
    def test_generate_compose_with_ngrok_missing_token(self, mock_write, mock_yaml):
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
            "compose": Path("/tmp/proj/docker-compose.yml"),
        }
        meta = {"tag": "2026.q1.7-lts", "container_name": "proj"}

        self.manager.args.expose = True
        self.manager.config.get_ngrok_auth_token.return_value = None

        with (
            patch.object(
                self.composer, "_build_liferay_service", return_value={"volumes": []}
            ),
            patch.object(self.composer, "_build_db_service", return_value=None),
            patch.object(self.composer, "_build_search_service", return_value=None),
            patch.object(self.composer, "_build_extensions_services", return_value={}),
            patch("ldm_core.ui.UI.warning") as mock_warning,
        ):
            self.composer.write_docker_compose(paths, meta)
            self.assertTrue(mock_yaml.called)
            compose = mock_yaml.call_args[0][0]
            self.assertNotIn("ngrok", compose["services"])
            mock_warning.assert_called_once()

    @patch("ldm_core.handlers.composer.dict_to_yaml")
    @patch("ldm_core.utils.safe_write_text")
    def test_generate_compose_with_lfr_tunnel_docker(self, mock_write, mock_yaml):
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
            "compose": Path("/tmp/proj/docker-compose.yml"),
        }
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "proj",
            "share": "true",
            "share_provider": "lfr-tunnel-docker",
        }

        self.manager.args.expose = False
        self.manager.args.share = True
        self.manager.args.share_provider = "lfr-tunnel-docker"
        self.manager.args.share_subdomain = "my-sub"
        self.manager.args.share_image = "custom/lfr-tunnel:latest"
        self.manager.share._get_auth_token.return_value = "my-token"

        import os

        with patch.dict(os.environ, {"LFT_SERVER_URL": "https://tunnel.lfr-demo.se"}):
            with (
                patch.object(
                    self.composer,
                    "_build_liferay_service",
                    return_value={"volumes": []},
                ),
                patch.object(self.composer, "_build_db_service", return_value=None),
                patch.object(self.composer, "_build_search_service", return_value=None),
                patch.object(
                    self.composer, "_build_extensions_services", return_value={}
                ),
            ):
                self.composer.write_docker_compose(paths, meta)
                self.assertTrue(mock_yaml.called)
                compose = mock_yaml.call_args[0][0]
                self.assertIn("lfr-tunnel", compose["services"])
                tunnel_service = compose["services"]["lfr-tunnel"]
                self.assertEqual(tunnel_service["image"], "custom/lfr-tunnel:latest")
                self.assertIn(
                    "LFT_CLIENT_TOKEN=${LFT_CLIENT_TOKEN:-my-token}",
                    tunnel_service["environment"],
                )
                self.assertIn("LFT_TARGET_HOST=liferay", tunnel_service["environment"])
                self.assertIn(
                    "LFT_CLIENT_SUBDOMAIN=${LFT_SUBDOMAIN:-my-sub}",
                    tunnel_service["environment"],
                )
                self.assertIn(
                    "LFT_CLIENT_SERVER=${LFT_SERVER_URL:-https://tunnel.lfr-demo.se}",
                    tunnel_service["environment"],
                )
                self.assertEqual(tunnel_service.get("ports"), ["4040:4040"])
                self.assertIn(
                    "LFT_INSPECTOR_BIND=${LFT_INSPECTOR_BIND:-0.0.0.0}",
                    tunnel_service["environment"],
                )
                self.assertEqual(
                    tunnel_service["deploy"]["resources"]["limits"]["cpus"], "0.10"
                )
                self.assertEqual(
                    tunnel_service["deploy"]["resources"]["limits"]["memory"], "50M"
                )

    @patch("ldm_core.handlers.composer.dict_to_yaml")
    @patch("ldm_core.utils.safe_write_text")
    def test_generate_compose_with_lfr_tunnel_docker_missing_token(
        self, mock_write, mock_yaml
    ):
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
            "compose": Path("/tmp/proj/docker-compose.yml"),
        }
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "proj",
            "share": "true",
            "share_provider": "lfr-tunnel-docker",
        }

        self.manager.args.expose = False
        self.manager.args.share = True
        self.manager.args.share_provider = "lfr-tunnel-docker"
        self.manager.share._get_auth_token.return_value = None

        with (
            patch.object(
                self.composer, "_build_liferay_service", return_value={"volumes": []}
            ),
            patch.object(self.composer, "_build_db_service", return_value=None),
            patch.object(self.composer, "_build_search_service", return_value=None),
            patch.object(self.composer, "_build_extensions_services", return_value={}),
            patch("ldm_core.ui.UI.warning") as mock_warning,
        ):
            self.composer.write_docker_compose(paths, meta)
            self.assertTrue(mock_yaml.called)
            compose = mock_yaml.call_args[0][0]
            self.assertNotIn("lfr-tunnel", compose["services"])
            mock_warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
