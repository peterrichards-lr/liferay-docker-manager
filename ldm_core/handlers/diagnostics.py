from ldm_core.handlers.base import BaseHandler


class DiagnosticsService(BaseHandler):
    """Service for diagnostic and maintenance commands."""

    def __init__(self, manager=None):
        self.manager = manager

    def cmd_info(self, project_id=None):
        from ldm_core.diagnostics.info import run_info

        run_info(self, project_id)

    def cmd_status(self, project_id=None, all_projects=False, detailed=False):
        from ldm_core.diagnostics.info import run_status

        run_status(self, project_id, all_projects, detailed)

    def cmd_update_check(self, force=True):
        from ldm_core.diagnostics.upgrade import run_update_check

        run_update_check(self, force)

    def cmd_clear_cache(self):
        from ldm_core.diagnostics.prune import run_clear_cache

        run_clear_cache(self)

    def cmd_cache(self, target="all"):
        from ldm_core.diagnostics.prune import run_cache

        run_cache(self, target)

    def cmd_upgrade(self):
        from ldm_core.diagnostics.upgrade import run_upgrade

        run_upgrade(self)

    def cmd_doctor(self, project_id=None, all_projects=False, fix_hosts=False):
        from ldm_core.diagnostics.doctor import run_doctor

        run_doctor(self, project_id, all_projects, fix_hosts)

    def cmd_list(self):
        from ldm_core.diagnostics.info import run_list

        run_list(self)

    def cmd_prune(self):
        from ldm_core.diagnostics.prune import run_prune

        run_prune(self)

    def cmd_completion(self, target_shell=None):
        from ldm_core.diagnostics.completions import run_completion

        run_completion(self, target_shell)

    def cmd_man(self):
        from ldm_core.diagnostics.completions import run_man

        run_man(self)

    def cmd_setup_completion(self, target_shell=None):
        from ldm_core.diagnostics.completions import run_setup_completion

        run_setup_completion(self, target_shell)
