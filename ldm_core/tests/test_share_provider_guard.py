"""The native lfr-tunnel provider cannot reach a project on a remote node (LDM-#1338).

`ldm share` has two providers that differ in where the tunnel runs:

- `lfr-tunnel-docker` creates the tunnel container ON the target node via
  `docker --context`, joined to the project's compose network.
- `lfr-tunnel` runs a binary on the invoking machine and forwards to
  `meta["host_name"]`, defaulting to localhost. `meta["target"]` is never
  consulted.

So for a project running on a remote compute node, the native provider tunnels
to the developer's own localhost. Nothing prevented choosing it, and the
failure was silent and misattributed: the tunnel starts, a public URL is
printed, `share status` reports it running -- and requests either fail for no
stated reason or reach whatever unrelated container happens to be on that port,
served under the project's public URL.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetNode
from ldm_core.handlers.share import ShareService


class _Base(unittest.TestCase):
    def _service(self):
        manager = MagicMock()
        manager.args = MagicMock()
        return ShareService(manager)

    def _guard(self, provider, target, host_name=None, project_id="proj"):
        meta = {"target": target.name}
        if host_name is not None:
            meta["host_name"] = host_name
        service = self._service()
        with patch("ldm_core.config.get_active_target", return_value=target):
            service._assert_provider_can_reach_target(provider, meta, project_id)


REMOTE = TargetNode(name="aws-1", host="10.0.1.7")
LOCAL = TargetNode(name="local", host="localhost")
LOOPBACK = TargetNode(name="loopback-node", host="127.0.0.2")


class TestNativeProviderIsRefusedForARemoteNode(_Base):
    def test_native_provider_with_a_remote_target_is_refused(self):
        """The #1338 case."""
        with self.assertRaises(SystemExit) as ctx:
            self._guard("lfr-tunnel", REMOTE)
        self.assertEqual(ctx.exception.code, 3)

    def test_the_refusal_names_the_node_and_the_alternative(self):
        """A bare refusal would leave the reader guessing.

        The whole defect is that nothing pointed at the provider, so the
        message must name both the node and the provider that works.
        """
        with (
            patch("ldm_core.ui.UI.error") as err,
            patch("ldm_core.ui.UI.info") as info,
            self.assertRaises(SystemExit),
        ):
            self._guard("lfr-tunnel", REMOTE, project_id="myproject")

        printed = " ".join(
            str(c.args[0])
            for c in list(err.call_args_list) + list(info.call_args_list)
            if c.args
        )
        self.assertIn("myproject", printed)
        self.assertIn("aws-1", printed)
        self.assertIn("10.0.1.7", printed)
        self.assertIn("lfr-tunnel-docker", printed)


class TestLegitimateCasesAreNotRefused(_Base):
    """The guard must not block anything that actually works."""

    def test_containerised_provider_is_always_allowed(self):
        """It runs the tunnel on the node, so a remote target is its purpose."""
        self._guard("lfr-tunnel-docker", REMOTE)

    def test_native_provider_with_a_local_target_is_allowed(self):
        self._guard("lfr-tunnel", LOCAL)

    def test_loopback_target_counts_as_local(self):
        """127.0.0.0/8 is local, matching DockerService.get_docker_cmd_prefix.

        A second, disagreeing definition of "is this remote" is what #1324 had
        to unpick for project discovery; this uses the same predicate.
        """
        self._guard("lfr-tunnel", LOOPBACK)

    def test_explicit_reachable_host_name_is_allowed(self):
        """The escape hatch.

        With host_name pointed somewhere reachable the native tunnel forwards
        there rather than to localhost, so it can legitimately work.
        """
        self._guard("lfr-tunnel", REMOTE, host_name="10.0.1.7")

    def test_default_localhost_host_name_is_still_refused(self):
        """Only an explicit non-localhost value is an escape hatch."""
        with self.assertRaises(SystemExit):
            self._guard("lfr-tunnel", REMOTE, host_name="localhost")


class TestGuardRunsBeforeAnyTunnelWork(unittest.TestCase):
    def test_the_check_precedes_the_binary_download_and_version_check(self):
        """Ordering is the point.

        Checked after `_ensure_binary()` the user would download a binary and
        watch a version check before being told the provider is wrong -- and
        after the tunnel start, they would have a working-looking tunnel.
        """
        import inspect

        source = inspect.getsource(ShareService.cmd_start)
        guard_at = source.index("_assert_provider_can_reach_target")
        self.assertLess(
            guard_at,
            source.index("_ensure_binary"),
            "the guard must run before the binary is fetched",
        )
        self.assertLess(
            guard_at,
            source.index("resolve_public_tunnel_url"),
            "the guard must run before any public URL is produced",
        )


if __name__ == "__main__":
    unittest.main()
