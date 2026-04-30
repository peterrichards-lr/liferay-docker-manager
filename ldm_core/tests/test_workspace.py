import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from ldm_core.handlers.assets import AssetHandler
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerHandler
from ldm_core.handlers.diagnostics import DiagnosticsHandler
from ldm_core.handlers.runtime import RuntimeHandler
from ldm_core.handlers.workspace import WorkspaceHandler


class MockWorkspaceManager(
    ComposerHandler,
    RuntimeHandler,
    AssetHandler,
    WorkspaceHandler,
    DiagnosticsHandler,
    BaseHandler,
):
    def __init__(self):
        self.verbose = False
        self.non_interactive = True
        self.args = MagicMock()
        # Default host_name to localhost to avoid gethostbyname calls
        self.args.host_name = "localhost"
        self.args.ssl = False

    def _check_java_version(self, *args, **kwargs):
        return True

    def _check_gradle_java_version(self, *args, **kwargs):
        return True

    def verify_runtime_environment(self, *args, **kwargs):
        pass

    def _hydrate_from_workspace(self, *args, **kwargs):
        pass

    def detect_project_path(self, project_name, for_init=False):
        # This will be patched in the test
        pass

    def setup_paths(self, project_path):
        root = Path(project_path)
        return {
            "root": root,
            "files": root / "files",
            "configs": root / "osgi" / "configs",
            "ce_dir": root / "client-extensions",
            "modules": root / "osgi" / "modules",
            "cx": root / "osgi" / "client-extensions",
            "deploy": root / "deploy",
            "marketplace": root / "osgi" / "marketplace",
        }

    def check_mkcert(self, *args, **kwargs):
        pass

    def read_meta(self, *args, **kwargs):
        return {}

    def write_meta(self, *args, **kwargs):
        pass

    def _ensure_seeded(self, *args, **kwargs):
        return False

    def check_hostname(self, *args, **kwargs):
        return True

    def cmd_run(self, *args, **kwargs):
        pass


