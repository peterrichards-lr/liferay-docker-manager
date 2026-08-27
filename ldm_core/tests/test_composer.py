import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.composer import ComposerService


class MockComposerManager:
    def __init__(self):
        from argparse import Namespace

        self.args = Namespace(
            database_mode=None,
            search_mode=None,
            ssl=None,
            lean=False,
            tunnel_managed_cors=False,
        )
        self.verbose = False
        self.non_interactive = True
        self.workspace = MagicMock()
        self.config = MagicMock()
        self.config.get_global_config.return_value = {}
        self.share = MagicMock()
        self.share.resolve_share_config.return_value = (None, None)
        # LDM-#1077: mirror ShareService's real defaults so composer tests
        # that don't care about custom-domain/self-hosted-override behavior
        # don't have to configure these individually -- tests exercising
        # that behavior can still override any of these per-test.
        self.share.get_known_tunnel_base_domains.return_value = [
            "lfr-demo.online",
            "lfr-demo.se",
        ]
        self.share.get_default_tunnel_domain.return_value = "lfr-demo.online"
        self.share.resolve_tunnel_gateway_url.side_effect = lambda share_domain: (
            "https://tunnel."
            + (
                share_domain
                if share_domain in ("lfr-demo.online", "lfr-demo.se")
                else "lfr-demo.online"
            )
        )
        self.defaults = MagicMock()
        self.get_resolved_ip = MagicMock(return_value="127.0.0.1")
        self.run_command = MagicMock(return_value="")
        self.parse_version = MagicMock(return_value=(2024, 1, 0))


