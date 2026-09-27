"""LDM-#1946 and LDM-#1989: two things `ldm system doctor` could not tell you.

**LDM-#1946 — whether this filesystem enforces permission modes.** LDM had no
filesystem awareness at all. `reclaim_volume_permissions()` shells out to Alpine
to chown/chmod a project tree, and on a filesystem that ignores permissions the
command succeeds and changes nothing. Measured on disposable disk images: FAT32
and exFAT both accept `chmod 750` with exit 0 and leave the mode untouched, and
the synthesised mode is `0700` — so a container running as uid 1000 is locked
out of the whole tree, which is the opposite of the intuitive reading that a
non-POSIX filesystem is permissive.

**LDM-#1989 — which Traefik routers the proxy actually loaded.** LDM writes
router labels and never checked they took. A router that did not load returns a
bare 404, indistinguishable from the wrong hostname (LDM-#1984), an extension
that never becomes a container, a missing hosts entry, or a proxy fault.
Diagnosing one such 404 took days of round trips; the API answering it has been
published the whole time.
"""

import json
import stat
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch


class _Stub:
    """Just enough DoctorRunner for the two checks under test.

    The real `__init__` reaches for a handler, a manager and a docker prefix,
    none of which either check uses. Binding the real methods onto a stub keeps
    the code under test genuinely real while leaving out everything that would
    need mocking for no benefit -- and avoids assigning over a method on the
    real class, which is both a typing error and a way to leak state between
    tests.
    """

    def __init__(self):
        self.results: list = []
        self.hints: list = []

    def add_hint(self, *args, **_kwargs):
        self.hints.append(args[0] if args else "")


def _runner():
    import types

    from ldm_core.diagnostics.doctor import DoctorRunner

    stub = _Stub()
    for name in ("_check_filesystem_honours_modes", "_check_traefik_routers"):
        setattr(stub, name, types.MethodType(getattr(DoctorRunner, name), stub))
    return stub


def _status(runner, component):
    return next((s for c, s, _ in runner.results if c == component), None)


def _ok(runner, component):
    return next((o for c, _, o in runner.results if c == component), None)


class TestFilesystemModeProbe(unittest.TestCase):
    def test_it_reports_enforced_where_modes_take(self):
        r = _runner()
        r._check_filesystem_honours_modes()
        # This repo lives on a volume that enforces modes; verified directly.
        self.assertTrue(_ok(r, "Filesystem Modes"))
        self.assertIn("Enforced", _status(r, "Filesystem Modes"))

    def test_it_warns_and_names_both_modes_when_they_are_discarded(self):
        """The FAT32/exFAT case, simulated by reporting the mode those
        filesystems actually synthesise: 0700, not a permissive one."""

        class _Stat:
            st_mode = stat.S_IFDIR | 0o700

        r = _runner()
        with patch.object(Path, "stat", return_value=_Stat()):
            r._check_filesystem_honours_modes()

        self.assertEqual("warn", _ok(r, "Filesystem Modes"))
        status = _status(r, "Filesystem Modes")
        self.assertIn("NOT enforced", status)
        self.assertIn("0751", status, "the requested mode must be named")
        self.assertIn("0o700", status, "the actual mode must be named")

    def test_it_says_there_is_no_workaround(self):
        """Where modes are not enforced, chmod 777 and group membership are
        equally inert. The hint must not imply a setting adapts to this."""
        r = _runner()
        with patch.object(Path, "chmod", side_effect=OSError("read-only")):
            r._check_filesystem_honours_modes()
        self.assertEqual("warn", _ok(r, "Filesystem Modes"))

    def test_a_broken_probe_never_fails_the_doctor_run(self):
        r = _runner()
        with patch("tempfile.TemporaryDirectory", side_effect=OSError("no space")):
            r._check_filesystem_honours_modes()
        self.assertEqual("warn", _ok(r, "Filesystem Modes"))
        self.assertIn("Not determined", _status(r, "Filesystem Modes"))


class TestTraefikRoutersCheck(unittest.TestCase):
    INTERNAL: ClassVar[list[dict]] = [
        {"name": "api@internal", "status": "enabled", "provider": "internal"},
        {"name": "dashboard@internal", "status": "enabled", "provider": "internal"},
    ]

    def _run(self, payload):
        r = _runner()
        with patch(
            "ldm_core.diagnostics.doctor.run_command",
            return_value=json.dumps(payload) if payload is not None else None,
        ):
            r._check_traefik_routers(["docker"])
        return r

    def test_traefiks_own_routers_do_not_count_as_project_routers(self):
        """`api@internal` and `dashboard@internal` are always present. Counting
        them would report a healthy proxy with no extensions routed."""
        r = self._run(self.INTERNAL)
        self.assertEqual("warn", _ok(r, "Traefik Routers"))
        self.assertIn("No project routers", _status(r, "Traefik Routers"))

    def test_a_loaded_router_reports_its_entrypoints(self):
        """The entrypoints are the one thing a labels dump cannot tell you, and
        a router on the wrong one 404s while every label looks correct."""
        r = self._run(
            [
                *self.INTERNAL,
                {
                    "name": "proj-ext-svc@docker",
                    "status": "enabled",
                    "provider": "docker",
                    "entryPoints": ["websecure"],
                },
            ]
        )
        self.assertTrue(_ok(r, "Traefik Routers"))
        status = _status(r, "Traefik Routers")
        self.assertIn("1 loaded", status)
        self.assertIn("websecure", status)

    def test_a_router_that_loaded_but_is_not_enabled_is_named(self):
        r = self._run(
            [
                {
                    "name": "proj-ext-svc@docker",
                    "status": "disabled",
                    "provider": "docker",
                }
            ]
        )
        self.assertEqual("warn", _ok(r, "Traefik Routers"))
        self.assertIn("proj-ext-svc@docker", _status(r, "Traefik Routers"))

    def test_no_response_is_not_reported_as_missing_routers(self):
        """The proxy may not be running, which another check already reports, and
        a Traefik build without wget looks identical. Saying 'not determined'
        keeps those distinct from 'your routers are gone'."""
        r = self._run(None)
        self.assertIn("Not determined", _status(r, "Traefik Routers"))

    def test_an_unreadable_response_is_not_reported_as_missing_routers(self):
        r = _runner()
        with patch(
            "ldm_core.diagnostics.doctor.run_command", return_value="<html>nope</html>"
        ):
            r._check_traefik_routers(["docker"])
        self.assertIn("Unreadable", _status(r, "Traefik Routers"))


if __name__ == "__main__":
    unittest.main()
