"""A restore must start shared search before it clears indices (LDM-#2392).

AICA, stable v2.26.10: `ldm -y quickstart aica --replace` printed

    Global search service not running; this project's existing indices were
    not cleared. Documents from the previous build may survive this restore.

and carried on. Nothing on the restore path starts `liferay-search-global`:
`cmd_restore` scaffolds and re-renders with `cmd_run(no_up=True)`, which never
provisions search, and the reset and `_restore_search` both clear indices only
if search is already running. So after a Docker VM restart every restore kept
the previous build's documents -- the stale indices behind the broken global
menu.

These drive the real `cmd_restore` and assert the ORDER: search is started
before the reset that clears it, and a search that cannot be started stops the
restore before anything is reset.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.tests.test_snapshot import MockSnapshotManager

SHARED_COMPOSE = (
    "services:\n"
    "  liferay:\n"
    "    environment:\n"
    "      - LIFERAY_ELASTICSEARCH_NETWORK_HOST_ADDRESSES=liferay-search-global:9200\n"
)
SIDECAR_COMPOSE = "services:\n  liferay:\n    image: liferay/dxp\n"


class RestoreHarness(unittest.TestCase):
    """Drives the real `cmd_restore`, recording the order of what it does.

    No tests of its own, so subclasses do not re-run any.
    """

    # What the patched `_restore_search` reports as unable to search.
    restore_search_result: tuple = ()

    def setUp(self):
        self.manager = MockSnapshotManager()
        self.test_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.test_dir)

        snap_dir = self.test_dir / "snapshots" / "20261116_120000"
        snap_dir.mkdir(parents=True)
        (snap_dir / "volume.tgz").touch()
        (snap_dir / "meta").touch()

        self.manager.args.latest = True
        self.manager.args.verify = False
        self.manager.args.list = False
        self.manager.args.backup_dir = None

        # One shared timeline, so the assertion is about order, not presence.
        self.calls: list[str] = []
        self.infra = MagicMock()
        # MockSnapshotManager declares no `infra`; the real manager does.
        setattr(self.manager, "infra", self.infra)  # noqa: B010
        self.infra.ensure_global_search_started.side_effect = lambda **_kw: (
            self._record("start-search", True)
        )
        self.manager.runtime.cmd_reset.side_effect = lambda **_kw: self._record("reset")

    def _record(self, step, result=None):
        self.calls.append(step)
        return result

    def _restore(self, compose):
        (self.test_dir / "docker-compose.yml").write_text(compose)
        paths = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "state": self.test_dir / "osgi" / "state",
            "data": self.test_dir / "data",
        }
        with (
            patch(
                "ldm_core.handlers.base.BaseHandler.detect_project_path",
                return_value=self.test_dir,
            ),
            patch("ldm_core.handlers.base.BaseHandler.setup_paths", return_value=paths),
            patch("ldm_core.handlers.base.BaseHandler.read_meta", return_value={}),
            patch("ldm_core.handlers.base.BaseHandler.write_meta"),
            patch.object(self.manager.snapshot.volumes, "_hydrate_named_volumes"),
            patch.object(
                self.manager.snapshot.database,
                "_restore_database",
                side_effect=lambda *_a, **_k: self._record("restore-db"),
            ),
            patch.object(
                self.manager.snapshot.search,
                "_restore_search",
                side_effect=lambda *_a, **_k: self._record(
                    "restore-search", list(self.restore_search_result)
                ),
            ),
            patch("time.sleep"),
        ):
            self.manager.snapshot.cmd_restore("test")


class RestoreStartsSharedSearchFirst(RestoreHarness):
    def test_shared_search_is_started_before_the_reset_clears_it(self):
        self._restore(SHARED_COMPOSE)

        self.assertIn("start-search", self.calls)
        self.assertLess(
            self.calls.index("start-search"),
            self.calls.index("reset"),
            "search was started after the reset, so the reset's index clear "
            "ran against a stopped search container and was skipped",
        )
        self.assertLess(
            self.calls.index("start-search"), self.calls.index("restore-search")
        )

    def test_a_search_that_cannot_start_stops_the_restore_before_any_reset(self):
        """`ensure_global_search_started` refuses with exit 3 when the
        container is missing or will not come up. The restore must not then
        carry on over the indices it could not clear."""

        def refuse(**_kw):
            self.calls.append("start-search")
            raise SystemExit(3)

        self.infra.ensure_global_search_started.side_effect = refuse

        with self.assertRaises(SystemExit) as cm:
            self._restore(SHARED_COMPOSE)

        self.assertEqual(cm.exception.code, 3)
        self.assertNotIn("reset", self.calls)
        self.assertNotIn("restore-db", self.calls)

    def test_a_sidecar_project_does_not_touch_shared_search(self):
        self._restore(SIDECAR_COMPOSE)

        self.infra.ensure_global_search_started.assert_not_called()
        self.assertIn("restore-db", self.calls)


if __name__ == "__main__":
    unittest.main()
