import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from ldm_core.ui import UI


def cmd_fork(self, source, target, snapshot=None):
    """Forks an existing project into a new one, cloning database and DL assets."""
    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        UI.detail(
            f"{UI.BYELLOW}[DRY RUN] Would fork project: {source} -> {target}{UI.COLOR_OFF}"
        )
        return target

    from ldm_core.utils import sanitize_id

    # 1. Resolve paths
    source_root = self.manager.detect_project_path(source, fatal=False)
    if not source_root:
        UI.die(f"Source project '{source}' does not exist.")
        return None

    # Target directory should be sibling of source
    target_root = source_root.parent / target
    if target_root.exists():
        # Check if there are any project files in target
        has_meta = any(
            (target_root / f).exists()
            for f in [
                "meta",
                ".liferay-docker.meta",
                ".ldm.meta",
                "docker-compose.yml",
            ]
        )
        if has_meta:
            UI.die(f"Target project '{target}' already exists at: {target_root}")
            return None

    # 2. Setup paths
    source_paths = self.manager.setup_paths(source_root)
    target_paths = self.manager.setup_paths(target_root)

    # 3. Determine or create snapshot to restore
    snapshot_dir = None
    if snapshot:
        snapshot_dir = source_paths["backups"] / snapshot
        if not snapshot_dir.exists():
            UI.die(
                f"Snapshot '{snapshot}' not found in source project: {source_paths['backups']}"
            )
            return None
    else:
        UI.detail(f"Creating backup snapshot of '{source}' for forking...")
        old_name = getattr(self.manager.args, "name", None)
        self.manager.args.name = f"Fork backup of {source}"
        try:
            self.manager.snapshot.cmd_snapshot(project_id=source)
        finally:
            self.manager.args.name = old_name

        snaps = self.manager.snapshot._get_snapshots(source_paths)
        if not snaps:
            UI.die(f"Failed to create snapshot of source project '{source}'.")
            return None
        snapshot_dir = snaps[-1]["path"]

    # 4. Read source metadata and construct target metadata
    source_meta = self.manager.read_meta(source_root) or {}
    target_meta = dict(source_meta)

    # Mutate target metadata fields
    sanitized_target = sanitize_id(target)
    target_meta.update(
        {
            "project_name": target,
            "container_name": sanitized_target,
            "db_container_name": f"{sanitized_target}-db",
            "tunnel_container_name": f"{sanitized_target}-tunnel",
            "host_name": f"{sanitized_target}.local",
            "seeded": "true",
        }
    )

    # Port Resolution: Scan starting from source port + 1 to find a free host port
    source_port = 8080
    try:
        source_port = int(source_meta.get("port") or 8080)
    except Exception:
        pass

    new_port = source_port + 1
    while not self.manager.check_port("127.0.0.1", new_port):
        new_port += 1

    target_meta["port"] = str(new_port)

    # 5. Create target directory and write metadata
    target_root.mkdir(parents=True, exist_ok=True)
    self.manager.write_meta(target_root, target_meta)

    # 6. Register target project in global registry
    self.manager.register_project(target, target_root, target_meta["host_name"])

    # 7. Restore the snapshot into the new target project
    UI.detail(f"Restoring cloned snapshot data to fork project '{target}'...")
    self.manager.snapshot.cmd_restore(project_id=target, backup_dir=str(snapshot_dir))

    # 8. Rebuild composition & configurations cleanly for target
    UI.detail(f"Synchronizing compose stack for fork project '{target}'...")
    self.manager.runtime.cmd_run(
        project_id=target_meta.get("container_name") or target_paths["root"].name,
        no_up=True,
        show_summary=True,
        paths=target_paths,
        project_meta=target_meta,
    )

    UI.success(
        f"Successfully forked project '{source}' to '{target}'!\n"
        f"  - Target directory: {target_root}\n"
        f"  - Port resolved:    {new_port}\n"
        f"  - Host resolved:    {target_meta['host_name']}\n\n"
        f"You can now run: {UI.CYAN}ldm run {target}{UI.COLOR_OFF}"
    )
    return target
