"""LDM-#2036: a Windows reserved port must be diagnosed, not dumped.

Reported on `v2.26.0-pre.15`. Docker refused to bind 8443 and LDM surfaced the
daemon's raw text:

    Error response from daemon: ports are not available: exposing port TCP
    0.0.0.0:8443 -> 127.0.0.1:0: listen tcp 0.0.0.0:8443: bind: An attempt was
    made to access a socket in a way forbidden by its access permissions.

That wording sends the reader to the wrong place. It reads as a privilege
problem and is not: the port sits inside a range Windows has reserved, usually
via WinNAT for Hyper-V or WSL. Nothing is listening on it, so every "what is
holding this port" check comes back empty -- which is the worst kind of
diagnostic dead end.

`reserved_port_tip` is a sibling of `disk_space_tip` (LDM-#1906): same call
site, same `-> str | None` contract, returns None for everything it does not
recognise so no other failure changes.

The strings below are verbatim from the reported run. What cannot be tested
off Windows is that Docker emits that wording -- the report is the evidence
for that, and this file is the evidence that LDM acts on it correctly.
"""

import unittest

from ldm_core.utils import disk_space_tip, reserved_port_tip

REAL_STDERR = (
    "Error response from daemon: ports are not available: exposing port TCP "
    "0.0.0.0:8443 -> 127.0.0.1:0: listen tcp 0.0.0.0:8443: bind: An attempt "
    "was made to access a socket in a way forbidden by its access permissions."
)

DOCKER_CMD = ["docker", "compose", "-f", "infra-compose.yml", "up", "-d"]


class TestReservedPortTip(unittest.TestCase):
    def test_the_remedy_does_not_cost_the_reader_their_wsl(self):
        """LDM-#2041: the first version of this tip broke the maintainer's WSL.

        It named WSL as a consumer of WinNAT and then recommended restarting
        WinNAT, with no warning. Docker Desktop on the WSL2 backend rides the
        same stack, so following it to fix a Docker problem could take Docker
        down on the way -- advice that can leave the reader worse off than the
        failure it addresses.
        """
        tip = reserved_port_tip(DOCKER_CMD, REAL_STDERR)
        assert tip is not None
        lowered = tip.lower()
        self.assertIn(
            "wsl --shutdown",
            lowered,
            "the WinNAT restart is offered without the recovery step",
        )
        self.assertLess(
            lowered.index("docker desktop"),
            lowered.index("net stop winnat"),
            "the safe remedy must be offered before the disruptive one",
        )
        disrupts = lowered.index("disrupts")
        winnat = lowered.index("net stop winnat")
        self.assertLess(
            abs(disrupts - winnat),
            260,
            "the warning must sit with the command it warns about, not "
            "somewhere else in the paragraph",
        )

    def test_the_reported_failure_is_recognised(self):
        tip = reserved_port_tip(DOCKER_CMD, REAL_STDERR)
        assert tip is not None, "the reported stderr was not recognised"
        self.assertIn("8443", tip, "the tip must name the port that failed")
        self.assertIn("winnat", tip.lower(), "the tip must name the remedy")
        self.assertIn("excludedportrange", tip, "and how to confirm it first")

    def test_a_port_already_held_by_a_container_is_not_claimed(self):
        """The distinction that makes this worth having.

        `ports are not available` is also emitted when another container holds
        the port. That is a different problem with a different remedy, already
        diagnosed by LDM-#1350, and claiming it here would send the user to
        WinNAT for a conflict WinNAT has nothing to do with.
        """
        held = (
            "Error response from daemon: ports are not available: exposing "
            "port TCP 0.0.0.0:8080 -> 127.0.0.1:0: listen tcp 0.0.0.0:8080: "
            "bind: address already in use"
        )
        self.assertIsNone(reserved_port_tip(DOCKER_CMD, held))

    def test_unrelated_failures_are_untouched(self):
        for stderr in ("", "no space left on device", "permission denied"):
            with self.subTest(stderr=stderr):
                self.assertIsNone(reserved_port_tip(DOCKER_CMD, stderr))

    def test_a_non_docker_command_is_not_claimed(self):
        """Gated like `disk_space_tip`: the remedy is Docker's to need."""
        self.assertIsNone(reserved_port_tip(["gzip", "-9", "big.tar"], REAL_STDERR))

    def test_it_does_not_steal_the_disk_space_case(self):
        """Both share a call site, so neither may answer for the other."""
        enospc = "write /var/lib/docker: no space left on device"
        self.assertIsNone(reserved_port_tip(DOCKER_CMD, enospc))
        self.assertIsNotNone(disk_space_tip(DOCKER_CMD, enospc))
        self.assertIsNone(disk_space_tip(DOCKER_CMD, REAL_STDERR))

    def test_the_port_is_read_from_the_message_not_guessed(self):
        other = REAL_STDERR.replace("8443", "9443")
        tip = reserved_port_tip(DOCKER_CMD, other)
        assert tip is not None
        self.assertIn("9443", tip)
        self.assertNotIn("8443", tip)

    def test_an_unparseable_port_still_produces_advice(self):
        """A message shape we have not seen must not lose the remedy."""
        odd = "ports are not available: forbidden by its access permissions"
        tip = reserved_port_tip(DOCKER_CMD, odd)
        assert tip is not None
        self.assertIn("That port", tip)


if __name__ == "__main__":
    unittest.main()
