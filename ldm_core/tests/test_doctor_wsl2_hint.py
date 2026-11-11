"""`ldm doctor` names `.wslconfig` when Docker runs on WSL2 (LDM-#2176).

The hint said "Allocate more RAM in your Docker provider settings". On Docker
Desktop's WSL2 backend that slider is greyed out; the allocation comes from
`%UserProfile%\\.wslconfig`. LDM could not tell, because it only knew it was on
WSL2 when running inside a WSL distro, never from Windows PowerShell.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.diagnostics.doctor import _check_docker_resources

GIB = 1024**3

# Measured from Windows PowerShell, Docker Desktop on WSL2 (LDM-#2176).
WSL2 = {
    "KernelVersion": "6.18.58.1-microsoft-standard-WSL2",
    "OperatingSystem": "Docker Desktop (containerized)",
}
# Docker Desktop on macOS: the slider is the right control there.
LINUXKIT = {"KernelVersion": "6.10.14-linuxkit", "OperatingSystem": "Docker Desktop"}


def _hint(mem_gb, engine):
    info = {"NCPU": 8, "MemTotal": int(mem_gb * GIB), **engine}
    with patch("ldm_core.diagnostics.doctor.UI") as ui:
        _check_docker_resources(MagicMock(), json.dumps(info))
    return "\n".join(str(c.args[0]) for c in ui.raw.call_args_list)


class TheMemoryHintNamesTheControlThatWorks(unittest.TestCase):
    def test_on_wsl2_it_names_wslconfig_and_the_restart(self):
        hint = _hint(6.0, WSL2)
        self.assertIn(r"%UserProfile%\.wslconfig", hint)
        self.assertIn("memory=", hint)
        self.assertIn("[wsl2]", hint)
        self.assertIn("wsl --shutdown", hint)
        self.assertIn("#windows-with-the-wsl2-backend", hint)
        self.assertNotIn("Docker provider settings", hint)

    def test_elsewhere_it_still_points_at_the_provider_settings(self):
        hint = _hint(6.0, LINUXKIT)
        self.assertIn("Allocate more RAM in your Docker provider settings", hint)
        self.assertNotIn(".wslconfig", hint)

    def test_without_a_kernel_version_it_is_not_wsl2(self):
        hint = _hint(6.0, {})
        self.assertIn("Docker provider settings", hint)

    def test_enough_memory_gives_no_hint_on_either_backend(self):
        # LDM-#2190 kept the tick as a floor: 7.6 GB passes, so no hint.
        self.assertEqual(_hint(7.6, WSL2), "")
        self.assertEqual(_hint(7.6, LINUXKIT), "")


if __name__ == "__main__":
    unittest.main()
