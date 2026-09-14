"""The port pre-flight must ask the machine the container will run on (LDM-#1727).

`ldm --target aws-2 run <project>` refused on a conflict detected on the
operator's laptop:

    Port conflict detected: Port 8080 is already in use on the host ...
    It is currently held by java (PID 18238).

`java (PID 18238)` was a local process. Nothing on the node was listening on
8080. The refusal, the named holder and the advice were all about the wrong
machine.

It was wrong in both directions, which is what makes it worth a test rather
than a message tweak: the check could not see the node, so it also *cleared*
ports genuinely taken there, and the conflict resurfaced later as a container
that would not start.

`DockerService.published_host_ports`, `is_running` and
`container_publishing_port` had all taken a `target_name` since remote nodes
shipped -- the call sites simply never passed one.

**The socket probe stays local-only on purpose.** Probing the node's published
port from here answers a different question: measured against a real node, a
port held by a running container *timed out* rather than connecting, because
the security group drops inbound traffic on it. A probe that reports "free" for
a port that is in use is worse than no probe, so on a remote target Docker's
allocator is the sole authority -- which `published_host_ports`' own docstring
already argues it should be.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetContext, TargetNode
from ldm_core.docker_service import DockerService
from ldm_core.pipelines.run import ComposerStage, RunPipelineContext

COMPOSE = """\
services:
  liferay:
    container_name: proj
    ports:
      - "8080:8080"
"""


def _target(name, remote):
    return TargetContext(
        target=TargetNode(name=name, host="10.0.0.9" if remote else "localhost"),
        is_remote=remote,
        docker_prefix=["docker"],
        compose_prefix=["docker", "compose"],
    )


class PreflightTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "docker-compose.yml").write_text(COMPOSE)

        self.context = RunPipelineContext(MagicMock())
        self.context.manager.non_interactive = True
        self.context.set("project_id", "proj")
        self.context.set("project_meta", {"container_name": "proj"})
        self.context.set("paths", {"root": self.root, "configs": self.root})
        self.context.set("no_up", False)
        self.context.set("dry_run", False)
        self.context.set("infra_ports", {})

    def _run(self, *, target, node_ports=frozenset(), local_free=True):
        """Drive the stage; return the UI.die call, or None if it never fired.

        `node_ports` is what Docker's allocator reports on the target;
        `local_free` is what a socket probe on THIS machine would say.
        """
        self.context.set("target_context", target)
        self.context.manager.check_port.return_value = local_free

        died: dict = {}

        def capture_die(msg, details=None, tip=None, exit_code=1):
            died.update(msg=msg, tip=tip, exit_code=exit_code)
            raise SystemExit(exit_code)

        with (
            patch.object(DockerService, "is_running", return_value=False) as running,
            patch.object(
                DockerService, "published_host_ports", return_value=set(node_ports)
            ) as allocator,
            patch.object(
                DockerService, "container_publishing_port", return_value=None
            ) as publisher,
            patch("ldm_core.utils.native_port_listener", return_value="java (PID 1)"),
            patch("ldm_core.ui.UI.die", side_effect=capture_die),
        ):
            try:
                ComposerStage().execute(self.context)
            except SystemExit:
                pass
            except Exception:
                # The stage does much more than the pre-flight; a later step
                # failing on a MagicMock manager is irrelevant here, and the
                # pre-flight has already run by then.
                pass

        self.calls = {
            "is_running": running.call_args,
            "allocator": allocator.call_args,
            "publisher": publisher.call_args,
        }
        return died or None


class RemoteTarget(PreflightTestCase):
    def test_a_locally_held_port_does_not_refuse_a_remote_run(self):
        """The exact report: local java on 8080, node free, run refused."""
        died = self._run(target=_target("aws-2", True), local_free=False)

        self.assertIsNone(
            died,
            "refused a remote run for a port held on the operator's laptop",
        )

    def test_the_allocator_is_asked_about_the_node(self):
        self._run(target=_target("aws-2", True))

        self.assertEqual(
            self.calls["allocator"].args[0],
            "aws-2",
            "asked the local daemon which ports are taken",
        )

    def test_the_container_check_is_asked_about_the_node(self):
        self._run(target=_target("aws-2", True))

        self.assertEqual(self.calls["is_running"].args[1], "aws-2")

    def test_a_port_held_on_the_node_still_refuses(self):
        """The other direction: the check must not become a no-op."""
        died = self._run(target=_target("aws-2", True), node_ports={8080})

        self.assertIsNotNone(died, "a genuine conflict on the node was cleared")
        self.assertEqual(died["exit_code"], 4)

    def test_the_message_names_the_node_not_the_host(self):
        died = self._run(target=_target("aws-2", True), node_ports={8080})

        self.assertIn("node 'aws-2'", died["msg"])
        self.assertNotIn("on the host", died["msg"])

    def test_the_advice_names_the_node(self):
        """'Free up port 8080' is useless if it does not say where."""
        died = self._run(target=_target("aws-2", True), node_ports={8080})

        self.assertIn("aws-2", died["tip"] or "")

    def test_no_local_pid_is_offered_as_the_holder(self):
        """Naming a local PID for a remote conflict is the original bug."""
        died = self._run(target=_target("aws-2", True), node_ports={8080})

        self.assertNotIn("PID 1", died["msg"])

    def test_the_holder_lookup_is_asked_about_the_node(self):
        self._run(target=_target("aws-2", True), node_ports={8080})

        self.assertEqual(self.calls["publisher"].args[1], "aws-2")


class LocalTargetIsUnchanged(PreflightTestCase):
    """The fix must not weaken the check it was narrowing."""

    def test_a_locally_held_port_still_refuses(self):
        died = self._run(target=_target("local", False), local_free=False)

        self.assertIsNotNone(died, "the local socket probe stopped refusing")
        self.assertEqual(died["exit_code"], 4)

    def test_the_message_still_says_the_host(self):
        died = self._run(target=_target("local", False), local_free=False)

        self.assertIn("the host", died["msg"])

    def test_a_port_held_by_a_local_container_still_refuses(self):
        died = self._run(target=_target("local", False), node_ports={8080})

        self.assertIsNotNone(died)

    def test_a_free_local_port_is_still_allowed(self):
        died = self._run(target=_target("local", False))

        self.assertIsNone(died)


class NoTargetContext(PreflightTestCase):
    """Resolution happens upstream, but the stage must not crash without it."""

    def test_absent_target_context_behaves_locally(self):
        died = self._run(target=None, local_free=False)

        self.assertIsNotNone(died, "a missing target context skipped the check")
        self.assertIn("the host", died["msg"])


if __name__ == "__main__":
    unittest.main()
