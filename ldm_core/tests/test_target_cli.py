import argparse
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.cli import get_parser
from ldm_core.diagnostics.info import run_info, run_list
from ldm_core.manager import LiferayManager
from ldm_core.tests.tmproot import TEST_TMP_ROOT


class TestTargetCLI(unittest.TestCase):
    def test_cli_target_and_node_parsing(self) -> None:
        parser, _ = get_parser()
        args1 = parser.parse_args(["run", "vanilla", "--target", "aws-1"])
        self.assertEqual(getattr(args1, "target", None), "aws-1")

        args2 = parser.parse_args(["run", "vanilla", "--node", "aws-1"])
        self.assertEqual(getattr(args2, "target", None), "aws-1")

        args3 = parser.parse_args(["--target", "aws-1", "run", "vanilla"])
        self.assertEqual(getattr(args3, "target", None), "aws-1")

        args4 = parser.parse_args(["logs", "vanilla", "--target", "prod-node"])
        self.assertEqual(getattr(args4, "target", None), "prod-node")

    def test_manager_target_initialization(self) -> None:
        args = argparse.Namespace(
            target="aws-1",
            node=None,
            verbose=False,
            info=False,
            quiet=False,
            non_interactive=False,
            dry_run=False,
        )
        mgr = LiferayManager(args)
        self.assertEqual(mgr.target, "aws-1")

    def test_manager_broadened_ci_detection(self) -> None:
        # LDM-#1092: previously only CI/GITHUB_ACTIONS/GITLAB_CI were
        # recognized -- an automation harness running under any other CI
        # provider fell through to sys.stdout.isatty(), which behaves
        # unpredictably depending on how the harness captures output,
        # causing interactive prompts to fire when the caller never
        # wanted them to.
        args = argparse.Namespace(
            target=None,
            node=None,
            verbose=False,
            info=False,
            quiet=False,
            non_interactive=False,
            dry_run=False,
        )
        # Force-clear the 3 originally-recognized vars (empty string is
        # falsy in the `os.getenv(x) or ...` chain) so this test actually
        # proves the broadened list, rather than passing trivially because
        # the real CI runner already sets CI/GITHUB_ACTIONS.
        base_env = dict.fromkeys(("CI", "GITHUB_ACTIONS", "GITLAB_CI"), "")

        for var in (
            "CIRCLECI",
            "TRAVIS",
            "APPVEYOR",
            "JENKINS_URL",
            "BUILD_NUMBER",
            "TEAMCITY_VERSION",
            "BUILDKITE",
            "DRONE",
            "TF_BUILD",
            "CODEBUILD_BUILD_ID",
            "BITBUCKET_BUILD_NUMBER",
            "SEMAPHORE",
            "bamboo_buildKey",
            "GO_PIPELINE_LABEL",
            "CONTINUOUS_INTEGRATION",
        ):
            with patch.dict("os.environ", {**base_env, var: "true"}):
                mgr = LiferayManager(args)
                self.assertTrue(
                    mgr.non_interactive, f"{var} should trigger non_interactive"
                )

    def test_config_edit_target_file_exclusion(self) -> None:
        args = argparse.Namespace(
            target="properties",
            node=None,
            verbose=False,
            info=False,
            quiet=False,
            non_interactive=False,
            dry_run=False,
        )
        mgr = LiferayManager(args)
        self.assertIsNone(mgr.target)

    @patch("ldm_core.diagnostics.info.UI")
    @patch("ldm_core.docker_service.DockerService.get_status")
    def test_run_info_target_reporting(
        self, mock_get_status: MagicMock, mock_ui: MagicMock
    ) -> None:
        mock_get_status.return_value = "running"
        handler = MagicMock()
        handler.manager.detect_project_path.return_value = Path("/tmp/vanilla")
        handler.manager.read_meta.return_value = {
            "target": "aws-1",
            "host_name": "vanilla.local",
            "container_name": "vanilla",
        }
        handler.manager.parse_version.return_value = (7, 4, 3)
        handler.manager.target = "aws-1"

        run_info(handler, "vanilla")
        mock_get_status.assert_called_once_with("vanilla", target_name="aws-1")

    @patch("ldm_core.diagnostics.info.UI")
    @patch("ldm_core.diagnostics.info.run_command")
    def test_run_list_target_column(
        self, mock_run_command: MagicMock, mock_ui: MagicMock
    ) -> None:
        mock_run_command.return_value = "running"
        handler = MagicMock()
        handler.manager.find_dxp_roots.return_value = [
            {"path": f"{TEST_TMP_ROOT}/proj1", "version": "2024.q1.3"}
        ]
        handler.manager.read_meta.return_value = {
            "container_name": "proj1",
            "target": "aws-1",
            "host_name": "localhost",
            "port": 8080,
            "ssl": False,
        }

        run_list(handler)
        self.assertTrue(mock_ui.table.called)
        headers = mock_ui.table.call_args[1].get("headers")
        self.assertIn("Target", headers)


if __name__ == "__main__":
    unittest.main()
