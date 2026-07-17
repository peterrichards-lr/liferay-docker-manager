import os
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from ldm_core.ui import UI
from ldm_core.utils import (
    atomic_copy,
    safe_copy,
    safe_move,
)


def _hydrate_from_workspace(self, workspace_root, paths, overwrite=True):  # noqa: C901, PLR0912
    """Initial scan and sync of artifacts from workspace to project."""
    UI.info("Scanning workspace for built artifacts...")

    # 1. Sync Client Extensions (ZIPs)
    ce_dir = workspace_root / "client-extensions"
    if ce_dir.exists():
        # Look in root and standard dist folders
        for dist_zip in list(ce_dir.glob("*.zip")) + list(ce_dir.glob("*/dist/*.zip")):
            self._sync_cx_artifact(dist_zip, paths, overwrite=overwrite)

    # 2. Sync Modules & Themes (JARs from build/libs)
    for folder in ["modules", "themes"]:
        base_dir = workspace_root / folder
        if base_dir.exists():
            for jar in base_dir.glob("**/build/libs/*.[jw]ar"):
                # Check if it's a valid bundle (not sources/javadoc)
                if not any(
                    x in jar.name.lower() for x in ["-sources", "-javadoc", "-tests"]
                ):
                    dest = paths["modules"] / jar.name
                    if not overwrite and dest.exists():
                        UI.detail(f"  - Skipping existing module: {jar.name}")
                        continue
                    atomic_copy(jar, dest)
                    UI.detail(f"  + Synced {folder.capitalize()[:-1]}: {jar.name}")

    # 3. Sync Fragments (ZIPs)
    frag_dir = workspace_root / "fragments"
    if frag_dir.exists():
        # Look in root and any nested zips
        for zip_file in list(frag_dir.glob("*.zip")) + list(
            frag_dir.glob("*/dist/*.zip")
        ):
            # Check if it's a fragment or a CX being miscategorized
            try:
                with zipfile.ZipFile(zip_file, "r") as zip_ref:
                    if "liferay-deploy-fragments.json" in zip_ref.namelist():
                        dest = paths["deploy"] / zip_file.name
                        if not overwrite and dest.exists():
                            UI.detail(
                                f"  - Skipping existing fragment: {zip_file.name}"
                            )
                            continue
                        atomic_copy(zip_file, dest)
                        UI.detail(f"  + Synced Fragment: {zip_file.name}")
                    else:
                        # If it's a ZIP in fragments but not a fragment, try syncing as CX
                        self._sync_cx_artifact(zip_file, paths, overwrite=overwrite)
            except Exception:
                pass

    # 4. Sync Fragment Overrides
    for override_file in [
        workspace_root / ".ldm" / "fragment-overrides.json",
        workspace_root / "configs" / "fragment-overrides.json",
    ]:
        if override_file.exists():
            dest = paths["root"] / ".ldm" / "fragment-overrides.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not overwrite and dest.exists():
                UI.detail("  - Skipping existing fragment overrides")
                continue
            try:
                if override_file.resolve() != dest.resolve():
                    safe_copy(override_file, dest)
                    UI.detail("  + Synced Fragment Overrides")
            except Exception:
                pass

    return True


