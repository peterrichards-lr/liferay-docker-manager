"""LDM-#2127: `ldm reset` must not report success for a target it ignores.

`reset` advertises five targets plus a comma-separated form. Three of the five
did nothing and said they had worked:

    $ ldm reset demo global-search
    Project demo reset successful.      # no index was touched

`target` is a bare positional with no argparse `choices=`, so ANY string was
accepted -- `ldm reset demo banana` printed the same success line. `search` and
`global-search` reached neither branch of the wipe loop, because
`_reset_volume_for` returns a name only for data/state/db and `setup_paths`
has no key for either. And `targets = [target]` made `"search,db"` one literal
target matching nothing, so the documented comma form reset neither.

This is the LDM-#2118 shape -- a success message over work that did not happen
-- and `docs/TROUBLESHOOTING.md` recommended `reset global-search` as a remedy,
so a reader following our own guide ran a command that did nothing.

The parsing tests drive `_parse_reset_targets` directly because it is pure: it
raises `ValueError` rather than calling `UI.die`, precisely so the rules can be
asserted without a process exit.

`ResettingGlobalSearchIssuesTheDelete` deliberately inspects the command line
rather than asserting that `_delete_project_indices` was called. The call is
the mechanism; the DELETE reaching Docker is the behaviour, and only the
latter distinguishes this fix from the no-op it replaces.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.runtime.orchestration import _RESET_ALL_TARGETS, _parse_reset_targets
from ldm_core.tests.test_orchestration import MockRuntime

PROJECT = "devcon2026"
INDEX = "ldm-devcon2026-workflow-metrics"


class TheTargetArgumentIsValidated(unittest.TestCase):
    def test_an_unknown_target_is_refused_rather_than_reported_successful(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_reset_targets("banana")
        self.assertIn("banana", str(ctx.exception))

    def test_the_refusal_names_the_valid_targets(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_reset_targets("banana")
        # A refusal that does not say what IS accepted sends the reader back to
        # the help text that advertised the broken target in the first place.
        self.assertIn("global-search", str(ctx.exception))

    def test_the_documented_comma_form_is_split(self):
        self.assertEqual(_parse_reset_targets("state,db"), ["state", "db"])

    def test_an_unknown_member_of_the_comma_form_is_refused(self):
        with self.assertRaises(ValueError):
            _parse_reset_targets("state,banana")

    def test_all_expands_and_covers_the_shared_search_node(self):
        expanded = _parse_reset_targets("all")
        self.assertEqual(expanded, list(_RESET_ALL_TARGETS))
        # "Performs all of the above" in docs/how-to/data_management.md was
        # false for the search targets; `all` omitted them entirely.
        self.assertIn("global-search", expanded)

    def test_sidecar_search_is_refused_rather_than_silently_succeeding(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_reset_targets("search")
        message = str(ctx.exception)
        self.assertIn("not implemented", message)
        # The refusal has to leave the reader somewhere useful, or it is just a
        # different way of wasting their time.
        self.assertIn("data", message)

    def test_duplicates_collapse_and_the_order_asked_for_is_kept(self):
        self.assertEqual(_parse_reset_targets("db,state,db"), ["db", "state"])

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(_parse_reset_targets(" db , state "), ["db", "state"])

    def test_an_empty_target_is_refused(self):
        with self.assertRaises(ValueError):
            _parse_reset_targets(",, ")


class ResettingGlobalSearchIssuesTheDelete(unittest.TestCase):
    def setUp(self):
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self.tmp_dir_obj.name) / PROJECT
        self.tmp_dir.mkdir(parents=True)
        self.handler = MockRuntime()
        self.handler.detect_project_path = MagicMock(return_value=self.tmp_dir)  # type: ignore[method-assign]

    def tearDown(self):
        self.tmp_dir_obj.cleanup()

    @staticmethod
    def _fake_docker(cmd, *_args, **_kwargs):
        """Answer the index lookup; everything else is irrelevant here."""
        joined = " ".join(str(c) for c in cmd)
        if "_cat/indices" in joined:
            return f"{INDEX}\n"
        return ""

    def _reset(self, target):
        with (
            patch.object(
                self.handler.manager,
                "read_meta",
                return_value={"container_name": PROJECT},
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
                BaseHandler, "run_command", side_effect=self._fake_docker
            ) as mock_run,
        ):
            self.handler.handler.orchestration.cmd_reset(PROJECT, target=target)
        return [
            " ".join(str(c) for c in call.args[0])
            for call in mock_run.call_args_list
            if call.args
        ]

    def test_it_deletes_this_projects_indices(self):
        issued = self._reset("global-search")
        deletes = [c for c in issued if "DELETE" in c]
        self.assertTrue(
            deletes, f"no DELETE was issued for global-search; saw: {issued}"
        )
        self.assertIn(INDEX, deletes[0])

    def test_reset_all_reaches_the_search_node_too(self):
        issued = self._reset("all")
        self.assertTrue(
            [c for c in issued if "DELETE" in c and INDEX in c],
            f"`reset all` did not clear the project's indices; saw: {issued}",
        )
