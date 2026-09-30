"""LDM-#1993: one SSH connection per run, not one per Docker command.

Docker's SSH connection helper opens a fresh connection for every `docker`
invocation, and LDM issues ~85 of them. On the reporting deployment's node that
was 478 authentications in 30 hours against sshd's default `MaxStartups
10:30:100`, producing 714 throttling lines and connections dropped outright.

`DockerSshTunnel` replaces that with a single port-forward. The tests below
cover the parts that can fail silently:

* the **cleanup contract** -- the remote `cat > /dev/null` reading a pipe this
  process holds is what makes an un-trappable `SIGKILL` leave no orphan. It is
  one string in an argv list, it looks decorative, and deleting it would break
  crash cleanup while every other test still passed.
* `ExitOnForwardFailure` -- without it ssh runs happily with no forward and the
  failure surfaces much later as a connection refused against localhost, which
  reads as a Docker fault rather than an SSH one.
* the keepalive -- deliberately present, and deliberately absent before this
  change. See the module docstring for why the same option was the wrong fix
  when there was no long-lived connection to keep alive.

The real end-to-end behaviour (docker reaching a remote daemon over the
forward, and `kill -9` leaving no orphan) was verified against a live Linux
sshd; `test_it_exits_when_its_stdin_closes` keeps the half of that contract
which needs no sshd.
"""

import os
import socket
import subprocess  # nosec B404 - fixed argv, no shell
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ldm_core.docker_tunnel import (
    DockerSshTunnel,
    DockerTunnelError,
    _free_loopback_port,
)


def _target(name="node-a", host="10.0.0.5", user="ec2-user", key_path=None):
    return SimpleNamespace(name=name, host=host, user=user, key_path=key_path)


class FreePortTests(unittest.TestCase):
    def test_it_returns_a_port_that_can_actually_be_bound(self):
        port = _free_loopback_port()
        self.assertTrue(0 < port < 65536)
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))  # would raise if not genuinely free
        finally:
            s.close()


class SshCommandTests(unittest.TestCase):
    """The argv IS the contract -- every clause here has a failure it prevents."""

    def test_it_keeps_the_remote_command_that_makes_crash_cleanup_work(self):
        # The pipe-EOF contract. Without a remote command reading stdin, ssh
        # has no reason to exit when LDM is SIGKILLed, and the tunnel is
        # orphaned with the forwarded port still held.
        cmd = DockerSshTunnel(_target())._ssh_command(12345)
        self.assertEqual(
            cmd[-1],
            "cat > /dev/null",
            "the remote stdin-reading command is the crash-cleanup guarantee",
        )

    def test_it_forwards_loopback_only_to_the_remote_docker_socket(self):
        cmd = DockerSshTunnel(_target())._ssh_command(12345)
        self.assertIn("-L", cmd)
        forward = cmd[cmd.index("-L") + 1]
        self.assertEqual(forward, "127.0.0.1:12345:/var/run/docker.sock")
        # Not 0.0.0.0: the forwarded port grants access to the node's Docker
        # daemon, so binding it to anything routable would publish that.
        self.assertTrue(forward.startswith("127.0.0.1:"))

    def test_it_refuses_to_run_without_the_forward(self):
        cmd = DockerSshTunnel(_target())._ssh_command(12345)
        self.assertIn("ExitOnForwardFailure=yes", cmd)

    def test_it_sets_the_keepalive_that_now_has_something_to_keep_alive(self):
        cmd = DockerSshTunnel(_target())._ssh_command(12345)
        self.assertIn("ServerAliveInterval=30", cmd)
        self.assertIn("ServerAliveCountMax=6", cmd)

    def test_it_passes_the_identity_file_when_the_target_has_one(self):
        cmd = DockerSshTunnel(_target(key_path="/k/id_rsa"))._ssh_command(1)
        self.assertIn("-i", cmd)
        self.assertEqual(cmd[cmd.index("-i") + 1], "/k/id_rsa")
        self.assertNotIn("-i", DockerSshTunnel(_target())._ssh_command(1))

    def test_it_omits_the_user_when_the_target_has_none(self):
        self.assertIn("ec2-user@10.0.0.5", DockerSshTunnel(_target())._ssh_command(1))
        bare = DockerSshTunnel(_target(user=None))._ssh_command(1)
        self.assertIn("10.0.0.5", bare)
        self.assertNotIn("@10.0.0.5", " ".join(bare))

    def test_it_never_enables_a_shell(self):
        # Bandit B603/B605: the argv is fixed and the host comes from stored
        # target config, but a shell here would make it injectable.
        cmd = DockerSshTunnel(_target(host="a; rm -rf /"))._ssh_command(1)
        self.assertIsInstance(cmd, list)
        self.assertIn("ec2-user@a; rm -rf /", cmd)  # one argv element, not parsed


