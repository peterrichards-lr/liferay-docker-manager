"""OSGi bundles LDM installs to work around Liferay defects (LDM-#2289).

Each workaround is a bundle from liferay-custom-osgi-modules, shipped inside
LDM and placed in the project's ``osgi/modules`` on every ``ldm run`` for the
DXP lines it applies to. It is internal: nothing outside LDM is expected to
know it is there. Once Liferay fixes the defect, move the entry from
``WORKAROUNDS`` to ``RETIRED_PREFIXES``; each project then loses the bundle on
its next run, and nothing else changes.

The tag window is the bundle's ``Import-Package`` range expressed as tags. A
bundle placed on a line outside it would not resolve, and Liferay would report
that at every start.
"""

from __future__ import annotations

from dataclasses import dataclass

from ldm_core.ui import UI


@dataclass(frozen=True)
class Workaround:
    issue: str
    #: The jar's file name under ``ldm_core/resources/portal_workarounds``.
    jar: str
    #: The file name prefix every version of the bundle shares, so a copy left
    #: by an older LDM is replaced rather than installed alongside.
    prefix: str
    #: ``(year, quarter, patch)``; the window is ``[first_tag, end_tag)``.
    first_tag: tuple[int, int, int]
    end_tag: tuple[int, int, int]


WORKAROUNDS: tuple[Workaround, ...] = (
    # Liferay's routes writer publishes Tomcat's :8080 in the DXP domain
    # behind a TLS-terminating proxy (LDM-#2344). The bundle imports
    # com.liferay.portal.kernel.util [96.7,101.0): 96.7.0 is 2026.q1.14-lts,
    # 100.0.0 is 2026.q3. Verified live at both ends.
    Workaround(
        issue="LDM-#2289",
        jar="com.liferay.custom.dxp.routes.domain.jar",
        prefix="com.liferay.custom.dxp.routes.domain",
        first_tag=(2026, 1, 14),
        end_tag=(2026, 4, 0),
    ),
)

#: Prefixes of workarounds Liferay has since fixed. Removed from every project.
RETIRED_PREFIXES: tuple[str, ...] = ()


def applies(workaround, tag, parse_version):
    """Whether ``tag`` falls in the workaround's window.

    Only quarterly tags can: ``7.4.13-u100`` parses as ``(7, 4, 13, ...)`` and
    an unparseable tag as ``(0, 0, 0)``, both below any 2026 window.
    """
    version = tuple(parse_version(tag))[:3]
    return len(version) == 3 and (workaround.first_tag <= version < workaround.end_tag)


def _bundled(workaround):
    import importlib.resources as pkg_resources

    from ldm_core import resources

    return pkg_resources.files(resources) / "portal_workarounds" / workaround.jar


def _copies(modules_dir, prefix):
    return [p for p in modules_dir.glob(f"{prefix}*.jar") if p.is_file()]


def sync_workarounds(manager, paths, tag):
    """Install, refresh or remove each workaround bundle for this project.

    Never raises: a missing workaround is the defect LDM already had, and must
    not stop a run.
    """
    modules_dir = paths.get("modules")
    if not modules_dir:
        return

    try:
        for prefix in RETIRED_PREFIXES:
            for stale in _copies(modules_dir, prefix):
                stale.unlink()

        for workaround in WORKAROUNDS:
            wanted = applies(workaround, tag, manager.parse_version)
            target = modules_dir / workaround.jar

            for copy in _copies(modules_dir, workaround.prefix):
                if not wanted or copy != target:
                    copy.unlink()

            if not wanted:
                continue

            bundled = _bundled(workaround)
            if target.is_file() and target.read_bytes() == bundled.read_bytes():
                continue

            import importlib.resources as pkg_resources

            from ldm_core.utils import atomic_copy

            modules_dir.mkdir(parents=True, exist_ok=True)
            with pkg_resources.as_file(bundled) as source:
                atomic_copy(source, target)
            UI.detail(f"Installed {workaround.jar} ({workaround.issue})")
    except Exception as exc:
        UI.debug(f"Portal workaround sync skipped: {exc}")
