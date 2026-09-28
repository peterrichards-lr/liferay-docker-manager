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
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from ldm_core.docker_tunnel import (
    TUNNEL_ENV_VAR,
    DockerSshTunnel,
    DockerTunnelClosedError,
    DockerTunnelError,
    _free_loopback_port,
    tunnel_enabled,
)
from ldm_core.utils import CommandRunner


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


class OptInGateTests(unittest.TestCase):
    """LDM-#1993: default-off has to mean *nothing changes*.

    This is the half of an opt-in that is never exercised deliberately and so
    is where a regression would hide: the feature is off for almost everyone,
    so a fault in the off-path ships quietly and breaks every remote command.
    """

    def setUp(self):
        os.environ.pop(TUNNEL_ENV_VAR, None)

    tearDown = setUp

    def test_it_is_off_unless_asked_for(self):
        self.assertFalse(tunnel_enabled())

    def test_it_accepts_the_spellings_a_ci_file_would_use(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            os.environ[TUNNEL_ENV_VAR] = value
            self.assertTrue(tunnel_enabled(), f"{value!r} should enable it")
        for value in ("0", "false", "no", "off", ""):
            os.environ[TUNNEL_ENV_VAR] = value
            self.assertFalse(tunnel_enabled(), f"{value!r} should not enable it")

    def test_the_command_is_untouched_when_the_flag_is_absent(self):
        cmd = ["docker", "--context", "aws-1", "ps"]
        env: dict[str, str] = {}
        result = CommandRunner._apply_docker_tunnel(list(cmd), env)
        self.assertEqual(result, cmd, "default path must not be rewritten")
        self.assertNotIn("DOCKER_HOST", env, "no tunnel, so nothing to point at")

    def test_a_local_command_is_untouched_even_when_enabled(self):
        os.environ[TUNNEL_ENV_VAR] = "1"
        cmd = ["docker", "ps"]  # no --context: nothing to reroute
        env: dict[str, str] = {}
        self.assertEqual(CommandRunner._apply_docker_tunnel(list(cmd), env), cmd)
        self.assertNotIn("DOCKER_HOST", env)


class TunnelRoutingTests(unittest.TestCase):
    """When enabled, BOTH halves must happen or the fix is a no-op."""

    def setUp(self):
        os.environ[TUNNEL_ENV_VAR] = "1"

    def tearDown(self):
        os.environ.pop(TUNNEL_ENV_VAR, None)

    def test_it_strips_the_context_and_points_docker_at_the_tunnel(self):
        fake = SimpleNamespace(docker_host="tcp://127.0.0.1:54321")
        env: dict[str, str] = {}
        with (
            mock.patch(
                "ldm_core.config.get_active_target", return_value=_target("aws-1")
            ),
            mock.patch("ldm_core.docker_tunnel.tunnel_for", return_value=fake),
        ):
            out = CommandRunner._apply_docker_tunnel(
                ["docker", "--context", "aws-1", "ps", "-a"], env
            )
        # Leaving --context in place would send Docker back down ssh:// --
        # the CLI prefers a context over the environment -- so the tunnel
        # would be opened and then bypassed, and the defect would persist
        # while every other sign said the feature was on.
        self.assertEqual(out, ["docker", "ps", "-a"])
        self.assertEqual(env["DOCKER_HOST"], "tcp://127.0.0.1:54321")

    def test_it_falls_back_to_context_when_the_target_is_local(self):
        env: dict[str, str] = {}
        with mock.patch(
            "ldm_core.config.get_active_target",
            return_value=SimpleNamespace(name="local", host="127.0.0.1"),
        ):
            out = CommandRunner._apply_docker_tunnel(
                ["docker", "--context", "local", "ps"], env
            )
        self.assertEqual(out, ["docker", "--context", "local", "ps"])
        self.assertNotIn("DOCKER_HOST", env)


class MidRunDeathTests(unittest.TestCase):
    """A tunnel that dies mid-run must name the tunnel, not a localhost port."""

    def test_it_reports_the_tunnel_when_it_dies_after_working(self):
        t = DockerSshTunnel(_target("aws-1"))
        t.established_at = datetime(2026, 9, 28, 14, 32, 5)
        err = t.closed_error()

        text = str(err)
        self.assertIsInstance(err, DockerTunnelClosedError)
        self.assertIn("tunnel", text.lower())
        self.assertIn("aws-1", text)
        self.assertIn("14:32", text, "the operator correlates this by time")
        # Silence from ssh is a fact, and differs from a reason nobody read.
        self.assertIn("no diagnostic available from ssh", text)
        # The failure it must NOT read as.
        self.assertNotIn("connection refused", text.lower())

    def test_never_established_and_died_are_different_types(self):
        # Chased differently: the first is credentials, host or firewall; the
        # second is a working connection that was lost or killed.
        self.assertTrue(issubclass(DockerTunnelClosedError, DockerTunnelError))
        self.assertFalse(
            isinstance(DockerTunnelError("never up"), DockerTunnelClosedError)
        )


if __name__ == "__main__":
    unittest.main()
