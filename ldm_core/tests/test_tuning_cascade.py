"""JVM tuning is layered, and the base layer is unchanged (LDM-#1449).

`--lean` used to be an early `return` of a fixed string, bypassing the adaptive
calculation entirely -- the same all-or-nothing shape as `--jvm-args`, just with
better numbers. It is now a set of overrides merged over the adaptive result, so
a profile setting four keys leaves the rest adaptive.

The load-bearing property of this refactor is that **nothing changed**.
`--lean` is applied implicitly whenever `GITHUB_ACTIONS=true`, so a difference
here would silently alter what CI runs. The golden table below was captured
from the pre-refactor implementation and is compared byte-for-byte.

That variable is removed for every test by `isolate_ci_environment` in
conftest.py (LDM-#1468) -- this file's first CI run failed precisely because it
was not. `TestImplicitLeanOnCI` opts back in to assert the behaviour directly.
"""

import os
import platform
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.handlers.composer import ComposerService

# Captured from the implementation before LDM-#1449. mem_gb | os | lean | output
GOLDEN = """
4|Darwin|False|-Xms1024m -Xmx2048m -XX:MaxMetaspaceSize=384m -XX:MetaspaceSize=384m -XX:NewSize=675m -XX:MaxNewSize=675m -XX:TieredStopAtLevel=1
4|Darwin|True|-Xms1536m -Xmx2048m -XX:MaxMetaspaceSize=512m -XX:MetaspaceSize=512m -XX:TieredStopAtLevel=1
4|Linux|False|-Xms1024m -Xmx2048m -XX:MaxMetaspaceSize=384m -XX:MetaspaceSize=384m -XX:NewSize=675m -XX:MaxNewSize=675m
4|Linux|True|-Xms1536m -Xmx2048m -XX:MaxMetaspaceSize=512m -XX:MetaspaceSize=512m -XX:TieredStopAtLevel=1
""".strip()


def _service(mem_gb, lean=False, defaults=None, meta=None, **cli):
    manager = MagicMock()
    manager.args = MagicMock()
    manager.args.lean = lean
    manager.target = None
    manager.defaults = defaults
    manager.meta = meta or {}
    for key in ComposerService._TUNING_CONFIG_KEYS:
        setattr(manager.args, key, cli.get(key))
    svc = ComposerService(manager)
    # Pinning host memory is the point of this fixture: the real method reads
    # the machine, which would make the golden table host-dependent.
    svc.get_physical_host_memory_bytes = (  # type: ignore[method-assign]
        lambda: mem_gb * 1024**3
    )
    # The Docker `info` probe must not run: it would consult the developer's
    # daemon and change the sizing (LDM-#1409's guard would also stop it).
    svc.manager.run_command = MagicMock(return_value=None)
    return svc


class TestAdaptiveOutputIsUnchanged(unittest.TestCase):
    def test_golden_output_is_byte_identical(self):
        """Captured before the refactor; any drift here changes what CI runs."""
        for row in GOLDEN.splitlines():
            mem_gb, osname, lean, expected = row.split("|", 3)
            svc = _service(int(mem_gb), lean=(lean == "True"))
            with patch.object(platform, "system", lambda o=osname: o):
                self.assertEqual(
                    expected,
                    svc.get_default_jvm_args(),
                    f"tuning output changed for {mem_gb}GB/{osname}/lean={lean}",
                )


class TestProfilesAreData(unittest.TestCase):
    def test_lean_is_a_mapping_not_a_string(self):
        """A profile must be mergeable, or it cannot leave anything adaptive."""
        self.assertIsInstance(ComposerService.TUNING_PROFILES["lean"], dict)

    def test_lean_leaves_unset_keys_adaptive(self):
        """The point of profiles-as-data.

        `lean` sets no `new_size_mb` value other than None (omit), so a profile
        that did not mention metaspace would inherit the adaptive figure. This
        pins that a profile is a partial override rather than a replacement.
        """
        svc = _service(32, lean=True)
        with patch.object(platform, "system", lambda: "Linux"):
            resolved = svc._resolve_tuning()
        adaptive = _service(32)._adaptive_tuning()
        self.assertEqual(2048, resolved["heap_max_mb"])
        self.assertNotEqual(adaptive["heap_max_mb"], resolved["heap_max_mb"])


