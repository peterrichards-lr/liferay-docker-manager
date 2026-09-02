"""Failures that used to print and carry on must now exit non-zero (LDM-#1548).

`UI.error` writes to stderr and RETURNS -- only `UI.die` calls `sys.exit`
(`ldm_core/ui.py:517`). Four sites in the audit reported a real failure with
`UI.error` and then either fell off the end of the handler or returned into a
caller that ignored the result, so the command exited 0 having done nothing.

Every test here asserts the *outcome* -- the SystemExit code, or the state of
the compose dict that was produced -- never that a particular string appears
in the source. Neutering any one of the fixes back to `UI.error` (keeping the
symbol and signature) fails the corresponding test.

Exit codes follow .agents/skills/ldm-architecture/SKILL.md:
0 success, 1 validation, 2 auth, 3 infrastructure/data, 4 orchestration.
"""

import argparse
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.node import NodeService
from ldm_core.handlers.share import ShareService
from ldm_core.snapshot.database import DatabaseSnapshotService

# --------------------------------------------------------------------------
# Finding 2: `ldm snapshot restore` could exit 0 with the dump never applied.
# --------------------------------------------------------------------------


class _RestoreManager:
    """Minimal manager for DatabaseSnapshotService._restore_database."""

    def __init__(self, *, container_status="running", ps_result=""):
        self.args = MagicMock()
        self.target = None
        self.defaults = {}
        self.infra = MagicMock()
        self.runtime = MagicMock()
        self._status = container_status
        self._ps_result = ps_result
        self.run_command = MagicMock(side_effect=self._probe)

    def _probe(self, *_args, **_kwargs):
        return self._ps_result

    def get_container_status(self, name, *args, **kwargs):
        return self._status


class _Facade:
    def __init__(self, manager):
        self.manager = manager


class TestRestoreDatabaseFailsLoudly(unittest.TestCase):
    """`handlers/snapshot.py:326` ignores what `_restore_database` returns.

    So a bare `return` on a failure path meant the restore printed
    "Restore complete." and exited 0 with the database untouched. The sibling
    failure inside `_execute_orchestrated_db_restore` already exits 3.
    """

    def _run_restore(self, manager, *, db_mode, project_meta):
        with TemporaryDirectory() as tmp:
            choice = Path(tmp) / "snap"
            choice.mkdir()
            # A real dump exists -- so there IS something to restore, and
            # "nothing happened" cannot be excused as "nothing to do".
            (choice / "database.sql").write_text("SELECT 1;", encoding="utf-8")
            root = Path(tmp) / "proj"
            root.mkdir()

            svc = DatabaseSnapshotService(_Facade(manager))
            with (
                patch(
                    "ldm_core.utils.resolve_infrastructure_mode",
                    return_value=db_mode,
                ),
                patch(
                    "ldm_core.utils.shared_database_container",
                    return_value="ldm-global-mysql",
                ),
                patch(
                    "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
                    return_value=["docker"],
                ),
                patch(
                    "ldm_core.docker_service.DockerService.get_compose_cmd_prefix",
                    return_value=None,
                ),
                patch("time.sleep"),
            ):
                svc._restore_database({"root": root}, choice, project_meta, "myproj")

    def test_a_shared_database_that_exited_fails_the_restore(self):
        manager = _RestoreManager(container_status="exited", ps_result="abc123")
        with self.assertRaises(SystemExit) as ctx:
            self._run_restore(
                manager,
                db_mode="shared",
                project_meta={"db_type": "mysql"},
            )
        self.assertEqual(
            ctx.exception.code,
            3,
            "a shared database that died mid-restore must be an "
            "Infrastructure/Data error, not a silent success (LDM-#1548)",
        )

    def test_no_database_container_at_all_fails_the_restore(self):
        # Nothing matches the `docker ps` probes and compose is unavailable,
        # so the restore has a dump and nowhere to put it.
        manager = _RestoreManager(container_status="running", ps_result="")
        with self.assertRaises(SystemExit) as ctx:
            # LDM-#1511: "local" was never a mode argparse accepts, and the
            # mode axis is now validated. The point of this case is "LDM owns
            # a container and cannot find it", which "isolated" expresses.
            self._run_restore(
                manager,
                db_mode="isolated",
                project_meta={"db_type": "mysql"},
            )
        self.assertEqual(
            ctx.exception.code,
            3,
            "a restore with no target container must not report success (LDM-#1548)",
        )

    def test_hypersonic_still_succeeds(self):
        # The guard must not turn the file-based engine, which has no
        # container by design, into a failure.
        manager = _RestoreManager()
        # LDM-#1511: Hypersonic's mode is `embedded` and is derived from the
        # engine, so whatever is stored here is normalised away. Spelled
        # correctly rather than left as the non-existent "local".
        self._run_restore(
            manager,
            db_mode="embedded",
            project_meta={"db_type": "hypersonic"},
        )


