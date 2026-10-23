"""A node's container-written trees are reclaimed BEFORE they are synced or deleted (LDM-#2266).

Reported from a reachable AWS node (AICA nightly 39383065775): a
`routes/default` written by the Liferay container as uid 1000 failed the
rsync, and rsync abandoned `--delete` for the whole transfer. A script the
project had deleted survived on the node, was mounted at `/mnt/liferay/scripts`,
ran, and killed the container at bring-up.

The reclaim that repairs this existed (LDM-#2144) but ran in the run pipeline
AFTER the sync, so it fixed the next run rather than this one -- and `ldm start`
and target migration, which sync too, never reclaimed at all. Teardown had the
same blind spot: a plain `rm -rf` as the SSH user, which then reported a node
that had answered as "may be unreachable".

These drive the real functions through a recorder at the `run_command` seam, so
the reclaim's own docker argv is observed rather than a mock being asserted on.
The probe reports uid 4242, which this host cannot have, so a reclaim that
chowns to the LOCAL uid -- the trap that made the old one work only where both
sides happened to be uid 1001 -- fails here.
"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

_NODE_UID = "4242"
_HOME = "/home/ldm"


def _target():
    return SimpleNamespace(name="node-b", host="10.0.0.9", user="ldm", key_path=None)


class _Node:
    """Scripted node. Records every command in order and answers by shape."""

    def __init__(self, probe, rm_ok=True):
        self.probe = probe
        self.rm_ok = rm_ok
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **_kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        last = cmd[-1]
        if cmd[0] == "ssh" and "id -u" in last:
            return self.probe
        if cmd[0] == "ssh" and "rm -rf" in last:
            return "" if self.rm_ok else None
        if cmd[0] == "docker":
            return "777\n"  # the reclaim's own `stat`
        return ""

    def index(self, predicate):
        for i, c in enumerate(self.calls):
            if predicate(c):
                return i
        return -1

    def reclaims(self):
        return [c for c in self.calls if c[0] == "docker" and "run" in c]


def _is_rsync(c):
    return c[0] == "rsync"


def _mount_of(cmd):
    return cmd[cmd.index("-v") + 1].split(":")[0]


class _Harness:
    """`exercises_docker_helper` on each class lifts conftest's stub of
    reclaim_volume_permissions; the docker call is still intercepted, one level
    down, at run_command."""

    def __init__(self, node):
        self.node = node

    def __enter__(self):
        self._patches: list[Any] = [
            patch("ldm_core.config.get_active_target", return_value=_target()),
            patch("ldm_core.config.is_local_host", return_value=False),
            patch("ldm_core.config.run_command", self.node),
            patch("ldm_core.utils.run_command", self.node),
            patch("shutil.which", return_value="/usr/bin/rsync"),
            patch(
                "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
                return_value=["docker", "--context", "node-b"],
            ),
            patch.dict("os.environ", {"LDM_DRY_RUN": ""}),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()


_PROBE_STEADY = (
    f"{_HOME} {_NODE_UID} {_NODE_UID}\n:present\nroutes\nlogs\nscripts\nmeta\n"
)


@pytest.mark.exercises_docker_helper
class TheSyncReclaimsFirst(unittest.TestCase):
    def _sync(self, probe):
        from ldm_core.config import sync_project_to_target

        node = _Node(probe)
        with _Harness(node):
            sync_project_to_target(Path("/tmp/aica-e2e"), target_name="node-b")
        return node

    def test_container_written_trees_are_reclaimed_before_the_rsync(self):
        node = self._sync(_PROBE_STEADY)
        rsync_at = node.index(_is_rsync)
        self.assertGreaterEqual(rsync_at, 0, f"no rsync issued: {node.calls}")

        routes_at = node.index(
            lambda c: c[0] == "docker" and _mount_of(c).endswith("/routes")
        )
        self.assertGreaterEqual(
            routes_at, 0, f"routes was never reclaimed: {node.reclaims()}"
        )
        self.assertLess(
            routes_at,
            rsync_at,
            "routes was reclaimed AFTER the rsync, which is the LDM-#2266 "
            "defect: the transfer has already failed and dropped --delete.",
        )

    def test_the_reclaim_targets_the_absolute_node_path(self):
        """The node's Docker daemon expands no `~`, so the mount source must be
        built from the probed home, not from the shell-facing `~` form."""
        node = self._sync(_PROBE_STEADY)
        mounts = {_mount_of(c) for c in node.reclaims()}
        self.assertIn(f"{_HOME}/.liferay-docker/projects/aica-e2e/routes", mounts)
        self.assertFalse(any("~" in m for m in mounts), mounts)

    def test_ownership_goes_to_the_node_user_not_this_host(self):
        node = self._sync(_PROBE_STEADY)
        scripts = [c[-1] for c in node.reclaims()]
        self.assertTrue(scripts, "nothing was reclaimed")
        for script in scripts:
            self.assertIn(
                f"chown -R {_NODE_UID}:{_NODE_UID}",
                script,
                "chowned to this host's uid. On the node that is a stranger, so "
                "rsync's chgrp is refused and the sync fails as before.",
            )

    def test_only_present_container_trees_are_touched(self):
        """`scripts` and `meta` are the project's own -- never reclaimed. An
        absent tree is not created by the helper's bind mount."""
        node = self._sync(_PROBE_STEADY)
        leaves = sorted(_mount_of(c).rsplit("/", 1)[1] for c in node.reclaims())
        self.assertEqual(leaves, ["logs", "routes"])

    def test_an_unanswered_probe_still_syncs(self):
        """No answer is reported by the sync itself (LDM-#2139). The reclaim
        must not turn a node it cannot ask into one it never syncs."""
        node = self._sync(None)
        self.assertEqual(node.reclaims(), [])
        self.assertGreaterEqual(node.index(_is_rsync), 0)


@pytest.mark.exercises_docker_helper
class TeardownReclaimsFirst(unittest.TestCase):
    def _remove(self, probe, rm_ok=True):
        from ldm_core.config import remove_project_from_target

        node = _Node(probe, rm_ok=rm_ok)
        warnings: list[str] = []
        with (
            _Harness(node),
            patch("ldm_core.ui.UI.warning", side_effect=warnings.append),
            patch("ldm_core.ui.UI.success"),
        ):
            result = remove_project_from_target(Path("/tmp/aica-e2e"))
        return node, result, warnings

    def test_the_whole_project_is_reclaimed_before_rm(self):
        node, result, _ = self._remove(_PROBE_STEADY)
        self.assertTrue(result)
        rm_at = node.index(lambda c: c[0] == "ssh" and "rm -rf" in c[-1])
        root_at = node.index(
            lambda c: (
                c[0] == "docker"
                and _mount_of(c) == f"{_HOME}/.liferay-docker/projects/aica-e2e"
            )
        )
        self.assertGreaterEqual(root_at, 0, f"project not reclaimed: {node.calls}")
        self.assertLess(root_at, rm_at)

    def test_a_node_that_answered_is_not_called_unreachable(self):
        _, result, warnings = self._remove(_PROBE_STEADY, rm_ok=False)
        self.assertFalse(result)
        failure = next(w for w in warnings if "Could not remove" in w)
        self.assertNotIn("unreachable", failure)
        self.assertIn("answered", failure)

    def test_a_node_that_did_not_answer_still_says_so(self):
        _, result, warnings = self._remove(None, rm_ok=False)
        self.assertFalse(result)
        failure = next(w for w in warnings if "Could not remove" in w)
        self.assertIn("unreachable", failure)


if __name__ == "__main__":
    unittest.main()
