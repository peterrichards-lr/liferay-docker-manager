"""`ldm rm --delete` removes the project's indices from the shared search node (LDM-#2337).

It dropped a shared-database project's schema and left its shared-search
indices behind for good, on a node every project's disk headroom depends on.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.infra import InfraService
from ldm_core.tests.test_orchestration import MockRuntime


class _SearchNode:
    """Answers the index listing and records the DELETEs."""

    def __init__(self, indices):
        self.indices = indices
        self.listed: list[str] = []
        self.deleted: list[str] = []

    def __call__(self, cmd, *args, **kwargs):
        if "liferay-search-global" not in cmd:
            return ""
        url = cmd[-1]
        if "DELETE" in cmd:
            self.deleted.extend(url.split("/", 1)[1].split(","))
            return '{"acknowledged":true}'
        if "_cat/indices/" in url:
            self.listed.append(url)
            return "\n".join(self.indices)
        return ""


@patch("ldm_core.runtime.orchestration.subprocess.run")
@patch("ldm_core.runtime.orchestration.shutil.rmtree")
class DeletingAProjectRemovesItsIndices(unittest.TestCase):
    """Through the real `cmd_down` and the real index deleter."""

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
                manager,
                "detect_project_path",
                return_value=Path("/tmp/demo"),  # nosec B108
            ),
            patch.object(BaseHandler, "run_command", side_effect=node),
            patch.object(Path, "exists", return_value=True),
            patch("ldm_core.utils.registered_project_names", return_value=registered),
        ):
            manager.cmd_down("demo", delete=True)

    def test_the_projects_indices_are_deleted_by_name(self, *_):
        node = _SearchNode(["ldm-demo-20097-journal", "ldm-demo-20097-workflow"])
        self._delete(["demo"], node)
        self.assertEqual(
            node.deleted, ["ldm-demo-20097-journal", "ldm-demo-20097-workflow"]
        )

    def test_a_project_whose_name_extends_it_is_excluded(self, *_):
        node = _SearchNode([])
        self._delete(["demo", "demo-b"], node)
        self.assertTrue(node.listed)
        self.assertIn("-ldm-demo-b-*", node.listed[0])

    def test_indices_another_project_resolves_to_are_kept(self, *_):
        # `Demo` and `demo` share the prefix `ldm-demo-`.
        node = _SearchNode(["ldm-demo-20097-journal"])
        self._delete(["demo", "Demo"], node)
        self.assertEqual(node.listed, [])
        self.assertEqual(node.deleted, [])


if __name__ == "__main__":
    unittest.main()
