"""
Orchestrates the main 'ldm import' pipeline.
"""

import os
import shutil
import tarfile
import time
import typing
import zipfile
from datetime import datetime
from pathlib import Path

from ldm_core.pipelines.base import Pipeline, PipelineContext, PipelineStage
from ldm_core.pipelines.validation import ValidationStage as SharedValidationStage
from ldm_core.ui import UI
from ldm_core.utils import calculate_sha256


class ImportPipelineContext(PipelineContext):
    """Strongly typed context for the Import pipeline."""

    def __init__(self, manager, **kwargs):
        super().__init__(**kwargs)
        self.manager = manager
        self.set("total_start", time.time())
        self.set("is_brand_new", False)
        self.set("project_name", kwargs.get("project_name"))
        self.set("source_path", kwargs.get("source_path"))
        self.set("temp_dirs", [])
        self.set("project_path", None)
        self.set("paths", {})
        self.set("backup_dir", None)
        self.set("is_init_from", kwargs.get("is_init_from", False))


class ImportValidationStage(PipelineStage):
    """Verifies target paths, existing states, and CLI flags."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager
        source_path = context.get("source_path")

        # We assume source_path is a local file or directory after download/clone logic
        source = Path(source_path).resolve()

        if not source.exists():
            UI.die(f"Source path not found: {source}")
        if not manager._check_java_version("21"):
            UI.die("Incorrect system Java version. LDM import requires JDK 21.")

        if source.is_file():
            if source.suffix.lower() not in [".zip", ".tgz", ".gz", ".tar", ".ldmp"]:
                UI.die(f"Unsupported source format: {source.suffix}")

            verify_enabled = getattr(manager.args, "verify", True)
            sha_file = source.with_name(f"{source.name}.sha256")

            if verify_enabled:
                if sha_file.exists():
                    UI.info(f"Verifying integrity of {source.name}...")
                    actual_sha = calculate_sha256(source)
                    expected_sha = sha_file.read_text().strip()
                    if actual_sha != expected_sha:
                        UI.die(
                            f"Integrity check failed for archive: {source.name}\n"
                            f"Expected: {expected_sha}\n"
                            f"Actual:   {actual_sha}\n"
                            "The archive file may be corrupted or tampered with."
                        )
                    UI.success("Archive integrity verified.")
                else:
                    UI.warning(
                        "Archive does not have an integrity checksum. Proceeding without verification."
                    )
            else:
                UI.warning("Integrity verification disabled via --no-verify.")

        context.set("source_resolved", source)


class ExtractionStage(PipelineStage):
    """Handles zip/tar expansion and prepares the payload."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        source = context.get("source_resolved")

        if source.is_file():
            temp_extract_dir = (
                Path.cwd()
                / ".ldm_temp"
                / f"import_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            temp_extract_dir.mkdir(parents=True, exist_ok=True)
            temp_dirs = context.get("temp_dirs", [])
            temp_dirs.append(temp_extract_dir)
            context.set("temp_dirs", temp_dirs)

            UI.info("Extracting source archive...")
            from ldm_core.utils import safe_extract

            if source.suffix.lower() == ".zip":
                with zipfile.ZipFile(source, "r") as z:
                    safe_extract(z, temp_extract_dir)
            else:
                mode = (
                    "r:gz"
                    if source.suffix.lower() in [".tgz", ".gz", ".ldmp"]
                    else "r:"
                )
                with tarfile.open(source, mode) as t:
                    safe_extract(t, temp_extract_dir)

            extracted_source = temp_extract_dir
            for r, _d, f in os.walk(temp_extract_dir):
                if (
                    Path(r) / "liferay" / "LCP.json"
                ).exists() or "gradle.properties" in f:
                    extracted_source = Path(r)
                    break

            context.set("extracted_source", extracted_source)
            context.set("backup_dir", temp_extract_dir)
        else:
            context.set("extracted_source", source)


class ProjectSetupStage(PipelineStage):
    """Sets up the project directory and meta configuration."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager
        backup_dir = context.get("backup_dir")

        is_ldmp = backup_dir and (backup_dir / "meta").exists()
        manifest = manager.read_meta(backup_dir) or {} if is_ldmp else {}
        context.set("is_ldmp", is_ldmp)

        db_type = manifest.get("db_type")
        if db_type and db_type not in ["postgresql", "mysql", "mariadb", "hypersonic"]:
            UI.die(f"Unsupported database type '{db_type}' in LDM package manifest.")

        project_name = getattr(manager.args, "project", None) or getattr(
            manager.args, "project_flag", None
        )
        if not project_name:
            project_name = (
                context.get("source_resolved").stem
                if context.get("source_resolved").is_file()
                else context.get("source_resolved").name
            )
            if manager.non_interactive:
                UI.info(f"Using default project name: {project_name}")
            else:
                project_name = UI.ask("Project Name", project_name)

        context.set("project_name", project_name)
        project_path = manager.detect_project_path(project_name, for_init=True)
        context.set("project_path", project_path)

        manager.check_uncommitted_changes(project_path)

        is_brand_new = not project_path.exists()
        context.set("is_brand_new", is_brand_new)

        if not is_brand_new:
            if manager.non_interactive:
                UI.info(
                    f"Project '{project_name}' exists. Overwriting in non-interactive mode."
                )
            else:
                ans = UI.ask(
                    f"Project '{project_name}' exists. Overwrite? [y]es, [n]o (skip existing), [c]lean, [q]uit",
                    "Y",
                ).upper()
                if ans == "C":
                    UI.info(f"Cleaning existing project directory: {project_path}")
                    manager.safe_rmtree(project_path)
                    context.set("is_brand_new", True)
                elif ans == "N":
                    context.set("overwrite", False)
                    UI.info("Proceeding in 'skip existing' mode.")
                elif ans == "Y":
                    context.set("overwrite", True)
                else:
                    UI.die("Initialization aborted.")

        paths = manager.setup_paths(project_path)
        context.set("paths", paths)
        for p in [v for v in paths.values() if isinstance(v, Path) and not v.suffix]:
            p.mkdir(parents=True, exist_ok=True)

        manager.verify_runtime_environment(paths)

        project_meta = manager.read_meta(project_path) or {}
        if "tag" in manifest:
            project_meta["tag"] = manifest["tag"]
        if "db_type" in manifest:
            project_meta["db_type"] = manifest["db_type"]

        from ldm_core.utils import sanitize_id

        safe_container_name = sanitize_id(project_name)
        if safe_container_name != project_name:
            UI.info(
                f"Project name '{project_name}' contains invalid characters for Docker. "
                f"Using '{safe_container_name}' for container names."
            )

        final_host_name = (
            getattr(manager.args, "host_name", None)
            or project_meta.get("host_name")
            or "localhost"
        )
        ssl_arg = getattr(manager.args, "ssl", None)
        if ssl_arg is not None:
            final_ssl = str(ssl_arg).lower()
        elif getattr(manager.args, "host_name", None) is not None:
            final_ssl = str(final_host_name != "localhost").lower()
        else:
            final_ssl = str(project_meta.get("ssl") or "false").lower()

        project_meta.update(
            {
                "project_name": project_name,
                "container_name": safe_container_name,
                "port": str(
                    getattr(manager.args, "port", None)
                    or project_meta.get("port")
                    or 8080
                ),
                "ssl": final_ssl,
                "host_name": final_host_name,
                "last_run": datetime.now().isoformat(),
            }
        )
        manager.write_meta(project_path, project_meta)


class BackupStateStage(PipelineStage):
    """Captures current database/volume state and implements rollback."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        # For simplicity, if it's brand new, rollback is just deleting the directory.
        # If it existed, we could snapshot it, but LDM already relies on snapshot.cmd_restore
        # which overwrites.
        # In a real implementation, we would call cmd_snapshot here if not is_brand_new.
        # For now, we set the rollback point.
        pass

    def rollback(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager
        UI.info("Rolling back ImportPipeline...")

        # Resource Cleanup
        temp_dirs = context.get("temp_dirs", [])
        for d in temp_dirs:
            if isinstance(d, Path) and d.exists():
                UI.detail(f"Cleaning up temporary directory: {d}")
                shutil.rmtree(d, ignore_errors=True)

        is_brand_new = context.get("is_brand_new")
        project_path = context.get("project_path")

        if is_brand_new and project_path and project_path.exists():
            UI.detail(f"Removing newly created project directory: {project_path}")
            manager.safe_rmtree(project_path)

        # DB Container Verification and dropping tables could be done here if needed
        # but since we drop the container/volume on brand new, it's sufficient for now.


class DatabaseRestoreStage(PipelineStage):
    """Restores database from SQL dumps using snapshot manager."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        # If it's an LDMP package, cmd_restore handles DB and Volumes
        if context.get("is_ldmp"):
            UI.info("Restoring database and volume assets from LDM package...")
            try:
                manager.snapshot.cmd_restore(
                    context.get("project_name"), backup_dir=context.get("backup_dir")
                )
            except Exception as e:
                UI.error(f"Failed to restore snapshot: {e}")
                context.stopped = True
                raise


class VolumeSyncStage(PipelineStage):
    """Synchronizes files and artifacts from the source workspace to the project paths."""

    def execute(self, context: PipelineContext) -> None:
        import os
        import shutil
        import typing
        import zipfile
        from pathlib import Path

        from ldm_core.pipelines.import_pipeline import ImportPipelineContext
        from ldm_core.ui import UI
        from ldm_core.utils import safe_copy

        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        if context.get("is_ldmp"):
            return  # Handled by restore

        workspace_root = context.get("extracted_source")
        paths = context.get("paths")
        overwrite = context.get("overwrite", True)
        is_cloud = (
            manager.workspace._is_lcp_workspace(workspace_root)
            if hasattr(manager.workspace, "_is_lcp_workspace")
            else False
        )

        def import_zips(search_base, label, target_dir, overwrite=False):
            count = 0
            if not search_base.exists():
                return count

            for zip_path in search_base.glob("**/*.zip"):
                if "-sources" in zip_path.name or "javadoc" in zip_path.name:
                    continue

                UI.debug(f"Found {label} ZIP: {zip_path.name}")
                with zipfile.ZipFile(zip_path, "r") as z:
                    if z.testzip() is not None:
                        UI.error(f"{zip_path.name} corrupt.")
                        continue
                target_file = target_dir / zip_path.name
                if target_file.exists() and not overwrite:
                    UI.debug(f"Skipping existing {label}: {zip_path.name}")
                    continue

                safe_copy(zip_path, target_file)
                count += 1
            return count

        # Sync code elements directly
        # Standard structural folders
        UI.info("Syncing workspace structure and files...")
        structural_mappings = {
            "configs": paths.get("configs"),
            "deploy": paths.get("deploy"),
            "files": paths.get("files"),
            "scripts": paths.get("scripts"),
        }

        for source_folder, target_dir in structural_mappings.items():
            if not target_dir:
                continue
            src = workspace_root / source_folder
            if src.exists():
                for item in src.iterdir():
                    dest = target_dir / item.name
                    if item.is_dir():
                        if dest.exists() and overwrite:
                            shutil.rmtree(dest)
                        if not dest.exists():
                            shutil.copytree(item, dest, copy_function=safe_copy)
                    elif not dest.exists() or overwrite:
                        safe_copy(item, dest)

        # Handle CEs and Fragments
        import_zips(
            workspace_root / "client-extensions",
            "Extension",
            paths.get("ce_dir"),
            overwrite,
        )
        import_zips(
            workspace_root / "fragments", "Fragment", paths.get("ce_dir"), overwrite
        )

        # Modules and Themes
        for search_folder in ["modules", "themes"]:
            base = workspace_root / search_folder
            if base.exists():
                for root, dirs, _files in os.walk(base):
                    if "build" in dirs:
                        libs = Path(root) / "build" / "libs"
                        if libs.exists():
                            for f in libs.glob("*.[jw]ar"):
                                if not any(
                                    x in f.name.lower()
                                    for x in ["-sources", "-javadoc", "-tests"]
                                ):
                                    safe_copy(f, paths.get("modules") / f.name)

        if is_cloud:
            infra_dirs = [
                "liferay",
                "backup",
                "ci",
                "database",
                "search",
                "webserver",
                ".git",
            ]
            for item in [
                i
                for i in workspace_root.parent.iterdir()
                if i.is_dir()
                and i.name not in infra_dirs
                and not i.name.startswith(".")
            ]:
                if (item / "LCP.json").exists() and (item / "Dockerfile").exists():
                    dest = paths.get("root") / "services" / item.name
                    if dest.exists():
                        manager.safe_rmtree(dest)
                    shutil.copytree(item, dest, copy_function=safe_copy)

        backup_dir = context.get("backup_dir")
        if backup_dir and hasattr(manager, "snapshot"):
            if hasattr(manager.snapshot, "_restore_from_cloud_layout"):
                project_meta = manager.read_meta(context.get("project_path"))
                manager.snapshot._restore_from_cloud_layout(
                    backup_dir, paths, project_meta
                )

        if hasattr(manager.workspace, "_hydrate_from_workspace"):
            manager.workspace._hydrate_from_workspace(
                workspace_root, paths, overwrite=overwrite
            )


class BuildWorkspaceStage(PipelineStage):
    """Builds the Gradle workspace if requested."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        if not context.get("is_ldmp") and getattr(manager.args, "build", False):
            workspace_root = context.get("extracted_source")
            UI.heading(f"Building Workspace: {workspace_root.name}")
            import platform

            gradlew = workspace_root / (
                "gradlew" if platform.system() != "Windows" else "gradlew.bat"
            )
            if gradlew.exists():
                if platform.system() != "Windows":
                    try:
                        os.chmod(gradlew, 0o755)  # nosec B103
                    except Exception:
                        pass
                try:
                    UI.info(f"Executing clean build in {gradlew.parent}...")
                    manager.run_command(
                        [str(gradlew), "clean", "build", "-x", "test"],
                        capture_output=False,
                        cwd=str(gradlew.parent),
                    )
                except Exception as e:
                    UI.error(f"Build failed: {e}")
                    if manager.non_interactive:
                        UI.die("Build failed in non-interactive mode. Aborting.")


class FinalizationStage(PipelineStage):
    """Handles post-import cleanup and starts the stack if needed."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        temp_dirs = context.get("temp_dirs", [])
        for d in temp_dirs:
            if isinstance(d, Path) and d.exists():
                shutil.rmtree(d, ignore_errors=True)

        project_path = context.get("project_path")
        UI.success(f"Project created/imported at: {project_path}")

        if not getattr(manager.args, "no_run", False):
            manager.cmd_run(project_id=context.get("project_name"), is_restart=True)


class ImportPipeline(Pipeline):
    """The complete pipeline for 'ldm import'."""

    def __init__(self):
        super().__init__(
            name="import",
            stages=[
                SharedValidationStage(),
                ImportValidationStage(),
                ExtractionStage(),
                ProjectSetupStage(),
                BackupStateStage(),
                DatabaseRestoreStage(),
                VolumeSyncStage(),
                BuildWorkspaceStage(),
                FinalizationStage(),
            ],
        )
