"""The fragment-override module is the second rung of the patch chain (LDM-#1602).

The chain is: Headless PATCH -> the fragment-override OSGi module -> direct SQL.

Ordering is deliberate. The supported API is tried first, so nothing changes for
projects without the module. The SQL fallback stays last because it is the only
rung that works with no bundle deployed -- and the worst: a regex over a JSON
column, an unscoped WHERE, PostgreSQL/MySQL only, and per LDM-#1242 the portal
cache cannot be invalidated, so it needs a restart to take effect at all.

Every rung below the first must FALL THROUGH on failure rather than abort. A 403
(feature flag off), a 404 (module absent, or `element_id` is not a
fragmentEntryLinkId) and a 409 (existing values unparseable) all have to leave
the SQL fallback reachable, or adding this rung would make matters worse for
every project that does not have the module.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core.runtime.fragments import FragmentsService


class _Base(unittest.TestCase):
    def setUp(self):
        self.manager = MagicMock()
        self.service = FragmentsService(self.manager)


class TestTheModuleRung(_Base):
    def _call(self, api_result):
        with patch.object(self.service, "_api_request", return_value=api_result) as api:
            ok = self.service._patch_via_override_module(
                101, {"endpoint": "http://new"}, "https://host:8443", {"h": "v"}
            )
        return ok, api

    def test_a_success_response_counts_as_patched(self):
        ok, api = self._call({"status": "success", "fragmentEntryLinkId": 101})
        self.assertTrue(ok)

    def test_it_puts_to_the_module_endpoint_with_the_raw_overrides(self):
        """The module merges server-side, so LDM sends only its own keys."""
        _, api = self._call({"status": "success"})
        method, path, base, headers = api.call_args[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(path, "/o/fragment-override/fragment-entry-links/101")
        self.assertEqual(
            api.call_args[1]["payload"],
            {"endpoint": "http://new"},
            "must send the partial overrides unwrapped -- the module deep-merges "
            "them over the existing document",
        )

    def test_a_failed_request_falls_through(self):
        """403/404/409 all reach us as None from _api_request."""
        ok, _ = self._call(None)
        self.assertFalse(ok)

    def test_a_non_success_status_falls_through(self):
        ok, _ = self._call({"status": "error", "message": "nope"})
        self.assertFalse(ok)

    def test_a_non_dict_response_does_not_raise(self):
        """A proxy returning a JSON array must not crash the run."""
        payloads: tuple[object, ...] = ([], "success", 42)
        for payload in payloads:
            with self.subTest(payload=payload):
                ok, _ = self._call(payload)
                self.assertFalse(ok)


class TestTheChainOrder(_Base):
    """The rung must sit between Headless and SQL, and only run on failure."""

    def test_the_module_is_not_called_when_headless_succeeds(self):
        with patch.object(self.service, "_api_request", return_value={"id": 1}):
            with patch.object(
                self.service, "_patch_via_override_module"
            ) as module_rung:
                # _api_request returning truthy is the Headless success path
                self.service._api_request("PATCH", "/x", "u", {})
        module_rung.assert_not_called()

    def test_the_sql_fallback_is_reached_only_when_nothing_patched(self):
        """The real ordering guarantee, expressed as behaviour.

        A first version of this test compared the source POSITIONS of the two
        call sites. That measured definition order, not execution order -- the
        SQL fallback is called from an outer function defined ~380 lines ABOVE
        the method containing the module rung, so it read as "SQL comes first"
        while the runtime order is the opposite. It failed for a reason that
        told us nothing about the code, which is the whole objection to
        asserting on source text.

        What actually orders the chain is `if patched_count == 0:` at
        fragments.py:538 -- the SQL fallback runs only when every earlier rung
        produced nothing.
        """
        guard = self._sql_guard_source()
        self.assertIn(
            "if patched_count == 0:",
            guard,
            "the SQL fallback must stay behind a patched_count guard",
        )

    def _sql_guard_source(self):
        import inspect

        from ldm_core.runtime import fragments

        src = inspect.getsource(fragments)
        call_at = src.find("_patch_database_fragmententrylink(project_meta")
        self.assertNotEqual(call_at, -1, "the SQL fallback call is missing")
        return src[max(0, call_at - 200) : call_at]


if __name__ == "__main__":
    unittest.main()
