"""A stage must undo what it created -- and the limit of that rule (LDM-#1635).

`pipelines/base.py` has stated the rule since LDM-#1630: undo what THIS stage
created, on this stage. Nothing enforced it, and the import pipeline shipped its
own counter-example -- `BackupStateStage`, whose `execute` was an empty `pass`
and which existed only to host cleanup for state that `ExtractionStage` and
`ProjectSetupStage` created. That is the arrangement LDM-#1630 traced the
`.ldm_temp/import_<timestamp>/` leak to, because `Pipeline._rollback` visits
only stages that executed and the cleanup sat three stages past the refusal.

Two things are pinned here:

1. The **limit** of the rule, measured rather than assumed: a stage that raises
   is never appended to `executed_stages`, so its own rollback does not run.
2. A **ratchet** on the import pipeline: a stage whose `execute` creates
   filesystem state must define `rollback`, unless it is a documented
   pre-existing exception. New stages cannot join that list silently.
"""

import ast
import pathlib
import unittest

from ldm_core.pipelines.base import Pipeline, PipelineContext, PipelineStage

_PIPELINE_SRC = (
    pathlib.Path(__file__).resolve().parent.parent / "pipelines" / "import_pipeline.py"
)

# Calls that mean "this stage put something on disk".
_FS_CREATORS = frozenset(
    {"mkdir", "makedirs", "copytree", "copy2", "extractall", "write_meta", "write_text"}
)

# Stages that create filesystem state and do NOT yet own its cleanup.
#
# This is a RATCHET, not an approval: it records the gap that existed when
# LDM-#1635 landed so the invariant can be enforced for everything else. Adding
# a name here is a deliberate act that needs an issue; removing one is progress.
#
# LDM-#1643 examined all three original entries and two were wrong:
#
# * PackageVerificationStage creates NOTHING. It parses, reconciles the manifest
#   dict in memory, and warns; the scratch dirs it is handed belong to
#   ExtractionStage. Removed -- a rollback there would have had nothing to undo.
# * FinalizationStage must NEVER define a rollback. It sets `import_committed`
#   before `cmd_run`, and that is the commit point ProjectSetupStage.rollback
#   checks before declining to delete a finished import. Removed, and pinned by
#   TestFinalizationStageMustNotRollBack below so nobody "completes" LDM-#1643
#   by adding one.
#
# * VolumeSyncStage gained a rollback in LDM-#1677. It snapshots the artifact
#   directories before anything writes to them and restores them wholesale,
#   rather than journalling individual writes -- much of the copying happens
#   inside workspace/hydration.py, which this stage never sees, so a journal
#   would have covered about half the writes while appearing to succeed.
#
# The allowlist is now EMPTY, and that is the point of a ratchet. Adding a name
# back requires a tracking issue and a deliberate decision.
_KNOWN_UNCOVERED: frozenset = frozenset()


def _stage_classes(source: str):
    """Yield (name, ClassDef) for every PipelineStage subclass in `source`."""
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        if "PipelineStage" in bases:
            yield node.name, node


