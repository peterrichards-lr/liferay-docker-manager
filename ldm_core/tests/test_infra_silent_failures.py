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

import contextlib
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
        self.select_project_interactively = MagicMock(return_value=None)

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


class TestTheSearchWipeIsGuardedLikeTheSSLRecreate(unittest.TestCase):
    """LDM-#2083: the irreversible operation had no guard, the reversible one did.

    `shutil.rmtree(es_data)` deletes the SHARED search data directory,
    destroying the index of every project on the machine. It ran with no
    prompt and no --force. The SSL proxy recreate, in the same file, refuses
    outright when projects are live -- and costs only a few seconds of
    connectivity.

    Observed 2026-10-07: LDM wiped a running project's index and then refused
    to recreate the proxy for that same project two steps later. Nothing
    surfaced the loss, because a project's healthcheck is an HTTP probe
    against the portal that never touches search.
    """

    def _run(self, *, running, cli_force=False, probe_raises=False, home):
        calls = []

        def responder(cmd, **_kwargs):
            calls.append(list(cmd))
            return ""

        manager = _Manager(responder=responder)
        manager.args.force = cli_force
        infra = InfraService(manager)

        if probe_raises:
            scan = MagicMock(side_effect=RuntimeError("daemon gave no answer"))
        else:
            scan = MagicMock(return_value=running)

        die_calls = []

        def fake_die(msg, details=None, tip=None, exit_code=1, **_kw):
            die_calls.append(
                {
                    "msg": msg,
                    "details": details or "",
                    "tip": tip or "",
                    "code": exit_code,
                }
            )
            raise SystemExit(exit_code)

        with (
            _no_real_docker(),
            patch("ldm_core.docker_service.DockerService.exists", return_value=False),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=False
            ),
            patch("ldm_core.docker_service.DockerService.start"),
            patch("ldm_core.handlers.infra.get_actual_home", return_value=home),
            patch("ldm_core.utils.reclaim_volume_permissions"),
            patch.object(InfraService, "scan_running_projects", scan),
            patch("ldm_core.handlers.infra.UI.die", side_effect=fake_die),
            patch("shutil.rmtree") as rmtree,
            patch("time.sleep"),
        ):
            with self.assertRaises(SystemExit):
                infra.setup_global_search()
            return rmtree, die_calls, calls

    @staticmethod
    def _tmp():
        import tempfile

        return tempfile.TemporaryDirectory()

    def test_the_wipe_is_refused_while_a_project_runs(self):
        with self._tmp() as tmp:
            rmtree, die, _ = self._run(
                running=[("devcon2026", "local")], home=Path(tmp)
            )
        rmtree.assert_not_called()
        self.assertEqual(die[0]["code"], 3)

    def test_the_refusal_names_the_project_and_how_to_stop_it(self):
        """A refusal that does not say what to do just moves the problem."""
        with self._tmp() as tmp:
            _, die, _ = self._run(running=[("devcon2026", "local")], home=Path(tmp))
        blob = die[0]["msg"] + die[0]["details"] + die[0]["tip"]
        self.assertIn("devcon2026", blob)
        self.assertIn("ldm stop devcon2026", blob)
        self.assertIn("--force", blob)

    def test_the_refusal_says_the_loss_is_silent(self):
        """The reason this went unnoticed for a cycle, stated where it helps."""
        with self._tmp() as tmp:
            _, die, _ = self._run(running=[("devcon2026", "local")], home=Path(tmp))
        self.assertIn("healthcheck", die[0]["details"])

    def test_force_wipes_anyway(self):
        """--force is the documented way through, exactly as the SSL guard."""
        with self._tmp() as tmp:
            rmtree, _, _ = self._run(
                running=[("devcon2026", "local")], cli_force=True, home=Path(tmp)
            )
        self.assertTrue(rmtree.called)

    def test_an_unanswerable_probe_refuses_rather_than_assuming_idle(self):
        """LDM-#1548's lesson applied to the destructive path: a daemon that
        cannot answer must never read as 'nothing is running'."""
        with self._tmp() as tmp:
            rmtree, die, _ = self._run(running=[], probe_raises=True, home=Path(tmp))
        rmtree.assert_not_called()
        self.assertEqual(die[0]["code"], 3)

    def test_an_unanswerable_probe_with_force_still_proceeds(self):
        with self._tmp() as tmp:
            rmtree, _, _ = self._run(
                running=[], probe_raises=True, cli_force=True, home=Path(tmp)
            )
        self.assertTrue(rmtree.called)

    def test_an_idle_machine_still_repairs(self):
        """The contrast case. The guard must not break the repair itself."""
        with self._tmp() as tmp:
            rmtree, _, _ = self._run(running=[], home=Path(tmp))
        self.assertTrue(rmtree.called)

    def test_the_failing_containers_logs_are_captured_before_it_is_removed(self):
        """The repair ended with `docker rm -f`, taking the only record of
        what it was repairing. Ordering is the whole assertion: captured
        after the removal is captured never."""
        with self._tmp() as tmp:
            _, _, calls = self._run(running=[], home=Path(tmp))

        def first_index(verb):
            for i, c in enumerate(calls):
                if verb in c:
                    return i
            return None

        logs_at, rm_at = first_index("logs"), first_index("rm")
        self.assertIsNotNone(logs_at, f"no 'docker logs' call was made: {calls}")
        self.assertIsNotNone(rm_at, f"no 'docker rm' call was made: {calls}")
        self.assertLess(logs_at, rm_at)


