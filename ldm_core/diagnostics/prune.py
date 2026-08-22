import json
import os

from ldm_core.constants import SCRIPT_DIR
from ldm_core.ui import UI
from ldm_core.utils import (
    get_actual_home,
    run_command,
)

LDM_MANAGED_LABEL = "com.liferay.ldm.managed=true"
LDM_PROJECT_LABEL = "com.liferay.ldm.project"
LDM_ROLE_LABEL = "com.liferay.ldm.role"


def _list_ldm_volumes(docker_prefix):
    """Returns [(name, project, role)] for every volume LDM labelled as its own.

    LDM-#1267 attaches ownership labels at creation, which is what makes safe
    cleanup possible at all: without them there is no way to tell an abandoned
    LDM volume from a third-party one, so the only available tool would be a
    blanket `docker volume prune -a` -- which would also destroy the database
    volumes of stopped-but-wanted projects.
    """
    fmt = (
        '{{.Name}}\t{{.Label "'
        + LDM_PROJECT_LABEL
        + '"}}\t{{.Label "'
        + LDM_ROLE_LABEL
        + '"}}'
    )
    res = run_command(
        [
            *docker_prefix,
            "volume",
            "ls",
            "--filter",
            f"label={LDM_MANAGED_LABEL}",
            "--format",
            fmt,
        ],
        check=False,
        capture_output=True,
    )
    volumes = []
    for line in (res or "").strip().splitlines():
        if not line.strip():
            continue
        parts = [*line.split("\t"), "", ""][:3]
        volumes.append((parts[0], parts[1], parts[2] or "unknown"))
    return volumes


def _orphaned_ldm_volumes(handler, docker_prefix):
    """Splits LDM-owned volumes into those whose project still exists and those without.

    A volume is only ever offered for removal when its owning project is no
    longer registered. Anything belonging to a live project -- running or
    stopped -- is never a candidate.
    """
    from ldm_core.utils import sanitize_id

    try:
        registered = {
            sanitize_id(root["path"].name) for root in handler.manager.find_dxp_roots()
        }
    except Exception as e:
        # Fail closed: if the project list can't be established, treat every
        # volume as owned rather than risk offering a live project's database.
        UI.debug(f"Could not enumerate projects for volume ownership check: {e}")
        return []

    return [
        (name, project, role)
        for name, project, role in _list_ldm_volumes(docker_prefix)
        if project and project not in registered
    ]


def _remove_orphaned_volumes(
    handler, docker_prefix, disposable, destructive, prune_all
):
    """Removes orphaned LDM volumes, confirming DATA separately from state.

    LDM-#1267: `state` and `data` are not equally disposable. OSGi state is
    regenerated on the next boot (LDM already wipes it itself on a tag change),
    whereas a `data` volume is the project's database. A single combined
    "remove orphaned volumes?" prompt would be a data-loss trap, so the two are
    always confirmed separately and `--all` never implies the destructive half.
    """
    interactive = not handler.manager.non_interactive
    removed = 0

    if disposable and (
        prune_all
        or (
            interactive
            and UI.confirm(
                f"Remove {len(disposable)} regenerable OSGi state volume(s)?", "N"
            )
        )
    ):
        for name, _project, _role in disposable:
            res = run_command(
                [*docker_prefix, "volume", "rm", name], check=False, capture_output=True
            )
            if res is not None:
                removed += 1

    # Deliberately NOT covered by --all: destroying a database must be an
    # explicit, interactive decision every time.
    if destructive and interactive:
        UI.warning(
            f"{len(destructive)} orphaned volume(s) contain project DATA. "
            "This cannot be undone."
        )
        if UI.confirm(f"Permanently delete {len(destructive)} DATA volume(s)?", "N"):
            for name, _project, _role in destructive:
                res = run_command(
                    [*docker_prefix, "volume", "rm", name],
                    check=False,
                    capture_output=True,
                )
                if res is not None:
                    removed += 1
    elif destructive:
        UI.detail(
            f"Skipped {len(destructive)} DATA volume(s) -- these require an "
            "interactive confirmation and are never removed by --all."
        )

    if removed:
        UI.success(f"Removed {removed} orphaned LDM volume(s).")
    else:
        UI.detail("No orphaned LDM volumes were removed.")


def run_clear_cache(handler):
    """Deprecated: Use ldm cache instead."""
    handler.cmd_cache(target="tags")