class LifecycleTests(unittest.TestCase):
    def test_docker_host_is_none_until_started(self):
        self.assertIsNone(DockerSshTunnel(_target()).docker_host)

    def test_stop_is_safe_before_start_and_is_idempotent(self):
        t = DockerSshTunnel(_target())
        t.stop()
        t.stop()
        self.assertFalse(t.is_alive())

    def test_it_diagnoses_a_node_it_cannot_reach_rather_than_hanging(self):
        # Port 1 on loopback refuses immediately, so ssh exits at once. The
        # readiness poll must notice the dead process instead of waiting out
        # the full timeout -- a 25s hang per command would be worse than the
        # bug this module fixes.
        t = DockerSshTunnel(_target(host="127.0.0.1", user=None), ready_timeout=15.0)
        started = time.monotonic()
        with self.assertRaises(DockerTunnelError) as caught:
            t.start()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 14.0, "gave up on the timeout, not on ssh exiting")
        self.assertIn("node-a", str(caught.exception))
        self.assertTrue(caught.exception.reason)
        self.assertFalse(t.is_alive())

    def test_it_exits_when_its_stdin_closes(self):
        """The crash-cleanup guarantee, without needing an sshd.

        This is the behaviour `cat > /dev/null` buys: a child holding a pipe
        from this process exits the moment that pipe closes. Verified against a
        real `ssh` and a real `kill -9` outside the suite; here it is pinned
        with a stand-in so a regression is caught in CI.
        """
        child = subprocess.Popen(  # nosec B603 - fixed argv, no shell
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
        )
        self.assertIsNone(child.poll(), "child should be waiting on stdin")
        # Asserted rather than assumed: if Popen had not given us a pipe there
        # would be nothing to close, and the test would pass for the wrong
        # reason -- the child would exit on its own EOF.
        self.assertIsNotNone(child.stdin, "asked Popen for a stdin pipe")
        if child.stdin:
            child.stdin.close()
        self.assertEqual(
            child.wait(timeout=10), 0, "child did not exit when its stdin closed"
        )


if __name__ == "__main__":
    unittest.main()