def _methods(cls: ast.ClassDef) -> dict:
    return {
        n.name: n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _creates_fs_state(func) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            attr = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if attr in _FS_CREATORS:
                return True
    return False


class TestTheFailingStageDoesNotRollItselfBack(unittest.TestCase):
    """The rule covers a failure in a LATER stage, not in the owning stage.

    `Pipeline.run` appends to `executed_stages` only *after* `execute` returns
    (`base.py`), so the stage that raised is not in the rollback walk. This is
    the difference between "cleanup on the owning stage is sufficient" and
    "cleanup on the owning stage is sufficient for later failures", and getting
    it wrong is how a leak gets declared fixed while still leaking.
    """

    def test_a_raising_stage_is_skipped_and_earlier_stages_are_not(self):
        calls = []

        class Creates(PipelineStage):
            name = "creates"

            def execute(self, context):
                calls.append("creates.execute")

            def rollback(self, context):
                calls.append("creates.rollback")

        class Raises(PipelineStage):
            name = "raises"

            def execute(self, context):
                calls.append("raises.execute")
                raise SystemExit(3)

            def rollback(self, context):  # pragma: no cover - must NOT be reached
                calls.append("raises.rollback")

        class Later(PipelineStage):
            name = "later"

            def execute(self, context):  # pragma: no cover - never reached
                calls.append("later.execute")

            def rollback(self, context):  # pragma: no cover - never executed
                calls.append("later.rollback")

        pipeline = Pipeline(name="probe", stages=[Creates(), Raises(), Later()])
        with self.assertRaises(SystemExit) as caught:
            pipeline.run(PipelineContext())

        self.assertEqual(caught.exception.code, 3, "the exit code must survive")
        self.assertIn("creates.rollback", calls, "an earlier stage must roll back")
        self.assertNotIn(
            "raises.rollback",
            calls,
            "a raising stage rolled itself back -- base.py's documented limit "
            "no longer holds, and cleanup guidance needs revisiting (LDM-#1635)",
        )
        self.assertNotIn(
            "later.rollback", calls, "a stage that never executed must not roll back"
        )


class TestImportStagesOwnTheirCleanup(unittest.TestCase):
    def setUp(self):
        self.source = _PIPELINE_SRC.read_text(encoding="utf-8")

    def test_the_dissolved_stage_has_not_come_back(self):
        """LDM-#1635: an empty `execute` hosting another stage's rollback."""
        self.assertNotIn(
            "class BackupStateStage",
            self.source,
            "BackupStateStage was dissolved because its execute did nothing and "
            "it hosted cleanup for state other stages created (LDM-#1635)",
        )

    def test_project_setup_owns_the_project_directory_it_creates(self):
        """The specific move LDM-#1635 made, pinned against a silent revert."""
        stages = dict(_stage_classes(self.source))
        self.assertIn("ProjectSetupStage", stages)
        self.assertIn(
            "rollback",
            _methods(stages["ProjectSetupStage"]),
            "ProjectSetupStage sets project_path and is_brand_new, so it must "
            "define the rollback that removes them (LDM-#1635)",
        )

    def test_finalization_stage_must_not_define_a_rollback(self):
        """LDM-#1643: the one stage where adding a rollback is the bug.

        `FinalizationStage.execute` sets `import_committed` and then calls
        `cmd_run`. That flag is the commit point: `ProjectSetupStage.rollback`
        reads it and declines to delete the project once it is set, which is
        what LDM-#1630 added to stop an aborted post-import `ldm run` from
        destroying a successful import.

        A rollback on this stage would run inside that committed region and
        undo the very thing the commit point protects. The state `cmd_run`
        creates belongs to the run pipeline, whose stages own their own
        rollbacks.

        Asserted because LDM-#1643's title invites the opposite fix, and
        "every stage should own a rollback" is a plausible-sounding way to
        reintroduce LDM-#1630.
        """
        stages = dict(_stage_classes(self.source))
        self.assertIn("FinalizationStage", stages, "the stage was renamed or removed")
        self.assertNotIn(
            "rollback",
            _methods(stages["FinalizationStage"]),
            "FinalizationStage defines a rollback. It runs after "
            "`import_committed` is set, so its rollback would undo a completed "
            "import -- the exact regression the commit point prevents "
            "(LDM-#1630, LDM-#1643).",
        )

    def test_every_state_creating_stage_owns_a_rollback_or_is_a_known_gap(self):
        """The ratchet: a NEW stage cannot leak silently.

        Fails in two directions on purpose. A state-creating stage with no
        `rollback` and no entry in `_KNOWN_UNCOVERED` is the bug this exists to
        stop. An entry in `_KNOWN_UNCOVERED` that has since gained a `rollback`
        is stale bookkeeping, and the list must shrink when the gap closes.
        """
        offenders, stale = [], []
        for name, cls in _stage_classes(self.source):
            methods = _methods(cls)
            execute = methods.get("execute")
            if execute is None:
                continue
            creates = _creates_fs_state(execute)
            has_rollback = "rollback" in methods

            if creates and not has_rollback and name not in _KNOWN_UNCOVERED:
                offenders.append(name)
            if has_rollback and name in _KNOWN_UNCOVERED:
                stale.append(name)
            # LDM-#1643: a third direction. An entry that creates no filesystem
            # state was never a gap, and the original list carried two of them
            # for weeks precisely because nothing objected. The allowlist is
            # supposed to shrink; an entry that could never have belonged is
            # noise that makes the real one harder to see.
            if not creates and name in _KNOWN_UNCOVERED:
                stale.append(name)

        self.assertEqual(
            offenders,
            [],
            f"{offenders} create filesystem state but define no rollback. Either "
            "add one, or add the name to _KNOWN_UNCOVERED with a tracking issue "
            "(LDM-#1635).",
        )
        self.assertEqual(
            stale,
            [],
            f"{stale} are listed in _KNOWN_UNCOVERED but do not belong there -- "
            "either they now define a rollback, or they create no filesystem "
            "state and never needed one. Remove them so the ratchet keeps "
            "tightening (LDM-#1635, LDM-#1643).",
        )


if __name__ == "__main__":
    unittest.main()
