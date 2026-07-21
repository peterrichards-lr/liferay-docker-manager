import json
import os
import tarfile
import time
from datetime import datetime
from pathlib import Path
from typing import cast

from ldm_core.ui import UI
from ldm_core.utils import get_actual_home, safe_extract

class VolumesSnapshotService:
    def __init__(self, facade):
        self.facade = facade
        self.manager = facade.manager
        self.args = facade.manager.args

    def _sync_volume(self, host_path, volume_name, direction="to_volume"):
        """Synchronizes data between a host directory and a Docker Named Volume."""
        host_path_abs = Path(host_path).resolve()
        if not host_path_abs.exists():
            host_path_abs.mkdir(parents=True, exist_ok=True)

        # Ensure volume exists
        self.manager.run_command(
            ["docker", "volume", "create", volume_name], check=False
        )

        src = "/host" if direction == "to_volume" else "/vol"
        dst = "/vol" if direction == "to_volume" else "/host"

        # Note: We use 'tar' piped to 'tar' to safely copy the entire directory structure
        # (including hidden files and deep nesting) without relying on shell expansion quirks in Alpine.
        # LDM-420: We MUST chown the files to 1000:1000 (liferay) when hydrating the volume,
        # otherwise the Alpine container creates them as root, causing 404 access errors.
        chown_cmd = f" && chown -R 1000:1000 {dst}" if direction == "to_volume" else ""
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{host_path_abs.as_posix()}:/host",
            "-v",
            f"{volume_name}:/vol",
            "alpine",
            "sh",
            "-c",
            f"tar -cC {src} . | tar -xC {dst}{chown_cmd}",
        ]

        try:
            if self.manager.verbose:
                UI.info(
                    f"  + Syncing volume {UI.CYAN}{volume_name}{UI.COLOR_OFF} ({direction})..."
                )
            res = self.manager.run_command(cmd, check=False)
            if res is None:
                UI.warning(
                    f"Failed to sync volume {volume_name}: Command execution returned error status."
                )
                return False
            return True
        except Exception as e:
            UI.warning(f"Failed to sync volume {volume_name}: {e}")
            return False

    def _dehydrate_named_volumes(self, paths):
        """Copies data from Docker Named Volumes back to the host for snapshotting."""
        if not self.manager.composer.is_using_named_volumes():
            return

        meta = self.manager.read_meta(paths["root"])
        c_name = meta.get("container_name") or paths["root"].name

        for target in ["data", "state"]:
            volume_name = f"{c_name}-{target}"
            host_path = paths[target]
            UI.info(
                f"  + Dehydrating volume {UI.CYAN}{volume_name}{UI.COLOR_OFF} to host..."
            )
            self._sync_volume(host_path, volume_name, direction="from_volume")

    def _hydrate_named_volumes(self, paths):
        """Copies data from the host into Docker Named Volumes after extraction."""
        if not self.manager.composer.is_using_named_volumes():
            return

        meta = self.manager.read_meta(paths["root"])
        c_name = meta.get("container_name") or paths["root"].name

        for target in ["data", "state"]:
            volume_name = f"{c_name}-{target}"
            host_path = paths[target]
            if host_path.exists():
                UI.info(
                    f"  + Hydrating volume {UI.CYAN}{volume_name}{UI.COLOR_OFF} from host..."
                )
                self._sync_volume(host_path, volume_name, direction="to_volume")

    def _restore_cloud_volume(self, paths, choice_path, project_meta):  # noqa: C901, PLR0912, PLR0915
        UI.detail("  + Restoring cloud data volume...")
        target_data = paths["data"]
        from ldm_core.utils import safe_mkdir

        safe_mkdir(target_data, parents=True, exist_ok=True)

        volume_tgz = choice_path / "volume.tgz"

        if volume_tgz.exists():
            hash_file = target_data / ".ldm_volume.sha256"
            skip_extraction = False
            has_files = False
            try:
                if target_data.exists():
                    has_files = any(
                        item
                        for item in target_data.iterdir()
                        if item.name not in (".DS_Store", ".ldm_volume.sha256")
                    )
            except Exception:
                pass

            if has_files and hash_file.exists():
                try:
                    from ldm_core.utils import calculate_sha256

                    current_hash = calculate_sha256(volume_tgz)
                    cached_hash = hash_file.read_text().strip()
                    if current_hash == cached_hash:
                        skip_extraction = True
                        UI.info(
                            "  + Volume archive unchanged (hash matched). Skipping extraction."
                        )
                except Exception:
                    pass

            if not skip_extraction:
                self.manager.run_command(
                    ["tar", "-xzf", str(volume_tgz), "-C", str(target_data)]
                )
                try:
                    from ldm_core.utils import calculate_sha256

                    current_hash = calculate_sha256(volume_tgz)
                    hash_file.write_text(current_hash)
                except Exception:
                    pass
        else:
            import shutil

            from ldm_core.utils import safe_rmtree

            if target_data.exists():
                safe_rmtree(target_data)

            UI.detail("  + Unpacking volume to host...")
            shutil.copytree(str(choice_path / "volume"), str(target_data))

            if self.manager.composer.is_using_named_volumes():
                time.sleep(2)

                UI.detail("  + Hydrating internal Docker volumes...")

                self._hydrate_named_volumes(paths)

        UI.success("Cloud volume restoration completed.")

        try:
            doclib_root = target_data / "document_library"
            if doclib_root.exists():
                is_simple = False
                for company_dir in doclib_root.iterdir():
                    if company_dir.is_dir() and company_dir.name.isdigit():
                        subdirs = [
                            d
                            for d in company_dir.iterdir()
                            if d.is_dir() and d.name.isdigit()
                        ]
                        if subdirs:
                            for s in subdirs:
                                grandkids = [
                                    d
                                    for d in s.iterdir()
                                    if d.is_dir() and d.name.isdigit()
                                ]
                                if not grandkids:
                                    is_simple = True
                                    break
                    if is_simple:
                        break

                if is_simple:
                    UI.info("  + Detected simplified FileSystemStore layout.")
                    project_meta["dl_store_impl"] = (
                        "com.liferay.portal.store.file.system.FileSystemStore"
                    )
                else:
                    project_meta["dl_store_impl"] = (
                        "com.liferay.portal.store.file.system.AdvancedFileSystemStore"
                    )
                self.manager.write_meta(paths["root"], project_meta)
        except Exception as e:
            UI.debug(f"Store detection failed: {e}")

