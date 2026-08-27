import errno
import json
import os
import tarfile
from pathlib import Path

from ldm_core.ui import UI
from ldm_core.utils import get_actual_home, safe_extract


class ArchiveSnapshotService:
    def __init__(self, facade):
        self.facade = facade
        self.manager = facade.manager
        self.args = facade.manager.args

    def _create_archive(self, paths, snap_dir, search_snapshot_name):  # noqa: C901
        self.manager.verify_runtime_environment(paths)

        try:
            from ldm_core.utils import reclaim_volume_permissions

            UI.detail("Reclaiming project permissions before snapshot...")
            for d in [
                "deploy",
                "files",
                "logs",
                "configs",
                "modules",
                "client-extensions",
            ]:
                if paths.get(d) and paths[d].exists():
                    reclaim_volume_permissions(
                        paths[d], uid="1000", gid="1000", chmod_val="777"
                    )
            for d in ["data", "state"]:
                if paths.get(d) and paths[d].exists():
                    reclaim_volume_permissions(
                        paths[d],
                        uid=str(os.getuid()),
                        gid=str(os.getgid()),
                        chmod_val="755",
                    )
        except Exception as e:
            UI.debug(f"Failed to reclaim permissions: {e}")

        from ldm_core.utils import safe_mkdir

        safe_mkdir(snap_dir, parents=True, exist_ok=True)
        files_tar = snap_dir / "files.tar.gz"

        # LDM-#1429: a snapshot that skipped its payload is not a snapshot.
        # Previously every OSError here was caught, labelled "permission error"
        # and skipped past. On a full Docker disk that produced five warnings
        # naming the wrong cause, an archive containing essentially nothing, and
        # a "Database dump completed." success line -- the user would hold a
        # .tar.gz they believe is a backup and which cannot restore anything.
        skipped: list[tuple[str, str]] = []
        out_of_space = False

        with tarfile.open(files_tar, "w:gz") as tar:
            for f in [
                "files",
                "scripts",
                "osgi",
                "data",
                "deploy",
                "routes",
                "client-extensions",
                "configs",
                ".ldm",
            ]:
                f_path = paths["root"] / f
                if f_path.exists():
                    if not self._add_to_archive(tar, f_path, f, skipped):
                        out_of_space = True
                        break

            osgi_state = paths.get("state")
            if osgi_state and osgi_state.exists() and osgi_state.is_dir():
                try:
                    tar_names = [m.name for m in tar.getmembers()]
                    if "osgi/state" not in tar_names:
                        UI.detail("Adding missing osgi/state to archive...")
                        tar.add(osgi_state, arcname="osgi/state")
                except Exception:
                    pass

            fragment_overrides = paths["root"] / ".ldm" / "fragment-overrides.json"
            if fragment_overrides.exists():
                try:
                    UI.detail("Adding .ldm/fragment-overrides.json to archive...")
                    tar.add(fragment_overrides, arcname=".ldm/fragment-overrides.json")
                except Exception as e:
                    UI.warning(
                        f"Skipping .ldm/fragment-overrides.json due to error: {e}"
                    )

            if search_snapshot_name:
                es_infra_backup = (
                    get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                )
                if es_infra_backup.exists():
                    from ldm_core.utils import reclaim_volume_permissions

                    reclaim_volume_permissions(es_infra_backup, chmod_val="777")
                    tar.add(es_infra_backup, arcname="search_backup")

        # LDM-#1429: refuse to hand back an archive that is missing content.
        # Done after the `with` closes, so the tarball is finalised before it is
        # removed -- and removed it must be, or a truncated .tar.gz is left on
        # disk looking exactly like a usable snapshot.
        self._fail_incomplete_archive(files_tar, skipped, out_of_space)

        return files_tar

    @staticmethod
    def _add_to_archive(tar, f_path, name, skipped):
        """Adds one entry, recording why if it could not (LDM-#1429).

        Returns False when the disk is full, which is not a "skip": every
        remaining write fails the same way, slowly.

        The causes are separated deliberately. `PermissionError` is a subclass
        of `OSError`, so the previous combined handler asserted "permission
        error" for every OS-level failure -- ENOSPC, a stale mount, an I/O error
        -- and sent the user to chmod when the fix was disk space.
        """
        try:
            UI.detail(f"Adding {name} to archive...")
            tar.add(f_path, arcname=name)
            return True
        except PermissionError as e:
            # The original case, and the only one that message was right about.
            UI.warning(f"Skipping {name} due to permission error: {e}")
            skipped.append((name, f"permission error: {e}"))
            return True
        except OSError as e:
            if e.errno == errno.ENOSPC:
                UI.warning(f"Cannot add {name}: no space left on device.")
                skipped.append((name, "no space left on device"))
                return False
            UI.warning(f"Skipping {name}: {e}")
            skipped.append((name, str(e)))
            return True

    @staticmethod
    def _fail_incomplete_archive(files_tar, skipped, out_of_space):
        """Refuses to hand back an archive missing content (LDM-#1429).

        Called after the tarfile context closes, so the file is finalised before
        it is removed -- and removed it must be, or a truncated .tar.gz is left
        on disk looking exactly like a usable snapshot.

        Exit 3 per the contract in .agents/skills/ldm-architecture: a full disk
        during a backup is an Infrastructure/Data error, not a validation one.

        No-ops when nothing was skipped, so the caller stays a single call.
        """
        if not skipped:
            return

        missing = "\n".join(f"  - {name}: {why}" for name, why in skipped)
        try:
            files_tar.unlink()
        except OSError:
            pass

        if out_of_space:
            UI.die(
                "Snapshot failed: the disk filled up while writing the archive.",
                details=f"Could not add:\n{missing}",
                tip=(
                    "Free up space and re-run. Docker's disk is usually the one "
                    "that matters, not the host's -- check it with "
                    "'docker system df', and reclaim with 'ldm prune'."
                ),
                exit_code=3,
            )
        UI.die(
            "Snapshot failed: content could not be added to the archive.",
            details=f"Could not add:\n{missing}",
            tip=(
                "The archive was removed rather than left incomplete: a snapshot "
                "missing these cannot restore the project. Resolve the errors "
                "above and re-run."
            ),
            exit_code=3,
        )

    def _extract_snapshot_archive(self, files_tar, paths):  # noqa: C901, PLR0912, PLR0915
        """Extracts a snapshot tarball into the project root with security checks."""
        # Ensure paths is a dictionary for subscripting
        if not isinstance(paths, dict):
            paths = self.manager.setup_paths(paths)

        # Guardrail: Active Git Repository Source Tree Overwrite Protection (Issue #1126)
        target_root = paths["root"].resolve()
        is_git_repo = (target_root / ".git").exists()
        has_meta = bool(self.manager.read_meta(target_root))
        force = bool(getattr(self.manager.args, "force", False))

        if is_git_repo and not has_meta and not force:
            if getattr(self.manager, "non_interactive", False):
                UI.die(
                    f"Refusing to extract snapshot over active git repository '{target_root}' without LDM project metadata.\n"
                    f"Use --force to override this safety guard or specify a dedicated project path."
                )
            else:
                UI.warning(
                    f"Target directory '{target_root}' is a git repository root without LDM project metadata."
                )
                if not UI.ask(
                    "Extracting this snapshot will overwrite source files in your working repository. Proceed?",
                    default="n",
                ):
                    UI.die("Snapshot restore cancelled by user.")

        import shutil

        try:
            free_space = shutil.disk_usage(target_root).free
            archive_size = Path(files_tar).stat().st_size
            if free_space < archive_size * 1.5:
                free_mb = round(free_space / (1024 * 1024), 2)
                required_mb = round((archive_size * 1.5) / (1024 * 1024), 2)
                UI.die(
                    f"Insufficient disk space on target partition to safely extract backup. "
                    f"Required: {required_mb} MB (1.5x archive size), Available: {free_mb} MB."
                )
        except OSError as e:
            UI.warning(f"Could not verify available disk space: {e}")

        no_osgi = getattr(self.manager.args, "no_osgi_seed", False)

        with tarfile.open(files_tar, "r:gz") as tar:
            from ldm_core.utils import is_safe_path

            # 1. Extract standard project files
            target_root = paths["root"].resolve()
            members = []
            for m in tar.getmembers():
                if m.name.startswith("search_backup"):
                    continue

                # OSGi State Handling: Only extract if not opted-out
                if m.name.startswith("osgi/state") and no_osgi:
                    continue

                # Security: Validate path to prevent Zip Slip / Path Traversal
                is_link = m.issym() or m.islnk()
                if not is_safe_path(target_root, m.name, is_link, m.linkname):
                    UI.error(f"Security: Skipping unsafe member: {m.name}")
                    continue

                member_path = (target_root / m.name).resolve()
                # Pre-emptively remove file to avoid PermissionError (Errno 13) during overwrite
                if member_path.exists() and not member_path.is_dir():
                    try:
                        member_path.unlink()
                    except Exception:
                        pass

                members.append(m)

            tar.errorlevel = (
                0  # Robustness: suppress non-fatal OSErrors (like copystat failures)
            )
            safe_extract(tar, target_root, members=members)

            # 2. Extract search_backup if present
            has_search = any(
                m.name.startswith("search_backup") for m in tar.getmembers()
            )
            if has_search:
                es_infra_backup = (
                    get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                )
                from ldm_core.utils import safe_mkdir

                safe_mkdir(es_infra_backup, parents=True, exist_ok=True)
                es_infra_root = es_infra_backup.resolve()

                # Reclaim permissions before extracting (Fixes [Errno 13] in CI/Linux)
                from ldm_core.utils import reclaim_volume_permissions

                reclaim_volume_permissions(es_infra_backup, chmod_val="777")

                for m in tar.getmembers():
                    if m.name.startswith("search_backup/"):
                        # Security: Validate path
                        rel_name = m.name.replace("search_backup/", "", 1)
                        if not rel_name:
                            continue
                        is_link = m.issym() or m.islnk()
                        if not is_safe_path(
                            es_infra_root, rel_name, is_link, m.linkname
                        ):
                            UI.error(f"Security: Skipping unsafe ES member: {m.name}")
                            continue

                        # Temporarily adjust member name for extraction into the target dir
                        m.name = rel_name
                        tar.extract(m, path=es_infra_root)  # nosec B202

            # 3. Hydrate Named Volumes (LDM-382)
            # If using Named Volumes (macOS), sync the extracted files into Docker
            # volumes. Goes through hydrate_named_volumes_with_sync_wait() (not
            # _hydrate_named_volumes() directly) so the mandatory 2-second host
            # filesystem sync wait (VirtioFS/gRPC-FUSE lag) is never skipped on
            # this restore path -- see ldm-architecture/SKILL.md.
            self.facade.volumes.hydrate_named_volumes_with_sync_wait(paths)

    def _generate_snapshot_metadata(  # noqa: C901, PLR0912, PLR0915
        self,
        name,
        timestamp,
        project_meta,
        root,
        paths,
        snap_dir,
        db_snapshot_file,
        search_snapshot_name,
    ):
        custom_env_dict = {}
        compose_path = paths["root"] / "docker-compose.yml"
        if compose_path.exists():
            try:
                from ldm_core.utils import yaml_to_dict

                compose_data = yaml_to_dict(compose_path.read_text())
                liferay_service = (compose_data.get("services") or {}).get(
                    "liferay", {}
                )
                env_vars = liferay_service.get("environment", [])
                if isinstance(env_vars, list):
                    standard_vars = [
                        "LIFERAY_JVM_OPTS",
                        "LIFERAY_HOME",
                        "LIFERAY_HSQL_PERIOD_ENABLED",
                    ]
                    for var in env_vars:
                        if "=" in var:
                            key, val = var.split("=", 1)
                            if key.startswith("LIFERAY_") and key not in standard_vars:
                                custom_env_dict[key] = val
            except Exception as e:
                UI.warning(
                    f"Could not parse docker-compose.yml for environment variables: {e}"
                )

        db_included = db_snapshot_file is not None and db_snapshot_file.exists()

        has_data = False
        data_dir = paths.get("data")
        if data_dir and data_dir.exists() and data_dir.is_dir():
            try:
                has_data = any(data_dir.iterdir())
            except Exception:
                pass

        has_cx = False
        for d in ["cx", "deploy"]:
            dir_path = paths.get(d)
            if dir_path and dir_path.exists() and dir_path.is_dir():
                try:
                    if any(dir_path.glob("*.zip")):
                        has_cx = True
                        break
                except Exception:
                    pass
        if not has_cx:
            ce_dir = paths.get("ce_dir")
            if ce_dir and ce_dir.exists() and ce_dir.is_dir():
                try:
                    if any(ce_dir.glob("*.zip")) or any(ce_dir.glob("*/dist/*.zip")):
                        has_cx = True
                except Exception:
                    pass

        has_modules = False
        for d in ["modules", "deploy"]:
            dir_path = paths.get(d)
            if dir_path and dir_path.exists() and dir_path.is_dir():
                try:
                    if any(dir_path.glob("*.jar")) or any(dir_path.glob("*.war")):
                        has_modules = True
                        break
                except Exception:
                    pass
        if not has_modules:
            for s_folder in ["modules", "themes"]:
                base = paths["root"] / s_folder
                if base.exists() and base.is_dir():
                    try:
                        if any(base.glob("**/build/libs/*.[jw]ar")):
                            has_modules = True
                            break
                    except Exception:
                        pass

        cx_list = []
        ce_dir = paths.get("ce_dir")
        if ce_dir and ce_dir.exists() and ce_dir.is_dir():
            try:
                cx_list = [f.name for f in ce_dir.glob("*.zip")]
                if not cx_list:
                    cx_list = [f.name for f in ce_dir.glob("*/dist/*.zip")]
            except Exception:
                pass

        modules_list = []
        for d in ["modules", "deploy", "themes"]:
            dir_path = paths.get(d)
            if dir_path and dir_path.exists() and dir_path.is_dir():
                try:
                    for ext in ["*.jar", "*.war"]:
                        modules_list.extend([f.name for f in dir_path.glob(ext)])
                except Exception:
                    pass
        modules_list = sorted(set(modules_list))

        active_services = []
        try:
            from ldm_core.utils import sanitize_id

            safe_name = sanitize_id(project_meta.get("container_name") or root.name)
            target_name = getattr(self.manager, "target", None) or (
                project_meta.get("target") if isinstance(project_meta, dict) else None
            )
            from ldm_core.docker_service import DockerService

            docker_prefix = DockerService.get_docker_cmd_prefix(target_name)

            cmd = [
                *docker_prefix,
                "ps",
                "--filter",
                f"label=com.liferay.ldm.project={safe_name}",
                "--filter",
                "status=running",
                "--format",
                '{{.Label "com.docker.compose.service"}}',
            ]
            res = self.manager.run_command(cmd, check=False)
            if res and res.strip():
                active_services = sorted(
                    {line.strip() for line in res.strip().splitlines() if line.strip()}
                )
        except Exception:
            pass

        meta = {
            "name": name,
            "timestamp": timestamp,
            "tag": project_meta.get("tag") or self.manager.defaults.get("tag"),
            "db_type": project_meta.get("db_type"),
            "host_name": getattr(self.manager.args, "host_name", None)
            or project_meta.get("host_name"),
            "ssl": str(
                getattr(self.manager.args, "ssl", None)
                if getattr(self.manager.args, "ssl", None) is not None
                else (project_meta.get("ssl") or "false")
            ).lower(),
            "search_snapshot": search_snapshot_name,
            "custom_env": json.dumps(custom_env_dict) if custom_env_dict else None,
            "includes_database": str(db_included).lower(),
            "includes_volume_assets": str(has_data).lower(),
            "includes_client_extensions": str(has_cx).lower(),
            "includes_osgi_modules": str(has_modules).lower(),
            "client_extensions": ",".join(cx_list) if cx_list else "",
            "osgi_modules": ",".join(modules_list) if modules_list else "",
            "active_services": ",".join(active_services) if active_services else "",
        }
        self.manager.write_meta(snap_dir, meta)
