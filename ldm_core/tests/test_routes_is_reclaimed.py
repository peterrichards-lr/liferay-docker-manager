"""`routes` must be reclaimed wherever the other bind-mounted trees are (LDM-#2144).

Liferay publishes into `routes/default/<project>` as uid 1000 under its own
umask. `_scaffold_routes_tree` creates the tree with
`safe_mkdir(..., exist_ok=True)`, which sets no mode on a directory that already
exists, and Liferay's `_deleteRoutes` removes only the per-project child. So a
`routes/default` created before the `UMASK=0022` fix (LDM-#1944) kept mode 750
indefinitely -- measured on a node where the three client-extension children
were correctly 755 and their parent was still 750 from before the upgrade.

A 750 directory owned by uid 1000 cannot be traversed by the SSH user running
the remote sync. rsync cannot repair it either: `-a` implies `-p`/`-g`/`-o`, so
it *attempts* the chgrp and is refused for want of ownership. Neither can any
in-process step, for the same reason. Only a root helper container can -- which
makes the reclaim list the one mechanism positioned to fix this, and it was the
one list `routes` was missing from.

`routes` was already in the eleven-key list in `handlers/base.py`. It was absent
from the four-key list used by the run pipeline and the repair path, which is
the list that actually ran on the reporting node.

The first test proves the behaviour end to end on the path where that is cheap.
The second is a deliberate config-level assertion covering all three sites,
recorded as such: driving `ExecutionStage.execute` would mean standing up a
compose run, and the thing worth protecting is that the lists do not drift apart
again -- which is a property of the lists, not of one execution.
"""

import ast
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Every site that reclaims a set of project subdirectories.
_RECLAIM_SITES = [
    "ldm_core/pipelines/run.py",
    "ldm_core/handlers/system.py",
    "ldm_core/handlers/base.py",
    # LDM-#2266: the node-side reclaim that runs before every remote sync.
    "ldm_core/config.py",
]

#: The call that makes a loop a RECLAIM loop. `handlers/base.py` also carries
#: two directory-CREATION lists driving `safe_mkdir`, which disagree with each
#: other and with this -- deliberately, and tracked separately. Keying on the
#: call rather than on the entries keeps those out.
_RECLAIM_CALL = "reclaim_volume_permissions"


def _reclaim_lists(rel_path: str) -> list[list[str]]:
    """Every `for <var> in [...]` whose BODY calls reclaim_volume_permissions."""
    tree = ast.parse((_PROJECT_ROOT / rel_path).read_text(encoding="utf-8"))
    found: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.List):
            continue
        calls = {
            c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "")
            for c in ast.walk(node)
            if isinstance(c, ast.Call)
        }
        if _RECLAIM_CALL not in calls:
            continue
        found.append(
            [
                e.value
                for e in node.iter.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
        )
    return found


class TheRepairPathReclaimsRoutes(unittest.TestCase):
    """`ldm rescue` exists to hand a wedged project back to its container.

    A routes tree stuck at 750 from before the umask fix is exactly that state,
    and this is the path a user is told to run.
    """

    def test_rescue_reclaims_the_routes_tree(self):
        from ldm_core.handlers.system import SystemService

        with TemporaryDirectory() as td:
            root = Path(td) / "proj"
            for sub in ("deploy", "logs", "osgi", "files", "routes"):
                (root / sub).mkdir(parents=True)

            manager = MagicMock()
            manager.detect_project_path.return_value = root
            manager.read_meta.return_value = {}
            manager.args = MagicMock()
            # Every absent attribute on a MagicMock is truthy, so these must be
            # set explicitly or cmd_rescue takes its dry-run path and does
            # nothing -- which would make this test pass against ANY code.
            manager.dry_run = False
            manager.verbose = False
            svc = SystemService(manager)
            svc.manager = manager

            reclaimed = []

            def record(path, *a, **kw):
                reclaimed.append(Path(path).name)
                return True

            keys = ("deploy", "logs", "osgi", "files", "routes")
            with (
                # cmd_rescue resolves through the SERVICE, not the manager.
                patch.object(SystemService, "detect_project_path", return_value=root),
                patch.object(
                    SystemService,
                    "setup_paths",
                    return_value={k: root / k for k in keys},
                ),
                patch("platform.system", return_value="Linux"),
                patch("ldm_core.utils.reclaim_volume_permissions", side_effect=record),
                patch("ldm_core.ui.UI.ask_choices", return_value="no"),
                patch("ldm_core.ui.UI.ask", return_value=""),
            ):
                try:
                    svc.cmd_rescue(project_id="proj")
                except (SystemExit, Exception):
                    # The rescue does much more than this; we only care that
                    # the reclaim reached routes before anything else failed.
                    pass

        self.assertIn(
            "routes",
            reclaimed,
            "'ldm rescue' must reclaim the routes tree. It is the repair path "
            "a user is pointed at, and a routes/default left at 750 by an "
            "older umask is precisely the wedged state it exists to undo "
            f"(LDM-#2144). Reclaimed: {reclaimed}",
        )


class EveryReclaimListAgrees(unittest.TestCase):
    """The lists are maintained by hand in three files and have drifted before.

    LDM-#2143 records the consequence: the documented table describes a single
    "pre-boot reclaim", while the code has several that disagree -- which is how
    a reader concluded `routes` was covered when on their path it was not.
    """

    def test_at_least_one_reclaim_list_was_found_in_each_file(self):
        """Guards this whole class: a parser that silently finds nothing
        would make every assertion below vacuously true."""
        for rel in _RECLAIM_SITES:
            with self.subTest(file=rel):
                self.assertTrue(
                    _reclaim_lists(rel),
                    f"No reclaim list found in {rel}. Either the list moved or "
                    f"this parser no longer recognises it -- fix the parser "
                    f"rather than deleting the test, or the checks below stop "
                    f"checking anything.",
                )

    def test_every_reclaim_list_includes_routes(self):
        for rel in _RECLAIM_SITES:
            for items in _reclaim_lists(rel):
                with self.subTest(file=rel, entries=len(items)):
                    self.assertIn(
                        "routes",
                        items,
                        f"{rel} reclaims {items} without 'routes'. Only a root "
                        f"helper container can repair a routes tree owned by "
                        f"another uid, so a list that omits it leaves "
                        f"LDM-#2144 unfixable on that path.",
                    )

    def test_every_reclaim_list_includes_the_container_shared_trees(self):
        """The four Liferay writes into. Dropping one reproduces LDM-#645."""
        for rel in _RECLAIM_SITES:
            for items in _reclaim_lists(rel):
                with self.subTest(file=rel):
                    for required in ("deploy", "logs", "files"):
                        self.assertIn(
                            required,
                            items,
                            f"{rel} reclaims {items}, missing '{required}'. "
                            f"These are bind-mounted into the Liferay "
                            f"container, which runs as uid 1000 and must write "
                            f"to them.",
                        )


if __name__ == "__main__":
    unittest.main()
