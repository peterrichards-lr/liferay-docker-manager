"""The lfr-tunnel-docker sidecar starts only after the credentials check (LDM-#2526).

`composer._build_lfr_tunnel_service` puts the tunnel sidecar in the project's
compose file. A bare `compose up -d` (or `create` + `start`) starts every
service in that file, so `ldm run --share` with the Docker provider made the
instance public as soon as the stack came up -- and the default-credentials
check, which can only run once the portal answers, ran afterwards from
`share.cmd_start`. A refusal then stopped LDM but not the tunnel.

The bring-ups now name every service except the sidecar, and only
`share.cmd_start` (`compose up -d lfr-tunnel`) starts it, after the check.

These drive the shipped call sites -- `ExecutionStage.execute`,
`orchestration.cmd_start` and `recreate_with_patches` -- against a real compose
file on disk, and record the compose argv at `run_command`. Nothing is spawned.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from ldm_core.handlers.share_credentials import (
    SIDECAR_SERVICE,
    services_holding_back_sidecar,
)
from ldm_core.pipelines.run import ExecutionStage, RunPipelineContext


def _write_compose(root: Path, services) -> Path:
    path = root / "docker-compose.yml"
    path.write_text(
        yaml.safe_dump({"services": {name: {"image": "x"} for name in services}})
    )
    return path


SHARED = ["liferay", "proj-db", SIDECAR_SERVICE]
UNSHARED = ["liferay", "proj-db"]


class TheHelper(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)

    def test_names_every_other_service_when_the_sidecar_is_defined(self):
        path = _write_compose(self.root, SHARED)
        self.assertEqual(services_holding_back_sidecar(path), UNSHARED)

    def test_changes_nothing_without_a_sidecar(self):
        path = _write_compose(self.root, UNSHARED)
        self.assertEqual(services_holding_back_sidecar(path), [])

    def test_changes_nothing_without_a_compose_file(self):
        self.assertEqual(services_holding_back_sidecar(self.root / "absent.yml"), [])


class TheRunPipelineBringUp(unittest.TestCase):
    """`ldm run --share` / `import --share` / `quickstart --share` all boot here.

    Harness follows `test_container_hostname.TheExecutionStageChecksIt`.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name) / "proj"
        self.root.mkdir()

        self.context = RunPipelineContext(MagicMock())
        self.context.set("project_id", "proj")
        self.context.set("is_new_project", False)
        self.context.set("dry_run", False)
        self.context.set("no_up", False)
        self.context.set("paths", {"root": self.root})
        self.context.set(
            "project_meta",
            {
                "container_name": "proj",
                "share": "true",
                "share_provider": "lfr-tunnel-docker",
            },
        )
        manager = self.context.manager
        manager.non_interactive = True
        manager.target = None
        for flag in (
            "no_wait",
            "follow",
            "force_recreate",
            "rebuild",
            "quiet",
            "dry_run",
            "no_up",
        ):
            setattr(manager.args, flag, False)
        manager.dry_run = False
        manager.args.timeout = 900
        manager.get_container_status.return_value = "healthy"
        manager.runtime._wait_for_ready.return_value = True

    def _bring_up_calls(self, services, patches=None):
        _write_compose(self.root, services)
        with (
            patch("ldm_core.config.sync_project_to_target"),
            patch("ldm_core.pipelines.run.offer_shared_database_tip"),
            patch("ldm_core.pipelines.run.time.sleep"),
            patch("ldm_core.pipelines.run.platform.system", return_value="Darwin"),
            patch(
                "ldm_core.runtime.portal_patches.plan_patches",
                return_value=patches or [],
            ),
            patch("ldm_core.runtime.portal_patches.copy_patches_into"),
            patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit),
        ):
            try:
                ExecutionStage().execute(self.context)
            except SystemExit:
                pass
        argvs = [
            [str(a) for a in c.args[0]]
            for c in self.context.manager.run_command.call_args_list
            if c.args and isinstance(c.args[0], list)
        ]
        verbs = {"up", "create", "start"}
        return [a for a in argvs if verbs & set(a)]

    def test_the_bring_up_leaves_the_sidecar_out(self):
        calls = self._bring_up_calls(SHARED)
        bring_up = [a for a in calls if "--remove-orphans" in a]
        self.assertEqual(len(bring_up), 1, calls)
        self.assertEqual(
            bring_up[0][-2:],
            UNSHARED,
            "the bring-up must NAME every service except the sidecar; a bare "
            "`up -d` starts the tunnel before the credentials check",
        )
        for argv in calls:
            self.assertNotIn(SIDECAR_SERVICE, argv)

    def test_the_patched_bring_up_starts_without_the_sidecar(self):
        """With portal patches the bring-up is `create`, `docker cp`, `start`."""
        calls = self._bring_up_calls(SHARED, patches=[object()])
        create = [a for a in calls if "create" in a]
        start = [a for a in calls if "start" in a]
        self.assertEqual(len(create), 1, calls)
        self.assertEqual(len(start), 1, calls)
        self.assertEqual(create[0][-2:], UNSHARED)
        self.assertEqual(start[0][-3:], ["start", *UNSHARED])
        for argv in calls:
            self.assertNotIn(SIDECAR_SERVICE, argv)

    def test_an_unshared_project_boots_exactly_as_before(self):
        """Control: no sidecar in the file, so the bare form is unchanged."""
        calls = self._bring_up_calls(UNSHARED)
        bring_up = [a for a in calls if "--remove-orphans" in a]
        self.assertEqual(len(bring_up), 1, calls)
        self.assertEqual(bring_up[0][-1], "--remove-orphans")


