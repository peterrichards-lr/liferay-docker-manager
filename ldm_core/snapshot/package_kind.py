"""What kind of `.ldmp` a manifest describes (LDM-#2493).

* **stateful** -- a snapshot: database dump, `data/`, OSGi state. Import
  restores them. This is what `ldm package` has always produced.
* **stateless** -- assets only: client extensions, fragments, configs and
  similar. Import deploys them onto a new vanilla instance and restores
  nothing.

A manifest with no `package_kind` is stateful, so every package published
before the field existed keeps behaving exactly as it did. Only the exact
value `stateless` selects the stateless path: anything else, including a
value this LDM does not recognise, is read as stateful, which is today's
behaviour.

Kept free of other LDM imports so the samples code in `handlers/config.py`
can read it without pulling in the snapshot services.
"""

PACKAGE_KIND_KEY = "package_kind"
STATEFUL = "stateful"
STATELESS = "stateless"


def package_kind(manifest) -> str:
    """`STATELESS` or `STATEFUL` for a package manifest (a dict, or None)."""
    if not isinstance(manifest, dict):
        return STATEFUL
    value = str(manifest.get(PACKAGE_KIND_KEY) or "").strip().lower()
    return STATELESS if value == STATELESS else STATEFUL


def is_stateless(manifest) -> bool:
    """True only for a manifest that declares itself stateless."""
    return package_kind(manifest) == STATELESS
