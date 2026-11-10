"""Troubleshooting advice must not fire on normal output, nor name fixes that
cannot work (LDM-#2307).

The lines below are the ones from the report, or the JVM's own wording.
"""

import unittest
from unittest.mock import patch

from ldm_core.utils import check_troubleshooting_signatures as advice_for


class TheFirstPostgresBootIsNotADisaster(unittest.TestCase):
    LINE = '2026-11-01 09:13:31.259 UTC [65] FATAL:  database "lportal" does not exist'

    def test_it_no_longer_tells_the_user_to_hydrate(self):
        # Every first boot printed this; nothing was wrong.
        self.assertNotIn("ldm hydrate", advice_for(self.LINE) or "")

    def test_it_says_the_line_is_expected_on_a_first_boot(self):
        self.assertIn("first boot", advice_for(self.LINE) or "")


class TheHealthcheckNoLongerAsksForTheMissingDatabase(unittest.TestCase):
    """Measured on postgres:16.2: polling `pg_isready -U lportal` during init
    logged `FATAL: database "lportal" does not exist`; with `-d postgres` it
    logged none, and the image still ran CREATE DATABASE."""

    def test_the_rendered_healthcheck_names_the_postgres_database(self):
        from ldm_core.handlers.composer import ComposerService
        from ldm_core.tests.test_orchestration import MockRuntime

        manager = MockRuntime()
        manager.args.database_mode = None
        with patch(
            "ldm_core.handlers.composer.resolve_dependency_version",
            return_value="16.2",
        ):
            service = ComposerService(manager)._build_db_service(
                {"db_type": "postgresql", "database_mode": "isolated"}, "shop"
            )
        self.assertEqual(
            service["healthcheck"]["test"],
            ["CMD-SHELL", "pg_isready -U lportal -d postgres"],
        )


class EachJvmFailureGetsItsOwnFix(unittest.TestCase):
    def test_heap(self):
        advice = advice_for(
            'Exception in thread "main" java.lang.OutOfMemoryError: Java heap space'
        )
        self.assertIn("--jvm-heap-max", advice or "")
        self.assertNotIn("--jvm-metaspace", advice or "")

    def test_metaspace(self):
        advice = advice_for("java.lang.OutOfMemoryError: Metaspace")
        self.assertIn("--jvm-metaspace", advice or "")
        self.assertNotIn("--jvm-heap-max", advice or "")

    def test_code_cache_old_and_segmented_wording(self):
        for line in (
            "CodeCache is full. Compiler has been disabled.",
            "CodeHeap 'non-profiled nmethods' is full. Compiler has been disabled.",
        ):
            with self.subTest(line=line):
                self.assertIn(
                    "--jvm-tiered-stop-at-level false", advice_for(line) or ""
                )

    def test_other_out_of_memory_errors_still_get_advice(self):
        advice = advice_for(
            "java.lang.OutOfMemoryError: unable to create native thread"
        )
        self.assertIn("ldm info", advice or "")

    def test_no_jvm_advice_sends_users_to_restart_or_portal_properties(self):
        lines = (
            "java.lang.OutOfMemoryError: Java heap space",
            "java.lang.OutOfMemoryError: Metaspace",
            "CodeCache is full. Compiler has been disabled.",
            "java.lang.OutOfMemoryError: Direct buffer memory",
        )
        for line in lines:
            with self.subTest(line=line):
                advice = advice_for(line) or ""
                self.assertNotIn("portal properties", advice)
                self.assertNotIn("ldm restart", advice)
                self.assertIn("ldm run", advice)


class LdmsOwnJvmFlagsAreNotErrors(unittest.TestCase):
    def test_the_command_line_echo_of_the_code_cache_flag(self):
        self.assertIsNone(
            advice_for(
                "INFO [main] org.apache.catalina.startup.VersionLoggerListener.log "
                "Command line argument: -XX:ReservedCodeCacheSize=512m"
            )
        )

    def test_a_bare_mention_of_the_code_cache_is_not_an_error(self):
        self.assertIsNone(advice_for("-XX:ReservedCodeCacheSize=512m"))


if __name__ == "__main__":
    unittest.main()
