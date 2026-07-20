import shutil

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI


class SearchService(BaseHandler):
    """Search service for runtime operations."""

    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager

    def cmd_migrate_search(self, project_id=None):
        """Migrates a project from Sidecar to Global Elasticsearch."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return

        p_id = root.name
        paths = self.manager.setup_paths(p_id)

        # 1. Ensure Liferay is NOT running
        is_running = self.manager.run_command(
            ["docker", "ps", "-q", "-f", f"name=^{p_id}$"], check=False
        )
        if is_running:
            UI.die(
                f"Project '{p_id}' is currently running. Please stop it first with: ldm stop {p_id}"
            )

        UI.heading(f"Migrating '{p_id}' to Global Search")

        # 2. Check if Global Search is running
        search_running = self.manager.run_command(
            ["docker", "ps", "-q", "-f", "name=^liferay-search-global$"], check=False
        )
        if not search_running:
            if (
                UI.ask(
                    "Global Search container is not running. Start it now?", "Y"
                ).upper()
                == "Y"
            ):
                self.manager.infra.setup_global_search()
            else:
                UI.die("Migration aborted. Global Search is required.")

        # 3. Clean up internal indices
        data_dir = paths["data"]
        indices_found = False
        for es_dir in ["elasticsearch7", "elasticsearch8"]:
            target = data_dir / es_dir
            if target.exists():
                UI.detail(f"Removing internal index directory: {target}")
                shutil.rmtree(target)
                indices_found = True

        if not indices_found:
            UI.detail("No internal sidecar indices found. (Already clean?)")

        # 4. Sync configuration
        UI.detail("Applying Global Search configurations...")
        # We force use_shared_search=True in meta
        project_meta = self.manager.read_meta(root)
        project_meta["use_shared_search"] = "true"
        self.manager.write_meta(root, project_meta)

        # sync_common_assets will now find the global search running and copy the configs
        self.manager.config.sync_common_assets(paths)

        UI.success(
            f"Migration complete! Project '{p_id}' is now configured for Global Search."
        )

        if not self.manager.non_interactive:
            if UI.ask("Restart project now?", "Y").upper() == "Y":
                self.manager.cmd_run(project_id)

    def cmd_reindex(self, project_id=None):
        """Triggers search reindexing (immediately if running, otherwise on next boot)."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return

        from ldm_core.docker_service import DockerService

        meta = self.manager.read_meta(root)
        container_name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or root.name
        )
        force_boot = getattr(self.manager.args, "force_boot", False)

        is_running = DockerService.is_running(container_name)

        if is_running and not force_boot:
            UI.info(
                f"Liferay container '{container_name}' is running. Triggering immediate runtime reindex..."
            )
            groovy_code = 'com.liferay.portal.kernel.search.IndexWriterHelperUtil.reindex(0, "reindex", [com.liferay.portal.kernel.util.PortalUtil.getDefaultCompanyId()] as long[], null)'
            command_list = [
                "sh",
                "-c",
                f"echo '{groovy_code}' | telnet localhost 11311",
            ]
            try:
                DockerService.exec(container_name, command_list, check=True)
                UI.success(
                    f"Successfully triggered immediate runtime reindex on '{container_name}'."
                )
                return
            except Exception as e:
                UI.warning(
                    f"Failed to execute immediate reindex via Gogo shell ({e}). Falling back to boot-time scheduling."
                )

        if self.flag_reindex(root):
            UI.success(
                f"Project '{root.name}' scheduled for search reindex on next boot."
            )
            if not self.manager.non_interactive:
                if UI.confirm("Do you want to restart the project now to apply?", "Y"):
                    self.manager.runtime.cmd_run(root.name)
        else:
            UI.error(f"Failed to schedule reindex for project '{root.name}'.")
