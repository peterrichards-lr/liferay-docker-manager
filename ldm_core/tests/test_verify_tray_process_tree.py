"""The Windows tray check stops the whole `ldm tray` process tree (LDM-#2389).

`ldm.exe` is a PyInstaller one-file build: the PID `Start-Process` returns is
the bootloader, and the tray runs in a child `ldm.exe`. The check stopped only
that PID, so the child kept the console and `exit` hung after every Windows
verification run. Confirmed on the maintainer's machine: after the script's own
`Stop-Process`, `"ldm.exe" tray` was still listed with the killed PID as parent.

This runs the script's real tray block under pwsh, with `Start-Process`,
`Stop-Process` and `taskkill.exe` shadowed by recording stubs, so nothing is
started or killed and the call that reaches Windows is what is asserted.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_e2e_refactor.ps1"
START = 'Write-Host ">> Verifying System Tray (GUI)..."'
END = 'Write-Host ">> Verifying ldm doctor Dependency Integrity..."'

HARNESS = r"""
$script:calls = [System.Collections.Generic.List[string]]::new()
function Start-Process { [pscustomobject]@{ Id = 4242; HasExited = $false } }
function Start-Sleep { }
function Write-Verdict { }
function Stop-Process { $script:calls.Add("Stop-Process " + ($args -join ' ')) }
function taskkill.exe { $script:calls.Add("taskkill " + ($args -join ' ')) }
$LDM_CMD = 'ldm.exe'
"""


def _tray_block():
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index(START)
    return text[start : text.index(END, start)]


def _pwsh():
    return shutil.which("pwsh") or shutil.which("powershell")


class TheTrayIsStoppedWithItsChildren(unittest.TestCase):
    @unittest.skipUnless(_pwsh(), "PowerShell is not installed")
    def test_the_whole_tree_is_stopped(self):
        script = HARNESS + _tray_block() + '\n$script:calls -join "`n"\n'
        res = subprocess.run(
            [_pwsh(), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        calls = res.stdout.split()
        joined = " ".join(calls)
        self.assertIn("taskkill /T /F /PID 4242", joined, res.stdout)
        # Stopping the bootloader PID on its own is the defect.
        self.assertNotIn("Stop-Process", joined, res.stdout)


if __name__ == "__main__":
    unittest.main()
