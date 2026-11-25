"""LDM-#2526: no public URL for an instance the default admin login still opens.

The probe is injected or `urlopen` is faked throughout -- no test reaches a
portal.
"""

import io
import json
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ldm_core.constants import DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD
from ldm_core.handlers import share_credentials as guard


def _manager(non_interactive=True, allow=False, dry_run=False):
    manager = MagicMock()
    manager.dry_run = dry_run
    manager.non_interactive = non_interactive
    manager.args = SimpleNamespace(allow_default_credentials=allow)
    manager.composer._is_ssl_active.return_value = False
    return manager


META = {"host_name": "localhost", "port": 8080}


class TestTheGate(unittest.TestCase):
    def _check(self, manager, works):
        probe = MagicMock(return_value=works)
        guard.assert_default_credentials_acceptable(manager, dict(META), probe=probe)
        return probe

    @patch.object(guard.UI, "die", side_effect=SystemExit(1))
    def test_non_interactive_refuses_and_names_the_flag(self, die):
        with self.assertRaises(SystemExit):
            self._check(_manager(), True)
        self.assertIn("default admin login", die.call_args.args[0])
        self.assertIn("--allow-default-credentials", die.call_args.kwargs["tip"])

    def test_it_probes_the_default_pair_at_the_project_url(self):
        with patch.object(guard.UI, "die", side_effect=SystemExit(1)):
            with self.assertRaises(SystemExit):
                probe = MagicMock(return_value=True)
                guard.assert_default_credentials_acceptable(
                    _manager(), dict(META), probe=probe
                )
        probe.assert_called_once_with(
            "http://localhost:8080", DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD
        )

    @patch.object(guard.UI, "die", side_effect=SystemExit(1))
    def test_the_flag_lets_it_through_with_a_warning(self, die):
        with patch.object(guard.UI, "warning") as warn:
            self._check(_manager(allow=True), True)
        die.assert_not_called()
        self.assertIn("--allow-default-credentials", warn.call_args.args[0])

    @patch.object(guard.UI, "die", side_effect=SystemExit(1))
    def test_interactive_asks_and_respects_no(self, die):
        with patch.object(guard.UI, "confirm", return_value=False) as confirm:
            with self.assertRaises(SystemExit):
                self._check(_manager(non_interactive=False), True)
        self.assertEqual(confirm.call_args.args[1], "N", "the safe answer is default")

    @patch.object(guard.UI, "die", side_effect=SystemExit(1))
    def test_interactive_yes_shares(self, die):
        with patch.object(guard.UI, "confirm", return_value=True):
            self._check(_manager(non_interactive=False), True)
        die.assert_not_called()

    @patch.object(guard.UI, "die", side_effect=SystemExit(1))
    def test_changed_credentials_share_silently(self, die):
        with patch.object(guard.UI, "warning") as warn:
            self._check(_manager(), False)
        die.assert_not_called()
        warn.assert_not_called()

    @patch.object(guard.UI, "die", side_effect=SystemExit(1))
    def test_an_unanswerable_check_warns_and_shares(self, die):
        with patch.object(guard.UI, "warning") as warn:
            self._check(_manager(), None)
        die.assert_not_called()
        self.assertIn("Could not check", warn.call_args.args[0])

    def test_a_dry_run_never_probes(self):
        probe = self._check(_manager(dry_run=True), True)
        probe.assert_not_called()


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestTheProbe(unittest.TestCase):
    def _probe(self, effect):
        with patch.object(guard.urllib.request, "urlopen", side_effect=effect) as op:
            result = guard.default_credentials_work(
                "https://acme.demo/", "test@liferay.com", "test"
            )
        return result, op

    def test_a_sign_in_as_that_account_is_true(self):
        result, op = self._probe(
            [_Resp(json.dumps({"emailAddress": "Test@Liferay.com"}).encode())]
        )
        self.assertIs(result, True)
        req = op.call_args.args[0]
        self.assertEqual(req.full_url, "https://acme.demo" + guard.PROBE_PATH)
        self.assertEqual(
            req.get_header("Authorization"), "Basic dGVzdEBsaWZlcmF5LmNvbTp0ZXN0"
        )

    def test_a_refusal_is_false(self):
        for code in (401, 403):
            with self.subTest(code=code):
                err = urllib.error.HTTPError("u", code, "no", {}, None)  # type: ignore[arg-type]
                self.assertIs(self._probe(err)[0], False)

    def test_anything_else_is_unknown(self):
        self.assertIsNone(self._probe(urllib.error.URLError("refused"))[0])
        err = urllib.error.HTTPError("u", 500, "boom", {}, None)  # type: ignore[arg-type]
        self.assertIsNone(self._probe(err)[0])
        self.assertIsNone(self._probe([_Resp(b"{}")])[0])


class TestShareStartRunsTheGate(unittest.TestCase):
    """The call site: `ShareService.cmd_start` asks before any tunnel work."""

    def test_the_gate_runs_after_the_running_check_and_before_the_binary(self):
        from ldm_core.handlers.share import ShareService
        from ldm_core.tests.test_share import MockManager

        manager = MockManager()
        manager.args = MagicMock()
        manager.args.share_domain = None
        manager.args.domain = None
        manager.args.share_provider = None
        manager.args.provider = None
        manager.detect_project_path = MagicMock(return_value=Path("/fake/demo"))  # type: ignore[method-assign]
        manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={"container_name": "demo", "share_provider": "lfr-tunnel"}
        )
        service = ShareService(manager)
        boom = MagicMock(side_effect=AssertionError("tunnel work before the gate"))
        service._ensure_binary = boom  # type: ignore[method-assign]
        service._get_auth_token = boom  # type: ignore[method-assign]

        with (
            patch(
                "ldm_core.handlers.share_credentials.assert_default_credentials_acceptable",
                side_effect=SystemExit(1),
            ) as gate,
            self.assertRaises(SystemExit),
        ):
            service.cmd_start(project_id="demo", provider="lfr-tunnel")
        gate.assert_called_once()
        self.assertIs(gate.call_args.args[0], manager)
        self.assertEqual(gate.call_args.args[1]["container_name"], "demo")
        boom.assert_not_called()


class TestTheFlagIsAccepted(unittest.TestCase):
    def test_every_command_that_shares_takes_it(self):
        from ldm_core.cli import get_parser, preprocess_args

        parser = get_parser()[0]
        for argv in (
            ["share", "start", "--allow-default-credentials"],
            ["run", "--share", "--allow-default-credentials"],
            ["start", "acme", "--share", "--allow-default-credentials"],
            ["import", "x.ldmp", "--share", "--allow-default-credentials"],
            ["quickstart", "aica", "--share", "--allow-default-credentials"],
        ):
            with self.subTest(argv=argv):
                args = parser.parse_args(preprocess_args(["ldm", *argv])[1:])
                self.assertIs(args.allow_default_credentials, True)


if __name__ == "__main__":
    unittest.main()
