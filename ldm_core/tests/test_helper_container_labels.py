"""LDM's throwaway containers must be identifiable (LDM-#1506).

LDM runs short-lived alpine containers to chown volumes, probe writability and
measure disk. They were started anonymously, so a stray was indistinguishable
from anything the user ran -- one was found in `created` state after two days,
under the Docker-generated name `affectionate_hofstadter`:

    cmd   = ["sh","-c","chown -R 501:20 /workspace; chmod -R 777 /workspace; "]
    binds = ["/Volumes/SanDisk/.ldm/infra/search/data:/workspace"]

`--rm` removes a container when it EXITS. One that never starts never exits, so
`--rm` cannot fire. Without a label nothing could reclaim it afterwards either:
`ldm prune` sweeps orphaned volumes by ownership label (#1393/#1395) but had no
equivalent for containers.
"""

import re
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock

from ldm_core.diagnostics.prune import _stray_helper_containers
from ldm_core.utils import helper_container_flags

_ROOT = Path(__file__).resolve().parent.parent


class TestHelperContainerFlags(unittest.TestCase):
    def test_still_removes_itself_on_exit(self):
        self.assertIn("--rm", helper_container_flags("chown"))

    def test_carries_the_managed_label(self):
        """Same vocabulary as archetype services, not a new one."""
        self.assertIn("com.liferay.ldm.managed=true", helper_container_flags("chown"))

    def test_purpose_is_recorded_so_a_stray_explains_itself(self):
        self.assertIn(
            "com.liferay.ldm.helper=disk-probe",
            helper_container_flags("disk-probe"),
        )


class TestEveryHelperSiteIsLabelled(unittest.TestCase):
    """No `docker run ... alpine` may go out unlabelled.

    Six call sites across four modules. A seventh added later without the
    flags would reintroduce exactly this bug, so this counts rather than
    spot-checks.
    """

    SITES: ClassVar[list[str]] = [
        "utils.py",
        "snapshot/volumes.py",
        "diagnostics/doctor.py",
        "handlers/base.py",
    ]

    def test_no_bare_rm_alpine_runs_remain(self):
        offenders = []
        for rel in self.SITES:
            src = (_ROOT / rel).read_text(encoding="utf-8")
            # A "run" whose flags are a literal --rm rather than the helper.
            for match in re.finditer(r'"run",\s*\n\s*"--rm"', src):
                line = src[: match.start()].count("\n") + 1
                offenders.append(f"{rel}:{line}")
        self.assertEqual(
            [],
            offenders,
            "unlabelled helper container run(s) -- use helper_container_flags() "
            f"so a stray can be reclaimed (LDM-#1506): {offenders}",
        )

    def test_all_six_sites_use_the_helper(self):
        total = sum(
            (_ROOT / rel).read_text(encoding="utf-8").count("helper_container_flags(")
            for rel in self.SITES
        )
        # utils.py holds the definition plus one use.
        self.assertGreaterEqual(total, 7, "expected six call sites plus the definition")


class TestStrayHelperSweep(unittest.TestCase):
    PS_OUTPUT = (
        "abc123\taffectionate_hofstadter\tcreated\n"
        "def456\tsleepy_wozniak\texited\n"
        "ghi789\tbusy_chown\trunning\n"
    )

    def _handler(self, output):
        h = MagicMock()
        h.manager.run_command.return_value = output
        return h

    def test_finds_created_and_exited_helpers(self):
        strays = _stray_helper_containers(self._handler(self.PS_OUTPUT), ["docker"])
        self.assertEqual(
            ["affectionate_hofstadter", "sleepy_wozniak"], [s[1] for s in strays]
        )

    def test_never_touches_a_running_helper(self):
        """A chown midway through a large volume must survive maintenance."""
        strays = _stray_helper_containers(self._handler(self.PS_OUTPUT), ["docker"])
        self.assertNotIn("busy_chown", [s[1] for s in strays])

    def test_filters_on_the_ownership_label(self):
        h = self._handler(self.PS_OUTPUT)
        _stray_helper_containers(h, ["docker"])
        argv = h.manager.run_command.call_args[0][0]
        self.assertIn("label=com.liferay.ldm.helper", argv)

    def test_docker_unreachable_is_not_an_error(self):
        self.assertEqual([], _stray_helper_containers(self._handler(None), ["docker"]))


if __name__ == "__main__":
    unittest.main()
