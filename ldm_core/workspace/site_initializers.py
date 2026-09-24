"""Deferred deployment of site-initializer client extensions (LDM-#1779).

A site-initializer client extension must NOT be present in
``osgi/client-extensions/`` when the portal first starts against an empty
database. ``SiteInitializerClientExtension``'s bundle tracker opens during
portal startup, before the built-in ``welcome`` and ``cms`` site initializers
have created the Guest site's layouts, and ``ServiceContextFactory`` needs one.
Measured on DXP ``2026.q3.0``::

    ERROR bundle com.liferay.site.initializer.extender:1.0.147
      [SiteInitializerClientExtension(4567)] : The activate method has thrown
      an exception
    java.lang.RuntimeException: java.lang.NullPointerException:
      Cannot invoke "com.liferay.portal.kernel.model.Layout.getGroupId()"
      because "layout" is null
        at com.liferay.portal.util.PortalImpl.getCanonicalURL(PortalImpl.java:1556)
        at ...ServiceContextFactory._getInstance(ServiceContextFactory.java:176)
        at ...SiteResourceImpl._addGroup(SiteResourceImpl.java:482)
        at ...SiteInitializerClientExtension.addingBundle(...:94)

**The failure is silent.** The bundle still logs ``STARTED``, no site is
created, no fragments are imported, and the only sign is one stack trace among
several thousand startup lines. The same artifact dropped into the *running*
portal initialises in ~100 ms.

So ``ldm import`` stages such an artifact here instead of deploying it, and
``_wait_for_ready`` copies it into ``osgi/client-extensions/`` once the portal
is healthy. A warning alone was rejected: it would leave the consumer with a
site that silently never initialises, which is the exact class of failure this
cycle has been spent removing.

Only the *import* path defers. ``ldm deploy`` and the ``ldm dev`` file monitor
call ``_sync_cx_artifact`` with the default ``defer_site_initializers=False``,
because dropping an artifact into a portal that is already up is precisely the
moment that works.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from ldm_core.ui import UI

#: The header Liferay's ``SiteInitializerClientExtension`` tracks bundles on.
#: Read out of a real built artifact --
#: ``ldm-cx-samples/client-extensions/ecopulse-site-initializer``, whose
#: ``client-extension.yaml`` declares ``type: siteInitializer`` and whose
#: ``dist/*.zip`` carries this key. The yaml is a *source* descriptor and is
#: not present in the built zip, so it cannot be the marker here.
SITE_INITIALIZER_HEADER = "Liferay-Client-Extension-Site-Initializer"

#: Where that header lives inside the built zip.
PLUGIN_PACKAGE_PROPERTIES = "WEB-INF/liferay-plugin-package.properties"

#: Staging directory, relative to the project root. ``.ldm/`` already holds
#: project-scoped LDM state (``fragment-overrides.json``) and is archived into
#: a ``.ldmp`` package wholesale, so a package built from a project whose
#: initializer has not yet been deployed carries the deferral with it.
DEFERRED_DIRNAME = "deferred-client-extensions"


def deferred_dir(root: Path) -> Path:
    """The staging directory for one project root."""
    return Path(root) / ".ldm" / DEFERRED_DIRNAME


def is_site_initializer_zip(zip_path: Path) -> bool:
    """True when the built client-extension zip declares a site initializer.

    Keyed on the header the extender itself tracks, not on the file name and
    not on the source ``client-extension.yaml``. A zip that cannot be read is
    reported as *not* a site initializer -- deferring an artifact we failed to
    identify would change the deployment moment for extensions that are fine
    where they are.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            raw = archive.read(PLUGIN_PACKAGE_PROPERTIES).decode(
                "utf-8", errors="replace"
            )
    except Exception:
        return False

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == SITE_INITIALIZER_HEADER and value.strip():
            return True
    return False


def rejected_dir(root: Path) -> Path:
    """Where an artifact LDM refuses to deploy is parked (LDM-#1962)."""
    return Path(root) / ".ldm" / "rejected-client-extensions"


