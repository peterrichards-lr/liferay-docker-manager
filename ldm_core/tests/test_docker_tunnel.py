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

import socket
import subprocess  # nosec B404 - fixed argv, no shell
import sys
import time
import unittest
from types import SimpleNamespace

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
