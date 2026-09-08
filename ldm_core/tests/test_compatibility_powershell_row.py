"""Windows PowerShell 5.1 and 7 must be separate matrix rows (LDM-#1563).

The slug was arch + OS + Docker provider only, so both editions canonicalised
to `verify-windows-pc-windows-11-docker-desktop-<status>.txt` and the later
timestamp silently replaced the earlier. The matrix showed one Windows row with
nothing recording which shell produced it, and running both was a trap rather
than a benefit.

The reports already carry the edition; only the sync needed to learn it.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _ROOT / "scripts" / "sync_compatibility.py"


def _sync():
    spec = importlib.util.spec_from_file_location("_sync_under_test", _SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_HEADER = """=== LDM BINARY VERIFICATION REPORT ===
Timestamp: 02/09/2026 15:08:14
Platform:  {platform}
{psline}Binary:    C:\\ldm\\ldm.exe
Version:      ldm 2.20.0-pre.6
Script Ver: 2.20.0-pre.6
Docker Desktop 4.35.0
[SUCCESS] ALL E2E VERIFICATIONS PASSED!
"""


def _slug_for(tmp, name, psline, platform="Microsoft Windows 10.0.22631"):
    p = Path(tmp) / name
    p.write_text(_HEADER.format(platform=platform, psline=psline), encoding="utf-8")
    return _sync().get_report_metadata(p)["internal_slug"]


class TestPowerShellEditionsGetDistinctRows(unittest.TestCase):
    def test_desktop_and_core_do_not_collide(self):
        with tempfile.TemporaryDirectory() as d:
            five = _slug_for(
                d,
                "verify-windows-pc-windows-11-docker-desktop-a-pass.txt",
                "PowerShell: 5.1.22621.6133 (Desktop)\n",
            )
            seven = _slug_for(
                d,
                "verify-windows-pc-windows-11-docker-desktop-b-pass.txt",
                "PowerShell: 7.6.5 (Core)\n",
            )
        self.assertNotEqual(
            five,
            seven,
            "both editions collapse to one row and the later run silently "
            "replaces the earlier (LDM-#1563)",
        )
        self.assertIn("powershell-5.1", five)
        self.assertIn("powershell-7", seven)

    def test_the_edition_not_the_patch_version_decides(self):
        """7.6 -> 7.7 must not spawn a new row."""
        with tempfile.TemporaryDirectory() as d:
            a = _slug_for(
                d,
                "verify-windows-pc-windows-11-docker-desktop-a-pass.txt",
                "PowerShell: 7.6.5 (Core)\n",
            )
            b = _slug_for(
                d,
                "verify-windows-pc-windows-11-docker-desktop-b-pass.txt",
                "PowerShell: 7.7.1 (Core)\n",
            )
        self.assertEqual(a, b)

    def test_a_report_without_the_line_keeps_its_old_slug(self):
        """WSL2 and every historical report must not be orphaned."""
        with tempfile.TemporaryDirectory() as d:
            slug = _slug_for(
                d,
                "verify-windows-pc-windows-11-native-wsl2-pass.txt",
                "",
                platform="Linux 5.15.0-microsoft-standard-WSL2",
            )
        self.assertNotIn("powershell", slug)
        self.assertIn("wsl2", slug)


class TestPopulatingPlatformDoesNotMoveTheRow(unittest.TestCase):
    """LDM-#1639's hard constraint: filling `Platform:` must not move the row.

    The .ps1 left the field blank on Windows PowerShell 5.1 and now falls back
    to `[System.Environment]::OSVersion.VersionString`. The published matrix
    links to each report by its canonical filename, so a slug that shifted
    would break those links -- and the constraint was argued from reading
    `sync_compatibility.py` rather than measured, which this repository treats
    as the same error as asserting from reading.

    The mechanism it pins: a Windows report's identity comes from the
    `PowerShell:` line's edition, NOT from the platform string. The platform
    string feeds only `is_windows_native` (already true from the filename) and
    `is_wsl` (which needs `linux` in the string, so `Microsoft Windows NT ...`
    leaves it false).
    """

    NAME = "verify-windows-pc-windows-11-docker-desktop-pass.txt"
    PS_51 = "PowerShell: 5.1.22621.6133 (Desktop)\n"

    def test_a_populated_platform_yields_the_same_slug_as_a_blank_one(self):
        with tempfile.TemporaryDirectory() as d:
            blank = _slug_for(d, self.NAME, self.PS_51, platform="")
            populated = _slug_for(
                d,
                self.NAME,
                self.PS_51,
                # Exactly what the fallback renders on a real 5.1 host.
                platform="Microsoft Windows NT 10.0.22621.0",
            )
        self.assertEqual(
            blank,
            populated,
            "populating Platform: changed the canonical slug, which would "
            "break every link in the published matrix (LDM-#1639)",
        )
        self.assertIn("powershell-5.1", populated)

    def test_the_populated_platform_is_not_mistaken_for_wsl(self):
        """`is_wsl` needs `linux`; a native Windows string must not trip it."""
        with tempfile.TemporaryDirectory() as d:
            slug = _slug_for(
                d,
                self.NAME,
                self.PS_51,
                platform="Microsoft Windows NT 10.0.22621.0",
            )
        self.assertNotIn("wsl", slug)


if __name__ == "__main__":
    unittest.main()
