"""One SSH connection per run, instead of one per Docker command (LDM-#1993).

## Why this exists

`DockerService.get_docker_cmd_prefix` has ~85 call sites, each shelling out a
separate `docker --context <node>` process, and Docker's SSH connection helper
opens a **fresh SSH connection for every one of them**. Measured on a reporting
deployment's node: 478 `Accepted publickey` in 30 hours, against sshd's default
`MaxStartups 10:30:100`, with 714 log lines of

    sshd: error: beginning MaxStartups throttling
    sshd: drop connection #10 from [...] past MaxStartups

A dropped connection surfaces to the user as `Docker not accessible`, which
names neither SSH nor the node.

## What was tried first, and why it was wrong

This issue originally proposed an SSH **keepalive**, on the stated premise that
`DOCKER_HOST=ssh://` holds "one persistent connection... idle for long
stretches". That premise is false -- there is no single long-lived connection
to go idle -- and the reporter applied keepalives independently with no effect,
which is the result that premise predicts.

Note the irony, because it is load-bearing rather than decorative: a keepalive
is the WRONG fix for connection-per-command and the RIGHT companion to this
module, because a single long-lived tunnel is the first thing LDM has ever held
that can genuinely go idle. Hence `ServerAliveInterval` below.

## The mechanism

One `ssh` process forwards a loopback TCP port to the node's Docker socket, and
commands address it through `DOCKER_HOST=tcp://127.0.0.1:<port>`. N connections
become 1.

## Cleanup without signal handlers

The remote command is `cat > /dev/null`, reading a pipe this process holds
open. When LDM exits -- cleanly, by exception, by `SIGTERM`, or by `SIGKILL`,
which cannot be trapped -- the OS closes its file descriptors, the pipe's write
end closes, the remote `cat` reads EOF and exits, and `ssh` exits with it.

This is deliberately not a signal handler and deliberately not an idle
watchdog. A signal handler cannot cover `SIGKILL`. An idle watchdog would be
actively harmful: a Liferay first boot is legitimately idle for many minutes,
so an inactivity timeout would tear down a healthy tunnel -- reintroducing, on
our own side, the exact "idleness mistaken for death" failure this issue began
as.

Verified against a real Linux sshd (Lima/Colima, Ubuntu 24.04): docker reached
the remote daemon over the tunnel, `kill -9` of the parent left no orphaned
`ssh`, and the forwarded port was released.

## What this does NOT fix

It removes the connections **LDM** creates. It does not make a node immune:
on the reporting deployment, unrelated background traffic put 159 connections
past `MaxStartups` in three hours with zero successful authentications. Port
exposure and `MaxStartups` sizing are the node operator's to fix.
"""

import atexit
import os
import socket
import subprocess  # nosec B404 - fixed argv, no shell
import time
from typing import Any

from ldm_core.ui import UI
from ldm_core.utils import _ssh_failure_reason

#: Where dockerd listens on a Linux node. A bind-mounted socket elsewhere would
#: need this overridden; no supported node layout does.
DEFAULT_REMOTE_SOCKET = "/var/run/docker.sock"

#: How long to wait for the forwarded port to accept a connection. Generous
#: because it covers SSH authentication on a cold or distant node, and cheap
#: because the poll exits as soon as the port answers.
READY_TIMEOUT_SECONDS = 25.0

#: LDM-#1993. This is the keepalive the issue originally proposed, applied to
#: the one connection that can actually benefit from it. Six missed probes at
#: 30s gives three minutes before the client gives up -- longer than any
#: plausible network blip, shorter than a hung run.
_KEEPALIVE_OPTS = [
    "-o",
    "ServerAliveInterval=30",
    "-o",
    "ServerAliveCountMax=6",
]


class DockerTunnelError(RuntimeError):
    """The tunnel could not be established, with a diagnosis already applied.

    Carries `reason` (the phrase naming why SSH failed, from the same table
    `diagnose_remote_context_failure` uses) so callers can report in LDM's
    voice rather than printing raw SSH stderr.
    """

    def __init__(self, message: str, reason: str = "", stderr: str = ""):
        super().__init__(message)
        self.reason = reason
        self.stderr = stderr


