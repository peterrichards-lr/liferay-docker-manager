"""An imported workspace's `liferay.workspace.product` sets the tag (LDM-#1693).

PR #497 (`ba24c012`) rebuilt the import's `project_meta` literal without the
`gradle.properties` read, so a workspace pinned to `dxp-2026.q1.7` recorded no
`tag` at all. `_resolve_tag` then falls through to discovery and the project
boots whatever the LTS default happens to be.

Four behaviours were lost with that read. Three are restored here -- the tag,
the portal/DXP flag, and the fallback for an unresolvable key. The fourth, the
seeded start, needs nothing: `pipelines/run.py:1264` calls `_ensure_seeded`
with whatever tag it resolves, so recording the right tag restores the right
seed as a consequence.

The interaction with LDM-#1658 is the reason this was fixed before the v2.22.0
cut rather than after. With nothing recorded, discovery and the pin disagreed on
every run, so the mismatch warning fired constantly instead of only when the
user had actually chosen a different tag. Measured: a workspace pinned to
`dxp-2026.q3.0` warned on every `ldm run`, resolving `2026.q1.12-lts`.

`resolve_liferay_docker_tag` is patched throughout -- it performs an HTTP GET to
releases.liferay.com and caches into the developer's real home.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from ldm_core.pipelines.base import PipelineContext
from ldm_core.pipelines.import_pipeline import ProjectSetupStage

GRADLE_PROPERTIES = """\
liferay.workspace.product=dxp-2026.q1.7
liferay.workspace.docker.image.liferay=liferay/dxp:2026.q1.7-lts
liferay.workspace.environment=local
"""


class _Manager:
    def __init__(self, tag=None):
        self.args = SimpleNamespace(tag=tag)


class _Context(PipelineContext):
    def __init__(self, manager=None, **data):
        super().__init__(**data)
        self.manager = manager or _Manager()


class WorkspaceProductTagTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir()

        patcher = patch("ldm_core.pipelines.import_pipeline.UI")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, content=GRADLE_PROPERTIES):
        (self.workspace / "gradle.properties").write_text(content, encoding="utf-8")

    def _apply(self, manager=None, resolved=("2026.q1.7-lts", False), meta=None):
        meta = meta if meta is not None else {}
        context = _Context(manager=manager, workspace_root=self.workspace)
        with patch(
            "ldm_core.utils.resolve_liferay_docker_tag", return_value=resolved
        ) as resolve:
            ProjectSetupStage._apply_workspace_product(context, meta)
        return meta, resolve

    def test_the_pin_becomes_the_project_tag(self):
        self._write()

        meta, _ = self._apply()

        self.assertEqual(meta["tag"], "2026.q1.7-lts")

    def test_a_dxp_pin_records_portal_false(self):
        self._write()

        meta, _ = self._apply()

        self.assertEqual(meta["portal"], "false")

    def test_a_portal_pin_records_portal_true(self):
        """The flag decides the image repository; losing it sends LDM to liferay/dxp."""
        self._write("liferay.workspace.product=portal-7.4.3.132-ga132\n")

        meta, _ = self._apply(resolved=("7.4.3.132-ga132", True))

        self.assertEqual(meta["tag"], "7.4.3.132-ga132")
        self.assertEqual(meta["portal"], "true")

    def test_an_unresolvable_key_falls_back_to_the_stripped_prefix(self):
        """Offline, or a key releases.json does not carry.

        Recording `2026.q1.7` unconfirmed beats recording nothing -- it is
        still the right image, and `_resolve_tag` can work with it.
        """
        self._write()

        meta, _ = self._apply(resolved=(None, None))

        self.assertEqual(meta["tag"], "2026.q1.7")
        self.assertNotIn("portal", meta, "an unconfirmed key cannot claim a repository")

    def test_an_explicit_tag_wins_over_the_pin(self):
        """LDM-#1658 settled this collision the other way from the original.

        The pre-refactor comment read "Workspace product always wins" and it
        overrode `-t`. The two rules have to agree, or LDM warns that an
        explicit tag disagrees with the pin and then silently overrides it.
        """
        self._write()

        meta, resolve = self._apply(manager=_Manager(tag="2026.q3.2"))

        self.assertEqual(meta, {})
        resolve.assert_not_called()

    def test_a_commented_out_pin_is_not_a_pin(self):
        self._write("#liferay.workspace.product=dxp-2026.q1.7\n")

        meta, _ = self._apply()

        self.assertEqual(meta, {})

    def test_a_workspace_with_no_pin_records_nothing(self):
        self._write("liferay.workspace.environment=local\n")

        meta, _ = self._apply()

        self.assertEqual(meta, {})

    def test_a_missing_gradle_properties_does_not_raise(self):
        meta, _ = self._apply()

        self.assertEqual(meta, {})

    def test_the_pin_overrides_a_tag_already_in_the_meta(self):
        """Re-importing from the workspace re-reads the workspace's answer."""
        self._write()

        meta, _ = self._apply(meta={"tag": "2026.q3.2"})

        self.assertEqual(meta["tag"], "2026.q1.7-lts")

    def test_manager_args_is_not_mutated(self):
        """`cmd_import` moved deliberately away from mutating manager.args."""
        self._write()
        manager = _Manager()

        self._apply(manager=manager)

        self.assertIsNone(manager.args.tag)


class LcpWorkspaceTestCase(unittest.TestCase):
    """The pin lives in `<repo>/liferay/gradle.properties` for a cloud source."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = patch("ldm_core.pipelines.import_pipeline.UI")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_nested_workspace_root_is_read(self):
        from ldm_core.pipelines.import_pipeline import ExtractionStage

        repo = Path(self._tmp.name) / "lcp-repo"
        (repo / "liferay").mkdir(parents=True)
        (repo / "liferay" / "LCP.json").write_text('{"id": "liferay"}')
        (repo / "liferay" / "gradle.properties").write_text(
            "liferay.workspace.product=dxp-2026.q1.7\n", encoding="utf-8"
        )

        context = _Context(extracted_source=repo)
        ExtractionStage._resolve_layout(context, repo)
        meta: dict = {}
        with patch(
            "ldm_core.utils.resolve_liferay_docker_tag",
            return_value=("2026.q1.7-lts", False),
        ):
            ProjectSetupStage._apply_workspace_product(context, meta)

        self.assertEqual(
            meta["tag"],
            "2026.q1.7-lts",
            "LDM-#1681 resolves workspace_root to <repo>/liferay; this must use it "
            "rather than re-deriving the path",
        )


if __name__ == "__main__":
    unittest.main()