class TunnelIsReachableFromTheCLI(unittest.TestCase):
    """LDM-#1993: the wiring, which #2004 shipped without.

    `DockerSshTunnel` landed with no switch and no call site, so it was
    unreachable from the CLI. Anyone setting the variable would have measured
    a feature that never ran and read the unchanged accept count as the
    tunnel having failed.
    """

    def setUp(self):
        from ldm_core import docker_tunnel

        docker_tunnel._TUNNELS.clear()
        docker_tunnel._DEAD_REPORTED.clear()
        self.addCleanup(docker_tunnel._TUNNELS.clear)
        self.addCleanup(docker_tunnel._DEAD_REPORTED.clear)

        # No test here may spawn a real ssh. One did, briefly, through a
        # target-name mismatch: the registry entry was keyed on a different
        # name, so the lookup missed and `active_tunnel` opened a genuine
        # tunnel and sat out the 25s readiness timeout. A guard is cheaper
        # than noticing a slow suite.
        def _never(*_a, **_k):
            raise AssertionError(
                "a test tried to open a real SSH tunnel -- check the target "
                "name matches the registry key"
            )

        starter = patch.object(DockerSshTunnel, "start", _never)
        starter.start()
        self.addCleanup(starter.stop)

    def test_it_is_off_unless_the_variable_is_set(self):
        from ldm_core.docker_tunnel import active_tunnel, tunnelling_enabled

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(tunnelling_enabled())
            self.assertIsNone(active_tunnel(_target(name="aws-1")))

    def test_the_variable_is_read_tolerantly(self):
        from ldm_core.docker_tunnel import tunnelling_enabled

        for value in ("1", "true", "TRUE", " yes ", "on"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"LDM_DOCKER_TUNNEL": value}),
            ):
                self.assertTrue(tunnelling_enabled())
        for value in ("", "0", "false", "no"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"LDM_DOCKER_TUNNEL": value}),
            ):
                self.assertFalse(tunnelling_enabled())

    def test_a_remote_target_routes_through_the_tunnel_when_enabled(self):
        """The whole point: `--host`, not `--context`, so one connection."""
        from ldm_core.docker_service import DockerService

        fake = MagicMock()
        fake.docker_host = "tcp://127.0.0.1:54321"
        with (
            patch.dict(os.environ, {"LDM_DOCKER_TUNNEL": "1"}),
            patch(
                "ldm_core.docker_service.get_active_target",
                return_value=_target(name="aws-1"),
            ),
            patch("ldm_core.docker_tunnel.active_tunnel", return_value=fake),
        ):
            cmd = DockerService.get_docker_cmd_prefix("aws-1")
        self.assertEqual(cmd, ["docker", "--host", "tcp://127.0.0.1:54321"])
        self.assertNotIn("--context", cmd)

    def test_without_the_variable_the_context_path_is_untouched(self):
        from ldm_core.docker_service import DockerService

        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "ldm_core.docker_service.get_active_target",
                return_value=_target(name="aws-1"),
            ),
        ):
            cmd = DockerService.get_docker_cmd_prefix("aws-1")
        self.assertEqual(cmd, ["docker", "--context", "aws-1"])

    def test_a_tunnel_is_opened_once_and_reused(self):
        """N connections become 1 -- reuse is the entire mechanism."""
        from ldm_core import docker_tunnel

        started = []

        class _Fake:
            docker_host = "tcp://127.0.0.1:1"

            def start(self):
                started.append(1)
                return self.docker_host

            def is_alive(self):
                return True

        with (
            patch.dict(os.environ, {"LDM_DOCKER_TUNNEL": "1"}),
            patch.object(docker_tunnel, "DockerSshTunnel", lambda *_a, **_k: _Fake()),
        ):
            for _ in range(5):
                docker_tunnel.active_tunnel(_target(name="aws-1"))
        self.assertEqual(len(started), 1, "a tunnel was opened per call")

    def test_a_tunnel_that_dies_is_reported_as_a_tunnel(self):
        """Requirement 4: "established then died", named as itself.

        Otherwise the next command fails against a loopback port nothing is
        listening on and the user is told `Docker not accessible` -- a
        symptom naming the wrong subject.
        """
        from ldm_core import docker_tunnel

        dead = MagicMock()
        dead.is_alive.return_value = False
        dead._proc = None
        docker_tunnel._TUNNELS["aws-1"] = dead

        with (
            patch.dict(os.environ, {"LDM_DOCKER_TUNNEL": "1"}),
            patch("ldm_core.docker_tunnel.UI.error") as err,
            patch("ldm_core.docker_tunnel.UI.detail") as detail,
        ):
            result = docker_tunnel.active_tunnel(_target(name="aws-1"))

        self.assertIsNone(result, "a dead tunnel must not be handed out")
        err.assert_called_once()
        message = err.call_args[0][0]
        self.assertIn("SSH tunnel", message)
        self.assertIn("aws-1", message)
        self.assertIn(
            "detected at", message, "we know when we noticed, not when it died"
        )
        notes = " ".join(str(c[0][0]) for c in detail.call_args_list)
        self.assertIn("established tunnel that died", notes)

    def test_the_death_is_reported_once_not_per_command(self):
        from ldm_core import docker_tunnel

        dead = MagicMock()
        dead.is_alive.return_value = False
        dead._proc = None

        with (
            patch.dict(os.environ, {"LDM_DOCKER_TUNNEL": "1"}),
            patch("ldm_core.docker_tunnel.UI.error") as err,
            patch("ldm_core.docker_tunnel.UI.detail"),
        ):
            for _ in range(3):
                docker_tunnel._TUNNELS["aws-1"] = dead
                docker_tunnel.active_tunnel(_target(name="aws-1"))
        err.assert_called_once()

    def test_a_dead_tunnel_falls_back_rather_than_failing_the_run(self):
        """A slower run beats no run."""
        from ldm_core.docker_service import DockerService

        with (
            patch.dict(os.environ, {"LDM_DOCKER_TUNNEL": "1"}),
            patch(
                "ldm_core.docker_service.get_active_target",
                return_value=_target(name="aws-1"),
            ),
            patch("ldm_core.docker_tunnel.active_tunnel", return_value=None),
        ):
            cmd = DockerService.get_docker_cmd_prefix("aws-1")
        self.assertEqual(cmd, ["docker", "--context", "aws-1"])