class TheRecreateWithPatchesPath(unittest.TestCase):
    """`ldm start/restart --force-recreate` with portal patches."""

    def test_its_start_leaves_the_sidecar_out(self):
        from ldm_core.runtime import portal_patches as pp

        with tempfile.TemporaryDirectory() as d:
            _write_compose(Path(d), SHARED)
            manager = MagicMock()
            manager.run_command.return_value = ""
            with (
                patch.object(pp, "plan_patches", return_value=[object()]),
                patch.object(pp, "copy_patches_into"),
            ):
                self.assertTrue(
                    pp.recreate_with_patches(
                        manager, d, {"tag": "t"}, ["docker", "compose"], ["docker"]
                    )
                )
        start = manager.run_command.call_args_list[-1].args[0]
        self.assertEqual(start, ["docker", "compose", "start", *UNSHARED])


class ShareStartChecksBeforeTheSidecar(unittest.TestCase):
    """`ldm share start --provider lfr-tunnel-docker`, and the call readiness
    makes after `ldm run --share`: a refusal must come before any of the
    sidecar work, so nothing is regenerated or started."""

    def test_a_refusal_starts_nothing(self):
        from ldm_core.handlers.share import ShareService
        from ldm_core.tests.test_share import MockManager

        manager = MockManager()
        manager.args = MagicMock()
        manager.args.share_domain = None
        manager.args.domain = None
        manager.args.share_provider = None
        manager.args.provider = None
        manager.detect_project_path = MagicMock(return_value=Path("/fake/demo"))  # type: ignore[method-assign]
        manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={
                "container_name": "demo",
                "share_provider": "lfr-tunnel-docker",
            }
        )
        manager.runtime = MagicMock()
        service = ShareService(manager)
        boom = MagicMock(side_effect=AssertionError("sidecar work before the gate"))
        service._get_auth_token = boom  # type: ignore[method-assign]

        with (
            patch.object(service, "_assert_target_is_running"),
            patch(
                "ldm_core.handlers.share_credentials.assert_default_credentials_acceptable",
                side_effect=SystemExit(1),
            ) as gate,
            patch("ldm_core.handlers.share.subprocess.run") as run,
            self.assertRaises(SystemExit) as raised,
        ):
            service.cmd_start(project_id="demo", provider="lfr-tunnel-docker")
        self.assertEqual(raised.exception.code, 1)
        gate.assert_called_once()
        boom.assert_not_called()
        run.assert_not_called()
        manager.runtime.cmd_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
