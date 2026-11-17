"""The Windows self-upgrade no longer reads as a frozen terminal (LDM-#2212).

Reported on PowerShell: the terminal seemed to freeze, never getting past
"[1/10] Access denied...". The updater printed nothing until a retry failed
and nothing on success, ran in the caller's console, and force-killed every
ldm.exe on the machine on every attempt. Nothing tested it at all.
"""

import subprocess
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from ldm_core.diagnostics.upgrade import (
    _launch_windows_updater,
    _updater_environment,
    _windows_updater_script,
)


class TheUpdaterScriptSaysWhatItDoes(unittest.TestCase):
    def setUp(self):
        self.bat = _windows_updater_script(
            Path(r"C:\Temp\ldm.new"), Path(r"C:\Users\me\bin\ldm.exe")
        )

    def test_it_announces_itself_before_the_first_attempt(self):
        self.assertLess(self.bat.index("echo Replacing"), self.bat.index("move /y"))

    def test_success_is_reported_before_ldm_starts(self):
        ok = self.bat.index("echo [OK] LDM upgraded")
        self.assertLess(ok, self.bat.index("doctor --skip-project"))

    def _success_block(self):
        start = self.bat.index("echo [OK] LDM upgraded")
        return self.bat[start : self.bat.index("goto :CLEANUP", start)]

    def test_doctor_runs_in_this_window_not_a_new_one(self):
        """LDM-#2327: `start` gave doctor a console of its own, which closed
        when doctor exited, so its output was never seen."""
        block = self._success_block()
        self.assertIn('"C:\\Users\\me\\bin\\ldm.exe" doctor --skip-project', block)
        self.assertNotIn("start ", block)

    def test_the_window_waits_after_success(self):
        """LDM-#2327: success fell straight into :CLEANUP, which deletes the
        script and ends it, closing the window as [OK] printed."""
        block = self._success_block()
        self.assertLess(block.index("doctor --skip-project"), block.index("timeout /t"))
        # Closes by itself: a `pause` would hold an unattended window forever.
        self.assertNotIn("pause", block)

    def test_it_never_kills_other_ldm_processes(self):
        self.assertNotIn("taskkill", self.bat.lower())


class TheUpdaterGetsItsOwnConsole(unittest.TestCase):
    def test_it_is_launched_in_a_new_console_not_the_callers(self):
        with patch("subprocess.Popen") as popen:
            _launch_windows_updater(Path(r"C:\Temp\ldm.update.bat"), has_access=True)
        args, kwargs = popen.call_args
        self.assertEqual(args[0][:2], ["cmd.exe", "/c"])
        self.assertEqual(
            kwargs.get("creationflags"),
            getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
        )
        self.assertFalse(kwargs.get("shell", False))

    def test_without_write_access_it_elevates(self):
        with patch("subprocess.Popen") as popen:
            _launch_windows_updater(Path(r"C:\Temp\ldm.update.bat"), has_access=False)
        self.assertEqual(popen.call_args.args[0][0], "powershell.exe")
        self.assertIn("-Verb RunAs", popen.call_args.args[0][-1])


class TheNewBinaryDoesNotReuseTheOldOnesFolder(unittest.TestCase):
    """LDM-#2411: the post-upgrade doctor failed with PYI-10120, looking for
    python313.dll in the exited ldm.exe's deleted _MEI folder."""

    INHERITED: ClassVar[dict[str, str]] = {
        "_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI000044142",
        "_PYI_ARCHIVE_FILE": r"C:\Users\me\bin\ldm.exe",
        "_MEIPASS2": r"C:\Temp\_MEI000044142",
        "PATH": r"C:\Windows",
    }

    def test_the_inherited_pyinstaller_state_is_dropped_and_reset_requested(self):
        with patch.dict("os.environ", self.INHERITED, clear=True):
            env = _updater_environment()
        self.assertEqual(env.get("PYINSTALLER_RESET_ENVIRONMENT"), "1")
        self.assertFalse([k for k in env if k.startswith("_PYI_")])
        self.assertNotIn("_MEIPASS2", env)
        self.assertEqual(env["PATH"], r"C:\Windows")  # everything else kept

    def _launched_env(self, has_access):
        with (
            patch.dict("os.environ", self.INHERITED, clear=True),
            patch("subprocess.Popen") as popen,
        ):
            _launch_windows_updater(Path(r"C:\Temp\ldm.update.bat"), has_access)
        return popen.call_args.kwargs.get("env") or {}

    def test_both_launch_routes_hand_on_the_clean_environment(self):
        for has_access in (True, False):
            with self.subTest(has_access=has_access):
                env = self._launched_env(has_access)
                self.assertEqual(env.get("PYINSTALLER_RESET_ENVIRONMENT"), "1")
                self.assertNotIn("_PYI_APPLICATION_HOME_DIR", env)

    def test_the_batch_resets_it_before_running_the_new_binary(self):
        """The elevated route goes through Start-Process, which need not pass
        the environment on, so the batch sets it itself."""
        bat = _windows_updater_script(
            Path(r"C:\Temp\ldm.new"), Path(r"C:\Users\me\bin\ldm.exe")
        )
        reset = bat.index('set "PYINSTALLER_RESET_ENVIRONMENT=1"')
        self.assertLess(reset, bat.index("doctor --skip-project"))
        self.assertIn('set "_MEIPASS2="', bat)
        self.assertIn("set _PYI_", bat)


if __name__ == "__main__":
    unittest.main()
