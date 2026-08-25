"""`ldm share` must route to the project's compute node (LDM-#1337, tier 1).

The containerised provider (`lfr-tunnel-docker`) is the only one that can reach
a project on a remote node: it creates the tunnel container ON that node via
`docker --context <name>`, joined to the project's compose network.

That depends on one thing -- the docker prefix being built from
`project_meta["target"]` rather than assuming local. If it regressed, the
tunnel would be created on the wrong daemon and the failure would present as a
networking or gateway problem rather than a routing one, which is exactly how
#1338 wasted debugging time on the native provider.

This is deliberately a unit test, not an E2E assertion. The claim is about a
*decision*, so it needs no tunnel, no token and no remote machine, and it
therefore runs on every PR rather than only when someone remembers to trigger a
verification. Proving that traffic actually reaches the node is a different
claim and needs a real node -- that is tier 3.
"""

import unittest
from unittest.mock import patch

from ldm_core.config import TargetNode
from ldm_core.docker_service import DockerService

REMOTE = TargetNode(name="aws-1", host="51.20.52.201")
LOCAL = TargetNode(name="local", host="localhost")
LOOPBACK = TargetNode(name="loopback-node", host="127.0.0.2")


class TestPrefixIsBuiltFromTheTarget(unittest.TestCase):
    """`DockerService.get_docker_cmd_prefix` is the single routing decision."""

    def _prefix(self, target, name=None):
        # Patched where it is USED, not where it is defined. docker_service.py
        # imports get_active_target at module level (line 1), so the name is
        # already bound there and patching ldm_core.config would not reach it.
        # The first version of this test did exactly that and silently ran the
        # real resolver against the live registry.
        with patch("ldm_core.docker_service.get_active_target", return_value=target):
            return DockerService.get_docker_cmd_prefix(name or target.name)

    def test_a_remote_target_yields_an_explicit_context(self):
        """The assertion that matters: the node's context, by name."""
        self.assertEqual(
            self._prefix(REMOTE),
            ["docker", "--context", "aws-1"],
            "a project on a remote node must be reached through its context, "
            "or the tunnel container is created on the wrong daemon",
        )

    def test_a_local_target_yields_a_plain_docker_command(self):
        self.assertEqual(self._prefix(LOCAL), ["docker"])

    def test_a_loopback_target_counts_as_local(self):
        """All of 127.0.0.0/8 is local, deliberately.

        This is why a loopback target cannot stand in for a remote one in an
        E2E test -- it never produces a context. Pinned here so the limitation
        is visible rather than rediscovered.
        """
        self.assertEqual(self._prefix(LOOPBACK), ["docker"])

    def test_compose_prefix_carries_the_context_too(self):
        """`compose` inherits the same routing; a divergence would be silent."""
        with patch("ldm_core.docker_service.get_active_target", return_value=REMOTE):
            self.assertEqual(
                DockerService.get_compose_cmd_prefix("aws-1"),
                ["docker", "--context", "aws-1", "compose"],
            )


class TestShareUsesTheProjectsTarget(unittest.TestCase):
    """The containerised path must read the target from the project's meta."""

    def test_cmd_start_builds_its_prefix_from_project_meta_target(self):
        import inspect

        from ldm_core.handlers.share import ShareService

        source = inspect.getsource(ShareService.cmd_start)

        self.assertIn(
            'project_meta.get("target")',
            source,
            "the containerised provider must take the compute target from the "
            "project's meta, not assume local",
        )
        target_at = source.index('project_meta.get("target")')
        prefix_at = source.index("get_docker_cmd_prefix", target_at)
        self.assertLess(
            target_at,
            prefix_at,
            "the target must be resolved before the docker prefix is built",
        )

    def test_the_prefix_is_actually_used_for_the_tunnel_container(self):
        """Resolving the prefix and then not using it would be the same bug."""
        import inspect

        from ldm_core.handlers.share import ShareService

        source = inspect.getsource(ShareService.cmd_start)
        after = source[source.index("get_docker_cmd_prefix") :]
        self.assertIn(
            "*docker_prefix",
            after,
            "the resolved prefix must be spread into the docker invocation",
        )


if __name__ == "__main__":
    unittest.main()
