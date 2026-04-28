import os
import sys
from ldm_core.ui import UI
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerHandler
from ldm_core.handlers.runtime import RuntimeHandler
from ldm_core.handlers.assets import AssetHandler
from ldm_core.handlers.workspace import WorkspaceHandler
from ldm_core.handlers.snapshot import SnapshotHandler
from ldm_core.handlers.config import ConfigHandler
from ldm_core.handlers.diagnostics import DiagnosticsHandler
from ldm_core.handlers.cloud import CloudHandler
from ldm_core.handlers.license import LicenseHandler
from ldm_core.handlers.infra import InfraHandler
from ldm_core.handlers.dev import DevHandler
from ldm_core.constants import RUN_ATTRS


class LiferayManager(
    ComposerHandler,
    RuntimeHandler,
    AssetHandler,
    WorkspaceHandler,
    SnapshotHandler,
    ConfigHandler,
    DiagnosticsHandler,
    CloudHandler,
    LicenseHandler,
    InfraHandler,
    DevHandler,
    BaseHandler,
):
    """Orchestrator class for LDM, composed of multiple functional mixins."""

    def __init__(self, args):
        self.args = args
        self.verbose = getattr(args, "verbose", False)
        self.non_interactive = getattr(args, "non_interactive", False)

        # Automatic CI and TTY detection
        if (
            os.getenv("CI")
            or os.getenv("GITHUB_ACTIONS")
            or os.getenv("GITLAB_CI")
            or not sys.stdin.isatty()
        ):
            self.non_interactive = True

        # Synchronize global UI state
        UI.NON_INTERACTIVE = self.non_interactive
        UI.VERBOSE = self.verbose

        # Ensure standard attributes exist on args
        for attr in RUN_ATTRS:
            if not hasattr(args, attr):
                setattr(args, attr, None)
