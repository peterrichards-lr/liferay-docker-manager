"""`ldm start` must bring shared search back before Liferay (LDM-#2319).

`docker compose start` cannot do it: the compose file names
`liferay-search-global` only in Liferay's environment, never as a dependency.
After a Docker VM restart every container is stopped, and `ldm start` booted
Liferay against no search at all.

These drive the real `cmd_start` into the real `InfraService` with Docker
simulated underneath, and record the order things happen in -- the fix is
worthless if search starts AFTER Liferay, and a test of either half alone
would not see that.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.infra import InfraService
from ldm_core.runtime.orchestration import OrchestrationService
from ldm_core.tests.test_orchestration import MockRuntime

SHARED_COMPOSE = """services:
  liferay:
    environment:
      - LIFERAY_ELASTICSEARCH_PERIOD_CONNECTION_PERIOD_URL=http://liferay-search-global:9200
"""

SIDECAR_COMPOSE = """services:
  liferay:
    environment:
      - LIFERAY_ELASTICSEARCH_PERIOD_CONNECTION_PERIOD_URL=http://search:9200
  search:
    image: elasticsearch:8.19.1
"""


class _Docker:
    """A Docker daemon holding one shared search container, or none."""

    def __init__(self, search="stopped", health="yellow"):
        self.search = search  # "stopped", "running" or "missing"
        self.health = health
        self.events: list[str] = []

    def exists(self, name, target_name=None):
        return name == "liferay-search-global" and self.search != "missing"

    def is_running(self, name, target_name=None):
        return name == "liferay-search-global" and self.search == "running"

    def start(self, name, target_name=None):
        self.events.append(f"docker start {name}")
        if self.search == "stopped":
            self.search = "running"

    def run_command(self, cmd, **kwargs):
        if "ps" in cmd and "-q" in cmd:
            return "container-id"
        if "exec" in cmd and "liferay-search-global" in cmd:
            if any("_cluster/health" in part for part in cmd):
                self.events.append("cluster health")
                return json.dumps({"status": self.health, "active_shards": 1})
            return '{"cluster_name": "docker-cluster"}'
        if "start" in cmd:
            self.events.append(f"compose start ({Path(kwargs['cwd']).name})")
        return ""


class StartBringsSharedSearchBack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = MockRuntime()
        self.manager.infra = InfraService(self.manager)  # type: ignore[assignment]
        self.orch = OrchestrationService(self.manager)

    def tearDown(self):
        self.tmp.cleanup()

    def _project(self, name, compose):
        root = Path(self.tmp.name) / name
        root.mkdir()
        (root / "docker-compose.yml").write_text(compose)
        return root

    def _start(self, docker, roots, **kwargs):
        with (
            patch("ldm_core.docker_service.DockerService.exists", docker.exists),
            patch(
                "ldm_core.docker_service.DockerService.is_running", docker.is_running
            ),
            patch("ldm_core.docker_service.DockerService.start", docker.start),
            patch.object(BaseHandler, "run_command", side_effect=docker.run_command),
            patch.object(self.manager, "read_meta", return_value={}),
            patch.object(self.manager, "get_container_status", return_value="running"),
            patch.object(
                self.manager,
                "find_dxp_roots",
                return_value=[{"path": r} for r in roots],
            ),
            patch.object(self.manager, "detect_project_path", return_value=roots[0]),
            patch.object(
                self.manager.infra,
                "setup_global_search",
                side_effect=AssertionError(
                    "start must never provision shared search: that path can wipe it"
                ),
            ),
            patch("ldm_core.handlers.infra.time.sleep"),
        ):
            self.orch.cmd_start(**kwargs)

    def test_a_stopped_search_container_is_started_before_liferay(self):
        docker = _Docker(search="stopped")
        root = self._project("shop", SHARED_COMPOSE)

        self._start(docker, [root], project_id="shop")

        self.assertEqual(
            docker.events,
            [
                "docker start liferay-search-global",
                "cluster health",
                "compose start (shop)",
            ],
        )

    def test_a_running_search_container_is_left_alone(self):
        docker = _Docker(search="running")
        root = self._project("shop", SHARED_COMPOSE)

        self._start(docker, [root], project_id="shop")

        self.assertEqual(docker.events, ["compose start (shop)"])

    def test_a_missing_search_container_is_refused_not_provisioned(self):
        docker = _Docker(search="missing")
        root = self._project("shop", SHARED_COMPOSE)

        with patch("ldm_core.ui.UI.die", side_effect=SystemExit(3)) as die:
            with self.assertRaises(SystemExit):
                self._start(docker, [root], project_id="shop")

        self.assertIn("does not exist", die.call_args[0][0])
        self.assertIn("ldm run", die.call_args.kwargs["tip"])
        self.assertEqual(die.call_args.kwargs["exit_code"], 3)
        self.assertEqual(docker.events, [], "nothing may start without search")

    def test_a_red_cluster_stops_liferay_from_starting(self):
        docker = _Docker(search="stopped", health="red")
        root = self._project("shop", SHARED_COMPOSE)

        with (
            patch("ldm_core.ui.UI.die", side_effect=SystemExit(3)),
            # The refusal's allocation report is a bonus that asks the real
            # daemon; it is not what this test is about.
            patch("ldm_core.diagnostics.doctor._check_elasticsearch_watermarks"),
        ):
            with self.assertRaises(SystemExit):
                self._start(docker, [root], project_id="shop")

        self.assertNotIn("compose start (shop)", docker.events)

    def test_a_sidecar_project_never_touches_shared_search(self):
        docker = _Docker(search="stopped")
        root = self._project("lab", SIDECAR_COMPOSE)

        self._start(docker, [root], project_id="lab")

        self.assertEqual(docker.events, ["compose start (lab)"])

    def test_start_all_skips_shared_projects_but_starts_the_rest(self):
        docker = _Docker(search="missing")
        shared = self._project("shop", SHARED_COMPOSE)
        sidecar = self._project("lab", SIDECAR_COMPOSE)

        with self.assertRaises(SystemExit) as ctx:
            self._start(docker, [shared, sidecar], all_projects=True)

        # LDM-#1343's batch contract: the batch exits non-zero, but only after
        # every other project has been started.
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(docker.events, ["compose start (lab)"])


if __name__ == "__main__":
    unittest.main()
