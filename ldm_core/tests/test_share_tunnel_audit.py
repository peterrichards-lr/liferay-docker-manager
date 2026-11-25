"""lfr-tunnel <-> LDM integration audit (LDM-#2502 .. #2507).

Each class drives the entry point a user reaches -- `cmd_start`, or the
composer that writes the sidecar -- and asserts what crosses the boundary: the
argv and environment the client gets, the `.env` and compose environment the
sidecar gets, the files LDM writes in the user's home. Nothing here executes a
binary; `subprocess.run` is recorded, never run.
"""

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from ldm_core.constants import LFR_TUNNEL_IMAGE, LFR_TUNNEL_IMAGE_LATEST
from ldm_core.handlers import share as share_module
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.share import ShareService

# The module, not the class: a TestCase imported by name is collected again here.
from ldm_core.tests import test_share


def _completed(returncode=0, stdout="", stderr=""):
    res = MagicMock()
    res.returncode = returncode
    res.stdout = stdout
    res.stderr = stderr
    return res


class _NativeStart(unittest.TestCase):
    """Borrows TestShareTunnelTopology's fixture: a native start with the
    binary, version check, token and health poll stubbed."""

    service: Any
    mock_manager: Any

    def _run_start(self, mock_run, **kwargs):
        """Starts a native tunnel and returns the (argv, env) the client got."""
        mock_run.return_value = _completed()
        self.service.cmd_start(**kwargs)
        return mock_run.call_args[0][0], mock_run.call_args[1]["env"]

    def setUp(self):
        test_share.TestShareTunnelTopology.setUp(self)  # type: ignore[arg-type]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        home_patch = patch(
            "ldm_core.handlers.share.get_actual_home", return_value=self.home
        )
        home_patch.start()
        self.addCleanup(home_patch.stop)


# --- LDM-#2502: the client log moved ----------------------------------------


class TheFailureOutputReadsTheCurrentClientLog(_NativeStart):
    def _write(self, relative, text):
        path = self.home / ".lfr-tunnel" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    @patch("builtins.print")
    @patch("ldm_core.handlers.share.UI")
    @patch("subprocess.run")
    def test_a_log_at_the_new_path_only_is_shown(self, mock_run, mock_ui, mock_print):
        log = self._write("logs/client-demo.log", "current-run: gateway said no")
        mock_run.return_value = _completed(returncode=1, stderr="exit status 1")

        self.service.cmd_start(subdomain="demo", ports="8080")

        printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
        self.assertIn("current-run: gateway said no", printed)
        infos = " ".join(str(c.args[0]) for c in mock_ui.info.call_args_list)
        self.assertIn(str(log), infos)
        self.assertIn("last modified", infos)

    @patch("builtins.print")
    @patch("ldm_core.handlers.share.UI")
    @patch("subprocess.run")
    def test_the_new_path_wins_over_a_stale_legacy_log(
        self, mock_run, mock_ui, mock_print
    ):
        self._write("client-demo.log", "16 July: something long ago")
        self._write("logs/client-demo.log", "today: the real failure")
        mock_run.return_value = _completed(returncode=1)

        self.service.cmd_start(subdomain="demo", ports="8080")

        printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
        self.assertIn("today: the real failure", printed)
        self.assertNotIn("16 July", printed)

    @patch("builtins.print")
    @patch("ldm_core.handlers.share.UI")
    @patch("subprocess.run")
    def test_an_older_clients_legacy_log_is_still_read(
        self, mock_run, mock_ui, mock_print
    ):
        self._write("client-demo.log", "old client output")
        mock_run.return_value = _completed(returncode=1)

        self.service.cmd_start(subdomain="demo", ports="8080")

        printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
        self.assertIn("old client output", printed)

    @patch("builtins.print")
    @patch("ldm_core.handlers.share.UI")
    @patch("subprocess.run")
    def test_a_log_path_the_client_announced_is_followed(
        self, mock_run, mock_ui, mock_print
    ):
        """If the client moves its log again, LDM reads where it says."""
        moved = self.home / "custom-logs" / "client-demo.log"
        moved.parent.mkdir(parents=True)
        moved.write_text("from the announced path", encoding="utf-8")
        self._write("logs/client-demo.log", "from the standard path")
        mock_run.return_value = _completed(
            returncode=1, stdout=f"INFO [Client] Logs: {moved}\n"
        )

        self.service.cmd_start(subdomain="demo", ports="8080")

        printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
        self.assertIn("from the announced path", printed)
        self.assertNotIn("from the standard path", printed)

    @patch("subprocess.run")
    def test_the_announced_log_path_reaches_the_health_check(self, mock_run):
        announced = self.home / "elsewhere" / "client-demo.log"
        mock_run.return_value = _completed(
            stderr=f"2026/11/24 10:00:00 INFO [Client] Logs: {announced}\n"
        )

        self.service.cmd_start(subdomain="demo", ports="8080")

        self.assertEqual(
            self.service._poll_tunnel_health.call_args.kwargs["log_path"], announced
        )

    def test_the_announced_line_parses_through_slogs_text_handler(self):
        self.assertEqual(
            ShareService.client_log_path_from_output(
                "", 'time=x level=INFO msg="[Client] Logs: /h/.lfr-tunnel/x.log\\n"'
            ),
            Path("/h/.lfr-tunnel/x.log"),
        )
        self.assertIsNone(ShareService.client_log_path_from_output("nothing", None))

    @patch("requests.get", side_effect=Exception("Connection refused"))
    @patch("subprocess.run")
    def test_the_health_check_diagnoses_from_the_new_path(self, mock_run, _get):
        """The 'already taken' detection could never fire for a native share,
        because the file it read was never written."""
        self._write("logs/client-demo.log", "[Error] subdomain already taken")
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )
        mock_run.return_value = _completed(returncode=1)

        ok, reason = self.service._poll_tunnel_health("demo", timeout=0)

        self.assertFalse(ok)
        self.assertIn("Subdomain Conflict", reason)


