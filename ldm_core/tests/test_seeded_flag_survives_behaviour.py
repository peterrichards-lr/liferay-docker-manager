"""The `seeded` flag must actually survive the run (LDM-#1509).

The existing test_seeded_flag_survives.py reads `run.py` as TEXT and asserts
that `context.set("project_meta", project_meta)` appears near the seed write.
It executes nothing, so it passes whenever that line is present -- including if
the line were present but ineffective. The E2E assertion added alongside it
(LDM-#1525) cannot pass at all: its setup hand-writes a `meta` file, so
`is_new_project` is False and seeding never runs.

So the #1509 fix was guarded by one test that cannot fail and one that cannot
pass. This runs the stage.

The bug: the seeding block rebinds `project_meta` to a LOCAL dict read back
from disk. Without putting it into the context, later stages re-read the
pre-seed dict and write it straight over the file, dropping `seeded`.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ldm_core.pipelines.run import EnvironmentSetupStage, RunPipelineContext
from ldm_core.utils import read_meta, write_meta


def _meta_path(p):
    p = Path(p)
    return p / "meta" if p.is_dir() else p


class TestSeededFlagSurvivesALaterStage(unittest.TestCase):
    def _run_stage(self, root):
        (root / "meta").write_text(
            json.dumps(
                {
                    "project_name": "p",
                    "container_name": "p",
                    "tag": "2026.q1.7-lts",
                    "db_type": "postgresql",
                    "port": "8080",
                }
            ),
            encoding="utf-8",
        )
        manager = MagicMock()
        manager.read_meta = lambda p: read_meta(_meta_path(p))
        manager.write_meta = lambda p, m: write_meta(_meta_path(p), m)
        manager.assets._ensure_seeded.return_value = True
        manager.args.command = "run"

        context = RunPipelineContext(manager=manager)
        context.set("project_meta", manager.read_meta(root))
        context.set(
            "paths",
            {k: root / k for k in ("data", "cx", "ce_dir", "deploy", "logs", "files")}
            | {"root": root},
        )
        context.set("is_new_project", True)
        context.set("tag", "2026.q1.7-lts")
        context.set("db_type", "postgresql")
        context.set("project_id", "p")

        EnvironmentSetupStage().execute(context)
        return manager, context

    def test_the_context_carries_the_post_seed_meta(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, context = self._run_stage(root)
            self.assertEqual(context.get("project_meta").get("seeded"), "true")

    def test_a_later_stage_writing_the_context_does_not_drop_the_flag(self):
        """The actual regression: three later write_meta calls each dropped it."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manager, context = self._run_stage(root)

            # Exactly what the later stages do: re-read from the context and
            # write it back. With the pre-seed dict, this is what erased the
            # flag while the disk write had been correct all along.
            for _ in range(3):
                later = context.get("project_meta")
                manager.write_meta(root, later)

            on_disk = read_meta(root / "meta")
            self.assertEqual(
                on_disk.get("seeded"),
                "true",
                "a later stage wrote the pre-seed dict over the file (LDM-#1509)",
            )
            self.assertTrue(on_disk.get("seed_version"))

    def test_an_unseeded_run_is_left_alone(self):
        """The flag must not be invented when nothing seeded."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "meta").write_text(
                json.dumps({"project_name": "p", "container_name": "p"}),
                encoding="utf-8",
            )
            manager = MagicMock()
            manager.read_meta = lambda p: read_meta(_meta_path(p))
            manager.write_meta = lambda p, m: write_meta(_meta_path(p), m)
            manager.assets._ensure_seeded.return_value = False
            manager.args.command = "run"

            context = RunPipelineContext(manager=manager)
            context.set("project_meta", manager.read_meta(root))
            context.set(
                "paths",
                {
                    k: root / k
                    for k in ("data", "cx", "ce_dir", "deploy", "logs", "files")
                }
                | {"root": root},
            )
            context.set("is_new_project", True)
            context.set("tag", "2026.q1.7-lts")
            context.set("db_type", "postgresql")
            context.set("project_id", "p")

            EnvironmentSetupStage().execute(context)
            self.assertIsNone(context.get("project_meta").get("seeded"))


if __name__ == "__main__":
    unittest.main()


class TestSeedingKeepsWhatTheCliResolved(unittest.TestCase):
    """Seeding must not discard the run's own configuration.

    LDM-#1523 fixed LDM-#1509 by putting the re-read meta into the context:

        project_meta = manager.read_meta(paths["root"])   # from DISK
        project_meta["seeded"] = "true"
        context.set("project_meta", project_meta)

    For a NEW project, ConfigResolutionStage has resolved db_type, port,
    host_name and container_name from the CLI into the context's dict and has
    not yet written them to disk. Replacing that dict with the disk read threw
    all of it away.

    `ldm run <proj> --db mysql --database-mode shared --port N` on a fresh
    project therefore became postgresql on 8080 with host_name None: the MySQL
    global was never created, and container_name=None reached the readiness
    probe and raised TypeError without failing the run. It passed on every
    v2.19.x and failed on every v2.20.0 pre-release.
    """

    def _run_with_cli_config(self, root):
        # Disk holds only what a bare project has; the CLI values live in the
        # context, exactly as they do before the first write_meta.
        (root / "meta").write_text(json.dumps({"project_name": "p"}), encoding="utf-8")

        manager = MagicMock()
        manager.read_meta = lambda p: read_meta(_meta_path(p))
        manager.write_meta = lambda p, m: write_meta(_meta_path(p), m)
        manager.assets._ensure_seeded.return_value = True
        manager.args.command = "run"

        context = RunPipelineContext(manager=manager)
        context.set(
            "project_meta",
            {
                "project_name": "p",
                "container_name": "sharedboot-mysql-9911",
                "host_name": "localhost",
                "db_type": "mysql",
                "port": "9911",
            },
        )
        context.set(
            "paths",
            {k: root / k for k in ("data", "cx", "ce_dir", "deploy", "logs", "files")}
            | {"root": root},
        )
        context.set("is_new_project", True)
        context.set("tag", "2026.q1.7-lts")
        context.set("db_type", "mysql")
        context.set("project_id", "p")

        EnvironmentSetupStage().execute(context)
        return context

    def test_the_cli_resolved_values_survive_seeding(self):
        with tempfile.TemporaryDirectory() as d:
            context = self._run_with_cli_config(Path(d))
            meta = context.get("project_meta")
            self.assertEqual(meta.get("db_type"), "mysql", "--db was discarded")
            self.assertEqual(meta.get("port"), "9911", "--port was discarded")
            self.assertEqual(meta.get("host_name"), "localhost")
            self.assertEqual(
                meta.get("container_name"),
                "sharedboot-mysql-9911",
                "container_name=None is what reached the readiness probe and "
                "raised TypeError",
            )

    def test_the_seeded_flag_is_still_set(self):
        # The LDM-#1509 property must hold at the same time.
        with tempfile.TemporaryDirectory() as d:
            context = self._run_with_cli_config(Path(d))
            self.assertEqual(context.get("project_meta").get("seeded"), "true")

    def test_the_values_reach_disk(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._run_with_cli_config(root)
            on_disk = read_meta(root / "meta")
            self.assertEqual(on_disk.get("db_type"), "mysql")
            self.assertEqual(on_disk.get("seeded"), "true")
