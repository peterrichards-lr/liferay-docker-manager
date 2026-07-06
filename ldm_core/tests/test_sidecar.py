import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.config import ConfigService
from ldm_core.handlers.infra import InfraService
from ldm_core.handlers.runtime import RuntimeService


class MockManager(BaseHandler):
    def __init__(self):
        from argparse import Namespace

        self.args = Namespace(
            database_mode=None,
            search_mode=None,
            search=False,
            ssl=None,
            lean=False,
            tunnel_managed_cors=False,
        )
        self.verbose = False
        self.non_interactive = True
        self.composer = ComposerService(self)
        self.infra = InfraService(self)
        self.runtime = RuntimeService(self)
        self.config = ConfigService(self)
        self.defaults = MagicMock()
        self.defaults.get = MagicMock(return_value="isolated")
        self.parse_version = MagicMock(return_value=(2024, 1, 0))  # type: ignore[method-assign]
        self.run_command = MagicMock(return_value="")  # type: ignore[method-assign]

    def setup_paths(self, root):
        root = Path(root)
        return {
            "root": root,
            "files": root / "files",
            "configs": root / "osgi" / "configs",
            "data": root / "data",
            "deploy": root / "deploy",
            "modules": root / "osgi" / "modules",
            "scripts": root / "scripts",
            "compose": root / "docker-compose.yml",
            "logs": root / "logs",
            "state": root / "osgi" / "state",
            "ce_dir": root / "client-extensions",
            "cx": root / "osgi" / "client-extensions",
            "routes": root / "routes",
            "backups": root / "snapshots",
            "portal_log4j": root / "osgi" / "portal-log4j",
            "log4j": root / "osgi" / "log4j",
        }

    def read_meta(self, path):
        return self.meta

    def write_meta(self, path, meta):
        self.meta = meta

    def get_resolved_ip(self, host):
        return "127.0.0.1"

    def get_resource_path(self, name):
        return Path("/tmp/res") / name