# --- LDM-#2503: a custom domain is requested per project --------------------
#
# v2.26.16-pre.1 passed a global vanity `share_domain` as `-domain` and broke
# every such share. A custom domain is now requested only for the project that
# names it; test_share_custom_domain.py covers that in full.


class AVanityDomainFromLdmrcIsNotRequested(_NativeStart):
    @patch.dict(os.environ, {}, clear=True)
    @patch("subprocess.run")
    def test_native_does_not_pass_domain_for_a_global_vanity_domain(self, mock_run):
        self.mock_manager.config.get_global_config = lambda: {  # type: ignore[method-assign]
            "share_domain": "dev.solaramoto.com"
        }
        with patch("ldm_core.handlers.share.UI"):
            cmd, _env = self._run_start(mock_run, subdomain="demo", ports="8080")
        self.assertNotIn("-domain", cmd)

    @patch.dict(os.environ, {}, clear=True)
    @patch("subprocess.run")
    def test_native_does_not_pass_domain_for_a_gateway_domain(self, mock_run):
        cmd, _env = self._run_start(
            mock_run, subdomain="demo", ports="8080", domain="lfr-demo.se"
        )
        self.assertNotIn("-domain", cmd)


# --- LDM-#2507: lifecycle hooks are wired on the first share -----------------