class TestCascadePrecedence(unittest.TestCase):
    """Most specific wins: CLI > meta > defaults > profile > adaptive."""

    def test_config_default_overrides_the_adaptive_value(self):
        defaults = MagicMock()
        defaults.get = lambda key, fallback=None: (
            "9999" if key == "jvm_heap_max" else fallback
        )
        svc = _service(32, defaults=defaults)
        with patch.object(platform, "system", lambda: "Linux"):
            self.assertIn("-Xmx9999m", svc.get_default_jvm_args())

    def test_project_meta_overrides_the_config_default(self):
        defaults = MagicMock()
        defaults.get = lambda key, fallback=None: (
            "9999" if key == "jvm_heap_max" else fallback
        )
        svc = _service(32, defaults=defaults, meta={"jvm_heap_max": "7777"})
        with patch.object(platform, "system", lambda: "Linux"):
            self.assertIn("-Xmx7777m", svc.get_default_jvm_args())

    def test_cli_overrides_everything(self):
        defaults = MagicMock()
        defaults.get = lambda key, fallback=None: (
            "9999" if key == "jvm_heap_max" else fallback
        )
        svc = _service(
            32, defaults=defaults, meta={"jvm_heap_max": "7777"}, jvm_heap_max="5555"
        )
        with patch.object(platform, "system", lambda: "Linux"):
            self.assertIn("-Xmx5555m", svc.get_default_jvm_args())

    def test_an_override_leaves_the_other_settings_adaptive(self):
        """The property that distinguishes this from --jvm-args.

        Setting one value must not discard the rest, which is exactly what
        --jvm-args does and why LDM-#1446 was raised.
        """
        defaults = MagicMock()
        defaults.get = lambda key, fallback=None: (
            "9999" if key == "jvm_heap_max" else fallback
        )
        svc = _service(32, defaults=defaults)
        with patch.object(platform, "system", lambda: "Linux"):
            out = svc.get_default_jvm_args()
        baseline = _service(32)
        with patch.object(platform, "system", lambda: "Linux"):
            base_out = baseline.get_default_jvm_args()

        for flag in ("-XX:MaxMetaspaceSize=", "-XX:NewSize=", "-Xms"):
            token = next(t for t in base_out.split() if t.startswith(flag))
            self.assertIn(token, out, f"{flag} was discarded by an unrelated override")


class TestValueValidation(unittest.TestCase):
    """A bad config value must not reach the renderer (LDM-#1449).

    `int("huge")` raises `ValueError: invalid literal for int()` at container
    start, naming neither the setting nor the layer it came from. Warn, ignore,
    and keep the adaptive value: a project that starts on sane defaults beats
    one that will not start.
    """

    def test_sizes_accept_plain_and_suffixed_forms(self):
        cases = {"8g": 8192, "2048": 2048, "512m": 512, 4096: 4096}
        for raw, want in cases.items():
            self.assertEqual(
                want, ComposerService._tuning_value("jvm_heap_max", raw), f"{raw!r}"
            )

    def test_metaspace_keeps_its_suffix(self):
        """Rendered verbatim into -XX:MetaspaceSize, which takes a suffix."""
        self.assertEqual("768m", ComposerService._tuning_value("jvm_metaspace", "768m"))

    def test_a_nonsense_size_is_ignored_not_fatal(self):
        with patch("ldm_core.handlers.composer.UI") as ui:
            self.assertIsNone(ComposerService._tuning_value("jvm_heap_max", "huge"))
        self.assertTrue(ui.warning.called, "an ignored setting must say so")

    def test_a_non_scalar_is_not_mistaken_for_a_setting(self):
        """A test double whose .get() returns a Mock means 'not configured'.

        Without this the Mock renders as `-Xmx1m` and the adaptive sizing is
        silently discarded -- which is how this was first noticed.
        """
        self.assertIsNone(ComposerService._tuning_value("jvm_heap_max", MagicMock()))

    def test_booleans_parse_for_the_flag_setting(self):
        for raw, want in (("true", True), ("false", False), (True, True)):
            self.assertEqual(
                want,
                ComposerService._tuning_value("jvm_tiered_stop_at_level", raw),
            )


class TestImplicitLeanOnCI(unittest.TestCase):
    """`--lean` is applied whenever GITHUB_ACTIONS=true (LDM-385).

    Asserted directly because it is easy to lose in a refactor, impossible to
    notice locally, and it silently broke this file's own golden table on its
    first CI run -- every `lean=False` case produced lean output.
    """

    def test_github_actions_env_var_applies_the_lean_profile(self):
        svc = _service(32, lean=False)
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
            patch.object(platform, "system", lambda: "Linux"),
        ):
            out = svc.get_default_jvm_args()
        self.assertIn("-Xmx2048m", out, "lean was not applied on CI")

    def test_without_the_env_var_the_adaptive_value_is_used(self):
        """The `isolate_ci_environment` fixture (LDM-#1468) removes it for us."""
        svc = _service(32, lean=False)
        with patch.object(platform, "system", lambda: "Linux"):
            out = svc.get_default_jvm_args()
        self.assertIn("-Xmx16384m", out, "adaptive sizing was not used off CI")


if __name__ == "__main__":
    unittest.main()
