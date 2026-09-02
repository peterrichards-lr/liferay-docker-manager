"""Infrastructure failures in `handlers/infra.py` that reported success (LDM-#1548).

`handlers/` was never triaged against the exit-code contract (#996 covered
`pipelines/run.py` only), and two mechanics made its failures invisible:
`run_command(..., check=False)` returns None for a non-zero exit *and* for a
timeout, and `UI.error` prints and returns -- only `UI.die` exits.

Every test here asserts an outcome: the process exit code, whether a directory
was wiped, whether a move happened, or which argv reached Docker. Exit code 3
is the contract's Infrastructure/Data Error
(.agents/skills/ldm-architecture/SKILL.md).
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.infra import (
    _INFRA_CREATE_TIMEOUT,
    InfraService,
)


class _Manager:
    """Minimal stand-in for the LDM manager, with a scriptable run_command."""

    def __init__(self, responder=None, status="running"):
        self.args = MagicMock()
        self.args.force = False
        self.args.search = False
        self.args.no_move = False
        self.verbose = False
        self.non_interactive = True
        self.target: str | None = None
        self.defaults = MagicMock()
        self.defaults.get.side_effect = lambda _key, default=None: default
        self._status = status
        self._responder = responder or (lambda _cmd, **_kw: "")
        self.run_command = MagicMock(side_effect=self._responder)
        self.check_docker = MagicMock(return_value=True)
        self.get_resolved_ip = MagicMock(return_value="127.0.0.1")
        self.detect_project_path = MagicMock(return_value=None)
        self.check_port = MagicMock(return_value=True)
        self.find_available_port = MagicMock(return_value=443)

    def get_container_status(self, *_args, **_kwargs):
        return self._status

    def get_resource_path(self, *args, **kwargs):
        from ldm_core.utils import get_resource_path

        return get_resource_path(*args, **kwargs)

    def find_dxp_roots(self, *_args, **_kwargs):
        return []

    def read_meta(self, *_args, **_kwargs):
        return {}


def _no_real_docker():
    """Blocks the module-scope run_command every DockerService static calls.

    The LDM-#1409/#1365 trap: patching the manager's run_command does not reach
    DockerService, whose statics would otherwise issue real `docker` commands
    against the developer's own global containers.
    """
    return patch("ldm_core.docker_service.run_command", return_value=None)


class TestRestartProxyReportsWhatHappened(unittest.TestCase):
    """`ldm infra restart-proxy` reported success unconditionally (LDM-#1548).

    `DockerService.restart` runs with check=False and its result was discarded;
    an absent container only reached `UI.error`, which returns. Both exited 0.
    """

    def setUp(self):
        self.infra = InfraService(_Manager())

    def test_a_missing_proxy_container_exits_3(self):
        with (
            _no_real_docker(),
            patch("ldm_core.docker_service.DockerService.exists", return_value=False),
            self.assertRaises(SystemExit) as ctx,
        ):
            self.infra.cmd_restart_proxy()
        self.assertEqual(ctx.exception.code, 3)

    def test_a_proxy_that_does_not_come_back_exits_3(self):
        with (
            _no_real_docker(),
            patch("ldm_core.docker_service.DockerService.exists", return_value=True),
            patch("ldm_core.docker_service.DockerService.restart") as mock_restart,
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=False
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            self.infra.cmd_restart_proxy()
        self.assertEqual(ctx.exception.code, 3)
        mock_restart.assert_called_once()

    def test_a_successful_restart_still_succeeds(self):
        """The guard must not turn a healthy restart into a failure."""
        with (
            _no_real_docker(),
            patch("ldm_core.docker_service.DockerService.exists", return_value=True),
            patch("ldm_core.docker_service.DockerService.restart"),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=True
            ),
        ):
            self.infra.cmd_restart_proxy()


class TestSearchAutoRepairDoesNotWipeBlindly(unittest.TestCase):
    """The ES auto-repair destroyed data on an unverified removal (LDM-#1548).

    `docker rm -f` ran with check=False and its result was discarded, yet the
    `shutil.rmtree` below assumed it had worked. When removal failed, the data
    directory was deleted under a live Elasticsearch and the recursive call
    found the container still present -- taking the existing-container branch,
    which has no readiness probe, so the run reported success.
    """

    def _run(self, *, rm_result, exists_after_rm, home):
        def responder(cmd, **_kwargs):
            if "rm" in cmd:
                return rm_result
            return ""  # the readiness curl never reports a cluster_name

        manager = _Manager(responder=responder)
        infra = InfraService(manager)
        # exists(): False for the initial provisioning check, then whatever the
        # scenario says the `rm -f` left behind.
        exists_answers = [False, exists_after_rm]

        with (
            _no_real_docker(),
            patch(
                "ldm_core.docker_service.DockerService.exists",
                side_effect=lambda *_a, **_k: (
                    exists_answers.pop(0) if exists_answers else exists_after_rm
                ),
            ),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=False
            ),
            patch("ldm_core.docker_service.DockerService.start"),
            patch("ldm_core.handlers.infra.get_actual_home", return_value=home),
            patch("ldm_core.utils.reclaim_volume_permissions"),
            patch("shutil.rmtree") as mock_rmtree,
            patch("time.sleep"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                infra.setup_global_search(force=True)
            return ctx.exception.code, mock_rmtree

    def test_a_container_that_survived_rm_is_not_wiped(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            code, mock_rmtree = self._run(
                rm_result=None, exists_after_rm=True, home=Path(tmp)
            )
        self.assertEqual(code, 3)
        mock_rmtree.assert_not_called()

    def test_a_removal_that_worked_still_wipes_and_retries(self):
        """The contrast case: only the `rm -f` result differs."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            code, mock_rmtree = self._run(
                rm_result="", exists_after_rm=False, home=Path(tmp)
            )
        # Exhausts the two retries and dies with the pre-existing depth guard.
        self.assertEqual(code, 3)
        self.assertTrue(mock_rmtree.called)


class TestExistingSearchContainerIsProbed(unittest.TestCase):
    """The existing-container branch never checked anything (LDM-#1548).

    `DockerService.start` runs with check=False, its result was discarded, and
    the only readiness probe lives in the create branch -- so a global search
    that failed to start left LDM configuring Liferay against a dead engine.
    """

    def test_a_search_container_that_will_not_start_exits_3(self):
        infra = InfraService(_Manager())
        with (
            _no_real_docker(),
            patch("ldm_core.docker_service.DockerService.exists", return_value=True),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=False
            ),
            patch("ldm_core.docker_service.DockerService.start") as mock_start,
            self.assertRaises(SystemExit) as ctx,
        ):
            infra.setup_global_search(force=True)
        self.assertEqual(ctx.exception.code, 3)
        mock_start.assert_called()

    def test_a_running_search_container_is_left_alone(self):
        manager = _Manager(responder=lambda _cmd, **_kw: '{"acknowledged":true}')
        infra = InfraService(manager)
        with (
            _no_real_docker(),
            patch("ldm_core.docker_service.DockerService.exists", return_value=True),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=True
            ),
            patch("ldm_core.ui.UI.warning") as mock_warning,
        ):
            infra.setup_global_search(force=True)
        mock_warning.assert_not_called()


