import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.composer import ComposerService


class MockComposerManager:
    def __init__(self):
        self.args = MagicMock()
        self.args.ssl = None
        self.args.lean = False
        self.args.tunnel_managed_cors = False
        self.verbose = False
        self.non_interactive = True
        self.workspace = MagicMock()
        self.config = MagicMock()
        self.config.get_global_config.return_value = {}
        self.share = MagicMock()
        self.defaults = MagicMock()
        self.get_resolved_ip = MagicMock(return_value="127.0.0.1")
        self.run_command = MagicMock(return_value="")


class TestComposerService(unittest.TestCase):
    def setUp(self):
        self.manager = MockComposerManager()
        self.composer = ComposerService(self.manager)
        # Ensure GITHUB_ACTIONS is not "true" during testing so that adaptive tier tests pass
        self.environ_patcher = patch.dict("os.environ", {"GITHUB_ACTIONS": "false"})
        self.environ_patcher.start()

    def tearDown(self):
        self.environ_patcher.stop()

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

    def test_windows_drive_letter_volumes_not_named(self):
        """Verify that Windows drive letter paths are not incorrectly classified as named volume C."""
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
                        "C:/Liferay/Projects/Zukunft Digital/deploy:/mnt/liferay/deploy",
                        "proj-data:/opt/liferay/data",
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
            self.assertNotIn("C", compose_dict["volumes"])
            self.assertIn("proj-data", compose_dict["volumes"])

    def test_spaces_in_volume_names_are_sanitized(self):
        """Verify that projects with spaces in their name do not generate named volumes with spaces."""
        paths = {
            "root": Path("/tmp/Zukunft Digital"),
            "deploy": Path("/tmp/Zukunft Digital/deploy"),
            "files": Path("/tmp/Zukunft Digital/files"),
            "scripts": Path("/tmp/Zukunft Digital/scripts"),
            "modules": Path("/tmp/Zukunft Digital/modules"),
            "cx": Path("/tmp/Zukunft Digital/cx"),
            "portal_log4j": Path("/tmp/Zukunft Digital/portal_log4j"),
            "state": Path("/tmp/Zukunft Digital/state"),
            "logs": Path("/tmp/Zukunft Digital/logs"),
        }
        meta = {"container_name": "Zukunft Digital"}

        service = self.composer._build_liferay_service(
            paths, meta, "localhost", "Zukunft-Digital", False, None
        )
        volumes = service["volumes"]
        self.assertTrue(
            any(
                v.startswith("Zukunft-Digital-state:/opt/liferay/osgi/state")
                for v in volumes
            ),
            f"State mapping was: {volumes}",
        )

    def test_spaces_in_container_names_are_sanitized(self):
        """Verify that Liferay, DB, and Tunnel container names with spaces are sanitized to use hyphens."""
        paths = {
            "root": Path("/tmp/Zukunft Digital"),
            "deploy": Path("/tmp/Zukunft Digital/deploy"),
            "files": Path("/tmp/Zukunft Digital/files"),
            "scripts": Path("/tmp/Zukunft Digital/scripts"),
            "modules": Path("/tmp/Zukunft Digital/modules"),
            "cx": Path("/tmp/Zukunft Digital/cx"),
            "portal_log4j": Path("/tmp/Zukunft Digital/portal_log4j"),
            "state": Path("/tmp/Zukunft Digital/state"),
            "logs": Path("/tmp/Zukunft Digital/logs"),
            "compose": Path("/tmp/Zukunft Digital/docker-compose.yml"),
        }

        # 1. Custom container names in metadata containing spaces
        meta_explicit = {
            "tag": "2026.q1.7-lts",
            "liferay_container_name": "Zukunft Digital Liferay",
            "db_container_name": "Zukunft Digital Database",
            "tunnel_container_name": "Zukunft Digital Tunnel",
            "db": "postgresql",
        }

        liferay_service = self.composer._build_liferay_service(
            paths, meta_explicit, "localhost", "Zukunft Digital", False, None
        )
        self.assertEqual(liferay_service["container_name"], "Zukunft-Digital-Liferay")

        db_service = self.composer._build_db_service(meta_explicit, "Zukunft Digital")
        self.assertEqual(db_service["container_name"], "Zukunft-Digital-Database")

        # 2. Fallbacks when container names are omitted in metadata but project name has spaces
        meta_fallback = {
            "tag": "2026.q1.7-lts",
            "db": "postgresql",
        }

        liferay_service_fb = self.composer._build_liferay_service(
            paths, meta_fallback, "localhost", "Zukunft Digital", False, None
        )
        self.assertEqual(liferay_service_fb["container_name"], "Zukunft-Digital")

        db_service_fb = self.composer._build_db_service(
            meta_fallback, "Zukunft Digital"
        )
        self.assertEqual(db_service_fb["container_name"], "Zukunft-Digital-db")

        # 3. Tunnel sidecar container names (both explicit and fallback)
        self.manager.args.expose = False
        self.manager.args.share = True
        self.manager.args.share_provider = "lfr-tunnel-docker"
        self.manager.args.share_subdomain = "my-sub"
        self.manager.args.share_image = "custom/lfr-tunnel:latest"
        self.manager.share._get_auth_token.return_value = "my-token"
        import os

        with patch.dict(os.environ, {"LFT_SERVER_URL": "https://tunnel.lfr-demo.se"}):
            with patch("ldm_core.handlers.composer.dict_to_yaml") as mock_yaml:
                with patch("ldm_core.utils.safe_write_text") as mock_write:
                    with (
                        patch.object(
                            self.composer,
                            "_build_liferay_service",
                            return_value={"volumes": []},
                        ),
                        patch.object(
                            self.composer, "_build_db_service", return_value=None
                        ),
                        patch.object(
                            self.composer, "_build_search_service", return_value=None
                        ),
                        patch.object(
                            self.composer, "_build_extensions_services", return_value={}
                        ),
                    ):
                        # 3a. Explicit Tunnel Container Name
                        self.composer.write_docker_compose(paths, meta_explicit)
                        self.assertTrue(mock_yaml.called)
                        compose_explicit = mock_yaml.call_args[0][0]
                        tunnel_explicit = compose_explicit["services"]["lfr-tunnel"]
                        self.assertEqual(
                            tunnel_explicit["container_name"], "Zukunft-Digital-Tunnel"
                        )

                        # Reset mock and test fallback
                        mock_yaml.reset_mock()
                        self.composer.write_docker_compose(paths, meta_fallback)
                        self.assertTrue(mock_yaml.called)
                        compose_fallback = mock_yaml.call_args[0][0]
                        tunnel_fallback = compose_fallback["services"]["lfr-tunnel"]
                        self.assertEqual(
                            tunnel_fallback["container_name"],
                            "Zukunft-Digital-lfr-tunnel",
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
                self.assertEqual(tunnel_service.get("pull_policy"), "always")
                self.assertEqual(tunnel_service["container_name"], "proj-lfr-tunnel")
                self.assertEqual(
                    tunnel_service.get("volumes"),
                    ["/tmp/proj/logs:/opt/liferay/logs"],
                )
                self.assertEqual(
                    tunnel_service.get("entrypoint"),
                    [
                        "/bin/sh",
                        "-c",
                        "./lfr-tunnel -ports 8080 2>&1 | tee /opt/liferay/logs/lfr-tunnel.log",
                    ],
                )
                self.assertIn(
                    "LFT_CLIENT_TOKEN=${LFT_CLIENT_TOKEN:-my-token}",
                    tunnel_service["environment"],
                )
                self.assertIn("LFT_TARGET_HOST=liferay", tunnel_service["environment"])
                self.assertIn("LFT_PRESERVE_HOST=true", tunnel_service["environment"])
                self.assertIn(
                    "LFT_CLIENT_SUBDOMAIN=${LFT_SUBDOMAIN:-my-sub}",
                    tunnel_service["environment"],
                )
                self.assertIn(
                    "LFT_CLIENT_SERVER=${LFT_SERVER_URL:-https://tunnel.lfr-demo.se}",
                    tunnel_service["environment"],
                )
                self.assertNotIn("ports", tunnel_service)
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

    @patch("ldm_core.handlers.composer.dict_to_yaml")
    @patch("ldm_core.utils.safe_write_text")
    def test_generate_compose_with_lfr_tunnel_docker_opt_in_inspector(
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
            "share_inspector": "true",
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
                self.assertEqual(tunnel_service.get("pull_policy"), "always")
                self.assertEqual(tunnel_service["container_name"], "proj-lfr-tunnel")
                self.assertEqual(
                    tunnel_service.get("volumes"),
                    ["/tmp/proj/logs:/opt/liferay/logs"],
                )
                self.assertEqual(
                    tunnel_service.get("entrypoint"),
                    [
                        "/bin/sh",
                        "-c",
                        "./lfr-tunnel -ports 8080 2>&1 | tee /opt/liferay/logs/lfr-tunnel.log",
                    ],
                )
                self.assertEqual(tunnel_service.get("ports"), ["4040:4040"])
                self.assertIn(
                    "LFT_INSPECTOR_BIND=${LFT_INSPECTOR_BIND:-0.0.0.0}",
                    tunnel_service["environment"],
                )

    def test_build_liferay_service_with_share_host(self):
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

        self.manager.args.share = True
        self.manager.args.share_provider = "lfr-tunnel"
        self.manager.args.share_subdomain = "my-sub"
        self.manager.share.resolve_public_tunnel_url.return_value = (
            "https://my-sub.lfr-demo.se"
        )

        self.composer._build_liferay_service(
            paths, meta, "localhost", "proj", False, None
        )

        self.manager.config.update_portal_ext.assert_any_call(
            paths,
            {
                "web.server.forwarded.host.header": "X-Forwarded-Host",
                "web.server.forwarded.port.header": "X-Forwarded-Port",
                "web.server.forwarded.proto.header": "X-Forwarded-Proto",
                "virtual.hosts.valid.hosts": "localhost,127.0.0.1,localhost,liferay,*.lfr-demo.online,*.lfr-demo.se",
                "web.server.host": "my-sub.lfr-demo.se",
                "web.server.https.port": "443",
                "web.server.protocol": "https",
            },
        )

    def test_build_liferay_service_cleanup_portal_ext(self):
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

        self.manager.args.share = False

        self.composer._build_liferay_service(
            paths, meta, "localhost", "proj", False, None
        )

        self.manager.config.update_portal_ext.assert_any_call(
            paths,
            {
                "web.server.forwarded.host.header": "X-Forwarded-Host",
                "web.server.forwarded.port.header": "X-Forwarded-Port",
                "web.server.forwarded.proto.header": "X-Forwarded-Proto",
                "virtual.hosts.valid.hosts": "localhost,127.0.0.1,localhost,liferay,*.lfr-demo.online,*.lfr-demo.se",
                "web.server.host": "",
                "web.server.https.port": "",
                "web.server.protocol": "",
            },
        )

    @patch("ldm_core.handlers.composer.dict_to_yaml")
    @patch("ldm_core.utils.safe_write_text")
    def test_generate_compose_with_lfr_tunnel_docker_custom_domain(
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
        meta = {"tag": "2026.q1.9-lts", "container_name": "proj"}

        self.manager.args.share = True
        self.manager.args.share_provider = "lfr-tunnel-docker"
        self.manager.args.share_subdomain = "my-sub"
        self.manager.args.share_domain = "lfr-demo.se"
        self.manager.share._get_auth_token.return_value = "my-token"

        with (
            patch.object(
                self.composer,
                "_build_liferay_service",
                return_value={"volumes": []},
            ),
            patch.object(self.composer, "_build_db_service", return_value=None),
            patch.object(self.composer, "_build_search_service", return_value=None),
            patch.object(self.composer, "_build_extensions_services", return_value={}),
        ):
            self.composer.write_docker_compose(paths, meta)
            self.assertTrue(mock_yaml.called)
            compose = mock_yaml.call_args[0][0]
            self.assertIn("lfr-tunnel", compose["services"])
            tunnel_service = compose["services"]["lfr-tunnel"]
            self.assertIn(
                "LFT_CLIENT_SERVER=${LFT_SERVER_URL:-https://tunnel.lfr-demo.se}",
                tunnel_service["environment"],
            )

    @patch("ldm_core.handlers.composer.ComposerService.get_physical_host_memory_bytes")
    def test_get_default_jvm_args_low_memory_4gb(self, mock_host_mem):
        # Simulated host memory 4 GB (in bytes)
        mock_host_mem.return_value = 4 * 1024 * 1024 * 1024
        self.manager.run_command.return_value = ""  # No docker info
        args = self.composer.get_default_jvm_args()
        self.assertIn("-Xmx2048m", args)
        self.assertIn("-Xms1024m", args)
        self.assertIn("-XX:MaxMetaspaceSize=384m", args)
        self.assertNotIn("MaxMetadataSize", args)

    @patch("ldm_core.handlers.composer.ComposerService.get_physical_host_memory_bytes")
    def test_get_default_jvm_args_low_memory_8gb(self, mock_host_mem):
        # Simulated host memory 8 GB
        mock_host_mem.return_value = 8 * 1024 * 1024 * 1024
        self.manager.run_command.return_value = ""  # No docker info
        args = self.composer.get_default_jvm_args()
        self.assertIn("-Xmx3072m", args)
        self.assertIn("-Xms2048m", args)
        self.assertIn("-XX:MaxMetaspaceSize=512m", args)
        self.assertNotIn("MaxMetadataSize", args)

    @patch("ldm_core.handlers.composer.ComposerService.get_physical_host_memory_bytes")
    def test_get_default_jvm_args_high_memory_32gb(self, mock_host_mem):
        # Simulated host memory 32 GB
        mock_host_mem.return_value = 32 * 1024 * 1024 * 1024
        self.manager.run_command.return_value = ""  # No docker info
        args = self.composer.get_default_jvm_args()
        self.assertIn("-Xmx16384m", args)
        self.assertIn("-Xms4096m", args)
        self.assertIn("-XX:MaxMetaspaceSize=1024m", args)
        self.assertNotIn("MaxMetadataSize", args)

    @patch("ldm_core.handlers.composer.ComposerService.get_physical_host_memory_bytes")
    def test_get_default_jvm_args_min_logic(self, mock_host_mem):
        # Simulated host memory is 32 GB, but Docker memory limit is 8 GB
        mock_host_mem.return_value = 32 * 1024 * 1024 * 1024
        self.manager.run_command.return_value = json.dumps(
            {"MemTotal": 8 * 1024 * 1024 * 1024}
        )
        args = self.composer.get_default_jvm_args()
        # Effective memory should be min(32, 8) = 8 GB, which lands in the 8GB tier.
        self.assertIn("-Xmx3072m", args)
        self.assertIn("-Xms2048m", args)
        self.assertIn("-XX:MaxMetaspaceSize=512m", args)

    def test_get_physical_host_memory_bytes_execution(self):
        mem = self.composer.get_physical_host_memory_bytes()
        self.assertGreater(mem, 0)
        self.assertIsInstance(mem, int)

    def test_get_default_jvm_args_lean_profile(self):
        # 1. Test when manager.args.lean is True
        self.manager.args.lean = True
        args = self.composer.get_default_jvm_args()
        self.assertIn("-Xmx2048m", args)
        self.assertIn("-Xms1536m", args)

        # 2. Test when GITHUB_ACTIONS env var is "true"
        self.manager.args.lean = False
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
            args_ga = self.composer.get_default_jvm_args()
            self.assertIn("-Xmx2048m", args_ga)
            self.assertIn("-Xms1536m", args_ga)

    def test_composer_shared_database_mode(self):
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
            "ce_dir": Path("/tmp/proj/client-extensions"),
        }
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "test-project",
            "db_type": "postgresql",
            "database_mode": "shared",
        }
        self.manager.defaults.get.side_effect = lambda _key, default=None: default

        # 1. Build DB service should be None
        db_service = self.composer._build_db_service(meta, "test-project")
        self.assertIsNone(db_service)

        # 2. Build Liferay service should have URL pointing to global DB
        self.composer._build_liferay_service(
            paths, meta, "localhost", "test-project", False, None
        )

        self.assertTrue(self.manager.config.update_portal_ext.called)
        db_call = next(
            (
                call
                for call in self.manager.config.update_portal_ext.call_args_list
                if "jdbc.default.url" in call[0][1]
            ),
            None,
        )
        self.assertIsNotNone(db_call)
        assert db_call is not None
        db_updates = db_call[0][1]
        assert isinstance(db_updates, dict)
        self.assertEqual(
            db_updates["jdbc.default.url"],
            "jdbc:postgresql://liferay-db-global:5432/lportal_test_project",
        )

    def test_composer_db_pool_limits(self):
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
            "ce_dir": Path("/tmp/proj/client-extensions"),
        }
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "test-project",
            "db_type": "postgresql",
        }
        self.manager.defaults.get.side_effect = lambda _key, default=None: default
        self.composer._build_liferay_service(
            paths, meta, "localhost", "test-project", False, None
        )

        self.assertTrue(self.manager.config.update_portal_ext.called)
        db_updates = None
        for call in self.manager.config.update_portal_ext.call_args_list:
            args = call[0]
            if "jdbc.default.url" in args[1]:
                db_updates = args[1]
                break
        self.assertIsNotNone(db_updates)
        assert isinstance(db_updates, dict)
        self.assertEqual(db_updates["jdbc.default.maxActive"], "15")
        self.assertEqual(db_updates["jdbc.default.minIdle"], "2")
        self.assertEqual(db_updates["jdbc.default.maxIdle"], "5")

    def test_composer_db_pool_limits_custom_overrides(self):
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
            "ce_dir": Path("/tmp/proj/client-extensions"),
        }
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "test-project",
            "db_type": "postgresql",
        }
        with patch.object(
            self.manager.defaults,
            "get",
            side_effect=lambda _key, default=None: {
                "db_max_active": "35",
                "db_min_idle": "8",
                "db_max_idle": "12",
            }.get(_key, default),
        ):
            self.composer._build_liferay_service(
                paths, meta, "localhost", "test-project", False, None
            )

        self.assertTrue(self.manager.config.update_portal_ext.called)
        db_updates = None
        for call in self.manager.config.update_portal_ext.call_args_list:
            args = call[0]
            if "jdbc.default.url" in args[1]:
                db_updates = args[1]
                break
        self.assertIsNotNone(db_updates)
        assert isinstance(db_updates, dict)
        self.assertEqual(db_updates["jdbc.default.maxActive"], "35")
        self.assertEqual(db_updates["jdbc.default.minIdle"], "8")
        self.assertEqual(db_updates["jdbc.default.maxIdle"], "12")

    @patch("ldm_core.utils.safe_write_text")
    def test_composer_logging_limits(self, mock_write):
        paths = {
            "root": Path("/tmp/proj"),
            "compose": Path("/tmp/proj/docker-compose.yml"),
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
            "ce_dir": Path("/tmp/proj/client-extensions"),
        }
        meta = {"container_name": "test-project", "db_type": "postgresql"}
        self.manager.defaults.get.side_effect = lambda _key, default=None: default
        self.composer.write_docker_compose(paths, meta)
        self.assertTrue(mock_write.called)

        # Verify the content written contains logging config
        written_content = mock_write.call_args[0][1]
        import yaml

        data = yaml.safe_load(written_content)

        self.assertIn("services", data)
        for svc_conf in data["services"].values():
            self.assertIn("logging", svc_conf)
            self.assertEqual(svc_conf["logging"]["driver"], "json-file")
            self.assertEqual(svc_conf["logging"]["options"]["max-size"], "10m")
            self.assertEqual(svc_conf["logging"]["options"]["max-file"], "3")

    @patch("ldm_core.utils.safe_write_text")
    def test_composer_logging_limits_custom_overrides(self, mock_write):
        paths = {
            "root": Path("/tmp/proj"),
            "compose": Path("/tmp/proj/docker-compose.yml"),
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
            "ce_dir": Path("/tmp/proj/client-extensions"),
        }
        meta = {"container_name": "test-project", "db_type": "postgresql"}
        with patch.object(
            self.manager.defaults,
            "get",
            side_effect=lambda _key, default=None: {
                "log_max_size": "25m",
                "log_max_file": "5",
            }.get(_key, default),
        ):
            self.composer.write_docker_compose(paths, meta)

        written_content = mock_write.call_args[0][1]
        import yaml

        data = yaml.safe_load(written_content)

        for svc_conf in data["services"].values():
            self.assertEqual(svc_conf["logging"]["options"]["max-size"], "25m")
            self.assertEqual(svc_conf["logging"]["options"]["max-file"], "5")


if __name__ == "__main__":
    unittest.main()
