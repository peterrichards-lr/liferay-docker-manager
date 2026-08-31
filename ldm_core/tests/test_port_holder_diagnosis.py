"""A port conflict must name what is HOLDING the port (LDM-#1479).

`ldm run` reported which service needed the port and advised freeing it, but
never said what occupied it -- leaving the user to work that out per-OS, which
is the slow part of resolving a conflict.

The capability already existed on the wrong side of the fence:
`scripts/verify_e2e_refactor.sh` grew `diagnose_port_holder()` in LDM-#1428 so
a failing verification run would name the holder. The test harness could
explain the conflict and the shipped product could not.
"""

import unittest
from unittest.mock import patch

from ldm_core.docker_service import DockerService
from ldm_core.utils import native_port_listener

PS_OUTPUT = (
    "liferay-search-global\t0.0.0.0:9200->9200/tcp, [::]:9200->9200/tcp\n"
    "other-proj-kibana\t0.0.0.0:5601->80/tcp, [::]:5601->80/tcp\n"
    "no-ports-container\t\n"
)


class TestContainerPublishingPort(unittest.TestCase):
    def test_names_the_container_holding_the_port(self):
        with patch("ldm_core.docker_service.run_command", return_value=PS_OUTPUT):
            self.assertEqual(
                DockerService.container_publishing_port(5601), "other-proj-kibana"
            )

    def test_returns_none_when_nothing_publishes_it(self):
        with patch("ldm_core.docker_service.run_command", return_value=PS_OUTPUT):
            self.assertIsNone(DockerService.container_publishing_port(8080))

    def test_container_side_port_is_not_mistaken_for_the_host_port(self):
        """5601->80 means host 5601. Matching '80' would name the wrong holder."""
        with patch("ldm_core.docker_service.run_command", return_value=PS_OUTPUT):
            self.assertIsNone(DockerService.container_publishing_port(80))

    def test_docker_unreachable_is_not_an_error(self):
        """Diagnosis failing must never stop the conflict being reported."""
        with patch("ldm_core.docker_service.run_command", return_value=None):
            self.assertIsNone(DockerService.container_publishing_port(5601))


class TestNativePortListener(unittest.TestCase):
    LSOF = (
        "COMMAND   PID          USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
        "nginx    4321 peterrichards   6u  IPv4 0x1234      0t0  TCP *:5601 (LISTEN)\n"
    )
    LSOF_FORWARDER = (
        "COMMAND     PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
        "docker-proxy 99 root    4u  IPv4 0x9999      0t0  TCP *:5601 (LISTEN)\n"
    )

    def test_names_the_process_and_pid(self):
        with (
            patch("shutil.which", return_value="/usr/bin/lsof"),
            patch("ldm_core.utils.run_command", return_value=self.LSOF),
        ):
            self.assertEqual(native_port_listener(5601), "nginx (PID 4321)")

    def test_header_row_is_not_reported_as_the_holder(self):
        with (
            patch("shutil.which", return_value="/usr/bin/lsof"),
            patch("ldm_core.utils.run_command", return_value=self.LSOF),
        ):
            self.assertNotIn("COMMAND", native_port_listener(5601) or "")

    def test_forwarder_is_labelled_rather_than_blamed(self):
        """docker-proxy holds the port BECAUSE a container published it."""
        with (
            patch("shutil.which", return_value="/usr/bin/lsof"),
            patch("ldm_core.utils.run_command", return_value=self.LSOF_FORWARDER),
        ):
            result = native_port_listener(5601) or ""
        self.assertIn("forwarder", result)

    def test_no_tool_available_returns_none(self):
        with patch("shutil.which", return_value=None):
            self.assertIsNone(native_port_listener(5601))

    def test_probe_failure_is_swallowed(self):
        with (
            patch("shutil.which", return_value="/usr/bin/lsof"),
            patch("ldm_core.utils.run_command", side_effect=OSError("boom")),
        ):
            self.assertIsNone(native_port_listener(5601))


if __name__ == "__main__":
    unittest.main()
