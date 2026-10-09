"""LDM-#2123: a Windows-reserved port must not be reported as available.

LDM-#2036 taught LDM to *explain* this failure:

    ports are not available: exposing port TCP 0.0.0.0:3001 -> 127.0.0.1:0:
    listen tcp4 0.0.0.0:3001: bind: An attempt was made to access a socket in
    a way forbidden by its access permissions.

What nobody checked then is where that port came from. It came from LDM.

`check_port` binds to decide availability, and Windows refuses a bind inside a
WinNAT-reserved range with WSAEACCES -- which Python raises as
`PermissionError`. The handler assumed the only cause of EACCES is an
unprivileged process touching a port below 1024, so it fell back to asking
whether anything was *listening*. Nothing ever listens on a reserved port, so
the answer was "free", `find_available_port` settled on it, and
`_resolve_and_persist_cx_port` wrote it into the project meta. Docker then
refused to publish it and LDM printed the #2036 tip -- diagnosing a port it had
chosen itself.

Hit on 2026-10-09 during Windows 11 / PowerShell 5.1 verification of
v2.26.5-pre.4: the `syntheticsvc` client extension declares container port
3001, WinNAT had reserved that range that day, and the run died at
`docker compose start`. The same suite passed on the same machine the day
before, because the reserved ranges move when the host or WinNAT restarts.

The sub-1024 fallback is deliberately preserved: there EACCES really does mean
"this process cannot bind it", while Docker's daemon still can, so LDM's own
inability is not evidence about the port.
"""

import errno
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.handlers.base import BaseHandler

# Verbatim from the Windows 11 run; this is the text Windows attaches to
# WSAEACCES and the reason the failure reads like a privilege problem.
WINDOWS_RESERVED_MESSAGE = (
    "An attempt was made to access a socket in a way forbidden by its "
    "access permissions"
)

# `connect_ex` on a port with no listener. The value differs by platform
# (ECONNREFUSED on POSIX, WSAECONNREFUSED on Windows); all that matters is
# that it is non-zero, which is what "nothing is listening" looks like.
NOTHING_LISTENING = errno.ECONNREFUSED


class _FakeSocket:
    """A socket whose bind is refused the way Windows refuses a reserved port."""

    def __init__(self, *_args, **_kwargs):
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def settimeout(self, value):
        self.timeout = value

    def connect_ex(self, _addr):
        return NOTHING_LISTENING

    def bind(self, _addr):
        raise PermissionError(errno.EACCES, WINDOWS_RESERVED_MESSAGE)

    def close(self):
        return None


def _record(sink):
    """A reservation probe that records the address it was asked about."""

    def probe(ip, _port):
        sink.append(ip)
        return False

    return probe


class TestReservedPortIsNotAvailable(unittest.TestCase):
    def setUp(self):
        self.handler = BaseHandler.__new__(BaseHandler)

    def test_a_reserved_high_port_is_not_available(self):
        """The port Windows refused is the port the E2E suite died on."""
        with patch.object(socket, "socket", _FakeSocket):
            self.assertFalse(
                self.handler.check_port("127.0.0.1", 3001),
                "A port whose bind Windows refuses with WSAEACCES is not "
                "available. Reporting it free is how LDM chose, persisted "
                "and then published a port Docker cannot bind.",
            )

    def test_find_available_port_walks_past_a_reserved_range(self):
        """The consequence: the search must not settle on a refused port.

        This is the assertion that actually protects the user. `check_port`
        returning False is only useful if the caller then moves on, and
        `find_available_port` is the caller whose answer is written into the
        project meta and then published by Docker.

        `check_port` is held at True throughout, so the walk is driven purely
        by the reservation probe. That is the real Windows shape: a reserved
        port has no listener, so every "is anything using this" test says it
        is free.
        """
        reserved = set(range(3001, 3005))
        probed = []

        def fake_check_port(_ip, _port):
            return True

        def fake_reserved(_ip, port):
            probed.append(int(port))
            return int(port) in reserved

        with (
            patch.object(BaseHandler, "check_port", staticmethod(fake_check_port)),
            patch.object(BaseHandler, "port_is_reserved", staticmethod(fake_reserved)),
        ):
            chosen = self.handler.find_available_port("127.0.0.1", 3001)

        self.assertEqual(chosen, 3005)
        self.assertEqual(probed, [3001, 3002, 3003, 3004, 3005])

    def test_the_reservation_probe_asks_about_the_address_docker_binds(self):
        """Docker publishes on 0.0.0.0; callers pass 127.0.0.1.

        If the probe inherited the caller's loopback address it would depend
        on Windows refusing a loopback bind inside a reserved range -- which
        is not something LDM can rely on, and not what Docker attempts.
        """
        seen: list[str] = []

        with (
            patch.object(
                BaseHandler, "check_port", staticmethod(lambda _ip, _port: True)
            ),
            patch.object(BaseHandler, "port_is_reserved", staticmethod(_record(seen))),
        ):
            self.handler.find_available_port("127.0.0.1", 3001)

        self.assertEqual(
            seen,
            [""],
            "the bind probe must use the wildcard address, not the caller's",
        )

    def test_a_privileged_port_still_defers_to_the_listener_check(self):
        """Below 1024 EACCES means LDM cannot bind it -- not that Docker cannot.

        Docker's daemon binds privileged ports routinely, which is how
        `ldm infra setup --ssl-port 443` works at all. Treating EACCES as
        "unavailable" here would make LDM refuse its own default HTTPS port.
        """
        with patch.object(socket, "socket", _FakeSocket):
            self.assertTrue(
                self.handler.check_port("127.0.0.1", 443),
                "443 has no listener, so it is available to the daemon that "
                "will actually bind it.",
            )


