"""LDM-#1987: the resolved host port never reached the generated compose.

Every test in this area bracketed one seam without crossing it:

* `test_cx_port_allocation.py` calls `_resolve_and_persist_cx_port` directly and
  asserts the values it writes. Correct, and it never touches the composer.
* `test_composer.py`'s port tests hand-build `meta = {"port_ms1": "8083"}`, pass
  it straight into `_build_extensions_services`, AND mock
  `scan_client_extensions`. They assert the composer honours a key the real
  pipeline never puts in that dict, so they cannot fail for this bug.
* `test_workspace.py::test_scan_client_extensions_port_resolution` runs the real
  scan and asserts the value ON DISK -- which is exactly the one place it does
  arrive.

The bug lived in the gap. `scan_client_extensions` takes no `meta` parameter; it
reads its own copy from disk and the resolver mutates THAT, while the composer
holds the pipeline's `project_meta`, which never receives the keys. So
`meta.get(f"port_{ext_id}")` was always None and every extension fell through to
`ms_port` -- 8080 for any extension declaring no ports. Two of them published
`0.0.0.0:8080:8080` and the second container could not bind (LDM-#1969).

This module runs the REAL scan and the REAL composer, in that order, against a
meta dict shaped like the pipeline's -- i.e. WITHOUT port keys -- and asserts on
the generated compose. That is the only arrangement that can observe the defect.
"""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ldm_core.handlers.composer import ComposerService
from ldm_core.tests.test_workspace import MockWorkspaceManager


def _cx_zip(path, ext_id, *, declare_ports=False):
    """A service extension: a Dockerfile makes it one, `kind` is not Job."""
    body = {"id": ext_id, "memory": 512, "kind": "Deployment"}
    if declare_ports:
        body["ports"] = [{"port": 9090, "external": True}]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("LCP.json", json.dumps(body))
        z.writestr("client-extension.yaml", f"{ext_id}:\n  type: customElement\n")
        z.writestr("Dockerfile", 'FROM alpine\nCMD ["sleep", "3600"]\n')


class TestTwoExtensionsDoNotPublishTheSamePort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "root"
        self.osgi_cx = self.root / "osgi" / "client-extensions"
        self.ce_build = base / "ce-build"
        for d in (self.root, self.osgi_cx, self.ce_build):
            d.mkdir(parents=True)

        self.manager = MockWorkspaceManager()
        # The pipeline's shape: a meta with NO port_* keys. That is the whole
        # point -- handing the composer a dict that already has them is the
        # control, not the test.
        self.manager.write_meta(self.root, {"ssl": "false", "host_name": "localhost"})

        for ext_id in ("ext-alpha", "ext-beta"):
            _cx_zip(self.ce_build / f"{ext_id}.zip", ext_id)

        # Every port free, so both probes would answer the same without the
        # resolver's exclusion -- the first-run condition this bug needs.
        port_patch = patch.object(
            self.manager, "check_port", side_effect=lambda _ip, _p: True
        )
        port_patch.start()
        self.addCleanup(port_patch.stop)

    def _generate(self):
        composer = ComposerService(self.manager)
        paths = {
            "root": self.root,
            "cx": self.osgi_cx,
            "ce_dir": self.ce_build,
        }
        pipeline_meta = self.manager.read_meta(self.root) or {}
        return composer._build_extensions_services(
            paths, pipeline_meta, "localhost", "proj", False
        ), pipeline_meta

    def test_the_published_ports_are_distinct(self):
        services, _ = self._generate()
        published = [
            spec.split(":")[1]
            for svc in services.values()
            for spec in (svc.get("ports") or [])
        ]
        self.assertEqual(
            len(published),
            len(set(published)),
            f"two client extensions published the same host port {published}; "
            f"the second container cannot bind (LDM-#1969/#1987)",
        )

    def test_the_port_the_resolver_chose_is_the_port_published(self):
        """The seam itself. The resolver's answer must survive to the compose.

        Asserted against what the resolver persisted, so this fails if the
        composer substitutes any value of its own.
        """
        services, _ = self._generate()
        on_disk = {
            k[len("port_") :]: v
            for k, v in (self.manager.read_meta(self.root) or {}).items()
            if k.startswith("port_")
        }
        self.assertTrue(on_disk, "the resolver persisted nothing to compare")

        for svc_name, svc in services.items():
            ports = svc.get("ports") or []
            if not ports:
                continue
            ext_id = svc_name[len("proj-") :]
            self.assertIn(ext_id, on_disk, f"no resolved port for {ext_id}")
            self.assertEqual(
                on_disk[ext_id],
                ports[0].split(":")[1],
                f"{svc_name} published {ports[0]} but the resolver chose "
                f"{on_disk[ext_id]} -- the value did not cross from the scan "
                f"to the composer (LDM-#1987)",
            )

    def test_the_pipeline_meta_is_still_not_the_carrier(self):
        """Documents WHY this works now, so the mechanism is not re-fixed.

        The composer no longer depends on the pipeline dict receiving the keys.
        It still does not receive them -- that is expected, and the port
        arrives on the extension instead.
        """
        _, pipeline_meta = self._generate()
        self.assertFalse(
            [k for k in pipeline_meta if k.startswith("port_")],
            "the pipeline dict now carries port keys, so this test is no "
            "longer pinning the seam it was written for",
        )


if __name__ == "__main__":
    unittest.main()
