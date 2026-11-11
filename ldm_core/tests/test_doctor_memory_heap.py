"""`ldm doctor` says what the Docker memory allocation buys (LDM-#2190).

`Docker Memory ✅ 7.6 GB` gave no hint that 7.6 GB is the flat 3 GB heap tier,
which took a real Windows host 9m31s to boot against 2m42s at 10.7 GB.

The heap the doctor reports is compared with what the real `_adaptive_tuning`
sizes for the same `docker info` -- not with a restated table, which would pass
against exactly the drift this is meant to prevent.
"""

import json
import re
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.diagnostics.doctor import _check_docker_resources
from ldm_core.handlers.composer import ComposerService

GIB = 1024**3


def _gb_after(label, value):
    match = re.search(rf"{label} ([\d.]+) GB", value)
    assert match, f"no '{label}' in {value!r}"
    return float(match[1])


def _docker_info(mem_gb, cpus=8):
    return json.dumps({"NCPU": cpus, "MemTotal": int(mem_gb * GIB)})


def _memory_line(mem_gb):
    with patch("ldm_core.diagnostics.doctor.UI"):
        results = _check_docker_resources(MagicMock(), _docker_info(mem_gb))
    return next(r for r in results if r[0] == "Docker Memory")


def _heap_ldm_would_use(mem_gb):
    manager = MagicMock()
    manager.target = None
    manager.run_command.return_value = _docker_info(mem_gb)
    composer = ComposerService(manager)
    # A host far larger than the allocation, so Docker's figure is the one used.
    with patch.object(
        composer, "get_physical_host_memory_bytes", return_value=64 * GIB
    ):
        return composer._adaptive_tuning()["heap_max_mb"] / 1024


class TheDoctorReportsTheHeapLdmActuallyUses(unittest.TestCase):
    ALLOCATIONS = (3.8, 7.6, 8.0, 9.7, 10.7, 15.6, 31.2)

    def test_for_allocations_either_side_of_each_tier(self):
        for mem_gb in self.ALLOCATIONS:
            with self.subTest(mem_gb=mem_gb):
                value = _memory_line(mem_gb)[1]
                reported = _gb_after("Liferay heap", value)
                self.assertEqual(reported, _heap_ldm_would_use(mem_gb))

    def test_below_a_boundary_it_says_what_the_next_tier_gives(self):
        value = _memory_line(7.6)[1]
        self.assertIn("above 8 GB gives", value)
        offered = _gb_after("above 8 GB gives", value)
        self.assertEqual(offered, _heap_ldm_would_use(8.5))
        self.assertGreater(offered, _heap_ldm_would_use(7.6))

    def test_above_the_last_boundary_there_is_no_next_tier_to_offer(self):
        self.assertNotIn("above", _memory_line(10.7)[1])


class ThePassFailContractIsUnchanged(unittest.TestCase):
    def test_wsl2s_default_still_passes(self):
        # Must not become a warning by the back door.
        self.assertIs(_memory_line(7.6)[2], True)

    def test_the_floors_still_apply(self):
        self.assertEqual(_memory_line(6.0)[2], "warn")
        self.assertIs(_memory_line(3.0)[2], False)

    def test_the_value_still_leads_with_the_reported_figure(self):
        self.assertTrue(_memory_line(7.6)[1].startswith("7.6 GB"))


if __name__ == "__main__":
    unittest.main()
