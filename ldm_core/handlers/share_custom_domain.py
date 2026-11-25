"""Per-project custom (vanity) share domains (LDM-#2503).

The lfr-tunnel contract this module is written against, confirmed with the
lfr-tunnel maintainers on 2026-11-25:

- A custom domain is the WHOLE public hostname. Nothing is prepended, wildcards
  are refused, and one live tunnel holds a domain at a time. Each hostname is
  registered to a user in the portal; the default quota is one per user.
- The client takes it as ``-domain <host>``. ``-status-json -subdomain <sub>``
  still finds the tunnel by the REQUESTED subdomain, and the public address is
  in ``public_urls`` (``https://<custom domain>``) -- never built from the
  subdomain.
- That lookup works from lfr-tunnel v1.52.12 (lfr-tunnel #2595). Before it, a
  tunnel started with ``-domain`` wrote its state under an empty name, so the
  lookup said ``{"running": false}`` for a connected tunnel and LDM's health
  check stopped it -- what broke v2.26.16-pre.1.
- Refusals: 409 ``Domain is already taken`` (the one lease is held), 409
  ``Domain is reserved by another user`` / ``... in quarantine``, and 403 when
  the domain is not registered to the user or the quota is spent. With
  ``-background`` (v1.52.11+) a refusal exits 1 within about 20s and prints the
  gateway's line.

Why a separate meta key (``share_custom_domain``) rather than ``share_domain``:
every share start before this change copied whatever domain it resolved --
including a vanity domain from ``~/.ldmrc`` -- into the project's
``share_domain``. Requesting every custom ``share_domain`` would therefore make
each of those projects ask for the same domain, and all but one would be
refused. A custom domain is requested only when it was named on the command
line or stored under ``share_custom_domain`` by such a request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ldm_core.utils import is_local_host, version_to_tuple

#: The first lfr-tunnel release whose ``-status-json -subdomain`` finds a tunnel
#: started with ``-domain`` (lfr-tunnel #2595 / PR #2601).
MIN_CUSTOM_DOMAIN_CLIENT_VERSION = "1.52.12"

#: Where a project keeps the custom domain it asked for.
CUSTOM_DOMAIN_META_KEY = "share_custom_domain"


def client_supports_custom_domain(version: str | None) -> bool:
    """True when an lfr-tunnel client of ``version`` can serve a custom domain.

    An unknown version is not assumed to be new enough: the failure it risks is
    a working tunnel stopped by LDM's own health check.
    """
    if not version:
        return False
    return version_to_tuple(version) >= version_to_tuple(
        MIN_CUSTOM_DOMAIN_CLIENT_VERSION
    )


def is_loopback_host_name(host: str | None) -> bool:
    """True for a host that can only mean this machine.

    ``is_local_host`` covers ``localhost`` and loopback addresses. RFC 6761
    reserves every name under ``.localhost`` for loopback as well, so a project
    called ``myproj.localhost`` is on this machine just as surely -- and is not
    a host the tunnel needs to be pointed at, nor one that makes the native
    provider able to reach a remote compute node.
    """
    if is_local_host(host):
        return True
    name = str(host).strip().lower().rstrip(".")
    return name.endswith(".localhost")


def public_url_for_custom_domain(status: dict | None, domain: str) -> str | None:
    """The ``public_urls`` entry serving ``domain``, from a ``-status-json`` object.

    Read, never composed: the client is the authority on what the gateway
    leased. Returns None when the status names no such URL.
    """
    if not isinstance(status, dict):
        return None
    urls = status.get("public_urls")
    if not isinstance(urls, list):
        return None
    wanted = domain.strip().lower().rstrip(".")
    for url in urls:
        text = str(url).strip()
        host = re.sub(r"^[a-z]+://", "", text, flags=re.IGNORECASE)
        host = host.split("/", 1)[0].split(":", 1)[0].lower().rstrip(".")
        if host == wanted:
            return text
    return None


@dataclass(frozen=True)
class CustomDomainRefusal:
    """Why the gateway would not give a tunnel the custom domain."""

    kind: str
    message: str
    tip: str
    gateway_line: str | None

    def as_reason(self) -> str:
        """One block of text for the health check's failure message."""
        parts = [self.message]
        if self.gateway_line:
            parts.append(f"Gateway said: {self.gateway_line}")
        parts.append(self.tip)
        return "\n".join(parts)


# Matched lower-cased. Order matters: the specific 409s before the generic 403.
_REFUSALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("taken", ("domain is already taken",)),
    ("reserved", ("domain is reserved by another user",)),
    ("quarantine", ("domain is currently in quarantine", "in quarantine")),
    ("quota", ("custom domain reservation quota",)),
    ("unregistered", ("custom domain is not registered to you",)),
)


