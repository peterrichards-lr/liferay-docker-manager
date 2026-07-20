import subprocess
import unittest
from pathlib import Path


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
        import shutil

        try:
            process = subprocess.run(
                [*ldm_executable, "start", "non-existent-project-xyz"],
                capture_output=True,
                text=True,
                cwd=str(tmp_dir),
                check=False,
            )
            output = process.stdout + process.stderr
            self.assertIn(
                "Project not found or not initialized. Please use 'ldm run' to initialize and configure a new project.",
                output,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_ldm_run_warns_on_existing_project(self):
        import sys
        import tempfile

        ldm_executable = [
            sys.executable,
            str(Path(__file__).parent.parent.parent / "liferay_docker.py"),
        ]

        tmp_dir = tempfile.mkdtemp()
        import shutil

        try:
            # Create a mock initialized project
            (Path(tmp_dir) / ".ldm.meta").write_text("{}")
            (Path(tmp_dir) / "files").mkdir()
            (Path(tmp_dir) / "deploy").mkdir()

            process = subprocess.run(
                [*ldm_executable, "-y", "run"],
                capture_output=True,
                text=True,
                cwd=str(tmp_dir),
                check=False,
            )
            output = process.stdout + process.stderr
            self.assertIn(
                "already exists and this command will reconfigure it.",
                output,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
