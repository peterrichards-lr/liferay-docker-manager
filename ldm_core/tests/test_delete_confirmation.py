"""`ldm rm --delete` must say what it is about to destroy (LDM-#1703).

`--delete` is the most destructive thing LDM does -- containers, volumes, the
shared database schema, the registry entry and the project directory -- and it
was the only destructive command that never asked. `ldm prune` stops to ask
before removing DATA volumes (`diagnostics/prune.py:278`); this proceeded on
the strength of one flag.

Two things make the placement of the prompt matter more than its wording.

**It has to run before the teardown, not at the deletion.** Inside `cmd_down`'s
loop, `compose down -v` destroys the volumes and the shared schema is dropped
long before `safe_rmtree` removes the directory. A prompt at the deletion site
would guard only the directory -- the cheap, reproducible part -- while the
database was already gone.

**The non-interactive gate is load-bearing.** `UI.ask` returns its default
verbatim under `-y`, and `UI.confirm` reads an unchanged default as "no", so
`UI.confirm(..., "N")` evaluates to **False** in automation. That is measured,
not assumed (`test_confirm_defaults_to_false_under_minus_y` below). Gating on
the default rather than on `non_interactive` would therefore have made every
scripted `ldm rm --delete` silently refuse to delete -- including the 70 calls
across `scripts/verify_e2e_refactor.{sh,ps1}` -- while still reporting success.
That test exists to keep the trap documented, not because the code relies on it.
"""

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.runtime.orchestration import OrchestrationService


class _Manager(BaseHandler):
    def __init__(self, tmp, non_interactive=True, dry_run=False):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = non_interactive
        self.dry_run = dry_run
        self.target = None
        self.tmp = tmp
        # The delete path resolves the database engine/mode pair off this.
        self.defaults = {}
        self.rmtree_calls: list = []
        self.commands: list = []
        self.manager = cast(Any, self)
        self.orchestration = OrchestrationService(self)

    def run_command(self, cmd, **kw):  # type: ignore[override]
        self.commands.append(list(cmd))
        return ""

    def read_meta(self, *a, **k):
        return {"project_name": self.tmp.name}

    def detect_project_path(self, *a, **k):
        return self.tmp

    def validate_project_dns(self, *a, **k):
        return (None, [], [])

    def get_compose_base(self, *a, **k):
        return ["docker", "compose"]

    def safe_rmtree(self, path):  # type: ignore[override]
        self.rmtree_calls.append(path)

    def unregister_project(self, *a, **k):
        pass


def _project(tmp_root, name="probe", size_bytes=2048, snapshot=False):
    root = tmp_root / name
    (root / "files").mkdir(parents=True)
    (root / "files" / "portal-ext.properties").write_bytes(b"x" * size_bytes)
    if snapshot:
        (root / "snapshots" / "snap-1").mkdir(parents=True)
        (root / "snapshots" / "snap-1" / "dump.sql").write_bytes(b"y")
    return root