class TestComposerService(unittest.TestCase):
    def setUp(self):
        self.manager = MockComposerManager()
        self.composer = ComposerService(self.manager)
        # Ensure GITHUB_ACTIONS is not "true" during testing so that adaptive tier tests pass
        self.environ_patcher = patch.dict("os.environ", {"GITHUB_ACTIONS": "false"})
        self.environ_patcher.start()
        # Isolate every write_docker_compose() call in this class from
        # whatever persisted default target a *real* ~/.ldmrc on the
        # machine running the tests happens to have -- write_docker_compose
        # now always resolves via resolve_target_context()/get_active_target()
        # when no target_context is passed in, and most of these tests
        # don't care about target resolution at all. Without this, an
        # unmocked call could resolve to a real remote host and attempt a
        # real SSH round trip (via get_remote_project_root) purely to build
        # a compose file in a unit test. Tests that DO care about target
        # resolution (e.g. test_write_docker_compose_uses_persisted_default_target)
        # override this with their own nested patch.
        from ldm_core.config import TargetNode

        self.target_patcher = patch(
            "ldm_core.config.get_active_target",
            return_value=TargetNode(name="local", host="localhost", is_default=True),
        )
        self.target_patcher.start()

    def tearDown(self):
        self.environ_patcher.stop()
        self.target_patcher.stop()

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
        self.assertIn("com.liferay.ldm.project=proj", services["proj-ms1"]["labels"])
        self.assertTrue(
            any(
                v.startswith(f"{Path('/tmp/routes').as_posix()}:/workspace/routes")
                for v in services["proj-ms1"]["volumes"]
            )
        )

    def test_build_extensions_services_non_ssl_port_mapping(self):
        paths = {"root": Path("/tmp"), "cx": Path("/tmp/cx"), "ce_dir": Path("/tmp/ce")}
        meta = {"port_ms1": "8083"}
        self.manager.workspace.scan_client_extensions.return_value = [
            {
                "id": "ms1",
                "deploy": True,
                "is_service": True,
                "path": "/tmp/ms1",
                "ports": [{"port": 8080}],
            }
        ]

        services = self.composer._build_extensions_services(
            paths, meta, "localhost", "proj", False
        )
        self.assertIn("proj-ms1", services)
        self.assertIn("0.0.0.0:8083:8080", services["proj-ms1"]["ports"])

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
        self.assertTrue(
            any(
                v.startswith(f"{Path('/tmp/proj/routes').as_posix()}:/workspace/routes")
                for v in volumes
            )
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

    def test_write_docker_compose_uses_persisted_default_target(self):
        """LDM-#1135: even with no explicit target on manager/meta, a
        persisted default target (set via `ldm target use`) must still be
        consulted -- get_active_target() must never be skipped just
        because target_name starts out falsy (self.manager has no
        `.target` attribute here, and meta has no "target" key -- exactly
        the "no explicit override, rely on the persisted default" case)."""
        paths = {"root": Path("/tmp/proj"), "compose": Path("/tmp/proj/compose.yml")}
        meta = {"container_name": "proj"}

        from ldm_core.config import TargetNode

        remote_target = TargetNode(name="aws-1", host="1.2.3.4", user="ec2-user")

        with (
            patch("ldm_core.handlers.composer.dict_to_yaml", return_value="yaml"),
            patch("ldm_core.utils.safe_write_text"),
            patch(
                "ldm_core.config.get_active_target", return_value=remote_target
            ) as mock_get_active,
            patch(
                "ldm_core.config.get_remote_project_root",
                return_value="/home/ec2-user/.liferay-docker/projects/proj",
            ),
            patch.object(
                self.composer, "_build_liferay_service", return_value={}
            ) as mock_build,
            patch.object(self.composer, "_build_db_service", return_value={}),
            patch.object(self.composer, "_build_search_service", return_value={}),
            patch.object(self.composer, "_build_extensions_services", return_value={}),
        ):
            self.composer.write_docker_compose(paths, meta)

            # get_active_target must have been called even though neither
            # self.manager.target nor meta["target"] provided a value --
            # now routed through resolve_target_context(), which passes
            # config_path through explicitly.
            mock_get_active.assert_called_once_with(None, config_path=None)

            mount_paths_arg = mock_build.call_args[0][6]
            self.assertEqual(
                str(mount_paths_arg["root"]),
                "/home/ec2-user/.liferay-docker/projects/proj",
            )

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
        self.assertEqual(
            db_service["volumes"],
            ["Zukunft-Digital-Database-db-data:/var/lib/postgresql/data"],
        )

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
        self.assertEqual(
            db_service_fb["volumes"],
            ["Zukunft-Digital-db-db-data:/var/lib/postgresql/data"],
        )

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
                with patch("ldm_core.utils.safe_write_text"):
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

    @patch("ldm_core.handlers.composer.UI")
    def test_shared_infra_ux_warning(self, mock_ui):
        """Verify UI.detail is called when shared mode is evaluated for search or db."""
        meta = {
            "database_mode": "shared",
            "search_mode": "shared",
            "tag": "2026.q1.7-lts",
        }
        paths = {
            "root": Path("/tmp/proj"),
            "deploy": Path("/tmp/proj/deploy"),
            "files": Path("/tmp/proj/files"),
            "scripts": Path("/tmp/proj/scripts"),
            "modules": Path("/tmp/proj/osgi/modules"),
            "cx": Path("/tmp/proj/osgi/client-extensions"),
            "portal_log4j": Path("/tmp/proj/osgi/log4j"),
            "data": Path("/tmp/proj/data"),
            "state": Path("/tmp/proj/osgi/state"),
            "logs": Path("/tmp/proj/logs"),
        }
        project_name = "test-project"

        self.composer._build_liferay_service(
            paths, meta, "localhost", project_name, False, []
        )
        mock_ui.detail.assert_any_call("Utilizing Global Shared Infrastructure")

        mock_ui.reset_mock()
        self.composer._build_db_service(meta, project_name)
        mock_ui.detail.assert_any_call("Utilizing Global Shared Infrastructure")

    @patch("ldm_core.handlers.composer.dict_to_yaml")
    @patch("ldm_core.utils.safe_write_text")
    def test_search_kibana_enabled_isolated(self, mock_write, mock_yaml):
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
            "search_kibana_enabled": "true",
            "search_mode": "isolated",
        }

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
            self.assertIn("kibana", compose["services"])
            kibana_service = compose["services"]["kibana"]
            self.assertTrue(kibana_service["image"].startswith("kibana:"))
            self.assertIn(
                "ELASTICSEARCH_HOSTS=http://proj-liferay:9200",
                kibana_service["environment"],
            )
            self.assertIn("5601:5601", kibana_service["ports"])

    @patch("ldm_core.handlers.composer.dict_to_yaml")
    @patch("ldm_core.utils.safe_write_text")
    def test_search_kibana_enabled_shared(self, mock_write, mock_yaml):
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
            "search_kibana_enabled": "true",
            "search_mode": "shared",
        }

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
            self.assertIn("kibana", compose["services"])
            kibana_service = compose["services"]["kibana"]
            self.assertIn(
                "ELASTICSEARCH_HOSTS=http://liferay-search-global:9200",
                kibana_service["environment"],
            )

    @patch("ldm_core.handlers.composer.dict_to_yaml")
    @patch("ldm_core.utils.safe_write_text")
    def test_search_kibana_disabled(self, mock_write, mock_yaml):
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
            "search_kibana_enabled": "false",
        }

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
            self.assertNotIn("kibana", compose["services"])

    def test_custom_env_dict_and_string_support(self):
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

        # Test Case 1: dictionary value
        meta_dict = {
            "tag": "2026.q1.7-lts",
            "container_name": "proj",
            "custom_env": {"MY_VAR": "dict_val", "OTHER": "xyz"},
            "db_type": "postgresql",
        }
        with patch.object(
            self.composer,
            "_inject_liferay_db_env",
            return_value=("postgresql", "isolated"),
        ):
            service = self.composer._build_liferay_service(
                paths, meta_dict, "proj.local", "proj", False, []
            )
        self.assertIn("MY_VAR=dict_val", service["environment"])
        self.assertIn("OTHER=xyz", service["environment"])

        # Test Case 2: string fallback
        meta_str = {
            "tag": "2026.q1.7-lts",
            "container_name": "proj",
            "custom_env": "MY_VAR=str_val,OTHER=abc",
            "db_type": "postgresql",
        }
        with patch.object(
            self.composer,
            "_inject_liferay_db_env",
            return_value=("postgresql", "isolated"),
        ):
            service_str = self.composer._build_liferay_service(
                paths, meta_str, "proj.local", "proj", False, []
            )
        self.assertIn("MY_VAR=str_val", service_str["environment"])
        self.assertIn("OTHER=abc", service_str["environment"])

    def test_rafa_project_custom_containers(self):
        import yaml

        self.manager.defaults.get.side_effect = lambda _key, default=None: default
        self.manager.args = MagicMock()
        self.manager.args.verbose = False
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
        config = {
            "container_name": "ldm-rafa-project",
            "tag": "2025.q1.0-lts",
            "db_type": "postgresql",
            "host_name": "rafa-project.localhost",
            "ssl": True,
            "search_kibana_enabled": True,
            "custom_containers": [
                {
                    "service_name": "wp-db",
                    "image": "mysql:8.0",
                    "environment": [
                        "MYSQL_ROOT_PASSWORD=wordpress_root_password",
                        "MYSQL_DATABASE=wordpress",
                        "MYSQL_USER=wordpress_user",
                        "MYSQL_PASSWORD=wordpress_password",
                    ],
                    "volumes": ["wp_db_data:/var/lib/mysql"],
                },
                {
                    "service_name": "wordpress",
                    "image": "wordpress:latest",
                    "depends_on": ["wp-db"],
                    "ports": ["8090:80"],
                    "environment": [
                        "WORDPRESS_DB_HOST=wp-db:3306",
                        "WORDPRESS_DB_USER=wordpress_user",
                        "WORDPRESS_DB_PASSWORD=wordpress_password",
                        "WORDPRESS_DB_NAME=wordpress",
                    ],
                    "subdomain": "wordpress",
                    "volumes": ["wp_data:/var/www/html"],
                },
                {
                    "service_name": "cx-spring-boot",
                    "image": "liferay-cx-crawler:latest",
                    "depends_on": ["wordpress"],
                    "ports": ["58081:58081"],
                    "environment": [
                        "COM_LIFERAY_LXC_DXP_DOMAINS=liferay:8080",
                        "COM_LIFERAY_LXC_DXP_MAINDOMAIN=liferay:8080",
                        "ELASTICSEARCH_HOSTS=http://liferay-search-global:9200",
                    ],
                },
            ],
        }

        with patch("ldm_core.utils.safe_write_text") as mock_write:
            self.composer.write_docker_compose(paths, config)
            compose_data = yaml.safe_load(mock_write.call_args[0][1])
            services = compose_data["services"]

            # 1. Assert namespaced DB service and dependency mapping
            self.assertIn("ldm-rafa-project-db", services)
            self.assertEqual(services["ldm-rafa-project-db"]["image"], "postgres:13.10")
            self.assertIn("ldm-rafa-project-db", services["liferay"]["depends_on"])

            # 2. Assert Liferay JDBC connection URL points directly to namespaced DB host in portal-ext.properties
            db_call = next(
                call
                for call in self.manager.config.update_portal_ext.call_args_list
                if "jdbc.default.url" in call[0][1]
            )
            db_updates = db_call[0][1]
            self.assertEqual(
                db_updates["jdbc.default.url"],
                "jdbc:postgresql://ldm-rafa-project-db:5432/lportal",
            )

            # 3. Assert Custom Containers are successfully generated
            self.assertIn("wp-db", services)
            self.assertEqual(services["wp-db"]["image"], "mysql:8.0")

            self.assertIn("wordpress", services)
            self.assertEqual(services["wordpress"]["image"], "wordpress:latest")
            self.assertEqual(services["wordpress"]["ports"], ["8090:80"])
            self.assertIn("wp-db", services["wordpress"]["depends_on"])

            self.assertIn("cx-spring-boot", services)
            self.assertEqual(
                services["cx-spring-boot"]["image"], "liferay-cx-crawler:latest"
            )
            self.assertEqual(services["cx-spring-boot"]["ports"], ["58081:58081"])
            self.assertIn("wordpress", services["cx-spring-boot"]["depends_on"])

            # 4. Assert Kibana is enabled
            self.assertIn("kibana", services)
            self.assertEqual(services["kibana"]["image"], "kibana:7.17.24")


