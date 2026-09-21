"""LDM-#1891: `ldm wait --probe-url` replaces the derived readiness URL.

LDM cannot infer the right probe target in every topology. With SSL on a
remote node the certificate is issued for the project's host name while the
derived URL dials the node's address, so the probe and the certificate
disagree by construction -- for any SSL-enabled remote project, not just the
one that reported it.

The default is NOT changing. LDM-#1223 made the probe follow the target node
instead of dialling 127.0.0.1 on the client, and that is correct; these tests
pin it, because it is the thing most at risk from adding an override.
"""

import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.runtime.readiness import ReadinessService


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        # Hold the mock directly: reaching through `svc.manager` makes mypy
        # resolve the real manager's signatures rather than MagicMock's.
        self.manager = MagicMock()
        self.svc = ReadinessService(self.manager)
        self.manager.infra.get_proxy_ports.return_value = None
        self.manager.detect_project_path.return_value = self.root
        self.meta = {
            "project_name": "aica-e2e",
            "host_name": "aica-e2e.demo",
            "port": 8080,
            "liferay_container_name": "aica-e2e",
        }
        self.manager.read_meta.return_value = self.meta

    def _target_ctx(self, is_remote, host):
        ctx = MagicMock()
        ctx.is_remote = is_remote
        ctx.target = MagicMock()
        ctx.target.host = host
        return ctx


class TestTheDefaultIsUnchanged(_Base):
    """LDM-#1223's behaviour must survive the addition of the override."""

    def _derive(self, is_remote, host, ssl=True):
        with patch(
            "ldm_core.runtime.readiness.resolve_target_context",
            return_value=self._target_ctx(is_remote, host),
        ):
            return self.svc._derive_probe_url(
                self.meta, "aica-e2e.demo", ssl, "aws-2", self.root
            )

    def test_a_remote_node_is_probed_at_its_own_address(self):
        """The whole point of LDM-#1223: do not dial 127.0.0.1 on the client."""
        self.assertEqual("https://16.192.101.229", self._derive(True, "16.192.101.229"))

    def test_a_local_target_is_probed_by_host_name(self):
        self.assertEqual("https://aica-e2e.demo", self._derive(False, None))

    def test_the_scheme_still_follows_the_ssl_decision(self):
        self.assertTrue(self._derive(False, None, ssl=True).startswith("https://"))
        self.assertTrue(self._derive(False, None, ssl=False).startswith("http://"))


class TestTheOverrideReplacesIt(_Base):
    def _dial(self, probe_url):
        """Runs cmd_wait far enough to reach the HTTP probe, recording the URL."""
        dialled = []
        derived = []

        class Resp:
            status_code = 200

        def fake_get(url, **_kw):
            dialled.append(url)
            return Resp()

        def spy_derive(*_a, **_k):
            derived.append(True)
            return "https://derived.example"

        with (
            patch.object(ReadinessService, "_wait_for_ready", return_value=True),
            patch.object(ReadinessService, "_derive_probe_url", side_effect=spy_derive),
            patch("requests.get", side_effect=fake_get),
        ):
            self.manager.composer._is_ssl_active.return_value = True
            self.manager.runtime._scan_for_expected_deployables.return_value = {}
            try:
                self.svc.cmd_wait("aica-e2e", timeout=5, probe_url=probe_url)
            except SystemExit:
                pass
        return dialled, bool(derived)

    def test_the_supplied_url_is_dialled_verbatim(self):
        dialled, _ = self._dial("https://aica-e2e.demo")
        self.assertIn("https://aica-e2e.demo", dialled)

    def test_nothing_rewrites_the_scheme_host_or_port(self):
        """A caller who names a URL means that URL."""
        odd = "http://127.0.0.1:18080/some/path"
        dialled, _ = self._dial(odd)
        self.assertIn(odd, dialled)

    def test_the_derivation_is_skipped_entirely(self):
        """Not computed and discarded -- it feeds nothing else in this method."""
        _, derived = self._dial("https://aica-e2e.demo")
        self.assertFalse(derived, "the derived URL should not even be computed")

    def test_without_the_override_the_derivation_is_used(self):
        dialled, derived = self._dial(None)
        self.assertTrue(derived, "the default path must still derive the URL")
        self.assertIn("https://derived.example", dialled)


class TestTheFlagReachesTheCommand(unittest.TestCase):
    def test_probe_url_parses_and_defaults_to_none(self):
        from ldm_core.cli import get_parser, preprocess_args

        parser = get_parser()[0]
        bare = parser.parse_args(preprocess_args(["wait", "demo"]))
        self.assertIsNone(getattr(bare, "probe_url", None))

        given = parser.parse_args(
            preprocess_args(["wait", "demo", "--probe-url", "https://x.test"])
        )
        self.assertEqual("https://x.test", given.probe_url)
