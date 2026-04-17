import os
import sys
from ldm_core.ui import UI
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.stack import StackHandler
from ldm_core.handlers.workspace import WorkspaceHandler
from ldm_core.handlers.snapshot import SnapshotHandler
from ldm_core.handlers.config import ConfigHandler
from ldm_core.handlers.diagnostics import DiagnosticsHandler
from ldm_core.handlers.cloud import CloudHandler
from ldm_core.handlers.license import LicenseHandler


class LiferayManager(
    BaseHandler,
    StackHandler,
    WorkspaceHandler,
    SnapshotHandler,
    ConfigHandler,
    DiagnosticsHandler,
    CloudHandler,
    LicenseHandler,
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

        # Ensure standard attributes exist on args
        run_attrs = [
            "tag",
            "tag_prefix",
            "project",
            "container",
            "follow",
            "release_type",
            "db",
            "jdbc_username",
            "jdbc_password",
            "recreate_db",
            "port",
            "host_network",
            "host_name",
            "disable_zip64",
            "delete_state",
            "remove_after",
            "portal",
            "refresh",
            "ssl",
            "force_ssl",
            "timeout",
            "rebuild",
            "env",
            "vars",
            "service",
            "remove",
            "import_env",
            "no_stop",
            "pg_host",
            "pg_port",
            "my_host",
            "my_port",
            "files_only",
            "index",
            "checkpoint",
            "sidecar",
            "no_up",
            "no_wait",
            "mount_logs",
            "gogo_port",
            "jvm_args",
            "no_vol_cache",
            "no_jvm_verify",
            "no_tld_skip",
            "no_seed",
            "seeded",
            "seed_version",
            "seed_config",
            "samples",
            "service_scale",
            "bundle",
            "category",
            "level",
            "list",
            "url",
            "env_id",
            "list_envs",
            "list_backups",
            "download",
            "restore",
            "sync_env",
            "logs",
            "volumes",
            "delete",
            "infra",
            "all_projects",
            "fix_hosts",
        ]
        for attr in run_attrs:
            if not hasattr(args, attr):
                setattr(args, attr, None)
