"""Behavioural cover for ``ldm_core/workspace/versioning.py`` (LDM-#1515).

``cmd_set_version`` rewrites ``liferay.workspace.product`` in a user's
``gradle.properties`` and then rewrites that project's ``meta``. It had zero
tests, in a cycle where two tags were burnt over version metadata (#1491,
#1498).

Every test here drives the real command against a real workspace on disk and
asserts the **files afterwards** -- the rewritten property, the neighbouring
properties that must survive it, and the persisted ``tag``/``portal`` -- rather
than asserting that a helper was called. The one thing stubbed is
``resolve_liferay_docker_tag``: it performs an HTTP GET to releases.liferay.com
and caches into the developer's real home, so a test that let it run would be
both flaky and a violation of the "no real state" rule in
``.agents/skills/testing-and-ci/SKILL.md``.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.workspace import WorkspaceService

GRADLE_PROPERTIES = """\
liferay.workspace.product=dxp-2025.q1.1
liferay.workspace.docker.image.liferay=liferay/dxp:2025.q1.1-lts
liferay.workspace.environment=local
"""


class _FakeManager(BaseHandler):
    """Minimal manager: real ``read_meta``/``write_meta``, fixed project path."""

    def __init__(self, project_path):
        args = SimpleNamespace(
            project="proj", non_interactive=True, verbose=False, host_name="localhost"
        )
        super().__init__(args)
        self._project_path = project_path

        from ldm_core.defaults import DefaultsManager

        self.defaults = DefaultsManager()

    def detect_project_path(self, project_id=None, for_init=False, fatal=True):
        return self._project_path


class TestCmdSetVersion(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.manager = _FakeManager(self.project)
        self.service = WorkspaceService(self.manager)

    def _write_gradle(self, content=GRADLE_PROPERTIES, subdir=None):
        target_dir = self.project if subdir is None else self.project / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "gradle.properties"
        path.write_text(content, encoding="utf-8")
        return path

    def _set_version(self, product_key, resolved=("2026.q1.7-lts", False)):
        with patch(
            "ldm_core.utils.resolve_liferay_docker_tag", return_value=resolved
        ) as mock_resolve:
            self.service.cmd_set_version(product_key)
        return mock_resolve

    def test_rewrites_the_product_property_and_leaves_its_neighbours_alone(self):
        gradle = self._write_gradle()

        self._set_version("dxp-2026.q1.7")

        lines = gradle.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "liferay.workspace.product=dxp-2026.q1.7")
        # The regex is anchored on the property name; a greedier one would eat
        # the following lines. Assert they are byte-identical, not merely present.
        self.assertEqual(
            lines[1],
            "liferay.workspace.docker.image.liferay=liferay/dxp:2025.q1.1-lts",
        )
        self.assertEqual(lines[2], "liferay.workspace.environment=local")
        self.assertNotIn("dxp-2025.q1.1", lines[0])

    def test_persists_the_resolved_docker_tag_to_the_project_meta(self):
        self._write_gradle()

        self._set_version("dxp-2026.q1.7", resolved=("2026.q1.7-lts", False))

        meta = self.manager.read_meta(self.project)
        self.assertEqual(meta.get("tag"), "2026.q1.7-lts")
        self.assertEqual(meta.get("portal"), "false")

    def test_a_portal_release_is_recorded_as_portal_true(self):
        self._write_gradle()

        self._set_version("portal-7.4.3.132-ga132", resolved=("7.4.3.132-ga132", True))

        meta = self.manager.read_meta(self.project)
        self.assertEqual(meta.get("tag"), "7.4.3.132-ga132")
        self.assertEqual(meta.get("portal"), "true")

    def test_unresolvable_key_falls_back_to_stripping_the_product_prefix(self):
        self._write_gradle()

        self._set_version("dxp-2026.q1.7", resolved=(None, None))

        meta = self.manager.read_meta(self.project)
        self.assertEqual(meta.get("tag"), "2026.q1.7")
        # `portal` is deliberately NOT written when nothing resolved -- guessing
        # it would silently flip an existing project between portal and DXP.
        self.assertNotIn("portal", meta)

    def test_existing_meta_keys_survive_the_bump(self):
        self._write_gradle()
        (self.project / "meta").write_text(
            "db_type=mysql\ntag=2025.q1.1-lts\nhost_name=demo.test\n", encoding="utf-8"
        )

        self._set_version("dxp-2026.q1.7")

        meta = self.manager.read_meta(self.project)
        self.assertEqual(meta.get("tag"), "2026.q1.7-lts")
        self.assertEqual(meta.get("db_type"), "mysql")
        self.assertEqual(meta.get("host_name"), "demo.test")

    def test_falls_back_to_the_liferay_subdirectory_layout(self):
        nested = self._write_gradle(subdir="liferay")

        self._set_version("dxp-2026.q1.7")

        self.assertIn(
            "liferay.workspace.product=dxp-2026.q1.7",
            nested.read_text(encoding="utf-8"),
        )
        # The root file must not be conjured into existence as a side effect.
        self.assertFalse((self.project / "gradle.properties").exists())

    def test_a_workspace_without_the_property_aborts_without_touching_the_file(self):
        gradle = self._write_gradle("liferay.workspace.environment=local\n")

        with (
            self.assertRaises(SystemExit),
            patch(
                "ldm_core.utils.resolve_liferay_docker_tag", return_value=("x", False)
            ),
        ):
            self.service.cmd_set_version("dxp-2026.q1.7")

        self.assertEqual(
            gradle.read_text(encoding="utf-8"), "liferay.workspace.environment=local\n"
        )
        self.assertFalse((self.project / "meta").exists())

    def test_a_workspace_without_gradle_properties_aborts(self):
        with self.assertRaises(SystemExit):
            self.service.cmd_set_version("dxp-2026.q1.7")

        self.assertFalse((self.project / "meta").exists())

    def test_an_unresolvable_project_path_aborts(self):
        self.manager._project_path = None

        with self.assertRaises(SystemExit):
            self.service.cmd_set_version("dxp-2026.q1.7")


if __name__ == "__main__":
    unittest.main()
