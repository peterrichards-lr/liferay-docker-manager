"""LDM-#1969: two service client extensions must not share a host port.

`find_available_port` asks whether a port is free **on the host right now**.
During a first run neither extension's container exists yet, so both probes
returned the same free port, both were persisted, and the collision only
appeared when the second container tried to bind:

    Bind for 0.0.0.0:28080 failed: port is already allocated

Observed on the `v2.26.0-pre.2` tag run, which failed on exactly this in both
`LDM Release E2E` and `LDM Platform Verification (Multi-OS)`.

The `if meta_port_key not in meta` guard hides it after the fact -- once a port
is recorded it is not re-resolved -- so it reproduces only when two service
extensions are resolved in the SAME pass, which is the first run of a project
that has two. Every pre-existing test in this area uses one extension.

`handlers/infra.py` already does this correctly for the proxy ports: maintain
an allocated list, pass it as `exclude`, append each result. The
client-extension path never adopted the pattern.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ldm_core.workspace.metadata import _resolve_and_persist_cx_port


class _Manager:
    """A host where every port from `first_free` upward is unused.

    That is the state a first run is in: no extension container exists yet, so
    the host cannot distinguish "free" from "about to be taken by the other
    extension a moment from now".
    """

    def __init__(self, first_free=28080):
        self.first_free = first_free
        self.written = []

    def find_available_port(self, ip, start_port, exclude=None):
        exclude = exclude or []
        port = max(int(start_port), self.first_free)
        while port in exclude:
            port += 1
        return port

    def write_meta(self, root, meta):
        self.written.append(dict(meta))


def _resolve(manager, meta, ext_id, port=8080):
    ext_info = {"ports": [{"port": port}], "loadBalancer": None}
    _resolve_and_persist_cx_port(
        MagicMock(manager=manager), ext_info, ext_id, meta, Path("/proj")
    )


class TestTwoExtensionsGetDistinctPorts(unittest.TestCase):
    def test_a_second_extension_does_not_reuse_the_first_port(self):
        manager = _Manager()
        meta: dict[str, str] = {}
        _resolve(manager, meta, "synthetic-svc")
        _resolve(manager, meta, "derivedsvc")

        first = meta["port_synthetic-svc"]
        second = meta["port_derivedsvc"]
        self.assertNotEqual(
            first,
            second,
            "both extensions were assigned the same host port; the second "
            "container cannot bind (LDM-#1969)",
        )

    def test_three_extensions_are_all_distinct(self):
        manager = _Manager()
        meta: dict[str, str] = {}
        for ext in ("alpha", "beta", "gamma"):
            _resolve(manager, meta, ext)
        ports = [meta[f"port_{e}"] for e in ("alpha", "beta", "gamma")]
        self.assertEqual(len(ports), len(set(ports)), f"duplicate ports: {ports}")

    def test_a_previously_persisted_port_is_avoided(self):
        """A re-run must not hand a new extension a port an existing one owns.

        `meta` is read from disk on a later run, so the ports it already
        carries are exactly the ones that must be excluded.
        """
        manager = _Manager()
        meta = {"port_existing": "28080"}
        _resolve(manager, meta, "newcomer")
        self.assertNotEqual("28080", meta["port_newcomer"])

    def test_an_already_resolved_extension_is_left_alone(self):
        """The control. Re-resolving a port on every run would move a
        published service's port out from under it."""
        manager = _Manager()
        meta = {"port_synthetic-svc": "29999"}
        _resolve(manager, meta, "synthetic-svc")
        self.assertEqual("29999", meta["port_synthetic-svc"])

    def test_a_malformed_entry_does_not_fail_the_run(self):
        """A meta carrying junk in a port key is not worth aborting for; it
        simply cannot be excluded."""
        manager = _Manager()
        meta = {"port_broken": "not-a-number"}
        _resolve(manager, meta, "fresh")
        self.assertIn("port_fresh", meta)

    def test_unrelated_meta_keys_are_not_treated_as_ports(self):
        manager = _Manager()
        meta = {"port": "8080", "container_name": "proj", "portal_log4j": "x"}
        _resolve(manager, meta, "svc")
        self.assertIn("port_svc", meta)


if __name__ == "__main__":
    unittest.main()
