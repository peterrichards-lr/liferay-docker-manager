import os

from ldm_core.constants import RUN_ATTRS
from ldm_core.defaults import DefaultsManager
from ldm_core.handlers.assets import AssetService
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.cloud import CloudService
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.config import ConfigService
from ldm_core.handlers.dashboard import DashboardService
from ldm_core.handlers.database import DatabaseService
from ldm_core.handlers.dev import DevService
from ldm_core.handlers.diagnostics import DiagnosticsService
from ldm_core.handlers.infra import InfraService
from ldm_core.handlers.license import LicenseService
from ldm_core.handlers.runtime import RuntimeService
from ldm_core.handlers.snapshot import SnapshotService
from ldm_core.handlers.system import SystemService
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
        self.database = DatabaseService(self)
        self.runtime = RuntimeService(self)
        self.system = SystemService(self)

        from ldm_core.handlers.share import ShareService
        from ldm_core.handlers.tray import TrayService

        self.share = ShareService(self)
        self.tray = TrayService(self)

        # Automatic CI detection
        if os.getenv("CI") or os.getenv("GITHUB_ACTIONS") or os.getenv("GITLAB_CI"):
            self.non_interactive = True

        # Synchronize global UI state
        UI.NON_INTERACTIVE = self.non_interactive
        UI.VERBOSE = self.verbose
        UI.INFO_MODE = self.info_mode
        UI.QUIET_MODE = self.quiet_mode

        # Resolve NO_COLOR and NO_UNICODE from CLI, Config, or Env
        no_color_cfg = self.defaults.get("no_color")
        no_color_val = (
            getattr(args, "no_color", False)
            or "NO_COLOR" in os.environ
            or "LDM_NO_COLOR" in os.environ
            or (isinstance(no_color_cfg, str) and no_color_cfg.lower() == "true")
            or (isinstance(no_color_cfg, bool) and no_color_cfg)
        )
        UI.NO_COLOR = bool(no_color_val)

        no_unicode_cfg = self.defaults.get("no_unicode")
        no_unicode_val = (
            getattr(args, "no_unicode", False)
            or "LDM_NO_UNICODE" in os.environ
            or (isinstance(no_unicode_cfg, str) and no_unicode_cfg.lower() == "true")
            or (isinstance(no_unicode_cfg, bool) and no_unicode_cfg)
        )
        UI.NO_UNICODE = bool(no_unicode_val)

        # Ensure standard attributes exist on args
        for attr in RUN_ATTRS:
            if not hasattr(args, attr):
                setattr(args, attr, None)

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

    def get_resource_path(self, filename):
        from ldm_core.utils import get_resource_path

        return get_resource_path(filename)