if __name__ == "__main__":
    unittest.main()


class TestVolumeOwnershipLabels(unittest.TestCase):
    """LDM-#1267: named volumes must carry ownership labels.

    Without them a volume is anonymous as to origin, so nothing can tell an
    abandoned LDM volume from a third-party one -- which is why `ldm prune`
    had no safe way to reclaim them and fell back to a no-op (#1266).
    """

    def test_role_classifies_by_destructiveness(self):
        from ldm_core.handlers.composer import _volume_role

        self.assertEqual("data", _volume_role("proj-data"))
        self.assertEqual("state", _volume_role("proj-state"))
        # `<project>-db-db-data` ends in -data and must classify as the
        # destructive role, not fall through to something sweepable.
        self.assertEqual("data", _volume_role("proj-db-db-data"))

    def test_unrecognised_volume_is_not_treated_as_disposable(self):
        """A new suffix must default to the safe side, never to 'state'."""
        from ldm_core.handlers.composer import _volume_role

        for name in ("proj-logs", "proj-cache", "something-else", ""):
            self.assertEqual(
                "unknown",
                _volume_role(name),
                f"{name!r} must not be classified as disposable by omission",
            )

    def test_volume_definition_carries_ownership_labels(self):
        from ldm_core.handlers.composer import _named_volume_definition

        spec = _named_volume_definition("proj-data", "proj")

        # LDM-424: explicit name must survive alongside the new labels.
        self.assertEqual("proj-data", spec["name"])
        self.assertEqual(
            {
                "com.liferay.ldm.project": "proj",
                "com.liferay.ldm.managed": "true",
                "com.liferay.ldm.role": "data",
            },
            spec["labels"],
        )

    def test_volume_definition_matches_service_label_convention(self):
        """Volume labels must use the same keys services already get."""
        from ldm_core.handlers.composer import _named_volume_definition

        labels = _named_volume_definition("proj-state", "proj")["labels"]
        self.assertEqual("proj", labels["com.liferay.ldm.project"])
        self.assertEqual("true", labels["com.liferay.ldm.managed"])
        self.assertEqual("state", labels["com.liferay.ldm.role"])


