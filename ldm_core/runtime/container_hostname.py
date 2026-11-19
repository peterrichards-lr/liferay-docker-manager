"""The Liferay container's hostname (LDM-#2445).

Docker defaults a container's hostname to its ID, which changes every time the
container is recreated. Some Marketplace apps (Digital Sales Room among them)
issue activation keys locked to the container hostname, so a recreated
container fails its licence check. The only workaround was to hand-edit the
rendered `docker-compose.yml`, which LDM overwrites on the next regeneration.

The value lives in project meta as `container_hostname`; the composer renders
it into the Liferay service, and this module confirms the running container
actually carries it -- the same request-then-confirm shape as
`ldm_core/runtime/mac_pin.py`, for the same reason: the compose key is a
request, and a licence check that fails presents as a healthy container.

## Why dots are refused

The value is validated as a single RFC 1123 label. Whether host-locked keys
are checked against the short hostname or a fully qualified one has not been
verified, so a dotted name is refused until it has been, rather than accepted
and possibly never matching.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ldm_core.ui import UI

META_KEY = "container_hostname"
MAX_LENGTH = 63

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def validate(name) -> bool:
    """True when `name` is a single RFC 1123 label LDM will accept.

    Lowercase letters, digits and hyphens; 1-63 characters; no leading or
    trailing hyphen. Dots are refused (see the module docstring).
    """
    if not isinstance(name, str):
        return False
    return 0 < len(name) <= MAX_LENGTH and bool(_LABEL.match(name))


def die_if_invalid(name) -> None:
    """Refuse an unusable hostname before anything is written."""
    if validate(name):
        return
    UI.die(
        f"'{name}' is not a valid container hostname.",
        details=(
            f"Use lowercase letters, digits and hyphens only, at most "
            f"{MAX_LENGTH} characters, not starting or ending with a hyphen. "
            "Dots are not accepted yet: it is unverified whether host-locked "
            "activation keys are checked against a fully qualified name."
        ),
        exit_code=1,
    )


def configured(project_meta) -> str:
    """The hostname configured for a project, or "" when there is none."""
    if not isinstance(project_meta, dict):
        return ""
    value = project_meta.get(META_KEY)
    return value.strip() if isinstance(value, str) else ""


def projects_sharing(name: str, project_root) -> list[str]:
    """Other registered projects whose meta configures the same hostname.

    Allowed, but worth saying: two projects with the same hostname will both
    pass a host-locked licence check, which may or may not be what the
    operator intends. Best-effort -- an unreadable registry or meta is skipped.
    """
    if not name:
        return []
    from ldm_core.constants import REGISTRY_FILE
    from ldm_core.utils import get_actual_home, read_meta, resolve_meta_file_path

    registry_path = get_actual_home() / ".ldm" / REGISTRY_FILE
    try:
        registry = json.loads(registry_path.read_text())
    except Exception:
        return []
    if not isinstance(registry, dict):
        return []

    try:
        own_root = Path(project_root).resolve() if project_root else None
    except Exception:
        own_root = None

    others = []
    for project_name, entry in registry.items():
        path = entry.get("path") if isinstance(entry, dict) else entry
        if not path:
            continue
        try:
            root = Path(path).resolve()
            if own_root is not None and root == own_root:
                continue
            meta_file = resolve_meta_file_path(root)
            if not meta_file.exists():
                continue
            if configured(read_meta(meta_file)) == name:
                others.append(str(project_name))
        except Exception:
            continue
    return sorted(others)


def warn_if_shared(name: str, project_root) -> None:
    others = projects_sharing(name, project_root)
    if others:
        UI.warning(
            f"Container hostname '{name}' is also configured for: "
            f"{', '.join(others)}. Allowed, but a host-locked activation key "
            "will then validate in each of them."
        )


def verify_container_hostname(
    project_meta,
    target_name: str | None,
    *,
    dry_run: bool = False,
    recreate_hint: str = "ldm run",
) -> None:
    """Confirm the running Liferay container carries the configured hostname.

    Silent when none is configured or when the project is scaled (the composer
    does not set one then, because replicas would share it).
    """
    if dry_run:
        return

    expected = configured(project_meta)
    if not expected:
        return
    try:
        if int(project_meta.get("scale_liferay", 1)) != 1:
            return
    except (TypeError, ValueError):
        pass

    from ldm_core.docker_service import DockerService
    from ldm_core.utils import liferay_container_of

    container = liferay_container_of(project_meta)
    actual = DockerService.container_hostname(container, target_name)

    if not actual:
        UI.warning(
            f"Could not read the hostname of '{container}' to confirm it is "
            f"'{expected}' (LDM-#2445). If a host-locked activation key fails, "
            "check it by hand."
        )
        return

    if actual != expected:
        UI.die(
            f"The container's hostname is '{actual}', not the '{expected}' "
            "configured for this project.",
            details=(
                "Host-locked Marketplace activation keys are validated against "
                "the container hostname, so the app would fail its licence check."
            ),
            tip=(
                "The hostname is fixed when a container is created. Recreate "
                f"it:\n    {recreate_hint}"
            ),
            exit_code=3,
        )

    UI.detail(f"Container hostname is '{actual}' as configured.")
