import inspect
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.infra import InfraService
from ldm_core.utils import reclaim_volume_permissions as _reclaim_impl

# Captured at import time on purpose (LDM-#1507). conftest replaces
# `ldm_core.utils.reclaim_volume_permissions` with a permissive
# `lambda *_a, **_k: True` for every test not marked
# `exercises_docker_helper` (LDM-#1409), and that substitution happens
# before a test's own `@patch(..., autospec=True)` does -- so autospec
# would spec the lambda and lose the real parameter names and defaults.
# Module import runs at collection, before any of that.
_RECLAIM_SIGNATURE = inspect.signature(_reclaim_impl)


class MockInfraManager:
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True
        self.defaults = MagicMock()
        self.target: str | None = None
        self.check_docker = MagicMock()
        self.get_resolved_ip = MagicMock(return_value="127.0.0.1")
        self.detect_project_path = MagicMock()

    def run_command(self, *args, **kwargs):
        pass

    def get_container_status(self, *args, **kwargs):
        pass

    def get_resource_path(self, *args, **kwargs):
        from ldm_core.utils import get_resource_path

        return get_resource_path(*args, **kwargs)

    def check_port(self, ip, port):
        pass

    def find_available_port(self, ip, start_port, exclude=None):
        pass

    def find_dxp_roots(self, *args, **kwargs):
        return []

    def read_meta(self, *args, **kwargs):
        return {}


