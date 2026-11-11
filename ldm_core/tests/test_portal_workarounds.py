"""LDM installs the bundles that work around Liferay defects (LDM-#2289).

The first is ``dxp-routes-domain``: Liferay's routes writer publishes Tomcat's
``:8080`` in the DXP domain behind a TLS-terminating proxy (LDM-#2344). It
imports ``com.liferay.portal.kernel.util`` [96.7,101.0), so it is placed only
for 2026.q1.14-lts up to, not including, 2026.q4.
"""

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.runtime import portal_workarounds
from ldm_core.runtime.portal_workarounds import sync_workarounds

JAR = "com.liferay.custom.dxp.routes.domain.jar"


class _Manager:
    """Only what sync_workarounds reads: the real tag parser."""

    @staticmethod
    def parse_version(tag):
        return BaseHandler.parse_version(cast(Any, None), tag)


class TheBundleFollowsTheTag(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.modules = Path(self._td.name) / "osgi" / "modules"

    def tearDown(self):
        self._td.cleanup()

    def _sync(self, tag):
        sync_workarounds(_Manager(), {"modules": self.modules}, tag)
        if not self.modules.is_dir():
            return []
        return sorted(p.name for p in self.modules.iterdir())

    def test_it_is_installed_on_every_line_in_its_window(self):
        for tag in ("2026.q1.14-lts", "2026.q2.3", "2026.q3.0", "2026.q3.5"):
            with self.subTest(tag=tag):
                self.assertEqual(self._sync(tag), [JAR])

    def test_it_is_left_out_where_it_cannot_resolve(self):
        for tag in ("2026.q1.13-lts", "2025.q4.2", "7.4.13-u100", "2026.q4.0", None):
            with self.subTest(tag=tag):
                self.assertEqual(self._sync(tag), [])

    def test_the_installed_copy_is_the_bundled_one(self):
        self._sync("2026.q3.5")
        bundled = portal_workarounds._bundled(portal_workarounds.WORKAROUNDS[0])
        self.assertEqual((self.modules / JAR).read_bytes(), bundled.read_bytes())

    def test_moving_to_a_line_outside_the_window_removes_it(self):
        self._sync("2026.q3.5")
        self.assertEqual(self._sync("2025.q4.2"), [])

    def test_an_older_copy_is_replaced_not_joined(self):
        self.modules.mkdir(parents=True)
        (self.modules / "com.liferay.custom.dxp.routes.domain-0.9.0.jar").write_text(
            "old"
        )
        (self.modules / JAR).write_text("stale")
        self.assertEqual(self._sync("2026.q3.5"), [JAR])
        self.assertNotEqual((self.modules / JAR).read_text(errors="ignore"), "stale")

    def test_the_projects_own_modules_are_untouched(self):
        self.modules.mkdir(parents=True)
        (self.modules / "my-module.jar").write_text("mine")
        self.assertEqual(self._sync("2025.q4.2"), ["my-module.jar"])
        self.assertEqual(self._sync("2026.q3.5"), [JAR, "my-module.jar"])

    def test_a_retired_workaround_is_removed_everywhere(self):
        self._sync("2026.q3.5")
        with (
            patch.object(portal_workarounds, "WORKAROUNDS", ()),
            patch.object(
                portal_workarounds,
                "RETIRED_PREFIXES",
                ("com.liferay.custom.dxp.routes.domain",),
            ),
        ):
            self.assertEqual(self._sync("2026.q3.5"), [])


class TheRunCallsIt(unittest.TestCase):
    """The call site: the ComposerStage syncs the bundles for this run's tag."""

    def test_composer_stage_syncs_for_the_resolved_tag(self):
        from ldm_core.pipelines.run import ComposerStage, RunPipelineContext

        context = RunPipelineContext(MagicMock())
        context.manager.non_interactive = True
        context.manager.parse_version = _Manager.parse_version
        context.set("project_id", "proj")
        context.set("is_new_project", False)
        context.set("dry_run", False)
        context.set("no_up", False)
        context.set("use_shared_search", False)
        context.set("host_name", "localhost")
        context.set("tag", "2026.q3.5")
        context.set(
            "project_meta", {"container_name": "proj", "ssl": "false", "port": 8080}
        )
        root = MagicMock()
        root.__truediv__.return_value.exists.return_value = False
        paths = {"root": root, "configs": MagicMock()}
        context.set("paths", paths)
        context.set("infra_ports", {})
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("ldm_core.pipelines.run.UI"),
            patch("ldm_core.pipelines.run.sync_workarounds") as sync,
        ):
            try:
                ComposerStage().execute(context)
            except Exception:
                pass
        sync.assert_called_once_with(context.manager, paths, "2026.q3.5")


if __name__ == "__main__":
    unittest.main()
