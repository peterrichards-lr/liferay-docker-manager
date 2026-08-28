"""Deleting a project must remove its volumes (LDM-#1414).

`cmd_down(delete=True)` swept containers by label but never volumes. Volumes
came off only as a side effect of `compose down -v`, and when the compose file
was absent -- deleted by hand, lost to a failed run, torn down out of order --
the whole step was skipped behind a `UI.debug` that needed `--verbose` to see.
Every volume the project owned was then orphaned permanently, and silently.

Measured on a developer machine for #1414: 32 volumes, **0 active**, 6.8 GB
reclaimable, against 12.5 GB free inside the Docker VM. 17 of those were
obviously LDM's by name yet invisible to every label filter.

Observed against the unfixed code before these were written: all five fail.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _sweep():
    """Imported lazily so TestCmdDownSweepsVolumes can still be collected when
    the helper does not exist -- otherwise a module-level import turns a
    behaviour failure into a collection error, and the wiring test proves
    nothing about behaviour."""
    from ldm_core.runtime.orchestration import _sweep_project_volumes

    return _sweep_project_volumes


class TestProjectVolumeSweep(unittest.TestCase):
    def setUp(self):
        self.manager = MagicMock()
        self.calls = []

    def _runner(self, volumes_by_filter, rm_result=""):
        """Fake run_command: answers `volume ls` per filter, records `volume rm`."""

        def run(cmd, **kw):
            self.calls.append(list(cmd))
            if "volume" in cmd and "ls" in cmd:
                flt = cmd[cmd.index("--filter") + 1]
                return volumes_by_filter.get(flt, "")
            if "volume" in cmd and "rm" in cmd:
                return rm_result
            if "df" in cmd:
                return ""
            return ""

        return run

    def test_volumes_are_found_by_uuid_label(self):
        """The UUID is exact and survives a rename (LDM-#1395)."""
        self.manager.run_command = self._runner(
            {"label=com.liferay.ldm.project.uuid=abc-123": "proj-data\nproj-state"}
        )
        with patch("ldm_core.runtime.orchestration.UI"):
            _sweep()(self.manager, ["docker"], "proj", "abc-123")

        removed = [c for c in self.calls if "rm" in c]
        names = {c[-1] for c in removed}
        self.assertEqual({"proj-data", "proj-state"}, names)

    def test_unlabelled_legacy_volumes_are_found_by_name_prefix(self):
        """17 of 32 volumes on the measured machine had no usable label."""
        self.manager.run_command = self._runner(
            {"name=^legacy-": "legacy-data\nlegacy-state"}
        )
        with patch("ldm_core.runtime.orchestration.UI"):
            _sweep()(self.manager, ["docker"], "legacy", None)

        names = {c[-1] for c in self.calls if "rm" in c}
        self.assertEqual(
            {"legacy-data", "legacy-state"},
            names,
            "volumes predating LDM-#1267 labelling were left behind.",
        )

    def test_a_volume_matched_twice_is_removed_once(self):
        """Label and name filters overlap; a double rm would error needlessly."""
        self.manager.run_command = self._runner(
            {
                "label=com.liferay.ldm.project=dup": "dup-data",
                "name=^dup-": "dup-data",
            }
        )
        with patch("ldm_core.runtime.orchestration.UI"):
            _sweep()(self.manager, ["docker"], "dup", None)

        rms = [c for c in self.calls if "rm" in c]
        self.assertEqual(1, len(rms), f"removed more than once: {rms}")

    def test_a_volume_still_in_use_is_reported_not_swallowed(self):
        """An invisible cleanup failure is the defect, not the leftover volume."""
        self.manager.run_command = self._runner(
            {"name=^busy-": "busy-data"}, rm_result=None
        )
        with patch("ldm_core.runtime.orchestration.UI") as ui:
            _sweep()(self.manager, ["docker"], "busy", None)
        self.assertTrue(
            ui.warning.called,
            "a volume that could not be removed was not reported at all.",
        )

    def test_nothing_found_is_silent(self):
        """A project with no volumes must not emit noise on every teardown."""
        self.manager.run_command = self._runner({})
        with patch("ldm_core.runtime.orchestration.UI") as ui:
            _sweep()(self.manager, ["docker"], "empty", None)
        self.assertFalse(ui.warning.called)
        self.assertFalse(ui.success.called)


class TestCmdDownSweepsVolumes(unittest.TestCase):
    """The wiring, not the helper.

    The unit tests above import `_sweep_project_volumes` directly, so against
    the unfixed code they fail with ImportError -- which proves the function is
    new, not that the behaviour was missing. This drives `cmd_down` itself and
    asserts on what Docker was asked to do, so it fails on *behaviour*: against
    the unfixed code no `volume rm` is issued at all.
    """

    def _mock_manager(self, tmp, recorded):
        from typing import Any, cast

        from ldm_core.handlers.base import BaseHandler
        from ldm_core.runtime.orchestration import OrchestrationService

        class M(BaseHandler):
            def __init__(self):
                self.args = MagicMock()
                self.verbose = False
                self.non_interactive = True
                self.dry_run = False
                self.target = None
                self.manager = cast(Any, self)
                self.orchestration = OrchestrationService(self)

            def run_command(  # type: ignore[override]
                self, cmd, **kw
            ):
                recorded.append(list(cmd))
                if "volume" in cmd and "ls" in cmd:
                    flt = cmd[cmd.index("--filter") + 1]
                    return "sweepme-data" if flt.startswith("name=^") else ""
                return ""

            def read_meta(self, *a, **k):
                return {"project_name": "sweepme"}

            def detect_project_path(self, *a, **k):
                return tmp

            def validate_project_dns(self, *a, **k):
                return (None, [], [])

            def get_compose_base(self, *a, **k):
                return ["docker", "compose"]

        return M()

    def test_delete_removes_volumes_when_the_compose_file_is_missing(self):
        """The stranding case: no compose file, so `down -v` cannot run."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # Deliberately NO docker-compose.yml -- this is the case that
            # orphaned volumes permanently and silently.
            self.assertFalse((tmp / "docker-compose.yml").exists())

            recorded: list[list[str]] = []
            mgr = self._mock_manager(tmp, recorded)
            with patch("ldm_core.runtime.orchestration.UI"):
                try:
                    mgr.orchestration.cmd_down(project_id=str(tmp), delete=True)
                except Exception:
                    # Teardown does a great deal besides the sweep; this test is
                    # only about whether Docker was asked to remove the volume.
                    pass

            volume_rms = [c for c in recorded if "volume" in c and "rm" in c]
            self.assertTrue(
                volume_rms,
                "cmd_down(delete=True) issued no 'docker volume rm' at all, so "
                "the project's volumes were orphaned (LDM-#1414). Calls seen: "
                f"{[' '.join(c) for c in recorded][:12]}",
            )


if __name__ == "__main__":
    unittest.main()