def run_cache(handler, target="all"):
    """Manages LDM internal caches (tags, projects)."""
    UI.heading("LDM Cache Management")

    home = get_actual_home()
    tag_cache = home / ".liferay_docker_cache.json"

    cleared = []

    if target in ["tags", "all"] and tag_cache.exists():
        os.remove(tag_cache)
        cleared.append("Docker tag cache")

    if target in ["seeds", "all"]:
        cache_dir = home / ".ldm" / "seeds"
        if cache_dir.exists():
            count = len(list(cache_dir.glob("*.tar.gz")))
            if count > 0:
                import shutil

                shutil.rmtree(cache_dir, ignore_errors=True)
                cleared.append(f"Pre-warmed seeds ({count} files)")

    if target in ["samples", "all"]:
        cache_dir = home / ".ldm" / "references" / "samples"
        if cache_dir.exists():
            import shutil

            shutil.rmtree(cache_dir, ignore_errors=True)
            cleared.append("Sample pack cache")

    if not cleared:
        UI.detail("No caches found to clear.")
    else:
        UI.success(f"Successfully cleared: {', '.join(cleared)}")


def _sum_reclaimed_space(docker_output):
    """Parses the byte count out of docker's own "Total reclaimed space:
    <n><unit>" line (e.g. from `docker image prune -af` / `docker builder
    prune -af`), so LDM can report one combined, human-readable total
    instead of just echoing docker's raw per-command lines."""
    if not docker_output:
        return 0.0

    import re

    units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    total = 0.0
    for match in re.finditer(
        r"Total reclaimed space:\s*([\d.]+)\s*([KMGT]?B)", docker_output, re.IGNORECASE
    ):
        value = float(match.group(1))
        unit = match.group(2).upper()
        total += value * units.get(unit, 1)
    return total


