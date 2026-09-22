"""The conftest guard must refuse a spawn of an EDR-watched binary (LDM-#1898).

`conftest.block_real_docker` has intercepted `subprocess.run`/`Popen` on every
test since LDM-#1409, but it only ever asked whether the argv was Docker. A
test added for LDM-#1883 spawned a `chmod +x` stub named `lfr-tunnel` on every
suite run for a day, through that same interception point, unremarked.

These probe the guard itself. **No probe here can execute anything**: each one
names a path that does not exist, so a guard that failed to fire raises
`FileNotFoundError` rather than launching a process. That is deliberate -- a
test of an endpoint-protection guard must not depend on the guard working in
order to be safe to run.
"""

import subprocess
import unittest
from pathlib import Path

import pytest
from _pytest.outcomes import Failed


class TestTheGuardRefusesProtectedNames(unittest.TestCase):
    def setUp(self):
        self.absent = Path("/nonexistent-by-construction-ldm1898")

    def test_a_protected_binary_is_refused(self):
        with pytest.raises(Failed) as ctx:
            subprocess.run([str(self.absent / "lfr-tunnel"), "-version"], check=False)
        self.assertIn("protected binary", str(ctx.value))

    def test_it_matches_wherever_the_binary_sits(self):
        """The signature is the name, not the directory."""
        for parent in ("/tmp/whatever", "/var/folders/x/T/tmpabc/.ldm/bin"):
            with self.subTest(parent=parent):
                with pytest.raises(Failed):
                    subprocess.run([f"{parent}/lfr-tunnel", "-version"], check=False)

    def test_popen_is_guarded_too(self):
        """share.py uses run(); other call sites use Popen. Both or neither."""
        with pytest.raises(Failed):
            subprocess.Popen([str(self.absent / "lfr-tunnel"), "-version"])

    def test_the_refusal_names_the_test_and_the_command(self):
        with pytest.raises(Failed) as ctx:
            subprocess.run([str(self.absent / "ldm"), "version"], check=False)
        said = str(ctx.value)
        self.assertIn("test_the_refusal_names_the_test_and_the_command", said)
        self.assertIn("/nonexistent-by-construction-ldm1898/ldm version", said)
        self.assertIn("spawns_protected_binary", said)  # names the escape hatch
        self.assertIn("subprocess.CompletedProcess", said)  # shows the fix


class TestTheGuardDoesNotOverMatch(unittest.TestCase):
    """A guard that refuses everything would be disabled within the week."""

    def test_an_unwatched_name_passes_through(self):
        """It must reach the real subprocess -- FileNotFoundError, not Failed."""
        with self.assertRaises(FileNotFoundError):
            subprocess.run(
                ["/nonexistent-by-construction-ldm1898/harmless"], check=False
            )

    def test_a_path_merely_containing_the_name_is_not_refused(self):
        """This checkout is `liferay-docker-manager`; a substring test over the
        whole command would match half the suite. LDM-#1409 measured 4 false
        positives in 335 hits before switching to basename matching.
        """
        with self.assertRaises(FileNotFoundError):
            subprocess.run(
                ["/nonexistent-by-construction-ldm1898/lfr-tunnel-notes.sh"],
                check=False,
            )

    def test_a_shell_carrying_the_name_as_an_argument_is_not_refused(self):
        """argv[0] is what launches. `echo lfr-tunnel` launches echo."""
        res = subprocess.run(
            ["echo", "lfr-tunnel"], capture_output=True, text=True, check=False
        )
        self.assertEqual("lfr-tunnel", res.stdout.strip())


if __name__ == "__main__":
    unittest.main()
