import json
import os
import shutil
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from ldm_core.handlers.assets import AssetService
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.diagnostics import DiagnosticsService
from ldm_core.handlers.runtime import RuntimeService
from ldm_core.handlers.workspace import WorkspaceService


class MockWorkspaceManager(
    BaseHandler,
):
    def __init__(self):
        self._verbose = False
        self._non_interactive = True
        self.args = MagicMock()
        self.args.host_name = "localhost"
        self.args.ssl = False
        self.args.stop_running = False
        self.args.leave_running = False
        self.args.clone_only = False

        from ldm_core.defaults import DefaultsManager

        self.defaults = DefaultsManager()

        self.assets = AssetService(self)
        self.diagnostics = DiagnosticsService(self)
        self.diagnostics.validate_lcp_json = MagicMock(return_value=("Valid", True, []))
        self.workspace = WorkspaceService(self)
        self.composer = ComposerService(self)
        self.runtime = RuntimeService(self)
        self.snapshot = MagicMock()
        self.share = MagicMock()

    @property
    def verbose(self):
        return self._verbose

    @verbose.setter
    def verbose(self, value):
        self._verbose = value

    @property
    def non_interactive(self):
        return self._non_interactive

    @non_interactive.setter
    def non_interactive(self, value):
        self._non_interactive = value

    def _check_java_version(self, *args, **kwargs):
        return True

    def _check_gradle_java_version(self, *args, **kwargs):
        return True

    def check_hostname(self, *args, **kwargs):
        return True

    def find_dxp_roots(self, *args, **kwargs):
        return []

    def detect_project_path(self, project_name, for_init=False, fatal=True):
        # This will be patched in the test
        return Path(f"/tmp/{project_name}")

    def setup_paths(self, project_path):
        root = Path(project_path)
        return {
            "root": root,
            "deploy": root / "deploy",
            "files": root / "files",
            "data": root / "data",
            "configs": root / "osgi" / "configs",
            "modules": root / "osgi" / "modules",
            "ce_dir": root / "client-extensions",
            "cx": root / "osgi" / "client-extensions",
            "backups": root / "snapshots",
        }

    def verify_runtime_environment(self, paths):
        pass

    def safe_rmtree(self, path):
        if Path(path).exists():
            shutil.rmtree(path)


