"""Liferay's `action.auto_create_index` entries are removed with their project (LDM-#2318).

Liferay's Elasticsearch connector, not LDM, writes a persistent entry for its
own index prefix (`ElasticsearchSearchEngine.setAutoCreateIndex`) and never
removes it. Every deleted project therefore left one on the shared node. The
entry is right while the project exists, so only a deleted project's is
removed, and `ldm doctor` names those left by projects deleted earlier.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.diagnostics.doctor import _check_orphaned_auto_create_entries
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.infra import InfraService
from ldm_core.tests.test_orchestration import MockRuntime
from ldm_core.utils import auto_create_index_without, orphaned_auto_create_prefixes

# As observed on a shared node (LDM-#2318), spaces included.
OBSERVED = "-ldm-devcon2026-*, -ldm-ldm-smoke-test-56363-roundtrip-*, *"


class TheSettingIsEditedPerPrefix(unittest.TestCase):
    def test_only_the_named_prefix_goes(self):
        self.assertEqual(
            auto_create_index_without(
                OBSERVED, {"ldm-ldm-smoke-test-56363-roundtrip-"}
            ),
            "-ldm-devcon2026-*,*",
        )

    def test_an_enabled_entry_goes_too(self):
        self.assertEqual(
            auto_create_index_without("+ldm-a-*,-ldm-b-*,*", {"ldm-a-"}),
            "-ldm-b-*,*",
        )

    def test_an_entry_that_is_not_ldms_is_kept(self):
        self.assertEqual(
            auto_create_index_without("-other-*,-ldm-a-*,*", {"ldm-a-"}),
            "-other-*,*",
        )

    def test_nothing_left_resets_to_the_default(self):
        self.assertIsNone(auto_create_index_without("-ldm-a-*, *", {"ldm-a-"}))

    def test_orphans_are_the_unregistered_prefixes(self):
        self.assertEqual(
            orphaned_auto_create_prefixes(OBSERVED, ["devcon2026"]),
            ["ldm-ldm-smoke-test-56363-roundtrip-"],
        )

    def test_a_registered_name_in_another_case_is_not_an_orphan(self):
        self.assertEqual(orphaned_auto_create_prefixes("-ldm-shop-*,*", ["Shop"]), [])


class _SearchNode:
    """Answers the two requests `InfraService` makes of the shared node."""

    def __init__(self, value):
        self.value = value
        self.puts = []

    def __call__(self, cmd, *args, **kwargs):
        if "liferay-search-global" not in cmd:
            return ""
        if "PUT" in cmd:
            body = json.loads(cmd[cmd.index("-d") + 1])
            self.puts.append(body)
            self.value = body["persistent"]["action.auto_create_index"]
            return '{"acknowledged":true}'
        if any("_cluster/settings" in c for c in cmd):
            persistent = (
                {} if self.value is None else {"action.auto_create_index": self.value}
            )
            return json.dumps({"persistent": persistent, "transient": {}})
        return ""


@patch("ldm_core.runtime.orchestration.subprocess.run")
@patch("ldm_core.runtime.orchestration.shutil.rmtree")
class DeletingAProjectRemovesItsEntry(unittest.TestCase):
    """Through the real `cmd_down` and the real `InfraService`."""

    def _delete(self, registered, node):
        manager = MockRuntime()
        manager.infra = InfraService(manager)  # type: ignore[assignment]
        with (
            patch.object(
                manager,
                "read_meta",
                return_value={"project_name": "demo", "db_type": "hypersonic"},
            ),
            patch.object(
                manager, "detect_project_path", return_value=Path("/tmp/demo")
            ),  # nosec B108
            patch.object(BaseHandler, "run_command", side_effect=node),
            patch.object(Path, "exists", return_value=True),
            patch("ldm_core.utils.registered_project_names", return_value=registered),
        ):
            manager.cmd_down("demo", delete=True)

    def test_the_deleted_projects_entry_goes_and_others_stay(self, *_):
        node = _SearchNode("-ldm-demo-*,-ldm-other-*,*")
        self._delete(["demo", "other"], node)
        self.assertEqual(node.value, "-ldm-other-*,*")

    def test_an_entry_another_project_resolves_to_is_kept(self, *_):
        # `Demo` and `demo` share the prefix `ldm-demo-`.
        node = _SearchNode("-ldm-demo-*,*")
        self._delete(["demo", "Demo"], node)
        self.assertEqual(node.puts, [])


class DoctorNamesTheOrphans(unittest.TestCase):
    def test_the_hint_names_them_and_keeps_registered_entries(self):
        manager = MagicMock()
        manager.run_command.side_effect = _SearchNode(OBSERVED)
        infra = InfraService(manager)
        hints: list[str] = []
        with patch(
            "ldm_core.utils.registered_project_names", return_value=["devcon2026"]
        ):
            _check_orphaned_auto_create_entries(infra, hints.append, ["docker"])
        self.assertEqual(len(hints), 1)
        self.assertIn("ldm-ldm-smoke-test-56363-roundtrip-", hints[0])
        self.assertIn('"action.auto_create_index":"-ldm-devcon2026-*,*"', hints[0])

    def test_no_orphans_no_hint(self):
        manager = MagicMock()
        manager.run_command.side_effect = _SearchNode("-ldm-devcon2026-*,*")
        hints: list[str] = []
        with patch(
            "ldm_core.utils.registered_project_names", return_value=["devcon2026"]
        ):
            _check_orphaned_auto_create_entries(
                InfraService(manager), hints.append, ["docker"]
            )
        self.assertEqual(hints, [])


if __name__ == "__main__":
    unittest.main()