class TestWorkspaceMetadata(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    def test_parse_lcp_json_basic(self):
        content = json.dumps({"id": "my-service", "kind": "deployment", "memory": 1024})
        info = self.handler._parse_lcp_json(content)
        self.assertEqual(info["id"], "my-service")
        self.assertEqual(info["kind"], "Deployment")
        self.assertEqual(info["memory"], 1024)
        self.assertTrue(info["deploy"])  # Default should be True

    def test_parse_lcp_json_with_deploy_false(self):
        content = json.dumps({"id": "test", "deploy": False})
        info = self.handler._parse_lcp_json(content)
        self.assertFalse(info["deploy"])

    def test_parse_lcp_json_target_port(self):
        content = json.dumps(
            {"id": "microservice", "loadBalancer": {"targetPort": 3001}}
        )
        info = self.handler._parse_lcp_json(content)
        self.assertEqual(info["loadBalancer"]["targetPort"], 3001)
        self.assertTrue(info["has_load_balancer"])

    def test_parse_lcp_json_external_port(self):
        content = json.dumps({"id": "web", "ports": [{"port": 80, "external": True}]})
        info = self.handler._parse_lcp_json(content)
        self.assertTrue(info["has_load_balancer"])

    def test_merge_info_logic(self):
        # Initial info template
        info = {
            "id": "base",
            "kind": "Deployment",
            "deploy": True,
            "loadBalancer": None,
            "env": {"FOO": "BAR"},
        }

        # New info to merge (e.g. from LCP.json)
        new_info = {
            "id": "overridden",
            "loadBalancer": {"targetPort": 3001},
            "env": {"BAZ": "QUX"},
        }

        # Manually trigger the merge logic (we're testing the logic inside _scan_extension_metadata)
        # Using a closure helper similar to the real one
        def merge(target: Any, source: Any):
            for k, val in target.items():
                if source.get(k) is not None:
                    if k == "loadBalancer" and source[k]:
                        if val is None:
                            target[k] = {}
                        target[k].update(source[k])
                    elif k == "env":
                        target[k].update(source[k])
                    else:
                        target[k] = source[k]

        merge(info, new_info)

        self.assertEqual(info["id"], "overridden")
        self.assertEqual(info["loadBalancer"]["targetPort"], 3001)
        self.assertEqual(info["env"]["FOO"], "BAR")
        self.assertEqual(info["env"]["BAZ"], "QUX")


class TestWorkspaceImport(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    @patch("ldm_core.handlers.runtime.RuntimeHandler.cmd_run")
    @patch("ldm_core.handlers.workspace.run_command")
    def test_cmd_import_project_id_passing(self, mock_run, mock_cmd_run):
        # Use real temporary directories to avoid mock-related isinstance failures
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "source-workspace"
            source_dir.mkdir()
            (source_dir / "gradle.properties").write_text(
                "liferay.workspace.product=portal-7.4.13-u100"
            )

            # Define where the project will be "created"
            project_dir = base_path / "ldm-projects" / "my-dev-stack"

            self.handler.args.project = "my-dev-stack"
            self.handler.args.no_run = False
            self.handler.args.build = False
            self.handler.args.project_flag = None
            self.handler.args.host_name = "localhost"
            self.handler.args.ssl = False

            with patch.object(
                self.handler, "detect_project_path", return_value=project_dir
            ):
                with patch("ldm_core.handlers.workspace.UI"):
                    self.handler.cmd_import(str(source_dir))

                    # CRITICAL CHECK: self.args.project must be the short name, NOT the absolute path
                    # This verifies the fix for the user's reported issue
                    self.assertEqual(self.handler.args.project, "my-dev-stack")
                    self.assertNotEqual(self.handler.args.project, str(project_dir))

    @patch("ldm_core.handlers.runtime.RuntimeHandler.cmd_run")
    @patch("ldm_core.handlers.workspace.run_command")
    def test_cmd_import_clean_option(self, mock_run, mock_cmd_run):
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "source-workspace"
            source_dir.mkdir()
            (source_dir / "gradle.properties").write_text(
                "liferay.workspace.product=portal-7.4.13-u100"
            )

            project_dir = base_path / "my-project"
            project_dir.mkdir()
            (project_dir / "old-file.txt").write_text("old")

            self.handler.args.project = "my-project"
            self.handler.args.no_run = True
            self.handler.args.build = False
            self.handler.non_interactive = False
            self.handler.args.host_name = "localhost"
            self.handler.args.ssl = False

            with patch.object(
                self.handler, "detect_project_path", return_value=project_dir
            ):
                with patch("ldm_core.handlers.workspace.UI") as mock_ui:
                    # Simulate user selecting 'C'
                    mock_ui.ask.return_value = "C"

                    self.handler.cmd_import(str(source_dir))

                    # Verify safe_rmtree was called (or just check if directory was recreated)
                    # If it was cleaned, the 'old-file.txt' should be gone.
                    self.assertFalse((project_dir / "old-file.txt").exists())
                    self.assertTrue(project_dir.exists())

    @patch("ldm_core.handlers.runtime.RuntimeHandler.cmd_run")
    @patch("ldm_core.handlers.workspace.run_command")
    def test_cmd_import_no_overwrite_option(self, mock_run, mock_cmd_run):
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "source-workspace"
            source_dir.mkdir()
            (source_dir / "gradle.properties").write_text(
                "liferay.workspace.product=portal-7.4.13-u100"
            )

            # Create a mock CX in the source
            ce_dir = source_dir / "client-extensions"
            ce_dir.mkdir()
            zip_path = ce_dir / "test-ext.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("LCP.json", "{}")

            project_dir = base_path / "my-project"
            project_dir.mkdir()

            # Create an existing CX in the destination with standard LDM structure
            cx_dest_dir = project_dir / "osgi" / "client-extensions"
            cx_dest_dir.mkdir(parents=True)
            existing_cx = cx_dest_dir / "test-ext.zip"
            existing_cx.write_text("ORIGINAL_CONTENT")

            self.handler.args.project = "my-project"
            self.handler.args.no_run = True
            self.handler.args.build = False
            self.handler.non_interactive = False
            self.handler.args.host_name = "localhost"
            self.handler.args.ssl = False

            with (
                patch.object(
                    self.handler, "detect_project_path", return_value=project_dir
                ),
                patch.object(
                    self.handler,
                    "setup_paths",
                    return_value={
                        "root": project_dir,
                        "ce_dir": cx_dest_dir,
                        "modules": project_dir / "osgi" / "modules",
                        "configs": project_dir / "osgi" / "configs",
                        "files": project_dir / "files",
                    },
                ),
                patch("ldm_core.handlers.workspace.UI") as mock_ui,
            ):
                # Simulate user selecting 'N' (Skip Existing)
                mock_ui.ask.return_value = "N"

                self.handler.cmd_import(str(source_dir))

                # Verify the original content was PRESERVED
                self.assertEqual(existing_cx.read_text(), "ORIGINAL_CONTENT")


if __name__ == "__main__":
    unittest.main()
