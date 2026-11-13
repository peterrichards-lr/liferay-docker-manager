"""`ldm -y rm --infra` must not tear shared infrastructure from under running projects (LDM-#2371).

`InfraService.cmd_infra_down` confirmed only on the interactive path, so `-y`
-- automation, where nobody is watching -- ran `compose down -v` on the shared
proxy and search node whatever was using them. Observed 2026-11-13: the verify
script's pre-run `ldm -y rm ... --delete --infra` took both away from a
running project, which went on reporting healthy.

These drive the real `cmd_infra_down` and the real `cmd_down(infra=True)`.
`scan_running_projects` runs for real too, answered at its `run_command`
boundary, so the names the guard excludes are checked against the names the
scan actually reports -- the seam a stubbed scan would bracket without
crossing.

Interlock: every route to a process is replaced. The manager's `run_command`
records, the module-scope `run_command` used by the scan and DockerService is
a fake, and `DockerService.stop`/`rm` are mocks. Nothing here can reach Docker.
"""

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.infra import InfraService
from ldm_core.runtime.orchestration import OrchestrationService


@pytest.fixture(autouse=True)
def _isolate_ldm_home(tmp_path, monkeypatch):
    """No test may write the real ~/.ldm -- the hard rule in testing-and-ci."""
    home = tmp_path / "ldm-home"
    home.mkdir()
    monkeypatch.setenv("LDM_HOME", str(home))


class _Manager(BaseHandler):
    def __init__(self, roots, project_root, non_interactive=True, force=False):
        self.args = MagicMock()
        self.args.force = force
        self.verbose = False
        self.non_interactive = non_interactive
        self.dry_run = False
        self.target = None
        self.defaults = {}
        self.roots = roots
        self.project_root = project_root
        self.commands: list = []
        self.manager = cast(Any, self)
        self.orchestration = OrchestrationService(self)
        self.infra = InfraService(self)

    def run_command(self, cmd, **kw):  # type: ignore[override]
        self.commands.append(list(cmd))
        return ""

    def find_dxp_roots(self, *a, **k):  # type: ignore[override]
        return [{"path": r} for r in self.roots]

    def read_meta(self, path, *a, **k):  # type: ignore[override]
        # The container name deliberately differs from the directory and
        # project name: `ldm rm probe` names the project, the scan reports the
        # container, and a fixture where they coincide cannot tell whether
        # the exclusion maps one to the other.
        return {
            "project_name": Path(path).name,
            "container_name": f"ctr-{Path(path).name}",
        }

    def detect_project_path(self, *a, **k):  # type: ignore[override]
        return self.project_root

    def get_resource_path(self, *a, **k):  # type: ignore[override]
        from ldm_core.utils import get_resource_path

        return get_resource_path(*a, **k)

    def validate_project_dns(self, *a, **k):
        return (None, [], [])

    def get_compose_base(self, *a, **k):
        return ["docker", "compose"]

    def safe_rmtree(self, path):  # type: ignore[override]
        pass

    def unregister_project(self, *a, **k):
        pass

    def infra_compose_downs(self):
        return [
            c
            for c in self.commands
            if "down" in c and any(str(x).endswith("infra-compose.yml") for x in c)
        ]


def _docker_ps(running, raise_probe=False):
    """Answers `docker ps --filter name=^X$` as the daemon would."""

    def fake(cmd, *a, **k):
        cmd = [str(x) for x in cmd]
        if "ps" in cmd:
            if raise_probe:
                return None  # check=False: timeout / non-zero exit
            for part in cmd:
                if part.startswith("name=^") and part.endswith("$"):
                    return "running" if part[6:-1] in running else ""
        return ""

    return fake


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        base = Path(self.td.name)
        self.probe = base / "probe"
        self.other = base / "other-proj"
        for p in (self.probe, self.other):
            p.mkdir()

        for target, kw in (
            ("ldm_core.docker_service.DockerService.stop", {}),
            ("ldm_core.docker_service.DockerService.rm", {}),
            (
                "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
                {"return_value": ["docker"]},
            ),
            (
                "ldm_core.handlers.infra.InfraService._get_infra_env",
                {"return_value": {}},
            ),
            ("ldm_core.handlers.infra.UI.error", {}),
            ("ldm_core.runtime.orchestration.UI", {}),
        ):
            patcher = patch(target, **kw)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            if target.endswith("DockerService.rm"):
                self.mock_rm = mock

    def _run(self, manager, running, raise_probe=False, call=None):
        fake = _docker_ps(running, raise_probe)
        with (
            patch("ldm_core.utils.run_command", side_effect=fake),
            patch("ldm_core.docker_service.run_command", side_effect=fake),
        ):
            return call()


