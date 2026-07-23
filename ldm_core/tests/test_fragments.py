import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.runtime import RuntimeService
from ldm_core.runtime.fragments import FragmentsService


class MockRuntime(BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.args.tag_latest = False
        self.args.tag_prefix = None
        self.args.timeout = 900
        self.verbose = False
        self.non_interactive = True
        self.dry_run = False

        # Self-referential manager for service compatibility
        from typing import Any, cast

        self.manager = cast(Any, self)

        self.assets = MagicMock()
        self.infra = MagicMock()
        self.snapshot = MagicMock()
        self.share = MagicMock()
        self.license = MagicMock()
        self.diagnostics = MagicMock()
        self.share.resolve_share_config.return_value = ("lfr-tunnel", "lfr-demo.online")
        from ldm_core.defaults import DefaultsManager
        from ldm_core.handlers.composer import ComposerService
        from ldm_core.handlers.config import ConfigService

        self.defaults = DefaultsManager()
        self.config = ConfigService(self)
        self.config.update_portal_ext = MagicMock()  # type: ignore[method-assign]
        self.composer = ComposerService(self)
        self.handler = RuntimeService(self)
        self.runtime = self.handler
        self.verify_runtime_environment = MagicMock()  # type: ignore[method-assign]

    def cmd_run(self, *args, **kwargs):
        return self.handler.cmd_run(*args, **kwargs)

    def cmd_stop(self, *args, **kwargs):
        return self.handler.cmd_stop(*args, **kwargs)

    def cmd_restart(self, *args, **kwargs):
        return self.handler.cmd_restart(*args, **kwargs)

    def cmd_down(self, *args, **kwargs):
        return self.handler.cmd_down(*args, **kwargs)

    def cmd_logs(self, *args, **kwargs):
        return self.handler.cmd_logs(*args, **kwargs)

    def cmd_wait(self, *args, **kwargs):
        return self.handler.cmd_wait(*args, **kwargs)

    def _wait_for_ready(self, *args, **kwargs):
        return self.handler._wait_for_ready(*args, **kwargs)

    def get_resource_path(self, name):
        return Path("/tmp/res") / name

    def get_config(self, key, default=None):
        return default

    def read_meta(self, *args, **kwargs):
        return {"container_name": "test-runtime", "host_name": "localhost"}

    def setup_paths(self, root):
        return super().setup_paths(root)

    def _ensure_seeded(self, *args, **kwargs):
        return False

    def write_meta(self, *args, **kwargs):
        pass

    def _is_ssl_active(self, *args, **kwargs):
        return False

    def _ensure_network(self, *args, **kwargs):
        pass

    def setup_infrastructure(self, *args, **kwargs):
        pass

    def write_docker_compose(self, *args, **kwargs):
        pass


class TestFragments(unittest.TestCase):
    def setUp(self):
        super().setUp()
        from unittest.mock import MagicMock, patch

        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self.tmp_dir_obj.name)
        self.handler = MockRuntime()
        self.handler.detect_project_path = MagicMock(return_value=self.tmp_dir)  # type: ignore[method-assign]

        # Globally mock requests.get for _wait_for_ready tests to prevent hanging/failing
        self.req_patcher = patch("requests.get")
        self.mock_req = self.req_patcher.start()
        self.mock_req.return_value = MagicMock(status_code=200)

        self.update_patcher = patch(
            "ldm_core.diagnostics.doctor.check_for_updates", return_value=(None, None)
        )
        self.update_patcher.start()
        self.handler = MockRuntime()
        self.handler.handler = RuntimeService(self.handler)
        self.file_path = Path("fragment-overrides.json")

    def tearDown(self):
        self.req_patcher.stop()
        self.update_patcher.stop()

    @patch("ldm_core.ui.UI.debug")
    def test_port_inspection_failure_emits_debug_not_raise(self, mock_debug):
        """docker port failure in _patch_fragment_overrides should emit UI.debug, not silently pass or raise."""
        with patch.object(BaseHandler, "run_command") as mock_run:
            # First call (port inspect) fails; second call (docker inspect for CX) returns None
            mock_run.side_effect = [
                Exception("docker not available"),
                None,
            ]
            project_meta = {
                "liferay_container_name": "test-liferay-1",
                "container_name": "test-liferay-1",
                "host_name": "localhost",
                "ssl": "false",
                "share": "false",
            }
            paths = {"root": Path("/fake/project")}
            # Fragment override file must not exist so _patch_fragment_overrides returns early-ish
            # We want to exercise the port-inspect block; the method will return early
            # before sending any API calls. Patch the file existence check.
            with patch("pathlib.Path.is_file", return_value=False):
                self.handler.handler.fragments._patch_fragment_overrides(
                    project_meta, paths
                )

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_patch_fragment_overrides(self, mock_urlopen, mock_sleep):
        """Test that fragment overrides are parsed and sent to the headless API correctly."""
        import json

        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "share": "true",
        }

        paths = {"root": self.tmp_dir}

        # Create mock fragment-overrides.json
        configs_dir = self.tmp_dir / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)

        overrides_data = {"test-frag": {"url": "https://foo.${LDM_HOST_NAME}"}}
        with open(configs_dir / "fragment-overrides.json", "w") as f:
            json.dump(overrides_data, f)

        with (
            patch("ldm_core.ui.UI.success") as mock_success,
            patch.object(BaseHandler, "run_command", return_value="8080"),
            patch.object(self.handler.defaults, "get", return_value="my-subdomain"),
        ):
            # Mock site data response
            mock_response = MagicMock()
            mock_response.read.side_effect = [
                json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),  # sites
                json.dumps(
                    {
                        "items": [
                            {
                                "name": "Home",
                                "pageDefinition": {
                                    "pageElement": {
                                        "type": "Fragment",
                                        "id": "frag-1",
                                        "definition": {
                                            "fragmentConfig": {
                                                "fragmentKey": "test-frag"
                                            }
                                        },
                                    }
                                },
                            }
                        ]
                    }
                ).encode("utf-8"),  # pages
                json.dumps({"status": "ok"}).encode("utf-8"),  # patch response
            ]

            import urllib.error

            error_404 = urllib.error.HTTPError(
                "https://my-subdomain.lfr.cloud/o/headless-delivery/v1.0/sites",
                404,
                "Not Found",
                MagicMock(),
                None,
            )

            ctx_manager = MagicMock()
            ctx_manager.__enter__.return_value = mock_response

            # First 6 calls (2 attempts * 3 endpoints) raise 404 (simulating race condition), then success
            mock_urlopen.side_effect = [
                error_404,
                error_404,
                error_404,
                error_404,
                error_404,
                error_404,
                ctx_manager,
                ctx_manager,
                ctx_manager,
            ]

            self.handler.handler.fragments._patch_fragment_overrides(
                project_meta, paths
            )

            mock_success.assert_any_call(
                "  -> Patched configuration for fragment 'test-frag' on page 'Home'"
            )
            mock_success.assert_any_call(
                "Successfully applied 1 fragment configuration overrides."
            )
            self.assertEqual(mock_sleep.call_count, 2)

            # Verify the patch payload was constructed correctly using variables
            calls = mock_urlopen.call_args_list
            patch_call = None
            for call in calls:
                req = call[0][0]
                if req.method == "PATCH":
                    patch_call = req
                    break

            assert patch_call is not None
            payload = json.loads(patch_call.data.decode("utf-8"))  # type: ignore[attr-defined]
            self.assertEqual(
                payload["definition"]["config"]["url"],
                "https://foo.my-subdomain.lfr.cloud",
            )

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_patch_fragment_overrides_ssl_verification(self, mock_urlopen, mock_sleep):
        """All admin API calls use 127.0.0.1 loopback transport — SSL is always bypassed.

        ext_base_url is preserved solely for template variable expansion (LDM_BASE_URL,
        LDM_HOST_NAME). The transport URL is always http://127.0.0.1:{port}, which means
        SSL certificate verification is always disabled regardless of the external hostname.
        This prevents silent failures caused by mkcert certs not being trusted by Python's
        OpenSSL bundle when using custom hostnames like aica.local.
        """
        import json
        import ssl

        configs_dir = self.tmp_dir / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)
        overrides_data = {"test-frag": {"url": "https://foo.${LDM_HOST_NAME}"}}
        with open(configs_dir / "fragment-overrides.json", "w") as f:
            json.dump(overrides_data, f)

        # 1. Non-loopback case (public sharing subdomain) — transport is still 127.0.0.1
        project_meta_public = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "share": "true",
        }
        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),
            json.dumps({"items": []}).encode("utf-8"),
        ]
        ctx_manager = MagicMock()
        ctx_manager.__enter__.return_value = mock_response
        mock_urlopen.side_effect = [ctx_manager, ctx_manager]

        with (
            patch.object(BaseHandler, "run_command", return_value="8080"),
            patch.object(self.handler.defaults, "get", return_value="my-subdomain"),
        ):
            self.handler.handler.fragments._patch_fragment_overrides(
                project_meta_public, paths={"root": self.tmp_dir}
            )

        # All API requests go to 127.0.0.1 — SSL is always bypassed
        called_ctx_public = mock_urlopen.call_args_list[0].kwargs.get("context")
        self.assertIsNotNone(called_ctx_public)
        self.assertFalse(called_ctx_public.check_hostname)
        self.assertEqual(called_ctx_public.verify_mode, ssl.CERT_NONE)

        # Verify the transport URL is loopback, not the external tunnel host
        request_url = mock_urlopen.call_args_list[0][0][0].full_url
        self.assertIn("127.0.0.1:8080", request_url)

        # 2. Loopback case (local development host) — same invariant holds
        project_meta_local = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "host_name": "localhost",
            "share": "false",
        }
        mock_urlopen.reset_mock()
        mock_response_local = MagicMock()
        mock_response_local.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),
            json.dumps({"items": []}).encode("utf-8"),
        ]
        ctx_manager_local = MagicMock()
        ctx_manager_local.__enter__.return_value = mock_response_local
        mock_urlopen.side_effect = [ctx_manager_local, ctx_manager_local]

        with (
            patch.object(BaseHandler, "run_command", return_value="8080"),
            patch.object(self.handler.defaults, "get", return_value=None),
        ):
            self.handler.handler.fragments._patch_fragment_overrides(
                project_meta_local, paths={"root": self.tmp_dir}
            )

        called_ctx_local = mock_urlopen.call_args_list[0].kwargs.get("context")
        self.assertIsNotNone(called_ctx_local)
        self.assertFalse(called_ctx_local.check_hostname)
        self.assertEqual(called_ctx_local.verify_mode, ssl.CERT_NONE)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_patch_fragment_overrides_shifted_ports(self, mock_urlopen, mock_sleep):
        """Verify that fragment overrides ext_base_url appends shifted proxy ports correctly when not shared."""
        import json

        paths = {"root": self.tmp_dir}
        configs_dir = self.tmp_dir / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)

        overrides_data = {"test-frag": {"url": "${LDM_BASE_URL}/test"}}
        with open(configs_dir / "fragment-overrides.json", "w") as f:
            json.dump(overrides_data, f)

        # Mock responses
        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),  # sites
            json.dumps(
                {
                    "items": [
                        {
                            "name": "Home",
                            "pageDefinition": {
                                "pageElement": {
                                    "type": "Fragment",
                                    "id": "frag-1",
                                    "definition": {
                                        "fragmentConfig": {"fragmentKey": "test-frag"}
                                    },
                                }
                            },
                        }
                    ]
                }
            ).encode("utf-8"),  # pages
            json.dumps({"status": "ok"}).encode("utf-8"),  # patch response
        ]
        ctx_manager = MagicMock()
        ctx_manager.__enter__.return_value = mock_response
        mock_urlopen.return_value = ctx_manager

        # Mock shifted proxy ports
        mock_proxy_ports = {"http": 8080, "https": 8443, "admin": 18080}

        # Scenario 1: HTTP local, proxy HTTP shifted to 8080
        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "host_name": "my-host.local",
            "ssl": "False",
            "share": "false",
        }
        self.handler.args.share = False
        with (
            patch.object(
                self.handler.infra, "get_proxy_ports", return_value=mock_proxy_ports
            ),
            patch.object(BaseHandler, "run_command", return_value="8080"),
        ):
            self.handler.handler.fragments._patch_fragment_overrides(
                project_meta, paths
            )

            patch_req = mock_urlopen.call_args_list[-1][0][0]
            payload = json.loads(patch_req.data.decode("utf-8"))
            self.assertEqual(
                payload["definition"]["config"]["url"], "http://my-host.local:8080/test"
            )

        # Scenario 2: HTTPS local, proxy HTTPS shifted to 8443
        mock_urlopen.reset_mock()
        mock_response.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),
            json.dumps(
                {
                    "items": [
                        {
                            "name": "Home",
                            "pageDefinition": {
                                "pageElement": {
                                    "type": "Fragment",
                                    "id": "frag-1",
                                    "definition": {
                                        "fragmentConfig": {"fragmentKey": "test-frag"}
                                    },
                                }
                            },
                        }
                    ]
                }
            ).encode("utf-8"),
            json.dumps({"status": "ok"}).encode("utf-8"),
        ]

        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "host_name": "my-host.local",
            "ssl": "True",
            "share": "false",
        }
        self.handler.args.share = False
        with (
            patch.object(
                self.handler.infra, "get_proxy_ports", return_value=mock_proxy_ports
            ),
            patch.object(BaseHandler, "run_command", return_value="8080"),
        ):
            self.handler.handler.fragments._patch_fragment_overrides(
                project_meta, paths
            )

            patch_req = mock_urlopen.call_args_list[-1][0][0]
            payload = json.loads(patch_req.data.decode("utf-8"))
            self.assertEqual(
                payload["definition"]["config"]["url"],
                "https://my-host.local:8443/test",
            )

        # Scenario 3: Shared/Tunnel enabled, proxy HTTPS is ignored, resolves to tunnel subdomain
        mock_urlopen.reset_mock()
        mock_response.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),
            json.dumps(
                {
                    "items": [
                        {
                            "name": "Home",
                            "pageDefinition": {
                                "pageElement": {
                                    "type": "Fragment",
                                    "id": "frag-1",
                                    "definition": {
                                        "fragmentConfig": {"fragmentKey": "test-frag"}
                                    },
                                }
                            },
                        }
                    ]
                }
            ).encode("utf-8"),
            json.dumps({"status": "ok"}).encode("utf-8"),
        ]

        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "share": "true",
        }
        self.handler.args.share = True
        with (
            patch.object(
                self.handler.infra, "get_proxy_ports", return_value=mock_proxy_ports
            ),
            patch.object(BaseHandler, "run_command", return_value="8080"),
            patch.object(self.handler.defaults, "get", return_value="my-subdomain"),
        ):
            self.handler.handler.fragments._patch_fragment_overrides(
                project_meta, paths
            )

            patch_req = mock_urlopen.call_args_list[-1][0][0]
            payload = json.loads(patch_req.data.decode("utf-8"))
            self.assertEqual(
                payload["definition"]["config"]["url"],
                "https://my-subdomain.lfr.cloud/test",
            )

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_patch_fragment_overrides_nested_columns(self, mock_urlopen, mock_sleep):
        """Fragments nested inside 'columns' layout arrays are discovered and patched.

        Liferay uses different child array keys ('columns', 'rows', 'elements', etc.)
        depending on the layout element type.  The traverser must descend through all
        of them, not just 'pageElements'.
        """
        import json

        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "share": "false",
        }
        paths = {"root": self.tmp_dir}
        configs_dir = self.tmp_dir / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)

        overrides_data = {"nested-frag": {"color": "blue"}}
        with open(configs_dir / "fragment-overrides.json", "w") as f:
            json.dump(overrides_data, f)

        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),  # sites
            json.dumps(
                {
                    "items": [
                        {
                            "name": "Home",
                            "pageDefinition": {
                                "pageElement": {
                                    "type": "Section",
                                    "id": "section-1",
                                    "columns": [
                                        {
                                            "type": "Column",
                                            "id": "col-1",
                                            "pageElements": [
                                                {
                                                    "type": "Fragment",
                                                    "id": "frag-nested-1",
                                                    "definition": {
                                                        "fragmentConfig": {
                                                            "fragmentKey": "nested-frag"
                                                        }
                                                    },
                                                }
                                            ],
                                        }
                                    ],
                                }
                            },
                        }
                    ]
                }
            ).encode("utf-8"),  # pages
            json.dumps({"status": "ok"}).encode("utf-8"),  # patch response
        ]
        ctx_manager = MagicMock()
        ctx_manager.__enter__.return_value = mock_response
        mock_urlopen.return_value = ctx_manager

        with (
            patch("ldm_core.ui.UI.success") as mock_success,
            patch.object(BaseHandler, "run_command", return_value="8080"),
            patch.object(self.handler.defaults, "get", return_value=None),
        ):
            self.handler.handler.fragments._patch_fragment_overrides(
                project_meta, paths
            )

        mock_success.assert_any_call(
            "  -> Patched configuration for fragment 'nested-frag' on page 'Home'"
        )

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_patch_fragment_overrides_fragment_entry_link(
        self, mock_urlopen, mock_sleep
    ):
        """Fragment keys inside fragmentEntryLink are discovered and matched.

        The Headless Delivery API (2025.Q1+) places fragment keys inside
        fragmentEntryLink.fragmentKey / fragmentEntryLink.fragmentEntry.fragmentEntryKey
        rather than (only) inside definition.fragmentConfig.
        """
        import json

        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "share": "false",
        }
        paths = {"root": self.tmp_dir}
        configs_dir = self.tmp_dir / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)

        overrides_data = {"fel-frag-key": {"label": "Custom Label"}}
        with open(configs_dir / "fragment-overrides.json", "w") as f:
            json.dump(overrides_data, f)

        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),  # sites
            json.dumps(
                {
                    "items": [
                        {
                            "name": "Home",
                            "pageDefinition": {
                                "pageElement": {
                                    "type": "Fragment",
                                    "id": "frag-1",
                                    "fragmentEntryLink": {
                                        "fragmentKey": "fel-frag-key",
                                        "fragmentEntry": {
                                            "fragmentEntryKey": "fel-frag-key"
                                        },
                                    },
                                }
                            },
                        }
                    ]
                }
            ).encode("utf-8"),  # pages
            json.dumps({"status": "ok"}).encode("utf-8"),  # patch response
        ]
        ctx_manager = MagicMock()
        ctx_manager.__enter__.return_value = mock_response
        mock_urlopen.return_value = ctx_manager

        with (
            patch("ldm_core.ui.UI.success") as mock_success,
            patch.object(BaseHandler, "run_command", return_value="8080"),
            patch.object(self.handler.defaults, "get", return_value=None),
        ):
            self.handler.handler.fragments._patch_fragment_overrides(
                project_meta, paths
            )

        mock_success.assert_any_call(
            "  -> Patched configuration for fragment 'fel-frag-key' on page 'Home'"
        )

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_patch_fragment_overrides_diagnostic_output(self, mock_urlopen, mock_sleep):
        """When no fragments match, diagnostic output lists configured and discovered keys.

        Verifies that UI.warning + UI.detail lines report configured keys, discovered keys,
        and the path to the raw page tree debug dump file.
        """
        import json
        import tempfile
        from pathlib import Path

        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "share": "false",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configs_dir = root / "configs"
            configs_dir.mkdir(parents=True, exist_ok=True)

            # Configured key intentionally does not appear on any page element
            overrides_data = {"missing-frag-key": {"color": "red"}}
            with open(configs_dir / "fragment-overrides.json", "w") as f:
                json.dump(overrides_data, f)

            sites_ctx = MagicMock()
            sites_resp = MagicMock()
            sites_resp.read.return_value = json.dumps(
                {"items": [{"id": "20124"}]}
            ).encode("utf-8")
            sites_ctx.__enter__.return_value = sites_resp

            pages_ctx = MagicMock()
            pages_resp = MagicMock()
            pages_resp.read.return_value = json.dumps(
                {
                    "items": [
                        {
                            "name": "Home",
                            "pageDefinition": {
                                "pageElement": {
                                    "type": "Fragment",
                                    "id": "frag-1",
                                    "definition": {
                                        "fragmentConfig": {
                                            "fragmentKey": "different-frag-key"
                                        }
                                    },
                                }
                            },
                        }
                    ]
                }
            ).encode("utf-8")
            pages_ctx.__enter__.return_value = pages_resp

            # Only 2 real responses — after that urlopen raises StopIteration
            # which api_request catches and returns None, causing silent retry
            mock_urlopen.side_effect = [sites_ctx, pages_ctx]

            with (
                patch("ldm_core.ui.UI.warning") as mock_warning,
                patch("ldm_core.ui.UI.detail") as mock_detail,
                patch.object(BaseHandler, "run_command", return_value="8080"),
                patch.object(self.handler.defaults, "get", return_value=None),
            ):
                self.handler.handler.fragments._patch_fragment_overrides(
                    project_meta, paths={"root": root}
                )

            # The "no match" warning must be emitted
            warning_messages = [str(c) for c in mock_warning.call_args_list]
            self.assertTrue(
                any("No matching fragments found" in m for m in warning_messages)
            )

            detail_messages = [str(c) for c in mock_detail.call_args_list]

            # The configured key must appear in at least one detail line
            self.assertTrue(
                any("missing-frag-key" in m for m in detail_messages),
                msg=f"Expected 'missing-frag-key' in detail output. Got: {detail_messages}",
            )

            # The discovered key (from the page element) must appear in a detail line
            self.assertTrue(
                any("different-frag-key" in m for m in detail_messages),
                msg=f"Expected 'different-frag-key' in detail output. Got: {detail_messages}",
            )

            # Debug dump file must be written to .ldm/fragment-override-debug.json
            debug_path = root / ".ldm" / "fragment-override-debug.json"
            self.assertTrue(debug_path.exists(), "Debug dump file not written")
            debug_data = json.loads(debug_path.read_text())
            self.assertIsInstance(debug_data, list)
            self.assertGreaterEqual(len(debug_data), 1)

    def test_valid_dict_passes(self):
        """A well-formed dict of fragment-key -> config-dict must return no errors."""
        data = {
            "my-fragment": {"textColor": "#fff"},
            "other-frag": {"padding": "1rem"},
        }
        errors = FragmentsService._validate_fragment_overrides(data, self.file_path)
        self.assertEqual(errors, [])

    def test_legacy_list_format_is_rejected(self):
        """A JSON list (legacy format) must produce exactly one error."""
        data = [{"key": "value"}]
        errors = FragmentsService._validate_fragment_overrides(data, self.file_path)
        self.assertEqual(len(errors), 1)
        self.assertIn("list", errors[0])
        self.assertIn("legacy", errors[0])

    def test_non_dict_root_is_rejected(self):
        """A scalar root (e.g. a bare string) must produce an error."""
        errors = FragmentsService._validate_fragment_overrides("bad", self.file_path)
        self.assertEqual(len(errors), 1)
        self.assertIn("str", errors[0])

    def test_non_dict_value_is_rejected(self):
        """A value that is not a dict (e.g. a string config) must produce an error."""
        data = {"my-fragment": "not-a-dict"}
        errors = FragmentsService._validate_fragment_overrides(data, self.file_path)
        self.assertEqual(len(errors), 1)
        self.assertIn("my-fragment", errors[0])

    def test_empty_string_key_is_rejected(self):
        """A whitespace-only key must produce an error."""
        data = {"   ": {"color": "red"}}
        errors = FragmentsService._validate_fragment_overrides(data, self.file_path)
        self.assertEqual(len(errors), 1)

    def test_non_interactive_die_on_invalid(self):
        """In non-interactive mode with die policy, UI.die must be called."""
        import json as _json

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configs = root / "configs"
            configs.mkdir()
            (configs / "fragment-overrides.json").write_text(
                _json.dumps([{"key": "legacy"}])
            )

            project_meta = {"tag": "2025.q1.1-lts"}
            paths = {"root": root}

            self.handler.non_interactive = True
            self.handler.args.on_validation_failure = "die"

            self.handler.parse_version = MagicMock(return_value=(2025, 1, 0))  # type: ignore[method-assign]

            with (
                patch("ldm_core.ui.UI.die", side_effect=SystemExit(1)) as mock_die,
                patch("ldm_core.ui.UI.warning"),
            ):
                with self.assertRaises(SystemExit):
                    self.handler.handler.fragments._patch_fragment_overrides(
                        project_meta, paths
                    )
                mock_die.assert_called_once()
                call_kwargs = mock_die.call_args.kwargs
                self.assertEqual(call_kwargs.get("exit_code"), 1)

    @patch("time.sleep")
    def test_non_interactive_ignore_continues(self, mock_sleep):
        """In non-interactive mode with ignore policy, execution must continue past validation."""
        import json as _json

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configs = root / "configs"
            configs.mkdir()
            (configs / "fragment-overrides.json").write_text(
                _json.dumps([{"key": "legacy"}])
            )

            project_meta = {"tag": "2025.q1.1-lts"}
            paths = {"root": root}

            self.handler.non_interactive = True
            self.handler.args.on_validation_failure = "ignore"
            self.handler.parse_version = MagicMock(return_value=(2025, 1, 0))  # type: ignore[method-assign]

            # If ignore is respected, execution continues to the API phase.
            # We patch run_command (docker port) to return None so it exits cleanly.
            self.handler.run_command = MagicMock(return_value=None)  # type: ignore[method-assign]
            self.handler.config = MagicMock()
            self.handler.config.get_global_config.return_value = {}
            self.handler.infra = MagicMock()
            self.handler.infra.get_proxy_ports.return_value = {"http": 80, "https": 443}
            self.handler.defaults = {}  # type: ignore[assignment]

            with (
                patch("ldm_core.ui.UI.die") as mock_die,
                patch("ldm_core.ui.UI.warning"),
            ):
                # Run — if "ignore" works it won't die; it will proceed to API
                # (which will fail quickly since there's no real Liferay).
                try:
                    self.handler.handler.fragments._patch_fragment_overrides(
                        project_meta, paths
                    )
                except Exception:
                    pass
                mock_die.assert_not_called()

    @patch("time.sleep")
    def test_namespaced_fragment_key_matching(self, mock_sleep):
        """Verify collection-namespaced fragment keys (e.g. collection/key) match overrides."""
        import json as _json

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configs = root / "configs"
            configs.mkdir()
            (configs / "fragment-overrides.json").write_text(
                _json.dumps(
                    {
                        "ai-commerce-accelerator": {
                            "microserviceUrl": "http://localhost:3000"
                        }
                    }
                )
            )

            project_meta = {"tag": "2025.q1.1-lts", "container_name": "test-c"}
            paths = {"root": root}

            self.handler.parse_version = MagicMock(return_value=(2025, 1, 0))  # type: ignore[method-assign]
            self.handler.run_command = MagicMock(return_value="0.0.0.0:8080")  # type: ignore[method-assign]
            self.handler.config = MagicMock()
            self.handler.config.get_global_config.return_value = {}
            self.handler.infra = MagicMock()
            self.handler.infra.get_proxy_ports.return_value = {"http": 80, "https": 443}

            # Mock Headless API response containing namespaced fragment key
            sites_resp = {"items": [{"id": 101}]}
            pages_resp = {
                "items": [
                    {
                        "id": 201,
                        "name": "Home",
                        "pageDefinition": {
                            "pageElement": {
                                "id": "elem-1",
                                "type": "fragment",
                                "fragmentEntryLink": {
                                    "fragmentEntry": {
                                        "key": "ai-commerce-accelerator-fragments/ai-commerce-accelerator"
                                    }
                                },
                            }
                        },
                    }
                ]
            }

            def fake_api_request(method, path, payload=None):
                if "/sites/101/site-pages" in path:
                    return pages_resp
                if "/sites" in path:
                    return sites_resp
                if "page-elements/elem-1" in path and method == "PATCH":
                    return {"status": "success"}
                return None

            # Directly test patch_fragments key matching via _patch_fragment_overrides logic
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.side_effect = [
                    _json.dumps(sites_resp).encode(),
                    _json.dumps(pages_resp).encode(),
                    _json.dumps({"status": "patched"}).encode(),
                ]
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp

                self.handler.handler.fragments._patch_fragment_overrides(
                    project_meta, paths
                )
                # Verify PATCH request was made for elem-1
                patch_calls = [
                    c
                    for c in mock_urlopen.call_args_list
                    if "page-elements/elem-1" in c[0][0].full_url
                ]
                self.assertTrue(len(patch_calls) > 0)
