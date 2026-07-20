from ldm_core.handlers.base import BaseHandler
from ldm_core.runtime.fragments import FragmentsService
from ldm_core.runtime.logs import LogsService
from ldm_core.runtime.orchestration import OrchestrationService
from ldm_core.runtime.readiness import ReadinessService
from ldm_core.runtime.search import SearchService


class RuntimeService(BaseHandler):
    """Facade for runtime operations. Delegates to specialized services."""

    def __init__(self, manager=None):
        super().__init__(manager)
        self.manager = manager
        if manager:
            self.orchestration = OrchestrationService(manager)
            self.readiness = ReadinessService(manager)
            self.fragments = FragmentsService(manager)
            self.logs = LogsService(manager)
            self.search = SearchService(manager)

    def cmd_run(self, *args, **kwargs):
        return self.orchestration.cmd_run(*args, **kwargs)

    def cmd_start(self, *args, **kwargs):
        return self.orchestration.cmd_start(*args, **kwargs)

    def cmd_stop(self, *args, **kwargs):
        return self.orchestration.cmd_stop(*args, **kwargs)

    def cmd_restart(self, *args, **kwargs):
        return self.orchestration.cmd_restart(*args, **kwargs)

    def cmd_down(self, *args, **kwargs):
        return self.orchestration.cmd_down(*args, **kwargs)

    def cmd_deploy(self, *args, **kwargs):
        return self.orchestration.cmd_deploy(*args, **kwargs)

    def cmd_scale(self, *args, **kwargs):
        return self.orchestration.cmd_scale(*args, **kwargs)

    def cmd_browser(self, *args, **kwargs):
        return self.orchestration.cmd_browser(*args, **kwargs)

    def cmd_renew_ssl(self, *args, **kwargs):
        return self.orchestration.cmd_renew_ssl(*args, **kwargs)

    def cmd_reset(self, *args, **kwargs):
        return self.orchestration.cmd_reset(*args, **kwargs)

    def _generate_keycloak_realm(self, *args, **kwargs):
        return self.orchestration._generate_keycloak_realm(*args, **kwargs)

    def cmd_shell(self, *args, **kwargs):
        return self.orchestration.cmd_shell(*args, **kwargs)

    def cmd_gogo(self, *args, **kwargs):
        return self.orchestration.cmd_gogo(*args, **kwargs)

    def cmd_reseed(self, *args, **kwargs):
        return self.orchestration.cmd_reseed(*args, **kwargs)

    def _scan_for_expected_deployables(self, *args, **kwargs):
        return self.orchestration._scan_for_expected_deployables(*args, **kwargs)

    def cmd_wait(self, *args, **kwargs):
        return self.readiness.cmd_wait(*args, **kwargs)

    def _wait_for_ready(self, *args, **kwargs):
        return self.readiness._wait_for_ready(*args, **kwargs)

    def _patch_fragment_overrides(self, *args, **kwargs):
        return self.fragments._patch_fragment_overrides(*args, **kwargs)

    def _validate_fragment_overrides(self, *args, **kwargs):
        return self.fragments._validate_fragment_overrides(*args, **kwargs)

    def cmd_logs(self, *args, **kwargs):
        return self.logs.cmd_logs(*args, **kwargs)

    def _cmd_logs_instance(self, *args, **kwargs):
        return self.logs._cmd_logs_instance(*args, **kwargs)

    def _run_log_command(self, *args, **kwargs):
        return self.logs._run_log_command(*args, **kwargs)

    def _print_ngrok_url(self, *args, **kwargs):
        return self.logs._print_ngrok_url(*args, **kwargs)

    def cmd_migrate_search(self, *args, **kwargs):
        return self.search.cmd_migrate_search(*args, **kwargs)

    def cmd_reindex(self, *args, **kwargs):
        return self.search.cmd_reindex(*args, **kwargs)
