"""Boot timings are recorded with the Docker allocation that explains them (LDM-#2172).

A 9m57s Windows boot and a 2m37s Mac boot were compared and the difference
put down to search mode. The Windows host had 7.6 GB for Docker -- the flat
3 GB heap tier -- and nothing recorded that beside the timing (LDM-#2164).

The figures recorded must be the ones the heap was sized from, so a report
cannot show one number while the JVM was sized from another.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerService
from ldm_core.runtime.readiness import (
    allocation_summary,
    format_startup_phases,
    write_startup_status,
)
from ldm_core.tests.test_orchestration import MockRuntime

GIB = 1024**3


def _composer(docker_gb, host_gb, cpus=8):
    manager = MagicMock()
    manager.target = None
    manager.run_command.return_value = json.dumps(
        {"NCPU": cpus, "MemTotal": int(docker_gb * GIB)}
    )
    composer = ComposerService(manager)
    composer.get_physical_host_memory_bytes = MagicMock(  # type: ignore[method-assign]
        return_value=int(host_gb * GIB)
    )
    return composer


class TheRecordedFiguresAreTheSizingFigures(unittest.TestCase):
    def test_the_allocation_is_what_docker_reports(self):
        summary = allocation_summary(_composer(7.6, 16).docker_allocation())
        self.assertEqual(
            summary,
            {
                "cpus": 8,
                "memory_gb": 7.6,
                "host_memory_gb": 16.0,
                "sizing_memory_gb": 7.6,
            },
        )

    def test_a_smaller_host_is_what_sizing_uses(self):
        summary = allocation_summary(_composer(7.6, 6).docker_allocation())
        assert summary is not None
        self.assertEqual(summary["sizing_memory_gb"], 6.0)

    def test_the_heap_is_sized_from_this_very_function(self):
        # Change only what docker_allocation answers: if the heap follows, the
        # heap and the record cannot come from two different readings.
        composer = _composer(7.6, 16)
        self.assertEqual(composer._adaptive_tuning()["heap_max_mb"], 3072)
        with patch.object(
            composer,
            "docker_allocation",
            return_value={"sizing_memory_bytes": 16 * GIB},
        ):
            self.assertEqual(composer._adaptive_tuning()["heap_max_mb"], 8192)


ALLOCATION = {
    "docker_cpus": 8,
    "docker_memory_bytes": int(7.6 * GIB),
    "host_memory_bytes": 16 * GIB,
    "sizing_memory_bytes": int(7.6 * GIB),
}


class TheBootRecordCarriesIt(unittest.TestCase):
    def test_the_status_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "startup-status.json"
            write_startup_status(status, "ready", {}, 90, allocation=ALLOCATION)
            recorded = json.loads(status.read_text())["docker_allocation"]
        self.assertEqual(recorded["sizing_memory_gb"], 7.6)
        self.assertEqual(recorded["cpus"], 8)

    def test_the_benchmark_breakdown(self):
        lines = format_startup_phases(
            {"Tomcat Server Ready": 60.0}, 90, allocation=ALLOCATION
        )
        self.assertIn(
            "Docker allocation: 8 CPUs, 7.6 GB (heap sized from 7.6 GB)", lines
        )

    def test_a_boot_writes_it_end_to_end(self):
        # Through the real wait: the record on disk carries the allocation the
        # composer answers.
        manager = MockRuntime()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(manager, "detect_project_path", return_value=root),
                patch.object(
                    manager.composer, "docker_allocation", return_value=ALLOCATION
                ),
                patch.object(
                    BaseHandler,
                    "run_command",
                    side_effect=[
                        # LDM-#2346: StartedAt, read before the first log poll.
                        "2026-11-11T10:20:30.123456789Z",
                        "org.apache.catalina.startup.Catalina.start Server startup in 1 ms",
                        "healthy",
                    ],
                ),
                patch("ldm_core.ui.UI.success"),
            ):
                manager.handler.readiness._wait_for_ready(
                    {"project_name": "shop", "container_name": "shop"},
                    "localhost",
                    timeout=10,
                )
            status = json.loads(
                (root / ".liferay-docker" / "startup-status.json").read_text()
            )
        self.assertEqual(status["docker_allocation"]["sizing_memory_gb"], 7.6)


if __name__ == "__main__":
    unittest.main()
