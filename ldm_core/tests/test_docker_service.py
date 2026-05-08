import unittest
from unittest.mock import patch

from ldm_core.docker_service import DockerService


class TestDockerService(unittest.TestCase):
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
        mock_run.return_value = "log line 1\nlog line 2"
        res = DockerService.get_logs("test-container", tail=50)
        self.assertEqual(res, "log line 1\nlog line 2")
        mock_run.assert_called_with(
            ["docker", "logs", "--tail", "50", "test-container"],
            check=False,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
