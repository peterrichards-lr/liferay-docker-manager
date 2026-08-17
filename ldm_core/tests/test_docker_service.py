import unittest
from unittest.mock import patch

from ldm_core.docker_service import DockerService


class TestDockerService(unittest.TestCase):
    def setUp(self):
        # Isolate every test in this class from whatever persisted default
        # target a *real* ~/.ldmrc on the machine running the tests happens
        # to have -- these tests exercise Docker command construction, not
        # target resolution (that's covered by the dedicated
        # get_*_cmd_prefix tests below, which override this per-test via
        # their own @patch). Without this, get_docker_cmd_prefix() now
        # correctly consulting get_active_target(None) for every call means
        # these would silently pick up e.g. a tester's own persisted "aws-2"
        # default instead of "local".
        from ldm_core.config import TargetNode

        self.target_patcher = patch(
            "ldm_core.docker_service.get_active_target",
            return_value=TargetNode(name="local", host="localhost", is_default=True),
        )
        self.target_patcher.start()

    def tearDown(self):
        self.target_patcher.stop()

    @patch("ldm_core.docker_service.run_command")
    def test_exists_true(self, mock_run):
        mock_run.return_value = "container_id_123\n"
        self.assertTrue(DockerService.exists("test-container"))
        mock_run.assert_called_with(
            ["docker", "ps", "-a", "-q", "-f", "name=^test-container$"], check=False
        )

    @patch("ldm_core.docker_service.run_command")
    def test_exists_false(self, mock_run):
        mock_run.return_value = ""
        self.assertFalse(DockerService.exists("test-container"))

    @patch("ldm_core.docker_service.run_command")
    def test_is_running_true(self, mock_run):
        mock_run.return_value = "container_id_123\n"
        self.assertTrue(DockerService.is_running("test-container"))
        mock_run.assert_called_with(
            ["docker", "ps", "-q", "-f", "name=^test-container$"], check=False
        )

    @patch("ldm_core.docker_service.run_command")
    def test_is_running_false(self, mock_run):
        mock_run.return_value = ""
        self.assertFalse(DockerService.is_running("test-container"))

    @patch("ldm_core.docker_service.run_command")
    def test_get_status_running(self, mock_run):
        mock_run.return_value = "running\n"
        self.assertEqual(DockerService.get_status("test-container"), "running")
        mock_run.assert_called_with(
            ["docker", "inspect", "-f", "{{.State.Status}}", "test-container"],
            check=False,
        )

    @patch("ldm_core.docker_service.run_command")
    def test_get_status_unknown(self, mock_run):
        mock_run.return_value = ""
        self.assertEqual(DockerService.get_status("test-container"), "unknown")

    @patch("ldm_core.docker_service.run_command")
    def test_get_health_healthy(self, mock_run):
        mock_run.return_value = "healthy\n"
        self.assertEqual(DockerService.get_health("test-container"), "healthy")
        mock_run.assert_called_with(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", "test-container"],
            check=False,
        )

    @patch("ldm_core.docker_service.run_command")
    def test_get_health_unknown(self, mock_run):
        mock_run.return_value = ""
        self.assertEqual(DockerService.get_health("test-container"), "unknown")

    @patch("ldm_core.docker_service.run_command")
    def test_stop(self, mock_run):
        mock_run.return_value = "test-container"
        res = DockerService.stop("test-container")
        self.assertEqual(res, "test-container")
        mock_run.assert_called_with(
            ["docker", "stop", "test-container"], check=False, capture_output=True
        )

    @patch("ldm_core.docker_service.run_command")
    def test_rm_without_force(self, mock_run):
        mock_run.return_value = "test-container"
        res = DockerService.rm("test-container")
        self.assertEqual(res, "test-container")
        mock_run.assert_called_with(
            ["docker", "rm", "test-container"], check=False, capture_output=True
        )

    @patch("ldm_core.docker_service.run_command")
    def test_rm_with_force(self, mock_run):
        mock_run.return_value = "test-container"
        res = DockerService.rm("test-container", force=True)
        self.assertEqual(res, "test-container")
        mock_run.assert_called_with(
            ["docker", "rm", "-f", "test-container"], check=False, capture_output=True
        )

    @patch("ldm_core.docker_service.run_command")
    def test_start(self, mock_run):
        mock_run.return_value = "test-container"
        res = DockerService.start("test-container")
        self.assertEqual(res, "test-container")
        mock_run.assert_called_with(
            ["docker", "start", "test-container"], check=False, capture_output=True
        )

    @patch("ldm_core.docker_service.run_command")
    def test_exec(self, mock_run):
        mock_run.return_value = "success"
        res = DockerService.exec("test-container", ["echo", "hello"])
        self.assertEqual(res, "success")
        mock_run.assert_called_with(
            ["docker", "exec", "test-container", "echo", "hello"],
            check=False,
            capture_output=True,
        )

    @patch("ldm_core.docker_service.run_command")
    def test_get_logs(self, mock_run):
        mock_run.return_value = "logs"
        res = DockerService.get_logs("test-container", tail=50)
        self.assertEqual(res, "logs")
        mock_run.assert_called_with(
            ["docker", "logs", "--tail", "50", "test-container"],
            check=False,
            capture_output=True,
        )

    @patch("ldm_core.docker_service.get_active_target")
    def test_get_docker_cmd_prefix_local(self, mock_get_target):
        from ldm_core.config import TargetNode

        mock_get_target.return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )
        prefix = DockerService.get_docker_cmd_prefix()
        self.assertEqual(prefix, ["docker"])
        # get_active_target must actually be consulted even with no
        # explicit target_name -- see test_get_docker_cmd_prefix_honors_
        # persisted_default_when_none for the regression this guards.
        mock_get_target.assert_called_once_with(None)

    @patch("ldm_core.docker_service.get_active_target")
    def test_get_docker_cmd_prefix_honors_persisted_default_when_none(
        self, mock_get_target
    ):
        """A falsy/None target_name used to short-circuit straight to
        ["docker"] *before* ever calling get_active_target() -- silently
        ignoring a persisted default target (`ldm target use`) for every
        caller that only knows a possibly-unset target. get_active_target()
        must always be consulted so its own persisted-default fallback can
        run."""
        from ldm_core.config import TargetNode

        mock_get_target.return_value = TargetNode(name="aws-2", host="5.6.7.8")
        prefix = DockerService.get_docker_cmd_prefix(None)
        self.assertEqual(prefix, ["docker", "--context", "aws-2"])

    @patch("ldm_core.docker_service.get_active_target")
    def test_get_docker_cmd_prefix_remote(self, mock_get_target):
        from ldm_core.config import TargetNode

        mock_get_target.return_value = TargetNode(name="win-wsl", host="192.168.1.50")
        prefix = DockerService.get_docker_cmd_prefix("win-wsl")
        self.assertEqual(prefix, ["docker", "--context", "win-wsl"])

    @patch("ldm_core.docker_service.get_active_target")
    def test_get_compose_cmd_prefix_remote(self, mock_get_target):
        from ldm_core.config import TargetNode

        mock_get_target.return_value = TargetNode(name="aws-ec2", host="34.200.1.1")
        prefix = DockerService.get_compose_cmd_prefix("aws-ec2")
        self.assertEqual(prefix, ["docker", "--context", "aws-ec2", "compose"])


if __name__ == "__main__":
    unittest.main()
