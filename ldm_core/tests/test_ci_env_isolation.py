"""The suite must run as if it were not on CI (LDM-#1468).

`GITHUB_ACTIONS` changes real behaviour in five places, and each is invisible to
a test that does not think to look:

- `handlers/composer.py` applies the lean JVM profile
- `cli.py` permits running as root, bypassing the guard
- `manager.py` flags the run as CI
- `utils.py` skips the OS keyring, on read and on write

A test that does not control it asserts one thing locally and another on CI,
passing in both while exercising different code. That happened three times here.

`isolate_ci_environment` in conftest.py removes it for every test. These
assertions exist because an autouse fixture that silently stops working
reintroduces exactly the bug it was added to prevent -- the same reasoning as
`test_the_docker_guard_can_actually_fail` (#1409) and
`test_suite_never_touches_the_developers_real_ldm_home` (#1349).
"""

import os
import unittest
from unittest.mock import patch


class TestCiEnvironmentIsolation(unittest.TestCase):
    def test_github_actions_is_not_set_during_tests(self):
        """The contract. Fails on CI if the fixture is removed or renamed."""
        self.assertIsNone(
            os.environ.get("GITHUB_ACTIONS"),
            "GITHUB_ACTIONS is visible to this test, so the suite is exercising "
            "CI-only code paths: the lean JVM profile, the root-guard bypass and "
            "the keyring skip (LDM-#1468).",
        )

    def test_the_lean_profile_is_not_applied_by_the_environment(self):
        """The consequence that has actually bitten, asserted end to end.

        Checked through `get_default_jvm_args` rather than the env var alone,
        because the env var is only interesting for what it causes.
        """
        import platform
        from unittest.mock import MagicMock

        from ldm_core.handlers.composer import ComposerService

        manager = MagicMock()
        manager.args = MagicMock()
        manager.args.lean = False
        manager.target = None
        manager.defaults = None
        manager.meta = {}
        for key in ComposerService._TUNING_CONFIG_KEYS:
            setattr(manager.args, key, None)

        svc = ComposerService(manager)
        svc.get_physical_host_memory_bytes = (  # type: ignore[method-assign]
            lambda: 32 * 1024**3
        )
        svc.manager.run_command = MagicMock(return_value=None)

        with patch.object(platform, "system", lambda: "Linux"):
            args = svc.get_default_jvm_args()

        self.assertIn(
            "-Xmx16384m",
            args,
            "the adaptive heap was replaced by the lean profile's 2048m, which "
            "means GITHUB_ACTIONS reached the calculation (LDM-#1468).",
        )

    def test_a_test_can_still_opt_in_to_ci_behaviour(self):
        """Isolation must not make the CI paths untestable.

        `TestImplicitLeanOnCI` in test_tuning_cascade.py relies on this.
        """
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            self.assertEqual("true", os.environ.get("GITHUB_ACTIONS"))
        self.assertIsNone(
            os.environ.get("GITHUB_ACTIONS"), "the opt-in leaked past its scope"
        )


if __name__ == "__main__":
    unittest.main()