class TestSharedDatabaseNameCasing(unittest.TestCase):
    """LDM-#1354: a capitalised project must get a lowercase shared database.

    PostgreSQL folds an unquoted `CREATE DATABASE`, so a mixed-case JDBC URL
    named a database that could not exist -- `FATAL: database
    "lportal_MyProject" does not exist` -- while the existence check compared a
    quoted literal that never matched, making every run re-attempt the create
    and the second run die.
    """

    PATHS_KEYS = (
        "deploy",
        "files",
        "data",
        "configs",
        "modules",
        "cx",
        "scripts",
        "state",
        "logs",
        "portal_log4j",
        "ce_dir",
    )

    def setUp(self):
        self.manager = MockComposerManager()
        from ldm_core.handlers.composer import ComposerService

        self.composer = ComposerService(self.manager)
        self.manager.defaults.get.side_effect = lambda _key, default=None: default

    def _paths(self):
        root = Path("/tmp/MyProject")
        paths = {"root": root}
        paths.update({k: root / k for k in self.PATHS_KEYS})
        return paths

    def _jdbc_url(self, db_type, project_name="MyProject"):
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": project_name,
            "db_type": db_type,
            "database_mode": "shared",
        }
        self.composer._build_liferay_service(
            self._paths(), meta, "localhost", project_name, False, None
        )
        for call in self.manager.config.update_portal_ext.call_args_list:
            args = call[0]
            if (
                len(args) > 1
                and isinstance(args[1], dict)
                and "jdbc.default.url" in args[1]
            ):
                return args[1]["jdbc.default.url"]
        self.fail("no jdbc.default.url was written")
        return ""

    def test_postgresql_shared_url_is_lowercase(self):
        url = self._jdbc_url("postgresql")
        self.assertIn("/lportal_myproject", url)
        self.assertNotIn("MyProject", url)

    def test_mariadb_shared_url_is_lowercase(self):
        url = self._jdbc_url("mysql")
        self.assertIn("/lportal_myproject", url)
        self.assertNotIn("MyProject", url)

    def test_a_transcoded_name_is_lowercased_in_the_url(self):
        url = self._jdbc_url("postgresql", project_name="Saarbrücken")
        self.assertIn("/lportal_saarbruecken", url)

    def test_isolated_mode_still_uses_the_constant(self):
        """The project name must not leak into the isolated database name."""
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "MyProject",
            "db_type": "postgresql",
        }
        self.composer._build_liferay_service(
            self._paths(), meta, "localhost", "MyProject", False, None
        )
        url = None
        for call in self.manager.config.update_portal_ext.call_args_list:
            args = call[0]
            if (
                len(args) > 1
                and isinstance(args[1], dict)
                and "jdbc.default.url" in args[1]
            ):
                url = args[1]["jdbc.default.url"]
                break
        self.assertIsNotNone(url)
        assert url is not None
        self.assertIn("/lportal", url)
        self.assertNotIn("lportal_", url)


