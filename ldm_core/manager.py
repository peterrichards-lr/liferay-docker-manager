import os

from ldm_core.constants import RUN_ATTRS
from ldm_core.defaults import DefaultsManager
from ldm_core.handlers.assets import AssetService
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.cloud import CloudService
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.config import ConfigService
from ldm_core.handlers.dashboard import DashboardService
from ldm_core.handlers.dev import DevService
from ldm_core.handlers.diagnostics import DiagnosticsService
from ldm_core.handlers.infra import InfraService
from ldm_core.handlers.license import LicenseService
from ldm_core.handlers.runtime import RuntimeService
from ldm_core.handlers.snapshot import SnapshotService
from ldm_core.handlers.workspace import WorkspaceService
from ldm_core.ui import UI


class LiferayManager(
    BaseHandler,
):
    """Orchestrator class for LDM, composed of multiple functional mixins."""

    def __init__(self, args):
        self.args = args
        self.verbose = getattr(args, "verbose", False)
        self.info_mode = getattr(args, "info", False)
        self.quiet_mode = getattr(args, "quiet", False)
        self.non_interactive = getattr(args, "non_interactive", False)
        self.dry_run = (
            getattr(args, "dry_run", False)
            or os.environ.get("LDM_DRY_RUN", "").lower() == "true"
        )
        self.defaults = DefaultsManager()

        # Services via Composition
        self.license = LicenseService(self)
        self.assets = AssetService(self)
        self.config = ConfigService(self)
        self.dashboard = DashboardService(self)
        self.dev = DevService(self)
        self.infra = InfraService(self)
        self.cloud = CloudService(self)
        self.diagnostics = DiagnosticsService(self)
        self.snapshot = SnapshotService(self)
        self.workspace = WorkspaceService(self)
        self.composer = ComposerService(self)
        self.runtime = RuntimeService(self)

        from ldm_core.handlers.share import ShareService

        self.share = ShareService(self)

        # Automatic CI detection
        if os.getenv("CI") or os.getenv("GITHUB_ACTIONS") or os.getenv("GITLAB_CI"):
            self.non_interactive = True

        # Synchronize global UI state
        UI.NON_INTERACTIVE = self.non_interactive
        UI.VERBOSE = self.verbose
        UI.INFO_MODE = self.info_mode
        UI.QUIET_MODE = self.quiet_mode

        # Ensure standard attributes exist on args
        for attr in RUN_ATTRS:
            if not hasattr(args, attr):
                setattr(args, attr, None)

    # --- Facade Methods (Delegating to Services) ---
    def sync_stack(self, *args, **kwargs):
        return self.runtime.sync_stack(*args, **kwargs)

    def get_resource_path(self, filename):
        from ldm_core.utils import get_resource_path

        return get_resource_path(filename)

    def write_docker_compose(self, *args, **kwargs):
        return self.composer.write_docker_compose(*args, **kwargs)

    def cmd_run(self, *args, **kwargs):
        return self.runtime.cmd_run(*args, **kwargs)

    def cmd_stop(self, *args, **kwargs):
        return self.runtime.cmd_stop(*args, **kwargs)

    def cmd_restart(self, *args, **kwargs):
        return self.runtime.cmd_restart(*args, **kwargs)

    def cmd_down(self, *args, **kwargs):
        return self.runtime.cmd_down(*args, **kwargs)

    def cmd_logs(self, *args, **kwargs):
        return self.runtime.cmd_logs(*args, **kwargs)

    def cmd_deploy(self, *args, **kwargs):
        return self.runtime.cmd_deploy(*args, **kwargs)

    def cmd_shell(self, *args, **kwargs):
        return self.runtime.cmd_shell(*args, **kwargs)

    def cmd_scale(self, *args, **kwargs):
        return self.runtime.cmd_scale(*args, **kwargs)

    def cmd_migrate_search(self, *args, **kwargs):
        return self.runtime.cmd_migrate_search(*args, **kwargs)

    def cmd_snapshot(self, *args, **kwargs):
        return self.snapshot.cmd_snapshot(*args, **kwargs)

    def cmd_restore(self, *args, **kwargs):
        return self.snapshot.cmd_restore(*args, **kwargs)

    def cmd_doctor(self, *args, **kwargs):
        return self.diagnostics.cmd_doctor(*args, **kwargs)

    def cmd_wait(self, *args, **kwargs):
        return self.runtime.cmd_wait(*args, **kwargs)

    def cmd_prune(self, *args, **kwargs):
        return self.diagnostics.cmd_prune(*args, **kwargs)

    def cmd_config(self, *args, **kwargs):
        return self.config.cmd_config(*args, **kwargs)

    def cmd_cache(self, *args, **kwargs):
        return self.diagnostics.cmd_cache(*args, **kwargs)

    def cmd_browser(self, *args, **kwargs):
        return self.runtime.cmd_browser(*args, **kwargs)

    def cmd_reset(self, *args, **kwargs):
        return self.runtime.cmd_reset(*args, **kwargs)

    def cmd_hydrate(self, *args, **kwargs):
        return self.cloud.cmd_hydrate(*args, **kwargs)

    def cmd_import(self, *args, **kwargs):
        return self.workspace.cmd_import(*args, **kwargs)

    def cmd_init(self, *args, **kwargs):
        return self.workspace.cmd_init(*args, **kwargs)

    def cmd_init_from(self, *args, **kwargs):
        return self.workspace.cmd_init_from(*args, **kwargs)

    def cmd_init_common(self, *args, **kwargs):
        return self.config.cmd_init_common(*args, **kwargs)

    def cmd_env(self, *args, **kwargs):
        return self.config.cmd_env(*args, **kwargs)

    def cmd_log_level(self, *args, **kwargs):
        return self.config.cmd_log_level(*args, **kwargs)

    def cmd_dev_setup(self, *args, **kwargs):
        return self.dev.cmd_dev_setup(*args, **kwargs)

    def cmd_version(self, *args, **kwargs):
        return self.dev.cmd_version(*args, **kwargs)

    def cmd_list(self, *args, **kwargs):
        return self.diagnostics.cmd_list(*args, **kwargs)

    def cmd_status(self, *args, **kwargs):
        return self.diagnostics.cmd_status(*args, **kwargs)

    def cmd_dashboard(self, *args, **kwargs):
        return self.dashboard.cmd_dashboard(*args, **kwargs)

    @property
    def ai(self):
        if getattr(self, "_ai", None) is None:
            from ldm_core.handlers.ai import AiService

            self._ai = AiService(self)
        return self._ai

    @property
    def mcp(self):
        if getattr(self, "_mcp", None) is None:
            from ldm_core.handlers.mcp import McpService

            self._mcp = McpService(self)
        return self._mcp

    def cmd_mcp(self, *args, **kwargs):
        return self.mcp.cmd_mcp(*args, **kwargs)

    def cmd_ai(self, *args, **kwargs):
        return self.ai.cmd_ai(*args, **kwargs)

    def cmd_completion(self, *args, **kwargs):
        return self.diagnostics.cmd_completion(*args, **kwargs)

    def cmd_man(self, *args, **kwargs):
        return self.diagnostics.cmd_man(*args, **kwargs)

    def cmd_infra_setup(self, *args, **kwargs):
        return self.infra.cmd_infra_setup(*args, **kwargs)

    def cmd_infra_down(self, *args, **kwargs):
        return self.infra.cmd_infra_down(*args, **kwargs)

    def cmd_infra_restart(self, *args, **kwargs):
        return self.infra.cmd_infra_restart(*args, **kwargs)

    def cmd_system(self, *args, **kwargs):
        return self.infra.cmd_system(*args, **kwargs)

    def cmd_system_relocate(self, *args, **kwargs):
        return self.infra.cmd_system_relocate(*args, **kwargs)

    def check_mkcert(self, *args, **kwargs):
        return self.diagnostics.check_mkcert(*args, **kwargs)

    def _wait_for_ready(self, *args, **kwargs):
        return self.runtime._wait_for_ready(*args, **kwargs)

    def _ensure_seeded(self, *args, **kwargs):
        return self.assets._ensure_seeded(*args, **kwargs)

    def _fetch_seed(self, *args, **kwargs):
        return self.assets._fetch_seed(*args, **kwargs)

    def _apply_version_update(self, *args, **kwargs):
        return self.dev._apply_version_update(*args, **kwargs)

    def _ensure_dev_env(self, *args, **kwargs):
        return self.dev._ensure_dev_env(*args, **kwargs)

    def _ensure_network(self, *args, **kwargs):
        return self.infra._ensure_network(*args, **kwargs)

    def _ensure_docker_proxy(self, *args, **kwargs):
        return self.infra._ensure_docker_proxy(*args, **kwargs)

    def _get_infra_env(self, *args, **kwargs):
        return self.infra._get_infra_env(*args, **kwargs)

    def thaw_elasticsearch(self, *args, **kwargs):
        return self.infra.thaw_elasticsearch(*args, **kwargs)

    def setup_infrastructure(self, *args, **kwargs):
        return self.infra.setup_infrastructure(*args, **kwargs)

    def setup_ssl(self, *args, **kwargs):
        return self.infra.setup_ssl(*args, **kwargs)

    def setup_global_search(self, *args, **kwargs):
        return self.infra.setup_global_search(*args, **kwargs)

    def update_portal_ext(self, *args, **kwargs):
        return self.config.update_portal_ext(*args, **kwargs)

    def _get_properties(self, *args, **kwargs):
        return self.config._get_properties(*args, **kwargs)

    def _is_ssl_active(self, *args, **kwargs):
        return self.composer._is_ssl_active(*args, **kwargs)

    def validate_lcp_json(self, *args, **kwargs):
        return self.diagnostics.validate_lcp_json(*args, **kwargs)

    def _check_container_health_logs(self, *args, **kwargs):
        return self.diagnostics._check_container_health_logs(*args, **kwargs)

    def _check_liferay_health_logs(self, *args, **kwargs):
        return self.diagnostics._check_liferay_health_logs(*args, **kwargs)

    def _check_elasticsearch_watermarks(self, *args, **kwargs):
        return self.diagnostics._check_elasticsearch_watermarks(*args, **kwargs)

    def _generate_debug_bundle(self, *args, **kwargs):
        return self.diagnostics._generate_debug_bundle(*args, **kwargs)

    def _restore_from_cloud_layout(self, *args, **kwargs):
        return self.snapshot._restore_from_cloud_layout(*args, **kwargs)

    def _hydrate_from_workspace(self, *args, **kwargs):
        return self.workspace._hydrate_from_workspace(*args, **kwargs)

    def _parse_lcp_json(self, *args, **kwargs):
        return self.workspace._parse_lcp_json(*args, **kwargs)

    def scan_standalone_services(self, *args, **kwargs):
        return self.workspace.scan_standalone_services(*args, **kwargs)

    def scan_client_extensions(self, *args, **kwargs):
        return self.workspace.scan_client_extensions(*args, **kwargs)

    def _scan_extension_metadata(self, *args, **kwargs):
        return self.workspace._scan_extension_metadata(*args, **kwargs)

    def _parse_client_extension_yaml(self, *args, **kwargs):
        return self.workspace._parse_client_extension_yaml(*args, **kwargs)

    def _parse_client_extension_config_json(self, *args, **kwargs):
        return self.workspace._parse_client_extension_config_json(*args, **kwargs)

    def _sync_cx_artifact(self, *args, **kwargs):
        return self.workspace._sync_cx_artifact(*args, **kwargs)

    def sync_common_assets(self, *args, **kwargs):
        return self.config.sync_common_assets(*args, **kwargs)

    def sync_logging(self, *args, **kwargs):
        return self.config.sync_logging(*args, **kwargs)

    def get_samples_tag(self, *args, **kwargs):
        return self.config.get_samples_tag(*args, **kwargs)

    def get_samples_db_type(self, *args, **kwargs):
        return self.config.get_samples_db_type(*args, **kwargs)

    def get_samples_root(self, *args, **kwargs):
        return self.config.get_samples_root(*args, **kwargs)

    def download_samples(self, *args, **kwargs):
        return self.assets.download_samples(*args, **kwargs)
