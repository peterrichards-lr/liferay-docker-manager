from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from ldm_core.ui import UI


def cmd_quickstart(self, template_name, share=False, share_subdomain=None):  # noqa: C901, PLR0912, PLR0915
    """Bootstraps a predefined accelerator stack, imports, seeds, runs, and exposes it."""
    templates = {
        "aica": {
            "repo": "https://github.com/peterrichards-lr/liferay-ai-commerce-accelerator.git",
            "default_name": "liferay-ai-commerce-accelerator",
        }
    }

    # Load user templates overrides if file exists
    from ldm_core.utils import get_actual_home

    user_templates_path = get_actual_home() / ".ldm_templates.json"
    if user_templates_path.exists():
        try:
            import json

            # LDM-#2281: explicit utf-8-sig. The platform default is cp1252 on
            # Windows, which turns a byte-order mark into text json rejects.
            overrides = json.loads(user_templates_path.read_text(encoding="utf-8-sig"))
            for key, val in overrides.items():
                if isinstance(val, dict) and "repo" in val and "default_name" in val:
                    templates[key.lower()] = val
        except Exception as e:
            UI.warning(
                f"Failed to load quickstart templates from {user_templates_path}: {e}"
            )

    name_lower = template_name.lower()
    if name_lower not in templates:
        UI.die(
            f"Unrecognized quickstart template: {template_name}",
            tip=f"Available: {', '.join(sorted(templates))}. Add your own in "
            f"{user_templates_path}.",
        )
        return None
    template_info = templates[name_lower]
    repo_url = template_info["repo"]
    project_name = template_info["default_name"]
    cli_name = getattr(self.manager.args, "name", None)
    if cli_name and isinstance(cli_name, str):
        project_name = cli_name

    if template_info.get("private"):
        from ldm_core.utils import get_github_token

        if not get_github_token():
            UI.die(
                f"Template '{template_name}' requires a private repository.\n"
                "Please authenticate using the GitHub CLI ('gh auth login') or set the GITHUB_PAT environment variable."
            )

    UI.heading(f"Starting Quickstart: {template_name.upper()}")

    project_path = self.manager.detect_project_path(project_name, for_init=True)

    # LDM-#2269: decide BEFORE stopping anything. Quickstart over an existing
    # project used to warn and carry on, and the package restore then runs
    # `cmd_reset(target="all")` on any project with a compose file -- `down -v`
    # plus the data directories -- so a re-run, or `--name` pointing at an
    # unrelated project, deleted its database and volumes after one yellow
    # line. A project is "existing" on the same evidence the reset acts on.
    if project_path and project_path.exists():
        from ldm_core.utils import resolve_meta_file_path

        is_project = (
            resolve_meta_file_path(project_path).exists()
            or (project_path / "docker-compose.yml").exists()
        )
        if is_project:
            wipe = (
                f"Project '{project_name}' already exists at {project_path}. "
                "Quickstart would replace it, deleting the project's database "
                "and volumes"
            )
            if getattr(self.manager.args, "replace", False) is True:
                UI.warning(f"{wipe} (--replace was given).")
            elif self.manager.non_interactive:
                UI.die(f"{wipe}; pass --replace to do that.")
                return None
            elif not UI.confirm(f"{wipe}. Continue?", default="N"):
                UI.info("Nothing was changed.")
                return None
            # LDM-#2275: a confirmed prompt is a replacement too, and the
            # package import reads this to resolve the replaced project's modes.
            self.manager.args.replace = True

    # Ensure running project is stopped BEFORE downloading/provisioning from GitHub
    self._ensure_stopped(project_name, project_path)

    # Phase 1: Download & Provision
    UI.phase(1, 4, f"Provisioning '{template_name}' from GitHub")
    self.cmd_import(repo_url, project_id=project_name, no_run=True)

    # Detect paths after import
    project_path = self.manager.detect_project_path(project_name)
    if not project_path or not project_path.exists():
        UI.die(f"Failed to locate imported workspace directory for {project_name}.")
        return None

    project_meta = self.manager.read_meta(project_path)

    # Let downstream components know this was initiated via quickstart
    project_meta["is_quickstart"] = True
    self.manager.write_meta(project_path, project_meta)

    tag = project_meta.get("tag")
    db_type = project_meta.get("db_type", "postgresql")

    # LDM-#1535: settle /etc/hosts here, not at "Starting Services Stack".
    # Declining is a legitimate answer, and it used to arrive after the seed
    # download and the stack boot -- everything expensive already spent on a
    # demo that cannot resolve its own hostname.
    #
    # This is still after the download and restore, because host_name comes
    # from the manifest the import writes. It is as early as the fact exists,
    # not as early as one would like. What it does buy: the seed fetch and the
    # compose boot have not happened yet, and the project on disk is complete,
    # so `ldm doctor --fix-hosts` + `ldm run` finishes the job later.
    #
    # The sudo prompt stays deliberately: it is the only step reaching outside
    # LDM's own directories, and sudo will ask for a password anyway -- so
    # removing the question would not remove the interruption, only the
    # explanation for it.
    quickstart_host = project_meta.get("host_name")
    if quickstart_host:
        self.manager.ensure_hostnames_resolve(
            project_path, quickstart_host, project_id=project_name
        )

    if not tag:
        # LDM-#1514: this is now a second-chance guard. The tag is settled
        # during package verification, so a packaged import should never
        # reach here -- but a project with no manifest at all still can.
        from ldm_core.constants import FALLBACK_LIFERAY_TAG

        tag = FALLBACK_LIFERAY_TAG
        project_meta["tag"] = tag
        self.manager.write_meta(project_path, project_meta)
        UI.warning(
            f"Project metadata missing 'tag'. Falling back to default Liferay tag: {tag}"
        )
        UI.interruptible_pause(3, "Press CTRL+C to cancel ")

    # Phase 2: Configuration
    UI.phase(2, 4, "Configuring Environment")
    default_shared = (
        "true" if self.manager.parse_version(tag) >= (2025, 1, 0) else "false"
    )
    use_shared = (
        str(project_meta.get("use_shared_search", default_shared)).lower() == "true"
    )
    if not use_shared and self.manager.parse_version(tag) >= (2025, 2, 0):
        use_shared = True
    search_mode = "shared" if use_shared else "sidecar"
    # LDM-#2267: the import's scaffold run has already resolved and persisted
    # the mode the project will boot in. Seed for THAT one. The guess above
    # disagreed with it -- "Seeding ... (postgresql/shared)" over a project
    # that then booted sidecar -- and stays only as the fallback for a
    # project with no recorded mode.
    recorded_search = str(project_meta.get("search_mode") or "").lower()
    if recorded_search in ("shared", "sidecar"):
        search_mode = recorded_search
    elif project_meta.get("ldm_version"):
        # LDM-#2275: a replaced project has had its recorded modes dropped,
        # and the restore skips the scaffold run when a compose file exists,
        # so nothing records one before the boot. Ask the resolver the boot
        # will ask, rather than guessing from the tag.
        from ldm_core.utils import resolve_infrastructure_mode

        search_mode = resolve_infrastructure_mode(
            "search_mode",
            project_meta,
            self.manager.defaults,
            getattr(self.manager.args, "search_mode", None),
        )

    restored_from_pkg = (
        str(project_meta.get("restored_from_package", "false")).lower() == "true"
    )
    pkg_has_db = (
        str(project_meta.get("package_includes_database", "false")).lower() == "true"
    )

    # Phase 3: Infrastructure (Database Seeding)
    UI.phase(3, 4, "Setting up Infrastructure")
    if getattr(self.manager.args, "no_seed", False):
        # LDM-#1535: the reused opt-out. Same flag name and meaning as on run,
        # import, init-from, link and clone.
        UI.detail("Skipping database seeding (--no-seed).")
    elif restored_from_pkg and pkg_has_db:
        UI.detail(
            "Project was restored from LDM package snapshot. Skipping database seeding."
        )
    else:
        UI.detail(f"Seeding database for {tag} ({db_type}/{search_mode})...")
        paths = self.manager.setup_paths(project_path)
        # assume_yes: quickstart declares the answer rather than asking it.
        if not self.manager.assets._fetch_seed(
            tag, db_type, search_mode, paths, assume_yes=True
        ):
            UI.detail(
                "No pre-warmed seed applied. Liferay will initialize the database schema on first boot (this may take several minutes)."
            )

    # Phase 4: Start Stack
    UI.phase(4, 4, "Starting Services Stack")

    # LDM-#2268: the tunnel is the run pipeline's to start, not ours. This
    # used to call share.cmd_start here, BEFORE cmd_run -- and since LDM-#2009
    # cmd_start refuses a project that is not running, so `quickstart --share`
    # did all the expensive work and then exited 3 with no stack. cmd_run
    # already shares after readiness when args.share is set
    # (ConfigResolutionStage._resolve_share_and_expose), so hand the request
    # over on args rather than starting a second tunnel. Set explicitly
    # because a programmatic caller passes `share=` without args carrying it.
    if share:
        self.manager.args.share = True
        if share_subdomain:
            self.manager.args.share_subdomain = share_subdomain
        UI.detail("The tunnel will start once the portal is ready.")

    # LDM-#2155: returned, not discarded. `cmd_run` answers with the run
    # pipeline's own verdict, and this was the last statement in the function
    # -- so a quickstart whose stack never came up returned None and exited 0.
    # The CLI turns a False into exit 4 (`_exit_code_for_pipeline`).
    return self.manager.runtime.cmd_run(project_name, browser=True)
