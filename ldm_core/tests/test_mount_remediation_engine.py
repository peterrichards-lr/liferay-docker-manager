"""A failed mount names the engine actually in use, or none (LDM-#2242).

On an OrbStack Mac the read-only mount refusal printed `colima stop` /
`colima start ...`. Colima was not installed, and following the advice would
have started a second runtime under the one in use.

These drive the real `verify_runtime_environment` on a simulated Mac whose
mount probe answers NO_WRITE, with `DOCKER_HOST` naming each engine's socket,
and read what reaches the terminal at default verbosity.
"""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI

HOME = "/Users/someone"
SOCKETS = {
    "colima": f"unix://{HOME}/.colima/default/docker.sock",
    "orbstack": f"unix://{HOME}/.orbstack/run/docker.sock",
    "docker-desktop": f"unix://{HOME}/.docker/run/docker.sock",
    "unknown": "tcp://10.0.0.5:2375",
}


def _refusal(docker_host):
    handler = BaseHandler(MagicMock())
    handler.verbose = False
    probe = MagicMock(stdout="NO_WRITE", returncode=0)
    out = io.StringIO()
    with (
        tempfile.TemporaryDirectory() as tmp,
        patch("platform.system", return_value="Darwin"),
        patch("platform.machine", return_value="arm64"),
        patch("ldm_core.handlers.base.subprocess.run", return_value=probe),
        patch("ldm_core.handlers.base.shutil.which", return_value="/usr/bin/docker"),
        patch.dict(os.environ, {"DOCKER_HOST": docker_host}),
        UI.patch(verbose=False, info_mode=False, quiet_mode=False),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(out),
    ):
        root = Path(tmp)
        with contextlib.suppress(SystemExit):
            handler.verify_runtime_environment({"root": root, "files": root / "files"})
    return out.getvalue()


class TheRemediationMatchesTheEngine(unittest.TestCase):
    def test_colima_keeps_its_commands(self):
        output = _refusal(SOCKETS["colima"])
        self.assertIn("VOLUME MOUNT IS READ-ONLY", output)
        self.assertIn("colima stop", output)
        self.assertIn("colima start", output)

    def test_orbstack_is_never_told_to_run_colima(self):
        output = _refusal(SOCKETS["orbstack"])
        self.assertIn("VOLUME MOUNT IS READ-ONLY", output)
        self.assertNotIn("colima", output.lower())
        self.assertIn("cannot use", output)

    def test_docker_desktop_is_pointed_at_file_sharing(self):
        output = _refusal(SOCKETS["docker-desktop"])
        self.assertNotIn("colima", output.lower())
        self.assertIn("Settings > Resources > File Sharing", output)

    def test_an_unknown_engine_is_not_named(self):
        output = _refusal(SOCKETS["unknown"])
        self.assertNotIn("colima", output.lower())
        self.assertNotIn("Docker Desktop", output)
        self.assertIn("cannot use", output)


class TheEngineIsReadFromTheRealSocket(unittest.TestCase):
    def _engine(self, docker_host):
        with patch.dict(os.environ, {"DOCKER_HOST": docker_host}):
            return BaseHandler(MagicMock()).macos_docker_engine()

    def test_each_engines_own_socket(self):
        for engine, socket in SOCKETS.items():
            with self.subTest(engine=engine):
                expected = None if engine == "unknown" else engine
                self.assertEqual(self._engine(socket), expected)

    def test_a_shared_socket_path_is_resolved_to_its_owner(self):
        # /var/run/docker.sock is a link any engine may own; read through it.
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / ".orbstack" / "run" / "docker.sock"
            real.parent.mkdir(parents=True)
            real.touch()
            link = Path(tmp) / "docker.sock"
            link.symlink_to(real)
            self.assertEqual(self._engine(f"unix://{link}"), "orbstack")

    def test_without_docker_host_the_context_endpoint_is_used(self):
        handler = BaseHandler(MagicMock())
        answers = {
            "show": "colima\n",
            "inspect": f"{SOCKETS['colima'].removeprefix('unix://')}\n",
        }

        def run(cmd, **_):
            return answers["show"] if "show" in cmd else answers["inspect"]

        env = {k: v for k, v in os.environ.items() if k != "DOCKER_HOST"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(handler, "run_command", side_effect=run),
        ):
            self.assertEqual(handler.macos_docker_engine(), "colima")


if __name__ == "__main__":
    unittest.main()
