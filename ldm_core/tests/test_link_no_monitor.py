"""`ldm link` must be able to finish (LDM-#1689).

`cmd_link` ended in `cmd_monitor`, whose `while True: time.sleep(1)` never
returns. That is right for the usual case -- you link a workspace in order to
watch it -- but it made the command impossible to assert on, which is why the
E2E check for LDM-#1684's `workspace_path` was deferred and tracked rather than
written.

`--no-monitor` records the link and stops. The link is persisted either way, so
`ldm monitor -p <project>` attaches later with no path argument, which is the
LDM-#1684 behaviour the deferred assertion existed to cover.

`--no-run` is declared here too. `cmd_import` has always honoured
`args.no_run`; the flag was simply never declared on `link`/`init-from`, so the
command could not be scripted without booting Liferay. Observed: with both
flags, `ldm link` returns in ~46s and exits 0. Without them it never returns at
all.

The monitor half stays a unit test on purpose. `cmd_monitor` is a watcher by
nature, so asserting it in a linear verification script means backgrounding a
process and killing it on a timer -- a duration the script does not own, which
is the shape LDM-#1444 refused. The E2E script asserts the half that is
deterministic: the command completes and records the path.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


class _Service:
    """Just enough of WorkspaceService for `cmd_link` to run."""

    def __init__(self, no_monitor):
        self.manager = SimpleNamespace(args=SimpleNamespace(no_monitor=no_monitor))
        self.imported: list = []
        self.monitored: list = []

    def cmd_import(self, source, **kwargs):
        self.imported.append((source, kwargs))
        return "proj"

    def cmd_monitor(self, source, project_id=None):
        self.monitored.append((source, project_id))


class LinkNoMonitorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "ws"
        self.workspace.mkdir()

    def _link(self, no_monitor):
        from ldm_core.workspace.importer import cmd_link

        service = _Service(no_monitor)
        with patch("ldm_core.utils.UI"):
            result = cmd_link(service, str(self.workspace))
        return service, result

    def test_the_watcher_is_skipped_when_asked(self):
        service, _ = self._link(no_monitor=True)

        self.assertEqual(
            service.monitored,
            [],
            "cmd_monitor was still called, so `ldm link` still never returns",
        )

    def test_the_import_still_happens(self):
        """Skipping the watcher must not skip the link itself."""
        service, _ = self._link(no_monitor=True)

        self.assertEqual(len(service.imported), 1)
        self.assertTrue(
            service.imported[0][1].get("is_init_from"),
            "is_init_from is what records workspace_path (LDM-#1684)",
        )

    def test_the_project_name_is_returned(self):
        """A caller needs it, and the old code returned None."""
        _, result = self._link(no_monitor=True)

        self.assertEqual(result, "proj")

    def test_the_default_still_watches(self):
        """The flag must be opt-in; linking in order to watch is the normal case."""
        service, _ = self._link(no_monitor=False)

        self.assertEqual(len(service.monitored), 1)
        self.assertEqual(service.monitored[0][1], "proj")


class TheFlagsAreDeclared(unittest.TestCase):
    """A flag `cmd_import` honours but no parser declares is inert (LDM-#1695)."""

    def _options(self, command):
        """Every option string the command's own parser declares.

        Read off the subparser rather than captured from `--help`, so the
        assertion is about what argparse will accept, not about how the help
        text happens to wrap.
        """
        from ldm_core.cli import get_parser

        _parser, subparsers = get_parser()
        sub = subparsers.choices[command]
        return {option for action in sub._actions for option in action.option_strings}

    def test_link_declares_both(self):
        options = self._options("link")

        self.assertIn("--no-monitor", options)
        self.assertIn(
            "--no-run", options, "cmd_import honours it; link must declare it"
        )

    def test_init_from_declares_both(self):
        options = self._options("init-from")

        self.assertIn("--no-monitor", options)
        self.assertIn("--no-run", options)


if __name__ == "__main__":
    unittest.main()
