"""LDM-#2525: stable `--json` for `info`, `doctor` and `share status`.

The contract (docs/reference/json_output.md): stdout carries exactly one JSON
object, whatever the helpers print on the way; the documented fields are there;
and the exit code is the one the formatted view would give.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ldm_core.cli import _build_command_map, get_parser, preprocess_args
from ldm_core.ui import UI


def _run(fn, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    code = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            fn(*args, **kwargs)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def _parse(*argv):
    return get_parser()[0].parse_args(preprocess_args(["ldm", *argv])[1:])


class TestTheFlagsReachTheirHandlers(unittest.TestCase):
    """The dispatch, not only the helper: what ships is the call site."""

    def test_info_json(self):
        args = _parse("info", "acme", "--json")
        manager = MagicMock()
        _build_command_map(args, manager)[("info", None)]()
        self.assertIs(manager.diagnostics.cmd_info.call_args.kwargs["as_json"], True)

    def test_doctor_json_is_on_the_args_the_runner_reads(self):
        self.assertIs(_parse("doctor", "--json").json, True)
        self.assertIs(_parse("system", "doctor", "--json").json, True)

    def test_share_status_json(self):
        args = _parse("share", "status", "acme", "--json")
        manager = MagicMock()
        with patch(
            "ldm_core.handlers.share_status_json.print_share_status_json"
        ) as emit:
            _build_command_map(args, manager)[("share", "status")]()
        emit.assert_called_once_with(manager.share, "acme")
        manager.share.cmd_status.assert_not_called()

    def test_share_status_without_json_is_unchanged(self):
        args = _parse("share", "status", "acme")
        manager = MagicMock()
        _build_command_map(args, manager)[("share", "status")]()
        manager.share.cmd_status.assert_called_once_with(project_id="acme")


class TestInfoJson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "acme"
        self.root.mkdir()
        self.meta = {
            "container_name": "acme",
            "host_name": "localhost",
            "port": "8080",
            "uuid": "u-1",
            "share": "true",
            "share_provider": "lfr-tunnel",
            "share_subdomain": "peters",
            "share_domain": "lfr-demo.se",
            "credentials": [
                {
                    "type": "admin",
                    "email": "a@b",
                    "password": "s3cret",  # pragma: allowlist secret
                }
            ],
            "extensions": [{"id": "svc", "is_service": True}],
            "port_svc": 58081,
        }

    def _info(self, **kwargs):
        from ldm_core.diagnostics.info import run_info

        handler = MagicMock()

        def detect(_pid):
            print("Select a project:")  # a picker must not reach stdout
            return self.root

        handler.manager.detect_project_path.side_effect = detect
        handler.manager.read_meta.return_value = dict(self.meta)
        handler.manager.target = None
        handler.manager.composer._is_ssl_active.return_value = False
        handler.manager.setup_paths.return_value = {
            "cx": self.root / "client-extensions"
        }
        handler.manager.share.resolve_public_tunnel_urls.return_value = [
            "https://peters.lfr-demo.se"
        ]
        with (
            patch(
                "ldm_core.docker_service.DockerService.get_status",
                return_value="running",
            ),
            patch(
                "ldm_core.config.get_active_target",
                return_value=SimpleNamespace(name="local"),
            ),
        ):
            return _run(run_info, handler, "acme", **kwargs)

    def test_stdout_is_one_json_object_with_the_documented_fields(self):
        code, out, err = self._info(as_json=True)
        self.assertIsNone(code)
        data = json.loads(out)
        self.assertIn("Select a project:", err)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["project"], "acme")
        self.assertEqual(data["url"], "http://localhost:8080")
        self.assertEqual(data["status"], "running")
        self.assertIs(data["running"], True)
        self.assertEqual(data["port"], 8080)
        self.assertEqual(data["id"], "u-1")
        self.assertEqual(data["share"]["public_urls"], ["https://peters.lfr-demo.se"])
        self.assertEqual(
            data["extensions"],
            [
                {
                    "id": "svc",
                    "container": "acme-svc",
                    "local_url": "http://svc.localhost:58081",
                    "public_url": "https://peters-svc.lfr-demo.se",
                }
            ],
        )

    def test_it_carries_no_credentials(self):
        _code, out, _err = self._info(as_json=True)
        self.assertNotIn("s3cret", out)
        self.assertNotIn("credentials", json.loads(out))

    def test_a_share_recorded_as_false_is_not_shared(self):
        self.meta.update(share="false", share_provider=None)
        _code, out, _err = self._info(as_json=True)
        data = json.loads(out)
        self.assertIs(data["share"]["enabled"], False)
        self.assertIsNone(data["extensions"][0]["public_url"])


class TestDoctorJson(unittest.TestCase):
    def setUp(self):
        for flag in ("NO_COLOR", "NON_INTERACTIVE"):
            self.addCleanup(setattr, UI, flag, getattr(UI, flag, False))
        UI.NO_COLOR = True

    def _doctor(self, results, hints=(), **flags):
        from ldm_core.diagnostics import doctor

        handler = MagicMock()
        handler.manager.args = SimpleNamespace(
            json=True, skip_project=True, all=False, **flags
        )
        handler.manager.target = None
        runner = doctor.DoctorRunner(handler)

        def checks():
            print("noise from a check")  # must land on stderr
            runner.results.extend(results)
            for h in hints:
                runner.add_hint(*h)

        with (
            patch.object(
                doctor, "_get_env_info", return_value=("arm64", "macOS", "x", "y")
            ),
            patch.object(runner, "_check_tooling_and_integrity", side_effect=checks),
            patch.object(runner, "_check_docker_runtime"),
            patch.object(runner, "_check_global_config_and_network"),
            patch.object(runner, "_check_target_ssh_users"),
            patch.object(runner, "_check_project_specific"),
            patch.object(runner, "_check_absolute_disk_space"),
            patch.object(runner, "_check_host_disk_space"),
            patch.object(doctor, "run_command", return_value=""),
        ):
            return _run(runner.run)

    def test_every_check_with_pass_warn_fail_and_the_hints(self):
        code, out, err = self._doctor(
            [
                ("Python Version", "3.14", True),
                ("Docker Engine", "\x1b[31mdown\x1b[0m", False),
                ("Disk Space", "12GB reclaimable", "warn"),
            ],
            hints=[("Run '\x1b[37mldm prune\x1b[0m'", "https://doc")],
        )
        self.assertEqual(code, 1, "a failed check exits 1, as the formatted view")
        self.assertIn("noise from a check", err)
        data = json.loads(out)
        self.assertEqual(data["status"], "fail")
        self.assertEqual(data["summary"], {"pass": 1, "warn": 1, "fail": 1, "total": 3})
        self.assertEqual(
            [(c["name"], c["status"], c["subsystem"]) for c in data["checks"]],
            [
                ("Python Version", "pass", "system"),
                ("Docker Engine", "fail", "docker"),
                ("Disk Space", "warn", "docker"),
            ],
        )
        self.assertEqual(data["checks"][1]["detail"], "down")
        self.assertEqual(
            data["hints"], [{"text": "Run 'ldm prune'", "doc": "https://doc"}]
        )
        self.assertEqual(data["subsystems"]["docker"], "fail")

    def test_warnings_alone_exit_zero(self):
        code, out, _err = self._doctor([("Disk Space", "low", "warn")])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "warn")

    def test_flags_with_their_own_output_are_refused(self):
        code, out, err = self._doctor([], fix=True)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("--fix", err)


class TestShareStatusJson(unittest.TestCase):
    def _share(self, api=None, meta=None, binary=None):
        share = MagicMock()
        share.manager.dry_run = False
        share.manager.detect_project_path.return_value = Path("/p/acme")
        share.manager.read_meta.return_value = meta or {
            "share_provider": "lfr-tunnel",
            "share_subdomain": "peters",
        }
        share._get_tunnel_api_state.return_value = api or {}
        share._get_binary_path.return_value = binary or Path("/nonexistent/lfr")
        share._ensure_binary.side_effect = AssertionError("installed a client")
        return share

    def test_the_live_tunnel_gives_the_public_url(self):
        from ldm_core.handlers.share_status_json import print_share_status_json

        share = self._share(
            api={
                "state": {
                    "connected": True,
                    "subdomain": "peters",
                    "public_urls": ["https://peters.lfr-demo.se"],
                },
                "info": {"status": "healthy"},
            }
        )
        code, out, _err = _run(print_share_status_json, share, "acme")
        self.assertIsNone(code)
        data = json.loads(out)
        self.assertEqual(data["public_url"], "https://peters.lfr-demo.se")
        self.assertIs(data["active"], True)
        self.assertEqual(data["source"], "live-api")
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["project"], "acme")

    def test_no_client_answering_is_inactive_and_installs_nothing(self):
        from ldm_core.handlers.share_status_json import print_share_status_json

        share = self._share()
        _code, out, _err = _run(print_share_status_json, share, "acme")
        data = json.loads(out)
        self.assertIs(data["active"], False)
        self.assertEqual(data["status"], "inactive")
        self.assertIsNone(data["public_url"])
        share._ensure_binary.assert_not_called()

    def test_the_native_client_status_json_is_read(self):
        from ldm_core.handlers import share_status_json as mod

        with tempfile.NamedTemporaryFile() as fake_bin:
            share = self._share(binary=Path(fake_bin.name))
            done = MagicMock(
                stdout=json.dumps(
                    {
                        "status": "healthy",
                        "connection_state": "connected",
                        "public_urls": ["https://peters.lfr-demo.se"],
                        "inspector_port": 4041,
                    }
                )
            )
            with patch.object(mod.subprocess, "run", return_value=done) as run:
                _code, out, _err = _run(mod.print_share_status_json, share, "acme")
        data = json.loads(out)
        self.assertEqual(
            run.call_args.args[0][1:], ["-status-json", "-subdomain", "peters"]
        )
        self.assertEqual(data["source"], "native-client")
        self.assertEqual(data["inspector_url"], "http://localhost:4041")


if __name__ == "__main__":
    unittest.main()