class TestNonFatalSearchDefectsWarn(unittest.TestCase):
    """Judgement calls: these degrade search, they do not break the stack.

    A rejected backup-repository registration only matters when a snapshot
    including search indices is taken, and a missing analysis plugin means
    CJK content is tokenised with the default analyser. Aborting an otherwise
    healthy provision over either would be worse than the silence -- but the
    user has to be told, which is what was missing.
    """

    def test_a_rejected_backup_repo_registration_warns(self):
        manager = _Manager(responder=lambda _cmd, **_kw: '{"error":"forbidden"}')
        infra = InfraService(manager)
        with (
            _no_real_docker(),
            patch("ldm_core.docker_service.DockerService.exists", return_value=True),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=True
            ),
            patch("ldm_core.ui.UI.warning") as mock_warning,
        ):
            infra.setup_global_search(force=True)
        self.assertTrue(
            any("backup repository" in str(c) for c in mock_warning.call_args_list),
            "a rejected registration must be reported, not swallowed",
        )

    def test_failed_analyzer_installs_warn_and_name_the_plugins(self):
        def responder(cmd, **_kwargs):
            if "install" in cmd:
                return None  # every analyzer download fails
            if "curl" in cmd:
                return '{"cluster_name": "liferay-cluster", "acknowledged":true}'
            return ""

        manager = _Manager(responder=responder)
        infra = InfraService(manager)
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with (
                _no_real_docker(),
                patch(
                    "ldm_core.docker_service.DockerService.exists", return_value=False
                ),
                patch(
                    "ldm_core.docker_service.DockerService.is_running",
                    return_value=False,
                ),
                patch(
                    "ldm_core.handlers.infra.get_actual_home", return_value=Path(tmp)
                ),
                patch("ldm_core.utils.reclaim_volume_permissions"),
                patch("ldm_core.ui.UI.warning") as mock_warning,
                patch("time.sleep"),
            ):
                infra.setup_global_search(force=True)

        warned = " ".join(str(c) for c in mock_warning.call_args_list)
        self.assertIn("analysis-kuromoji", warned)
        # Warned about, not fatal: no SystemExit was raised above.


