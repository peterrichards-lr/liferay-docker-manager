"""Java version gate parsing (LDM-#1660).

Table-driven on purpose: the whole defect was a *format variant*, so one
example proves nothing. Every dotted string below was collected from a real
container on 2026-09-10 (the four distros the platform-verification matrix
runs), and the dotless ones are the shape OpenJDK GA releases print before
their first update lands.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler

# (label, `java -version` stderr, expected major, satisfies "21")
JAVA_OUTPUTS = [
    (
        "debian temurin 21",
        'openjdk version "21.0.12.1" 2026-08-18 LTS\n'
        "OpenJDK Runtime Environment Temurin-21.0.12.1+1 (build 21.0.12.1+1-LTS)",
        21,
        True,
    ),
    (
        "alpine 21",
        'openjdk version "21.0.12" 2026-07-21\n'
        "OpenJDK Runtime Environment (build 21.0.12+8-alpine-r0)",
        21,
        True,
    ),
    (
        "rockylinux 21",
        'openjdk version "21.0.12.1" 2026-08-18 LTS\n'
        "OpenJDK Runtime Environment (Red_Hat-21.0.12.1.1-1) (build 21.0.12.1+1-LTS)",
        21,
        True,
    ),
    (
        "fedora 25",
        'openjdk version "25.0.4.1" 2026-08-18\n'
        "OpenJDK Runtime Environment (Red_Hat-25.0.4.1.1-1) (build 25.0.4.1+1)",
        25,
        True,
    ),
    # The defect. Before LDM-#1660 the mandatory `\\.` matched nothing here, so
    # a JDK newer than required was reported as a mismatch.
    (
        "GA build, no dotted component",
        'openjdk version "25" 2025-09-16\n'
        "OpenJDK Runtime Environment (build 25+36-2344)",
        25,
        True,
    ),
    (
        "GA build below the floor",
        'openjdk version "17" 2021-09-14\nOpenJDK Runtime Environment (build 17+35)',
        17,
        False,
    ),
    (
        "underscore-style legacy",
        'java version "1.8.0_452"\nJava(TM) SE Runtime Environment (build 1.8.0_452-b09)',
        1,
        False,
    ),
    (
        "runner default, dotted, below the floor",
        'openjdk version "17.0.20.1" 2026-08-18\n'
        "OpenJDK Runtime Environment Temurin-17.0.20.1+1 (build 17.0.20.1+1)",
        17,
        False,
    ),
]

GRADLE_OUTPUTS = [
    ("dotted", "JVM:          21.0.12 (Eclipse Adoptium 21.0.12+7-LTS)", True),
    ("GA, no dot", "JVM:          25 (Eclipse Adoptium 25+36-2344)", True),
    ("below the floor", "JVM:          17.0.20 (Eclipse Adoptium 17.0.20+1)", False),
]


class _Handler(BaseHandler):
    def __init__(self):
        self.manager = MagicMock()
        self.verbose = False
        self.non_interactive = True


class JavaVersionGateTests(unittest.TestCase):
    def setUp(self):
        self.handler = _Handler()

    def test_java_version_parsing(self):
        for label, output, major, satisfies in JAVA_OUTPUTS:
            with self.subTest(label):
                completed = MagicMock()
                completed.stderr = output
                with (
                    patch(
                        "ldm_core.handlers.base.shutil.which",
                        return_value="/usr/bin/java",
                    ),
                    patch(
                        "ldm_core.handlers.base.subprocess.run",
                        return_value=completed,
                    ),
                ):
                    self.assertEqual(
                        self.handler._check_java_version("21"),
                        satisfies,
                        f"{label}: expected JDK {major} to "
                        f"{'satisfy' if satisfies else 'fail'} the 21 floor",
                    )

    def test_gradle_jvm_parsing(self):
        for label, output, satisfies in GRADLE_OUTPUTS:
            with self.subTest(label):
                completed = MagicMock()
                completed.stdout = output
                with patch(
                    "ldm_core.handlers.base.subprocess.run", return_value=completed
                ):
                    self.assertEqual(
                        self.handler._check_gradle_java_version("./gradlew", "21"),
                        satisfies,
                        f"{label}: gradle JVM line misread",
                    )

    def test_missing_java_is_not_a_pass(self):
        """No `java` on PATH must fail rather than fall through as satisfied."""
        with patch("ldm_core.handlers.base.shutil.which", return_value=None):
            self.assertFalse(self.handler._check_java_version("21"))


if __name__ == "__main__":
    unittest.main()