class TheNonInteractiveGuard(_Base):
    def test_refuses_while_another_project_runs(self):
        m = _Manager([self.probe, self.other], None)
        with self.assertRaises(SystemExit) as cm:
            self._run(m, {"ctr-other-proj"}, call=m.infra.cmd_infra_down)
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(m.infra_compose_downs(), [], "compose down ran anyway")
        self.mock_rm.assert_not_called()

    def test_refusal_names_the_project_and_the_routes_out(self):
        m = _Manager([self.probe, self.other], None)
        with patch("ldm_core.handlers.infra.UI.die", side_effect=SystemExit(3)) as die:
            with self.assertRaises(SystemExit):
                self._run(m, {"ctr-other-proj"}, call=m.infra.cmd_infra_down)
        kwargs = die.call_args.kwargs
        self.assertEqual(kwargs["exit_code"], 3)
        self.assertIn("ctr-other-proj", kwargs["details"])
        self.assertIn("ldm stop ctr-other-proj", kwargs["tip"])
        self.assertIn("--force", kwargs["tip"])

    def test_unanswerable_probe_fails_closed(self):
        """LDM-#1548: a daemon that cannot answer is not 'nothing running'."""
        m = _Manager([self.probe, self.other], None)
        with self.assertRaises(SystemExit) as cm:
            self._run(m, set(), raise_probe=True, call=m.infra.cmd_infra_down)
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(m.infra_compose_downs(), [])

    def test_force_proceeds_while_another_project_runs(self):
        m = _Manager([self.probe, self.other], None, force=True)
        self.assertTrue(self._run(m, {"ctr-other-proj"}, call=m.infra.cmd_infra_down))
        self.assertEqual(len(m.infra_compose_downs()), 1)

    def test_proceeds_when_nothing_runs(self):
        m = _Manager([self.probe, self.other], None)
        self.assertTrue(self._run(m, set(), call=m.infra.cmd_infra_down))
        self.assertEqual(len(m.infra_compose_downs()), 1)

    def test_interactive_path_still_confirms_and_does_not_scan(self):
        m = _Manager([self.probe, self.other], None, non_interactive=False)
        with (
            patch("ldm_core.utils.has_shared_projects", return_value=True),
            patch("ldm_core.handlers.infra.UI.confirm", return_value=False),
            patch.object(InfraService, "scan_running_projects") as scan,
        ):
            self.assertFalse(
                self._run(m, {"ctr-other-proj"}, call=m.infra.cmd_infra_down)
            )
        scan.assert_not_called()
        self.assertEqual(m.infra_compose_downs(), [])


class RmInfraThroughCmdDown(_Base):
    """`cmd_down` tears infra down BEFORE the project it names."""

    def test_project_being_removed_does_not_block_its_own_teardown(self):
        m = _Manager([self.probe, self.other], self.probe)
        self._run(
            m,
            {"ctr-probe"},
            call=lambda: m.orchestration.cmd_down("probe", delete=True, infra=True),
        )
        self.assertEqual(len(m.infra_compose_downs()), 1)

    def test_another_running_project_still_blocks(self):
        m = _Manager([self.probe, self.other], self.probe)
        with self.assertRaises(SystemExit) as cm:
            self._run(
                m,
                {"ctr-probe", "ctr-other-proj"},
                call=lambda: m.orchestration.cmd_down("probe", delete=True, infra=True),
            )
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(m.infra_compose_downs(), [])
        self.assertEqual(m.commands, [], "the project was torn down regardless")

    def test_force_proceeds_through_cmd_down(self):
        m = _Manager([self.probe, self.other], self.probe, force=True)
        self._run(
            m,
            {"ctr-probe", "ctr-other-proj"},
            call=lambda: m.orchestration.cmd_down("probe", delete=True, infra=True),
        )
        self.assertEqual(len(m.infra_compose_downs()), 1)

    def test_rm_all_infra_excludes_every_project(self):
        m = _Manager([self.probe, self.other], None)
        self._run(
            m,
            {"ctr-probe", "ctr-other-proj"},
            call=lambda: m.orchestration.cmd_down(all_projects=True, infra=True),
        )
        self.assertEqual(len(m.infra_compose_downs()), 1)
