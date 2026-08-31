"""Non-ASCII project names must survive to the console (LDM-#1484).

The PowerShell verification of v2.19.0-pre.1 failed here:

    [ERROR] 'ldm info test-naming-Żółć' does not show the verbatim project name.

`meta`, `docker-compose.yml` and `ldm list --json` all carried the name
correctly in that same run, so nothing was corrupted -- only the rendered
output. `UI._print` probes `out.encode(sys.stdout.encoding)`, which on Windows
is the ANSI code page, so every line took the ASCII fallback and its blanket
`encode("ascii", "replace")` turned `Żółć` into `????`.

#1465 had attributed this to the console and added `chcp 65001` to the verify
script, on top of the `PYTHONUTF8` and `[Console]::OutputEncoding` it already
set. All three were in effect for the failing run. Those govern what the
console DECODES; this is about what the process ENCODES.
"""

import io
import sys
import unittest

from ldm_core.ui import UI

NAMES = ["Żółć", "Käsespätzle", "Được"]


class _Cp1252Stream(io.TextIOWrapper):
    """A stdout that cannot represent the names -- i.e. a Windows console."""

    def __init__(self):
        super().__init__(io.BytesIO(), encoding="cp1252", errors="strict")


class TestToAsciiReadable(unittest.TestCase):
    """The fallback must degrade names readably, not blank them."""

    def test_diacritics_are_transliterated_not_blanked(self):
        self.assertEqual(UI.to_ascii_readable("Żółć"), "Zolc")

    def test_german_umlauts_survive_readably(self):
        self.assertNotIn("?", UI.to_ascii_readable("Käsespätzle"))

    def test_plain_ascii_is_untouched(self):
        self.assertEqual(UI.to_ascii_readable("ldm-smoke-test"), "ldm-smoke-test")

    def test_undecomposable_still_degrades_rather_than_raising(self):
        # No ASCII decomposition exists; "?" is the honest answer.
        self.assertEqual(UI.to_ascii_readable("→"), "?")

    def test_names_are_not_reduced_to_question_marks(self):
        """The reported symptom: `Żółć` displayed as `????`."""
        for name in NAMES:
            rendered = UI.to_ascii_readable(name)
            self.assertNotEqual(
                rendered,
                "?" * len(name),
                f"{name!r} was blanked to {rendered!r}",
            )


class TestConfigureStreamEncoding(unittest.TestCase):
    """stdout must be moved to UTF-8 so the fallback is never reached."""

    def setUp(self):
        self._stdout, self._stderr = sys.stdout, sys.stderr

    def tearDown(self):
        sys.stdout, sys.stderr = self._stdout, self._stderr

    def test_non_utf8_stream_is_reconfigured(self):
        sys.stdout = _Cp1252Stream()
        sys.stderr = _Cp1252Stream()
        self.assertEqual(sys.stdout.encoding, "cp1252")

        UI.configure_stream_encoding()

        self.assertEqual(sys.stdout.encoding.lower().replace("-", ""), "utf8")
        self.assertEqual(sys.stderr.encoding.lower().replace("-", ""), "utf8")

    def test_reconfigured_stream_carries_the_names(self):
        """The point of the whole change."""
        sys.stdout = _Cp1252Stream()
        UI.configure_stream_encoding()
        for name in NAMES:
            # Would raise UnicodeEncodeError under cp1252, which is exactly
            # what sent _print into the ASCII fallback.
            name.encode(sys.stdout.encoding)

    def test_is_idempotent(self):
        sys.stdout = _Cp1252Stream()
        UI.configure_stream_encoding()
        UI.configure_stream_encoding()
        self.assertEqual(sys.stdout.encoding.lower().replace("-", ""), "utf8")

    def test_stream_without_reconfigure_is_left_alone(self):
        """A captured buffer under pytest has no reconfigure(); must not raise."""

        class _Plain:
            encoding = "cp1252"

        sys.stdout = _Plain()
        UI.configure_stream_encoding()
        self.assertEqual(sys.stdout.encoding, "cp1252")


if __name__ == "__main__":
    unittest.main()
