import os

from ldm_core.constants import RUN_ATTRS
from ldm_core.handlers.assets import AssetService
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.cloud import CloudService
from ldm_core.handlers.composer import ComposerHandler
from ldm_core.handlers.config import ConfigService
from ldm_core.handlers.dev import DevService
from ldm_core.handlers.diagnostics import DiagnosticsService
from ldm_core.handlers.infra import InfraService
from ldm_core.handlers.license import LicenseService
from ldm_core.handlers.runtime import RuntimeHandler
from ldm_core.handlers.snapshot import SnapshotService
from ldm_core.handlers.workspace import WorkspaceHandler
from ldm_core.ui import UI


class LiferayManager(
    ComposerHandler,
    RuntimeHandler,
    WorkspaceHandler,
    BaseHandler,
):
    """Orchestrator class for LDM, composed of multiple functional mixins."""

    def __init__(self, args):
        self.args = args
        self.verbose = getattr(args, "verbose", False)
        self.info_mode = getattr(args, "info", False)
        self.non_interactive = getattr(args, "non_interactive", False)

        # Services via Composition
        self.license = LicenseService(self)
        self.assets = AssetService(self)
        self.config = ConfigService(self)
        self.dev = DevService(self)
        self.infra = InfraService(self)
        self.cloud = CloudService(self)
        self.diagnostics = DiagnosticsService(self)
        self.snapshot = SnapshotService(self)

        # Automatic CI detection
        if os.getenv("CI") or os.getenv("GITHUB_ACTIONS") or os.getenv("GITLAB_CI"):
            self.non_interactive = True

        # Synchronize global UI state
        UI.NON_INTERACTIVE = self.non_interactive
        UI.VERBOSE = self.verbose
        UI.INFO_MODE = self.info_mode

        # Ensure standard attributes exist on args
        for attr in RUN_ATTRS:
            if not hasattr(args, attr):
                setattr(args, attr, None)
