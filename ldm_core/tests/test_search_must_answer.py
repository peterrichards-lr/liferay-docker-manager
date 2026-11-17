"""An index that cannot answer a search must fail the command (LDM-#2316).

Twice on 2026-11-02 a restored company index came back STARTED while every
`_count` answered 503 (`AlreadyClosedException`). The restore printed a warning,
then "Restore complete.", and exited 0. On 2026-11-13 the same dead-shard state
appeared with no restore at all: an index Liferay had created three minutes
earlier went dead during boot, and `ldm run` reported the project ready.

Either way, automation carried on over a portal whose search-backed lists were
empty. The root cause is not pinned down (low-to-medium confidence: a one-off
denial at the virtiofs bind mount), so these guard the outcome instead: both
paths now exit 3 with the remediation.
"""

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

# The module, not the class: a TestCase imported by name is collected again here.
from ldm_core.tests import test_readiness
from ldm_core.tests.test_restore_clears_stale_search import LIVE_INDICES, PROJECT
from ldm_core.tests.test_restore_starts_shared_search import (
    SHARED_COMPOSE,
    SIDECAR_COMPOSE,
    RestoreHarness,
)

COMPANY_INDEX, OTHER_INDEX = LIVE_INDICES


class _BootedElasticsearch:
    """Shared search after a boot: the project's indices exist, and each
    answers `_count` with the status the test gives it."""

    def __init__(self, count_status):
        self.count_status = count_status
        self.counted = []

    def __call__(self, cmd, *_args, **_kwargs):
        joined = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else ""
        if "inspect" in joined:
            return "healthy"
        url = next(
            (t for t in cmd if isinstance(t, str) and t.startswith("localhost:9200/")),
            "",
        )
        if "_cat/indices" in url:
            return "\n".join(LIVE_INDICES)
        if url.endswith("/_count"):
            name = url[len("localhost:9200/") : -len("/_count")]
            self.counted.append(name)
            return self.count_status.get(name, "200")
        return ""


class ABootOverADeadIndexIsNotReady(unittest.TestCase):
    """Drives the real `_wait_for_ready` to the moment it calls the project
    ready, with the real search probe behind it."""

    # Set by TestReadiness.setUp, which this borrows.
    handler: Any
    tmp_dir: Path

    def setUp(self):
        # TestReadiness's isolation (requests, update check, active target,
        # volume reclaim), borrowed without inheriting its test cases.
        test_readiness.TestReadiness.setUp(self)  # type: ignore[arg-type]
        self.addCleanup(test_readiness.TestReadiness.tearDown, self)  # type: ignore[arg-type]
        from ldm_core.handlers.snapshot import SnapshotService

        # The real probe, not the MagicMock MockRuntime carries.
        self.handler.snapshot = SnapshotService(self.handler)
        self.handler.args.browser = False

    def _boot(self, compose, count_status):
        (self.tmp_dir / "docker-compose.yml").write_text(compose)
        es = _BootedElasticsearch(count_status)
        with (
            patch("time.sleep"),
            patch.object(self.handler, "run_command", side_effect=es),
        ):
            self.handler.handler.readiness._wait_for_ready(
                {"container_name": PROJECT}, "test.local"
            )
        return es

    def test_a_dead_index_fails_the_boot_with_exit_3(self):
        with (
            patch(
                "ldm_core.runtime.readiness.UI.die", side_effect=SystemExit(3)
            ) as die,
            self.assertRaises(SystemExit),
        ):
            self._boot(SHARED_COMPOSE, {COMPANY_INDEX: "503"})

        message = die.call_args[0][0]
        self.assertIn(COMPANY_INDEX, message)
        self.assertNotIn(OTHER_INDEX, message)
        self.assertEqual(die.call_args.kwargs["exit_code"], 3)
        self.assertIn(
            "docker restart liferay-search-global", die.call_args.kwargs["tip"]
        )

    def test_healthy_indices_do_not_fail_the_boot(self):
        with patch("ldm_core.runtime.readiness.UI.die") as die:
            es = self._boot(SHARED_COMPOSE, {})

        self.assertEqual(sorted(es.counted), sorted(LIVE_INDICES))
        die.assert_not_called()

    def test_a_probe_that_could_not_run_does_not_fail_the_boot(self):
        with patch("ldm_core.runtime.readiness.UI.die") as die:
            self._boot(SHARED_COMPOSE, {COMPANY_INDEX: ""})
        die.assert_not_called()

    def test_a_sidecar_project_is_not_probed(self):
        es = self._boot(SIDECAR_COMPOSE, {COMPANY_INDEX: "503"})
        self.assertEqual(es.counted, [])


class ARestoreOverADeadIndexFails(RestoreHarness):
    """Drives the real `cmd_restore`; `_restore_search` answers with the
    indices its post-restore probe found dead."""

    def _restore_with_dead(self, dead):
        with (
            patch.object(
                self.manager.snapshot,
                "flag_reindex",
                side_effect=lambda *_a: self._record("flag-reindex", True),
            ),
            patch("ldm_core.handlers.snapshot.UI.success") as success,
            patch(
                "ldm_core.handlers.snapshot.UI.die", side_effect=SystemExit(3)
            ) as die,
        ):
            self.restore_search_result = tuple(dead)
            try:
                self._restore(SHARED_COMPOSE)
                exited = None
            except SystemExit as e:
                exited = e.code
        return exited, success, die

    def test_a_dead_index_fails_the_restore_after_scheduling_the_reindex(self):
        exited, success, die = self._restore_with_dead([COMPANY_INDEX])

        self.assertEqual(exited, 3)
        self.assertNotIn(
            "Restore complete.", [c.args[0] for c in success.call_args_list]
        )
        # The tip says "the reindex is already scheduled": it must be.
        self.assertIn("flag-reindex", self.calls)
        self.assertIn("ldm run", die.call_args.kwargs["tip"])

    def test_healthy_indices_still_complete(self):
        exited, success, die = self._restore_with_dead([])

        self.assertIsNone(exited)
        die.assert_not_called()
        success.assert_any_call("Restore complete.")


if __name__ == "__main__":
    unittest.main()
