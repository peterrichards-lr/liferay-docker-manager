"""The Windows self-upgrade no longer reads as a frozen terminal (LDM-#2212).

Reported on PowerShell: the terminal seemed to freeze, never getting past
"[1/10] Access denied...". The updater printed nothing until a retry failed
and nothing on success, ran in the caller's console, and force-killed every
ldm.exe on the machine on every attempt. Nothing tested it at all.
"""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.diagnostics.upgrade import (
    _launch_windows_updater,
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
        self.assertLess(ok, self.bat.index('start ""'))

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


if __name__ == "__main__":
    unittest.main()
