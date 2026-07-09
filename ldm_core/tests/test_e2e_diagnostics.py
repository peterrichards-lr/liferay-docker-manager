import subprocess
import unittest
from pathlib import Path


class TestE2EDiagnostics(unittest.TestCase):
    def test_interactive_prune_piped_input(self):
        """
        End-to-End test to ensure that piped input correctly navigates
        LDM's interactive prompts in the 'prune' command.
        """
        import sys

        # Use the current python interpreter to run the main script
        ldm_executable = [
            sys.executable,
            str(Path(__file__).parent.parent.parent / "liferay_docker.py"),
        ]

        # Provide multiple 'n' responses for the interactive prompts in ldm prune
        # 1. Orphaned containers
        # 2. Orphaned search snapshots
        # 3. Temp files
        # 4. Orphaned SSL certs
        # 5. Seeds cache
        # 6. Samples cache
        # 7. Hosts
        test_input = "n\nn\nn\nn\nn\nn\nn\n"
        import os

        env = os.environ.copy()
        env["LDM_IGNORE_DOCKER"] = "true"
        process = subprocess.run(
            [*ldm_executable, "prune", "--seeds", "--samples", "--clean-hosts"],
            input=test_input,
            capture_output=True,
            text=True,
            cwd="/tmp",
            env=env,
            check=False,
        )

        # Verify the command executes successfully without hanging
        self.assertEqual(
            process.returncode, 0, f"Prune command failed. Stderr: {process.stderr}"
        )

        # Verify it actually reached some of the interactive prompts
        self.assertIn("LDM Global Maintenance", process.stdout)


if __name__ == "__main__":
    unittest.main()
