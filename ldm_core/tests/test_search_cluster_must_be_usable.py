"""LDM-#2247: "ready" meant Elasticsearch answered HTTP, not that it was usable.

The readiness probe in `setup_global_search` is:

```python
res = ... curl -s localhost:9200 ...
if res and '"cluster_name"' in res:
    ready = True
```

A RED cluster with zero active shards answers `GET /` perfectly happily, so
that probe passes and LDM prints "Infrastructure setup complete". Measured on
macOS 26.6.2 / OrbStack after the auto-repair had just wiped the shared data:

```json
{"status":"red","active_primary_shards":0,"active_shards":0,
 "unassigned_shards":17,"unassigned_primary_shards":17}
```

Liferay's `ElasticsearchSearchEngine.activate()` calls `_waitForYellowStatus`,
which times out and throws, collapsing the OSGi service graph behind it --
`SearchEngineHelperImpl`, `IndexSearcherHelper`, `FacetedSearcherManager`,
`Searcher`, `AssetHelper` and every portlet downstream. It retries every 30
seconds, so the portal never finishes starting. Three `v2.26.6-pre.10`
verification runs failed this way and were investigated as disk (LDM-#2240)
and then as memory pressure (LDM-#2245); both were refuted.

The probe is shared by the first attempt AND the post-repair restart, so this
is one gate in one place rather than a new check bolted after the repair.

`TheRedClusterMustNotTriggerTheWipe` is the load-bearing one. Tightening a
readiness probe that gates a DESTRUCTIVE repair is how a stricter check
becomes a data-loss bug: the wipe at `handlers/infra.py` deletes the shared
search data of every project on the machine, and a cluster that is red for a
reason wiping cannot fix -- a disk watermark, say -- must not reach it.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.handlers.infra import InfraService
from ldm_core.tests.test_infra import MockInfraManager

ROOT_OK = json.dumps({"cluster_name": "liferay-cluster"})

RED = json.dumps(
    {
        "cluster_name": "liferay-cluster",
        "status": "red",
        "active_primary_shards": 0,
        "active_shards": 0,
        "unassigned_shards": 17,
        "unassigned_primary_shards": 17,
    }
)
YELLOW = json.dumps({"cluster_name": "liferay-cluster", "status": "yellow"})
GREEN = json.dumps({"cluster_name": "liferay-cluster", "status": "green"})

WATERMARK_EXPLAIN = json.dumps(
    {
        "node_allocation_decisions": [
            {
                "deciders": [
                    {
                        "decider": "disk_threshold",
                        "decision": "NO",
                        "explanation": (
                            "the node is above the high watermark cluster "
                            "setting [cluster.routing.allocation.disk.watermark.high=90%]"
                        ),
                    }
                ]
            }
        ]
    }
)


class SearchClusterCase(unittest.TestCase):
    def setUp(self):
        self.manager = MockInfraManager()
        self.infra = InfraService(self.manager)
        docker_patcher = patch("ldm_core.docker_service.run_command", return_value="")
        self.mock_docker_run_command = docker_patcher.start()
        self.addCleanup(docker_patcher.stop)
        self.manager.defaults.get.side_effect = lambda _key, default=None: default

    def run_setup(self, health, explain=WATERMARK_EXPLAIN):
        """Drive setup_global_search with a scripted Elasticsearch."""
        self.commands: list[str] = []

        def dispatch(cmd, *_args, **_kwargs):
            joined = (
                " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
            )
            self.commands.append(joined)
            if "_cluster/health" in joined:
                return health
            if "allocation/explain" in joined:
                return explain
            if "localhost:9200" in joined and "_" not in joined.split("9200")[-1]:
                return ROOT_OK
            if "localhost:9200" in joined:
                return ROOT_OK
            return ""

        raised = None
        with (
            patch("ldm_core.docker_service.DockerService.exists", return_value=False),
            patch("ldm_core.handlers.infra.get_actual_home", return_value=Path("/tmp")),
            patch("ldm_core.utils.reclaim_volume_permissions"),
            patch("ldm_core.handlers.infra.UI") as mock_ui,
            patch("ldm_core.diagnostics.doctor.run_command", side_effect=dispatch),
            patch.object(self.manager, "run_command", side_effect=dispatch),
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            mock_ui.die.side_effect = SystemExit(3)
            try:
                self.infra.setup_global_search(force=True)
            except SystemExit as exc:
                raised = exc
        return mock_ui, raised

    @staticmethod
    def said(mock_ui, channel):
        calls = getattr(mock_ui, channel).call_args_list
        parts = []
        for c in calls:
            parts += [str(a) for a in c.args]
            parts += [str(v) for v in c.kwargs.values()]
        return " ".join(parts)


class TheClusterMustBeUsableNotMerelyListening(SearchClusterCase):
    def test_a_red_cluster_is_refused(self):
        _, raised = self.run_setup(RED)
        self.assertIsNotNone(raised, "a red cluster was handed over as healthy")

    def test_a_yellow_cluster_is_accepted(self):
        _, raised = self.run_setup(YELLOW)
        self.assertIsNone(raised)

    def test_a_green_cluster_is_accepted(self):
        _, raised = self.run_setup(GREEN)
        self.assertIsNone(raised)

    def test_the_refusal_reports_the_unassigned_primaries(self):
        mock_ui, _ = self.run_setup(RED)
        said = self.said(mock_ui, "die")
        self.assertIn("17", said)

    def test_the_refusal_explains_why_the_shards_are_unassigned(self):
        # Remediation 4 on the issue: reuse the doctor's allocation-explain
        # rather than writing a second one. Without this the user is told the
        # cluster is red and left to find out why themselves -- which is the
        # two-hour diagnosis this issue exists to remove.
        mock_ui, _ = self.run_setup(RED)
        self.assertIn("watermark", self.said(mock_ui, "die").lower())

    def test_it_says_liferay_cannot_start_against_it(self):
        # The consequence is the point. A bare "cluster is red" reads as a
        # warning about search quality, not as "the portal will never boot".
        mock_ui, _ = self.run_setup(RED)
        self.assertIn("liferay", self.said(mock_ui, "die").lower())


class TheRedClusterMustNotTriggerTheWipe(SearchClusterCase):
    """Tightening the probe must not route a red cluster into the auto-repair.

    The repair deletes the SHARED search data -- every project's index on the
    machine. It exists for a cluster that will not start at all; a cluster that
    is red because shards cannot be ALLOCATED is not that, and wiping cannot
    fix it.
    """

    def test_no_container_is_removed(self):
        self.run_setup(RED)
        self.assertFalse(
            [c for c in self.commands if "rm" in c.split() and "-f" in c.split()],
            "the red-cluster path reached the destructive repair",
        )

    def test_the_wipe_is_not_announced(self):
        mock_ui, _ = self.run_setup(RED)
        self.assertNotIn(
            "automatic search volume repair", self.said(mock_ui, "warning")
        )


class AnUnreadableClusterIsNotAPass(SearchClusterCase):
    """ "Could not determine" is reported, never silently treated as healthy."""

    def test_an_empty_health_response_is_reported(self):
        mock_ui, raised = self.run_setup("")
        self.assertIsNone(raised, "an unreadable cluster must not be fatal")
        self.assertIn("cluster", self.said(mock_ui, "warning").lower())

    def test_unparseable_health_is_reported(self):
        mock_ui, raised = self.run_setup("<html>502 Bad Gateway</html>")
        self.assertIsNone(raised)
        self.assertIn("cluster", self.said(mock_ui, "warning").lower())
