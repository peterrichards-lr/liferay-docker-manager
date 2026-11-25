"""Host name classification for the rootless model (LDM-#2513, #2514).

One place answers "what kind of host name is this?", because the answer
decides three things that must never disagree:

- whether a hosts-file entry (and so ``sudo``) is ever needed,
- whether SSL is enabled by default,
- whether a non-SSL project is fronted by the global proxy's plain-HTTP router.

The three kinds:

- ``localhost`` -- the basic run, ``http://localhost:8080``. Unchanged.
- ``*.localhost`` -- a *named local* host. RFC 6761 reserves the TLD for
  loopback, and browsers resolve it to 127.0.0.1 with no hosts entry, so it is
  sudo-free. HTTP unless SSL is asked for explicitly.
- anything else -- a *custom domain*: hosts entry, SSL by default, mkcert.
"""

import re

LOCALHOST = "localhost"
LOCALHOST_SUFFIX = ".localhost"

# RFC 1123 label: letters, digits and hyphens, not starting or ending with a
# hyphen, at most 63 characters.
_DNS_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _clean(host_name) -> str:
    return str(host_name or "").strip().rstrip(".").lower()


def is_localhost_subdomain(host_name) -> bool:
    """True for ``<anything>.localhost`` -- not for bare ``localhost``."""
    host = _clean(host_name)
    return host.endswith(LOCALHOST_SUFFIX) and len(host) > len(LOCALHOST_SUFFIX)


def is_loopback_name(host_name) -> bool:
    """True for ``localhost`` and every ``*.localhost`` name.

    These never need a hosts-file entry, so they never need ``sudo``.
    """
    host = _clean(host_name)
    return host == LOCALHOST or is_localhost_subdomain(host)


def is_custom_domain(host_name) -> bool:
    """True for a host name that is neither ``localhost`` nor ``*.localhost``."""
    return bool(_clean(host_name)) and not is_loopback_name(host_name)


def auto_ssl_default(host_name) -> bool:
    """The SSL default when neither a flag nor the metadata decides (LDM-#2514).

    SSL auto-enables for custom domains only. ``localhost`` and ``*.localhost``
    stay HTTP unless SSL is requested explicitly.
    """
    return is_custom_domain(host_name)


def routes_http_via_proxy(host_name, ssl_enabled) -> bool:
    """True when the project is fronted by the global proxy's HTTP router.

    LDM-#2514: a non-SSL ``*.localhost`` project is served at
    ``http://<project>.localhost`` with no port, through Traefik's ``web``
    entrypoint. Basic ``localhost`` runs never use the proxy, and SSL projects
    use the ``websecure`` router instead.
    """
    return is_localhost_subdomain(host_name) and not ssl_enabled


def localhost_name_for(project_name) -> str:
    """The ``<project>.localhost`` name for a project, as a valid DNS label.

    Characters a DNS label cannot carry become hyphens; an empty result falls
    back to ``ldm``.
    """
    label = re.sub(r"[^a-z0-9-]+", "-", str(project_name or "").lower())
    label = re.sub(r"-{2,}", "-", label).strip("-")[:63].strip("-")
    return f"{label or 'ldm'}{LOCALHOST_SUFFIX}"


def normalize_host_name(host_name):
    """Normalise a host name given by a flag, a prompt or a manifest (LDM-#2513).

    - ``localhost`` -> unchanged.
    - a bare name with no dot (``myproj``) -> ``myproj.localhost``.
    - anything ending in ``.localhost`` -> unchanged.
    - anything else with a dot -> a custom domain, used as given.

    Raises ``ValueError`` when a bare name is not a valid DNS label. Returns
    the input unchanged when it is empty, so callers can keep their own
    fallbacks. Existing project metadata is never passed through this: stored
    host names are not rewritten.
    """
    if not isinstance(host_name, str):
        return host_name
    raw = host_name.strip()
    if not raw:
        return host_name
    host = raw.rstrip(".")
    if host.lower() == LOCALHOST:
        return LOCALHOST
    if "." in host:
        return host
    label = host.lower()
    if not _DNS_LABEL.match(label):
        raise ValueError(
            f"'{raw}' is not a valid host name: a bare name must be a DNS label "
            "(letters, digits and hyphens, at most 63 characters, not starting "
            "or ending with a hyphen)."
        )
    return f"{label}{LOCALHOST_SUFFIX}"


def normalize_host_name_or_die(host_name):
    """``normalize_host_name``, exiting with a validation error (1) if invalid.

    Prints the resulting name when a bare name was expanded, so the user sees
    the URL they will actually get.
    """
    try:
        normalized = normalize_host_name(host_name)
    except ValueError as e:
        from ldm_core.ui import UI

        UI.die(str(e), exit_code=1)
        return None
    if (
        isinstance(host_name, str)
        and isinstance(normalized, str)
        and normalized
        and "." not in host_name.strip().rstrip(".")
        and normalized != LOCALHOST
    ):
        from ldm_core.ui import UI

        UI.detail(
            f"Host name '{host_name}' has no domain, so LDM uses "
            f"'{normalized}' (http://{normalized}). It needs no hosts entry."
        )
    return normalized
