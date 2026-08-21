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

        meta = self.manager.read_meta(root) or {}
        target_name = getattr(self.manager, "target", None) or meta.get("target")
        from ldm_core.docker_service import DockerService

        docker_prefix = DockerService.get_docker_cmd_prefix(target_name)

        # 1. Ensure Liferay is NOT running
        is_running = self.manager.run_command(
            [*docker_prefix, "ps", "-q", "-f", f"name=^{p_id}$"], check=False
        )
        if is_running:
            UI.die(
                f"Project '{p_id}' is currently running. Please stop it first with: ldm stop {p_id}"
            )

        UI.heading(f"Migrating '{p_id}' to Global Search")

        # 2. Check if Global Search is running
        search_running = self.manager.run_command(
            [*docker_prefix, "ps", "-q", "-f", "name=^liferay-search-global$"],
            check=False,
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
                self.manager.runtime.cmd_run(project_id)

    def cmd_reindex(self, project_id=None):
        """Schedules a search reindex for the project's next boot.

        LDM-#1242: a reindex cannot be triggered on a running portal from
        outside it, so this always schedules for next boot and then offers (or,
        with --force-boot, performs) the restart that applies it.
        """
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
            # LDM-#1242: this previously piped a fully-qualified
            # IndexWriterHelperUtil.reindex(...) call into the Gogo shell and
            # reported success. Gogo is a command shell, not a Java evaluator --
            # it answered `gogo: PatternSyntaxException: Unclosed character
            # class` and no reindex ever ran. telnet still exits 0 (the
            # connection succeeded), so even check=True could not catch it, and
            # the false success return skipped the boot-time fallback below.
            # No Gogo command on any supported DXP version can trigger a
            # reindex (`updateIndexes*` are database indexes), so the honest
            # path is to schedule it for the next boot.
            UI.detail(
                f"Liferay container '{container_name}' is running, but an immediate "
                "runtime reindex cannot be triggered from outside the portal. "
                "Scheduling the reindex for the next boot instead."
            )

        if self.flag_reindex(root):
            UI.success(
                f"Project '{root.name}' scheduled for search reindex on next boot."
            )
            # LDM-#1242: --force-boot used to mean "skip the immediate runtime
            # reindex attempt". That attempt never worked, so the flag now
            # carries the meaning its name always implied: restart right away to
            # apply the reindex, without prompting. This is also what automation
            # needs, since the interactive prompt below is skipped entirely in
            # non-interactive mode.
            if force_boot:
                UI.detail("Restarting now to apply the reindex (--force-boot).")
                self.manager.runtime.cmd_run(root.name)
            elif not self.manager.non_interactive:
                if UI.confirm("Do you want to restart the project now to apply?", "Y"):
                    self.manager.runtime.cmd_run(root.name)
        else:
            UI.error(f"Failed to schedule reindex for project '{root.name}'.")
