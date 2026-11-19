"""An imported project must say where the consumer's own licence goes (LDM-#2443).

Since PR #2450, `ldm package` leaves the publisher's licence out of a `.ldmp`.
A project imported from one therefore boots to DXP's activation page until the
consumer adds their own key. Both `ldm doctor` and the first `ldm run` have to
say so, with the absolute path to put it in.

Also covered here, because it is the same check:

* the doctor licence hint linked to `docs/LDM_ARCHITECTURE.md`, which does not
  exist;
* a CE project stores `portal: "true"` with a bare tag, and doctor passed only
  the tag, so CE was reported as a missing licence.
"""

import inspect
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.constants import GITHUB_DOCS_URL
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.license import LicenseService
from ldm_core.ui import UI

DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"

#: A realistic key: `_parse_license_xml` rejects one without `<product-name>`.
LICENCE_XML = """<?xml version="1.0"?>
<license>
<account-name>Example, Inc.</account-name>
<owner>Example Owner</owner>
<description>Example Developer Activation Keys</description>
<product-name>DXP Development</product-name>
<product-version>7.4</product-version>
<license-type>developer</license-type>
<expiration-date>Wednesday, February 24, 2123 10:57:51 AM GMT</expiration-date>
</license>
"""

IMPORTED_DXP = {"restored_from_package": "true", "tag": "2025.q1.0-lts"}


class _TmpProject(unittest.TestCase):
    """A project tree and an isolated LDM_HOME, both in a temp directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        env = patch.dict(os.environ, {"LDM_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)

        self.root = (base / "proj").resolve()
        self.common = base / "common"
        for d in (self.root / "deploy", self.root / "osgi" / "modules", self.common):
            d.mkdir(parents=True)

    def paths(self):
        return {
            "root": self.root,
            "common": self.common,
            "deploy": self.root / "deploy",
            "modules": self.root / "osgi" / "modules",
        }

    def add_licence(self):
        (self.root / "deploy" / "activation-key.xml").write_text(LICENCE_XML)


class DoctorLicenceHint(_TmpProject):
    """Drives the real `DoctorRunner._check_project_license`."""

    def check(self, meta):
        from ldm_core.diagnostics.doctor import DoctorRunner

        doctor = DoctorRunner.__new__(DoctorRunner)
        doctor.results = []
        doctor.project_paths = [self.root]
        hints: list[tuple[str, str | None]] = []
        doctor.add_hint = lambda text, doc=None: hints.append((str(text), doc))  # type: ignore[method-assign]

        manager = MagicMock()
        manager.license = LicenseService(manager)
        manager.composer = ComposerService(manager)
        manager.get_common_dir.return_value = self.common
        manager.setup_paths.return_value = {
            k: v for k, v in self.paths().items() if k != "common"
        }
        doctor.handler = MagicMock()
        doctor.handler.manager = manager

        with patch.object(UI, "raw"):
            doctor._check_project_license(self.root, meta)
        return doctor.results, hints

    def test_imported_unlicensed_project_names_the_deploy_path(self):
        results, hints = self.check(dict(IMPORTED_DXP))

        self.assertEqual(results[0][1], "Missing")
        text = "\n".join(h for h, _ in hints)
        self.assertIn(f"{self.root / 'deploy'}/", text)
        self.assertIn(f"{self.home / '.ldm' / 'common'}/", text)
        self.assertIn("does not carry its publisher's licence", text)

    def test_imported_licensed_project_gets_no_hint(self):
        self.add_licence()
        results, hints = self.check(dict(IMPORTED_DXP))

        self.assertTrue(results[0][1].startswith("Present"), results)
        self.assertEqual(hints, [])

    def test_a_ce_project_with_a_bare_tag_needs_no_licence(self):
        """CE stores `portal: "true"` and a bare tag (composer.py)."""
        results, hints = self.check({"portal": "true", "tag": "7.4.3.132-ga132"})

        self.assertEqual(results[0][1], "Not Required (Portal CE)")
        self.assertIs(results[0][2], True)
        self.assertEqual(hints, [])

    def test_every_doc_link_the_licence_hint_emits_exists(self):
        links: set[str] = set()
        for meta in (dict(IMPORTED_DXP), {"tag": "2025.q1.0-lts"}):
            _, hints = self.check(meta)
            links.update(doc for _, doc in hints if doc)
        self.assertEqual(len(links), 2, links)

        for link in links:
            self.assertTrue(link.startswith(f"{GITHUB_DOCS_URL}/"), link)
            rel, _, anchor = link[len(GITHUB_DOCS_URL) + 1 :].partition("#")
            doc = DOCS_ROOT / rel
            self.assertTrue(doc.is_file(), f"{link} -> missing {doc}")
            if anchor:
                slugs = {
                    re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", h.strip().lower()))
                    for h in re.findall(r"^#+\s+(.*)$", doc.read_text(), re.M)
                }
                self.assertIn(anchor, slugs, f"{link}: no such heading")

    def test_doctor_has_no_link_to_the_missing_architecture_doc(self):
        from ldm_core.diagnostics import doctor

        self.assertNotIn("LDM_ARCHITECTURE.md", inspect.getsource(doctor))

    def test_project_health_calls_the_licence_check(self):
        """A check nobody calls is the LDM-#1774 shape."""
        from ldm_core.diagnostics.doctor import DoctorRunner

        source = inspect.getsource(DoctorRunner._check_project_specific)
        self.assertIn("self._check_project_license(p_path, meta)", source)


