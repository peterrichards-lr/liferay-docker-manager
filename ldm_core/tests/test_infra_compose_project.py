"""LDM-#2244: the global infra stack's Compose project name and ownership labels.

Without `-p`, Compose named the stack after the directory holding
`infra-compose.yml` -- `ldm_core/resources/` -- so it appeared in every Docker
UI as `resources`, and nothing on its containers said LDM owned them.

Each test asserts the exact argv LDM hands Docker. A stack started by an older
LDM is still in the `resources` project, so the migration branch is asserted
too: without it, `up -p ldm-infra` fails on the fixed container name and
`down -p ldm-infra` leaves the old proxy running.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from ldm_core.handlers.infra import InfraService

PROXY = "liferay-proxy-global"
COMPOSE = ["docker", "compose"]
INFRA_FILE = str(Path("/tmp/infra-compose.yml"))
LABELS_PROBE = '{{.Label "com.docker.compose.project"}}'


class _Manager:
    def __init__(self, proxy_project=""):
        self.args = MagicMock()
        self.args.search = False
        self.args.force = False
        self.non_interactive = True
        self.target = None
        self.calls = []
        self.proxy_project = proxy_project
        self.defaults = MagicMock()
        self.defaults.get.side_effect = lambda _k, default=None: default
        self.get_container_status = MagicMock(return_value="running")

    def run_command(self, cmd, *_a, **_k):
        self.calls.append(list(cmd))
        if LABELS_PROBE in cmd:
            return self.proxy_project
        return ""

    def get_resource_path(self, _name):
        return Path(INFRA_FILE)

    def check_port(self, *_a, **_k):
        return True

    def find_available_port(self, _ip, port, exclude=None):
        return port

    def find_dxp_roots(self, *_a, **_k):
        return []

    def read_meta(self, *_a, **_k):
        return {}


class _Case(unittest.TestCase):
    def setUp(self):
        # Every DockerService static goes through this module-scope name
        # (LDM-#1409); patched so nothing can reach a real daemon.
        p = patch("ldm_core.docker_service.run_command", return_value="")
        self.docker_run = p.start()
        self.addCleanup(p.stop)
        for target, value in (
            ("ldm_core.docker_service.DockerService.get_compose_cmd_prefix", COMPOSE),
            ("ldm_core.docker_service.DockerService.get_docker_cmd_prefix", ["docker"]),
            ("ldm_core.handlers.infra.get_docker_socket_path", "/var/run/docker.sock"),
        ):
            p = patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.stop = patch("ldm_core.docker_service.DockerService.stop").start()
        self.rm = patch("ldm_core.docker_service.DockerService.rm").start()
        self.addCleanup(patch.stopall)

    def compose_calls(self, manager, verb):
        return [c for c in manager.calls if c[:2] == COMPOSE and verb in c]

    def up(self, proxy_project, running=False):
        manager = _Manager(proxy_project)
        self.rm_at: list[int] = []
        self.rm.side_effect = lambda *_a, **_k: self.rm_at.append(len(manager.calls))
        infra = InfraService(manager)
        with (
            patch(
                "ldm_core.docker_service.DockerService.is_running",
                return_value=running,
            ),
            patch.object(
                InfraService,
                "get_proxy_ports",
                return_value={"http": 80, "https": 443, "admin": 18080},
            ),
            patch.object(InfraService, "_await_released_ports"),
            patch.object(InfraService, "_ensure_docker_proxy"),
            patch.object(InfraService, "_ensure_network"),
        ):
            infra.setup_infrastructure("127.0.0.1", 443, use_ssl=True, quiet=True)
        return manager

    def down(self, proxy_project):
        manager = _Manager(proxy_project)
        infra = InfraService(manager)
        with patch.object(InfraService, "_await_released_ports"):
            infra.cmd_infra_down()
        return manager


class TestEveryComposeCallNamesTheProject(_Case):
    def test_up_passes_the_ldm_project_name(self):
        manager = self.up(proxy_project="")
        self.assertEqual(
            [
                [
                    *COMPOSE,
                    "-p",
                    "ldm-infra",
                    "-f",
                    INFRA_FILE,
                    "up",
                    "-d",
                    "--remove-orphans",
                ]
            ],
            self.compose_calls(manager, "up"),
        )

    def test_down_passes_the_same_project_name_as_up(self):
        manager = self.down(proxy_project="ldm-infra")
        self.assertEqual(
            [[*COMPOSE, "-p", "ldm-infra", "-f", INFRA_FILE, "down", "-v"]],
            self.compose_calls(manager, "down"),
        )

    def test_logs_passes_the_project_name(self):
        manager = _Manager("ldm-infra")
        self.assertEqual("ldm-infra", InfraService(manager).infra_compose_project())

    def test_logs_still_reaches_a_stack_an_older_ldm_started(self):
        manager = _Manager("resources")
        self.assertEqual("resources", InfraService(manager).infra_compose_project())

    def test_logs_handler_puts_the_project_on_the_compose_argv(self):
        """The call site, not only the helper: `cmd_logs(infra=True)`."""
        from ldm_core.handlers.base import BaseHandler
        from ldm_core.tests.test_logs import MockRuntime

        runtime = MockRuntime()
        runtime.infra.infra_compose_project.return_value = "ldm-infra"
        runtime.infra._get_infra_env.return_value = {}
        with (
            patch.object(BaseHandler, "run_command", return_value="") as mock_run,
            patch.object(runtime, "get_resource_path", return_value=Path(INFRA_FILE)),
        ):
            runtime.handler.cmd_logs(infra=True, no_wait=True)
        logs = [c[0][0] for c in mock_run.call_args_list if "logs" in c[0][0]]
        self.assertTrue(logs, mock_run.call_args_list)
        argv = logs[0]
        self.assertEqual(
            ["-p", "ldm-infra", "-f", INFRA_FILE, "logs"],
            argv[argv.index("-p") : argv.index("logs") + 1],
        )


class TestLegacyResourcesStackIsMigrated(_Case):
    def test_up_replaces_a_running_proxy_from_the_resources_project(self):
        manager = self.up(proxy_project="resources", running=True)
        self.stop.assert_called_once_with(PROXY, target_name=None)
        self.rm.assert_called_once_with(PROXY, force=True, target_name=None)
        # Removed BEFORE the up that recreates it under the new name.
        up = self.compose_calls(manager, "up")
        self.assertEqual(1, len(up))
        self.assertIn("ldm-infra", up[0])
        self.assertLessEqual(self.rm_at[0], manager.calls.index(up[0]))

    def test_up_leaves_a_proxy_already_in_ldm_infra_alone(self):
        self.up(proxy_project="ldm-infra", running=True)
        self.stop.assert_not_called()
        self.rm.assert_not_called()

    def test_down_removes_the_legacy_proxy_rather_than_orphaning_it(self):
        manager = self.down(proxy_project="resources")
        self.rm.assert_any_call(PROXY, force=True, target_name=None)
        # `compose -p resources` is never run: a user's own stack in a
        # directory named `resources` would share that project.
        self.assertFalse(
            [c for c in manager.calls if "resources" in c and c[:2] == COMPOSE]
        )

    def test_down_without_a_legacy_proxy_does_not_touch_it_directly(self):
        self.down(proxy_project="ldm-infra")
        self.assertNotIn(PROXY, [c.args[0] for c in self.rm.call_args_list])


class TestOwnershipLabels(_Case):
    def test_compose_file_labels_traefik(self):
        import ldm_core

        compose = yaml.safe_load(
            (
                Path(ldm_core.__file__).parent / "resources" / "infra-compose.yml"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["com.liferay.ldm.managed=true", "com.liferay.ldm.infra=proxy"],
            compose["services"]["traefik"]["labels"],
        )

    def test_docker_socket_proxy_run_carries_the_labels(self):
        manager = _Manager()
        with patch("ldm_core.docker_service.DockerService.exists", return_value=False):
            InfraService(manager)._ensure_docker_proxy()
        run = [c for c in manager.calls if "run" in c]
        self.assertEqual(1, len(run))
        argv = run[0]
        self.assertEqual(
            [
                "run",
                "-d",
                "--label",
                "com.liferay.ldm.managed=true",
                "--label",
                "com.liferay.ldm.infra=docker-proxy",
                "--name",
                "liferay-docker-proxy",
            ],
            argv[argv.index("run") : argv.index("--name") + 2],
        )

    def test_global_search_run_carries_the_labels(self):
        manager = _Manager()
        original = manager.run_command

        def run(cmd, *a, **k):
            original(cmd, *a, **k)
            return '{"cluster_name": "liferay-cluster"}'

        manager.run_command = run  # type: ignore[method-assign]
        with (
            patch("ldm_core.docker_service.DockerService.exists", return_value=False),
            patch("ldm_core.handlers.infra.get_actual_home", return_value=Path("/tmp")),
            patch("ldm_core.utils.reclaim_volume_permissions"),
            patch("ldm_core.ui.UI.info"),
            patch("ldm_core.ui.UI.detail"),
        ):
            InfraService(manager).setup_global_search(force=True)
        run_calls = [
            c for c in manager.calls if "run" in c and "liferay-search-global" in c
        ]
        self.assertTrue(run_calls)
        argv = run_calls[0]
        self.assertEqual(
            [
                "run",
                "-d",
                "--label",
                "com.liferay.ldm.managed=true",
                "--label",
                "com.liferay.ldm.infra=search",
                "--name",
                "liferay-search-global",
            ],
            argv[argv.index("run") : argv.index("--name") + 2],
        )


class TestReservedNames(unittest.TestCase):
    def test_both_the_new_and_the_legacy_project_names_are_reserved(self):
        from ldm_core.handlers.infra import (
            INFRA_COMPOSE_PROJECT,
            LEGACY_INFRA_COMPOSE_PROJECT,
        )
        from ldm_core.utils import reserved_compose_project_name

        for name in (INFRA_COMPOSE_PROJECT, LEGACY_INFRA_COMPOSE_PROJECT):
            with self.subTest(name=name):
                self.assertEqual(name, reserved_compose_project_name(name))
        self.assertEqual("ldm-infra", reserved_compose_project_name("LDM-Infra"))
        self.assertIsNone(reserved_compose_project_name("ldm-infra-dev"))


if __name__ == "__main__":
    unittest.main()