class TestSharedDbModeFromCliFlag(unittest.TestCase):
    """LDM-#1359: the CLI flag path, which no test covered.

    Existing tests set `database_mode` in **meta**, which both call sites read,
    so they passed while the flag was broken. The flag lands in **args**, and
    only `_build_db_service` consulted it -- `_inject_liferay_db_env` did not.
    The two then disagreed within one run: no database service was emitted, yet
    `depends_on: <project>-db` remained and the isolated JDBC URL was written,
    so `docker compose config` rejected the file for every project.
    """

    PATHS_KEYS = (
        "deploy",
        "files",
        "data",
        "configs",
        "modules",
        "cx",
        "scripts",
        "state",
        "logs",
        "portal_log4j",
        "ce_dir",
    )

    def setUp(self):
        self.manager = MockComposerManager()
        from ldm_core.handlers.composer import ComposerService

        self.composer = ComposerService(self.manager)
        self.manager.defaults.get.side_effect = lambda _key, default=None: default
        # The flag, on args only -- deliberately NOT in meta.
        self.manager.args.database_mode = "shared"

    def _paths(self):
        root = Path("/tmp/MixedCase")
        paths = {"root": root}
        paths.update({k: root / k for k in self.PATHS_KEYS})
        return paths

    def _meta(self):
        return {
            "tag": "2026.q1.7-lts",
            "container_name": "MixedCase",
            "db_type": "postgresql",
        }

    def test_no_service_is_depended_on_that_does_not_exist(self):
        """The #1359 failure exactly: 'depends on undefined service'."""
        service = self.composer._build_liferay_service(
            self._paths(), self._meta(), "localhost", "MixedCase", False, None
        )
        db_service = self.composer._build_db_service(self._meta(), "MixedCase")

        defined = {"liferay"}
        if db_service:
            defined.add("MixedCase-db")

        for dep in service.get("depends_on") or []:
            self.assertIn(
                dep,
                defined,
                f"liferay depends on undefined service {dep!r} -- compose will refuse the file",
            )

    def test_the_jdbc_url_is_the_shared_one(self):
        """The flag must reach the URL, not just the service list."""
        self.composer._build_liferay_service(
            self._paths(), self._meta(), "localhost", "MixedCase", False, None
        )
        url = None
        for call in self.manager.config.update_portal_ext.call_args_list:
            args = call[0]
            if (
                len(args) > 1
                and isinstance(args[1], dict)
                and "jdbc.default.url" in args[1]
            ):
                url = args[1]["jdbc.default.url"]
                break
        self.assertIsNotNone(url)
        assert url is not None
        self.assertIn("liferay-db-global", url)
        self.assertIn("lportal_mixedcase", url)
        self.assertNotIn("MixedCase-db", url)

    def test_meta_still_wins_when_args_are_absent(self):
        """The pre-existing meta path must keep working."""
        self.manager.args.database_mode = None
        meta = self._meta()
        meta["database_mode"] = "shared"
        self.assertIsNone(self.composer._build_db_service(meta, "MixedCase"))

    def test_isolated_still_emits_the_database_service_and_its_dependency(self):
        """Guard against over-reach: the isolated path must be untouched."""
        self.manager.args.database_mode = "isolated"
        service = self.composer._build_liferay_service(
            self._paths(), self._meta(), "localhost", "MixedCase", False, None
        )
        self.assertIsNotNone(self.composer._build_db_service(self._meta(), "MixedCase"))
        self.assertIn("MixedCase-db", service.get("depends_on") or [])


