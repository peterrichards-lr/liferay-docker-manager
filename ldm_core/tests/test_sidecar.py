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
        self.args = MagicMock()
        self.args.search = False
        self.verbose = False
        self.non_interactive = True
        self.composer = ComposerService(self)
        self.infra = InfraService(self)
        self.runtime = RuntimeService(self)
        self.config = ConfigService(self)
        self.run_command = MagicMock(return_value="")  # type: ignore[method-assign]

    def setup_paths(self, root):
        root = Path(root)
        return {
            "root": root,
            "files": root / "files",
            "configs": root / "osgi" / "configs",
            "data": root / "data",
            "deploy": root / "deploy",
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
        self.project_root = Path("/tmp/sidecar-project")
        self.paths = self.manager.setup_paths(self.project_root)

    def test_composer_no_separate_search_container(self):
        """Requirement: There should no seperate search container for a ldm project which has used --sidecar."""
        meta = {
            "use_shared_search": "false",
            "tag": "7.4.13-u100",
            "container_name": "sidecar-test",
            "db_type": "hypersonic",
        }

        with patch("ldm_core.utils.safe_write_text") as mock_write:
            self.manager.composer.write_docker_compose(self.paths, meta)
            compose_data = yaml.safe_load(mock_write.call_args[0][1])

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
        """Requirement: Shared infrastructure projects should have a dependency on global search."""
        meta = {
            "use_shared_search": "true",
            "tag": "7.4.13-u100",
            "container_name": "shared-test",
            "db_type": "hypersonic",
        }

        with patch("ldm_core.utils.safe_write_text") as mock_write:
            self.manager.composer.write_docker_compose(self.paths, meta)
            compose_data = yaml.safe_load(mock_write.call_args[0][1])

            liferay_service = compose_data["services"]["liferay"]
            depends_on = liferay_service.get("depends_on", [])
            self.assertIn("liferay-search-global", depends_on)

    @patch("ldm_core.handlers.infra.InfraService.setup_global_search")
    def test_runtime_skips_global_search_setup(self, mock_setup_global_search):
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

            with patch("ldm_core.handlers.config.safe_copy") as mock_copy:
                self.manager.config.sync_common_assets(paths, project_meta=project_meta)

                # Verify safe_copy was NOT called for ES configs
                for call in mock_copy.call_args_list:
                    args, _ = call
                    self.assertNotIn("elasticsearch", str(args[0]).lower())

    def test_setup_global_search_skipped_in_sidecar_mode(self):
        """Requirement: sidecar project cannot be allowed to affect the shared infrastructure."""
        # Scenario: Project is sidecar
        self.manager.meta = {"use_shared_search": "false"}

        with patch.object(self.manager, "run_command") as mock_run:
            self.manager.infra.setup_global_search()

            # Verify no docker commands were run (early exit)
            mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
