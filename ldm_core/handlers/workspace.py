from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from ldm_core.handlers.base import BaseHandler


class WorkspaceService(BaseHandler):
    """Service for workspace management (import, monitor, scanning)."""

    def __init__(self, manager):
        super().__init__(manager.args)
        self.manager = manager

    def _parse_client_extension_yaml(self, content):
        from ldm_core.workspace.metadata import _parse_client_extension_yaml

        return _parse_client_extension_yaml(self, content)

    def _get_effective_blacklist(self, paths=None):
        from ldm_core.workspace.metadata import _get_effective_blacklist

        return _get_effective_blacklist(self, paths)

    def _parse_client_extension_config_json(self, content):
        from ldm_core.workspace.metadata import _parse_client_extension_config_json

        return _parse_client_extension_config_json(self, content)

    def _parse_lcp_json(self, content, context_name=None):
        from ldm_core.workspace.metadata import _parse_lcp_json

        return _parse_lcp_json(self, content, context_name)

    def _scan_extension_metadata(self, folder_path=None, zip_ref=None):
        from ldm_core.workspace.metadata import _scan_extension_metadata

        return _scan_extension_metadata(self, folder_path, zip_ref)

    def scan_client_extensions(
        self, root_dir, osgi_cx_dir, ce_build_dir, host_name=None
    ):
        from ldm_core.workspace.metadata import scan_client_extensions

        return scan_client_extensions(
            self, root_dir, osgi_cx_dir, ce_build_dir, host_name
        )

    def scan_standalone_services(self, root_path):
        from ldm_core.workspace.metadata import scan_standalone_services

        return scan_standalone_services(self, root_path)

    def get_host_passthrough_env(self, paths=None, target_id=None):
        from ldm_core.workspace.metadata import get_host_passthrough_env

        return get_host_passthrough_env(self, paths, target_id)

    def _hydrate_from_workspace(self, workspace_root, paths, overwrite=True):
        from ldm_core.workspace.hydration import _hydrate_from_workspace

        return _hydrate_from_workspace(self, workspace_root, paths, overwrite)

    def _sync_cx_artifact(self, zip_path, paths, overwrite=True):
        from ldm_core.workspace.hydration import _sync_cx_artifact

        return _sync_cx_artifact(self, zip_path, paths, overwrite)

    def _prompt_cloud_hydration(self, source_path, project_name=None):
        from ldm_core.workspace.hydration import _prompt_cloud_hydration

        return _prompt_cloud_hydration(self, source_path, project_name)

    def _execute_cloud_hydration(self, env_id, source_path, project_name):
        from ldm_core.workspace.hydration import _execute_cloud_hydration

        return _execute_cloud_hydration(self, env_id, source_path, project_name)

    def cmd_import(
        self,
        source_path,
        is_init_from=False,
        is_internal=False,
        project_id=None,
        clone_only=None,
        no_run=None,
    ):
        from ldm_core.workspace.importer import cmd_import

        return cmd_import(
            self, source_path, is_init_from, is_internal, project_id, clone_only, no_run
        )

    def cmd_link(self, source_path):
        from ldm_core.workspace.importer import cmd_link

        return cmd_link(self, source_path)

    def cmd_clone(self, source_path):
        from ldm_core.workspace.importer import cmd_clone

        return cmd_clone(self, source_path)

    def cmd_init_from(self, source_path):
        from ldm_core.workspace.importer import cmd_init_from

        return cmd_init_from(self, source_path)

    def _parse_github_repo(self, url: str) -> tuple[str, str] | None:
        from ldm_core.workspace.importer import _parse_github_repo

        return _parse_github_repo(self, url)

    def cmd_validate(self, project_id=None):
        from ldm_core.workspace.importer import cmd_validate

        return cmd_validate(self, project_id)

    def cmd_monitor(self, source_path=None, project_id=None):
        from ldm_core.workspace.monitor import cmd_monitor

        return cmd_monitor(self, source_path, project_id)

    def cmd_quickstart(self, template_name, share=False, share_subdomain=None):
        from ldm_core.workspace.quickstart import cmd_quickstart

        return cmd_quickstart(self, template_name, share, share_subdomain)

    def cmd_fork(self, source, target, snapshot=None):
        from ldm_core.workspace.fork import cmd_fork

        return cmd_fork(self, source, target, snapshot)

    def cmd_set_version(self, product_key):
        from ldm_core.workspace.versioning import cmd_set_version

        return cmd_set_version(self, product_key)

    def cmd_init(self, project_id=None):
        from ldm_core.workspace.utils import cmd_init

        return cmd_init(self, project_id)

    def _ensure_stopped(self, project_name, project_path):
        from ldm_core.workspace.utils import _ensure_stopped

        return _ensure_stopped(self, project_name, project_path)

    def _rewrite_oauth_urls_in_zip(self, zip_path, host_name, ext_name, root_dir=None):
        from ldm_core.workspace.utils import _rewrite_oauth_urls_in_zip

        return _rewrite_oauth_urls_in_zip(self, zip_path, host_name, ext_name, root_dir)
