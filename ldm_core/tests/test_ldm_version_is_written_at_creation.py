"""LDM-#2163: `init`/`run` must stamp the version that created the project.

`ldm_version` was written into a project's meta by the **import** path and by
nothing else. `resolve_infrastructure_mode` reads
`meta.get("ldm_version", "0.0.0")` and sends anything below `2.14.0` to the
legacy isolated/sidecar modes -- a guard built to stop a project created
*before* the shared model from being hijacked into shared infrastructure.

With no version written it fired on projects created **today**, so two
projects made on the same machine on the same day got different infrastructure
depending on which command made them, and nothing said so. Verified on a
project created 2026-10-14: `ldm_version: None`, `search_mode: 'sidecar'`,
`database_mode: 'isolated'`.

`TheVersionGuardIsWhyThisMatters` pins the consequence rather than the
mechanism: without it, a future reader could "tidy away" the stamp and only
discover what it was for when projects silently changed infrastructure again.

**No performance claim is made here, deliberately.** The figures that looked
like evidence were refuted in LDM-#2167: two boots attributed to search mode
differed by host, one a Windows machine with 7.6 GB allocated to Docker
Desktop. They measured the host, not the mode.
"""

import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

from ldm_core.constants import VERSION
from ldm_core.pipelines.run import (
    ProjectInitializationStage,
    RunPipelineContext,
    announce_resolved_infrastructure,
)
from ldm_core.utils import resolve_infrastructure_mode


class TheVersionGuardIsWhyThisMatters(unittest.TestCase):
    """The consequence of the stamp, not the stamp itself."""

    DEFAULTS: ClassVar[dict] = {"search_mode": "shared", "database_mode": "shared"}

    def test_without_a_version_the_legacy_guard_fires(self):
        self.assertEqual(
            resolve_infrastructure_mode("search_mode", {}, self.DEFAULTS), "sidecar"
        )
        self.assertEqual(
            resolve_infrastructure_mode("database_mode", {}, self.DEFAULTS), "isolated"
        )

    def test_with_the_current_version_the_default_is_honoured(self):
        meta = {"ldm_version": VERSION}
        self.assertEqual(
            resolve_infrastructure_mode("search_mode", meta, self.DEFAULTS), "shared"
        )
        self.assertEqual(
            resolve_infrastructure_mode("database_mode", meta, self.DEFAULTS), "shared"
        )

    def test_a_genuinely_old_project_is_still_protected(self):
        # The guard must keep doing its job: a project created before the
        # shared model has isolated state on disk, and must not be hijacked.
        meta = {"ldm_version": "2.13.9"}
        self.assertEqual(
            resolve_infrastructure_mode("search_mode", meta, self.DEFAULTS), "sidecar"
        )


class TheVersionIsStampedAtCreation(unittest.TestCase):
    def setUp(self):
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir_obj.cleanup)
        self.root = Path(self.tmp_dir_obj.name) / "proj"
        self.root.mkdir()

        self.context = RunPipelineContext(MagicMock())
        self.context.manager.non_interactive = True
        self.context.manager.detect_project_path.return_value = self.root
        self.context.manager.read_meta.return_value = {}
        self.context.manager.setup_paths.return_value = {"root": self.root}
        self.context.manager.args.scale_list = None
        self.context.manager.args.vanilla = False
        self.context.manager.args.command = "run"

    def _run_stage(self):
        with patch("ldm_core.pipelines.run.UI"):
            ProjectInitializationStage().execute(self.context)
        return self.context.get("project_meta")

    def test_a_new_project_is_stamped_with_the_creating_version(self):
        self.assertEqual(self._run_stage().get("ldm_version"), VERSION)

    def test_an_existing_project_is_not_back_filled(self):
        # The migration constraint on the issue: writing a version into a
        # project created by init/run between v2.14.0 and now would flip it
        # from sidecar to shared on its next boot, while its on-disk state is
        # isolated -- exactly the hijack the guard was built to prevent.
        (self.root / "meta").write_text("{}")
        self.assertIsNone(self._run_stage().get("ldm_version"))


class TheCreationAnnouncementTellsTheUserWhatTheyGot(unittest.TestCase):
    """LDM-#2163: the fix flips new projects to shared AND silences the only
    explanation they were getting.

    `should_offer_shared_database_tip` speaks only when the resolved mode is
    `isolated`, so it goes quiet for exactly the projects this change moves.
    The two halves therefore ship together -- without this, the fix would
    change what every new project gets and remove the sentence that mentioned
    it.
    """

    def setUp(self):
        self.manager = MagicMock()
        self.manager.args.search_mode = None
        self.manager.args.db_mode = None
        self.manager.defaults.has_explicit.return_value = False

    def _announce(self, **kw):
        params = {
            "manager": self.manager,
            "search_mode": "shared",
            "db_mode": "shared",
            "is_new_project": True,
            "project_id": "demo",
        }
        params.update(kw)
        with patch("ldm_core.pipelines.run.UI") as mock_ui:
            result = announce_resolved_infrastructure(**params)
        said = " ".join(str(c.args[0]) for c in mock_ui.info.call_args_list if c.args)
        return result, said, mock_ui

    def test_it_names_both_resolved_modes(self):
        _, said, _ = self._announce()
        self.assertIn("Search: shared", said)
        self.assertIn("Database: shared", said)

    def test_it_is_silent_on_a_project_that_already_existed(self):
        result, said, _ = self._announce(is_new_project=False)
        self.assertFalse(result)
        self.assertEqual(said, "")

    def test_it_is_visible_by_default_not_hidden_behind_info(self):
        # UI.detail is gated behind --info/--verbose (LDM-#1036), so an
        # announcement written with it would be invisible in exactly the
        # situation it exists to explain -- the failure LDM-#1790 and
        # LDM-#2180 each removed.
        _, _, mock_ui = self._announce()
        self.assertTrue(mock_ui.info.called)
        self.assertFalse(mock_ui.detail.called)

    def test_an_explicit_flag_is_reported_as_such(self):
        self.manager.args.search_mode = "sidecar"
        _, said, _ = self._announce(search_mode="sidecar")
        self.assertIn("Search: sidecar (chosen with a flag)", said)

    def test_a_configured_default_is_distinguished_from_a_convention(self):
        self.manager.defaults.has_explicit.return_value = True
        _, said, _ = self._announce()
        self.assertIn("from your config", said)
        self.assertNotIn("(default)", said)

    def test_the_counter_offer_names_the_project(self):
        _, said, _ = self._announce()
        self.assertIn("ldm run demo --search-mode sidecar --db-mode isolated", said)

    def test_no_counter_offer_when_nothing_is_shared(self):
        _, said, _ = self._announce(search_mode="sidecar", db_mode="isolated")
        self.assertIn("Search: sidecar", said)
        self.assertNotIn("--search-mode sidecar", said)