# --------------------------------------------------------------------------
# Finding 5: `ldm share` exited 0 with no tunnel, meta already written.
# --------------------------------------------------------------------------


class _ShareConfig:
    def get_global_config(self):
        return {}

    def get_ngrok_auth_token(self):
        return "ngrok-token"


class _ShareManager:
    def __init__(self, root, meta):
        self.non_interactive = True
        self.verbose = False
        self.dry_run = False
        self.config = _ShareConfig()
        self.args = MagicMock()
        self.runtime = MagicMock()
        self.workspace = MagicMock()
        self._root = root
        self._meta = meta
        self.written = []

    def detect_project_path(self, project_id=None):
        return self._root

    def read_meta(self, root):
        return dict(self._meta)

    def write_meta(self, root, meta):
        self.written.append(dict(meta))

    def setup_paths(self, root):
        return {"root": Path(root)}


class _ShareCase(unittest.TestCase):
    def _service(self, meta=None):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.manager = _ShareManager(root, meta or {})
        svc = ShareService(self.manager)
        svc._get_auth_token = MagicMock(  # type: ignore[method-assign]
            return_value="tunnel-token"
        )
        svc._resolve_existing_binary = MagicMock(  # type: ignore[method-assign]
            return_value=None
        )
        svc._get_tunnel_api_state = MagicMock(  # type: ignore[method-assign]
            return_value={}
        )
        svc._poll_tunnel_health = MagicMock(  # type: ignore[method-assign]
            return_value=(True, None)
        )
        svc._get_docker_installed_version = MagicMock(  # type: ignore[method-assign]
            return_value="1.0.0"
        )
        svc._verify_compatibility = MagicMock()  # type: ignore[method-assign]
        svc._sync_gui_state = MagicMock()  # type: ignore[method-assign]
        return svc


class TestShareStartFailsLoudly(_ShareCase):
    """`project_meta["share"]` is persisted *before* the tunnel is booted.

    Exiting 0 after the boot failed left the meta claiming an active tunnel
    that does not exist -- persisted state and reality diverge.
    """

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker", "compose"])
    @patch("subprocess.run")
    def test_a_tunnel_container_that_will_not_start_exits_3(self, mock_run, _compose):
        svc = self._service()
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="port is already allocated"
        )
        with self.assertRaises(SystemExit) as ctx:
            svc.cmd_start(
                project_id="myproj",
                subdomain="demo",
                ports="8080",
                provider="lfr-tunnel-docker",
            )
        self.assertEqual(ctx.exception.code, 3)
        # The meta really was written before the boot -- which is why the
        # exit code is the only thing standing between the user and a
        # project that believes it is shared.
        self.assertTrue(
            any(m.get("share") == "true" for m in self.manager.written),
            "precondition: share=true is persisted ahead of the boot",
        )

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker", "compose"])
    @patch("subprocess.run")
    def test_an_ngrok_container_that_will_not_start_exits_3(self, mock_run, _compose):
        svc = self._service()
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="boom"
        )
        with self.assertRaises(SystemExit) as ctx:
            svc.cmd_start(project_id="myproj", ports="8080", provider="ngrok")
        self.assertEqual(ctx.exception.code, 3)

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch("subprocess.run")
    def test_the_native_binary_failing_to_start_exits_3(self, mock_run, mock_home):
        # The audit missed this one; it is the same defect in the same
        # function, on the default (native) provider.
        svc = self._service()
        svc._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )
        svc._get_installed_version = MagicMock(  # type: ignore[method-assign]
            return_value="1.0.0"
        )
        mock_home.return_value = Path("/nonexistent-home")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="crash"
        )
        with self.assertRaises(SystemExit) as ctx:
            svc.cmd_start(project_id="myproj", subdomain="demo", ports="8080")
        self.assertEqual(ctx.exception.code, 3)

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker", "compose"])
    @patch("subprocess.run")
    def test_a_tunnel_that_starts_cleanly_still_exits_0(self, mock_run, _compose):
        svc = self._service()
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        svc.cmd_start(
            project_id="myproj",
            subdomain="demo",
            ports="8080",
            provider="lfr-tunnel-docker",
        )


