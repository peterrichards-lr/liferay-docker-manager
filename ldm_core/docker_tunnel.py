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
from datetime import datetime
from typing import Any

from ldm_core.ui import UI
from ldm_core.utils import _ssh_failure_reason

#: LDM-#1993. Opt-in, default off, for one release cycle. An environment
#: variable rather than an `~/.ldmrc` key or a CLI flag at the request of the
#: deployment that reported this and will be the first to enable it: their CI
#: already passes `LDM_NODE_TARGET` and `LDM_VERSION` this way, whereas a
#: config key would mean writing a file into an ephemeral runner before the
#: first call and a flag would mean touching ~30 invocation sites.
TUNNEL_ENV_VAR = "LDM_DOCKER_TUNNEL"

_TRUTHY = {"1", "true", "yes", "on"}

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


class DockerTunnelClosedError(DockerTunnelError):
    """The tunnel was established and then died mid-run.

    Deliberately distinct from its parent. "Never established" and
    "established then died" have different causes and are investigated
    differently -- the first is credentials, host or firewall, the second is
    the connection being lost or killed after it was working. Collapsing them
    into one message costs the reader that distinction, which is exactly the
    complaint that produced this class: a symptom naming the wrong subject.
    """


def tunnel_enabled() -> bool:
    """Whether the caller has opted in via `LDM_DOCKER_TUNNEL`."""
    return os.environ.get(TUNNEL_ENV_VAR, "").strip().lower() in _TRUTHY


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
        #: Wall-clock time the forward started answering. Reported on a
        #: mid-run death so the operator can line it up against their own run
        #: timeline, which is where the cause usually is.
        self.established_at: datetime | None = None

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
            self.established_at = datetime.now()
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

    def _drain_stderr(self) -> str:
        """Whatever ssh said before it exited, best effort.

        Never blocks for long: a dead process's pipe is already closed, and a
        live one is not our problem here.
        """
        if self._proc is None or self._proc.stderr is None:
            return ""
        try:
            return (self._proc.stderr.read() or b"").decode("utf-8", "replace").strip()
        except Exception:  # nosec B110 - diagnosis is best effort
            return ""

    def closed_error(self) -> "DockerTunnelClosedError":
        """The error for a tunnel that was working and has stopped.

        Four things, in the order the reporting deployment asked for them:

        1. that the TUNNEL died -- not that a port refused a connection. The
           localhost port is an implementation detail, and naming it sends the
           reader to investigate Docker when the fault is SSH. Their words:
           "a symptom that names the wrong subject", which is the class that
           cost them five occurrences.
        2. WHEN, so it can be correlated against their run's own timeline.
        3. what ssh last said -- and where nothing survived, say so, because
           an empty reason is a different fact from an unread one.
        4. implicitly, by being a distinct class from its parent: this is
           "established then died", not "never established".
        """
        node = getattr(self.target, "name", "<node>")
        stderr = self._drain_stderr()
        when = (
            self.established_at.strftime("%H:%M:%S")
            if self.established_at
            else "an unrecorded time"
        )
        if stderr:
            reason = _ssh_failure_reason(stderr)
            tail = stderr.splitlines()[-1].strip()
            detail = f"ssh said: {tail}"
        else:
            reason = "closed unexpectedly"
            # Stated rather than omitted: silence from ssh is information.
            detail = "no diagnostic available from ssh"
        self.stop()
        return DockerTunnelClosedError(
            f"The SSH tunnel to compute node '{node}' has closed; the Docker "
            f"socket forward is gone. It was established at {when} and {reason}. "
            f"{detail}.",
            reason=reason,
            stderr=stderr,
        )

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> "DockerSshTunnel":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


# -- process-wide registry -------------------------------------------------
#
# One tunnel per node, opened on first use rather than by an explicit setup
# step: there are ~85 Docker call sites and no single place all of them pass
# through before work begins, so lazy activation is what makes this reachable
# without touching every one of them.

_ACTIVE: dict[str, DockerSshTunnel] = {}


def tunnel_for(target: Any) -> DockerSshTunnel | None:
    """A live tunnel for `target`, opening one on first use.

    Returns None when the caller has not opted in, which is the default and
    means every command keeps its existing `--context` behaviour.

    Raises `DockerTunnelError` if a tunnel cannot be opened, and
    `DockerTunnelClosedError` if one that was working has since died. Those
    are separate types on purpose -- see `DockerTunnelClosedError`.
    """
    if not tunnel_enabled():
        return None

    name = getattr(target, "name", None) or str(target)
    existing = _ACTIVE.get(name)
    if existing is not None:
        if existing.is_alive():
            return existing
        # It was working and is not any more. Drop it before raising, so a
        # caller that catches this and retries opens a fresh one rather than
        # meeting the same corpse.
        _ACTIVE.pop(name, None)
        raise existing.closed_error()

    tunnel = DockerSshTunnel(target)
    tunnel.start()
    _ACTIVE[name] = tunnel
    UI.debug(f"Docker tunnel to '{name}' established at {tunnel.docker_host}")
    return tunnel


def shutdown_all() -> None:
    """Close every open tunnel. Idempotent.

    Registered with `atexit` as a tidiness measure, NOT as the cleanup
    guarantee -- `atexit` does not run on `SIGKILL`, which is precisely the
    case the pipe-EOF contract exists to cover. This only makes the normal
    exit prompt instead of waiting for the OS to close the pipe.
    """
    for name in list(_ACTIVE):
        tunnel = _ACTIVE.pop(name, None)
        if tunnel is not None:
            tunnel.stop()


atexit.register(shutdown_all)
