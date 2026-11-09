"""LDM-#2119: a restore must not leave the previous build's documents behind.

Reported by the devcon2026 team. After `ldm restore devcon2026 -n
clean-2026q3.5` the company index still held 189 object-entry documents for
classes that no longer exist.

The database was replaced; the indices were not. The reindex LDM schedules
cannot repair that -- a Liferay reindex re-adds documents for classes that
still have indexers, and orphaned documents are precisely the ones that do
not. So the scheduled reindex makes this look handled when it is not.

Three defects, and the third is the one that makes the other two moot:

1. `_delete_project_indices` is called only inside
   `if search_snapshot_name and search_snapshot_name != "None":`. A snapshot
   taken while search was a sidecar, taken before search existed, or whose
   search snapshot failed, skips the whole branch.

2. It asks for the wrong indices. Everything else in this module derives the
   pattern from `search_index_prefix()`, which is what LDM writes into
   `indexNamePrefix` (`composer.py`) -- `ldm-<name>-`, lowercased by Liferay.
   `_delete_project_indices` used `<container_name>-*` instead, with no `ldm-`
   prefix and no lowercasing, so it named indices that do not exist.

3. It issues a WILDCARD delete. LDM runs `elasticsearch:8.x`, which defaults
   `action.destructive_requires_name` to true and rejects a wildcard DELETE
   with 400. Nothing sets it to false, and the call passes `check=False`, so
   the rejection was discarded.

Taken together: the function has never deleted an index, on either branch.
Fixing only the reachability would have produced a call that still did
nothing, and the scheduled reindex would have gone on hiding it.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.tests.test_snapshot import MockSnapshotManager

PROJECT = "devcon2026"
# What `composer.py` writes as indexNamePrefix, via `search_index_prefix`.
INDEX_PREFIX = f"ldm-{PROJECT}-"
LIVE_INDICES = (
    f"{INDEX_PREFIX}14683668377142-object-entry",
    f"{INDEX_PREFIX}14683668377142-workflow-metrics-transitions",
)
FOREIGN_INDEX = "ldm-otherproject-14683668377142-object-entry"
# LDM-#2313: a project whose name starts with PROJECT plus "-". Its indices
# begin with INDEX_PREFIX, so `<prefix>*` alone matches them.
EXTENDING_PROJECT = f"{PROJECT}-approach2"
EXTENDING_INDEX = f"ldm-{EXTENDING_PROJECT}-14683668377142-object-entry"


def _pattern_matches(name, pattern):
    """Elasticsearch's multi-target syntax: comma-separated wildcards, where a
    leading `-` excludes what the patterns before it matched."""
    import fnmatch

    included = False
    for part in pattern.split(","):
        if part.startswith("-"):
            if fnmatch.fnmatchcase(name, part[1:]):
                included = False
        elif fnmatch.fnmatchcase(name, part):
            included = True
    return included


def _curl_url(argv):
    """The URL a `docker exec ... curl` argv is aimed at, or None."""
    if "curl" not in argv:
        return None
    for token in argv:
        if isinstance(token, str) and token.startswith("localhost:9200/"):
            return token
    return None


def _deleted_urls(mock_run):
    return [
        _curl_url(c.args[0])
        for c in mock_run.call_args_list
        if c.args and "-X" in c.args[0] and "DELETE" in c.args[0]
    ]


class _FakeElasticsearch:
    """Answers the handful of endpoints the restore path touches.

    Deliberately REJECTS a wildcard DELETE the way Elasticsearch 8 does, so a
    fix that keeps the wildcard cannot pass by accident.
    """

    def __init__(self, extra_indices=()):
        self.deleted = []
        self.extra_indices = tuple(extra_indices)

    def __call__(self, argv, *_args, **_kwargs):
        if not isinstance(argv, list):
            return ""
        if "ps" in argv:
            return "container-id"
        url = _curl_url(argv)
        if url is None:
            return ""
        target = url[len("localhost:9200/") :]
        if "DELETE" in argv:
            return self._delete(target)
        return self._get(target)

    def _delete(self, target):
        """Refuses a wildcard the way Elasticsearch 8 does.

        `action.destructive_requires_name` defaults to true and nothing in
        `handlers/infra.py` turns it off, so this is the real server's answer
        and not a convenience.
        """
        if "*" in target:
            return (
                '{"error":{"type":"illegal_argument_exception","reason":'
                '"Wildcard expressions or all indices are not allowed"},'
                '"status":400}'
            )
        for name in target.split(","):
            if name and name not in self.deleted:
                self.deleted.append(name)
        return '{"acknowledged":true}'

    def _get(self, target):
        if target.startswith("_cat/indices"):
            pattern = target[len("_cat/indices/") :].split("?")[0]
            known = [*LIVE_INDICES, FOREIGN_INDEX, *self.extra_indices]
            return "\n".join(
                i
                for i in known
                if _pattern_matches(i, pattern) and i not in self.deleted
            )
        if "_recovery" in target:
            return '{"x":{"shards":[{"stage":"DONE"}]}}'
        return ""


class TestRestoreClearsStaleSearch(unittest.TestCase):
    def setUp(self):
        self.manager = MockSnapshotManager()
        self.test_dir = Path(tempfile.mkdtemp())
        from ldm_core.config import TargetNode

        patcher = patch("ldm_core.docker_service.get_active_target")
        self.mock_target = patcher.start()
        self.mock_target.return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _restore(self, meta):
        es = _FakeElasticsearch()
        with (
            patch.object(self.manager, "run_command", side_effect=es) as mock_run,
            patch("time.sleep"),
        ):
            self.manager.snapshot.search._restore_search(self.test_dir, meta, PROJECT)
        return es, mock_run

    def test_indices_are_cleared_when_the_snapshot_has_no_search_snapshot(self):
        """The reported case: nothing to restore is not nothing to remove."""
        es, _ = self._restore({"use_shared_search": "true"})
        self.assertEqual(
            sorted(es.deleted),
            sorted(LIVE_INDICES),
            "a restore carrying no search snapshot left the previous build's "
            "indices in place, which is how 189 orphaned object-entry "
            "documents survived",
        )

    def test_indices_are_cleared_when_the_snapshot_has_none_as_a_string(self):
        """`search_snapshot` is written as the string "None" in older meta."""
        es, _ = self._restore({"search_snapshot": "None"})
        self.assertEqual(sorted(es.deleted), sorted(LIVE_INDICES))

    def test_the_delete_is_not_a_wildcard(self):
        """Elasticsearch 8 rejects a wildcard DELETE with 400.

        The fake refuses it exactly as the real server does, so this cannot be
        satisfied by a call that merely looks plausible.
        """
        _, mock_run = self._restore({"use_shared_search": "true"})
        urls = _deleted_urls(mock_run)
        self.assertTrue(urls, "no DELETE was issued at all")
        for url in urls:
            self.assertNotIn(
                "*",
                url or "",
                "a wildcard DELETE is rejected by ES 8 with 400 and, because "
                "the call is check=False, the rejection is discarded",
            )

    def test_another_projects_indices_are_untouched(self):
        """The prefix must identify this project, not merely start like it."""
        es, _ = self._restore({"use_shared_search": "true"})
        self.assertNotIn(FOREIGN_INDEX, es.deleted)

    def test_a_search_snapshot_still_clears_first_then_restores(self):
        """The pre-existing ordering must survive: delete, then restore.

        Restoring onto surviving indices is what `_delete_project_indices` was
        added for in the first place.
        """
        es, mock_run = self._restore({"search_snapshot": "snap1"})
        self.assertEqual(sorted(es.deleted), sorted(LIVE_INDICES))

        order = []
        for call in mock_run.call_args_list:
            argv = call.args[0] if call.args else []
            if not isinstance(argv, list):
                continue
            if "DELETE" in argv:
                order.append("delete")
            elif "_restore" in (_curl_url(argv) or ""):
                order.append("restore")
        self.assertIn("delete", order)
        self.assertIn("restore", order)
        self.assertLess(
            order.index("delete"),
            order.index("restore"),
            "the restore ran before the indices were cleared",
        )


class TestAnExtendingProjectsIndicesAreUntouched(unittest.TestCase):
    """LDM-#2313: `ldm-devcon2026-*` also matches `ldm-devcon2026-approach2-*`.

    The registry is a real file under `LDM_HOME`, read by the real
    `registered_project_names`, so the seam between the registry and the
    pattern is crossed rather than patched.
    """

    def setUp(self):
        import json
        import os

        from ldm_core.config import TargetNode
        from ldm_core.constants import REGISTRY_FILE

        self.manager = MockSnapshotManager()
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".ldm").mkdir()
        (self.home / ".ldm" / REGISTRY_FILE).write_text(
            json.dumps(
                {
                    name: {"path": str(self.home / name), "host": None}
                    for name in (PROJECT, EXTENDING_PROJECT)
                }
            ),
            encoding="utf-8",
        )
        env = patch.dict(os.environ, {"LDM_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        target = patch("ldm_core.docker_service.get_active_target")
        target.start().return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )
        self.addCleanup(target.stop)

    def _restore(self, meta):
        es = _FakeElasticsearch(extra_indices=(EXTENDING_INDEX,))
        with (
            patch.object(self.manager, "run_command", side_effect=es) as mock_run,
            patch("time.sleep"),
        ):
            self.manager.snapshot.search._restore_search(self.home, meta, PROJECT)
        return es, mock_run

    def test_the_restore_deletes_only_this_projects_indices(self):
        es, _ = self._restore({"use_shared_search": "true"})

        self.assertNotIn(EXTENDING_INDEX, es.deleted)
        self.assertEqual(sorted(es.deleted), sorted(LIVE_INDICES))

    def test_the_search_snapshot_restore_excludes_it_too(self):
        """A snapshot taken before this fix may hold the other project's
        indices; restoring it must not bring them back."""
        import json

        _, mock_run = self._restore({"search_snapshot": "snap1"})

        bodies = [
            json.loads(c.args[0][c.args[0].index("-d") + 1])
            for c in mock_run.call_args_list
            if c.args
            and isinstance(c.args[0], list)
            and "_restore" in (_curl_url(c.args[0]) or "")
        ]
        self.assertEqual(len(bodies), 1)
        self.assertFalse(
            _pattern_matches(EXTENDING_INDEX, bodies[0]["indices"]),
            bodies[0]["indices"],
        )
        self.assertTrue(_pattern_matches(LIVE_INDICES[0], bodies[0]["indices"]))


class TestSearchIndexPattern(unittest.TestCase):
    def test_extending_projects_are_excluded(self):
        from ldm_core.utils import search_index_pattern

        self.assertEqual(
            search_index_pattern("a", ["a", "a-b", "a-b-c", "ab", "b"]),
            "ldm-a-*,-ldm-a-b-*,-ldm-a-b-c-*",
        )

    def test_a_numbered_sibling_is_excluded(self):
        """Why a company-ID digit match was not used: `a-2`'s indices are
        `ldm-a-2-<companyId>-...`, which `ldm-a-<digits>-` would match."""
        from ldm_core.utils import search_index_pattern

        pattern = search_index_pattern("a", ["a", "a-2"])

        self.assertFalse(_pattern_matches("ldm-a-2-20097-object-entry", pattern))
        self.assertTrue(_pattern_matches("ldm-a-20097-object-entry", pattern))

    def test_no_other_project_is_the_plain_prefix(self):
        from ldm_core.utils import search_index_pattern

        self.assertEqual(search_index_pattern("a", ["a", "b"]), "ldm-a-*")


if __name__ == "__main__":
    unittest.main()
