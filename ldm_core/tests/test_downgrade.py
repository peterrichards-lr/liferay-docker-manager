import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.assets import AssetService
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.config import ConfigService
from ldm_core.handlers.diagnostics import DiagnosticsService
from ldm_core.handlers.infra import InfraService
from ldm_core.handlers.license import LicenseService
from ldm_core.handlers.runtime import RuntimeService
from ldm_core.handlers.snapshot import SnapshotService
from ldm_core.handlers.workspace import WorkspaceService


class MockManager(BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.args.search = False
        self.args.force_downgrade = False
        self.verbose = False
        self.non_interactive = True
        self.license = LicenseService(self)
        self.assets = AssetService(self)
        self.config = ConfigService(self)
        self.infra = InfraService(self)
        self.diagnostics = DiagnosticsService(self)
        self.snapshot = SnapshotService(self)
        self.workspace = WorkspaceService(self)
        self.composer = ComposerService(self)
        self.runtime = RuntimeService(self)

        self.run_command = MagicMock()  # type: ignore[method-assign]
        self.write_docker_compose = MagicMock()  # type: ignore[method-assign]
        self.get_container_status = MagicMock()  # type: ignore[method-assign]
        self.update_portal_ext = MagicMock()  # type: ignore[method-assign]

        # Mock side-effects
        self.config.sync_common_assets = MagicMock()  # type: ignore[method-assign]
        self.config.sync_logging = MagicMock()  # type: ignore[method-assign]
        self.composer.write_docker_compose = MagicMock()  # type: ignore[method-assign]
        self.infra.setup_infrastructure = MagicMock()  # type: ignore[method-assign]
        self.infra._ensure_network = MagicMock()  # type: ignore[method-assign]

    def check_port(self, host, port):
        return True


class TestDowngradePrevention(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name)
        self.paths = self.manager.setup_paths(self.root_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.die", side_effect=SystemExit(1))
    def test_liferay_downgrade_fails(self, mock_die, mock_compose_cmd):
        project_meta = {
            "container_name": "test-project",
            "tag": "2023.q1.3",
            "db_type": "hypersonic",
            "last_run_liferay_version": "2024.q4.1",
        }
        self.manager.args.force_downgrade = False

        with self.assertRaises(SystemExit):
            self.manager.runtime.sync_stack(
                self.paths, project_meta, no_up=True, show_summary=False
            )

        mock_die.assert_called_once()
        self.assertIn(
            "Downgrade detected: Liferay version tag changed", mock_die.call_args[0][0]
        )

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.die", side_effect=SystemExit(1))
    def test_postgres_downgrade_fails(self, mock_die, mock_compose_cmd):
        project_meta = {
            "container_name": "test-project",
            "tag": "2023.q1.3",
            "db_type": "postgresql",
            "last_run_liferay_version": "2023.q1.3",
            "last_run_postgres_version": "17",
        }
        self.manager.args.force_downgrade = False

        with patch("ldm_core.utils.resolve_dependency_version", return_value="16"):
            with self.assertRaises(SystemExit):
                self.manager.runtime.sync_stack(
                    self.paths, project_meta, no_up=True, show_summary=False
                )

        mock_die.assert_called_once()
        self.assertIn(
            "Downgrade detected: PostgreSQL version changed", mock_die.call_args[0][0]
        )

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.die", side_effect=SystemExit(1))
    def test_postgres_major_upgrade_fails(self, mock_die, mock_compose_cmd):
        project_meta = {
            "container_name": "test-project",
            "tag": "2024.q4.1",
            "db_type": "postgresql",
            "last_run_liferay_version": "2023.q1.3",
            "last_run_postgres_version": "15",
        }
        self.manager.args.force_downgrade = False

        with patch("ldm_core.utils.resolve_dependency_version", return_value="16"):
            with self.assertRaises(SystemExit):
                self.manager.runtime.sync_stack(
                    self.paths, project_meta, no_up=True, show_summary=False
                )

        mock_die.assert_called_once()
        self.assertIn(
            "Incompatible database directory: PostgreSQL version changed",
            mock_die.call_args[0][0],
        )

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.die", side_effect=SystemExit(1))
    def test_mysql_major_upgrade_fails(self, mock_die, mock_compose_cmd):
        project_meta = {
            "container_name": "test-project",
            "tag": "2024.q4.1",
            "db_type": "mysql",
            "last_run_liferay_version": "2023.q1.3",
            "last_run_mysql_version": "5.7",
        }
        self.manager.args.force_downgrade = False

        with patch("ldm_core.utils.resolve_dependency_version", return_value="8.0"):
            with self.assertRaises(SystemExit):
                self.manager.runtime.sync_stack(
                    self.paths, project_meta, no_up=True, show_summary=False
                )

        mock_die.assert_called_once()
        self.assertIn(
            "Incompatible database directory: MYSQL version changed",
            mock_die.call_args[0][0],
        )

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.warning")
    @patch("ldm_core.handlers.runtime.UI.info")
    @patch("ldm_core.utils.safe_rmtree")
    def test_elasticsearch_major_upgrade_wipes_indices(
        self, mock_rmtree, mock_info, mock_warning, mock_compose_cmd
    ):
        project_meta = {
            "container_name": "test-project",
            "tag": "2024.q4.1",
            "db_type": "hypersonic",
            "last_run_liferay_version": "2023.q1.3",
            "last_run_elasticsearch_major": "7",
        }
        self.manager.args.force_downgrade = False

        # Create the Elasticsearch data directory temporarily to trigger the wipe
        es_path = self.paths["data"] / "elasticsearch8"
        es_path.mkdir(parents=True, exist_ok=True)

        self.manager.runtime.sync_stack(
            self.paths, project_meta, no_up=True, show_summary=False
        )

        mock_warning.assert_called_once()
        self.assertIn(
            "Elasticsearch version changed from major '7' to '8'",
            mock_warning.call_args[0][0],
        )
        mock_rmtree.assert_called_once_with(es_path)

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.die", side_effect=SystemExit(1))
    def test_force_downgrade_bypasses(self, mock_die, mock_compose_cmd):
        project_meta = {
            "container_name": "test-project",
            "tag": "2023.q1.3",
            "db_type": "hypersonic",
            "last_run_liferay_version": "2024.q4.1",
        }
        self.manager.args.force_downgrade = True

        with patch("ldm_core.handlers.runtime.UI.info") as mock_info:
            self.manager.runtime.sync_stack(
                self.paths, project_meta, no_up=False, no_wait=True, show_summary=False
            )

        mock_die.assert_not_called()
        self.assertEqual(project_meta.get("last_run_liferay_version"), "2023.q1.3")
