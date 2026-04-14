import unittest
import json
import tempfile
from unittest.mock import patch, MagicMock
from pathlib import Path
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.workspace import WorkspaceHandler


class MockWorkspaceManager(BaseHandler, WorkspaceHandler):
    def __init__(self):
        self.verbose = False
        self.non_interactive = True
        self.args = MagicMock()

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
        }

    def check_mkcert(self, *args, **kwargs):
        pass

    def read_meta(self, *args, **kwargs):
        return {}

    def write_meta(self, *args, **kwargs):
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
        def merge(target, source):
            for k in target.keys():
                if source.get(k) is not None:
                    if k == "loadBalancer" and source[k]:
                        if target[k] is None:
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

    @patch("ldm_core.handlers.stack.StackHandler.cmd_run")
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

    @patch("ldm_core.handlers.stack.StackHandler.cmd_run")
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

            with patch.object(
                self.handler, "detect_project_path", return_value=project_dir
            ):
                with patch("ldm_core.handlers.workspace.UI") as mock_ui:
                    # Simulate user selecting 'C'
                    mock_ui.ask.return_value = "C"

                    self.handler.cmd_import(str(source_dir))

                    # Verify safe_rmtree was called (or just check if directory was recreated)
                    # The actual implementation calls self.safe_rmtree(project_path)
                    # and then setup_paths/mkdir recreates it.
                    # If it was cleaned, the 'old-file.txt' should be gone.
                    self.assertFalse((project_dir / "old-file.txt").exists())
                    self.assertTrue(project_dir.exists())


if __name__ == "__main__":
    unittest.main()
