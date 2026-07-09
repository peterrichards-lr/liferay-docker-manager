import json
import os

from ldm_core.constants import SCRIPT_DIR
from ldm_core.ui import UI
from ldm_core.utils import (
    get_actual_home,
    run_command,
)


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
        UI.info("No caches found to clear.")
    else:
        UI.success(f"Successfully cleared: {', '.join(cleared)}")

def run_prune(handler):
    UI.heading("LDM Global Maintenance - Pruning Orphaned Resources")
    is_dry_run = getattr(handler.manager, "dry_run", False)
    prune_all = getattr(handler.manager.args, "all", False)
    clean_hosts = getattr(handler.manager.args, "clean_hosts", False) or prune_all
    prune_seeds = getattr(handler.manager.args, "seeds", False) or prune_all
    prune_samples = getattr(handler.manager.args, "samples", False) or prune_all

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
    # LDM-381: We look for containers with our project label
    containers_raw = run_command(
        [
            "docker",
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
    if containers_raw:
        for line in containers_raw.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue

            # Docker names can sometimes have a leading slash
            name, project = line.split("|", 1)
            name = name.lstrip("/")

            if not project or project not in active_projects:
                orphans.append(name)

    if orphans:
        UI.info(f"Found {len(orphans)} orphaned containers from deleted projects.")
        if UI.INFO_MODE or UI.VERBOSE:
            for o in orphans:
                print(f"  - {o}")
        if is_dry_run:
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would remove orphaned containers: {', '.join(orphans)}{UI.COLOR_OFF}"
            )
        elif (
            prune_all
            or handler.manager.non_interactive
            or UI.confirm("Remove them? (y/n/q)", "N")
        ):
            from ldm_core.docker_service import DockerService

            for o in orphans:
                DockerService.rm(o, force=True)
            UI.success(f"{len(orphans)} orphaned containers removed.")
    else:
        UI.detail("No orphaned containers found.")

    # 2. Orphaned Search Snapshots
    from ldm_core.docker_service import DockerService

    search_name = "liferay-search-global"
    if DockerService.is_running(search_name):
        snaps_raw = DockerService.exec(
            search_name,
            [
                "curl",
                "-s",
                "localhost:9200/_snapshot/liferay_backup/_all",
            ],
            check=False,
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
                    UI.info(
                        f"Found {len(orphaned_snaps)} orphaned search snapshots."
                    )
                    if UI.INFO_MODE or UI.VERBOSE:
                        for s in orphaned_snaps:
                            print(f"  - {s}")
                    if is_dry_run:
                        UI.info(
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
        UI.info(f"Found {len(tmp_files)} temporary files.")
        if is_dry_run:
            UI.info(
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
            UI.info(f"Found {len(orphaned_certs)} orphaned SSL artifacts.")
            if UI.INFO_MODE or UI.VERBOSE:
                for c in orphaned_certs:
                    print(f"  - {c.name}")
            if is_dry_run:
                UI.info(
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
            UI.info(f"Found {len(seed_files)} pre-warmed seeds ({size_str}).")
            if is_dry_run:
                UI.info(
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
            UI.info(f"Found sample extension cache ({size_str}).")
            if is_dry_run:
                UI.info(
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
        UI.info(
            f"{UI.BYELLOW}[Dry Run] Would run volume prune (docker volume prune -f).{UI.COLOR_OFF}"
        )
    elif prune_all or (
        not handler.manager.non_interactive
        and UI.confirm("Remove all dangling Docker volumes? (y/n/q)", "N")
    ):
        UI.info("Pruning dangling Docker volumes...")
        UI.detail("Command: docker volume prune -f")
        run_command(["docker", "volume", "prune", "-f"], check=False)
        UI.success("Volume pruning complete.")

    if not handler.manager.non_interactive:
        UI.info(
            f"\n{UI.CYAN}ℹ{UI.COLOR_OFF} Hint: For a deep cleanup (including unused images), run: "
            f"{UI.WHITE}docker system prune -af{UI.COLOR_OFF}"
        )

    # 7. DNS Cleanup (Explicitly requested via --clean-hosts)
    if clean_hosts:
        if is_dry_run:
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would remove ALL LDM-managed entries from hosts file.{UI.COLOR_OFF}"
            )
        elif prune_all or (
            not handler.manager.non_interactive
            and UI.confirm(
                "Remove ALL LDM-managed entries from your hosts file?", "N"
            )
        ):
            handler.manager._remove_hosts_entries(all_ldm=True)

    UI.info("Prune complete.")