def run_prune(handler):  # noqa: C901, PLR0912, PLR0915
    UI.heading("LDM Global Maintenance - Pruning Orphaned Resources")
    is_dry_run = getattr(handler.manager, "dry_run", False)
    prune_all = getattr(handler.manager.args, "all", False)
    clean_hosts = getattr(handler.manager.args, "clean_hosts", False) or prune_all
    prune_seeds = getattr(handler.manager.args, "seeds", False) or prune_all
    prune_samples = getattr(handler.manager.args, "samples", False) or prune_all
    prune_images = getattr(handler.manager.args, "images", False) or prune_all

    # `ldm system prune --node <target>` inherits --node via base_sub_parent
    # (wired into handler.manager.target by manager.py), but every docker
    # call below hardcoded "docker" regardless -- always pruning the LOCAL
    # daemon even when an explicit remote target was requested.
    target_name = getattr(handler.manager, "target", None)
    from ldm_core.docker_service import DockerService

    docker_prefix = DockerService.get_docker_cmd_prefix(target_name)

    roots = handler.manager.find_dxp_roots()
    active_projects = set()
    active_hostnames = set()
    for r in roots:
        meta = handler.manager.read_meta(r["path"])
        # Use container_name from meta, or fall back to folder name
        name = meta.get("container_name") or r["path"].name
        active_projects.add(name)
        host = meta.get("host_name")
        if host and host != "localhost":
            active_hostnames.add(host)

    if handler.manager.verbose:
        UI.debug(
            f"Active projects identified: {', '.join(active_projects) if active_projects else 'None'}"
        )

    # 1. Orphaned Containers
    # LDM-381: We look for containers with our project label as well as *-db containers
    containers_raw = run_command(
        [
            *docker_prefix,
            "ps",
            "-a",
            "--filter",
            "label=com.liferay.ldm.project",
            "--format",
            '{{.Names}}|{{.Label "com.liferay.ldm.project"}}',
        ],
        check=False,
    )

    orphans = []
    seen_orphan_names = set()
    if containers_raw:
        for line in containers_raw.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue

            # Docker names can sometimes have a leading slash
            name, project = line.split("|", 1)
            name = name.lstrip("/")

            if not project or project not in active_projects:
                if name not in seen_orphan_names:
                    orphans.append(name)
                    seen_orphan_names.add(name)

    # Fallback scan: query all containers to find un-labeled *-db or tmp* database containers
    all_containers_raw = run_command(
        [
            *docker_prefix,
            "ps",
            "-a",
            "--format",
            "{{.Names}}",
        ],
        check=False,
    )
    if all_containers_raw:
        for name in all_containers_raw.splitlines():
            name = name.strip().lstrip("/")
            if not name or name in seen_orphan_names:
                continue

            # Match LDM database naming pattern: <project>-db or tmp*
            if name.endswith("-db"):
                project_name = name[:-3]
                if project_name not in active_projects:
                    orphans.append(name)
                    seen_orphan_names.add(name)
            elif name.startswith("tmp") and ("-db" in name or name.startswith("tmp_")):
                orphans.append(name)
                seen_orphan_names.add(name)

    if orphans:
        UI.detail(f"Found {len(orphans)} orphaned containers from deleted projects.")
        if UI.INFO_MODE or UI.VERBOSE:
            for o in orphans:
                print(f"  - {o}")
        if is_dry_run:
            UI.detail(
                f"{UI.BYELLOW}[Dry Run] Would remove orphaned containers: {', '.join(orphans)}{UI.COLOR_OFF}"
            )
        elif (
            prune_all
            or handler.manager.non_interactive
            or UI.confirm("Remove them? (y/n/q)", "N")
        ):
            from ldm_core.docker_service import DockerService

            for o in orphans:
                DockerService.rm(o, force=True, target_name=target_name)
            UI.success(f"{len(orphans)} orphaned containers removed.")
    else:
        UI.detail("No orphaned containers found.")

    # 2. Orphaned Search Snapshots
    from ldm_core.docker_service import DockerService

    search_name = "liferay-search-global"
    if DockerService.is_running(search_name, target_name=target_name):
        snaps_raw = DockerService.exec(
            search_name,
            [
                "curl",
                "-s",
                "localhost:9200/_snapshot/liferay_backup/_all",
            ],
            check=False,
            target_name=target_name,
        )
        if snaps_raw:
            try:
                data = json.loads(snaps_raw)
                all_snaps = data.get("snapshots", [])
                orphaned_snaps = []
                for s in all_snaps:
                    s_name = s.get("snapshot", "")
                    # LDM search snapshots follow the pattern [project-name]-[timestamp]
                    if "-" in s_name:
                        project_id = s_name.rsplit("-", 2)[0]
                        if project_id not in active_projects:
                            orphaned_snaps.append(s_name)
                    elif s_name == "initial_snapshot":
                        # Special case for legacy manual snapshots
                        orphaned_snaps.append(s_name)

                if orphaned_snaps:
                    UI.detail(f"Found {len(orphaned_snaps)} orphaned search snapshots.")
                    if UI.INFO_MODE or UI.VERBOSE:
                        for s in orphaned_snaps:
                            print(f"  - {s}")
                    if is_dry_run:
                        UI.detail(
                            f"{UI.BYELLOW}[Dry Run] Would remove orphaned search snapshots: {', '.join(orphaned_snaps)}{UI.COLOR_OFF}"
                        )
                    elif (
                        prune_all
                        or handler.manager.non_interactive
                        or UI.confirm("Remove them from global vault?", "N")
                    ):
                        for s in orphaned_snaps:
                            DockerService.exec(
                                search_name,
                                [
                                    "curl",
                                    "-s",
                                    "-X",
                                    "DELETE",
                                    f"localhost:9200/_snapshot/liferay_backup/{s}",
                                ],
                                check=False,
                                target_name=target_name,
                            )
                        UI.success(
                            f"{len(orphaned_snaps)} orphaned search snapshots removed."
                        )
                else:
                    UI.detail("No orphaned search snapshots found.")
            except Exception:
                pass

    # 3. Clean up .tmp files
    tmp_files = list(SCRIPT_DIR.glob("**/.*.tmp"))
    if tmp_files:
        UI.detail(f"Found {len(tmp_files)} temporary files.")
        if is_dry_run:
            UI.detail(
                f"{UI.BYELLOW}[Dry Run] Would remove temporary files: {', '.join(str(f.relative_to(SCRIPT_DIR)) for f in tmp_files)}{UI.COLOR_OFF}"
            )
        elif (
            prune_all
            or handler.manager.non_interactive
            or UI.confirm("Remove them? (y/n/q)", "Y")
        ):
            for f in tmp_files:
                f.unlink()
            UI.success("Temporary files removed.")

    # 4. Orphaned SSL Certificates
    cert_dir = get_actual_home() / "liferay-docker-certs"
    if cert_dir.exists():
        orphaned_certs = []
        # Patterns to look for: {host}.pem, {host}-key.pem, traefik-{host}.yml
        for f in cert_dir.iterdir():
            if not f.is_file():
                continue

            host = None
            if f.name.startswith("traefik-") and f.suffix == ".yml":
                host = f.name[8:-4]
            elif f.name.endswith("-key.pem"):
                host = f.name[:-8]
            elif f.suffix == ".pem":
                host = f.name[:-4]

            if host and host not in active_hostnames:
                orphaned_certs.append(f)

        if orphaned_certs:
            UI.detail(f"Found {len(orphaned_certs)} orphaned SSL artifacts.")
            if UI.INFO_MODE or UI.VERBOSE:
                for c in orphaned_certs:
                    print(f"  - {c.name}")
            if is_dry_run:
                UI.detail(
                    f"{UI.BYELLOW}[Dry Run] Would remove orphaned SSL certificates: {', '.join(f.name for f in orphaned_certs)}{UI.COLOR_OFF}"
                )
            elif (
                prune_all
                or handler.manager.non_interactive
                or UI.confirm("Remove them from global cert store?", "N")
            ):
                for c in orphaned_certs:
                    c.unlink()
                UI.success(f"{len(orphaned_certs)} orphaned SSL artifacts removed.")
        else:
            UI.detail("No orphaned SSL artifacts found.")

    # 5. Pre-warmed Seeds Cache
    seeds_cache = get_actual_home() / ".ldm" / "seeds"
    if seeds_cache.exists():
        seed_files = list(seeds_cache.glob("*.tar.gz"))
        if seed_files:
            size_bytes = sum(f.stat().st_size for f in seed_files)
            size_str = UI.format_size(size_bytes)
            UI.detail(f"Found {len(seed_files)} pre-warmed seeds ({size_str}).")
            if is_dry_run:
                UI.detail(
                    f"{UI.BYELLOW}[Dry Run] Would clear pre-warmed seed cache at {seeds_cache}{UI.COLOR_OFF}"
                )
            elif prune_seeds or (
                not handler.manager.non_interactive
                and UI.confirm("Clear pre-warmed seed cache?", "N")
            ):
                import shutil

                shutil.rmtree(seeds_cache, ignore_errors=True)
                UI.success("Seed cache cleared.")
        else:
            UI.detail("Seed cache is empty.")

    # 6. Sample Extensions Cache
    samples_cache = get_actual_home() / ".ldm" / "references" / "samples"
    if samples_cache.exists():
        sample_files = [f for f in samples_cache.glob("**/*") if f.is_file()]
        if sample_files:
            size_bytes = sum(f.stat().st_size for f in sample_files)
            size_str = UI.format_size(size_bytes)
            UI.detail(f"Found sample extension cache ({size_str}).")
            if is_dry_run:
                UI.detail(
                    f"{UI.BYELLOW}[Dry Run] Would clear sample extension cache at {samples_cache}{UI.COLOR_OFF}"
                )
            elif prune_samples or (
                not handler.manager.non_interactive
                and UI.confirm("Clear sample extension cache?", "N")
            ):
                import shutil

                shutil.rmtree(samples_cache, ignore_errors=True)
                UI.success("Sample cache cleared.")
        else:
            UI.detail("Sample cache is empty.")

    # 7. Global Docker Pruning (Dangling Volumes)
    if is_dry_run:
        UI.detail(
            f"{UI.BYELLOW}[Dry Run] Would run volume prune (docker volume prune -f).{UI.COLOR_OFF}"
        )
    elif prune_all or (
        not handler.manager.non_interactive
        and UI.confirm("Remove all dangling Docker volumes? (y/n/q)", "N")
    ):
        UI.detail("Pruning dangling Docker volumes...")
        UI.detail("Command: docker volume prune -f")
        vol_res = run_command(
            [*docker_prefix, "volume", "prune", "-f"], check=False, capture_output=True
        )
        # LDM-#1266: report what was actually reclaimed. `docker volume prune`
        # removes ANONYMOUS volumes only unless `-a` is passed, and LDM creates
        # exclusively *named* volumes -- so this step reclaims nothing LDM made
        # and used to print an unconditional "complete" regardless. `-a` is not
        # the fix: it would also delete the database volumes of stopped
        # projects. LDM-owned volumes are handled by the labelled sweep below.
        reclaimed_vol = _sum_reclaimed_space(vol_res)
        if reclaimed_vol:
            UI.success(
                f"Volume pruning complete ({UI.format_size(reclaimed_vol)} reclaimed)."
            )
        else:
            UI.detail(
                "No anonymous volumes to reclaim. "
                "LDM's own volumes are named and are handled separately below."
            )

    # 7a. LDM-owned volumes whose project no longer exists (LDM-#1266/#1267).
    # Selected by ownership label, never by a blanket `-a`, and split by role
    # so disposable OSGi state can be reclaimed without ever sweeping a
    # database along with it.
    orphans = _orphaned_ldm_volumes(handler, docker_prefix)
    if orphans:
        disposable = [v for v in orphans if v[2] == "state"]
        destructive = [v for v in orphans if v[2] != "state"]

        UI.detail(f"Found {len(orphans)} LDM volume(s) whose project no longer exists.")

        if disposable:
            UI.detail(
                f"  {len(disposable)} regenerable (OSGi state): "
                + ", ".join(n for n, _, _ in disposable[:3])
                + (" ..." if len(disposable) > 3 else "")
            )
        if destructive:
            UI.warning(
                f"  {len(destructive)} contain project DATA (databases) -- "
                "removing these is irreversible: "
                + ", ".join(n for n, _, _ in destructive[:3])
                + (" ..." if len(destructive) > 3 else "")
            )

        if is_dry_run:
            for name, project, role in orphans:
                UI.detail(
                    f"{UI.BYELLOW}[Dry Run] Would remove volume {name} "
                    f"(project '{project}', role {role}).{UI.COLOR_OFF}"
                )
        else:
            _remove_orphaned_volumes(
                handler, docker_prefix, disposable, destructive, prune_all
            )
    elif is_dry_run:
        UI.detail(f"{UI.BYELLOW}[Dry Run] No orphaned LDM volumes found.{UI.COLOR_OFF}")

    # 7b. Dangling/unused Docker images and unused build cache (LDM-#1086).
    # Volume/container/cert/cache pruning above only ever reclaims a few MB --
    # unused images and build cache are almost always the actual disk hog on
    # a long-lived dev machine, and this used to be nothing more than a
    # printed hint pointing the user at raw `docker system prune -af`
    # themselves instead of LDM doing it.
    if is_dry_run:
        UI.detail(
            f"{UI.BYELLOW}[Dry Run] Would run image prune (docker image prune "
            f"-af) and build cache prune (docker builder prune -af).{UI.COLOR_OFF}"
        )
    elif prune_images or (
        not handler.manager.non_interactive
        and UI.confirm(
            "Remove unused Docker images and build cache? (can be large, safe to re-pull/rebuild)",
            "N",
        )
    ):
        UI.detail("Pruning unused Docker images and build cache...")
        UI.detail("Command: docker image prune -af")
        image_res = run_command([*docker_prefix, "image", "prune", "-af"], check=False)
        UI.detail("Command: docker builder prune -af")
        cache_res = run_command(
            [*docker_prefix, "builder", "prune", "-af"], check=False
        )
        reclaimed = _sum_reclaimed_space(image_res) + _sum_reclaimed_space(cache_res)
        if reclaimed:
            UI.success(
                f"Docker image/build-cache pruning complete ({UI.format_size(reclaimed)} reclaimed)."
            )
        else:
            UI.success("Docker image/build-cache pruning complete.")
    elif not handler.manager.non_interactive:
        UI.detail(
            f"\n{UI.CYAN}ℹ{UI.COLOR_OFF} Hint: run again with --images (or --all) to also "
            f"reclaim unused Docker images and build cache, or do it yourself: "
            f"{UI.WHITE}docker system prune -af{UI.COLOR_OFF}"
        )

    # 7. DNS Cleanup (Explicitly requested via --clean-hosts)
    if clean_hosts:
        if is_dry_run:
            UI.detail(
                f"{UI.BYELLOW}[Dry Run] Would remove ALL LDM-managed entries from hosts file.{UI.COLOR_OFF}"
            )
        elif prune_all or (
            not handler.manager.non_interactive
            and UI.confirm("Remove ALL LDM-managed entries from your hosts file?", "N")
        ):
            handler.manager._remove_hosts_entries(all_ldm=True)

    UI.detail("Prune complete.")