def _free_loopback_port() -> int:
    """A port the OS says is free, bound to loopback only.

    Inherently racy -- something could take it between the probe and ssh
    binding it -- which is why `ExitOnForwardFailure=yes` is set: ssh then
    fails loudly at startup instead of running with no forward, which would
    present later as a confusing connection refused against localhost.
    """
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


class DockerSshTunnel:
    """A single SSH port-forward to a node's Docker socket.

    Usable as a context manager. `docker_host` is the value to put in the
    environment of every Docker command aimed at this node.
    """

    def __init__(
        self,
        target: Any,
        remote_socket: str = DEFAULT_REMOTE_SOCKET,
        ready_timeout: float = READY_TIMEOUT_SECONDS,
    ):
        self.target = target
        self.remote_socket = remote_socket
        self.ready_timeout = ready_timeout
        self.port: int | None = None
        self._proc: subprocess.Popen | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def docker_host(self) -> str | None:
        """`tcp://127.0.0.1:<port>`, or None before `start()`."""
        return f"tcp://127.0.0.1:{self.port}" if self.port else None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ssh_command(self, port: int) -> list[str]:
        spec = (
            f"{self.target.user}@{self.target.host}"
            if getattr(self.target, "user", None)
            else self.target.host
        )
        key = getattr(self.target, "key_path", None)
        cmd = ["ssh"]
        if key:
            cmd += ["-i", str(key)]
        cmd += [
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            # Without this, a port that became busy between the probe and now
            # leaves ssh running with no forward at all -- every later Docker
            # command then fails against a dead localhost port, which reads as
            # a Docker fault rather than an SSH one.
            "-o",
            "ExitOnForwardFailure=yes",
            *_KEEPALIVE_OPTS,
            "-L",
            f"127.0.0.1:{port}:{self.remote_socket}",
            spec,
            # The cleanup contract. See the module docstring: this exits on
            # EOF, which arrives the moment this process's stdin pipe closes
            # for any reason, including SIGKILL.
            "cat > /dev/null",
        ]
        return cmd

    def start(self) -> str:
        """Open the tunnel and return the `DOCKER_HOST` value.

        Raises `DockerTunnelError` with a diagnosis if it cannot be opened.
        """
        if self.is_alive() and self.docker_host:
            return self.docker_host

        port = _free_loopback_port()
        cmd = self._ssh_command(port)
        UI.debug(f"Opening Docker tunnel to '{self.target.name}' on port {port}")

        self._proc = subprocess.Popen(  # nosec B603 - fixed argv, no shell
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.port = port

        if self._wait_until_ready(port):
            return f"tcp://127.0.0.1:{port}"

        self._fail()
        raise AssertionError("unreachable")  # pragma: no cover - _fail always raises

    def _wait_until_ready(self, port: int) -> bool:
        """True once the forwarded port accepts a connection.

        Polls rather than sleeping a fixed interval, and gives up immediately
        if ssh has already exited -- a dead ssh will never open the port, and
        waiting out the full timeout only delays the diagnosis.
        """
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False
            try:
                probe = socket.create_connection(("127.0.0.1", port), timeout=1)
                probe.close()
                return True
            except OSError:
                time.sleep(0.2)
        return False

    def _fail(self) -> None:
        """Diagnose a failed start, tear down, and raise."""
        stderr = ""
        if self._proc is not None:
            try:
                if self._proc.poll() is None:
                    self._proc.kill()
                _, err = self._proc.communicate(timeout=5)
                stderr = (err or b"").decode("utf-8", "replace")
            except Exception:  # nosec B110 - diagnosis is best-effort
                pass

        reason = _ssh_failure_reason(stderr)
        node = getattr(self.target, "name", "<node>")
        host = getattr(self.target, "host", "")
        self.stop()
        raise DockerTunnelError(
            f"Cannot open a Docker tunnel to compute node '{node}' ({host} {reason}).",
            reason=reason,
            stderr=stderr,
        )

    def stop(self) -> None:
        """Close the tunnel.

        Closing stdin is the same EOF the crash path relies on, so the normal
        and abnormal exits converge on one mechanism rather than two. The
        terminate/kill ladder only covers an ssh that ignores it.
        """
        proc, self._proc = self._proc, None
        self.port = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:  # nosec B110 - already tearing down
            pass
        try:
            proc.wait(timeout=5)
            return
        except Exception:  # nosec B110 - fall through to the ladder
            pass
        for finish in (proc.terminate, proc.kill):
            try:
                finish()
                proc.wait(timeout=3)
                return
            except Exception:  # nosec B112 - best effort, try the next rung
                continue

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> "DockerSshTunnel":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


# --- the run-scoped registry (LDM-#1993) ---------------------------------
#
# `DockerSshTunnel` shipped in #2004 with no way to reach it: there was no
# switch and no call site, so the feature was unreachable from the CLI and
# the rollout recorded on LDM-#1993 could never have happened. This is the
# wiring.

#: Opt-in, default off. An environment variable rather than a flag or an
#: `~/.ldmrc` key because the consumers who need it drive LDM from CI, where
#: a variable costs nothing and a config file must be written into an
#: ephemeral runner.
TUNNEL_ENV_VAR = "LDM_DOCKER_TUNNEL"

_TUNNELS: dict[str, "DockerSshTunnel"] = {}
_DEAD_REPORTED: set[str] = set()


def tunnelling_enabled() -> bool:
    return os.environ.get(TUNNEL_ENV_VAR, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _report_tunnel_died(name: str, tunnel: "DockerSshTunnel") -> None:
    """Say the TUNNEL died, not that a port refused (LDM-#1993).

    Without this the next Docker command fails against a loopback port that
    nothing is listening on, and the user is told `Docker not accessible` --
    a symptom naming the wrong subject, which is the class of failure that
    cost the reporting deployment five occurrences.

    Reported once per node: every subsequent command would repeat it.

    **"Detected at" and not "died at".** Nothing watches the ssh process; its
    death is noticed the next time a Docker command needs the tunnel, which
    may be well after the fact. Saying when we noticed is honest; saying when
    it died would not be.
    """
    if name in _DEAD_REPORTED:
        return
    _DEAD_REPORTED.add(name)

    stderr = ""
    proc = tunnel._proc
    if proc is not None and proc.stderr is not None:
        try:
            stderr = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
        except Exception:  # nosec B110 - diagnosis is best-effort
            stderr = ""

    detected = time.strftime("%H:%M:%S")
    UI.error(
        f"The SSH tunnel to compute node '{name}' has closed; the Docker "
        f"socket forward is gone (detected at {detected})."
    )
    # An empty reason and an unread one are different facts, so say which.
    if stderr:
        UI.detail(f"  ssh said: {stderr}")
    else:
        UI.detail("  ssh wrote nothing before exiting.")
    UI.detail(
        f"  This is an established tunnel that died, not one that never "
        f"opened. Re-run, or unset {TUNNEL_ENV_VAR} to fall back to one "
        f"Docker connection per command."
    )


def active_tunnel(target: Any) -> "DockerSshTunnel | None":
    """The live tunnel for `target`, opening one on first use, or None.

    None whenever tunnelling is off, so every caller keeps its existing
    behaviour unless the switch is set.
    """
    if not tunnelling_enabled():
        return None

    name = getattr(target, "name", None)
    if not name:
        return None

    existing = _TUNNELS.get(name)
    if existing is not None:
        if existing.is_alive():
            return existing
        # Established, then died. Say so, drop it, and fall back to
        # `--context` rather than pointing commands at a dead port.
        _report_tunnel_died(name, existing)
        _TUNNELS.pop(name, None)
        return None

    tunnel = DockerSshTunnel(target)
    try:
        tunnel.start()
    except DockerTunnelError:
        # `start()` has already diagnosed the "never established" half. Fall
        # back to per-command connections rather than failing the run: the
        # tunnel is an optimisation, and a slower run beats no run.
        raise
    _TUNNELS[name] = tunnel
    return tunnel


def close_all_tunnels() -> None:
    """Close every tunnel this process opened."""
    for tunnel in list(_TUNNELS.values()):
        try:
            tunnel.stop()
        except Exception:  # nosec B110 - already tearing down
            pass
    _TUNNELS.clear()
    _DEAD_REPORTED.clear()


atexit.register(close_all_tunnels)