_STANDARD_GATEWAY = "or pass --domain default to share on the standard gateway."

# kind -> (message, tip). `{domain}` is filled in; "taken" gains a holder hint.
_REFUSAL_TEXT: dict[str, tuple[str, str]] = {
    "taken": (
        "Custom domain '{domain}' is already in use by another tunnel.",
        "A custom domain serves one tunnel at a time.",
    ),
    "reserved": (
        "Custom domain '{domain}' is registered to another user.",
        "Register a domain you own in the Liferay Tunnel portal, " + _STANDARD_GATEWAY,
    ),
    "quarantine": (
        "Custom domain '{domain}' is in quarantine on the gateway.",
        "It was released recently and cannot be leased yet. Try again later, "
        + _STANDARD_GATEWAY,
    ),
    "quota": (
        "Custom domain '{domain}' would exceed your custom-domain quota.",
        "The default quota is one custom domain per user. Release one in the "
        "Liferay Tunnel portal, " + _STANDARD_GATEWAY,
    ),
    "unregistered": (
        "Custom domain '{domain}' is not registered to you.",
        "Point the domain at the gateway with a CNAME record and register it "
        "in the Liferay Tunnel portal, then start the share again; "
        + _STANDARD_GATEWAY,
    ),
    "other": (
        "The gateway refused custom domain '{domain}'.",
        "Check the domain in the Liferay Tunnel portal, " + _STANDARD_GATEWAY,
    ),
}


def _gateway_line(text: str, needles: tuple[str, ...]) -> str | None:
    """The line the gateway's refusal arrived on, trimmed of log decoration."""
    for line in text.splitlines():
        low = line.lower()
        if any(n in low for n in needles):
            cleaned = line.strip()
            # slog's text handler quotes the message: msg="[Error] ...".
            match = re.search(r'msg="(.*)"\s*$', cleaned)
            if match:
                cleaned = match.group(1)
            return cleaned.replace("\\n", "").strip()
    return None


def custom_domain_refusal(
    text: str | None, domain: str, other_projects: list[str] | None = None
) -> CustomDomainRefusal | None:
    """Classify a refusal of ``domain`` found in client output or its log.

    ``other_projects`` names projects whose meta claims the same custom domain,
    so a "taken" refusal can say which tunnel is the likely holder.

    Returns None when ``text`` holds no custom-domain refusal, so the caller's
    existing diagnosis (token, subdomain conflict, timeout) still applies.
    """
    if not text:
        return None
    low = text.lower()
    kind = next((k for k, needles in _REFUSALS if any(n in low for n in needles)), None)
    # A 403 or 409 the gateway worded differently is still a refusal of the
    # domain when a custom domain was what was asked for.
    if kind is None and re.search(r"gateway error \((403|409)\)", low):
        kind = "other"
    if kind is None:
        return None

    needles = dict(_REFUSALS).get(kind, ("gateway error (",))
    line = _gateway_line(text, needles) or _gateway_line(text, ("gateway error (",))

    message, tip = _REFUSAL_TEXT[kind]
    if kind == "taken":
        others = ", ".join(sorted(other_projects or []))
        tip += (
            f" Project(s) {others} also use it; stop that share first:\n"
            "    ldm share stop <project>"
            if others
            else " Stop the other tunnel using it, then start this one again."
        )
    return CustomDomainRefusal(kind, message.format(domain=domain), tip, line)


def projects_claiming_domain(manager, domain: str, exclude_root=None) -> list[str]:
    """Names of OTHER projects whose meta holds ``domain`` as their custom domain.

    Best effort, never raises: this feeds a warning and a refusal message, and
    a project scan that fails must not stop a share.
    """
    finder = getattr(manager, "find_dxp_roots", None)
    reader = getattr(manager, "read_meta", None)
    if not callable(finder) or not callable(reader):
        return []
    wanted = domain.strip().lower()
    excluded = str(exclude_root.resolve()) if exclude_root else None
    names: list[str] = []
    try:
        for entry in finder() or []:
            path = entry.get("path") if isinstance(entry, dict) else entry
            if path is None:
                continue
            if excluded and str(path.resolve()) == excluded:
                continue
            meta = reader(path) or {}
            claimed = str(meta.get(CUSTOM_DOMAIN_META_KEY) or "").strip().lower()
            if claimed and claimed == wanted:
                names.append(path.name)
    except Exception:
        return names
    return names
