"""The fragment patcher must respect its budget, and must not spend requests
it knows will fail (LDM-#1728, LDM-#1618).

Two defects that presented as one symptom: `ldm run` sitting for 27 minutes
against a nominal 900s budget, with Liferay healthy the whole time.

**LDM-#1728 -- the budget was never a budget.** `--fragment-patch-timeout` is
documented and announced as a poll budget in seconds, then converted straight
into a retry count:

    max_retries = max(1, timeout // 5)

with the `time.sleep(5)` *inside* the loop alongside the HTTP calls. The real
cost was `retries x (request latency + 5s)` and nothing measured elapsed time,
so the stated budget was a floor, not a ceiling -- and the gap widened exactly
when the API was slow, which is the case the budget exists to bound. Both loops
took the full count independently, doubling it again.

**LDM-#1618 -- one rung could never fire.** The module endpoint declares

    @PathParam("fragmentEntryLinkId") long fragmentEntryLinkId

(verified in the module's source, not inferred) while the Headless page-element
id is a UUID, measured on a live instance. Every call was a guaranteed 404.
That rung is kept, because it is the better mechanism whenever a real
`fragmentEntryLinkId` is available -- it goes through
`FragmentEntryLinkLocalService`, so cache invalidation, model listeners and
indexing all happen, none of which the SQL fallback can do. What changes is
that LDM no longer spends a request proving the id is the wrong shape.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.runtime.fragments import FragmentsService

UUID_ID = "6f4d9b77-4a14-a5dc-74b8-e0ef4dcee23c"


class TheModuleRungSkipsAnImpossibleId(unittest.TestCase):
    """LDM-#1618."""

    def setUp(self):
        self.service = FragmentsService(MagicMock())

    def _call(self, element_id):
        with patch.object(
            self.service, "_api_request", return_value={"status": "success"}
        ) as api:
            ok = self.service._patch_via_override_module(
                element_id, {"endpoint": "http://new"}, "https://host:8443", {}
            )
        return ok, api

    def test_a_page_element_uuid_costs_no_request(self):
        """The measured real-world case: every id the traversal yields."""
        ok, api = self._call(UUID_ID)

        self.assertFalse(ok)
        api.assert_not_called()

    def test_a_numeric_id_is_still_sent(self):
        """The rung must not be disabled outright -- only the doomed calls."""
        ok, api = self._call(101)

        self.assertTrue(ok)
        api.assert_called_once()
        self.assertEqual(
            api.call_args[0][1], "/o/fragment-override/fragment-entry-links/101"
        )

    def test_a_numeric_string_is_still_sent(self):
        """Ids arrive from JSON, so the digits may be a str."""
        _, api = self._call("33693")

        api.assert_called_once()

    def test_a_negative_id_is_not_mistaken_for_a_uuid(self):
        """Still a long as far as JAX-RS is concerned; let the server judge."""
        _, api = self._call("-1")

        api.assert_called_once()


class TheBudgetIsAWallClockCeiling(unittest.TestCase):
    """LDM-#1728."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "configs").mkdir()
        (self.root / "configs" / "fragment-overrides.json").write_text(
            json.dumps({"someFragment": {"endpoint": "http://new"}})
        )

        self.manager = MagicMock()
        self.manager.non_interactive = True
        self.manager.parse_version.return_value = (2026, 1, 7)
        self.manager.run_command.return_value = "0.0.0.0:8080"
        self.manager.config.get_global_config.return_value = {}
        self.service = FragmentsService(self.manager)

        self.paths = {"root": self.root, "configs": self.root / "configs"}
        self.meta = {"container_name": "proj", "tag": "2026.q1.7-lts"}

    def _run(self, timeout, seconds_per_request):
        """Drive the patcher on a fake clock.

        `_api_request` returns nothing useful, so the loops keep polling --
        which is the shape that produced the 27-minute run.
        """
        clock = {"t": 0.0}

        def monotonic():
            return clock["t"]

        def sleep(seconds):
            clock["t"] += seconds

        def api_request(*_a, **_k):
            clock["t"] += seconds_per_request

        with (
            patch("ldm_core.runtime.fragments.time.monotonic", side_effect=monotonic),
            patch("ldm_core.runtime.fragments.time.sleep", side_effect=sleep),
            patch.object(self.service, "_api_request", side_effect=api_request) as api,
            patch.object(
                self.service, "_patch_database_fragmententrylink", return_value=0
            ),
            patch("ldm_core.runtime.fragments.UI.info") as info,
            patch("ldm_core.runtime.fragments.UI.warning"),
        ):
            self.service._patch_fragment_overrides(
                self.meta, self.paths, timeout=timeout
            )
        return clock["t"], api.call_count, info

    def test_a_slow_api_cannot_overrun_the_budget(self):
        """The defect: latency multiplied the budget instead of consuming it.

        At 5s per request the old arithmetic spent 60 retries x (5s + 5s) =
        600s against a 300s budget, and that was the *cheap* case.
        """
        elapsed, _calls, _ = self._run(timeout=300, seconds_per_request=5)

        self.assertLessEqual(
            elapsed,
            330,
            f"ran {elapsed:.0f}s against a 300s budget -- the timeout is still "
            "a retry count",
        )

    def test_the_two_loops_share_one_budget(self):
        """Each used to get the full count, so the real ceiling was doubled."""
        elapsed, _calls, _ = self._run(timeout=100, seconds_per_request=1)

        self.assertLessEqual(
            elapsed,
            130,
            f"ran {elapsed:.0f}s against a 100s budget -- the second loop got "
            "a fresh budget rather than the remainder",
        )

    def test_a_long_wait_says_something(self):
        """A silent poll is indistinguishable from a hang."""
        _elapsed, _calls, info = self._run(timeout=300, seconds_per_request=5)

        self.assertTrue(
            info.called,
            "nothing was printed during a five-minute wait; diagnosing this "
            "needed a stack sample last time",
        )

    def test_it_still_polls_rather_than_giving_up_at_once(self):
        """The budget must bound the wait, not remove it."""
        _elapsed, calls, _ = self._run(timeout=300, seconds_per_request=1)

        self.assertGreater(calls, 1, "the retry loop stopped retrying")


if __name__ == "__main__":
    unittest.main()