class TestSharedDbModeWithMysql(unittest.TestCase):
    """LDM-#1361: shared mode with MySQL/MariaDB.

    Between #1360 and #1361 this combination was refused outright, because
    `_inject_liferay_db_env` emitted `jdbc:mariadb://liferay-db-global:3306/`
    -- the MySQL port of a PostgreSQL container -- so it could never connect
    (#1357). The engine now resolves its own global container.
    """

    PATHS_KEYS = TestSharedDbModeFromCliFlag.PATHS_KEYS

    def setUp(self):
        self.manager = MockComposerManager()
        from ldm_core.handlers.composer import ComposerService

        self.composer = ComposerService(self.manager)
        self.manager.defaults.get.side_effect = lambda _key, default=None: default
        self.manager.args.database_mode = "shared"

    def _paths(self):
        root = Path("/tmp/MixedCase")
        paths = {"root": root}
        paths.update({k: root / k for k in self.PATHS_KEYS})
        return paths

    def _meta(self, db_type="mysql"):
        return {
            "tag": "2026.q1.7-lts",
            "container_name": "MixedCase",
            "db_type": db_type,
        }

    def _url(self, db_type="mysql"):
        self.composer._build_liferay_service(
            self._paths(), self._meta(db_type), "localhost", "MixedCase", False, None
        )
        for call in self.manager.config.update_portal_ext.call_args_list:
            args = call[0]
            if (
                len(args) > 1
                and isinstance(args[1], dict)
                and "jdbc.default.url" in args[1]
            ):
                return args[1]["jdbc.default.url"]
        return None

    def test_the_url_targets_the_mysql_global_not_the_postgres_one(self):
        """The #1357 defect, asserted directly."""
        url = self._url("mysql")
        self.assertIsNotNone(url)
        assert url is not None
        self.assertIn("liferay-db-mysql-global:3306", url)
        self.assertNotIn("liferay-db-global:", url)

    def test_mariadb_resolves_the_same_global(self):
        url = self._url("mariadb")
        self.assertIsNotNone(url)
        assert url is not None
        self.assertIn("liferay-db-mysql-global:3306", url)

    def test_the_derived_database_name_is_lowercase(self):
        """MySQL is case-sensitive on Linux, so #1354's contract matters here too."""
        url = self._url("mysql")
        assert url is not None
        db_part = url.split("/")[-1].split("?")[0]
        self.assertEqual("lportal_mixedcase", db_part)

    def test_no_database_service_is_emitted_and_nothing_depends_on_one(self):
        """The #1359 signature: compose refuses a dangling depends_on."""
        meta = self._meta("mysql")
        self.assertIsNone(self.composer._build_db_service(meta, "MixedCase"))
        service = self.composer._build_liferay_service(
            self._paths(), meta, "localhost", "MixedCase", False, None
        )
        for dep in service.get("depends_on") or []:
            self.assertEqual("liferay", dep)

    def test_isolated_mysql_is_untouched(self):
        """Guard against over-reach: isolated MySQL already worked."""
        self.manager.args.database_mode = "isolated"
        url = self._url("mysql")
        assert url is not None
        self.assertIn("MixedCase-db:3306/lportal", url)
        self.assertNotIn("global", url)


