"""`ldm info` must say where each JVM value came from (LDM-#1458).

Tuning resolves through five layers (LDM-#1449), so a value can change because
of a file the user is not looking at -- a machine-wide /etc/ldmrc, or a
project's own metadata. Without attribution, answering "why is my heap this
size?" means reading three config files and knowing the adaptive tiers.

Same reasoning as LDM-#1351, which made this command report the names actually
APPLIED rather than the ones requested: the difference is invisible, and the
guess is usually wrong.
"""

import os
import platform
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.handlers.composer import ComposerService


def _no_ci_lean():
    """Removes GITHUB_ACTIONS for the duration of a test.

    `get_default_jvm_args` applies the lean profile whenever
    GITHUB_ACTIONS=true, so on CI every value would be attributed to
    "profile (lean)" and the calculated/config/meta distinctions these tests
    exist to check would collapse. Same guard as test_tuning_cascade.py, for the
    same reason -- this env var has now caught two test files.
    """
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
    return patch.dict(os.environ, env, clear=True)


def _svc(defaults=None, meta=None, lean=False, **cli):
    m = MagicMock()
    m.args = MagicMock()
    m.args.lean = lean
    m.target = None
    m.defaults = defaults
    m.meta = meta or {}
    for key in ComposerService._TUNING_CONFIG_KEYS:
        setattr(m.args, key, cli.get(key))
    svc = ComposerService(m)
    svc.get_physical_host_memory_bytes = (  # type: ignore[method-assign]
        lambda: 32 * 1024**3
    )
    svc.manager.run_command = MagicMock(return_value=None)
    return svc


def _defaults(**values):
    d = MagicMock()
    d.get = lambda key, fallback=None: values.get(key, fallback)
    return d


class TestTuningProvenance(unittest.TestCase):
    def _origins(self, svc):
        origins: dict[str, str] = {}
        with _no_ci_lean(), patch.object(platform, "system", lambda: "Linux"):
            svc._resolve_tuning(origins_out=origins)
        return origins

    def test_untouched_values_are_reported_as_calculated(self):
        origins = self._origins(_svc())
        self.assertEqual("calculated", origins["heap_max_mb"])
        self.assertEqual("calculated", origins["metaspace"])

    def test_a_config_default_is_attributed_to_ldm_config(self):
        origins = self._origins(_svc(defaults=_defaults(jvm_heap_max="9999")))
        self.assertEqual("ldm config", origins["heap_max_mb"])
        self.assertEqual(
            "calculated",
            origins["metaspace"],
            "one override must not relabel the settings it did not touch",
        )

    def test_project_meta_outranks_config(self):
        origins = self._origins(
            _svc(defaults=_defaults(jvm_heap_max="9999"), meta={"jvm_heap_max": "7777"})
        )
        self.assertEqual("project meta", origins["heap_max_mb"])

    def test_the_command_line_outranks_everything(self):
        origins = self._origins(
            _svc(
                defaults=_defaults(jvm_heap_max="9999"),
                meta={"jvm_heap_max": "7777"},
                jvm_heap_max="5555",
            )
        )
        self.assertEqual("command line", origins["heap_max_mb"])

    def test_a_profile_is_named_as_the_source(self):
        """`lean` is data, so the values it supplies are attributable."""
        origins = self._origins(_svc(lean=True))
        self.assertEqual("profile (lean)", origins["heap_max_mb"])

    def test_provenance_does_not_change_the_rendered_arguments(self):
        """Additive only.

        The byte-identical golden table in test_tuning_cascade.py is the primary
        guard; this asserts the origins mapping is not accidentally consulted
        when rendering.
        """
        svc = _svc(defaults=_defaults(jvm_heap_max="9999"))
        with _no_ci_lean(), patch.object(platform, "system", lambda: "Linux"):
            with_origins = svc._render_jvm_args(svc._resolve_tuning(origins_out={}))
            without = svc._render_jvm_args(svc._resolve_tuning())
        self.assertEqual(without, with_origins)


if __name__ == "__main__":
    unittest.main()
