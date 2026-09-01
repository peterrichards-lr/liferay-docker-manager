"""The `seeded` flag must survive the rest of the run (LDM-#1509).

`ldm doctor` reported a genuinely seeded project as vanilla:

    Project Initialization              ⚠️  Vanilla (Not Seeded)

while the same run had printed "Project bootstrapped from seed (including OSGi
state)" and "saved you 14m 0s". The seed WAS applied -- the boot took 1m 21s
instead of ~15 minutes -- but `meta` had no `seeded` key at all.

The seeding stage rebinds a LOCAL name:

    project_meta = context.get("project_meta")          # dict A
    ...
        project_meta = manager.read_meta(paths["root"])  # dict B, local only
        project_meta["seeded"] = "true"
        manager.write_meta(paths["root"], project_meta)  # correct on disk

`context["project_meta"]` still referred to dict A, which has no `seeded` key.
Two later stages open with `project_meta = context.get("project_meta")` and
write it back, so three separate write_meta calls each dropped the flag again.

The write to disk was always correct; it was overwritten afterwards. So a test
that only checks the seeding stage passes while the bug is present -- which is
why this asserts what the CONTEXT carries onward.
"""

import re
import unittest
from pathlib import Path

from ldm_core.pipelines.base import PipelineContext

_RUN_PY = Path(__file__).resolve().parent.parent / "pipelines" / "run.py"


class TestContextIsRefreshedAfterSeeding(unittest.TestCase):
    def test_seed_write_puts_the_meta_back_into_the_context(self):
        """Otherwise later stages write a stale dict over the file."""
        src = _RUN_PY.read_text(encoding="utf-8")
        seed_write = src.index('project_meta["seeded"] = "true"')
        # The refresh must follow the write, before the stage ends.
        following = src[seed_write : seed_write + 1500]
        self.assertIn(
            'context.set("project_meta", project_meta)',
            following,
            "the seeding stage rebinds project_meta locally; without putting it "
            "back, later stages write the pre-seed dict over the file and "
            "`seeded` is lost (LDM-#1509)",
        )

    def test_every_local_rebind_is_followed_by_a_refresh(self):
        """A future rebind added without a refresh reintroduces the bug."""
        src = _RUN_PY.read_text(encoding="utf-8")
        # Scoped to the enclosing method, not a fixed character window: the
        # first stage rebinds at line 144 and refreshes at 183, ~40 lines
        # later, which a window would wrongly flag.
        method_starts = [
            m.start() for m in re.finditer(r"^    def ", src, re.MULTILINE)
        ]
        offenders = []
        for match in re.finditer(
            r"^\s*project_meta = manager\.read_meta\(", src, re.MULTILINE
        ):
            ends = [i for i in method_starts if i > match.end()]
            body = src[match.end() : ends[0] if ends else len(src)]
            line = src[: match.start()].count("\n") + 1
            # Safe if the context is refreshed, or if nothing is written from
            # it -- a read-only rebind cannot clobber anything.
            if 'context.set("project_meta"' in body:
                continue
            if "write_meta" not in body:
                continue
            offenders.append(f"run.py:{line}")
        self.assertEqual(
            [],
            offenders,
            f"project_meta rebound and written without refreshing the context: {offenders}",
        )


class TestPipelineContextRoundTrip(unittest.TestCase):
    """The mechanism the fix relies on."""

    def test_set_replaces_what_get_returns(self):
        ctx = PipelineContext()
        ctx.set("project_meta", {"seeded": "false"})
        ctx.set("project_meta", {"seeded": "true"})
        self.assertEqual("true", ctx.get("project_meta")["seeded"])


if __name__ == "__main__":
    unittest.main()