class _BusySocket(_FakeSocket):
    """A port another process already holds: EADDRINUSE, not EACCES."""

    def bind(self, _addr):
        raise OSError(errno.EADDRINUSE, "Address already in use")


class TestReservedIsNarrowerThanUnavailable(unittest.TestCase):
    """`port_is_reserved` must not be a synonym for `not check_port`.

    The persisted host port of a client extension is held by that extension's
    own container for as long as the project runs. If "busy" cleared the
    stored value, every `ldm run` would hand the extension a different host
    port -- a worse defect than the one #2123 fixes, and one that would look
    like LDM-#1969 all over again.
    """

    def setUp(self):
        self.handler = BaseHandler.__new__(BaseHandler)

    def test_a_refused_high_port_is_reserved(self):
        with patch.object(socket, "socket", _FakeSocket):
            self.assertTrue(self.handler.port_is_reserved("127.0.0.1", 3001))

    def test_a_busy_port_is_not_reserved(self):
        with patch.object(socket, "socket", _BusySocket):
            self.assertFalse(
                self.handler.port_is_reserved("127.0.0.1", 3001),
                "A port held by a running container is unavailable, not "
                "reserved. Clearing the persisted port here would move the "
                "extension's host port on every run.",
            )

    def test_a_privileged_port_is_never_reserved(self):
        """EACCES on 443 is about this process, not about the port."""
        with patch.object(socket, "socket", _FakeSocket):
            self.assertFalse(self.handler.port_is_reserved("127.0.0.1", 443))


class TestPersistedPortIsReExaminedWhenReserved(unittest.TestCase):
    """The end-to-end consequence, at the seam that writes the meta.

    Windows reserved ranges move when the host or WinNAT restarts, so the
    stored value is not permanently trustworthy. Before #2123 nothing ever
    re-examined it, so a project that had worked became permanently unbootable
    and the only remedy was hand-editing meta.
    """

    def _resolve(self, manager, meta):
        from unittest.mock import MagicMock

        from ldm_core.workspace.metadata import _resolve_and_persist_cx_port

        return _resolve_and_persist_cx_port(
            MagicMock(manager=manager),
            {"ports": [{"port": 3001}], "loadBalancer": None},
            "syntheticsvc",
            meta,
            Path("/proj"),
        )

    def test_a_reserved_persisted_port_is_replaced_and_rewritten(self):
        from ldm_core.tests.test_cx_port_allocation import _Manager

        manager = _Manager(first_free=3005, reserved=(3001,))
        meta = {"port_syntheticsvc": "3001"}

        resolved = self._resolve(manager, meta)

        self.assertNotEqual(resolved, "3001")
        self.assertEqual(resolved, "3005")
        self.assertEqual(meta["port_syntheticsvc"], "3005")
        self.assertTrue(
            manager.written,
            "the replacement must reach disk, or the next run re-does this",
        )

    def test_a_usable_persisted_port_is_left_exactly_as_it_was(self):
        from ldm_core.tests.test_cx_port_allocation import _Manager

        manager = _Manager(first_free=3005)
        meta = {"port_syntheticsvc": "3001"}

        resolved = self._resolve(manager, meta)

        self.assertEqual(resolved, "3001")
        self.assertEqual(
            manager.written,
            [],
            "nothing was wrong, so nothing should have been rewritten",
        )


if __name__ == "__main__":
    unittest.main()
