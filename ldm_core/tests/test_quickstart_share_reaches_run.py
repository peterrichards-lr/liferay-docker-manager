"""`ldm quickstart --share` must reach the run pipeline, not race it (LDM-#2268).

Quickstart called `share.cmd_start` BEFORE `cmd_run`. Since LDM-#2009 that
command refuses a project that is not running -- "nothing to share", exit 3 --
so `ldm quickstart aica --share` downloaded, restored and seeded, then died
with no stack. The documented example was the failing invocation.

The old tests mocked the share handler and asserted the call was made, in that
order: they pinned the defect. These assert the opposite, and then cross the
join that actually matters -- that what quickstart leaves on `args` is what
the run pipeline's own share resolver reads, by running the real resolver
against it rather than asserting on a flag nobody consults.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.tests.test_workspace import MockWorkspaceManager


class QuickstartShareIsHandedToRun(unittest.TestCase):
    def setUp(self):
        self.manager = MockWorkspaceManager()
        self.manager.args.no_seed = False
        self.events: list[str] = []
        self.at_run: dict = {}

        share = MagicMock()
        share.cmd_start.side_effect = lambda *_a, **_k: self.events.append("share")
        share.resolve_share_config.return_value = ("lfr-tunnel", None)
        self.manager.share = share

        self._td = TemporaryDirectory()
        self.project_dir = Path(self._td.name) / "liferay-ai-commerce-accelerator"
        self.project_dir.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def _run(self, **kwargs):
        from ldm_core.pipelines.run import ConfigResolutionStage

        def fake_run(*_a, **_k):
            self.events.append("run")
            # The join: the run pipeline's REAL share resolver, reading the
            # manager exactly as quickstart left it.
            self.at_run = ConfigResolutionStage()._resolve_share_and_expose(
                self.manager, {}
            )
            return True

        with (
            patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import"),
            patch.object(
                MockWorkspaceManager,
                "detect_project_path",
                return_value=self.project_dir,
            ),
            patch(
                "ldm_core.handlers.base.BaseHandler.read_meta",
                return_value={"tag": "2026.q3.0", "db_type": "postgresql"},
            ),
            patch.object(MockWorkspaceManager, "setup_paths", return_value={}),
            patch(
                "ldm_core.handlers.assets.AssetService._fetch_seed", return_value=True
            ),
            patch("ldm_core.handlers.runtime.RuntimeService.cmd_run", fake_run),
        ):
            return self.manager.workspace.cmd_quickstart("aica", **kwargs)

    def test_no_tunnel_is_requested_before_the_stack_runs(self):
        self._run(share=True, share_subdomain="demo-aica")
        self.assertIn("run", self.events)
        self.assertNotIn(
            "share",
            self.events[: self.events.index("run")],
            "share.cmd_start ran before cmd_run. It refuses a project that is "
            "not running (LDM-#2009), so quickstart --share exits 3 with no "
            "stack.",
        )

    def test_the_run_pipeline_sees_the_share_request(self):
        self._run(share=True, share_subdomain="demo-aica")
        self.assertTrue(
            self.at_run.get("is_share"),
            f"the run pipeline's resolver did not see --share: {self.at_run}",
        )
        self.assertEqual(self.at_run.get("share_subdomain"), "demo-aica")

    def test_without_share_nothing_is_shared(self):
        """Control: the hand-over must not turn sharing on by itself."""
        self.manager.args.share = False
        self.manager.args.share_subdomain = None
        self._run()
        self.assertNotIn("share", self.events)
        self.assertFalse(self.at_run.get("is_share"), self.at_run)


if __name__ == "__main__":
    unittest.main()