def cx_service_missing_lcp_id(zip_path: Path) -> bool:
    """True when a zip is a SERVICE extension that declares no LCP.json id.

    LDM-#1962. `is_service` keys on a Dockerfile, so a zip carrying one is
    deployed as a container and needs an identity. That identity comes only
    from `LCP.json`, and every real client extension has one -- every sample in
    `ldm-cx-samples` carries both `client-extension.yaml` and `LCP.json`.

    LDM must not invent one from the directory name. Liferay names the routes
    tree it publishes from the extension's `projectName`, which routinely
    differs from the folder (`ecopulse-headless-auth` declares
    `ecopulseheadlessauth`), so a guess would bind a directory Liferay never
    writes to -- LDM-#1944's failure, arrived at deliberately.

    A zip that cannot be read is reported as fine. Refusing an artifact we
    failed to identify would block deployments that are perfectly valid, and
    the existing `is_site_initializer_zip` takes the same view for the same
    reason.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            names = archive.namelist()
            has_dockerfile = any(
                n == "Dockerfile" or n.endswith("/Dockerfile") for n in names
            )
            if not has_dockerfile:
                # No Dockerfile means no container, so no identity is needed.
                return False
            lcp = next(
                (n for n in names if n == "LCP.json" or n.endswith("/LCP.json")),
                None,
            )
            if lcp is None:
                # No LCP.json at all: nothing declares `kind`, so
                # `_scan_extension_metadata` leaves it None and `is_service`
                # is True. A Dockerfile with no identity beside it.
                return True
            data = json.loads(archive.read(lcp).decode("utf-8", errors="replace"))
            if not isinstance(data, dict):
                return False
            # Mirror `is_service` exactly: a Dockerfile alone is not enough.
            # `kind: Job` is a one-shot task, not a running service -- a real
            # site initializer ships a Dockerfile AND `"kind": "Job"`, and is
            # never given a container identity.
            if data.get("kind") == "Job":
                return False
            return not data.get("id")
    except Exception:
        return False


def reject_cx_artifact(zip_path: Path, root: Path, reason: str) -> Path:
    """Park an artifact LDM will not deploy, and say where it went.

    LDM-#1962. `osgi/client-extensions` is a bind mount Liferay reads from, so
    an artifact left there is deployed whatever LDM thinks of it. Moving it is
    what makes the refusal real.

    Nothing is destroyed -- the zip is moved, not deleted, so it can be
    inspected and put back once it carries what it needs.
    """
    from ldm_core.utils import safe_move

    target_dir = rejected_dir(root)
    target_dir.mkdir(parents=True, exist_ok=True)
    parked = target_dir / Path(zip_path).name
    if parked.exists():
        parked.unlink()
    safe_move(str(zip_path), str(parked))

    UI.error(
        f"Refusing to deploy client extension '{Path(zip_path).stem}': {reason}",
        details=(
            "A client extension that ships a Dockerfile is deployed as a "
            "service, and its identity comes from LCP.json. LDM will not "
            "derive it from the directory name: Liferay names the config tree "
            "it publishes from the extension's projectName, which routinely "
            "differs from the folder, so a guess binds a directory Liferay "
            "never writes to."
        ),
        tip=(
            f"The artifact was moved to {parked} and NOT deployed. Add an "
            f"LCP.json declaring its id, then deploy it again."
        ),
    )
    return parked


def stage_for_deferred_deploy(zip_path: Path, root: Path) -> Path:
    """Move ``zip_path`` into the project's deferral staging directory.

    Returns the staged path. The caller is expected to have already placed the
    expanded build context under ``client-extensions/<stem>/`` -- that folder
    is what ``scan_client_extensions`` reads to describe the extension to
    compose, and it is deliberately left in place so the deferral is invisible
    to everything except the deployment moment.
    """
    from ldm_core.utils import safe_move

    target_dir = deferred_dir(root)
    target_dir.mkdir(parents=True, exist_ok=True)
    staged = target_dir / Path(zip_path).name
    if staged.exists():
        staged.unlink()
    safe_move(str(zip_path), str(staged))
    return staged


def pending(root: Path) -> list[Path]:
    """Staged site-initializer artifacts awaiting a healthy portal."""
    target_dir = deferred_dir(root)
    if not target_dir.is_dir():
        return []
    return sorted(target_dir.glob("*.zip"))


def deploy_pending(
    manager: Any, root: Path, paths: dict, project_meta: dict
) -> list[str]:
    """Copy every staged initializer into ``osgi/client-extensions/``.

    Called once the portal is healthy. Returns the names deployed, so the
    caller can stay quiet when there was nothing to do.

    The OAuth URL rewrite that ``scan_client_extensions`` performs at compose
    time is applied here instead, because it only ever runs against a zip that
    is already in ``osgi/client-extensions/`` and a deferred artifact is not
    there yet. Skipping it would trade one silent failure for another: a
    site-initializer bundling an ``oAuthApplicationHeadlessServer`` would get
    ``localhost`` URLs on a project served under a custom host name.
    """
    staged = pending(root)
    if not staged:
        return []

    cx_dir = paths["cx"]
    cx_dir.mkdir(parents=True, exist_ok=True)
    host_name = project_meta.get("host_name") or "localhost"

    # `UI.info`, not `UI.detail`. Site initialisation takes tens of seconds
    # after this point and creates a site the user is waiting for, so a
    # default run that said nothing here would look like the boot had simply
    # finished with no site -- which is the symptom this whole change exists
    # to remove. LDM-#1728 is the same lesson: a poll rendered with `UI.detail`
    # is invisible on a default run and indistinguishable from a hang.
    UI.info(
        "Deploying site initializer(s) held back from the first boot: "
        + ", ".join(a.name for a in staged)
    )

    deployed: list[str] = []
    for artifact in staged:
        try:
            rewrite = getattr(
                getattr(manager, "workspace", None), "_rewrite_oauth_urls_in_zip", None
            )
            if callable(rewrite):
                rewrite(
                    artifact,
                    host_name,
                    artifact.stem.lower().replace("_", "-"),
                    root,
                )
            from ldm_core.utils import safe_move

            destination = cx_dir / artifact.name
            if destination.exists():
                destination.unlink()
            safe_move(str(artifact), str(destination))
            deployed.append(artifact.name)
        except Exception as exc:
            UI.error(
                f"  ! Failed to deploy deferred site initializer {artifact.name}: {exc}"
            )

    return deployed
