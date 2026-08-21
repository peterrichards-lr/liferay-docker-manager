"""Regression coverage for ldm_core/workspace/monitor.py's EMFILE recovery path.

The file-descriptor-exhaustion fallback in cmd_monitor had never been executed
by any test (the module sat at 0% coverage), and was broken: it started the
replacement PollingObserverVFS inside the except block and then fell through to
the unconditional observer.start() after the loop. Watchdog observers are
threading.Thread subclasses, so the second start() raised
"threads can only be started once" -- crashing precisely when the graceful
degradation was supposed to kick in. See #1237.

Last Updated: 2026-08-21 | Last Reviewed: 2026-08-21
"""

import errno
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

from ldm_core.workspace.monitor import cmd_monitor


class _FakeObserver:
    """Stand-in for a watchdog observer.

    Must be a real class, not a MagicMock: cmd_monitor does
    `isinstance(observer, PollingObserverVFS)`, and isinstance() rejects a
    MagicMock as its second argument.
    """

    # Per-subclass queue of exceptions to raise from schedule(); a None entry
    # means "this call succeeds". Deliberately class-level: each test subclass
    # gets its own queue and instance registry, set up in setUp().
    schedule_effects: ClassVar[list] = []
    instances: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        self.scheduled: list[str] = []
        self.start_calls = 0
        self.stop_calls = 0
        self.join_calls = 0
        type(self).instances.append(self)

    def schedule(self, _handler, path, recursive=False):
        effects = type(self).schedule_effects
        effect = effects.pop(0) if effects else None
        if effect is not None:
            raise effect
        self.scheduled.append(path)

    def start(self):
        # Faithfully mirror threading.Thread, which every watchdog observer
        # subclasses -- this is the exact production symptom of #1237.
        if self.start_calls:
            raise RuntimeError("threads can only be started once")
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1

    def join(self, *_args, **_kwargs):
        self.join_calls += 1


class _FakeNativeObserver(_FakeObserver):
    """Stands in for watchdog.observers.Observer."""

    schedule_effects: ClassVar[list] = []
    instances: ClassVar[list] = []


class _FakePollingObserver(_FakeObserver):
    """Stands in for watchdog.observers.polling.PollingObserverVFS.

    Declared as an explicit subclass rather than built via type(): mypy widens a
    dynamically created class to bare `type`, losing every attribute and the
    element type of `instances`.
    """

    schedule_effects: ClassVar[list] = []
    instances: ClassVar[list] = []


def _emfile():
    return OSError(errno.EMFILE, "Too many open files")


class _Args:
    """Plain object rather than MagicMock: cmd_monitor does float(args.delay)."""

    def __init__(self, project="monitor-test", delay=0.1):
        self.project = project
        self.delay = delay


class TestMonitorFileLimitFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / "workspace"
        # Two watch targets, so a fallback triggered by the first can be
        # observed handling the second.
        (self.source / "client-extensions").mkdir(parents=True)
        (self.source / "modules").mkdir(parents=True)

        self.paths = {
            "root": self.source,
            "deploy": self.source / "deploy",
            "ce_dir": self.source / "ce",
        }

        self.handler_self = MagicMock()
        self.handler_self.manager.args = _Args()
        self.handler_self.manager.verbose = False
        self.handler_self.manager.setup_paths.return_value = self.paths
        self.handler_self.manager.read_meta.return_value = {}

        # Distinct subclasses so native and polling behaviour are configured
        # independently. The class-level queues and registries are shared state,
        # so reset them per test rather than relying on construction order.
        self.native_cls = _FakeNativeObserver
        self.polling_cls = _FakePollingObserver
        for cls in (_FakeNativeObserver, _FakePollingObserver):
            cls.schedule_effects = []
            cls.instances = []

        self.ui = MagicMock()
        # UI.die() calls sys.exit(); preserve that contract while staying quiet.
        self.ui.die.side_effect = SystemExit(1)

        # time.sleep is patched at module scope (not globally) to break the
        # otherwise-infinite watch loop, mirroring a user pressing Ctrl+C.
        fake_time = MagicMock()
        fake_time.sleep.side_effect = KeyboardInterrupt()

        # Annotated: patch() returns a different _patch[...] specialisation per
        # target, which mypy joins to bare `object` without this.
        self.patchers: list[Any] = [
            patch("watchdog.observers.Observer", self.native_cls),
            patch("watchdog.observers.polling.PollingObserverVFS", self.polling_cls),
            patch("ldm_core.workspace.monitor.UI", self.ui),
            patch("ldm_core.workspace.monitor.time", fake_time),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        self.tmp.cleanup()

    def _run(self, system="Linux"):
        with patch("ldm_core.workspace.monitor.platform") as fake_platform:
            fake_platform.system.return_value = system
            return cmd_monitor(
                self.handler_self,
                source_path=str(self.source),
                project_id="monitor-test",
            )

    def test_emfile_fallback_starts_replacement_observer_exactly_once(self):
        """#1237: the polling fallback must be started once, not twice.

        Before the fix this raised RuntimeError("threads can only be started
        once") because the except block started the replacement and the
        post-loop start() started it again.
        """
        # Native observer refuses both targets; polling accepts everything.
        self.native_cls.schedule_effects = [_emfile(), _emfile()]

        self._run(system="Linux")

        self.assertEqual(
            1,
            len(self.polling_cls.instances),
            "Expected exactly one PollingObserverVFS to be constructed.",
        )
        polling = self.polling_cls.instances[0]
        self.assertEqual(
            1,
            polling.start_calls,
            "Regression (#1237): the replacement observer must be started "
            f"exactly once, got {polling.start_calls}.",
        )

        native = self.native_cls.instances[0]
        self.assertEqual(
            0, native.start_calls, "The abandoned native observer must never start."
        )
        self.assertEqual(
            1, native.stop_calls, "The abandoned native observer must be stopped."
        )
        self.ui.die.assert_not_called()

    def test_emfile_fallback_still_watches_every_target(self):
        """All watch targets must end up registered on the replacement observer."""
        self.native_cls.schedule_effects = [_emfile()]

        self._run(system="Linux")

        polling = self.polling_cls.instances[0]
        self.assertEqual(
            2,
            len(polling.scheduled),
            "Both watch targets must be scheduled on the polling observer "
            f"(got {polling.scheduled}).",
        )

    def test_emfile_while_already_polling_is_fatal_not_silent(self):
        """#1237 secondary: exhausting FDs with no fallback left must not be silent.

        Previously the `if not isinstance(...)` guard was false here, so the
        branch logged an error and continued -- leaving the target unwatched
        while ldm monitor reported itself as running.
        """
        # First target forces the fallback; the polling observer then accepts
        # that target but hits the limit on the second one.
        self.native_cls.schedule_effects = [_emfile()]
        self.polling_cls.schedule_effects = [None, _emfile()]

        with self.assertRaises(SystemExit):
            self._run(system="Linux")

        self.ui.die.assert_called_once()
        self.assertIn("OS file limit", str(self.ui.die.call_args[0][0]))

    def test_emfile_on_macos_is_fatal(self):
        """macOS already polls by default, so an EMFILE there has no fallback."""
        self.polling_cls.schedule_effects = [_emfile()]

        with self.assertRaises(SystemExit):
            self._run(system="Darwin")

        self.ui.die.assert_called_once()
        self.assertEqual(
            0,
            len(self.native_cls.instances),
            "macOS must not construct a native Observer.",
        )

    def test_non_emfile_oserror_is_not_swallowed(self):
        """Only EMFILE is recoverable; anything else must propagate."""
        self.native_cls.schedule_effects = [OSError(errno.EACCES, "Permission denied")]

        with self.assertRaises(OSError) as ctx:
            self._run(system="Linux")

        self.assertEqual(errno.EACCES, ctx.exception.errno)


if __name__ == "__main__":
    unittest.main()
