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
