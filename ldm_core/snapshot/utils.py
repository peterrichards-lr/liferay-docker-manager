import time
from pathlib import Path
from typing import cast

from ldm_core.ui import UI


class UtilsSnapshotService:
    def __init__(self, facade):
        self.facade = facade
        self.manager = facade.manager
        self.args = facade.manager.args

    def _manage_snapshots(self, paths, delete_arg, keep_last, older_than):  # noqa: C901, PLR0912
        backups_dir = paths["backups"]
        if not backups_dir.exists():
            UI.warning("No snapshots found to manage.")
            return

        backups = sorted(
            [d for d in backups_dir.iterdir() if d.is_dir()],
            key=lambda x: x.name,
            reverse=True,
        )

        if not backups:
            UI.warning("No snapshots found to manage.")
            return

        to_delete = []

        if delete_arg:
            target = None
            if delete_arg.isdigit():
                idx = int(delete_arg) - 1
                if 0 <= idx < len(backups):
                    target = backups[idx]
            else:
                for b in backups:
                    meta = self.manager.read_meta(b / "meta")
                    if meta.get("name") == delete_arg:
                        target = b
                        break

            if target:
                to_delete.append(target)
            else:
                UI.die(f"Snapshot not found matching index or name: '{delete_arg}'")

        if keep_last is not None:
            if keep_last < len(backups):
                to_delete.extend(backups[keep_last:])

        if older_than is not None:
            cutoff = time.time() - (older_than * 86400)
            for b in backups:
                if b.stat().st_mtime < cutoff and b not in to_delete:
                    to_delete.append(b)

        if not to_delete:
            UI.info("No snapshots matched management criteria.")
            return

        for snap in to_delete:
            meta = self.manager.read_meta(snap / "meta")
            name = meta.get("name", "Untitled")
            search_snap = meta.get("search_snapshot")

            UI.info(f"Deleting snapshot: {name} ({snap.name})...")

            # Delete global search snapshot if it exists
            if search_snap:
                search_name = "liferay-search-global"
                if self.manager.run_command(
                    ["docker", "ps", "-q", "-f", f"name={search_name}"]
                ):
                    self.manager.run_command(
                        [
                            "docker",
                            "exec",
                            search_name,
                            "curl",
                            "-s",
                            "-X",
                            "DELETE",
                            f"localhost:9200/_snapshot/liferay_backup/{search_snap}",
                        ]
                    )

            self.manager.safe_rmtree(snap)

        UI.success(f"Successfully deleted {len(to_delete)} snapshot(s).")

    def _resolve_snapshot_choice(self, paths, auto_index, backup_dir):  # noqa: C901, PLR0912
        choice = None
        if backup_dir:
            choice = Path(backup_dir)
        elif getattr(self.manager.args, "backup_dir", None):
            choice = Path(self.manager.args.backup_dir)
        elif getattr(self.manager.args, "latest", False):
            backups = self.facade.cmd_snapshots(paths)
            if not backups:
                UI.die("No snapshots available for --latest.")
            choice = backups[0]

        if not choice:
            backups = self.facade.cmd_snapshots(paths)
            if not backups:
                if auto_index is not None or backup_dir is not None:
                    UI.warning(
                        "No snapshots available. Proceeding with vanilla startup."
                    )
                    return None
                UI.die("No snapshots available.")

            if auto_index is not None:
                choice = backups[auto_index - 1]
            elif getattr(self.manager.args, "name", None):
                target_name = self.manager.args.name
                for b in backups:
                    meta = self.manager.read_meta(b / "meta")
                    if meta.get("name") == target_name:
                        choice = b
                        break
                if not choice:
                    UI.die(f"No snapshot found with name: '{target_name}'")
            elif getattr(self.manager.args, "index", None):
                choice = backups[self.manager.args.index - 1]
            elif self.manager.non_interactive:
                UI.die(
                    "No snapshot index specified. In non-interactive mode, use: ldm restore <pid> --index <num>"
                )
            else:
                choice = backups[int(UI.ask("Select snapshot index", "1")) - 1]

        if not choice or not choice.exists():
            UI.die(f"Snapshot directory not found: {choice}")

        return cast(Path, choice)

    def _get_dir_size(self, path):
        total: float = 0
        try:
            for f in path.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
        except Exception:
            return "unknown"

        for unit in ["B", "KB", "MB", "GB"]:
            if total < 1024:
                return f"{total:.1f} {unit}"
            total /= 1024
        return f"{total:.1f} TB"

    def _list_backups(self, paths):
        """Helper to list available snapshots for a project, returning a list of dicts with paths."""
        backups_dir = paths["backups"]
        if not backups_dir.exists():
            return []

        # Find all directories in the snapshots folder, sorted by name
        subdirs = sorted(
            [d for d in backups_dir.iterdir() if d.is_dir()],
            key=lambda x: x.name,
        )

        return [{"path": d, "name": d.name} for d in subdirs]