class TestRunningProjectScanCannotBeFooledByABrokenDaemon(unittest.TestCase):
    """The scan could not tell "nothing running" from "docker broke" (#1548).

    `docker ps` ran with check=False, so an unreachable daemon returned None --
    identical to "no container matched" -- and the warning this block exists to
    print could not appear. It now fails closed into the handler that honours
    --force.
    """

    def _recreate(self, ps_result):
        manager = _Manager()
        infra = InfraService(manager)
        return manager, infra, ps_result

    def test_a_daemon_that_cannot_answer_aborts_without_force(self):
        manager, infra, ps_result = self._recreate(None)
        with (
            _no_real_docker(),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=True
            ),
            patch(
                "ldm_core.handlers.infra.InfraService.get_proxy_ports",
                return_value={"http": 80, "https": 443, "admin": 18080},
            ),
            patch.object(
                manager,
                "find_dxp_roots",
                return_value=[{"path": Path("/tmp/proj"), "version": "v1"}],
            ),
            patch.object(
                manager, "read_meta", return_value={"container_name": "proj-container"}
            ),
            patch("ldm_core.utils.run_command", return_value=ps_result),
        ):
            with self.assertRaises(SystemExit):
                infra.setup_infrastructure(
                    "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=True
                )

            # --force is the documented escape hatch and must still work.
            manager.args.force = True
            self.assertEqual(
                infra.setup_infrastructure(
                    "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=True
                ),
                8443,
            )

    def test_an_empty_answer_is_still_a_real_answer(self):
        """Exit 0 with no matching container must not be treated as a failure."""
        manager, infra, ps_result = self._recreate("")
        with (
            _no_real_docker(),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=True
            ),
            patch(
                "ldm_core.handlers.infra.InfraService.get_proxy_ports",
                return_value={"http": 80, "https": 443, "admin": 18080},
            ),
            patch.object(
                manager,
                "find_dxp_roots",
                return_value=[{"path": Path("/tmp/proj"), "version": "v1"}],
            ),
            patch.object(
                manager, "read_meta", return_value={"container_name": "proj-container"}
            ),
            patch("ldm_core.utils.run_command", return_value=ps_result),
        ):
            self.assertEqual(
                infra.setup_infrastructure(
                    "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=True
                ),
                8443,
            )


class TestRelocateWillNotMoveALiveVM(unittest.TestCase):
    """`colima stop`'s result was discarded, then ~/.colima was moved (#1548).

    Moving a running VM's disk image is how you corrupt it, and the command
    printed "Relocation complete" either way. `colima status` exits 0 only
    while the VM runs -- the same test docs/tutorials/quick_start.md relies on.
    """

    def _relocate(self, status_result):
        import tempfile

        def responder(cmd, **_kwargs):
            if "context" in cmd:
                return "colima"
            if cmd[:2] == ["colima", "stop"]:
                return None  # the stop failed
            if cmd[:2] == ["colima", "status"]:
                return status_result
            return ""

        manager = _Manager(responder=responder)
        infra = InfraService(manager)
        home_dir = tempfile.TemporaryDirectory()
        target_dir = tempfile.TemporaryDirectory()
        home = Path(home_dir.name)
        (home / ".colima").mkdir()
        with (
            patch("ldm_core.handlers.infra.get_actual_home", return_value=home),
            patch("shutil.move") as mock_move,
            patch("ldm_core.ui.UI.confirm", return_value=True),
        ):
            code: object = 0
            try:
                infra.cmd_system_relocate(target_dir.name)
            except SystemExit as exc:
                code = exc.code
        home_dir.cleanup()
        target_dir.cleanup()
        return code, mock_move

    def test_a_still_running_colima_aborts_before_the_move(self):
        code, mock_move = self._relocate(status_result="")  # status exit 0 => running
        self.assertEqual(code, 3)
        mock_move.assert_not_called()

    def test_an_already_stopped_colima_does_not_block_relocation(self):
        """`colima stop` also fails when it was never running -- not an abort."""
        code, mock_move = self._relocate(status_result=None)  # status non-zero
        self.assertEqual(code, 0)
        self.assertTrue(mock_move.called)


