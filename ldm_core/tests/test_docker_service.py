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
    def test_published_host_ports_parses_docker_ps(self, mock_run):
        """LDM-#1417: the allocator, not a socket, decides a container-held port.

        The socket probes cannot answer this on Windows -- a bind to
        127.0.0.1:P succeeds while Docker Desktop holds 0.0.0.0:P, and a
        connect to a published port was measured taking 1-3s to accept, so a
        probe short enough to run on every check times out and calls it free.
        """
        mock_run.return_value = (
            "0.0.0.0:5601->80/tcp, [::]:5601->80/tcp\n"
            "0.0.0.0:8080->8080/tcp, [::]:8080->8080/tcp\n"
        )
        self.assertEqual(DockerService.published_host_ports(), {5601, 8080})
        mock_run.assert_called_with(
            ["docker", "ps", "--format", "{{.Ports}}"], check=False
        )

    @patch("ldm_core.docker_service.run_command")
    def test_published_host_ports_ignores_unpublished_ports(self, mock_run):
        """A container port with no host mapping is not a host port."""
        mock_run.return_value = "80/tcp, 443/tcp\n0.0.0.0:9200->9200/tcp\n"
        self.assertEqual(DockerService.published_host_ports(), {9200})

    @patch("ldm_core.docker_service.run_command")
    def test_published_host_ports_empty_when_docker_unreachable(self, mock_run):
        """No answer is not the same as "free".

        Returning an empty set leaves the verdict to the socket probes rather
        than silently declaring every port available.
        """
        mock_run.return_value = ""
        self.assertEqual(DockerService.published_host_ports(), set())
        mock_run.return_value = None
        self.assertEqual(DockerService.published_host_ports(), set())

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

    @patch("ldm_core.docker_service.get_active_target")
    def test_get_docker_cmd_prefix_loopback(self, mock_get_target):
        """Loopback target IPs (127.0.0.0/8 and ::1) must return plain docker prefix without --context."""
        from ldm_core.config import TargetNode

        for loopback_ip in ("127.0.0.1", "127.0.0.2", "127.0.1.1", "::1"):
            mock_get_target.return_value = TargetNode(
                name="local-node", host=loopback_ip
            )
            prefix = DockerService.get_docker_cmd_prefix("local-node")
            self.assertEqual(prefix, ["docker"])

    # --- LDM-#1242: Gogo shell helper -------------------------------------

    # Verbatim shape of a real Gogo rejection, captured from
    # liferay/dxp:2026.q1.12-lts.
    _GOGO_REJECTION = (
        "Trying 127.0.0.1...\n"
        "Connected to localhost.\n"
        "Escape character is '^]'.\n"
        "____________________________\n"
        "Welcome to Apache Felix Gogo\n"
        "\n"
        "g! gogo: IOException: no matches found: "
        "com.liferay.portal.kernel.cache.MultiVMPoolUtil.clear()\n"
        "g! Connection closed by foreign host.\n"
    )

    _GOGO_SUCCESS = (
        "Trying 127.0.0.1...\n"
        "Connected to localhost.\n"
        "Escape character is '^]'.\n"
        "____________________________\n"
        "Welcome to Apache Felix Gogo\n"
        "\n"
        "g! START LEVEL 20\n"
        "   ID|State      |Level|Symbolic name\n"
        "    0|Active     |    0|org.eclipse.osgi (3.13.0)|3.13.0\n"
    )

    @patch("ldm_core.docker_service.run_command")
    def test_gogo_holds_pipe_open_for_reply(self, mock_run):
        """The command must keep stdin open, or Gogo's reply is never read.

        A bare `echo 'cmd' | telnet` closes stdin immediately and telnet tears
        the socket down before Gogo writes anything back -- the caller gets only
        the connection banner.
        """
        mock_run.return_value = self._GOGO_SUCCESS
        out, err = DockerService.gogo("liferay-1", "lb -s")

        self.assertIsNone(err)
        self.assertIn("|", out)

        shell_cmd = mock_run.call_args[0][0][-1]
        self.assertIn("sleep", shell_cmd)
        self.assertIn("telnet localhost 11311", shell_cmd)
        self.assertIn("lb -s", shell_cmd)

    @patch("ldm_core.docker_service.run_command")
    def test_gogo_detects_rejection_despite_zero_exit(self, mock_run):
        """A `gogo:` line means the command failed, even though telnet exits 0."""
        mock_run.return_value = self._GOGO_REJECTION
        _out, err = DockerService.gogo("liferay-1", "com.example.Foo.bar()")

        self.assertIsNotNone(err)
        self.assertIn("no matches found", err or "")

    @patch("ldm_core.docker_service.run_command")
    def test_gogo_handles_no_output(self, mock_run):
        """A silent container must not be mistaken for a successful command."""
        mock_run.return_value = None
        out, err = DockerService.gogo("liferay-1", "lb -s")

        self.assertEqual("", out)
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()


class TestContextEndpointHost(unittest.TestCase):
    """LDM-#1346: reading back what a Docker context actually dials."""

    def _endpoint(self, value):
        return patch("ldm_core.docker_service.run_command", return_value=value)

    def test_strips_scheme_and_user(self):
        with self._endpoint("ssh://ec2-user@13.49.210.78"):
            self.assertEqual(
                "13.49.210.78", DockerService.get_context_endpoint_host("aws-1")
            )

    def test_handles_an_endpoint_without_a_user(self):
        with self._endpoint("ssh://13.49.210.78"):
            self.assertEqual(
                "13.49.210.78", DockerService.get_context_endpoint_host("aws-1")
            )

    def test_strips_a_port(self):
        with self._endpoint("ssh://ec2-user@13.49.210.78:2222"):
            self.assertEqual(
                "13.49.210.78", DockerService.get_context_endpoint_host("aws-1")
            )

    def test_keeps_a_bracketed_ipv6_literal_intact(self):
        """Splitting an IPv6 address on ':' would truncate it to '['."""
        with self._endpoint("ssh://ec2-user@[2001:db8::1]:2222"):
            self.assertEqual(
                "[2001:db8::1]", DockerService.get_context_endpoint_host("aws-1")
            )

    def test_returns_none_when_the_context_has_no_endpoint(self):
        for value in ("", "   ", None):
            with self._endpoint(value):
                self.assertIsNone(DockerService.get_context_endpoint_host("aws-1"))