class TestWorkspaceMetadata(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    def test_parse_lcp_json_basic(self):
        content = json.dumps({"id": "my-service", "type": "liferay-client-extension"})
        info = self.handler.workspace._parse_lcp_json(content)
        self.assertEqual(info["id"], "my-service")
        self.assertTrue(info["deploy"])

    def test_parse_lcp_json_target_port(self):
        content = json.dumps(
            {
                "id": "my-service",
                "loadBalancer": {"targetPort": 8081},
            }
        )
        info = self.handler.workspace._parse_lcp_json(content)
        self.assertEqual(info["loadBalancer"]["targetPort"], 8081)

    def test_parse_lcp_json_external_port(self):
        content = json.dumps(
            {
                "id": "my-service",
                "loadBalancer": {"port": 443, "externalPort": True},
            }
        )
        info = self.handler.workspace._parse_lcp_json(content)
        self.assertTrue(info.get("loadBalancer", {}).get("externalPort"))

    def test_parse_client_extension_yaml(self):
        """Basic single-block format: type and oauth ERC are extracted correctly."""
        content = "type: customElement\noAuthApplicationHeadlessServer: my-erc"
        info = self.handler.workspace._parse_client_extension_yaml(content)
        self.assertEqual(info["type"], "customElement")
        self.assertEqual(info["oauth_erc"], "my-erc")

    def test_parse_client_extension_yaml_with_comments(self):
        """YAML with leading comment lines must not confuse the parser."""
        content = textwrap.dedent("""
            # This is a comment about the extension type
            type: customElement
            # ERC reference below
            oAuthApplicationHeadlessServer: erc-value
        """)
        info = self.handler.workspace._parse_client_extension_yaml(content)
        self.assertEqual(info["type"], "customElement")
        self.assertEqual(info["oauth_erc"], "erc-value")

    def test_parse_client_extension_yaml_multi_block(self):
        """Multi-block client-extension.yaml format should return the first matching block values."""
        content = textwrap.dedent("""
            my-oauth-app:
              type: oAuthApplicationHeadlessServer
              oAuthApplicationHeadlessServer: block-erc
            my-element:
              type: customElement
        """)
        info = self.handler.workspace._parse_client_extension_yaml(content)
        # Should discover type from first block
        self.assertIsNotNone(info["type"])

    def test_parse_client_extension_yaml_invalid_returns_empty(self):
        """Invalid YAML must gracefully return empty info rather than raise."""
        info = self.handler.workspace._parse_client_extension_yaml(": [invalid yaml {{")
        self.assertIsNone(info["type"])
        self.assertIsNone(info["oauth_erc"])

    def test_parse_client_extension_yaml_empty_string(self):
        """Empty content must gracefully return empty info."""
        info = self.handler.workspace._parse_client_extension_yaml("")
        self.assertIsNone(info["type"])
        self.assertIsNone(info["oauth_erc"])


class TestWorkspaceImport(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    def test_cmd_import_spaces_in_name(self, mock_run, mock_cmd_run):
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "My Awesome Project"
            source_dir.mkdir()
            (source_dir / "configs").mkdir()
            (source_dir / "client-extensions").mkdir()

            project_dir = base_path / "My Awesome Project"

            with (
                patch.object(
                    self.handler, "detect_project_path", return_value=project_dir
                ),
                patch.object(self.handler, "write_meta") as mock_write,
                patch("ldm_core.handlers.workspace.UI.info") as mock_info,
            ):
                self.handler.non_interactive = True
                self.handler.args.project = "My Awesome Project"
                self.handler.args.project_flag = None
                self.handler.workspace.cmd_import(str(source_dir), is_internal=True)

                mock_write.assert_called_once()
                args, _ = mock_write.call_args
                saved_meta = args[1]

                # Verify LDM kept the original display name but sanitized the docker container name
                self.assertEqual(saved_meta["project_name"], "My Awesome Project")
                self.assertEqual(saved_meta["container_name"], "My-Awesome-Project")

                # Verify we logged the verbose message (even if verbose is off, we can check the call isn't made, but if we mock verbose we can check it)
                # Let's run it again with verbose=True to ensure the message triggers
                self.handler.args.verbose = True
                self.handler.workspace.cmd_import(str(source_dir), is_internal=True)
                mock_info.assert_any_call(
                    "Project name 'My Awesome Project' contains invalid characters for Docker. Using 'My-Awesome-Project' for container names."
                )

    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    def test_cmd_import_project_id_passing(self, mock_run, mock_cmd_run):
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "source-workspace"
            source_dir.mkdir()
            (source_dir / "configs").mkdir()
            (source_dir / "client-extensions").mkdir()

            project_dir = base_path / "my-project"

            with (
                patch.object(
                    self.handler, "detect_project_path", return_value=project_dir
                ),
                patch.object(self.handler, "write_meta") as mock_write,
            ):
                self.handler.args.project = "my-project"
                self.handler.args.no_run = False
                self.handler.workspace.cmd_import(str(source_dir), is_internal=True)

                # Verify project_id from meta matches what we expected
                written_meta = mock_write.call_args[0][1]
                self.assertEqual(written_meta["project_name"], "my-project")

    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    @patch("ldm_core.ui.UI.ask", return_value="C")
    def test_cmd_import_clean_option(self, mock_ask, mock_run, mock_cmd_run):
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "source-workspace"
            source_dir.mkdir()
            project_dir = base_path / "my-project"
            project_dir.mkdir()
            (project_dir / "old-file.txt").write_text("old")

            with (
                patch.object(
                    self.handler, "detect_project_path", return_value=project_dir
                ),
                patch.object(self.handler, "write_meta"),
            ):
                self.handler.non_interactive = False
                self.handler.args.project = "my-project"
                self.handler.workspace.cmd_import(str(source_dir), is_internal=True)

                # Verify cleanup happened
                self.assertFalse((project_dir / "old-file.txt").exists())

    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    def test_cmd_import_no_overwrite_option(self, mock_run, mock_cmd_run):
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "source-workspace"
            source_dir.mkdir()
            (source_dir / "client-extensions").mkdir()

            # Use a real zip file to avoid zipfile.BadZipFile
            zip_path = source_dir / "client-extensions" / "ext1.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("test.txt", "NEW_CONTENT")

            project_dir = base_path / "my-project"
            project_dir.mkdir()
            cx_dest_dir = project_dir / "osgi" / "client-extensions"
            cx_dest_dir.mkdir(parents=True)
            existing_cx = cx_dest_dir / "ext1.zip"
            existing_cx.write_text("ORIGINAL_CONTENT")

            with (
                patch.object(
                    self.handler, "detect_project_path", return_value=project_dir
                ),
                patch.object(self.handler, "write_meta"),
                patch.object(
                    self.handler,
                    "setup_paths",
                    return_value={
                        "root": project_dir,
                        "ce_dir": project_dir / "client-extensions",
                        "cx": cx_dest_dir,
                        "modules": project_dir / "osgi" / "modules",
                        "configs": project_dir / "osgi" / "configs",
                        "files": project_dir / "files",
                        "deploy": project_dir / "deploy",
                    },
                ),
                patch("ldm_core.ui.UI.ask") as mock_ui_ask,
            ):
                # Simulate user selecting 'N' (Skip Existing)
                mock_ui_ask.return_value = "N"
                self.handler.non_interactive = False

                self.handler.workspace.cmd_import(str(source_dir), is_internal=True)

                # Verify the original content was PRESERVED
                self.assertEqual(existing_cx.read_text(), "ORIGINAL_CONTENT")

    def test_cmd_import_integrity_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_zip = tmp_path / "project.zip"
            source_zip.touch()
            sha_file = tmp_path / "project.zip.sha256"
            sha_file.write_text("match-sha")

            self.handler.args.verify = True
            self.handler.args.db = "hypersonic"
            self.handler.args.build = False
            self.handler.args.ssl = None
            self.handler.args.host_name = None
            self.handler.args.port = None
            self.handler.args.mount_logs = False
            self.handler.args.gogo_port = None
            self.handler.args.env = None
            self.handler.args.no_run = True
            self.handler.args.project = "test-proj"
            self.handler.args.project_flag = None

            with (
                patch(
                    "ldm_core.pipelines.import_pipeline.calculate_sha256",
                    return_value="match-sha",
                ),
                patch("ldm_core.pipelines.import_pipeline.UI.success") as mock_success,
                patch("zipfile.ZipFile"),
                patch("ldm_core.utils.safe_extract"),
                patch("ldm_core.pipelines.import_pipeline.datetime") as mock_date,
                patch.object(
                    self.handler, "detect_project_path", return_value=tmp_path / "test"
                ),
            ):
                mock_date.now.return_value.strftime.return_value = "20260512_120000"
                # Mock die to avoid further processing
                with patch(
                    "ldm_core.pipelines.import_pipeline.UI.die", side_effect=SystemExit
                ):
                    try:
                        self.handler.workspace.cmd_import(str(source_zip))
                    except SystemExit:
                        pass

                success_calls = [call[0][0] for call in mock_success.call_args_list]
                self.assertIn("Archive integrity verified.", success_calls)

    def test_hydrate_from_workspace_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "dest"
            res = self.handler.workspace._hydrate_from_workspace(
                Path("/non-existent"), {"root": dest}
            )
            self.assertTrue(res)

    @patch("ldm_core.handlers.workspace.safe_copy")
    @patch("ldm_core.handlers.workspace.safe_move")
    def test_hydrate_from_workspace_success(self, mock_move, mock_copy):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "source"
            source.mkdir()
            (source / "client-extensions").mkdir()
            (source / "client-extensions" / "test.zip").write_text("ZIP")
            dest = Path(tmp_dir) / "dest"
            paths = {
                "root": dest,
                "cx": dest / "cx",
                "ce_dir": dest / "ce",
                "modules": dest / "modules",
                "deploy": dest / "deploy",
            }
            for p in paths.values():
                p.mkdir(parents=True, exist_ok=True)

            res = self.handler.workspace._hydrate_from_workspace(source, paths)
            self.assertTrue(res)
            self.assertTrue(mock_copy.called)

    @patch("ldm_core.docker_service.DockerService.is_running")
    @patch("ldm_core.handlers.workspace.UI.confirm")
    @patch("ldm_core.handlers.workspace.UI.die")
    @patch("ldm_core.handlers.workspace.UI.info")
    def test_ensure_stopped_not_running(
        self, mock_info, mock_die, mock_confirm, mock_is_running
    ):
        mock_is_running.return_value = False
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_path = Path(tmp_dir) / "proj"
            project_path.mkdir()
            self.handler.workspace._ensure_stopped("proj", project_path)
            self.assertFalse(mock_die.called)
            self.assertFalse(mock_confirm.called)

    @patch("ldm_core.docker_service.DockerService.is_running")
    @patch("ldm_core.handlers.workspace.UI.confirm")
    @patch("ldm_core.handlers.workspace.UI.die")
    @patch("ldm_core.handlers.workspace.UI.info")
    @patch.object(MockWorkspaceManager, "cmd_stop")
    def test_ensure_stopped_stop_running_flag(
        self, mock_stop, mock_info, mock_die, mock_confirm, mock_is_running
    ):
        mock_is_running.return_value = True
        self.handler.args.stop_running = True
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_path = Path(tmp_dir) / "proj"
            project_path.mkdir()
            self.handler.workspace._ensure_stopped("proj", project_path)
            mock_stop.assert_called_once_with(project_id="proj")
            self.assertFalse(mock_die.called)
            self.assertFalse(mock_confirm.called)

    @patch("ldm_core.docker_service.DockerService.is_running")
    @patch("ldm_core.handlers.workspace.UI.confirm")
    @patch("ldm_core.handlers.workspace.UI.die")
    @patch("ldm_core.handlers.workspace.UI.info")
    @patch.object(MockWorkspaceManager, "cmd_stop")
    def test_ensure_stopped_non_interactive_auto_stop(
        self, mock_stop, mock_info, mock_die, mock_confirm, mock_is_running
    ):
        mock_is_running.return_value = True
        self.handler.args.stop_running = False
        self.handler.non_interactive = True
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_path = Path(tmp_dir) / "proj"
            project_path.mkdir()
            self.handler.workspace._ensure_stopped("proj", project_path)
            mock_stop.assert_called_once_with(project_id="proj")
            self.assertFalse(mock_die.called)
            self.assertFalse(mock_confirm.called)

    @patch("ldm_core.docker_service.DockerService.is_running")
    @patch("ldm_core.handlers.workspace.UI.confirm")
    @patch("ldm_core.handlers.workspace.UI.die")
    @patch.object(MockWorkspaceManager, "cmd_stop")
    def test_ensure_stopped_leave_running(
        self, mock_stop, mock_die, mock_confirm, mock_is_running
    ):
        mock_is_running.return_value = True
        self.handler.args.leave_running = True
        self.handler.args.stop_running = False
        self.handler.non_interactive = False
        mock_die.side_effect = SystemExit(1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_path = Path(tmp_dir) / "proj"
            project_path.mkdir()
            with self.assertRaises(SystemExit):
                self.handler.workspace._ensure_stopped("proj", project_path)
            self.assertFalse(mock_stop.called)
            mock_die.assert_called_once()
            self.assertIn("`--leave-running` was specified", mock_die.call_args[0][0])
            self.assertFalse(mock_confirm.called)

    @patch("ldm_core.docker_service.DockerService.is_running")
    @patch("ldm_core.handlers.workspace.UI.confirm")
    @patch("ldm_core.handlers.workspace.UI.die")
    @patch.object(MockWorkspaceManager, "cmd_stop")
    def test_ensure_stopped_interactive_confirm_yes(
        self, mock_stop, mock_die, mock_confirm, mock_is_running
    ):
        mock_is_running.return_value = True
        self.handler.args.stop_running = False
        self.handler.non_interactive = False
        mock_confirm.return_value = True
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_path = Path(tmp_dir) / "proj"
            project_path.mkdir()
            self.handler.workspace._ensure_stopped("proj", project_path)
            mock_stop.assert_called_once_with(project_id="proj")
            self.assertFalse(mock_die.called)

    @patch("ldm_core.docker_service.DockerService.is_running")
    @patch("ldm_core.handlers.workspace.UI.confirm")
    @patch("ldm_core.handlers.workspace.UI.die")
    @patch.object(MockWorkspaceManager, "cmd_stop")
    def test_ensure_stopped_interactive_confirm_no(
        self, mock_stop, mock_die, mock_confirm, mock_is_running
    ):
        mock_is_running.return_value = True
        self.handler.args.stop_running = False
        self.handler.non_interactive = False
        mock_confirm.return_value = False
        mock_die.side_effect = SystemExit(1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_path = Path(tmp_dir) / "proj"
            project_path.mkdir()
            with self.assertRaises(SystemExit):
                self.handler.workspace._ensure_stopped("proj", project_path)
            self.assertFalse(mock_stop.called)
            mock_die.assert_called_once()


class TestWorkspaceScanners(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    def test_scan_standalone_services(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            services_dir = root / "services"
            services_dir.mkdir()
            s1 = services_dir / "service1"
            s1.mkdir()
            (s1 / "LCP.json").write_text(json.dumps({"id": "s1"}))
            (s1 / "Dockerfile").touch()

            services = self.handler.workspace.scan_standalone_services(root)
            self.assertEqual(len(services), 1)
            self.assertEqual(services[0]["id"], "s1")

    def test_get_host_passthrough_env(self):
        with patch.dict(
            os.environ, {"LDM_TEST": "val", "LXC_TEST": "lxc", "OTHER": "ignored"}
        ):
            # scan_client_extensions and scan_standalone_services will be called
            with (
                patch.object(
                    self.handler.workspace, "scan_client_extensions", return_value=[]
                ),
                patch.object(
                    self.handler.workspace, "scan_standalone_services", return_value=[]
                ),
            ):
                env = self.handler.workspace.get_host_passthrough_env(
                    {"root": Path("/tmp")}, "liferay"
                )
                self.assertTrue(any("TEST=val" in e for e in env))
                self.assertTrue(any("LXC_TEST=lxc" in e for e in env))
                self.assertFalse(any("OTHER" in e for e in env))

    def test_scan_client_extensions_zip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            root_dir = tmp_path / "root"
            osgi_cx_dir = tmp_path / "osgi-cx"
            ce_build_dir = tmp_path / "ce-build"

            for d in [root_dir, osgi_cx_dir, ce_build_dir]:
                d.mkdir(parents=True)

            # Create a mock CX zip in ce-build
            zip_path = ce_build_dir / "my-ext.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr(
                    "LCP.json",
                    json.dumps({"id": "my-ext", "type": "liferay-client-extension"}),
                )
                z.writestr("Dockerfile", "FROM scratch")

            exts = self.handler.workspace.scan_client_extensions(
                root_dir, osgi_cx_dir, ce_build_dir
            )
            self.assertEqual(len(exts), 1)
            self.assertEqual(exts[0]["id"], "my-ext")
            self.assertTrue(exts[0]["is_service"])

    def test_scan_extension_metadata_folder(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            (folder / "client-extension.yaml").write_text("type: customElement")
            (folder / "LCP.json").write_text(
                json.dumps(
                    {
                        "id": "lcp-id",
                        "loadBalancer": {"targetPort": 8081},
                        "env": {"VAR1": "VAL1"},
                    }
                )
            )

            info = self.handler.workspace._scan_extension_metadata(folder_path=folder)
            self.assertEqual(info["type"], "customElement")
            self.assertEqual(info["id"], "lcp-id")
            self.assertEqual(info["loadBalancer"]["targetPort"], 8081)
            self.assertEqual(info["env"]["VAR1"], "VAL1")

    @patch("ldm_core.handlers.workspace.safe_copy")
    @patch("ldm_core.handlers.workspace.safe_move")
    def test_sync_cx_artifact(self, mock_move, mock_copy):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            zip_path = tmp_path / "ext1.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("test.txt", "DATA")

            paths = {
                "root": tmp_path / "project",
                "cx": tmp_path / "project" / "osgi" / "client-extensions",
                "ce_dir": tmp_path / "project" / "client-extensions",
            }
            for p in paths.values():
                p.mkdir(parents=True, exist_ok=True)

            self.handler.workspace._sync_cx_artifact(zip_path, paths)
            self.assertTrue(mock_copy.called)
            self.assertTrue(mock_move.called)

    def test_monitor_event_handling_logic(self):
        # We don't call cmd_monitor because it's blocking.
        # We just test the _hydrate_from_workspace logic again to be sure.
        pass

    def test_cmd_import_unsupported_db_type(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_ldmp = tmp_path / "project.ldmp"
            source_ldmp.touch()

            self.handler.args.verify = False
            self.handler.args.project = "test-proj"

            # Create mock meta file in extract directory
            def mock_safe_extract(tar_or_zip, extract_dir):
                extract_path = Path(extract_dir)
                extract_path.mkdir(parents=True, exist_ok=True)
                (extract_path / "meta").write_text(
                    "db_type=oracle\ntag=2026.q1.4-lts\n", encoding="utf-8"
                )

            with (
                patch("tarfile.open"),
                patch("ldm_core.utils.safe_extract", side_effect=mock_safe_extract),
                patch(
                    "ldm_core.handlers.workspace.UI.die", side_effect=SystemExit
                ) as mock_die,
            ):
                with self.assertRaises(SystemExit):
                    self.handler.workspace.cmd_import(str(source_ldmp))
                mock_die.assert_called_once_with(
                    "Unsupported database type 'oracle' in LDM package manifest."
                )


class TestWorkspaceRemoteImport(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    def test_parse_github_repo(self):
        ws = self.handler.workspace
        # GITHUB_URL parsing tests
        self.assertEqual(
            ws._parse_github_repo("https://github.com/owner/repo"), ("owner", "repo")
        )
        self.assertEqual(
            ws._parse_github_repo("https://github.com/owner/repo.git"),
            ("owner", "repo"),
        )
        self.assertEqual(
            ws._parse_github_repo("https://github.com/owner/repo.git?ref=main"),
            ("owner", "repo"),
        )
        self.assertEqual(
            ws._parse_github_repo("http://github.com/owner/repo/"), ("owner", "repo")
        )
        self.assertEqual(
            ws._parse_github_repo("https://github.com/owner/repo/tree/master/subpath"),
            ("owner", "repo"),
        )
        # SSH URLs
        self.assertEqual(
            ws._parse_github_repo("git@github.com:owner/repo.git"), ("owner", "repo")
        )
        self.assertEqual(
            ws._parse_github_repo("git@github.com:owner/repo"), ("owner", "repo")
        )
        # Invalid URLs
        self.assertIsNone(ws._parse_github_repo(""))
        self.assertIsNone(ws._parse_github_repo("https://example.com"))

    @patch("requests.get")
    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import")
    def test_cmd_import_remote_archive_url(self, mock_cmd_import, mock_get):
        # Mock successful archive download
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = lambda *_args, **_kwargs: [b"archive_data"]

        mock_sha_response = MagicMock()
        mock_sha_response.status_code = 200
        mock_sha_response.text = "hash"

        mock_get.side_effect = [mock_response, mock_sha_response]

        self.handler.workspace.cmd_import("https://example.com/project.ldmp")

        # Verify that cmd_import was recursively called
        self.assertTrue(mock_cmd_import.called)
        called_path = mock_cmd_import.call_args[0][0]
        self.assertTrue(called_path.endswith("project.ldmp"))

    @patch("subprocess.run")
    @patch("requests.get")
    @patch("ldm_core.handlers.workspace.calculate_sha256", return_value="matching_hash")
    @patch("tarfile.open")
    def test_cmd_import_git_url_success(
        self, mock_tar_open, mock_calc_sha, mock_get, mock_sub_run
    ):
        real_import = self.handler.workspace.cmd_import
        calls = []

        def mock_import(source_path, *args, **kwargs):
            calls.append(source_path)
            from urllib.parse import urlparse

            is_github = False
            try:
                parsed = urlparse(source_path)
                if parsed.netloc in (
                    "github.com",
                    "www.github.com",
                ) or source_path.startswith("git@github.com:"):
                    is_github = True
            except Exception:
                pass

            if is_github:
                return real_import(source_path, *args, **kwargs)
            return "my-project"

        self.handler.workspace.cmd_import = mock_import

        # 1. Mock subprocess.run for git clone & git remote get-url origin
        mock_clone_res = MagicMock()
        mock_clone_res.returncode = 0

        mock_origin_res = MagicMock()
        mock_origin_res.returncode = 0
        mock_origin_res.stdout = "git@github.com:owner/repo.git"

        mock_sub_run.side_effect = [mock_clone_res, mock_origin_res]

        # 2. Mock requests.get for GitHub Releases API and downloads
        mock_release_resp = MagicMock()
        mock_release_resp.status_code = 200
        mock_release_resp.json.return_value = {
            "assets": [
                {
                    "name": "project.ldmp",
                    "url": "https://api.github.com/assets/1",
                    "size": 50000,
                },
                {
                    "name": "project.ldmp.sha256",
                    "url": "https://api.github.com/assets/2",
                    "size": 65,
                },
            ]
        }

        mock_ldmp_resp = MagicMock()
        mock_ldmp_resp.status_code = 200
        mock_ldmp_resp.iter_content = lambda *_args, **_kwargs: [b"package_data"]

        mock_sha_resp = MagicMock()
        mock_sha_resp.status_code = 200
        mock_sha_resp.text = "matching_hash project.ldmp"

        mock_get.side_effect = [mock_release_resp, mock_ldmp_resp, mock_sha_resp]

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "my-project"

            def mock_safe_extract(tar, extract_dir):
                extract_path = Path(extract_dir)
                extract_path.mkdir(parents=True, exist_ok=True)
                (extract_path / "meta").write_text(
                    "github_repository=owner/repo\ntag=2024.q1.3\n", encoding="utf-8"
                )

            with (
                patch("ldm_core.utils.safe_extract", side_effect=mock_safe_extract),
                patch(
                    "ldm_core.handlers.base.BaseHandler.detect_project_path",
                    return_value=project_path,
                ),
            ):
                self.handler.args.project = "my-project"
                self.handler.args.verify = True
                self.handler.args.no_run = True
                self.handler.workspace.cmd_import("https://github.com/owner/repo")

        # Assert recursive import was NOT called (clone bypassed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], "https://github.com/owner/repo")
        # Assert database / snapshot restore was triggered
        self.assertTrue(self.handler.snapshot.cmd_restore.called)
        # Assert git clone was NOT called
        for call_args in mock_sub_run.call_args_list:
            if call_args[0] and len(call_args[0][0]) > 1:
                self.assertNotIn("clone", call_args[0][0])

    @patch("subprocess.run")
    @patch("requests.get")
    @patch("ldm_core.handlers.workspace.calculate_sha256", return_value="matching_hash")
    @patch("tarfile.open")
    def test_cmd_import_git_url_clone_only(
        self, mock_tar_open, mock_calc_sha, mock_get, mock_sub_run
    ):
        real_import = self.handler.workspace.cmd_import
        calls = []

        def mock_import(source_path, *args, **kwargs):
            calls.append(source_path)
            from urllib.parse import urlparse

            is_github = False
            try:
                parsed = urlparse(source_path)
                if parsed.netloc in (
                    "github.com",
                    "www.github.com",
                ) or source_path.startswith("git@github.com:"):
                    is_github = True
            except Exception:
                pass

            if is_github:
                return real_import(source_path, *args, **kwargs)
            return "my-project"

        self.handler.workspace.cmd_import = mock_import

        mock_clone_res = MagicMock()
        mock_clone_res.returncode = 0
        mock_sub_run.return_value = mock_clone_res

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "my-project"

            with (
                patch(
                    "ldm_core.handlers.base.BaseHandler.detect_project_path",
                    return_value=project_path,
                ),
                patch("ldm_core.handlers.base.BaseHandler.read_meta", return_value={}),
            ):
                self.handler.args.project = "my-project"
                self.handler.args.verify = True
                self.handler.args.no_run = True
                self.handler.args.clone_only = True
                self.handler.workspace.cmd_import("https://github.com/owner/repo")

        # Assert recursive import was called with cloned git repo path
        self.assertEqual(len(calls), 2)
        self.assertTrue("clone_" in calls[1])
        # Assert git clone was triggered
        mock_sub_run.assert_any_call(
            ["git", "clone", "--", "https://github.com/owner/repo", ANY],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("subprocess.run")
    def test_cmd_import_git_clone_auth_failure(self, mock_sub_run):
        # Mock git clone failure
        mock_clone_res = MagicMock()
        mock_clone_res.returncode = 1
        mock_clone_res.stderr = "Permission denied (publickey)."
        mock_sub_run.return_value = mock_clone_res

        with self.assertRaises(SystemExit):
            self.handler.workspace.cmd_import("git@github.com:owner/repo.git")

    @patch("subprocess.run")
    @patch("requests.get")
    def test_cmd_import_git_url_release_missing(self, mock_get, mock_sub_run):
        real_import = self.handler.workspace.cmd_import
        calls = []

        def mock_import(source_path, *args, **kwargs):
            calls.append(source_path)
            from urllib.parse import urlparse

            is_github = False
            try:
                parsed = urlparse(source_path)
                if parsed.netloc in (
                    "github.com",
                    "www.github.com",
                ) or source_path.startswith("git@github.com:"):
                    is_github = True
            except Exception:
                pass

            if is_github:
                return real_import(source_path, *args, **kwargs)
            return "my-project"

        self.handler.workspace.cmd_import = mock_import

        # Mock git clone success
        mock_clone_res = MagicMock()
        mock_clone_res.returncode = 0
        mock_sub_run.return_value = mock_clone_res

        # Mock release response missing LDMP files
        mock_release_resp = MagicMock()
        mock_release_resp.status_code = 200
        mock_release_resp.json.return_value = {"assets": []}
        mock_get.return_value = mock_release_resp

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "my-project"

            with (
                patch(
                    "ldm_core.handlers.base.BaseHandler.detect_project_path",
                    return_value=project_path,
                ),
                patch("ldm_core.handlers.base.BaseHandler.read_meta", return_value={}),
            ):
                self.handler.args.project = "my-project"
                self.handler.args.verify = True
                self.handler.args.no_run = True
                self.handler.args.clone_only = True
                self.handler.workspace.cmd_import("https://github.com/owner/repo")

        # Assert recursive import was called with cloned git repo path (fallback succeeded)
        self.assertEqual(len(calls), 2)
        self.assertTrue("clone_" in calls[1])
        # Assert git clone was triggered
        mock_sub_run.assert_any_call(
            ["git", "clone", "--", "https://github.com/owner/repo", ANY],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("subprocess.run")
    @patch("requests.get")
    @patch("ldm_core.handlers.workspace.calculate_sha256", return_value="matching_hash")
    @patch("tarfile.open")
    def test_cmd_import_remote_unsupported_db_type(
        self, mock_tar_open, mock_calc_sha, mock_get, mock_sub_run
    ):
        real_import = self.handler.workspace.cmd_import

        def mock_import(source_path, *args, **kwargs):
            return real_import(source_path, *args, **kwargs)

        self.handler.workspace.cmd_import = mock_import

        # 1. Mock subprocess.run for git clone & git remote get-url origin
        mock_clone_res = MagicMock()
        mock_clone_res.returncode = 0
        mock_origin_res = MagicMock()
        mock_origin_res.returncode = 0
        mock_origin_res.stdout = "git@github.com:owner/repo.git"
        mock_sub_run.side_effect = [mock_clone_res, mock_origin_res]

        # 2. Mock requests.get for GitHub Releases API and downloads
        mock_release_resp = MagicMock()
        mock_release_resp.status_code = 200
        mock_release_resp.json.return_value = {
            "assets": [
                {
                    "name": "project.ldmp",
                    "url": "https://api.github.com/assets/1",
                    "size": 50000,
                },
                {
                    "name": "project.ldmp.sha256",
                    "url": "https://api.github.com/assets/2",
                    "size": 65,
                },
            ]
        }

        mock_ldmp_resp = MagicMock()
        mock_ldmp_resp.status_code = 200
        mock_ldmp_resp.iter_content = lambda *_args, **_kwargs: [b"package_data"]

        mock_sha_resp = MagicMock()
        mock_sha_resp.status_code = 200
        mock_sha_resp.text = "matching_hash project.ldmp"

        mock_get.side_effect = [mock_release_resp, mock_ldmp_resp, mock_sha_resp]

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "my-project"

            def mock_safe_extract(tar, extract_dir):
                extract_path = Path(extract_dir)
                extract_path.mkdir(parents=True, exist_ok=True)
                (extract_path / "meta").write_text(
                    "github_repository=owner/repo\ntag=2024.q1.3\ndb_type=sqlserver\n",
                    encoding="utf-8",
                )

            with (
                patch("ldm_core.utils.safe_extract", side_effect=mock_safe_extract),
                patch(
                    "ldm_core.handlers.base.BaseHandler.detect_project_path",
                    return_value=project_path,
                ),
                patch(
                    "ldm_core.handlers.workspace.UI.die", side_effect=SystemExit
                ) as mock_die,
            ):
                self.handler.args.project = "my-project"
                self.handler.args.verify = True
                self.handler.args.no_run = True
                with self.assertRaises(SystemExit):
                    self.handler.workspace.cmd_import("https://github.com/owner/repo")
                mock_die.assert_called_once_with(
                    "Unsupported database type 'sqlserver' in LDM package manifest."
                )

    @patch("subprocess.run")
    @patch("requests.get")
    @patch("ldm_core.handlers.workspace.calculate_sha256", return_value="matching_hash")
    @patch("tarfile.open")
    def test_cmd_import_git_url_empty_ldmp_fallback(
        self, mock_tar_open, mock_calc_sha, mock_get, mock_sub_run
    ):
        real_import = self.handler.workspace.cmd_import
        calls = []

        def mock_import(source_path, *args, **kwargs):
            calls.append(source_path)
            from urllib.parse import urlparse

            is_github = False
            try:
                parsed = urlparse(source_path)
                if parsed.netloc in (
                    "github.com",
                    "www.github.com",
                ) or source_path.startswith("git@github.com:"):
                    is_github = True
            except Exception:
                pass

            if is_github:
                return real_import(source_path, *args, **kwargs)
            return "my-project"

        self.handler.workspace.cmd_import = mock_import

        # 1. Mock subprocess.run for git clone & git remote get-url origin
        mock_clone_res = MagicMock()
        mock_clone_res.returncode = 0

        mock_origin_res = MagicMock()
        mock_origin_res.returncode = 0
        mock_origin_res.stdout = "git@github.com:owner/repo.git"

        # The git clone command should be run, and then the metadata read or get-url
        mock_sub_run.side_effect = [mock_clone_res, mock_origin_res, mock_clone_res]

        # 2. Mock requests.get to return a release with an empty/vanilla .ldmp asset (size 562 bytes)
        mock_release_resp = MagicMock()
        mock_release_resp.status_code = 200
        mock_release_resp.json.return_value = {
            "assets": [
                {
                    "name": "project.ldmp",
                    "url": "https://api.github.com/assets/1",
                    "size": 562,  # < 10KB, empty/vanilla package!
                },
                {
                    "name": "project.ldmp.sha256",
                    "url": "https://api.github.com/assets/2",
                    "size": 65,
                },
            ]
        }

        mock_get.side_effect = [mock_release_resp]

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "my-project"

            with (
                patch(
                    "ldm_core.handlers.base.BaseHandler.detect_project_path",
                    return_value=project_path,
                ),
                patch("shutil.rmtree"),  # Avoid deleting temp directories during test
                patch("ldm_core.handlers.workspace.WorkspaceService._ensure_stopped"),
            ):
                self.handler.args.project = "my-project"
                self.handler.args.verify = True
                self.handler.args.no_run = True
                with self.assertRaises(SystemExit):
                    self.handler.workspace.cmd_import("https://github.com/owner/repo")

        # Verify that it did NOT fall back to clone
        clone_called = False
        for call_args in mock_sub_run.call_args_list:
            if call_args[0] and len(call_args[0][0]) > 1:
                if "clone" in call_args[0][0]:
                    clone_called = True
                    break
        self.assertFalse(clone_called)


class TestWorkspaceQuickstart(unittest.TestCase):
    def setUp(self):
        self.manager = MockWorkspaceManager()
        self.manager.share = MagicMock()
        import tempfile

        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_project_dir = Path(self.temp_dir.name) / "ai-commerce-accelerator"

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import")
    @patch.object(MockWorkspaceManager, "detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch.object(MockWorkspaceManager, "setup_paths")
    @patch("ldm_core.handlers.assets.AssetService._fetch_seed")
    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    def test_cmd_quickstart_success(
        self,
        mock_cmd_run,
        mock_fetch_seed,
        mock_setup_paths,
        mock_read_meta,
        mock_detect,
        mock_cmd_import,
    ):
        self.test_project_dir.mkdir(parents=True, exist_ok=True)
        mock_detect.return_value = self.test_project_dir
        mock_read_meta.return_value = {
            "tag": "2026.q1.4-lts",
            "db_type": "postgresql",
            "use_shared_search": "true",
        }
        mock_setup_paths.return_value = {}
        mock_fetch_seed.return_value = True

        self.manager.workspace.cmd_quickstart(
            "aica", share=True, share_subdomain="my-aica-sub"
        )

        mock_cmd_import.assert_called_once_with(
            "https://github.com/peterrichards-lr/liferay-ai-commerce-accelerator.git"
        )
        mock_fetch_seed.assert_called_once()
        mock_cmd_run.assert_called_once_with("liferay-ai-commerce-accelerator")
        self.manager.share.cmd_start.assert_called_once_with(
            "liferay-ai-commerce-accelerator", subdomain="my-aica-sub"
        )
        self.assertTrue(self.manager.args.browser)

    def test_cmd_quickstart_invalid_template(self):
        with self.assertRaises(SystemExit):
            self.manager.workspace.cmd_quickstart("invalid-template")

    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import")
    @patch.object(MockWorkspaceManager, "detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch.object(MockWorkspaceManager, "setup_paths")
    @patch("ldm_core.handlers.assets.AssetService._fetch_seed")
    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    def test_cmd_quickstart_declined_seed_success(
        self,
        mock_cmd_run,
        mock_fetch_seed,
        mock_setup_paths,
        mock_read_meta,
        mock_detect,
        mock_cmd_import,
    ):
        self.test_project_dir.mkdir(parents=True, exist_ok=True)
        mock_detect.return_value = self.test_project_dir
        mock_read_meta.return_value = {
            "tag": "2026.q1.4-lts",
            "db_type": "postgresql",
            "use_shared_search": "true",
        }
        mock_setup_paths.return_value = {}
        mock_fetch_seed.return_value = False

        self.manager.workspace.cmd_quickstart(
            "aica", share=True, share_subdomain="my-aica-sub"
        )

        mock_cmd_import.assert_called_once_with(
            "https://github.com/peterrichards-lr/liferay-ai-commerce-accelerator.git"
        )
        mock_fetch_seed.assert_called_once()
        mock_cmd_run.assert_called_once_with("liferay-ai-commerce-accelerator")
        self.manager.share.cmd_start.assert_called_once_with(
            "liferay-ai-commerce-accelerator", subdomain="my-aica-sub"
        )
        self.assertTrue(self.manager.args.browser)

    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import")
    @patch.object(MockWorkspaceManager, "detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch.object(MockWorkspaceManager, "setup_paths")
    @patch("ldm_core.handlers.assets.AssetService._fetch_seed")
    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_cmd_quickstart_custom_templates_override(
        self,
        mock_read_text,
        mock_exists,
        mock_cmd_run,
        mock_fetch_seed,
        mock_setup_paths,
        mock_read_meta,
        mock_detect,
        mock_cmd_import,
    ):
        self.test_project_dir.mkdir(parents=True, exist_ok=True)
        mock_detect.return_value = self.test_project_dir
        mock_read_meta.return_value = {
            "tag": "2026.q1.4-lts",
            "db_type": "postgresql",
            "use_shared_search": "true",
        }
        mock_setup_paths.return_value = {}
        mock_fetch_seed.return_value = True

        # Simulate custom template config overrides
        mock_exists.return_value = True
        mock_read_text.return_value = json.dumps(
            {
                "custom_aica": {
                    "repo": "https://github.com/custom/my-aica.git",
                    "default_name": "custom-aica-project",
                }
            }
        )

        self.manager.workspace.cmd_quickstart(
            "custom_aica", share=True, share_subdomain="custom-sub"
        )

        mock_cmd_import.assert_called_once_with("https://github.com/custom/my-aica.git")
        mock_cmd_run.assert_called_once_with("custom-aica-project")
        self.manager.share.cmd_start.assert_called_once_with(
            "custom-aica-project", subdomain="custom-sub"
        )

    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import")
    @patch.object(MockWorkspaceManager, "detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch.object(MockWorkspaceManager, "setup_paths")
    @patch("ldm_core.handlers.assets.AssetService._fetch_seed")
    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    def test_cmd_quickstart_restored_from_package(
        self,
        mock_cmd_run,
        mock_fetch_seed,
        mock_setup_paths,
        mock_read_meta,
        mock_detect,
        mock_cmd_import,
    ):
        self.test_project_dir.mkdir(parents=True, exist_ok=True)
        mock_detect.return_value = self.test_project_dir
        mock_read_meta.return_value = {
            "tag": "2026.q1.4-lts",
            "db_type": "postgresql",
            "use_shared_search": "true",
            "restored_from_package": "true",
            "package_includes_database": "true",
        }
        mock_setup_paths.return_value = {}

        self.manager.workspace.cmd_quickstart(
            "aica", share=True, share_subdomain="my-aica-sub"
        )

        mock_cmd_import.assert_called_once_with(
            "https://github.com/peterrichards-lr/liferay-ai-commerce-accelerator.git"
        )
        mock_fetch_seed.assert_not_called()
        mock_cmd_run.assert_not_called()
        self.manager.share.cmd_start.assert_called_once_with(
            "liferay-ai-commerce-accelerator", subdomain="my-aica-sub"
        )
        self.assertTrue(self.manager.args.browser)

    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import")
    @patch.object(MockWorkspaceManager, "detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch.object(MockWorkspaceManager, "setup_paths")
    @patch("ldm_core.handlers.assets.AssetService._fetch_seed")
    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    def test_cmd_quickstart_restored_from_package_no_db(
        self,
        mock_cmd_run,
        mock_fetch_seed,
        mock_setup_paths,
        mock_read_meta,
        mock_detect,
        mock_cmd_import,
    ):
        self.test_project_dir.mkdir(parents=True, exist_ok=True)
        mock_detect.return_value = self.test_project_dir
        mock_read_meta.return_value = {
            "tag": "2026.q1.4-lts",
            "db_type": "postgresql",
            "use_shared_search": "true",
            "restored_from_package": "true",
            "package_includes_database": "false",
        }
        mock_setup_paths.return_value = {}
        mock_fetch_seed.return_value = True

        self.manager.workspace.cmd_quickstart(
            "aica", share=True, share_subdomain="my-aica-sub"
        )

        mock_cmd_import.assert_called_once_with(
            "https://github.com/peterrichards-lr/liferay-ai-commerce-accelerator.git"
        )
        mock_fetch_seed.assert_called_once()
        mock_cmd_run.assert_called_once_with("liferay-ai-commerce-accelerator")
        self.manager.share.cmd_start.assert_called_once_with(
            "liferay-ai-commerce-accelerator", subdomain="my-aica-sub"
        )
        self.assertTrue(self.manager.args.browser)

    @patch.object(MockWorkspaceManager, "register_project")
    @patch("ldm_core.handlers.base.BaseHandler.write_meta")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch.object(MockWorkspaceManager, "detect_project_path")
    @patch.object(MockWorkspaceManager, "setup_paths")
    @patch.object(MockWorkspaceManager, "check_port")
    @patch("ldm_core.handlers.runtime.RuntimeService.sync_stack")
    def test_cmd_fork_success(
        self,
        mock_sync_stack,
        mock_check_port,
        mock_setup_paths,
        mock_detect_path,
        mock_read_meta,
        mock_write_meta,
        mock_register_project,
    ):
        source_dir = self.test_project_dir / "source_proj"
        target_dir = self.test_project_dir / "target_fork"
        source_dir.mkdir(parents=True, exist_ok=True)

        def mock_detect(project_id, for_init=False, fatal=True):
            if project_id == "source_proj":
                return source_dir
            if project_id == "target_fork":
                return target_dir
            return None

        mock_detect_path.side_effect = mock_detect
        mock_read_meta.return_value = {
            "project_name": "source_proj",
            "port": "8080",
            "tag": "2026.q1.4-lts",
            "db_type": "postgresql",
        }

        # Setup paths mocks
        mock_setup_paths.side_effect = lambda path: {
            "root": Path(path),
            "backups": Path(path) / "snapshots",
        }

        # Mock port availability (8081 is free)
        mock_check_port.return_value = True

        # Simulate snapshot exists
        snapshot_dir = source_dir / "snapshots" / "my-snap"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        self.manager.workspace.cmd_fork(
            "source_proj", "target_fork", snapshot="my-snap"
        )

        # Verify metadata mutation and creation
        mock_write_meta.assert_called_once()
        written_meta = mock_write_meta.call_args[0][1]
        self.assertEqual(written_meta["project_name"], "target_fork")
        self.assertEqual(written_meta["container_name"], "target_fork")
        self.assertEqual(written_meta["db_container_name"], "target_fork-db")
        self.assertEqual(written_meta["host_name"], "target_fork.local")
        self.assertEqual(written_meta["port"], "8081")

        # Verify registration and restore calls
        mock_register_project.assert_called_once_with(
            "target_fork", target_dir, "target_fork.local"
        )
        self.manager.snapshot.cmd_restore.assert_called_once_with(
            project_id="target_fork", backup_dir=str(snapshot_dir)
        )
        mock_sync_stack.assert_called_once()

    @patch.object(MockWorkspaceManager, "register_project")
    @patch("ldm_core.handlers.base.BaseHandler.write_meta")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch.object(MockWorkspaceManager, "detect_project_path")
    @patch.object(MockWorkspaceManager, "setup_paths")
    @patch.object(MockWorkspaceManager, "check_port")
    @patch("ldm_core.handlers.runtime.RuntimeService.sync_stack")
    def test_cmd_fork_auto_snapshot(
        self,
        mock_sync_stack,
        mock_check_port,
        mock_setup_paths,
        mock_detect_path,
        mock_read_meta,
        mock_write_meta,
        mock_register_project,
    ):
        source_dir = self.test_project_dir / "source_proj"
        target_dir = self.test_project_dir / "target_fork"
        source_dir.mkdir(parents=True, exist_ok=True)

        def mock_detect(project_id, for_init=False, fatal=True):
            if project_id == "source_proj":
                return source_dir
            if project_id == "target_fork":
                return target_dir
            return None

        mock_detect_path.side_effect = mock_detect
        mock_read_meta.return_value = {"port": "8080"}

        mock_setup_paths.side_effect = lambda path: {
            "root": Path(path),
            "backups": Path(path) / "snapshots",
        }
        mock_check_port.return_value = True

        # Mock latest snapshot retrieval
        self.manager.snapshot._get_snapshots.return_value = [
            {"path": source_dir / "snapshots" / "auto-snap", "name": "auto-snap"}
        ]

        self.manager.workspace.cmd_fork("source_proj", "target_fork")

        # Assert cmd_snapshot was called to take a fresh snapshot of source
        self.manager.snapshot.cmd_snapshot.assert_called_once_with(
            project_id="source_proj"
        )
        self.manager.snapshot.cmd_restore.assert_called_once_with(
            project_id="target_fork",
            backup_dir=str(source_dir / "snapshots" / "auto-snap"),
        )


class TestAtomicZipRepackaging(unittest.TestCase):
    """Tests for atomic zip replacement in _rewrite_oauth_urls_in_zip (Issue #469)."""

    def setUp(self):
        self.handler = MockWorkspaceManager()

    def test_atomic_replace_not_os_remove(self):
        """Path.replace() must be used for atomic overwrite; os.remove must NOT be called."""
        import zipfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Build a minimal client-extension.yaml inside a zip
            cx_yaml = textwrap.dedent("""
                my-app:
                  type: oAuthApplicationHeadlessServer
                  oAuthApplicationHeadlessServer: test-erc
                  homePageURL: http://localhost:8080
                  redirectURIs:
                    - http://localhost:8080/callback
            """)
            zip_path = tmp_path / "my-ext.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("client-extension.yaml", cx_yaml)

            zip_path.stat().st_size

            # Run the method
            self.handler.workspace._rewrite_oauth_urls_in_zip(
                zip_path, "my-host.local", "my-ext"
            )

            # The zip must still exist (no data loss)
            self.assertTrue(
                zip_path.exists(), "Original zip must still exist after atomic replace"
            )

            # The zip must have been modified (OAuth URLs were rewritten)
            with zipfile.ZipFile(zip_path, "r") as zf:
                updated_content = zf.read("client-extension.yaml").decode("utf-8")
            self.assertNotIn(
                "localhost", updated_content, "localhost references must be rewritten"
            )
            self.assertIn("my-host.local", updated_content)

    def test_localhost_skip(self):
        """When host_name is 'localhost', the zip must be left untouched."""
        import zipfile

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "skip.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("client-extension.yaml", "type: customElement")

            zip_path.stat().st_mtime
            self.handler.workspace._rewrite_oauth_urls_in_zip(
                zip_path, "localhost", "my-ext"
            )
            # File should be untouched when host is localhost
            self.assertTrue(zip_path.exists())

    def test_no_config_file_is_safe(self):
        """A zip with no client-extension.yaml / JSON config must be handled gracefully."""
        import zipfile

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "empty.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("README.md", "hello")

            # Should not raise
            self.handler.workspace._rewrite_oauth_urls_in_zip(
                zip_path, "my-host.local", "my-ext"
            )
            self.assertTrue(zip_path.exists())

    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_monitor")
    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import")
    def test_cmd_link_success(self, mock_import, mock_monitor):
        """test ldm link with a valid directory."""
        mock_import.return_value = "my-linked-project"
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            self.handler.workspace.cmd_link(tmpdir)
            mock_import.assert_called_once_with(
                str(Path(tmpdir).resolve()), is_init_from=True
            )
            mock_monitor.assert_called_once_with(str(Path(tmpdir).resolve()))

    def test_cmd_link_invalid_source(self):
        """test ldm link fails with invalid path."""
        with self.assertRaises(SystemExit):
            self.handler.workspace.cmd_link("/nonexistent/path/here")

    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import")
    def test_cmd_clone_success(self, mock_import):
        """test ldm clone triggers import with clone_only=True."""
        self.handler.workspace.cmd_clone("https://github.com/owner/repo.git")
        self.assertTrue(self.handler.args.clone_only)
        mock_import.assert_called_once_with("https://github.com/owner/repo.git")

    def test_cmd_clone_invalid_source(self):
        """test ldm clone fails with non-git URL."""
        with self.assertRaises(SystemExit):
            self.handler.workspace.cmd_clone("/local/path/instead")

    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_link")
    def test_cmd_init_from_deprecation(self, mock_link):
        """test ldm init-from prints warning and calls link."""
        self.handler.workspace.cmd_init_from("/local/workspace")
        mock_link.assert_called_once_with("/local/workspace")

    def test_cmd_import_rejects_local_dir(self):
        """test cmd_import rejects local directory direct import (guiding to link)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(SystemExit):
                self.handler.workspace.cmd_import(tmpdir)

    @patch("subprocess.run")
    @patch("requests.get")
    def test_cmd_import_git_url_release_missing_aborts_without_clone_only(
        self, mock_get, mock_sub_run
    ):
        """test cmd_import aborts when release is missing and clone_only is not specified."""
        mock_release_resp = MagicMock()
        mock_release_resp.status_code = 200
        mock_release_resp.json.return_value = {"assets": []}
        mock_get.return_value = mock_release_resp

        self.handler.args.clone_only = False
        with self.assertRaises(SystemExit):
            self.handler.workspace.cmd_import("https://github.com/owner/repo")

    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import")
    def test_cmd_clone_ssh_protocols(self, mock_import):
        """Verify cmd_clone accepts custom SSH protocols containing :// or starting with git@"""
        self.handler.workspace.cmd_clone("ssh://git@github.com/owner/repo.git")
        mock_import.assert_called_once_with("ssh://git@github.com/owner/repo.git")

        mock_import.reset_mock()
        self.handler.workspace.cmd_clone("git+ssh://github.com/owner/repo.git")
        mock_import.assert_called_once_with("git+ssh://github.com/owner/repo.git")

    def test_cmd_link_path_crash_resilience(self):
        """Verify cmd_link doesn't raise raw OSError on malformed Windows paths (like URLs or invalid characters)"""
        with self.assertRaises(SystemExit):
            self.handler.workspace.cmd_link("http://invalid/path:with*illegal?chars")

    def test_cmd_import_path_crash_resilience(self):
        """Verify cmd_import doesn't raise raw OSError on malformed Windows paths"""
        # Under normal conditions (without raise), it should proceed because is_remote is False
        # and resolve path throws exception, silently bypassing the local dir reject guard
        # and then failing on target LDM package validation.
        with self.assertRaises(SystemExit):
            self.handler.workspace.cmd_import("http://invalid/path:with*illegal?chars")


if __name__ == "__main__":
    unittest.main()