class TestOsgiConfigsMount(unittest.TestCase):
    """LDM-#1364: <project>/osgi/configs must reach /opt/liferay/osgi/configs.

    `df59dea6` ("isolate configuration volumes", v2.7.2) removed both this and
    the osgi/modules mount; `57fd4b9f` restored modules and this was
    overlooked. Nothing a user placed in `osgi/configs` reached Liferay from
    v2.7.2 onwards -- invisible because seven code paths still read the
    directory and branch on it, including `run.py`'s "Custom Elasticsearch OSGi
    configs detected" message.
    """

    PATHS_KEYS = (
        "deploy",
        "files",
        "data",
        "configs",
        "modules",
        "cx",
        "scripts",
        "state",
        "logs",
        "portal_log4j",
        "ce_dir",
        "routes",
    )

    def setUp(self):
        self.manager = MockComposerManager()
        from ldm_core.handlers.composer import ComposerService

        self.composer = ComposerService(self.manager)
        self.manager.defaults.get.side_effect = lambda _key, default=None: default

    def _volumes(self):
        root = Path("/tmp/proj")
        paths = {"root": root}
        paths.update({k: root / k for k in self.PATHS_KEYS})
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "proj",
            "db_type": "postgresql",
        }
        service = self.composer._build_liferay_service(
            paths, meta, "localhost", "proj", False, None
        )
        return service.get("volumes") or []

    def test_osgi_configs_is_mounted(self):
        targets = [v.split(":")[1] for v in self._volumes() if ":" in v]
        self.assertIn(
            "/opt/liferay/osgi/configs",
            targets,
            "osgi/configs is not mounted, so OSGi .config files never reach Liferay",
        )

    def test_it_maps_the_projects_own_configs_directory(self):
        mount = next(
            v for v in self._volumes() if v.split(":")[1] == "/opt/liferay/osgi/configs"
        )
        self.assertTrue(
            mount.startswith("/tmp/proj/configs:"),
            f"osgi/configs maps from {mount.split(':')[0]!r}, expected the project's configs path",
        )

    def test_the_sibling_osgi_mounts_are_still_present(self):
        """Guard: the same commit removed modules too. Do not lose one again."""
        targets = [v.split(":")[1] for v in self._volumes() if ":" in v]
        for expected in (
            "/opt/liferay/osgi/modules",
            "/opt/liferay/osgi/client-extensions",
            "/mnt/liferay/files",
        ):
            self.assertIn(expected, targets)