def _sync_cx_artifact(self, zip_path, paths, overwrite=True):
    """Internal helper for the mandatory 3-step CX sync sequence."""
    ce_source_truth = paths["root"] / "client-extensions"
    ce_source_truth.mkdir(parents=True, exist_ok=True)

    # Step 1: Copy ZIP to root client-extensions/
    root_zip_path = ce_source_truth / zip_path.name

    # In 'no-overwrite' mode, we check if the final destination exists
    dest_zip = paths["cx"] / zip_path.name
    if not overwrite and dest_zip.exists():
        UI.detail(f"  - Skipping existing CX: {zip_path.name}")
        return

    if zip_path.resolve() != root_zip_path.resolve():
        safe_copy(zip_path, root_zip_path)
    # Step 2: Expand ZIP in root for Docker builds
    try:
        with zipfile.ZipFile(root_zip_path, "r") as zip_ref:
            target_folder = ce_source_truth / zip_path.stem
            if target_folder.exists():
                if not overwrite:
                    # If skipping, we don't clear/re-expand the build folder either
                    pass
                else:
                    shutil.rmtree(target_folder)
                    target_folder.mkdir(parents=True)
                    from ldm_core.utils import safe_extract

                    safe_extract(zip_ref, target_folder)
            else:
                target_folder.mkdir(parents=True)
                from ldm_core.utils import safe_extract

                safe_extract(zip_ref, target_folder)

            if overwrite or not dest_zip.exists():
                UI.detail(f"  + Synced & Expanded CX: {zip_path.name}")
    except Exception as e:
        UI.error(f"  ! Failed to expand CX {zip_path.name}: {e}")

    # Step 3: Move original ZIP to osgi/client-extensions/ for Liferay
    if dest_zip.exists():
        if not overwrite:
            if root_zip_path.exists():
                os.remove(root_zip_path)
            return
        os.remove(dest_zip)
    safe_move(str(root_zip_path), str(dest_zip))


def _prompt_cloud_hydration(self, source_path, project_name=None):
    """Helper to prompt for and orchestrate Liferay Cloud data hydration."""
    from ldm_core.utils import is_lcp_workspace

    source = Path(source_path).resolve()
    is_cloud = is_lcp_workspace(source)

    hydrate_env = getattr(self.manager.args, "hydrate_from", None)

    # Automation Path: If --hydrate-from is provided, we skip prompts
    if is_cloud and hydrate_env:
        self._execute_cloud_hydration(hydrate_env, source_path, project_name)
        return

    # Interactive Path
    if is_cloud and not self.manager.non_interactive:
        UI.info("\n> Detected Liferay Cloud Workspace structure.")
        if UI.confirm(
            "Would you also like to pull the remote database and document library to complete the local replica?",
            "Y",
        ):
            default_env = self.manager.defaults.get("target_env", "prd")
            env_id = UI.ask(
                "Which environment would you like to mirror (e.g., prd, uat)",
                default_env,
            )
            if env_id:
                self._execute_cloud_hydration(env_id.strip(), source_path, project_name)


def _execute_cloud_hydration(self, env_id, source_path, project_name):
    """Internal helper to execute the cloud fetch/restore/sync sequence."""
    # Persist the chosen environment for future cloud operations
    project_path = self.manager.detect_project_path(project_name, for_init=True)
    if not project_path:
        return

    p_meta = self.manager.read_meta(project_path)
    p_meta["cloud_env_id"] = env_id
    self.manager.write_meta(project_path, p_meta)

    self.manager.setup_paths(project_path)

    UI.info(f"Fetching backups from '{env_id}'...")
    try:
        # 1. Sync Env Vars (Do this first so they are in place for the restoration boot)
        # LDM-423: Skip env sync if --no-env-sync is provided
        if not getattr(self.manager.args, "no_env_sync", False):
            self.manager.cloud.cmd_cloud_fetch(
                project_id=project_name,
                env_id=env_id,
                sync_env=True,
                download=False,
                restore=False,
                source_path=str(source_path),
            )
        else:
            UI.info("  - Skipping environment variable sync (--no-env-sync).")

        # 2. Fetch Data & Restore
        # We set no_run=True to prevent cmd_restore from starting the stack early.
        # The outer cmd_import/cmd_init_from will handle the final startup.
        self.manager.cloud.cmd_cloud_fetch(
            project_id=project_name,
            env_id=env_id,
            sync_env=False,
            download=True,
            restore=True,
            no_run=True,
        )

    except SystemExit:
        UI.warning(
            "Cloud hydration could not be completed. Falling back to local runtime only."
        )
