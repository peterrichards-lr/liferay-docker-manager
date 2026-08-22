import subprocess
import unittest
from pathlib import Path

# LDM-#1271: these tests invoke the real `ldm run` in a temp directory, which
# creates real Docker volumes named after the project -- and the project name is
# the temp directory's basename. `shutil.rmtree` removes the directory, but
# Docker volumes are independent of it and survive, so each full suite run
# leaked 6 volumes (2 tests x 3 volumes). On one machine that had accumulated to
# 650 volumes holding 21.69 GB.
#
# Note this happens even with `LDM_IGNORE_DOCKER=true` and `--no-up`: LDM
# pre-creates its named volumes rather than leaving it to `compose up`, so
# avoiding container startup is not sufficient to avoid volume creation.
_LDM_VOLUME_SUFFIXES = ("-data", "-state", "-db-db-data")


def _remove_ldm_volumes_for(tmp_dir):
    """Removes the containers AND volumes `ldm run` created for a temp-dir project.

    Containers must go first: `ldm run` leaves them in `Created` state, and a
    volume still referenced by one cannot be removed --

        Error response from daemon: remove tmp1fcnk1wu-data: volume is in use

    `compose down -v` handles both together and is what LDM itself uses, so it
    is tried first; the explicit removals below are a fallback for the case
    where the compose file was never written.
    """
    from ldm_core.utils import sanitize_id

    subprocess.run(
        ["docker", "compose", "down", "-v", "--remove-orphans"],
        cwd=str(tmp_dir),
        capture_output=True,
        check=False,
    )

    project = sanitize_id(Path(tmp_dir).name)
    for container in (project, f"{project}-db"):
        subprocess.run(
            ["docker", "rm", "-f", container], capture_output=True, check=False
        )
    for suffix in _LDM_VOLUME_SUFFIXES:
        subprocess.run(
            ["docker", "volume", "rm", f"{project}{suffix}"],
            capture_output=True,
            check=False,
        )


class TestE2EInteractive(unittest.TestCase):
    def test_interactive_fallback_with_piped_input(self):
        """
        End-to-End test to ensure that piped input correctly navigates
        LDM's interactive prompts (like project selection).
        """
        import shutil
        import sys
        import tempfile

        # Use the current python interpreter to run the main script
        ldm_executable = [
            sys.executable,
            str(Path(__file__).parent.parent.parent / "liferay_docker.py"),
        ]

        import os

        # Create a clean environment without CI markers to force interactivity
        env = os.environ.copy()
        env.pop("CI", None)
        env.pop("GITHUB_ACTIONS", None)
        env.pop("GITLAB_CI", None)
        env["LDM_IGNORE_DOCKER"] = "true"

        # 1. 'n' (select new project)
        # We stop here to let it hit EOF at the "Enter project name" prompt.
        # This prevents it from proceeding to verify_runtime_environment which triggers Docker.
        test_input = "n\n"

        tmp_dir = tempfile.mkdtemp()
        try:
            process = subprocess.run(
                [*ldm_executable, "run"],
                input=test_input,
                capture_output=True,
                text=True,
                cwd=str(tmp_dir),
                env=env,
                check=False,
            )

            # Verify the prompt for project name was actually reached
            output = process.stdout + process.stderr
            self.assertIn("Enter a new project name to initialize", output)
        finally:
            _remove_ldm_volumes_for(tmp_dir)  # LDM-#1271
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Because we sent 'q' to abort at the next prompt (Release type),
        # the process should exit with code 130 or 1 (depending on how the abort is handled)
        self.assertTrue(
            process.returncode in [0, 1, 130],
            f"Unexpected return code: {process.returncode}",
        )

    def test_ldm_start_fails_fast_on_missing_project(self):
        import sys
        import tempfile

        ldm_executable = [
            sys.executable,
            str(Path(__file__).parent.parent.parent / "liferay_docker.py"),
        ]

        tmp_dir = tempfile.mkdtemp()
        import os
        import shutil

        env = os.environ.copy()
        env["LDM_IGNORE_DOCKER"] = "true"

        try:
            process = subprocess.run(
                [*ldm_executable, "start", "non-existent-project-xyz"],
                capture_output=True,
                text=True,
                cwd=str(tmp_dir),
                env=env,
                check=False,
            )
            output = process.stdout + process.stderr
            self.assertIn(
                "Project not found or not initialized. Please use 'ldm run' to initialize and configure a new project.",
                output,
            )
        finally:
            _remove_ldm_volumes_for(tmp_dir)  # LDM-#1271
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_ldm_run_warns_on_existing_project(self):
        import sys
        import tempfile

        ldm_executable = [
            sys.executable,
            str(Path(__file__).parent.parent.parent / "liferay_docker.py"),
        ]

        tmp_dir = tempfile.mkdtemp()
        import os
        import shutil

        env = os.environ.copy()
        env["LDM_IGNORE_DOCKER"] = "true"

        try:
            # Create a mock initialized project
            (Path(tmp_dir) / ".ldm.meta").write_text("{}")
            (Path(tmp_dir) / "files").mkdir()
            (Path(tmp_dir) / "deploy").mkdir()

            process = subprocess.run(
                [*ldm_executable, "-y", "run", "--no-up", "--info"],
                capture_output=True,
                text=True,
                cwd=str(tmp_dir),
                env=env,
                check=False,
            )
            output = process.stdout + process.stderr
            self.assertIn(
                "already exists and this command will reconfigure it.",
                output,
            )
        finally:
            _remove_ldm_volumes_for(tmp_dir)  # LDM-#1271
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_ldm_run_warns_on_existing_project_without_info_flag(self):
        """LDM-#1036: the reconfigure warning must precede the CTRL+C
        countdown by default, not just when --info is explicitly passed --
        it was previously gated behind UI.detail() (--info/--verbose only)
        while the countdown itself fired unconditionally, leaving users with
        no context for what they were being asked to cancel."""
        import sys
        import tempfile

        ldm_executable = [
            sys.executable,
            str(Path(__file__).parent.parent.parent / "liferay_docker.py"),
        ]

        tmp_dir = tempfile.mkdtemp()
        import os
        import shutil

        env = os.environ.copy()
        env["LDM_IGNORE_DOCKER"] = "true"

        try:
            (Path(tmp_dir) / ".ldm.meta").write_text("{}")
            (Path(tmp_dir) / "files").mkdir()
            (Path(tmp_dir) / "deploy").mkdir()

            process = subprocess.run(
                [*ldm_executable, "-y", "run", "--no-up"],
                capture_output=True,
                text=True,
                cwd=str(tmp_dir),
                env=env,
                check=False,
            )
            output = process.stdout + process.stderr
            self.assertIn(
                "already exists and this command will reconfigure it.",
                output,
            )
        finally:
            _remove_ldm_volumes_for(tmp_dir)  # LDM-#1271
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