class TestShareStopFailsLoudly(_ShareCase):
    """`share=false` is persisted before the container is removed.

    If the removal fails and LDM exits 0, the project is still publicly
    exposed while its metadata says it is not.
    """

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker", "compose"])
    @patch("subprocess.run")
    def test_a_tunnel_container_that_will_not_stop_exits_3(self, mock_run, _compose):
        svc = self._service({"share_provider": "lfr-tunnel-docker"})
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="no such container"
        )
        with self.assertRaises(SystemExit) as ctx:
            svc.cmd_stop(project_id="myproj")
        self.assertEqual(ctx.exception.code, 3)

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker", "compose"])
    @patch("subprocess.run")
    def test_an_ngrok_container_that_will_not_stop_exits_3(self, mock_run, _compose):
        svc = self._service({"share_provider": "ngrok"})
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="no such container"
        )
        with self.assertRaises(SystemExit) as ctx:
            svc.cmd_stop(project_id="myproj")
        self.assertEqual(ctx.exception.code, 3)

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker", "compose"])
    @patch("subprocess.run")
    def test_a_clean_stop_still_exits_0(self, mock_run, _compose):
        svc = self._service({"share_provider": "lfr-tunnel-docker"})
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        svc.cmd_stop(project_id="myproj")


# --------------------------------------------------------------------------
# Finding 6: the dispatcher discarded the exit code handlers returned.
# --------------------------------------------------------------------------


def _dispatch(handler):
    """Runs cli._execute_command with a single registered handler.

    `command="system", subcommand="upgrade"` suppresses the background
    update-check thread, so this touches no network.
    """
    from ldm_core.cli import _execute_command

    args = argparse.Namespace(command="system", subcommand="upgrade")
    _execute_command(args, ("system", "upgrade"), {("system", "upgrade"): handler})


