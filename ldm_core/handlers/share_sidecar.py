"""Which gateway variable the lfr-tunnel-docker sidecar gets (LDM-#2536).

The published image has no gateway built in (its build sets no
DefaultServerURL), so a sidecar given none exits with "No tunnel server
configured". Until now LDM always gave it `LFT_CLIENT_SERVER`, which PINS the
client: no region election and no failover.

`LFT_CLIENT_BOOTSTRAP` names the gateway to start from and keeps both. The
first image that reads it is `lfr-tunnel:v1.52.12` -- confirmed with the
lfr-tunnel maintainers on 2026-11-25; v1.52.11 ignores it and would exit with
no gateway. From v1.52.12 the client refuses `LFT_CLIENT_BOOTSTRAP` together
with any pinning variable, so exactly one is ever set.

**An unparseable tag keeps `LFT_CLIENT_SERVER`.** That covers `:latest` (the
default image), an untagged name and a digest. `:latest` follows the newest
release, but nothing at compose time says which release that is: the compose
file is written before the image is pulled, and `pull_policy: always` can
replace a local copy with a newer one -- or a mirror can serve an older one.
`LFT_CLIENT_SERVER` works on every image; `LFT_CLIENT_BOOTSTRAP` on an older
one does not start. A pinned tag (`--share-image peterjrichards/lfr-tunnel:v1.52.12`)
is how to get failover today; resolving `:latest` is LDM-#2542.
"""

from __future__ import annotations

import re

from ldm_core.utils import version_to_tuple

#: The first lfr-tunnel image that reads LFT_CLIENT_BOOTSTRAP.
MIN_BOOTSTRAP_IMAGE_VERSION = "1.52.12"

_TAG_VERSION = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)$")


def image_tag_version(image: str | None) -> str | None:
    """The release an image reference names by its tag, or None.

    ``peterjrichards/lfr-tunnel:v1.52.12`` gives ``1.52.12``. None for
    ``:latest``, no tag, a digest (``@sha256:...``), or any tag that is not a
    version. A registry port (``host:5000/img``) is not a tag: the tag is only
    what follows the last ``:`` after the last ``/``.
    """
    if not isinstance(image, str) or not image.strip() or "@" in image:
        return None
    last = image.strip().rsplit("/", 1)[-1]
    if ":" not in last:
        return None
    match = _TAG_VERSION.match(last.rsplit(":", 1)[1])
    return match.group(1) if match else None


def image_reads_bootstrap(image: str | None) -> bool:
    """True when ``image`` is known to read LFT_CLIENT_BOOTSTRAP."""
    version = image_tag_version(image)
    if not version:
        return False
    return version_to_tuple(version) >= version_to_tuple(MIN_BOOTSTRAP_IMAGE_VERSION)


def sidecar_gateway_env(image: str | None, gateway_url: str, pinned: bool) -> str:
    """The one gateway variable for the sidecar's environment.

    ``pinned`` means the user asked for this gateway (a pin variable in LDM's
    environment, or a known gateway named on this invocation) -- that stays a
    pin on every image. Otherwise an image known to read it gets
    ``LFT_CLIENT_BOOTSTRAP``, and everything else ``LFT_CLIENT_SERVER``.
    """
    if not pinned and image_reads_bootstrap(image):
        return f"LFT_CLIENT_BOOTSTRAP={gateway_url}"
    return f"LFT_CLIENT_SERVER={gateway_url}"