class TestTheProxyIsNotMistakenForAPortConflict(unittest.TestCase):
    """LDM-#2101: --force-recreate raced its own teardown.

    `docker rm -f` returns when the container is gone, not when the host has
    released its port bindings. The availability check ran immediately
    after, so LDM concluded its own just-removed proxy was a conflict and
    reallocated -- permanently, because a later `infra setup` without
    --force-recreate adopts the running container's ports (LDM-#1568).

    Measured in a committed verification report: created on 80/443/18080,
    `--force-recreate` seconds later, both 80 and 18080 reported in use and
    bumped to 81 and 18081. Both by exactly one, to the adjacent port.
    """

    def _service(self, free_after):
        """check_port returns False until it has been asked `free_after`
        times for that port, then True -- a binding that clears shortly
        after removal, which is the real behaviour being modelled."""
        manager = _Manager()
        asked: dict[int, int] = {}

        def check_port(_ip, port):
            asked[port] = asked.get(port, 0) + 1
            return asked[port] > free_after

        manager.check_port = MagicMock(side_effect=check_port)
        manager.find_available_port = MagicMock(side_effect=lambda _ip, port: port + 1)
        return InfraService(manager), manager

    def test_a_binding_that_clears_shortly_is_not_reallocated(self):
        """The bug itself: with free_after=2 the port is busy for the first
        two probes and free afterwards."""
        infra, manager = self._service(free_after=2)

        with patch("time.sleep"):
            infra._await_released_ports([80, 18080], timeout=5.0, interval=0)

        # Having waited, the ports now read as free to the caller.
        self.assertTrue(manager.check_port("0.0.0.0", 80))
        self.assertTrue(manager.check_port("0.0.0.0", 18080))

    def test_a_port_held_by_something_else_is_still_reported_busy(self):
        """The guard must not be weakened. A port that never frees must
        still fall through to the existing reallocation."""
        manager = _Manager()
        manager.check_port = MagicMock(return_value=False)
        infra = InfraService(manager)

        with patch("time.sleep"):
            infra._await_released_ports([80], timeout=0.3, interval=0)

        self.assertFalse(manager.check_port("0.0.0.0", 80))

    def test_the_wait_is_scoped_to_the_ports_we_released(self):
        """Never a blind sleep: nothing is probed when the proxy was not
        running and there was nothing to release."""
        manager = _Manager()
        manager.check_port = MagicMock(return_value=True)
        infra = InfraService(manager)

        infra._await_released_ports([])
        infra._await_released_ports(None)

        manager.check_port.assert_not_called()

    def test_it_returns_as_soon_as_the_ports_are_free(self):
        """It must not burn the whole timeout on the common case."""
        manager = _Manager()
        manager.check_port = MagicMock(return_value=True)
        infra = InfraService(manager)

        with patch("time.sleep") as slept:
            infra._await_released_ports([80, 443, 18080], timeout=30.0)

        slept.assert_not_called()
        self.assertEqual(manager.check_port.call_count, 3)

    def test_the_recreate_path_actually_waits(self):
        """The helper being correct is worthless if nothing calls it.

        Asserts the wiring, not the implementation: a force-recreate of a
        RUNNING proxy must await exactly the ports it just gave up, and must
        do so before the availability checks that follow.
        """
        manager = _Manager()
        infra = InfraService(manager)
        order = []

        # Takes `self`: patch.object with a plain function binds it as an
        # unbound method, so omitting it silently shifts every argument --
        # which is how this test first "failed" against working code.
        def await_spy(_self, ports, **_kw):
            order.append(("await", sorted(p for p in ports if p)))

        def check_spy(_ip, port):
            order.append(("check", port))
            return True

        manager.check_port = MagicMock(side_effect=check_spy)

        with (
            _no_real_docker(),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=True
            ),
            patch("ldm_core.docker_service.DockerService.stop"),
            patch("ldm_core.docker_service.DockerService.rm"),
            patch.object(
                InfraService,
                "get_proxy_ports",
                return_value={"http": 80, "https": 443, "admin": 18080},
            ),
            patch.object(InfraService, "_await_released_ports", await_spy),
            patch.object(InfraService, "scan_running_projects", return_value=[]),
            patch("ldm_core.handlers.infra.UI.die", side_effect=SystemExit(1)),
            patch("time.sleep"),
        ):
            # Narrow deliberately. A broad `except Exception` here hid a
            # TypeError in this test's own stub and made the assertion
            # below look like a defect in the code under test.
            with contextlib.suppress(SystemExit):
                infra.setup_infrastructure(
                    "0.0.0.0", 443, use_ssl=True, force_recreate=True
                )

        awaits = [o for o in order if o[0] == "await"]
        self.assertTrue(awaits, f"the recreate path never awaited: {order[:8]}")
        self.assertEqual(awaits[0][1], [80, 443, 18080])

        first_check = next((i for i, o in enumerate(order) if o[0] == "check"), None)
        first_await = next(i for i, o in enumerate(order) if o[0] == "await")
        if first_check is not None:
            self.assertLess(
                first_await,
                first_check,
                "the wait must come BEFORE the availability checks",
            )


