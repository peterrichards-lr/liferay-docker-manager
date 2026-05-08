import subprocess
import unittest
from pathlib import Path


class TestE2EInteractive(unittest.TestCase):
    def test_interactive_fallback_with_piped_input(self):
        """
        End-to-End test to ensure that piped input correctly navigates
        LDM's interactive prompts (like project selection).
        """
        # Resolve the LDM executable path
        ldm_executable = Path(__file__).parent.parent.parent / "ldm"

        # We run 'ldm run' with no arguments in a temporary directory
        # to force the interactive 'Select Project' menu and 'new project' fallback.
        # The input simulates:
        # 1. 'n' (select new project)
        # 2. 'piped-test-project' (project name)
        # 3. 'q' (quit / abort immediately after name to avoid spinning up docker)

        test_input = "n\npiped-test-project\nq\n"

        process = subprocess.run(
            [str(ldm_executable), "run"],
            input=test_input,
            capture_output=True,
            text=True,
            cwd="/tmp",
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