class TheAutomationGate(unittest.TestCase):
    """The regression this feature could most easily have caused."""

    def test_confirm_defaults_to_false_under_minus_y(self):
        """Documents the trap the gate exists to avoid.

        Not asserting production behaviour -- asserting the property of
        `UI.confirm` that makes gating on its return value wrong in automation.
        """
        from ldm_core.ui import UI

        original = UI.NON_INTERACTIVE
        try:
            UI.NON_INTERACTIVE = True
            self.assertFalse(
                UI.confirm("Delete?", "N"),
                "if this ever returns True under -y, the comment in cmd_down "
                "explaining the gate is stale and should be corrected",
            )
        finally:
            UI.NON_INTERACTIVE = original

    def test_non_interactive_delete_does_not_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td))
            manager = _Manager(root, non_interactive=True)
            service = manager.orchestration

            with patch.object(service, "_confirm_permanent_deletion") as confirm:
                with patch("ldm_core.runtime.orchestration.UI"):
                    service.cmd_down("probe", delete=True)

            confirm.assert_not_called()

    def test_non_interactive_delete_still_deletes(self):
        """The 70 scripted `rm --delete` calls must keep working."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td))
            manager = _Manager(root, non_interactive=True)

            with patch("ldm_core.runtime.orchestration.UI"):
                manager.orchestration.cmd_down("probe", delete=True)

            self.assertIn(
                root,
                manager.rmtree_calls,
                "a non-interactive delete removed nothing -- automation is broken",
            )


class TheInteractivePrompt(unittest.TestCase):
    def _run(self, answer, **project_kwargs):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = _project(Path(td.name), **project_kwargs)
        manager = _Manager(root, non_interactive=False)

        with patch("ldm_core.runtime.orchestration.UI") as ui:
            ui.confirm.return_value = answer
            ui.format_size.side_effect = lambda n: f"{n}B"
            manager.orchestration.cmd_down("probe", delete=True)
        return manager, ui

    def test_declining_removes_nothing(self):
        manager, ui = self._run(answer=False)

        self.assertTrue(ui.confirm.called, "the user was never asked")
        self.assertEqual(
            manager.rmtree_calls, [], "declining still deleted the project"
        )
        self.assertEqual(
            manager.commands,
            [],
            "declining still ran docker -- the prompt is after the teardown",
        )

    def test_accepting_proceeds(self):
        manager, ui = self._run(answer=True)

        self.assertTrue(ui.confirm.called)
        self.assertIn(manager.tmp, manager.rmtree_calls)

    def test_the_prompt_defaults_to_no(self):
        _, ui = self._run(answer=False)

        self.assertEqual(
            ui.confirm.call_args[0][1],
            "N",
            "a destructive prompt must not default to yes",
        )

    def test_without_delete_nothing_is_asked(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = _project(Path(td.name))
        manager = _Manager(root, non_interactive=False)

        with patch("ldm_core.runtime.orchestration.UI") as ui:
            manager.orchestration.cmd_down("probe", delete=False)

        self.assertFalse(
            ui.confirm.called, "a plain `ldm down` must not prompt to delete"
        )

    def test_dry_run_does_not_prompt(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = _project(Path(td.name))
        manager = _Manager(root, non_interactive=False, dry_run=True)

        with patch("ldm_core.runtime.orchestration.UI") as ui:
            manager.orchestration.cmd_down("probe", delete=True)

        self.assertFalse(ui.confirm.called, "a dry run must report, not ask")
        self.assertEqual(manager.rmtree_calls, [])


class TheFactsItReports(unittest.TestCase):
    """Size and snapshot presence are the two facts that decide recoverability."""

    def test_size_is_measured(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), size_bytes=4096)
            size, _ = OrchestrationService._project_removal_facts(root)
            self.assertGreaterEqual(size, 4096)

    def test_a_snapshot_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), snapshot=True)
            _, has_snapshot = OrchestrationService._project_removal_facts(root)
            self.assertTrue(has_snapshot)

    def test_no_snapshot_is_reported_as_such(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), snapshot=False)
            _, has_snapshot = OrchestrationService._project_removal_facts(root)
            self.assertFalse(has_snapshot)

    def test_an_empty_snapshots_directory_is_not_a_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td))
            (root / "snapshots").mkdir()
            _, has_snapshot = OrchestrationService._project_removal_facts(root)
            self.assertFalse(
                has_snapshot,
                "an empty snapshots/ would promise an undo that does not exist",
            )

    def test_an_unreadable_project_is_still_deletable(self):
        """Refusing to delete because `stat` failed is worse than a vague prompt."""
        missing = Path(tempfile.gettempdir()) / "ldm-does-not-exist-1703"
        size, has_snapshot = OrchestrationService._project_removal_facts(missing)
        self.assertIsNone(size)
        self.assertFalse(has_snapshot)

    def test_the_facts_reach_a_default_run_not_only_a_verbose_one(self):
        """The bug a mocked-UI test cannot see.

        The first version of this used `UI.detail`, asserted `ui.detail` had
        been called, and passed -- while the real command printed the prompt
        and none of the facts, because `UI.detail` is gated behind INFO_MODE /
        VERBOSE. Found by running `ldm rm --delete`, not by the suite.

        So this drives the REAL `UI` with both flags off and reads stdout.
        """
        import contextlib
        import io

        from ldm_core.ui import UI

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = _project(Path(td.name), size_bytes=4096, snapshot=False)
        manager = _Manager(root, non_interactive=False)

        saved = (UI.INFO_MODE, UI.VERBOSE, UI.NON_INTERACTIVE)
        buffer = io.StringIO()
        try:
            UI.INFO_MODE = False
            UI.VERBOSE = False
            UI.NON_INTERACTIVE = True  # so confirm() returns without reading stdin
            with contextlib.redirect_stdout(buffer):
                manager.orchestration._confirm_permanent_deletion([root])
        finally:
            UI.INFO_MODE, UI.VERBOSE, UI.NON_INTERACTIVE = saved

        shown = buffer.getvalue()
        self.assertIn(root.name, shown, "the project was never named")
        self.assertIn("no snapshot", shown)
        self.assertIn(
            "database state will be lost",
            shown,
            "the consequence must be visible on a DEFAULT run, not only under -v",
        )
        self.assertRegex(shown, r"\d", "no size was reported")


if __name__ == "__main__":
    unittest.main()