class FirstBootWarning(_TmpProject):
    """Drives the real `ExecutionStage.execute`."""

    def boot(self, meta, *, no_up=False, write_fails=False):
        from ldm_core.pipelines.run import ExecutionStage, RunPipelineContext

        manager = MagicMock()
        manager.license = LicenseService(manager)
        manager.composer = ComposerService(manager)
        manager.non_interactive = True
        manager.args.no_up = no_up
        manager.args.no_wait = True
        manager.get_container_status.return_value = "healthy"
        # What a later run would read back: the last meta the stage wrote.
        self.written: list[dict] = []
        manager.write_meta.side_effect = lambda _root, m: self.written.append(dict(m))
        if write_fails:
            # Only the marker write (the first) fails; the stage's own later
            # meta write is not under test here.
            manager.write_meta.side_effect = [OSError("read-only meta")] + [None] * 9

        context = RunPipelineContext(manager)
        context.set("project_id", "proj")
        context.set("project_meta", dict(meta))
        context.set("paths", self.paths())
        context.set("no_up", no_up)
        context.set("no_wait", True)

        with patch.object(UI, "warning") as warning:
            ExecutionStage().execute(context)
        return [
            str(c.args[0])
            for c in warning.call_args_list
            if c.args and "activation page" in str(c.args[0])
        ]

    def test_imported_unlicensed_dxp_project_is_warned_once(self):
        warnings = self.boot(IMPORTED_DXP)

        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn(f"{self.root / 'deploy'}/", warnings[0])
        self.assertIn(f"{self.home / '.ldm' / 'common'}/", warnings[0])

    def test_the_first_run_records_that_it_warned(self):
        from ldm_core.pipelines.run import LICENCE_HINT_SHOWN_KEY

        self.assertEqual(len(self.boot(IMPORTED_DXP)), 1)
        self.assertTrue(self.written, "the marker was never written")
        self.assertEqual(self.written[-1].get(LICENCE_HINT_SHOWN_KEY), "true")

    def test_the_second_run_does_not_warn_again(self):
        """A key activated in the browser lives in the data volume, which
        `find_license` cannot see, so without the marker this repeats forever."""
        self.assertEqual(len(self.boot(IMPORTED_DXP)), 1)
        self.assertEqual(self.boot(self.written[-1]), [])

    def test_a_licensed_project_needs_no_marker(self):
        from ldm_core.pipelines.run import LICENCE_HINT_SHOWN_KEY

        self.add_licence()
        self.assertEqual(self.boot(IMPORTED_DXP), [])
        self.assertTrue(
            all(LICENCE_HINT_SHOWN_KEY not in m for m in self.written), self.written
        )

    def test_a_failing_marker_write_never_fails_the_run(self):
        warnings = self.boot(IMPORTED_DXP, write_fails=True)
        self.assertEqual(len(warnings), 1)

    def test_a_project_not_imported_is_not_warned(self):
        self.assertEqual(self.boot({"tag": "2025.q1.0-lts"}), [])

    def test_a_licensed_imported_project_is_not_warned(self):
        self.add_licence()
        self.assertEqual(self.boot(IMPORTED_DXP), [])

    def test_a_licence_in_common_counts(self):
        (self.common / "activation-key.xml").write_text(LICENCE_XML)
        self.assertEqual(self.boot(IMPORTED_DXP), [])

    def test_an_imported_ce_project_is_not_warned(self):
        meta = {"restored_from_package": "true", "portal": "true", "tag": "7.4.3"}
        self.assertEqual(self.boot(meta), [])

    def test_no_up_is_not_warned(self):
        self.assertEqual(self.boot(IMPORTED_DXP, no_up=True), [])

    def test_a_failing_licence_lookup_never_fails_the_run(self):
        with patch.object(
            LicenseService, "check_license_health", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(self.boot(IMPORTED_DXP), [])


if __name__ == "__main__":
    unittest.main()