class TheFirstShareRegistersTheHooks(_NativeStart):
    def setUp(self):
        super().setUp()
        self.config, self.marker = share_module.lfr_tunnel_hook_paths()

    def _loaded(self):
        import yaml

        return yaml.safe_load(self.config.read_text(encoding="utf-8"))

    def _infos(self, mock_ui):
        return [str(c.args[0]) for c in mock_ui.info.call_args_list]

    @patch("ldm_core.handlers.share.UI")
    @patch("subprocess.run")
    def test_first_start_registers_and_says_so_once(self, mock_run, mock_ui):
        self._run_start(mock_run, subdomain="demo", ports="8080")
        self.assertEqual(
            self._loaded()["hooks"],
            dict.fromkeys(share_module.LDM_TUNNEL_HOOK_EVENTS, "ldm tunnel-event"),
        )
        said = [m for m in self._infos(mock_ui) if "lifecycle hooks" in m]
        self.assertEqual(len(said), 1)

        mock_ui.reset_mock()
        self._run_start(mock_run, subdomain="demo", ports="8080")
        self.assertFalse([m for m in self._infos(mock_ui) if "lifecycle hooks" in m])

    @patch("ldm_core.handlers.share.UI")
    @patch("subprocess.run")
    def test_hooks_the_user_set_are_never_overwritten(self, mock_run, mock_ui):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(
            'theme: dark\nhooks:\n  stopping: "notify-send bye"\n', encoding="utf-8"
        )

        self._run_start(mock_run, subdomain="demo", ports="8080")

        data = self._loaded()
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["hooks"]["stopping"], "notify-send bye")
        self.assertEqual(data["hooks"]["started"], "ldm tunnel-event")
        said = " ".join(self._infos(mock_ui))
        self.assertIn("stopping were left unchanged", said)

    @patch("ldm_core.handlers.share.UI")
    @patch("subprocess.run")
    def test_a_config_without_hooks_keeps_its_comments(self, mock_run, mock_ui):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text("# mine\nbypass_proxy: true\n", encoding="utf-8")

        self._run_start(mock_run, subdomain="demo", ports="8080")

        text = self.config.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# mine\nbypass_proxy: true\n"))
        self.assertEqual(
            self._loaded()["hooks"]["warning_received"], "ldm tunnel-event"
        )

    @patch("ldm_core.handlers.share.UI")
    @patch("subprocess.run")
    def test_an_unreadable_config_is_left_alone(self, mock_run, mock_ui):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        broken = "hooks: [unclosed\n"
        self.config.write_text(broken, encoding="utf-8")

        self._run_start(mock_run, subdomain="demo", ports="8080")

        self.assertEqual(self.config.read_text(encoding="utf-8"), broken)

    @patch("ldm_core.handlers.share.UI")
    @patch("subprocess.run")
    def test_hooks_the_user_removed_stay_removed(self, mock_run, mock_ui):
        self._run_start(mock_run, subdomain="demo", ports="8080")
        self.config.write_text("theme: light\n", encoding="utf-8")

        self._run_start(mock_run, subdomain="demo", ports="8080")

        self.assertEqual(self.config.read_text(encoding="utf-8"), "theme: light\n")

    @patch("subprocess.run")
    def test_a_dry_run_registers_nothing(self, mock_run):
        self.mock_manager.dry_run = True
        self.service.cmd_start(subdomain="demo", ports="8080")
        self.assertFalse(self.config.exists())
        self.assertFalse(self.marker.exists())


# --- LDM-#2505 / #2503 / #2506: the Docker sidecar ---------------------------


class _SidecarManager:
    """Real ShareService behind a real ComposerService; only the edges stubbed."""

    def __init__(self, project_dir):
        self.project_dir = project_dir
        self.args = MagicMock()
        self.args.share = True
        self.args.share_provider = "lfr-tunnel-docker"
        self.args.share_subdomain = "demo"
        self.args.share_image = None
        self.args.share_inspector = False
        self.args.share_domain = None
        self.args.domain = None
        self.config = MagicMock()
        self.config.get_global_config.return_value = {}
        self.non_interactive = True
        self.verbose = False
        self.share = ShareService(self)
        self.share._get_auth_token = MagicMock(return_value="tok-SECRET")  # type: ignore[method-assign]
        self.share._advertised_base_domains = MagicMock(return_value=[])  # type: ignore[method-assign]

    def detect_project_path(self, _project_id=None):
        return self.project_dir

    def write_meta(self, _root, _meta):
        pass


