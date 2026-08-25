"""Registry reconciliation and a single project-validity definition (LDM-#1324).

`find_dxp_roots()` scans with `iterdir()` -- exactly one level deep -- so a
project is discoverable by the scan only when it is an immediate child of the
search directory. The registry exists so that location stops mattering.

Before #1324 the registry was written only when a project was *created*
through the run pipeline. A project created any other way -- by hand, cloned,
or predating registration -- was never recorded, so it was findable only while
the caller happened to be standing in the right place. On Windows this was
observed with the E2E verification project itself: its directory and `meta`
are written directly and then run, so `is_new_project` was False, nothing was
registered, and `ldm list` found it only because it sat one level down.

The two discovery paths also disagreed about what a project *is*: the scan
accepted `meta`, `.liferay-docker.meta`, `.ldm.meta` or a structural fallback,
while the registry path accepted only the first two.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.utils import find_dxp_roots, is_valid_project_dir, project_meta_file


def _make_project(root: Path, name: str, meta_name: str = "meta") -> Path:
    proj = root / name
    (proj / "files").mkdir(parents=True)
    (proj / "deploy").mkdir(parents=True)
    (proj / meta_name).write_text(
        json.dumps({"tag": "2026.q1.12-lts", "container_name": name}),
        encoding="utf-8",
    )
    return proj


class TestSingleValidityDefinition(unittest.TestCase):
    """Both discovery paths must agree on what counts as a project."""

    def test_every_supported_metadata_filename_is_recognised(self):
        """`.ldm.meta` was accepted by the scan but not by registry validation.

        A project written with it was therefore discoverable one way and
        invisible the other, depending only on how it was reached.
        """
        for meta_name in ("meta", ".liferay-docker.meta", ".ldm.meta"):
            with tempfile.TemporaryDirectory() as tmp:
                proj = _make_project(Path(tmp), "p", meta_name=meta_name)
                self.assertIsNotNone(
                    project_meta_file(proj),
                    f"{meta_name} not recognised as project metadata",
                )
                self.assertTrue(is_valid_project_dir(proj))

    def test_structural_fallback_is_rejected_in_the_home_directory(self):
        """`files/` and `deploy/` are far too generic to mean anything in ~."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "not-a-project"
            (proj / "files").mkdir(parents=True)
            (proj / "deploy").mkdir(parents=True)
            self.assertTrue(is_valid_project_dir(proj, allow_structural=True))
            self.assertFalse(is_valid_project_dir(proj, allow_structural=False))

    def test_a_plain_directory_is_not_a_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain"
            plain.mkdir()
            self.assertFalse(is_valid_project_dir(plain))
            self.assertIsNone(project_meta_file(plain))


class TestReconciliation(unittest.TestCase):
    """Discovery must be self-healing rather than depending on how a project was made."""

    def _run_discovery(self, workspace: Path, home: Path):
        with (
            patch.dict(os.environ, {"LDM_WORKSPACE": str(workspace)}),
            patch("ldm_core.utils.get_actual_home", return_value=home),
        ):
            return find_dxp_roots()

    def _registry(self, home: Path) -> dict:
        path = home / ".ldm" / "registry.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def test_a_scan_found_project_is_added_to_the_registry(self):
        """The #1324 case: created outside the registering code path."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home, workspace = tmp_path / "home", tmp_path / "ws"
            home.mkdir()
            workspace.mkdir()
            _make_project(workspace, "handmade")

            self.assertNotIn("handmade", self._registry(home))
            roots = self._run_discovery(workspace, home)

            self.assertEqual(len(roots), 1, "scan should still find the project")
            self.assertIn(
                "handmade",
                self._registry(home),
                "a project the scan can see must be recorded so it stays "
                "findable from anywhere",
            )

    def test_registration_survives_leaving_the_directory(self):
        """The point of registering: discovery stops depending on location.

        After reconciliation, a project nested too deep for the one-level scan
        is still found, because the registry now knows about it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home, workspace = tmp_path / "home", tmp_path / "ws"
            home.mkdir()
            workspace.mkdir()
            _make_project(workspace, "seen-once")

            self._run_discovery(workspace, home)

            # A different workspace entirely -- the scan cannot reach it now.
            other = tmp_path / "elsewhere"
            other.mkdir()
            roots = self._run_discovery(other, home)

            names = [r["path"].name for r in roots]
            self.assertIn(
                "seen-once",
                names,
                "the registry should keep the project discoverable from elsewhere",
            )

    def test_a_registered_directory_that_is_no_longer_a_project_is_pruned(self):
        """Previously only a *vanished* path was pruned.

        A directory that still existed but had lost its metadata was skipped in
        silence and its entry kept indefinitely.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home, workspace = tmp_path / "home", tmp_path / "ws"
            home.mkdir()
            workspace.mkdir()
            proj = _make_project(workspace, "decaying")

            self._run_discovery(workspace, home)
            self.assertIn("decaying", self._registry(home))

            # Still on disk, no longer a project.
            (proj / "meta").unlink()
            (proj / "files").rmdir()
            (proj / "deploy").rmdir()

            self._run_discovery(workspace, home)
            self.assertNotIn(
                "decaying",
                self._registry(home),
                "a registered directory that is no longer a project must be pruned",
            )

    def test_reconciliation_failure_never_breaks_discovery(self):
        """`list` is a read command; failing to write the registry must not fail it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home, workspace = tmp_path / "home", tmp_path / "ws"
            home.mkdir()
            workspace.mkdir()
            _make_project(workspace, "unwritable")

            with patch(
                "ldm_core.utils.safe_write_text", side_effect=OSError("read-only")
            ):
                roots = self._run_discovery(workspace, home)

            self.assertEqual(
                len(roots), 1, "discovery must still return the project it found"
            )


if __name__ == "__main__":
    unittest.main()
