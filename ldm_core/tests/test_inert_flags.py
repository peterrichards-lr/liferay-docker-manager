"""Declared flags must do something, or say they do not (LDM-#1695).

PR #497 rebuilt the `project_meta` literal from scratch and dropped fields on
the way. LDM-#1695 enumerated the remainder mechanically rather than finding
them one at a time: every key the old import wrote against every key written
now, and every CLI flag it read against every flag read anywhere in `ldm_core`.
Four flags were consumed by nothing.

`--env` is the one that matters. It is published in
`docs/reference/cli/core.md`:

    ldm run my-project --env LIFERAY_COMPANY_DEFAULT_WEB_ID=my-domain.com

and did nothing at all. `handlers/composer.py` still consumes
`meta["custom_env"]` and `ldm config env` still writes it, so both ends of the
plumbing were intact -- only the flag-to-meta step was missing.

`--gogo-port` is the `--cloud-project` shape exactly: a live consumer
(`runtime/orchestration.py:1150` reads `meta.get("gogo_port")`) with no producer
anywhere.

`--mount-logs` is deliberately **not** wired up. Nothing reads it under any
spelling, and the behaviour it names happens anyway -- `handlers/composer.py`
bind-mounts `logs/` for every single-node project, inside `if scale == 1`.
Removing the flag would break scripts that pass it, so it is accepted and
reported as the no-op it is.

Observed before the fix: `ldm run flagtest --no-up --env LDM_PROBE=hello
--gogo-port 11311 --mount-logs` recorded none of the keys and warned nothing.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ldm_core.pipelines.run import ConfigResolutionStage


class _Manager:
    def __init__(self, **flags):
        self.args = SimpleNamespace(
            env=flags.get("env"),
            gogo_port=flags.get("gogo_port"),
            mount_logs=flags.get("mount_logs", False),
        )


def _apply(manager, meta=None):
    meta = meta if meta is not None else {}
    with patch("ldm_core.pipelines.run.UI") as ui:
        ConfigResolutionStage._apply_inert_flags(manager, meta)
    return meta, ui


class TheEnvFlag(unittest.TestCase):
    def test_a_pair_is_recorded(self):
        meta, _ = _apply(_Manager(env=["LDM_PROBE=hello"]))

        self.assertEqual(json.loads(meta["custom_env"]), {"LDM_PROBE": "hello"})

    def test_repeated_flags_accumulate(self):
        meta, _ = _apply(_Manager(env=["A=1", "B=2"]))

        self.assertEqual(json.loads(meta["custom_env"]), {"A": "1", "B": "2"})

    def test_a_value_may_contain_equals(self):
        """A JDBC URL or a base64 value must survive the split."""
        meta, _ = _apply(_Manager(env=["URL=jdbc:postgresql://h:5432/db?a=b"]))

        self.assertEqual(
            json.loads(meta["custom_env"])["URL"],
            "jdbc:postgresql://h:5432/db?a=b",
        )

    def test_a_pair_without_equals_is_ignored(self):
        meta, _ = _apply(_Manager(env=["NOT_A_PAIR"]))

        self.assertNotIn("custom_env", meta)

    def test_existing_custom_env_is_merged_not_replaced(self):
        """`ldm config env` writes this key too; a run must not wipe it."""
        meta, _ = _apply(
            _Manager(env=["NEW=2"]),
            meta={"custom_env": json.dumps({"EXISTING": "1"})},
        )

        self.assertEqual(json.loads(meta["custom_env"]), {"EXISTING": "1", "NEW": "2"})

    def test_the_flag_wins_on_a_conflicting_key(self):
        meta, _ = _apply(
            _Manager(env=["K=from-flag"]),
            meta={"custom_env": json.dumps({"K": "from-meta"})},
        )

        self.assertEqual(json.loads(meta["custom_env"])["K"], "from-flag")

    def test_unparseable_existing_meta_does_not_raise(self):
        meta, _ = _apply(_Manager(env=["A=1"]), meta={"custom_env": "not json at all"})

        self.assertEqual(json.loads(meta["custom_env"]), {"A": "1"})

    def test_no_flag_records_nothing(self):
        meta, _ = _apply(_Manager())

        self.assertNotIn("custom_env", meta)


class TheGogoPortFlag(unittest.TestCase):
    def test_it_is_recorded_for_the_consumer_that_was_starved(self):
        meta, _ = _apply(_Manager(gogo_port=11311))

        self.assertEqual(meta["gogo_port"], "11311")

    def test_no_flag_records_nothing(self):
        meta, _ = _apply(_Manager())

        self.assertNotIn("gogo_port", meta)


class TheMountLogsFlag(unittest.TestCase):
    def test_it_is_reported_as_a_no_op(self):
        """Silently ignoring a flag someone typed is its own surprise."""
        _, ui = _apply(_Manager(mount_logs=True))

        warned = " ".join(str(c) for c in ui.warning.call_args_list)
        self.assertIn("mount-logs", warned)
        self.assertIn("no effect", warned)

    def test_it_is_not_wired_into_the_meta(self):
        """Nothing reads it; inventing a key would be worse than the no-op."""
        meta, _ = _apply(_Manager(mount_logs=True))

        self.assertNotIn("mount_logs", meta)

    def test_not_passing_it_is_silent(self):
        _, ui = _apply(_Manager(mount_logs=False))

        self.assertFalse(ui.warning.called)


class TheLdmVersionStamp(unittest.TestCase):
    """LDM-#1695: only `_handle_dry_run` wrote it after PR #497."""

    def test_an_imported_project_records_the_version_that_made_it(self):
        """AST, not a substring: a comment mentioning the line would pass that.

        Driving `ProjectSetupStage.execute` for one assignment would need a
        whole LiferayManager, a project directory and a registry, so this
        asserts the assignment exists in that function body -- the same ratchet
        shape as `test_hasattr_guards_name_real_methods.py`.
        """
        import ast
        import inspect
        import textwrap

        from ldm_core.constants import VERSION
        from ldm_core.pipelines.import_pipeline import ProjectSetupStage

        tree = ast.parse(textwrap.dedent(inspect.getsource(ProjectSetupStage.execute)))
        stamped = any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(tgt, ast.Subscript)
                and getattr(tgt.value, "id", None) == "project_meta"
                and isinstance(tgt.slice, ast.Constant)
                and tgt.slice.value == "ldm_version"
                for tgt in node.targets
            )
            for node in ast.walk(tree)
        )

        self.assertTrue(stamped, "the ldm_version stamp was dropped again")
        self.assertTrue(VERSION, "VERSION must be non-empty to be worth stamping")


if __name__ == "__main__":
    unittest.main()
