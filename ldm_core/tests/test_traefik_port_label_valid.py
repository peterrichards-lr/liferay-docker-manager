"""LDM-#1996: LDM could emit a Traefik label Traefik cannot parse.

    traefik.http.services.<svc>.loadbalancer.server.port=None

**A malformed `traefik.*` label makes Traefik discard the WHOLE container's
configuration**, not just that label. The container then runs with
correct-looking labels and no router, and its hostname returns a bare 404 --
indistinguishable from every other cause of a 404 (LDM-#1989).

The cause was a `next()` whose guard and whose yielded value were different
keys:

    next(
        (p.get("port") for p in ext.get("ports", [])
         if isinstance(p, dict) and p.get("external")),
        (ext.get("loadBalancer") or {}).get("targetPort", 8080),
    )

A `ports` entry with `external` truthy and no `port` key satisfies the guard and
yields **None**. `next` returns that -- its default applies only when the
generator is EMPTY, never when it produces a falsy value.

Same family as LDM-#1962, where `dict.get`'s default did not apply to a key that
existed with value None. Both are "the default did not fire because the value
was present and falsy", and both produced a downstream string containing `None`.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ldm_core.handlers.composer import ComposerService

BASE: dict = {"id": "ms", "deploy": True, "is_service": True, "path": "/tmp/x"}


def _service_labels(ext, ssl_enabled=True):
    composer = ComposerService(MagicMock())
    composer.manager.workspace.scan_client_extensions.return_value = [ext]
    composer.manager.workspace.get_service_targeted_env.return_value = {}
    services = composer._build_extensions_services(
        {"root": Path("/tmp"), "cx": Path("/tmp/cx"), "ce_dir": Path("/tmp/ce")},
        {},
        "h.test",
        "p",
        ssl_enabled,
    )
    return next(iter(services.values()))["labels"]


def _port_label(ext, **kw):
    return next(
        (lbl for lbl in _service_labels(ext, **kw) if "server.port" in lbl), None
    )


class TestNoTraefikLabelEverContainsNone(unittest.TestCase):
    """The assertion with teeth: a label carrying `None` costs the container its
    ENTIRE routing configuration, so this is not a cosmetic concern."""

    def test_no_label_contains_none_for_any_declaration_shape(self):
        shapes: dict[str, dict] = {
            "external with no port": {"ports": [{"external": True}]},
            "targetPort explicitly None": {"loadBalancer": {"targetPort": None}},
            "empty ports list": {"ports": []},
            "ports entry is not a dict": {"ports": ["8080"]},
            "port present but zero": {"ports": [{"port": 0, "external": True}]},
            "loadBalancer is None": {"loadBalancer": None},
            "nothing declared at all": {},
        }
        for name, extra in shapes.items():
            with self.subTest(shape=name):
                for label in _service_labels({**BASE, **extra}):
                    self.assertNotIn(
                        "None",
                        label,
                        f"{name}: Traefik cannot parse this and discards the "
                        f"whole container's config (LDM-#1996): {label}",
                    )


class TestThePortIsResolvedSensibly(unittest.TestCase):
    def test_a_declared_external_port_is_used(self):
        self.assertIn(
            "=3001", _port_label({**BASE, "ports": [{"port": 3001, "external": True}]})
        )

    def test_load_balancer_target_port_is_the_fallback(self):
        self.assertIn(
            "=9090", _port_label({**BASE, "loadBalancer": {"targetPort": 9090}})
        )

    def test_8080_is_the_last_resort(self):
        """An extension declaring nothing still gets a usable label."""
        self.assertIn("=8080", _port_label(BASE))

    def test_an_external_entry_without_a_port_falls_through(self):
        """The bug itself. The entry satisfies the `external` guard and carries
        no port, so it must NOT short-circuit the fallback."""
        self.assertIn(
            "=7070",
            _port_label(
                {
                    **BASE,
                    "ports": [{"external": True}],
                    "loadBalancer": {"targetPort": 7070},
                }
            ),
        )

    def test_a_non_external_port_is_not_used(self):
        """`external` is the contract; an internal-only port is not the one
        Traefik should target."""
        self.assertIn(
            "=8080", _port_label({**BASE, "ports": [{"port": 5555, "external": False}]})
        )


class TestBothDerivationSitesAgree(unittest.TestCase):
    """`ms_port` is derived in two places -- the Liferay-service routes
    injection and the extension-service builder. They drifted before
    (LDM-#1962), so the shared helper is asserted rather than assumed.
    """

    def test_only_one_implementation_exists(self):
        import inspect

        src = inspect.getsource(ComposerService)
        self.assertEqual(
            2,
            src.count("self._resolve_container_port(ext)"),
            "both ms_port derivations must go through the shared helper, or "
            "they can disagree about the same declaration (LDM-#1996)",
        )
        self.assertNotIn(
            'p.get("port")\n                            for p in ext.get("ports", [])',
            src,
            "an inline next() derivation is back; it is the shape that yielded "
            "None (LDM-#1996)",
        )


if __name__ == "__main__":
    unittest.main()
