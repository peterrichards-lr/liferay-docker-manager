"""`ldm doctor` must check the host disk, not only Docker's (LDM-#1435).

`_check_absolute_disk_space` asks Docker deliberately: on Docker Desktop, Colima
and OrbStack the engine's storage lives in a VM with its own, far smaller disk,
so a host-side check passes on exactly the machines most likely to fail. That
reasoning is correct and unchanged.

It is also half the picture. The VM's disk is typically a sparse image on the
host filesystem, so what Docker reports is a promise the host may not be able to
keep. Measured on a developer machine at one moment:

    docker run --rm alpine df -P -h /   ->  77.9 GB free
    df -h /System/Volumes/Data          ->   2.8 GB free, 100% capacity

The pre-flight passed at 77.9 GB and the run died with ENOSPC (#1430, #1429).
Neither view is sufficient alone.
"""

import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import ldm_core.diagnostics.doctor as doctor_mod

DoctorRunner = doctor_mod.DoctorRunner


def _runner():
    svc = DoctorRunner(MagicMock())
    svc.results = []
    svc.hints = []
    # Collect hints rather than emitting them, so a warning can be asserted on.
    svc.add_hint = svc.hints.append  # type: ignore[method-assign,assignment]
    return svc


class TestHostDiskCheck(unittest.TestCase):
    def test_a_full_host_is_reported_even_when_docker_looks_fine(self):
        """The #1430 scenario: Docker reports plenty, the host has nothing."""
        svc = _runner()
        with patch.object(
            shutil, "disk_usage", return_value=shutil._ntuple_diskusage(0, 0, 1024)
        ):
            svc._check_host_disk_space(is_remote=False)

        self.assertEqual(1, len(svc.results))
        label, _value, status = svc.results[0]
        self.assertEqual("Disk Space (Host)", label)
        self.assertEqual("warn", status)
        self.assertTrue(svc.hints, "a warning with no hint is not actionable")
        self.assertIn("sparse image", svc.hints[0])

    def test_ample_host_space_passes(self):
        svc = _runner()
        with patch.object(
            shutil,
            "disk_usage",
            return_value=shutil._ntuple_diskusage(0, 0, 500 * 1024**3),
        ):
            svc._check_host_disk_space(is_remote=False)
        self.assertTrue(svc.results[0][2] is True)

    def test_it_is_skipped_for_a_remote_target(self):
        """This host's free space says nothing about another machine's engine."""
        svc = _runner()
        svc._check_host_disk_space(is_remote=True)
        self.assertEqual([], svc.results)

    def test_it_measures_the_engine_storage_path_not_the_home_directory(self):
        """Storage is often relocated, and then home is the wrong volume.

        Measured on a developer machine where `~/.colima` is a symlink to an
        external drive: the home volume showed 154GB free while the volume
        actually backing Docker showed 480GB. Checking home would have reported
        a disk Docker does not use.
        """
        svc = _runner()
        seen = []

        def _record(path):
            seen.append(str(path))
            return shutil._ntuple_diskusage(0, 0, 500 * 1024**3)

        with (
            patch.object(shutil, "disk_usage", side_effect=_record),
            patch.object(Path, "exists", lambda self: str(self).endswith(".colima")),
        ):
            svc._check_host_disk_space(is_remote=False)

        self.assertTrue(seen, "no path was measured")
        self.assertTrue(
            seen[0].endswith(".colima"),
            f"measured {seen[0]!r}; expected the engine's storage path",
        )

    def test_a_failure_to_measure_is_not_fatal(self):
        """Diagnostics must not break the command they are diagnosing."""
        svc = _runner()
        with patch.object(shutil, "disk_usage", side_effect=OSError("nope")):
            svc._check_host_disk_space(is_remote=False)
        self.assertEqual([], svc.results)


if __name__ == "__main__":
    unittest.main()
