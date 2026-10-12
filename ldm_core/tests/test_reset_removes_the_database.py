"""LDM-#2118: `ldm reset <project> all` must not leave the database behind.

Reported by the AI Commerce Accelerator / devcon2026 team against
v2.26.5-pre.1 on isolated PostgreSQL:

    $ ldm reset devcon2026 all -y
    Project devcon2026 reset successful
    $ docker volume ls | grep devcon2026
    devcon2026-db-db-data      # full PGDATA inside: base/, global/, pg_commit_ts/

The project then reports `Not Created`, so the surviving database is invisible
until the next start quietly boots the old data. A success message over
retained data is worse than a failure, because nothing prompts the user to
look.

Two independent gaps, each sufficient on its own:

1. The `down -v` the code documents is never performed. `cmd_down` appends
   `-v` only when `volumes or delete`, and the call site passed `delete=False`
   and nothing else -- so the LDM-388 comment directly above it ("we must
   'down -v' to destroy anonymous DB volumes") described an intention the call
   did not carry out.

2. The explicit volume sweep never names the database volume. It removes
   `<c_name>-data` and `<c_name>-state`; `_build_db_service` names the
   database volume `<db_container>-db-data`, which for project `devcon2026` is
   `devcon2026-db-db-data`. No pattern in the reset path matched it.

The second gap is what makes this survive a missing or broken compose file --
precisely the case where `compose down -v` cannot run at all.

Note these tests drive the real `cmd_down`, rather than asserting that it was
called with the right keyword. The keyword is the mechanism; `-v` reaching the
compose command line is the behaviour, and only the latter would have caught
gap 1.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.tests.test_orchestration import MockRuntime

PROJECT = "devcon2026"
# What `_build_db_service` actually names it: `<db_container>-db-data`, where
# db_container is `<project>-db`. The doubled "db" is not a typo and is the
# whole reason `<c_name>-data` never matched.
DB_VOLUME = "devcon2026-db-db-data"


# A realistic volume set: the project's three, plus one belonging to a
# DIFFERENT project whose name starts with the same characters. `reset` must
# never take that one -- the sweep filters by prefix, and a prefix is not an
# identity.
PROJECT_VOLUMES = (f"{PROJECT}-data", f"{PROJECT}-state", DB_VOLUME)
FOREIGN_VOLUME = f"{PROJECT}-staging-data"
ALL_VOLUMES = (*PROJECT_VOLUMES, FOREIGN_VOLUME)


def _fake_docker(argv, *_args, **_kwargs):
    """Enough of the Docker CLI for cmd_reset to be exercised honestly.

    Both volume queries are answered, because the two code paths use
    different ones: the per-target removal probes `volume ls -q -f name=^X$`,
    and the label sweep uses `volume ls --filter ... --format {{.Name}}`.
    Stubbing only the first would let the sweep look like a no-op.
    """
    if "volume" in argv and "ls" in argv:
        if "-q" in argv:
            wanted = argv[-1].replace("name=^", "").replace("$", "")
            return wanted if wanted in ALL_VOLUMES else ""
        flt = next(
            (argv[i + 1] for i, a in enumerate(argv) if a == "--filter"),
            "",
        )
        if flt.startswith("name=^"):
            prefix = flt[len("name=^") :]
            return "\n".join(v for v in ALL_VOLUMES if v.startswith(prefix))
        if flt.startswith("label=com.liferay.ldm.project="):
            return "\n".join(PROJECT_VOLUMES)
        return ""
    return ""


def _volume_rm_targets(mock_run):
    """Every volume name passed to `docker volume rm`."""
    targets = []
    for call in mock_run.call_args_list:
        argv = call.args[0] if call.args else []
        if "volume" in argv and "rm" in argv:
            targets.append(argv[-1])
    return targets


def _compose_down_commands(mock_run):
    """Every `compose ... down ...` argv issued."""
    return [
        call.args[0]
        for call in mock_run.call_args_list
        if call.args and "down" in call.args[0]
    ]


class TestResetAllRemovesTheDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self.tmp_dir_obj.name) / PROJECT
        self.tmp_dir.mkdir(parents=True)
        # cmd_down only issues a compose command when there is a compose file
        # to issue it against; without one it falls back to the label sweep
        # and `-v` is never reached. The gap being tested lives on the compose
        # path, so the fixture has to put one there.
        (self.tmp_dir / "docker-compose.yml").write_text("services: {}\n")
        self.handler = MockRuntime()
        self.handler.detect_project_path = MagicMock(return_value=self.tmp_dir)  # type: ignore[method-assign]

    def tearDown(self):
        self.tmp_dir_obj.cleanup()

    def _reset(self, target):
        """Run cmd_reset with Docker stubbed, returning the run_command mock."""
        with (
            patch.object(
                self.handler.manager,
                "read_meta",
                return_value={"container_name": PROJECT, "db_type": "postgresql"},
            ),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={"data": self.tmp_dir / "nonexistent"},
            ),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=False
            ),
            patch.object(
                BaseHandler, "run_command", side_effect=_fake_docker
            ) as mock_run,
        ):
            self.handler.handler.orchestration.cmd_reset(PROJECT, target=target)
        return mock_run

    def test_reset_all_tears_down_with_volumes(self):
        """Gap 1: the documented `down -v` must actually reach the command line."""
        mock_run = self._reset("all")
        downs = _compose_down_commands(mock_run)
        self.assertTrue(downs, "reset all issued no compose down at all")
        self.assertTrue(
            any("-v" in argv for argv in downs),
            "reset all ran `compose down` without -v, so compose kept the "
            f"database volume. Commands issued: {downs}",
        )

    def test_reset_all_removes_the_database_volume(self):
        """Gap 2: the sweep must name the volume compose would have missed."""
        mock_run = self._reset("all")
        self.assertIn(
            DB_VOLUME,
            _volume_rm_targets(mock_run),
            "reset all never removed the database volume by name, so a project "
            "whose compose file is missing or broken keeps its database",
        )

    def test_reset_db_removes_the_database_volume(self):
        """`db` is a documented target and did nothing whatsoever."""
        mock_run = self._reset("db")
        self.assertIn(
            DB_VOLUME,
            _volume_rm_targets(mock_run),
            "`ldm reset <project> db` is advertised in the CLI help and left "
            "the database in place",
        )

    def test_reset_all_leaves_another_projects_volume_alone(self):
        """A prefix is not an identity.

        `reset all` now passes `volumes=True`, which also widens cmd_down's
        fallback sweep. That sweep matches on `name=^<project>-`, so a
        DIFFERENT project whose name merely begins with these characters is
        one bad filter away from being wiped by a reset of its neighbour.
        """
        mock_run = self._reset("all")
        self.assertNotIn(
            FOREIGN_VOLUME,
            _volume_rm_targets(mock_run),
            f"reset of '{PROJECT}' removed '{FOREIGN_VOLUME}', which belongs "
            "to a different project",
        )


class TestResetDryRunAnnouncesTheDatabase(unittest.TestCase):
    """The dry run is the only chance to see this before it happens.

    The report in #2118 shows `ldm reset devcon2026 db --dry-run -v` printing
    one line -- "Would stop project stack if running." -- and saying nothing
    about any volume. A dry run that omits the most destructive part of the
    operation is worse than none.
    """

    def setUp(self):
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self.tmp_dir_obj.name) / PROJECT
        self.tmp_dir.mkdir(parents=True)
        self.handler = MockRuntime()
        self.handler.dry_run = True
        self.handler.detect_project_path = MagicMock(return_value=self.tmp_dir)  # type: ignore[method-assign]

    def tearDown(self):
        self.tmp_dir_obj.cleanup()

    def _dry_run_output(self, target):
        lines = []
        with (
            patch.object(
                self.handler.manager,
                "read_meta",
                return_value={"container_name": PROJECT, "db_type": "postgresql"},
            ),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={"data": self.tmp_dir / "nonexistent"},
            ),
            patch(
                "ldm_core.ui.UI.detail",
                side_effect=lambda m, *_a, **_k: lines.append(m),
            ),
        ):
            self.handler.handler.orchestration.cmd_reset(PROJECT, target=target)
        return "\n".join(lines)

    def test_dry_run_all_names_the_database_volume(self):
        self.assertIn(DB_VOLUME, self._dry_run_output("all"))

    def test_dry_run_db_names_the_database_volume(self):
        self.assertIn(DB_VOLUME, self._dry_run_output("db"))


if __name__ == "__main__":
    unittest.main()
