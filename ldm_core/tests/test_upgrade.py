import tempfile
import unittest
from pathlib import Path
from typing import cast
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
        self.args.upgrade_db = False
        self.args.no_upgrade_db = False
        self.args.backup_on_upgrade = False
        self.args.no_backup_on_upgrade = False
        self.verbose = False
        self.non_interactive = False
        self.defaults = {}
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
        self.verify_runtime_environment = MagicMock()  # type: ignore[method-assign]

    def check_port(self, host, port):
        return True


class TestVersionUpgrades(unittest.TestCase):
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
    @patch("ldm_core.handlers.runtime.UI.confirm", return_value=True)
    @patch("ldm_core.handlers.runtime.UI.warning")
    def test_upgrade_triggers_backup_and_upgrade_on_confirm(
        self, mock_warn, mock_confirm, mock_compose_cmd
    ):
        project_meta = {
            "container_name": "test-project",
            "tag": "2024.q4.2",
            "db_type": "postgresql",
            "last_run_liferay_version": "2024.q4.1",
        }

        # Mock check if DB container is running (return False)
        # Mock run_command to simulate docker ps and docker compose up
        run_cmd_mock = cast(MagicMock, self.manager.run_command)
        run_cmd_mock.side_effect = [
            False,  # docker ps check
            "",  # docker compose up db
            "",  # docker compose config validation
        ]

        with (
            patch.object(self.manager.snapshot, "cmd_snapshot") as mock_snapshot,
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=False
            ),
        ):
            self.manager.runtime.cmd_run(
                project_id="test-project",
                no_up=True,
                show_summary=False,
                paths=self.paths,
                project_meta=project_meta,
            )

            # Verify UI warnings and confirmations were triggered
            mock_confirm.assert_any_call(
                "Would you like to take a database backup snapshot before proceeding?",
                default=True,
            )
            mock_confirm.assert_any_call(
                "Do you want to run Liferay's database auto-upgrade tool on startup?",
                default=True,
            )

            # Verify snapshot was triggered
            mock_snapshot.assert_called_once_with(
                "test-project", name="Pre-upgrade snapshot to 2024.q4.2"
            )

            # Verify auto-upgrade env var was injected in compose write
            write_mock = cast(MagicMock, self.manager.composer.write_docker_compose)
            write_mock.assert_called_once()
            called_env = write_mock.call_args[1].get("liferay_env")
            self.assertIn(
                "LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true",
                called_env,
            )

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.confirm", return_value=False)
    @patch("ldm_core.handlers.runtime.UI.warning")
    def test_upgrade_skips_backup_and_upgrade_on_decline(
        self, mock_warn, mock_confirm, mock_compose_cmd
    ):
        project_meta = {
            "container_name": "test-project",
            "tag": "2024.q4.2",
            "db_type": "postgresql",
            "last_run_liferay_version": "2024.q4.1",
        }

        with patch.object(self.manager.snapshot, "cmd_snapshot") as mock_snapshot:
            self.manager.runtime.cmd_run(
                project_id="test-project",
                no_up=True,
                show_summary=False,
                paths=self.paths,
                project_meta=project_meta,
            )

            # Verify snapshot was NOT triggered
            mock_snapshot.assert_not_called()

            # Verify auto-upgrade env var was NOT injected in compose write
            write_mock = cast(MagicMock, self.manager.composer.write_docker_compose)
            write_mock.assert_called_once()
            called_env = write_mock.call_args[1].get("liferay_env")
            self.assertNotIn(
                "LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true",
                called_env,
            )

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.confirm")
    @patch("ldm_core.handlers.runtime.UI.warning")
    def test_upgrade_obey_no_flags(self, mock_warn, mock_confirm, mock_compose_cmd):
        project_meta = {
            "container_name": "test-project",
            "tag": "2024.q4.2",
            "db_type": "postgresql",
            "last_run_liferay_version": "2024.q4.1",
        }
        self.manager.args.no_backup_on_upgrade = True
        self.manager.args.no_upgrade_db = True

        with patch.object(self.manager.snapshot, "cmd_snapshot") as mock_snapshot:
            self.manager.runtime.cmd_run(
                project_id="test-project",
                no_up=True,
                show_summary=False,
                paths=self.paths,
                project_meta=project_meta,
            )

            # No prompts should be shown
            mock_confirm.assert_not_called()
            mock_snapshot.assert_not_called()

            # Verify auto-upgrade env var was NOT injected
            write_mock = cast(MagicMock, self.manager.composer.write_docker_compose)
            write_mock.assert_called_once()
            called_env = write_mock.call_args[1].get("liferay_env")
            self.assertNotIn(
                "LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true",
                called_env,
            )

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.confirm")
    @patch("ldm_core.handlers.runtime.UI.warning")
    def test_upgrade_obey_yes_flags(self, mock_warn, mock_confirm, mock_compose_cmd):
        project_meta = {
            "container_name": "test-project",
            "tag": "2024.q4.2",
            "db_type": "postgresql",
            "last_run_liferay_version": "2024.q4.1",
        }
        self.manager.args.backup_on_upgrade = True
        self.manager.args.upgrade_db = True

        # Mock DB container check to return True
        run_cmd_mock = cast(MagicMock, self.manager.run_command)
        run_cmd_mock.return_value = True

        with patch.object(self.manager.snapshot, "cmd_snapshot") as mock_snapshot:
            self.manager.runtime.cmd_run(
                project_id="test-project",
                no_up=True,
                show_summary=False,
                paths=self.paths,
                project_meta=project_meta,
            )

            # No prompts should be shown because overrides are active
            mock_confirm.assert_not_called()
            mock_snapshot.assert_called_once_with(
                "test-project", name="Pre-upgrade snapshot to 2024.q4.2"
            )

            # Verify auto-upgrade env var WAS injected
            write_mock = cast(MagicMock, self.manager.composer.write_docker_compose)
            write_mock.assert_called_once()
            called_env = write_mock.call_args[1].get("liferay_env")
            self.assertIn(
                "LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true",
                called_env,
            )

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.confirm")
    @patch("ldm_core.handlers.runtime.UI.warning")
    def test_non_interactive_skips_prompts_and_defaults_false(
        self, mock_warn, mock_confirm, mock_compose_cmd
    ):
        project_meta = {
            "container_name": "test-project",
            "tag": "2024.q4.2",
            "db_type": "postgresql",
            "last_run_liferay_version": "2024.q4.1",
        }
        self.manager.non_interactive = True

        with patch.object(self.manager.snapshot, "cmd_snapshot") as mock_snapshot:
            self.manager.runtime.cmd_run(
                project_id="test-project",
                no_up=True,
                show_summary=False,
                paths=self.paths,
                project_meta=project_meta,
            )

            # No prompts should be shown, backup should be skipped, upgrade should be disabled
            mock_confirm.assert_not_called()
            mock_snapshot.assert_not_called()

            write_mock = cast(MagicMock, self.manager.composer.write_docker_compose)
            write_mock.assert_called_once()
            called_env = write_mock.call_args[1].get("liferay_env")
            self.assertNotIn(
                "LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true",
                called_env,
            )
