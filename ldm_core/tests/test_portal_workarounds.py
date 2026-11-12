"""LDM installs the bundles that work around Liferay defects (LDM-#2289).

The first is ``dxp-routes-domain``: Liferay's routes writer publishes Tomcat's
``:8080`` in the DXP domain behind a TLS-terminating proxy (LDM-#2344). It
imports ``com.liferay.portal.kernel.util`` [96.7,101.0), so it is placed only
for 2026.q1.14-lts up to, not including, 2026.q4.

A workaround holds one jar per window, so a line that needs a different build
gets its own variant (LDM-#2289).
"""

import itertools
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.runtime import portal_workarounds
from ldm_core.runtime.portal_workarounds import Variant, Workaround, sync_workarounds

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
        bundled = portal_workarounds._bundled(
            portal_workarounds.WORKAROUNDS[0].variants[0]
        )
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


class EachLineGetsItsOwnVariant(unittest.TestCase):
    """Two builds of one bundle, for two windows; a tag selects at most one."""

    EARLY = "com.liferay.custom.example.jar"
    LATE = "com.liferay.custom.example-q4.jar"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name)
        self.modules = root / "osgi" / "modules"
        self.bundled = root / "bundled"
        self.bundled.mkdir()
        (self.bundled / self.EARLY).write_text("early build")
        (self.bundled / self.LATE).write_text("late build")
        self.workaround = Workaround(
            issue="LDM-#0",
            prefix="com.liferay.custom.example",
            variants=(
                Variant(jar=self.EARLY, first_tag=(2026, 1, 14), end_tag=(2026, 4, 0)),
                Variant(jar=self.LATE, first_tag=(2026, 4, 0), end_tag=(2027, 1, 0)),
            ),
        )

    def tearDown(self):
        self._td.cleanup()

    def _sync(self, tag, workaround=None):
        with (
            patch.object(
                portal_workarounds, "WORKAROUNDS", (workaround or self.workaround,)
            ),
            patch.object(
                portal_workarounds,
                "_bundled",
                lambda variant: self.bundled / variant.jar,
            ),
        ):
            sync_workarounds(_Manager(), {"modules": self.modules}, tag)
        if not self.modules.is_dir():
            return {}
        return {p.name: p.read_text() for p in self.modules.iterdir()}

    def test_each_window_installs_its_own_build(self):
        self.assertEqual(self._sync("2026.q3.5"), {self.EARLY: "early build"})
        self.assertEqual(self._sync("2026.q4.1"), {self.LATE: "late build"})

    def test_moving_between_lines_swaps_rather_than_adds(self):
        self._sync("2026.q4.1")
        self.assertEqual(self._sync("2026.q2.3"), {self.EARLY: "early build"})

    def test_ending_the_last_window_retires_it_from_that_line_on(self):
        fixed_in_q4 = Workaround(
            issue="LDM-#0",
            prefix="com.liferay.custom.example",
            variants=self.workaround.variants[:1],
        )
        self._sync("2026.q4.1")
        self.assertEqual(self._sync("2026.q4.1", fixed_in_q4), {})
        self.assertEqual(
            self._sync("2026.q3.5", fixed_in_q4), {self.EARLY: "early build"}
        )


class TheShippedWorkaroundsAreConsistent(unittest.TestCase):
    def test_windows_do_not_overlap_and_every_jar_is_vendored(self):
        for workaround in portal_workarounds.WORKAROUNDS:
            windows = sorted((v.first_tag, v.end_tag) for v in workaround.variants)
            for (_, end), (start, _) in itertools.pairwise(windows):
                self.assertLessEqual(end, start, workaround.issue)
            for variant in workaround.variants:
                self.assertLess(variant.first_tag, variant.end_tag, variant.jar)
                self.assertTrue(variant.jar.startswith(workaround.prefix))
                self.assertTrue(
                    portal_workarounds._bundled(variant).is_file(), variant.jar
                )


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