class TestDispatcherPropagatesReturnedExitCodes(unittest.TestCase):
    """`cmds[current_cmd]()` threw the result away (cli.py:3205).

    `handlers/node.py:_run_node_script` is the one place in handlers/ that
    signals failure by returning an exit code, and its caller ignored it --
    so `ldm node wake <n>` exited 0 when the script failed.
    """

    def test_a_returned_non_zero_becomes_the_process_exit_code(self):
        with self.assertRaises(SystemExit) as ctx:
            _dispatch(lambda: 3)
        self.assertEqual(ctx.exception.code, 3)

    def test_a_returned_zero_is_not_an_exit(self):
        _dispatch(lambda: 0)

    def test_a_handler_that_returns_nothing_is_not_an_exit(self):
        _dispatch(lambda: None)

    def test_a_returned_false_is_not_read_as_an_exit_code(self):
        # bool IS an int in Python, and the ("run", None) entry returns
        # Pipeline.run()'s bool. Reading False as 0 or True as 1 here would
        # change the exit code of every `ldm run`.
        _dispatch(lambda: False)

    def test_a_returned_true_is_not_read_as_an_exit_code(self):
        _dispatch(lambda: True)

    def test_a_non_int_return_is_ignored(self):
        # cmd_snapshots returns a list; cmd_fork returns a path.
        _dispatch(lambda: ["snapshot-a", "snapshot-b"])
        _dispatch(lambda: Path("/some/project"))

    @patch("subprocess.run")
    def test_ldm_node_wake_reports_the_scripts_failure(self, mock_run):
        # End to end over the real handler: NodeService returns the script's
        # exit code and the dispatcher must now honour it.
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=7)
        node = NodeService(MagicMock())
        node.script_path = Path(__file__)  # must exist, or the handler returns 1
        with self.assertRaises(SystemExit) as ctx:
            _dispatch(lambda: node.cmd_node_power_wake("aws-1"))
        self.assertEqual(ctx.exception.code, 7)

    @patch("subprocess.run")
    def test_a_successful_node_command_still_exits_0(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        node = NodeService(MagicMock())
        node.script_path = Path(__file__)
        _dispatch(lambda: node.cmd_node_power_wake("aws-1"))

    def test_a_missing_node_script_is_a_failure(self):
        node = NodeService(MagicMock())
        node.script_path = Path("/definitely/not/here/manage_target_nodes.py")
        with self.assertRaises(SystemExit) as ctx:
            _dispatch(node.cmd_node_power_status)
        self.assertEqual(ctx.exception.code, 1)


# --------------------------------------------------------------------------
# Finding 7: a broken archetype overlay silently produced the wrong stack.
# --------------------------------------------------------------------------


class TestArchetypeOverlayFailsLoudly(unittest.TestCase):
    """A swallowed merge emitted compose without the overlay's services.

    For the `clustered` archetype that means no `liferay2`, so a two-node
    request produced a one-node stack and exited 0.
    """

    def _overlay_dir(self, body):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        script_dir = Path(tmp.name)
        overlay = script_dir / "ldm_core" / "resources" / "archetypes" / "clustered"
        overlay.mkdir(parents=True)
        (overlay / "compose-overlay.yml").write_text(body, encoding="utf-8")
        return script_dir

    def _merge(self, script_dir, compose):
        svc = ComposerService(MagicMock())
        with patch("ldm_core.constants.SCRIPT_DIR", script_dir):
            svc._merge_archetype_overlay({"archetype": "clustered"}, compose)
        return compose

    def test_an_unparseable_overlay_exits_1(self):
        script_dir = self._overlay_dir("services:\n  liferay2:\n   bad: [unclosed\n")
        compose = {"services": {"liferay": {"image": "liferay:latest"}}}
        with self.assertRaises(SystemExit) as ctx:
            self._merge(script_dir, compose)
        self.assertEqual(
            ctx.exception.code,
            1,
            "an archetype that cannot be applied is a validation error, the "
            "same bucket the #996 triage put 'archetype not found' in",
        )

    def test_a_valid_overlay_still_adds_the_second_node(self):
        # The outcome that finding 7 is about: liferay2 present, and sharing
        # the primary's image.
        script_dir = self._overlay_dir(
            "services:\n"
            "  liferay2:\n"
            "    environment:\n"
            "      - LIFERAY_CLUSTER_PERIOD_LINK_PERIOD_ENABLED=true\n"
        )
        compose = {"services": {"liferay": {"image": "liferay:7.4"}}}
        merged = self._merge(script_dir, compose)
        self.assertIn("liferay2", merged["services"])
        self.assertEqual(merged["services"]["liferay2"]["image"], "liferay:7.4")

    def test_no_archetype_is_a_no_op(self):
        compose = {"services": {"liferay": {"image": "liferay:7.4"}}}
        svc = ComposerService(MagicMock())
        svc._merge_archetype_overlay({}, compose)
        self.assertEqual(list(compose["services"]), ["liferay"])


if __name__ == "__main__":
    unittest.main()
