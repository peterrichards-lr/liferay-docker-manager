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

        with patch("ldm_core.utils.resolve_dependency_version", return_value="8.17"):
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

        with patch("ldm_core.handlers.runtime.UI.info"):
            self.manager.runtime.sync_stack(
                self.paths, project_meta, no_up=False, no_wait=True, show_summary=False
            )

        mock_die.assert_not_called()
        self.assertEqual(project_meta.get("last_run_liferay_version"), "2023.q1.3")


class TestElasticsearchVersionDetection(unittest.TestCase):
    """Tests that ES major version detection uses resolve_dependency_version() correctly.

    Regression tests for Issue #602: substring matching on tag would false-match
    future Liferay tags like '2027.3.0-lts' as ES7.
    """

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
    @patch("ldm_core.handlers.runtime.UI.info")
    def test_future_tag_with_73_substring_resolves_es8(
        self, mock_info, mock_compose_cmd
    ):
        """A tag like '2027.3.0-lts' containing '7.3' must NOT be treated as ES7.

        Before fix: '7.3' in '2027.3.0-lts' → True → current_es_major = '7' (wrong).
        After fix: resolve_dependency_version('2027.3.0-lts', 'elasticsearch') → '8.17'
                   → current_es_major = '8' (correct).
        Verified by asserting NO ES7 → ES8 upgrade wipe triggers (since last_run == '8' implied).
        """
        project_meta = {
            "container_name": "test-project",
            "tag": "2027.3.0-lts",
            "db_type": "hypersonic",
            # No last_run_elasticsearch_major: simulate fresh install (no wipe expected)
        }
        with (
            patch(
                "ldm_core.utils.resolve_dependency_version", return_value="8.17"
            ) as mock_rdv,
            patch("ldm_core.utils.safe_rmtree") as mock_rmtree,
        ):
            self.manager.runtime.sync_stack(
                self.paths, project_meta, no_up=True, show_summary=False
            )
            # Verify resolve_dependency_version was called with "elasticsearch"
            mock_rdv.assert_called()
            es_calls = [c for c in mock_rdv.call_args_list if "elasticsearch" in str(c)]
            self.assertGreater(
                len(es_calls),
                0,
                f"Expected resolve_dependency_version('...', 'elasticsearch') call; got: {mock_rdv.call_args_list}",
            )
            # No ES data directory wipe should occur (we're starting fresh on ES8)
            mock_rmtree.assert_not_called()

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.warning")
    @patch("ldm_core.handlers.runtime.UI.info")
    @patch("ldm_core.utils.safe_rmtree")
    def test_legacy_7x_tag_resolves_es7(
        self, mock_rmtree, mock_info, mock_warning, mock_compose_cmd
    ):
        """A classic Liferay 7.3.x tag must still resolve to ES7.

        The new implementation delegates to resolve_dependency_version which
        returns a '7.x' string for old-style tags; the major '7' is extracted.
        Verified by asserting that no ES8 upgrade-wipe occurs when last_run was also ES7.
        """
        project_meta = {
            "container_name": "test-project",
            "tag": "7.3.10-ga1",
            "db_type": "hypersonic",
            "last_run_elasticsearch_major": "7",
        }
        # resolve_dependency_version returns ES7 for 7.x Liferay tags
        with patch("ldm_core.utils.resolve_dependency_version", return_value="7.17"):
            self.manager.runtime.sync_stack(
                self.paths, project_meta, no_up=True, show_summary=False
            )
        # Major stayed at "7" → no upgrade wipe triggered
        mock_rmtree.assert_not_called()
        # No "version changed" warning
        upgrade_warnings = [
            c
            for c in mock_warning.call_args_list
            if "Elasticsearch version changed" in str(c)
        ]
        self.assertEqual(
            len(upgrade_warnings),
            0,
            "No ES upgrade warning expected when major stays at 7",
        )

    @patch(
        "ldm_core.handlers.runtime.get_compose_cmd", return_value=["docker", "compose"]
    )
    @patch("ldm_core.handlers.runtime.UI.info")
    def test_no_tag_defaults_to_es8(self, mock_info, mock_compose_cmd):
        """When tag is None/empty and resolve returns None, ES major must default to '8'.

        Verified by asserting no ES7-specific wipe occurs on a fresh install.
        """
        project_meta = {
            "container_name": "test-project",
            "tag": None,
            "db_type": "hypersonic",
        }
        with (
            patch("ldm_core.utils.resolve_dependency_version", return_value=None),
            patch("ldm_core.utils.safe_rmtree") as mock_rmtree,
        ):
            self.manager.runtime.sync_stack(
                self.paths, project_meta, no_up=True, show_summary=False
            )
        # No wipe: default ES major is "8", matches no previous version
        mock_rmtree.assert_not_called()
