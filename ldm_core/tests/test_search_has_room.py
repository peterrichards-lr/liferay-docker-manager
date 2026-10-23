"""A boot is refused when the shared search disk cannot take it (LDM-#2247).

Remediation 3 on the issue. A cluster can be yellow on a disk too full for the
NEXT index: above the high watermark Elasticsearch will not place a new
index's primary, so a new project's Liferay hangs for its whole healthcheck
window; above flood stage every index is read-only. Observed on v2.26.7-pre.6
verification (#2290): an OrbStack Mac at 96% passed infra setup, then hung.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.handlers.infra import InfraService


def _responder(percent, indices=(), settings=None):
    def respond(cmd, *_a, **_k):
        url = cmd[-1]
        if "_cat/allocation" in url:
            if percent is None:
                return ""
            return json.dumps([{"node": "n1", "disk.percent": str(percent)}])
        if "_cluster/settings" in url:
            return json.dumps(settings or {"defaults": {}})
        if "_cat/indices" in url:
            return json.dumps([{"index": i} for i in indices])
        return ""

    return respond


class _Infra(unittest.TestCase):
    def _run(self, percent, indices=(), settings=None, prefix="ldm-proj-"):
        manager = MagicMock()
        manager.target = None
        manager.run_command.side_effect = _responder(percent, indices, settings)
        infra = InfraService(manager)
        said: dict[str, list[str]] = {"warning": [], "detail": []}
        with (
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=True
            ),
            patch(
                "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
                return_value=["docker"],
            ),
            patch(
                "ldm_core.handlers.infra.UI.warning", side_effect=said["warning"].append
            ),
            patch(
                "ldm_core.handlers.infra.UI.detail", side_effect=said["detail"].append
            ),
        ):
            try:
                infra.ensure_search_has_room(prefix)
            except SystemExit as exc:
                return exc.code, said
        return None, said


class TheDiskIsChecked(_Infra):
    def test_flood_stage_refuses_every_project(self):
        code, _ = self._run(96, indices=["ldm-proj-1"])
        self.assertEqual(code, 3)

    def test_high_watermark_refuses_a_project_without_its_index(self):
        code, _ = self._run(92)
        self.assertEqual(code, 3)

    def test_high_watermark_lets_a_project_with_its_index_through(self):
        code, said = self._run(92, indices=["ldm-proj-1-workflow"])
        self.assertIsNone(code)
        self.assertTrue(said["warning"])

    def test_low_watermark_warns(self):
        code, said = self._run(87)
        self.assertIsNone(code)
        self.assertTrue(said["warning"])

    def test_plenty_of_room_says_nothing(self):
        code, said = self._run(40)
        self.assertIsNone(code)
        self.assertEqual(said["warning"], [])

    def test_an_unreadable_disk_is_unverified_not_passed(self):
        code, said = self._run(None)
        self.assertIsNone(code)
        self.assertTrue(any("NOT verified" in d for d in said["detail"]))


class TheClustersOwnThresholdsAreUsed(_Infra):
    def test_a_custom_high_watermark_is_respected(self):
        settings = {
            "persistent": {"cluster.routing.allocation.disk.watermark.high": "80%"}
        }
        code, _ = self._run(82, settings=settings)
        self.assertEqual(code, 3)

    def test_a_byte_watermark_falls_back_to_the_default(self):
        settings = {
            "defaults": {"cluster.routing.allocation.disk.watermark.high": "10gb"}
        }
        code, _ = self._run(88, settings=settings)
        self.assertIsNone(code)


class TheBootCallsIt(unittest.TestCase):
    """The call site: a boot against shared search asks for room, with its prefix."""

    def test_composer_stage_checks_room_for_this_projects_index(self):

        from ldm_core.pipelines.run import ComposerStage, RunPipelineContext

        context = RunPipelineContext(MagicMock())
        context.manager.non_interactive = True
        context.set("project_id", "proj")
        context.set("is_new_project", False)
        context.set("dry_run", False)
        context.set("no_up", False)
        context.set("use_shared_search", True)
        context.set("host_name", "localhost")
        context.set(
            "project_meta", {"container_name": "proj", "ssl": "false", "port": 8080}
        )
        root = MagicMock()
        root.__truediv__.return_value.exists.return_value = False
        context.set("paths", {"root": root, "configs": MagicMock()})
        context.set("infra_ports", {})
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("ldm_core.pipelines.run.UI"),
        ):
            try:
                ComposerStage().execute(context)
            except Exception:
                pass
        context.manager.infra.ensure_search_has_room.assert_called_once_with(
            "ldm-proj-"
        )


if __name__ == "__main__":
    unittest.main()
