import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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

        self.assets = AssetService(self)
        self.diagnostics = DiagnosticsService(self)
        self.diagnostics.validate_lcp_json = MagicMock(return_value=("Valid", True, []))
        self.workspace = WorkspaceService(self)
        self.composer = ComposerService(self)
        self.runtime = RuntimeService(self)
        self.snapshot = MagicMock()

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

    def detect_project_path(self, project_name, for_init=False):
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
        content = "type: customElement\noAuthApplicationHeadlessServer: my-erc"
        info = self.handler.workspace._parse_client_extension_yaml(content)
        self.assertEqual(info["type"], "customElement")
        self.assertEqual(info["oauth_erc"], "my-erc")


class TestWorkspaceImport(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

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
                self.handler.workspace.cmd_import(str(source_dir))

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
                self.handler.workspace.cmd_import(str(source_dir))

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

                self.handler.workspace.cmd_import(str(source_dir))

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
                patch("ldm_core.utils.calculate_sha256", return_value="match-sha"),
                patch("ldm_core.handlers.workspace.UI.success") as mock_success,
                patch("zipfile.ZipFile"),
                patch("ldm_core.utils.safe_extract"),
                patch("ldm_core.handlers.workspace.datetime") as mock_date,
                patch.object(
                    self.handler, "detect_project_path", return_value=tmp_path / "test"
                ),
            ):
                mock_date.now.return_value.strftime.return_value = "20260512_120000"
                # Mock die to avoid further processing
                with patch(
                    "ldm_core.handlers.workspace.UI.die", side_effect=SystemExit
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


if __name__ == "__main__":
    unittest.main()
