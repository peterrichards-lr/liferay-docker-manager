import subprocess
import unittest
from pathlib import Path


class TestE2EInteractive(unittest.TestCase):
    def test_interactive_fallback_with_piped_input(self):
        """
        End-to-End test to ensure that piped input correctly navigates
        LDM's interactive prompts (like project selection).
        """
        import sys
        import tempfile

        # Use the current python interpreter to run the main script
        ldm_executable = [
            sys.executable,
            str(Path(__file__).parent.parent.parent / "liferay_docker.py"),
        ]

        # We run 'ldm run' with no arguments in a temporary directory
        # to force the interactive 'Select Project' menu and 'new project' fallback.
        # The input simulates:
        # 1. 'n' (select new project)
        # 2. 'piped-test-project' (project name)
        # 3. Enter (accept default host)
        # 4. Enter (accept default tag)
        # 5. 'q' (quit)

        test_input = "n\npiped-test-project\n\n\nq\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            process = subprocess.run(
                [*ldm_executable, "run"],
                input=test_input,
                capture_output=True,
                text=True,
                cwd=str(tmp_dir),
            )

        # Verify the prompt for project name was actually reached and handled
        self.assertIn("Enter a new project name to initialize", process.stdout)

        # Because we sent 'q' to abort at the next prompt (Release type),
        # the process should exit with code 130 or 1 (depending on how the abort is handled)
        self.assertTrue(
            process.returncode in [0, 1, 130],
            f"Unexpected return code: {process.returncode}",
        )


if __name__ == "__main__":
    unittest.main()