class TestSharedSearchOsgiConfig(unittest.TestCase):
    """LDM-#1353: shared search is configured by an OSGi .config, not env vars.

    The `LIFERAY_ELASTICSEARCH*` variables LDM emits do not reach Liferay:
    `indexNamePrefix`, `productionModeEnabled` and the sidecar toggle are OSGi
    configuration on
    `com.liferay.portal.search.elasticsearch{N}.configuration.ElasticsearchConfiguration`,
    while `LIFERAY_*` maps to portal properties. Measured on a running project:
    env vars alone left the global cluster empty for 360s and Liferay started
    its embedded sidecar; the same values in a .config produced indices
    immediately with no sidecar.
    """

    PATHS_KEYS = (
        "deploy",
        "files",
        "data",
        "configs",
        "modules",
        "cx",
        "scripts",
        "state",
        "logs",
        "portal_log4j",
        "ce_dir",
        "routes",
    )

    def setUp(self):
        self.manager = MockComposerManager()
        from ldm_core.handlers.composer import ComposerService

        self.composer = ComposerService(self.manager)
        self.manager.defaults.get.side_effect = lambda _key, default=None: default
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "proj"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _paths(self):
        paths = {"root": self.root}
        paths.update({k: self.root / k for k in self.PATHS_KEYS})
        return paths

    def _build(self, project_name="MixedCase", tag="2026.q1.12-lts"):
        meta = {
            "tag": tag,
            "container_name": project_name,
            "db_type": "postgresql",
            "use_shared_search": "true",
        }
        self.composer._build_liferay_service(
            self._paths(), meta, "localhost", project_name, False, None
        )
        return self.root / "configs"

    def test_the_config_is_written_for_shared_search(self):
        configs = self._build()
        written = list(configs.glob("*.config"))
        self.assertEqual(1, len(written), f"expected one .config, got {written}")
        self.assertIn("elasticsearch8", written[0].name)

    def test_it_carries_the_cluster_and_prefix(self):
        configs = self._build()
        body = next(configs.glob("*.config")).read_text()
        self.assertIn('productionModeEnabled=B"true"', body)
        self.assertIn(
            'networkHostAddresses=["http://liferay-search-global:9200"]', body
        )
        # Lowercased at source by `search_index_prefix` -- Liferay lowercases it
        # anyway, and writing it lowercase keeps what LDM records identical to
        # what Liferay uses.
        self.assertIn('indexNamePrefix="ldm-mixedcase-"', body)

    def test_an_older_tag_gets_the_es7_pid(self):
        """The filename is the PID; ES7 and ES8 are different services.

        `parse_version` is pinned explicitly: the mock manager's default returns
        a MagicMock, which compares truthy against the (2024, 1, 0) threshold
        and would make this assert the mock rather than the branch.
        """
        self.manager.parse_version = MagicMock(return_value=(7, 4, 13))
        configs = self._build(tag="7.4.13-u108")
        self.assertIn("elasticsearch7", next(configs.glob("*.config")).name)

    def test_nothing_is_written_when_search_is_not_shared(self):
        """Guard against over-reach -- and against clobbering a user's own
        config on the `--search-mode remote` path, where that file IS the
        mechanism."""
        meta = {
            "tag": "2026.q1.12-lts",
            "container_name": "proj",
            "db_type": "postgresql",
            "use_shared_search": "false",
        }
        self.composer._build_liferay_service(
            self._paths(), meta, "localhost", "proj", False, None
        )
        configs = self.root / "configs"
        self.assertEqual([], list(configs.glob("*.config")) if configs.exists() else [])