class TestInfraSetupNeverAsksWhichProject(unittest.TestCase):
    """LDM-#2102: a machine-scope command reached the interactive picker.

    `ldm infra setup` resolved a project only to read two values --
    `resolve_database_mode(meta, ...)` and `meta.get("db_type")` -- and with
    no project in the cwd it asked:

        === Select Project ===
        [1] liferay-ai-commerce-accelerator [2026.q3.0]
        [2] devcon2026 [2026.q3.5]

    The answer decides whether the WHOLE MACHINE gets a shared database and
    with which engine, from a list ordered by discovery rather than
    relevance, and the prompt says none of that. It also made the command
    unscriptable: unattended use hangs on the question.
    """

    def _run_setup(self):
        manager = _Manager()
        manager.args.ssl_port = 443
        manager.args.force_recreate = False
        manager.args.database_mode = None
        manager.args.db = None
        infra = InfraService(manager)

        picker = MagicMock(return_value={"path": Path("/tmp/whichever"), "new": False})
        manager.select_project_interactively = picker
        captured = {}

        def fake_setup(*args, **kwargs):
            captured["kwargs"] = kwargs

        with (
            _no_real_docker(),
            patch.object(InfraService, "setup_infrastructure", fake_setup),
        ):
            infra.cmd_infra_setup()
        return picker, captured

    def test_it_does_not_ask(self):
        picker, _ = self._run_setup()

        picker.assert_not_called()

    def test_it_still_sets_infrastructure_up(self):
        """The guard must not turn the command into a no-op."""
        _, captured = self._run_setup()

        self.assertIn("kwargs", captured)

    def test_the_detection_is_asked_not_to_prompt(self):
        """Asserts the mechanism, since the picker could also be unreached
        by accident -- e.g. if detection simply failed in this fixture."""
        manager = _Manager()
        manager.args.ssl_port = 443
        manager.args.force_recreate = False
        manager.args.database_mode = None
        manager.args.db = None
        infra = InfraService(manager)
        seen = {}

        def detect(project_id=None, **kwargs):
            seen.update(kwargs)

        manager.detect_project_path = MagicMock(side_effect=detect)

        with (
            _no_real_docker(),
            patch.object(InfraService, "setup_infrastructure", MagicMock()),
        ):
            infra.cmd_infra_setup()

        self.assertFalse(
            seen.get("interactive", True),
            f"infra setup must request non-interactive detection; got {seen}",
        )


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