class TestSidecarImplementation(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.manager.config.update_portal_ext = MagicMock()  # type: ignore[method-assign]
        self.project_root = Path("/tmp/sidecar-project")
        self.paths = self.manager.setup_paths(self.project_root)

    def _validate_compose_dependencies(self, compose_data):
        """Internal helper to verify all depends_on targets are defined in the same file."""
        services = compose_data.get("services", {})
        for svc_id, svc_def in services.items():
            depends_on = svc_def.get("depends_on", [])
            if isinstance(depends_on, dict):
                depends_on = list(depends_on.keys())
            for dep in depends_on:
                self.assertIn(
                    dep,
                    services,
                    f"Service '{svc_id}' depends on undefined service '{dep}'",
                )

    def test_composer_no_separate_search_container(self):
        """Requirement: There should no seperate search container for a ldm project which has used --sidecar."""
        meta = {
            "use_shared_search": "false",
            "tag": "2026.q1.4-lts",
            "container_name": "sidecar-test",
            "db_type": "hypersonic",
        }

        with patch("ldm_core.utils.safe_write_text") as mock_write:
            self.manager.composer.write_docker_compose(self.paths, meta)
            compose_data = yaml.safe_load(mock_write.call_args[0][1])

            # Requirement: Validate integrity of the generated file
            self._validate_compose_dependencies(compose_data)

            # Verify no 'search' service is present
            self.assertNotIn("search", compose_data["services"])

            # Verify Liferay is configured for sidecar
            liferay_service = compose_data["services"]["liferay"]
            liferay_env = liferay_service["environment"]
            self.assertIn(
                "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=true", liferay_env
            )
            self.assertIn(
                "LIFERAY_ELASTICSEARCH_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=false",
                liferay_env,
            )

            # Requirement: No dependency on search in sidecar mode
            depends_on = liferay_service.get("depends_on", [])
            self.assertNotIn("liferay-search-global", depends_on)

    def test_composer_shared_search_dependency(self):
        """Requirement: Shared infrastructure projects should NOT have a dependency on global search in the compose file
        (as it is an external service managed by the global infra stack)."""
        meta = {
            "use_shared_search": "true",
            "tag": "2026.q1.4-lts",
            "container_name": "shared-test",
            "db_type": "hypersonic",
        }

        with patch("ldm_core.utils.safe_write_text") as mock_write:
            self.manager.composer.write_docker_compose(self.paths, meta)
            compose_data = yaml.safe_load(mock_write.call_args[0][1])

            # Requirement: Validate integrity of the generated file
            self._validate_compose_dependencies(compose_data)

            liferay_service = compose_data["services"]["liferay"]
            depends_on = liferay_service.get("depends_on", [])
            # LDM-369: External services must not be in depends_on
            self.assertNotIn("liferay-search-global", depends_on)

    @patch("ldm_core.handlers.infra.InfraService.setup_global_search")
    @patch("ldm_core.handlers.config.ConfigService.update_portal_ext")
    def test_runtime_skips_global_search_setup(
        self, mock_update, mock_setup_global_search
    ):
        """Requirement: ldm should not interfer with any pre-existing search containers."""
        # Scenario: User runs with --search flag, but project is --sidecar
        self.manager.args.search = True
        project_meta = {
            "use_shared_search": "false",
            "host_name": "localhost",
            "ssl": "false",
            "container_name": "sidecar-test",
        }
        self.manager.meta = project_meta

        with (
            patch.object(self.manager.infra, "_ensure_network"),
            patch.object(self.manager.infra, "_ensure_docker_proxy"),
            patch.object(self.manager.infra, "manager"),
            patch(
                "ldm_core.handlers.runtime.get_compose_cmd",
                return_value=["docker", "compose"],
            ),
        ):
            # Mock manager.run_command to avoid actual docker calls
            self.manager.infra.manager.run_command = MagicMock(return_value="")

            self.manager.runtime.sync_stack(self.paths, project_meta, no_up=True)

            # Verify setup_global_search was NOT called
            mock_setup_global_search.assert_not_called()

    def test_config_skips_copying_search_configs(self):
        """Requirement: make sure the search .config files are not copied from the common folder."""
        project_meta = {"use_shared_search": "false"}

        common_dir = Path("/tmp/common")
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.glob") as mock_glob,
            patch("ldm_core.handlers.config.run_command") as mock_run,
        ):
            # Mock glob to return some elasticsearch configs
            es_config = MagicMock(spec=Path)
            es_config.name = "com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConfiguration.config"
            es_config.read_text.return_value = "content"
            mock_glob.return_value = [es_config]

            # Mock paths
            paths = self.manager.setup_paths("/tmp/proj")

            with patch("ldm_core.handlers.config.atomic_copy") as mock_copy:
                self.manager.config.sync_common_assets(paths, project_meta=project_meta)

                # Verify atomic_copy was NOT called for ES configs
                for call in mock_copy.call_args_list:
                    args, _ = call
                    self.assertNotIn("elasticsearch", str(args[0]).lower())

    def test_setup_global_search_skipped_in_sidecar_mode(self):
        """Requirement: sidecar project cannot be allowed to affect the shared infrastructure."""
        # Scenario 1: Project is sidecar - should skip
        self.manager.meta = {"use_shared_search": "false"}
        with patch.object(self.manager, "run_command") as mock_run:
            self.manager.infra.setup_global_search()
            mock_run.assert_not_called()

        # Scenario 2: Project is shared - should proceed
        self.manager.meta = {"use_shared_search": "true"}
        with (
            patch.object(self.manager, "run_command") as mock_run,
            patch("time.sleep"),
            patch("pathlib.Path.mkdir"),
            patch("ldm_core.utils.reclaim_volume_permissions"),
        ):
            # Setup side_effect to avoid recursion/repair loop
            # 1. exists check (run_command)
            # 2. docker run (run_command)
            # 3. health check (run_command)
            # 4. snapshot register (run_command)
            # 5. plugin list (run_command)
            # 6-9. plugin installs (run_command)
            # 10. docker restart
            # 11. health check after restart
            # 12. repo registration check

            mock_run.side_effect = [
                "",  # exists check (ps)
                "",  # docker run
                '{"cluster_name": "liferay-cluster"}',  # health check (curl)
                "OK",  # snapshot
                "plugins",  # plugin list
                "OK",
                "OK",
                "OK",
                "OK",  # plugin installs
                "OK",  # docker restart
                '{"cluster_name": "liferay-cluster"}',  # health check after restart
                "OK",  # repo registration check
            ]
            self.manager.get_container_status = MagicMock(return_value="running")  # type: ignore[method-assign]

            self.manager.infra.setup_global_search()

            # Verify it proceeded past the sidecar check
            self.assertTrue(mock_run.called)

        # Scenario 3: Force flag overrides sidecar check
        self.manager.meta = {"use_shared_search": "false"}
        with (
            patch.object(self.manager, "run_command") as mock_run,
            patch("time.sleep"),
            patch("pathlib.Path.mkdir"),
            patch("ldm_core.utils.reclaim_volume_permissions"),
        ):
            mock_run.side_effect = [
                "",  # exists
                "",  # run
                '{"cluster_name": "liferay-cluster"}',  # health
                "OK",  # snapshot
                "plugins",  # plugin list
                "OK",
                "OK",
                "OK",
                "OK",  # plugin installs
                "OK",  # docker restart
                '{"cluster_name": "liferay-cluster"}',  # health check after restart
                "OK",  # repo registration check
            ]
            self.manager.get_container_status = MagicMock(return_value="running")  # type: ignore[method-assign]

            self.manager.infra.setup_global_search(force=True)
            self.assertTrue(mock_run.called)

    def test_composer_sidecar_env_vars(self):
        """Verify specific environment variables for sidecar mode."""
        meta = {
            "use_shared_search": "false",
            "tag": "2026.q1.4-lts",
            "container_name": "env-test",
            "db_type": "hypersonic",
        }
        with patch("ldm_core.utils.safe_write_text") as mock_write:
            self.manager.composer.write_docker_compose(self.paths, meta)
            compose_data = yaml.safe_load(mock_write.call_args[0][1])
            env = compose_data["services"]["liferay"]["environment"]

            # Sidecar specific env vars
            self.assertIn(
                "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=true", env
            )
            self.assertIn(
                "LIFERAY_ELASTICSEARCH_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=false",
                env,
            )

            # Verify port injection via portal-ext.properties
            update_mock = self.manager.config.update_portal_ext
            self.assertTrue(getattr(update_mock, "called", False))

            # Ensure no connection URL to global search

            self.assertFalse(any("liferay-search-global" in e for e in env))

    def test_composer_shared_env_vars(self):
        """Verify specific environment variables for shared mode."""
        meta = {
            "use_shared_search": "true",
            "tag": "2026.q1.4-lts",
            "container_name": "shared-env-test",
            "db_type": "hypersonic",
        }
        with patch("ldm_core.utils.safe_write_text") as mock_write:
            self.manager.composer.write_docker_compose(self.paths, meta)
            compose_data = yaml.safe_load(mock_write.call_args[0][1])
            env = compose_data["services"]["liferay"]["environment"]

            self.assertIn(
                "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=false", env
            )
            self.assertIn(
                "LIFERAY_ELASTICSEARCH_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=true",
                env,
            )
            self.assertIn(
                "LIFERAY_ELASTICSEARCH_PERIOD_CONNECTION_PERIOD_URL=http://liferay-search-global:9200",
                env,
            )
            self.assertIn(
                "LIFERAY_ELASTICSEARCH_PERIOD_INDEX_PERIOD_NAME_PERIOD_PREFIX=ldm-shared-env-test-",
                env,
            )


if __name__ == "__main__":
    unittest.main()