class TestFailedCertificateGenerationSaysSo(unittest.TestCase):
    """`setup_ssl` returned False on five paths and nobody read it (LDM-#1548).

    Both callers (`pipelines/run.py:1341`, `runtime/orchestration.py:957`)
    discard the result, so four of the five said nothing about the consequence:
    the run continued and reported success while Traefik served its built-in
    untrusted certificate.

    Deliberately a warning, not a UI.die -- that fallback is a browser trust
    prompt, not a broken stack, and the mkcert-missing path has always been an
    intentional degradation. What was missing was telling the user.
    """

    def test_a_failed_mkcert_run_warns_about_the_fallback_certificate(self):
        import tempfile

        manager = _Manager(responder=lambda _cmd, **_kw: None)  # mkcert fails
        infra = InfraService(manager)
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "ldm_core.handlers.infra.shutil.which",
                    return_value="/usr/bin/mkcert",
                ),
                patch("ldm_core.ui.UI.warning") as mock_warning,
            ):
                result = infra.setup_ssl(Path(tmp), "example.lvh.me")

        self.assertFalse(result)
        warned = " ".join(str(c) for c in mock_warning.call_args_list)
        self.assertIn("self-signed", warned)


class TestDockerSocketBridgeFollowsTheTarget(unittest.TestCase):
    """The socket bridge ignored the target and was unbounded (LDM-#1548).

    Every other container here resolves the target first; this one hardcoded
    `docker`, so provisioning a remote node created the bridge on the laptop
    and left Traefik on the remote node with nothing to talk to. The `docker
    run` also pulls an image with no timeout, which LDM-#1413's constants exist
    to prevent.
    """

    def test_a_remote_target_creates_the_bridge_on_the_remote_daemon(self):
        manager = _Manager()
        manager.target = "aws-1"
        infra = InfraService(manager)
        with (
            _no_real_docker(),
            patch("ldm_core.docker_service.DockerService.exists", return_value=False),
            patch(
                "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
                return_value=["docker", "--context", "aws-1"],
            ),
        ):
            infra._ensure_docker_proxy("aws-1")

        call = manager.run_command.call_args
        argv = call[0][0]
        self.assertEqual(argv[:3], ["docker", "--context", "aws-1"])
        # A host path from this machine would just become an empty directory on
        # the remote engine, so the remote daemon's own socket is bound.
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock:ro", argv)
        self.assertEqual(call[1].get("timeout"), _INFRA_CREATE_TIMEOUT)

    def test_the_infrastructure_stack_bounds_its_compose_up(self):
        manager = _Manager()
        infra = InfraService(manager)
        with (
            _no_real_docker(),
            patch("ldm_core.docker_service.DockerService.exists", return_value=True),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=False
            ),
            patch("ldm_core.docker_service.DockerService.start"),
            patch("ldm_core.handlers.infra.InfraService.setup_ssl", return_value=True),
        ):
            infra.setup_infrastructure(
                "127.0.0.1", 8443, use_ssl=True, quiet=True, use_shared_search=False
            )

        up_calls = [
            c
            for c in manager.run_command.call_args_list
            if isinstance(c[0][0], list) and "up" in c[0][0]
        ]
        self.assertTrue(up_calls, "expected a `compose up` for the infra stack")
        for call in up_calls:
            self.assertEqual(
                call[1].get("timeout"),
                _INFRA_CREATE_TIMEOUT,
                "`compose up` may pull the Traefik image; unbounded, a stalled "
                "daemon is indistinguishable from LDM being slow (#1413)",
            )


if __name__ == "__main__":
    unittest.main()
