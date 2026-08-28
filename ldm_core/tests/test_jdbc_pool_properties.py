"""The pool settings must use the names Liferay reads (LDM-#1454).

LDM wrote `jdbc.default.maxActive` / `minIdle` / `maxIdle` -- DBCP and
Tomcat-JDBC names. Liferay uses HikariCP. Verified by extracting
portal.properties from `liferay/dxp:2026.q1.7-lts`: the documented pool block is

    jdbc.default.connectionTimeout=30000
    jdbc.default.idleTimeout=600000
    jdbc.default.maximumPoolSize=180
    jdbc.default.maxLifetime=0
    jdbc.default.minimumIdle=10

and `maxActive`, `minIdle` and `maxIdle` appear nowhere in all 12,085 lines.

So `db_max_active` and friends were settable through `ldm config`, written into
portal-ext.properties, and silently ignored -- the same inert shape as the JVM
flags in #1447 and the UUID labels in #1395.

Correcting the names gives those values effect for the first time, which is a
behaviour change rather than a rename: the pool moves from Liferay's 180 to
LDM's 15. That number is a deliberate choice for a laptop running one project.
"""

import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "handlers" / "composer.py"
DEFAULTS = Path(__file__).resolve().parents[1] / "defaults.py"

# Names Liferay does not read. Written here rather than inline so a failure
# names the offender rather than a regex.
DBCP_NAMES = (
    "jdbc.default.maxActive",
    "jdbc.default.minIdle",
    "jdbc.default.maxIdle",
)

HIKARI_NAMES = (
    "jdbc.default.maximumPoolSize",
    "jdbc.default.minimumIdle",
    "jdbc.default.idleTimeout",
)


class TestPoolPropertyNames(unittest.TestCase):
    def test_no_dbcp_property_names_are_written(self):
        text = SOURCE.read_text()
        for name in DBCP_NAMES:
            self.assertNotIn(
                f'"{name}"',
                text,
                f"{name} is a DBCP name that Liferay ignores; the setting would "
                "be accepted and have no effect (LDM-#1454).",
            )

    def test_the_hikari_names_are_written(self):
        text = SOURCE.read_text()
        for name in HIKARI_NAMES:
            self.assertIn(f'"{name}"', text, f"{name} is not being written")

    def test_db_max_idle_is_not_a_convention_default(self):
        """HikariCP has no maximum-idle setting, so the key cannot be honoured."""
        from ldm_core.defaults import CONVENTION_DEFAULTS

        self.assertNotIn("db_max_idle", CONVENTION_DEFAULTS)
        self.assertIn("db_idle_timeout", CONVENTION_DEFAULTS)

    def test_an_existing_db_max_idle_is_warned_about_not_ignored(self):
        """An existing ~/.ldmrc must not break -- but must not be silent either.

        AGENTS.md forbids breaking user habits, so the key keeps loading. A
        setting that silently stops working is the defect this issue is about,
        so it warns and names the replacement.
        """
        text = SOURCE.read_text()
        self.assertIn('defaults.get("db_max_idle")', text)
        self.assertIn("db_idle_timeout", text)

    def test_the_pool_size_default_is_the_deliberate_one(self):
        from ldm_core.defaults import CONVENTION_DEFAULTS

        self.assertEqual(
            "15",
            CONVENTION_DEFAULTS["db_max_active"],
            "changing this changes every project's pool size, which was "
            "Liferay's 180 until LDM-#1454 made the setting real.",
        )


if __name__ == "__main__":
    unittest.main()