class TheSidecarIsNotPinnedAndKeepsNoToken(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = _SidecarManager(self.root)
        self.composer = ComposerService(self.manager)
        self.env_file = self.root / ".env"

    def _build(self, meta=None):
        meta = {"share": "true", "share_provider": "lfr-tunnel-docker", **(meta or {})}
        with patch("ldm_core.handlers.composer.UI") as ui:
            svc = self.composer._build_lfr_tunnel_service(
                {"root": self.root}, meta, "proj"
            )
        return svc, ui

    @patch.dict(os.environ, {}, clear=True)
    def test_a_default_share_writes_no_token_to_env_and_still_names_a_gateway(self):
        svc, _ui = self._build({"share_domain": "lfr-demo.se"})

        env_text = self.env_file.read_text()
        self.assertNotIn("LFT_CLIENT_TOKEN", env_text)
        self.assertNotIn("LFT_SERVER_URL", env_text)
        self.assertNotIn("tok-SECRET", env_text)
        self.assertNotIn("tok-SECRET", " ".join(svc["environment"]))
        # The published image has no built-in gateway: without a server
        # variable the sidecar exits at startup, so a default share still
        # names the domain's gateway (in the compose file, not in .env).
        self.assertIn(
            "LFT_CLIENT_SERVER=https://tunnel.lfr-demo.se", svc["environment"]
        )
        self.assertIn("LFT_CLIENT_TOKEN", svc["environment"])
        # ...and compose gets it from this process's environment.
        self.assertEqual(os.environ.get("LFT_CLIENT_TOKEN"), "tok-SECRET")

    @patch.dict(os.environ, {}, clear=True)
    def test_a_previously_written_token_is_removed_and_announced(self):
        self.env_file.write_text(
            "OTHER=1\nLFT_CLIENT_TOKEN=tok-OLD\nLFT_SERVER_URL=https://tunnel.lfr-demo.se\n"
        )

        _svc, ui = self._build()

        env_text = self.env_file.read_text()
        self.assertNotIn("tok-OLD", env_text)
        self.assertNotIn("LFT_SERVER_URL", env_text)
        self.assertIn("OTHER=1", env_text)
        said = " ".join(str(c.args[0]) for c in ui.info.call_args_list)
        self.assertIn("Removed the plain-text LFT_CLIENT_TOKEN", said)

        ui.reset_mock()
        with patch("ldm_core.handlers.composer.UI") as again:
            self.composer._build_lfr_tunnel_service(
                {"root": self.root}, {"share": "true"}, "proj"
            )
        self.assertFalse(again.info.called)

    @patch.dict(os.environ, {}, clear=True)
    def test_an_explicitly_named_gateway_is_pinned(self):
        self.manager.args.share_domain = "lfr-demo.se"
        with patch("ldm_core.handlers.share.UI"):
            svc, _ui = self._build()
        self.assertIn(
            "LFT_CLIENT_SERVER=https://tunnel.lfr-demo.se", svc["environment"]
        )

    @patch.dict(os.environ, {"LFT_SERVER_URL": "https://tunnel.example"}, clear=True)
    def test_a_gateway_variable_in_the_environment_is_honoured(self):
        with patch("ldm_core.handlers.share.UI"):
            svc, _ui = self._build()
        self.assertIn("LFT_CLIENT_SERVER=https://tunnel.example", svc["environment"])

    @patch.dict(os.environ, {}, clear=True)
    def test_a_vanity_domain_is_not_requested_from_the_sidecar(self):
        svc, _ui = self._build({"share_domain": "dev.solaramoto.com"})
        self.assertFalse(
            [e for e in svc["environment"] if e.startswith("LFT_CLIENT_CUSTOM_DOMAIN")]
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_the_default_image_is_the_published_one(self):
        svc, _ui = self._build()
        self.assertEqual(svc["image"], LFR_TUNNEL_IMAGE_LATEST)


class ShareStartDockerHandsTheTokenToCompose(unittest.TestCase):
    """`ldm share start --provider lfr-tunnel-docker` end to end, compose recorded."""

    def setUp(self):
        test_share.TestShareService.setUp(self)  # type: ignore[arg-type]
        self.service._get_auth_token = MagicMock(return_value="tok-SECRET")  # type: ignore[method-assign]
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.service._get_docker_installed_version = MagicMock(return_value=None)  # type: ignore[method-assign]
        self.service.resolve_public_tunnel_url = MagicMock(  # type: ignore[method-assign]
            return_value="https://demo.lfr-demo.online"
        )
        self.service._sync_gui_state = MagicMock()  # type: ignore[method-assign]

    service: Any
    mock_manager: Any

    @patch.dict(os.environ, {}, clear=True)
    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker-compose"])
    @patch("subprocess.run")
    def test_compose_up_gets_the_token_and_the_default_image(self, mock_run, _c):
        mock_run.return_value = _completed()

        self.service.cmd_start(
            project_id="myproj", subdomain="demo", provider="lfr-tunnel-docker"
        )

        (up,) = [
            c
            for c in mock_run.call_args_list
            if c.args[0] == ["docker-compose", "up", "-d", "lfr-tunnel"]
        ]
        self.assertEqual(up.kwargs["env"]["LFT_CLIENT_TOKEN"], "tok-SECRET")
        # LDM-#2506: the version check ran against the published image.
        self.service._get_docker_installed_version.assert_called_once_with(
            LFR_TUNNEL_IMAGE_LATEST, None
        )
        self.assertTrue(LFR_TUNNEL_IMAGE_LATEST.startswith(LFR_TUNNEL_IMAGE))
        self.assertEqual(LFR_TUNNEL_IMAGE, "peterjrichards/lfr-tunnel")


if __name__ == "__main__":
    unittest.main()