class TestInfraService(unittest.TestCase):
    def setUp(self):
        self.manager = MockInfraManager()
        self.infra = InfraService(self.manager)

        # LDM-#1409: every DockerService static (stop/rm/start/exists/
        # is_running/...) calls the module-scope `run_command` imported in
        # docker_service.py. `patch.object(self.manager, "run_command")` --
        # what most tests below use -- replaces a different function entirely
        # and never reaches them. That is the #1365 trap, and it was still
        # live here: measured across a full suite run, ten tests in this class
        # issued real `docker stop liferay-proxy-global`,
        # `docker rm -f liferay-proxy-global` and `docker start
        # liferay-docker-proxy` against the developer's own global proxy.
        #
        # Patching the module-scope name once, for the whole class, closes all
        # of them at the single point they share. Tests that need a specific
        # DockerService answer still patch that method directly, which replaces
        # the method and is unaffected by this.
        docker_patcher = patch("ldm_core.docker_service.run_command", return_value="")
        self.mock_docker_run_command = docker_patcher.start()
        self.addCleanup(docker_patcher.stop)

        # LDM-#1409: a second, separate route to the daemon. InfraService
        # imports get_docker_socket_path (utils.py), which shells out to
        # `docker context inspect` via subprocess directly -- not through
        # run_command, so the patch above does not cover it, and neither would
        # a guard hooked only at CommandRunner. It is also wrapped in a bare
        # `except Exception`, so the call was invisible: it ran, and any
        # failure was swallowed. Pin it to the platform default rather than
        # asking the machine.
        socket_patcher = patch(
            "ldm_core.handlers.infra.get_docker_socket_path",
            return_value="/var/run/docker.sock",
        )
        self.mock_docker_socket_path = socket_patcher.start()
        self.addCleanup(socket_patcher.stop)

    @patch("ldm_core.ui.UI.confirm", return_value=True)
    def test_fix_cert_permissions_success(self, mock_confirm):
        with (
            patch("os.getuid", return_value=1000, create=True),
            patch("os.getgid", return_value=1000, create=True),
            patch.object(self.manager, "run_command") as mock_run,
        ):
            res = self.infra._fix_cert_permissions(Path("/tmp/certs"))
            self.assertTrue(res)
            self.assertTrue(mock_run.called)
            cmd = mock_run.call_args[0][0]
            self.assertIn("chown", cmd)

    def test_get_infra_env_basic(self):
        env = self.infra._get_infra_env("192.168.1.1", 8443)
        self.assertEqual(env["LDM_RESOLVED_IP"], "192.168.1.1")
        self.assertEqual(env["LDM_SSL_PORT"], "8443")

    @patch("ldm_core.docker_service.DockerService.is_running", return_value=False)
    @patch("ldm_core.ui.UI.warning")
    def test_setup_infrastructure_port_conflict(self, mock_warning, mock_is_running):
        self.manager.args.search = False
        with (
            patch.object(
                self.manager,
                "check_port",
                side_effect=lambda _, port: port not in {80, 443},
            ),
            patch.object(
                self.manager,
                "find_available_port",
                side_effect=lambda _, port, exclude=None: port + 10,  # noqa: ARG005
            ),
            patch.object(self.manager, "run_command"),
        ):
            ssl_port = self.infra.setup_infrastructure(
                "127.0.0.1", 443, use_ssl=True, quiet=True
            )
            self.assertEqual(ssl_port, 453)  # 443 + 10
            self.assertTrue(mock_warning.called)
            warn_msgs = [call[0][0] for call in mock_warning.call_args_list]
            self.assertTrue(any("HTTP" in msg and "90" in msg for msg in warn_msgs))
            self.assertTrue(any("HTTPS" in msg and "453" in msg for msg in warn_msgs))

    @patch("ldm_core.docker_service.DockerService.is_running", return_value=False)
    def test_setup_infrastructure_custom_ssl_port(self, mock_is_running):
        self.manager.args.search = False
        with (
            patch.object(self.manager, "check_port", return_value=True),
            patch.object(self.manager, "run_command") as mock_run_cmd,
        ):
            ssl_port = self.infra.setup_infrastructure(
                "127.0.0.1", 8443, use_ssl=True, quiet=True
            )
            self.assertEqual(ssl_port, 8443)
            # Verify custom ssl_port was passed to Docker Compose env
            self.assertTrue(mock_run_cmd.called)
            called_env = mock_run_cmd.call_args[1].get("env", {})
            self.assertEqual(called_env.get("LDM_SSL_PORT"), "8443")

    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    @patch("ldm_core.handlers.infra.InfraService.get_proxy_ports")
    def test_setup_infrastructure_force_recreate(self, mock_get_ports, mock_is_running):
        self.manager.args.search = False
        mock_get_ports.return_value = {"http": 80, "https": 443, "admin": 18080}
        with (
            patch.object(self.manager, "check_port", return_value=True),
            patch.object(self.manager, "run_command") as mock_run_cmd,
        ):
            # Without force_recreate, port reverts to running port (443)
            ssl_port = self.infra.setup_infrastructure(
                "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=False
            )
            self.assertEqual(ssl_port, 443)

            # With force_recreate, port stays 8443 and --force-recreate is passed
            ssl_port_recreate = self.infra.setup_infrastructure(
                "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=True
            )
            self.assertEqual(ssl_port_recreate, 8443)
            called_args = mock_run_cmd.call_args[0][0]
            self.assertIn("--force-recreate", called_args)

    @patch("ldm_core.handlers.infra.InfraService.setup_infrastructure")
    def test_cmd_infra_setup_ssl_port_from_arg(self, mock_setup):
        import sys

        self.manager.check_docker.return_value = True
        self.manager.detect_project_path.return_value = None
        self.manager.args.ssl_port = 8443
        self.manager.args.force_recreate = True
        self.manager.args.database_mode = "local"
        self.manager.args.db = None
        self.infra.cmd_infra_setup()
        mock_setup.assert_called_once_with(
            "0.0.0.0" if sys.platform == "darwin" else "127.0.0.1",
            8443,
            use_ssl=True,
            use_shared_db=False,
            force_recreate=True,
            db_type=None,
        )

    @patch("ldm_core.handlers.infra.InfraService.setup_infrastructure")
    def test_cmd_infra_setup_ssl_port_from_env(self, mock_setup):
        import os
        import sys

        self.manager.check_docker.return_value = True
        self.manager.detect_project_path.return_value = None
        self.manager.args.ssl_port = None
        self.manager.args.force_recreate = False
        self.manager.args.database_mode = "local"
        self.manager.args.db = None
        with patch.dict(os.environ, {"LDM_SSL_PORT": "9443"}):
            self.infra.cmd_infra_setup()
            mock_setup.assert_called_once_with(
                "0.0.0.0" if sys.platform == "darwin" else "127.0.0.1",
                9443,
                use_ssl=True,
                use_shared_db=False,
                force_recreate=False,
                db_type=None,
            )

    def test_get_proxy_ports_not_running(self):
        with patch("ldm_core.docker_service.DockerService.inspect", return_value=""):
            ports = self.infra.get_proxy_ports()
            self.assertEqual(ports, {"http": 80, "https": 443, "admin": 18080})

    def test_get_proxy_ports_running(self):
        mock_inspect_json = '{"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}], "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8443"}], "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18081"}]}'
        with patch(
            "ldm_core.docker_service.DockerService.inspect",
            return_value=mock_inspect_json,
        ):
            ports = self.infra.get_proxy_ports(target_name="aws-1")
            self.assertEqual(ports, {"http": 8080, "https": 8443, "admin": 18081})

    def test_get_proxy_ports_null_settings(self):
        with patch(
            "ldm_core.docker_service.DockerService.inspect", return_value="null"
        ):
            ports = self.infra.get_proxy_ports()
            self.assertEqual(ports, {"http": 80, "https": 443, "admin": 18080})

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.detail")
    def test_setup_global_database(self, _mock_detail, _mock_info, _mock_exists):
        with (
            patch.object(
                self.manager, "run_command", return_value="accepting connections"
            ) as mock_run,
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            self.infra.setup_global_database()

            self.assertTrue(mock_run.called)
            run_cmd = None
            for call in mock_run.call_args_list:
                args = call[0][0]
                if (
                    isinstance(args, list)
                    and "run" in args
                    and "liferay-db-global" in args
                ):
                    run_cmd = args
                    break
            self.assertIsNotNone(run_cmd)
            assert isinstance(run_cmd, list)
            self.assertIn("POSTGRES_DB=lportal", run_cmd)
            self.assertIn("-v", run_cmd)
            self.assertIn("liferay-db-global-data:/var/lib/postgresql/data", run_cmd)

    def _global_db_run_cmd(self, mock_run, container):
        """The `docker run` that created `container`, or None."""
        for call in mock_run.call_args_list:
            args = call[0][0]
            if isinstance(args, list) and "run" in args and container in args:
                return args
        return None

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.detail")
    def test_setup_global_database_mysql(self, _mock_detail, _mock_info, _mock_exists):
        """LDM-#1361: `--db mysql` must provision a MySQL global, not postgres."""
        with (
            patch.object(self.manager, "run_command", return_value="mysqld is alive"),
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            mock_run = self.manager.run_command
            self.infra.setup_global_database(db_type="mysql")

            run_cmd = self._global_db_run_cmd(mock_run, "liferay-db-mysql-global")
            self.assertIsNotNone(run_cmd)
            assert isinstance(run_cmd, list)

            # The image, not just the container name -- pointing a MariaDB
            # driver at a postgres image is the #1357 defect this fixes.
            self.assertTrue(
                any(str(a).startswith("mysql:") for a in run_cmd),
                f"no mysql image in {run_cmd}",
            )
            self.assertFalse(any(str(a).startswith("postgres:") for a in run_cmd))

            self.assertIn("MYSQL_DATABASE=lportal", run_cmd)
            self.assertIn("MYSQL_USER=lportal", run_cmd)
            # Liferay connects as lportal/test and the teardown DROP uses root.
            self.assertIn("MYSQL_ROOT_PASSWORD=test", run_cmd)
            self.assertIn("MYSQL_PASSWORD=test", run_cmd)
            self.assertIn("liferay-db-mysql-global-data:/var/lib/mysql", run_cmd)
            # Makes the lowercase shared_database_name contract hold on Linux.
            self.assertIn("--lower_case_table_names=1", run_cmd)

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.detail")
    def test_setup_global_database_mariadb_joins_the_mysql_global(
        self, _mock_detail, _mock_info, _mock_exists
    ):
        """One container per protocol: mariadb must not create a third global."""
        with (
            patch.object(self.manager, "run_command", return_value="mysqld is alive"),
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            mock_run = self.manager.run_command
            self.infra.setup_global_database(db_type="mariadb")

            self.assertIsNotNone(
                self._global_db_run_cmd(mock_run, "liferay-db-mysql-global")
            )
            self.assertIsNone(self._global_db_run_cmd(mock_run, "liferay-db-global"))

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.detail")
    def test_setup_global_database_defaults_to_postgres(
        self, _mock_detail, _mock_info, _mock_exists
    ):
        """No db_type is what every pre-#1361 caller passed; it must not change."""
        with (
            patch.object(self.manager, "run_command", return_value="accepting"),
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            mock_run = self.manager.run_command
            self.infra.setup_global_database()

            run_cmd = self._global_db_run_cmd(mock_run, "liferay-db-global")
            self.assertIsNotNone(run_cmd)
            assert isinstance(run_cmd, list)
            self.assertTrue(any(str(a).startswith("postgres:") for a in run_cmd))
            self.assertIsNone(
                self._global_db_run_cmd(mock_run, "liferay-db-mysql-global")
            )

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.handlers.infra.get_actual_home", return_value=Path("/tmp"))
    @patch("ldm_core.utils.reclaim_volume_permissions")
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.detail")
    def test_setup_global_search_defaults(
        self, _mock_detail, _mock_info, _mock_reclaim, _mock_home, _mock_exists
    ):
        self.manager.defaults.get.side_effect = lambda _key, default=None: default
        with (
            patch.object(
                self.manager,
                "run_command",
                return_value='{"cluster_name": "liferay-cluster"}',
            ) as mock_run,
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            self.infra.setup_global_search(force=True)

            self.assertTrue(mock_run.called)
            # Find the docker run command invocation
            run_cmd = None
            for call in mock_run.call_args_list:
                args = call[0][0]
                if (
                    isinstance(args, list)
                    and "run" in args
                    and "liferay-search-global" in args
                ):
                    run_cmd = args
                    break
            self.assertIsNotNone(run_cmd)
            assert isinstance(run_cmd, list)
            self.assertIn("ES_JAVA_OPTS=-Xms512m -Xmx512m", run_cmd)
            self.assertIn("processors=1", run_cmd)

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.handlers.infra.get_actual_home", return_value=Path("/tmp"))
    @patch("ldm_core.utils.reclaim_volume_permissions")
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.detail")
    def test_setup_global_search_overrides(
        self, _mock_detail, _mock_info, _mock_reclaim, _mock_home, _mock_exists
    ):
        with (
            patch.object(
                self.manager,
                "run_command",
                return_value='{"cluster_name": "liferay-cluster"}',
            ) as mock_run,
            patch.object(self.manager, "get_container_status", return_value="running"),
            patch.object(self.manager.defaults, "get", return_value="256m"),
        ):
            self.infra.setup_global_search(force=True)

            self.assertTrue(mock_run.called)
            run_cmd = None
            for call in mock_run.call_args_list:
                args = call[0][0]
                if (
                    isinstance(args, list)
                    and "run" in args
                    and "liferay-search-global" in args
                ):
                    run_cmd = args
                    break
            self.assertIsNotNone(run_cmd)
            assert isinstance(run_cmd, list)
            self.assertIn("ES_JAVA_OPTS=-Xms256m -Xmx256m", run_cmd)
            self.assertIn("processors=1", run_cmd)

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.handlers.infra.get_actual_home", return_value=Path("/tmp"))
    @patch("ldm_core.utils.reclaim_volume_permissions")
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.detail")
    def test_setup_global_search_keeps_es_bind_mounts_writable_by_uid_1000(
        self, _mock_detail, _mock_info, mock_reclaim, _mock_home, _mock_exists
    ):
        """LDM-#1507: the ES bind mounts must stay writable by the container.

        `liferay-search-global` is the stock `elasticsearch:8.x` image started
        with no `--user`, so uid 1000. `reclaim_volume_permissions` chowns to
        the *host* uid (1001 on Linux CI, 501 on macOS) and a bind mount does
        no uid translation on native Linux, so the "other" class is the only
        one uid 1000 falls into. Provisioning must therefore leave both the
        data directory and the `path.repo` backup directory world-writable --
        anything narrower and ES cannot open its data path on boot or write a
        snapshot repository.

        This is the LDM-#599 -> LDM-#645 regression pinned: `c618419f` moved
        the helper default to `750` and broke native Linux; `6861e26e`
        restored `777` here. Asserted on the resolved mode bits rather than
        the literal string so it fails for *any* mode that closes the door on
        uid 1000, not only for a change back to `750`.
        """
        # Binding through the real signature scores an omitted `chmod_val`
        # as the helper's actual default -- dropping the kwarg is precisely
        # the regression, and it must not read here as "no opinion".
        requested: dict[str, str] = {}

        def _record(*args, **kwargs):
            bound = _RECLAIM_SIGNATURE.bind(*args, **kwargs)
            bound.apply_defaults()
            tree = Path(str(bound.arguments["path"])).name
            requested[tree] = str(bound.arguments["chmod_val"])
            return True

        mock_reclaim.side_effect = _record

        self.manager.defaults.get.side_effect = lambda _key, default=None: default
        with (
            patch.object(
                self.manager,
                "run_command",
                return_value='{"cluster_name": "liferay-cluster"}',
            ),
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            self.infra.setup_global_search(force=True)

        self.assertEqual(
            {"data", "backup"},
            set(requested),
            f"Provisioning must reclaim both ES bind mounts; saw {sorted(requested)}",
        )

        for tree, mode in requested.items():
            other = int(mode[-1])
            self.assertTrue(
                other & 0o2,
                f"ES bind mount '{tree}' reclaimed as {mode}: uid 1000 cannot "
                "write it. ES runs as 1000, the chown targets the host uid, "
                "and they share no group -- this reintroduces LDM-#645.",
            )
            self.assertTrue(
                other & 0o1,
                f"ES bind mount '{tree}' reclaimed as {mode}: uid 1000 cannot "
                "traverse it (LDM-#645).",
            )

    @patch("ldm_core.handlers.infra.UI")
    @patch("ldm_core.utils.has_shared_projects")
    def test_cmd_infra_down_guard(self, mock_has_shared, mock_ui):
        """Verify cmd_infra_down guards properly."""
        mock_has_shared.return_value = True
        mock_ui.confirm.return_value = False
        self.manager.non_interactive = False

        # Should abort
        res = self.infra.cmd_infra_down()
        self.assertFalse(res)

        # Should proceed
        #
        # LDM-#1365: `DockerService.stop`/`rm` must be patched too, not just the
        # manager's run_command. They are static methods calling `run_command`
        # imported at module scope in docker_service.py, so a
        # `patch.object(self.manager, ...)` never reaches them -- this test
        # issued a real `docker rm` and destroyed the developer's
        # `liferay-search-global` container on every suite run. Reproduced
        # standalone: plant a container with that name, run this test alone,
        # and it is gone.
        mock_ui.confirm.return_value = True
        with (
            patch.object(self.manager, "run_command") as mock_run,
            patch("ldm_core.docker_service.DockerService.stop") as mock_stop,
            patch("ldm_core.docker_service.DockerService.rm") as mock_rm,
        ):
            self.infra.cmd_infra_down()
            mock_run.assert_called()

            # Assert WHICH containers were targeted, rather than only that
            # something ran. INFRA_SERVICES minus liferay-proxy-global, which
            # the compose `down -v` above handles and the loop skips.
            from ldm_core.constants import INFRA_SERVICES

            expected = [
                name for name, _ in INFRA_SERVICES if name != "liferay-proxy-global"
            ]
            self.assertEqual(expected, [c.args[0] for c in mock_rm.call_args_list])
            self.assertEqual(expected, [c.args[0] for c in mock_stop.call_args_list])

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.handlers.infra.get_actual_home", return_value=Path("/tmp"))
    @patch("ldm_core.utils.reclaim_volume_permissions")
    @patch("ldm_core.ui.UI.die")
    @patch("shutil.rmtree")
    @patch("time.sleep")
    def test_setup_global_search_recursion_limit(
        self, mock_sleep, mock_rmtree, mock_die, mock_reclaim, mock_home, mock_exists
    ):
        mock_die.side_effect = SystemExit(3)
        with (
            patch.object(self.manager, "run_command", return_value=""),
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            with self.assertRaises(SystemExit):
                self.infra.setup_global_search(force=True)
            mock_die.assert_called_once()
            self.assertEqual(mock_die.call_args[1].get("exit_code"), 3)

    def test_cmd_infra_setup_remote_target(self) -> None:
        """Test cmd_infra_setup passes target context prefix to docker compose command."""
        self.manager.target = "aws-1"
        self.manager.args.search = False
        self.manager.check_docker = MagicMock(return_value=True)  # type: ignore[attr-defined]
        self.manager.detect_project_path = MagicMock(return_value=None)  # type: ignore[attr-defined]
        with (
            patch.object(self.manager, "run_command") as mock_run,
            patch("ldm_core.docker_service.get_active_target") as mock_target,
        ):
            from ldm_core.config import TargetNode

            mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
            mock_run.return_value = "OK"

            self.infra.cmd_infra_setup()
            mock_run.assert_called()
            called_cmds = [call[0][0] for call in mock_run.call_args_list]
            has_context = any(
                "--context" in cmd and "aws-1" in cmd
                for cmd in called_cmds
                if isinstance(cmd, list)
            )
            self.assertTrue(has_context)

    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    @patch("ldm_core.handlers.infra.InfraService.get_proxy_ports")
    def test_setup_infrastructure_active_projects_guard(
        self, mock_get_ports, mock_is_running
    ):
        self.manager.args.search = False
        self.manager.args.force = False
        mock_get_ports.return_value = {"http": 80, "https": 443, "admin": 18080}

        # Mock active running project
        with (
            patch.object(
                self.manager,
                "find_dxp_roots",
                return_value=[{"path": Path("/tmp/my-project"), "version": "v1"}],
            ),
            patch.object(
                self.manager,
                "read_meta",
                return_value={"container_name": "my-project-container"},
            ),
            patch("ldm_core.utils.run_command", return_value="running"),
            patch.object(self.manager, "check_port", return_value=True),
            patch.object(self.manager, "run_command"),
        ):
            # 1. Active running projects without --force should raise SystemExit (die)
            with self.assertRaises(SystemExit):
                self.infra.setup_infrastructure(
                    "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=True
                )

            # 2. Active running projects with --force should succeed
            self.manager.args.force = True
            ssl_port = self.infra.setup_infrastructure(
                "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=True
            )
            self.assertEqual(ssl_port, 8443)

    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    @patch("ldm_core.handlers.infra.InfraService.get_proxy_ports")
    def test_setup_infrastructure_fail_closed_on_exception(
        self, mock_get_ports, mock_is_running
    ):
        self.manager.args.search = False
        self.manager.args.force = False
        mock_get_ports.return_value = {"http": 80, "https": 443, "admin": 18080}

        # Mock find_dxp_roots raising exception
        with (
            patch.object(
                self.manager, "find_dxp_roots", side_effect=Exception("Disk error")
            ),
            patch.object(self.manager, "check_port", return_value=True),
            patch.object(self.manager, "run_command"),
        ):
            # 1. Verification exception without --force should raise SystemExit (die)
            with self.assertRaises(SystemExit):
                self.infra.setup_infrastructure(
                    "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=True
                )

            # 2. Verification exception with --force should succeed
            self.manager.args.force = True
            ssl_port = self.infra.setup_infrastructure(
                "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=True
            )
            self.assertEqual(ssl_port, 8443)


if __name__ == "__main__":
    unittest.main()


class TestGlobalServiceDockerCallsAreBounded(unittest.TestCase):
    """LDM-#1413: these run on paths a user waits on -- `ldm run` and
    `ldm restore` both provision global services through here -- and every one
    contacts a daemon that may never answer.

    Unbounded, a stalled socket is indistinguishable from LDM being slow. That
    ambiguity cost 84 minutes three times over in LDM-#1410 before anyone
    realised the restore was not merely taking a while.
    """

    BOUNDED_FUNCTIONS = (
        "setup_global_database",
        "setup_global_search",
        "_ensure_network",
    )

    def _calls(self, function_name):
        """Returns each run_command call's argument text within `function_name`."""
        from ldm_core.handlers import infra as infra_mod

        lines = Path(infra_mod.__file__).read_text(encoding="utf-8").splitlines()
        start = next(
            i
            for i, line in enumerate(lines, 1)
            if line.strip().startswith(f"def {function_name}")
        )
        end = next(
            i
            for i, line in enumerate(lines, 1)
            if i > start and line.startswith("    def ")
        )
        body = "\n".join(lines[start - 1 : end - 1])
        # LDM-#1361: return the FULL call text. This used to truncate to 1200
        # characters, which made the assertion below unreliable for long calls:
        # the shared-MySQL provisioning argv is long enough that a `timeout=`
        # present in the source fell past the cut, and the guard reported it as
        # unbounded. Truncation now happens only when building the failure
        # message, where it belongs.
        return list(body.split("run_command(")[1:])

    def test_every_docker_call_in_the_global_setup_paths_is_bounded(self):
        for fn in self.BOUNDED_FUNCTIONS:
            calls = self._calls(fn)
            self.assertTrue(calls, f"expected run_command calls in {fn}")
            for i, call in enumerate(calls, 1):
                self.assertIn(
                    "timeout=",
                    call,
                    f"{fn} call #{i} has no timeout -- a stalled daemon would "
                    "hang it indefinitely and look like LDM being slow (#1413).\n"
                    f"call begins: {call[:400]}",
                )

    def test_the_readiness_probes_are_not_bounded_so_tightly_they_flap(self):
        """A probe that has not answered in 30s is not going to, but an image
        pull legitimately takes minutes -- so these must not share a value."""
        from ldm_core.handlers import infra as infra_mod

        self.assertGreaterEqual(infra_mod._INFRA_PROBE_TIMEOUT, 15)
        self.assertGreater(
            infra_mod._INFRA_CREATE_TIMEOUT,
            infra_mod._INFRA_PROBE_TIMEOUT,
            "creating a service may pull an image; a probe may not",
        )
