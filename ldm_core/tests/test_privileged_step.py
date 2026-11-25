"""LDM-#2517: a sudo step LDM cannot take non-interactively is handed over.

Every non-interactive sudo call site either succeeds with `sudo -n` or exits
with the dedicated code, naming one shell-quoted command -- and, with `--json`
or LDM_JSON=1, a `{"privileged_step": ...}` line on stdout. None waits on a
password.

Each call-site test drives the real handler code with only the subprocess (and
for the upgrade, the network) faked, so the assertion is on what ships.
"""

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ldm_core import privileged
from ldm_core.ui import UI


def _capture(fn, *args, **kwargs):
    """Run ``fn``; return (exit code or None, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            fn(*args, **kwargs)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def _payload_lines(stdout):
    return [
        json.loads(line)
        for line in stdout.splitlines()
        if line.startswith('{"privileged_step"')
    ]


class _PrivilegedStateCase(unittest.TestCase):
    def setUp(self):
        privileged.reset()
        self.addCleanup(privileged.reset)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(privileged.JSON_ENV_VAR, None)
        self.addCleanup(setattr, UI, "NO_COLOR", getattr(UI, "NO_COLOR", False))
        UI.NO_COLOR = True


class TestTheContract(_PrivilegedStateCase):
    def test_the_code_is_six_and_distinct_from_the_contract_codes(self):
        self.assertEqual(privileged.PRIVILEGED_STEP_EXIT_CODE, 6)

    def test_require_exits_with_the_code_and_prints_a_runnable_command(self):
        cmd = ["sudo", "chown", "-R", "501:20", "/path with space/certs"]
        code, out, err = _capture(privileged.require, "why", cmd, resume="ldm run x")
        self.assertEqual(code, 6)
        self.assertIn(shlex.join(cmd), err)
        # Parsed back by a shell, the printed line is exactly the argv.
        printed = next(line for line in err.splitlines() if "sudo chown" in line)
        self.assertEqual(shlex.split(printed.strip()), cmd)
        self.assertIn("ldm run x", err)
        self.assertEqual(out, "", "no JSON without --json/LDM_JSON")

    def test_json_mode_prints_the_documented_payload_on_stdout(self):
        privileged.set_json_mode(True)
        code, out, _err = _capture(
            privileged.require, "why", ["sudo", "true"], resume=None
        )
        self.assertEqual(code, 6)
        self.assertEqual(
            _payload_lines(out),
            [
                {
                    "privileged_step": {
                        "reason": "why",
                        "command": ["sudo", "true"],
                        "resume": None,
                    }
                }
            ],
        )

    def test_the_environment_variable_turns_json_on(self):
        os.environ[privileged.JSON_ENV_VAR] = "1"
        _code, out, _err = _capture(privileged.require, "why", ["sudo", "true"])
        self.assertEqual(len(_payload_lines(out)), 1)

    def test_a_shell_string_is_refused(self):
        with self.assertRaises(TypeError):
            privileged.build_payload("why", "sudo rm -rf /")

    def test_defer_does_not_exit(self):
        _code, _out, err = _capture(privileged.defer, "why", ["sudo", "true"])
        self.assertIsNone(_code)
        self.assertTrue(privileged.has_deferred())
        self.assertIn("sudo true", err)


class TestTheDispatcherReportsADeferredStep(_PrivilegedStateCase):
    """`cli._dispatch_command` turns a successful finish into exit 6."""

    def _dispatch(self, handler):
        from ldm_core.cli import _dispatch_command

        args = SimpleNamespace(benchmark=False)
        return _capture(_dispatch_command, args, ("x", None), {("x", None): handler})

    def test_a_handler_that_returns_normally(self):
        def handler():
            privileged.defer("why", ["sudo", "true"])

        code, _out, _err = self._dispatch(handler)
        self.assertEqual(code, 6)

    def test_a_handler_that_exits_zero(self):
        def handler():
            privileged.defer("why", ["sudo", "true"])
            sys.exit(0)

        code, _out, _err = self._dispatch(handler)
        self.assertEqual(code, 6)

    def test_a_real_failure_keeps_its_own_code(self):
        def handler():
            privileged.defer("why", ["sudo", "true"])
            sys.exit(3)

        code, _out, _err = self._dispatch(handler)
        self.assertEqual(code, 3)

    def test_nothing_deferred_is_still_success(self):
        code, _out, _err = self._dispatch(lambda: None)
        self.assertIsNone(code)


class TestHostsEntries(_PrivilegedStateCase):
    def setUp(self):
        super().setUp()
        from ldm_core.tests.test_base import MockBaseManager

        self.handler = MockBaseManager()
        self.handler.non_interactive = True

    def _fail_sudo(self):
        return (
            patch("ldm_core.handlers.base.platform.system", return_value="Darwin"),
            patch.object(self.handler, "is_wsl", return_value=False),
            patch(
                "ldm_core.handlers.base.subprocess.Popen",
                return_value=MagicMock(returncode=1),
            ),
        )

    def test_adding_entries_hands_over_the_command_and_exits_six(self):
        privileged.set_json_mode(True)
        a, b, c = self._fail_sudo()
        with a, b, c as popen:
            code, out, _err = _capture(
                self.handler._apply_hosts_fix,
                ["acme.demo"],
                project="acme",
                resume="ldm run acme",
            )
        self.assertEqual(code, 6)
        # It only ever tried the non-prompting form.
        self.assertEqual(popen.call_args.args[0][:2], ["sudo", "-n"])
        (payload,) = _payload_lines(out)
        step = payload["privileged_step"]
        self.assertEqual(step["command"][:3], ["sudo", "sh", "-c"])
        self.assertEqual(step["resume"], "ldm run acme")
        self.assertIn("acme.demo", step["reason"])

    def test_the_handed_over_command_appends_the_entries_when_run(self):
        """Run the command for real, minus `sudo`, against a scratch file."""
        with tempfile.TemporaryDirectory() as tmp:
            hosts = Path(tmp) / "hosts"
            hosts.write_text("127.0.0.1 localhost\n")
            content = (
                "\n# Added by Liferay Docker Manager (LDM)\n"
                "127.0.0.1 acme.demo # [LDM]\n"
                "127.0.0.1 it's.demo # [LDM]\n"
            )
            cmd = self.handler._hosts_append_command(content, str(hosts))
            self.assertEqual(cmd[0], "sudo")
            # Through a shell, exactly as a person pasting the printed line.
            subprocess.run(shlex.join(cmd[1:]), shell=True, check=True)  # nosec B602
            self.assertEqual(
                hosts.read_text(),
                "127.0.0.1 localhost\n"
                "# Added by Liferay Docker Manager (LDM)\n"
                "127.0.0.1 acme.demo # [LDM]\n"
                "127.0.0.1 it's.demo # [LDM]\n",
            )

    def test_a_missing_hostname_during_run_resumes_with_ldm_run(self):
        """The call site in ensure_hostnames_resolve passes the resume."""
        with (
            patch.object(self.handler, "check_hostname", return_value=False),
            patch.object(self.handler, "setup_paths", create=True) as paths,
            patch.object(
                self.handler, "_apply_hosts_fix", return_value=True
            ) as apply_fix,
            patch("time.sleep"),
        ):
            paths.return_value = {"cx": Path("/nonexistent/cx")}
            _capture(
                self.handler.ensure_hostnames_resolve,
                Path("/tmp/acme"),
                "acme.demo",
                project_id="acme",
            )
        self.assertEqual(apply_fix.call_args.kwargs["resume"], "ldm run acme")

    def test_removing_entries_defers_instead_of_stopping_a_teardown(self):
        with (
            patch("ldm_core.handlers.base.platform.system", return_value="Linux"),
            patch.object(self.handler, "is_wsl", return_value=False),
            patch(
                "ldm_core.handlers.base.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "sudo"),
            ) as run,
        ):
            code, _out, err = _capture(
                self.handler._remove_hosts_entries, hostnames=["acme.demo"]
            )
        self.assertIsNone(code, "cleanup must not exit mid-teardown")
        self.assertEqual(run.call_args.args[0][:2], ["sudo", "-n"])
        self.assertTrue(privileged.has_deferred())
        printed = next(line for line in err.splitlines() if "sed" in line)
        argv = shlex.split(printed.strip())
        self.assertEqual(argv[0], "sudo")
        self.assertNotIn("-n", argv[:2])
        self.assertEqual(argv[-1], "/etc/hosts")


class TestCertificateFolderPermissions(_PrivilegedStateCase):
    def test_non_interactive_never_runs_a_prompting_sudo(self):
        from ldm_core.handlers.infra import InfraService

        manager = MagicMock()
        manager.non_interactive = True
        service = InfraService(manager)
        with (
            patch("subprocess.run", return_value=MagicMock(returncode=1)) as run,
            patch.object(UI, "confirm", side_effect=AssertionError("prompted")),
        ):
            code, _out, err = _capture(
                service._fix_cert_permissions, Path("/root/certs")
            )
        self.assertIsNone(code)
        self.assertEqual(run.call_args.args[0][:3], ["sudo", "-n", "chown"])
        manager.run_command.assert_not_called()
        self.assertTrue(privileged.has_deferred())
        self.assertIn("sudo chown -R", err)


class TestUpgradeSudoFallback(_PrivilegedStateCase):
    """`ldm system upgrade -y` into a root-owned directory."""

    LATEST = "9.9.9"

    def test_it_exits_six_with_the_install_command(self):
        import hashlib

        from ldm_core.diagnostics import upgrade as up

        privileged.set_json_mode(True)
        body = b"new-binary"
        url = "https://example.invalid/ldm-linux"
        digest = hashlib.sha256(body).hexdigest()

        def fake_get(target, **_kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.iter_content = MagicMock(return_value=[body])
            resp.text = f"{digest}  ldm-linux\n"
            return resp

        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "bin" / "ldm"
            exe.parent.mkdir()
            exe.write_bytes(b"old")

            real_mkstemp = tempfile.mkstemp

            def mkstemp(prefix=""):
                return real_mkstemp(prefix=prefix, dir=tmp)

            handler = MagicMock()
            handler.manager.args = SimpleNamespace(
                pre_release=False,
                check_only=False,
                version=None,
                repair=False,
                non_interactive=True,
                force=False,
            )
            handler.manager.non_interactive = True
            with (
                patch.object(up, "VERSION", "1.0.0"),
                patch.object(up, "check_for_updates", return_value=(self.LATEST, url)),
                patch.object(up.tempfile, "mkstemp", side_effect=mkstemp),
                patch.object(up.sys, "argv", [str(exe)]),
                patch.object(up.platform, "system", return_value="Linux"),
                patch.object(up, "_replace_binary", side_effect=PermissionError),
                patch("requests.get", side_effect=fake_get),
                patch.object(
                    up.subprocess,
                    "run",
                    side_effect=subprocess.CalledProcessError(1, "sudo"),
                ) as run,
                patch.object(up.os, "system", side_effect=AssertionError("prompted")),
            ):
                code, out, _err = _capture(up.run_upgrade, handler)

            self.assertEqual(code, 6)
            self.assertTrue(
                any(c.args[0][:2] == ["sudo", "-n"] for c in run.call_args_list)
            )
            (payload,) = _payload_lines(out)
            command = payload["privileged_step"]["command"]
            self.assertEqual(command[:3], ["sudo", "sh", "-c"])
            script = shlex.split(command[3])
            self.assertEqual(script[0], "install")
            self.assertEqual(script[script.index("&&") - 1], str(exe.resolve()))
            self.assertEqual(script[script.index("&&") + 1 : -1], ["rm", "-f"])


if __name__ == "__main__":
    unittest.main()
