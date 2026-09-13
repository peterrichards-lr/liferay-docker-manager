"""A linked project must remember the workspace it came from (LDM-#1684).

`ba24c012` (PR #497) rebuilt the import's `project_meta` literal without
`"workspace_path": str(source) if is_init_from else None`, and nothing has
written the key since. Two readers depend on it and both fail quietly:

* `workspace/monitor.py:39` -- `ldm monitor <project>` with no path argument
  falls back to the stored path and refuses when it is missing. `ldm link`
  itself kept working because `cmd_link` hands the source to `cmd_monitor`
  directly; it is re-attaching the watcher afterwards that broke.
* `handlers/snapshot.py:422` -- reads it to record the workspace's git origin
  into a package, and skips that silently when it is absent.

`docs/reference/cli/core.md` still described the behaviour that had stopped
happening: "This command records the workspace path in the project metadata".

The restored write differs from the removed line in two ways, and both are
asserted below: it never writes `None` over an existing value, and it never
records a path that is not a directory on disk.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ldm_core.pipelines.base import PipelineContext
from ldm_core.pipelines.import_pipeline import ProjectSetupStage


class _Context(PipelineContext):
    """Minimal stand-in; only the keys this helper reads are populated."""

    def __init__(self, **data):
        super().__init__(**data)
        self.manager = None


class LinkedWorkspacePathTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "my-workspace"
        self.workspace.mkdir()

        patcher = patch("ldm_core.pipelines.import_pipeline.UI")
        self.ui = patcher.start()
        self.addCleanup(patcher.stop)

    def _record(self, meta=None, **context_data):
        meta = meta if meta is not None else {}
        ProjectSetupStage._record_linked_workspace(_Context(**context_data), meta)
        return meta

    def test_a_linked_import_records_the_source_workspace(self):
        meta = self._record(is_init_from=True, source_resolved=self.workspace)

        self.assertEqual(meta["workspace_path"], str(self.workspace))

    def test_a_plain_import_records_nothing(self):
        meta = self._record(is_init_from=False, source_resolved=self.workspace)

        self.assertNotIn("workspace_path", meta)

    def test_a_plain_import_does_not_erase_an_existing_link(self):
        """The removed line wrote `None` here, which would unlink the project.

        `ldm import` over an already-linked project is a normal thing to do --
        re-importing a package into it, for instance -- and it must not quietly
        detach the watcher's source.
        """
        meta = self._record(
            meta={"workspace_path": "/somewhere/previously/linked"},
            is_init_from=False,
            source_resolved=self.workspace,
        )

        self.assertEqual(meta["workspace_path"], "/somewhere/previously/linked")

    def test_an_archive_source_is_not_a_workspace(self):
        archive = Path(self._tmp.name) / "project.ldmp"
        archive.write_bytes(b"")

        meta = self._record(is_init_from=True, source_resolved=archive)

        self.assertNotIn("workspace_path", meta)

    def test_a_vanished_directory_is_not_recorded(self):
        """`ldm clone` imports from a scratch checkout it deletes straight after."""
        gone = Path(self._tmp.name) / "clone_20260913"

        meta = self._record(is_init_from=True, source_resolved=gone)

        self.assertNotIn("workspace_path", meta)

    def test_a_missing_source_does_not_raise(self):
        meta = self._record(is_init_from=True)

        self.assertNotIn("workspace_path", meta)


class TheKeySurvivesTheMetaRoundTripTestCase(unittest.TestCase):
    """`monitor` reads it back through `read_meta`, not out of the dict."""

    def test_write_then_read_returns_the_path(self):
        from ldm_core.utils import read_meta, write_meta

        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "my-workspace"
            workspace.mkdir()
            meta_file = Path(tmp) / "project" / "meta"
            meta_file.parent.mkdir()

            meta: dict = {}
            with patch("ldm_core.pipelines.import_pipeline.UI"):
                ProjectSetupStage._record_linked_workspace(
                    _Context(is_init_from=True, source_resolved=workspace), meta
                )
            write_meta(meta_file, meta)

            self.assertEqual(read_meta(meta_file).get("workspace_path"), str(workspace))


if __name__ == "__main__":
    unittest.main()
