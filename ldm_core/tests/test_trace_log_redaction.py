"""Nothing reaches the trace log unredacted (LDM-#2437).

`UI._print` redacted its messages before tracing them, but the `logging`
bridge (LDM-#1669) and the direct `UI.trace` callers -- tracebacks among them --
wrote whatever they were given, and the header wrote the raw command line.
The trace log (`~/.ldm/last-command.log`) is what users attach to bug reports.

Isolation as in test_logging_bridge.py: `LDM_HOME` points at a temporary
directory, the only primitive that redirects LDM's state dir (LDM-#1349).
"""

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.ui import UI

SECRET = "hunter2-s3cret"  # pragma: allowlist secret


class TraceLogRedaction(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(UI.reset)
        patcher = patch.dict(os.environ, {"LDM_HOME": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = Path(self._tmp.name) / ".ldm" / "last-command.log"

    def _start(self, argv=("ldm", "test")):
        UI.init_trace_log(list(argv))
        self.assertTrue(self.log.exists(), "no trace log; LDM_HOME isolation failed")

    def _read(self):
        return self.log.read_text(encoding="utf-8")

    def test_a_logging_record_is_redacted(self):
        self._start()
        logging.getLogger("pipeline.demo").warning("env MYSQL_PASSWORD=%s", SECRET)
        written = self._read()
        self.assertNotIn(SECRET, written)
        self.assertIn("MYSQL_PASSWORD=[REDACTED]", written)

    def test_a_direct_trace_is_redacted(self):
        self._start()
        UI.trace(f"GET https://api.example/x?access_token={SECRET}")
        written = self._read()
        self.assertNotIn(SECRET, written)
        self.assertIn("token=[REDACTED]", written)

    def test_a_printed_message_is_redacted_once(self):
        """`_print` already redacts; redacting again must not mangle it."""
        self._start()
        with patch("sys.stdout"):
            UI.info(f"using DB_PASSWORD={SECRET}")
        written = self._read()
        self.assertNotIn(SECRET, written)
        self.assertIn("DB_PASSWORD=[REDACTED]", written)

    def test_the_command_line_masks_a_credential_config_value(self):
        self._start(["ldm", "config", "admin_password", SECRET])
        header = self._read()
        self.assertNotIn(SECRET, header)
        self.assertIn("Command: ldm config admin_password [REDACTED]", header)

    def test_the_command_line_masks_a_password_flag(self):
        self._start(["ldm", "db", "shell", f"-p{SECRET}"])
        self.assertNotIn(SECRET, self._read())

    def test_an_ordinary_command_line_is_kept(self):
        self._start(["ldm", "run", "demo", "--tag", "2026.q1.7-lts", "-y"])
        self.assertIn("Command: ldm run demo --tag 2026.q1.7-lts -y", self._read())


if __name__ == "__main__":
    unittest.main()
