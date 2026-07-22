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

            overrides = json.loads(user_templates_path.read_text())
            for key, val in overrides.items():
                if isinstance(val, dict) and "repo" in val and "default_name" in val:
                    templates[key.lower()] = val
        except Exception as e:
            UI.warning(
                f"Failed to load quickstart templates from {user_templates_path}: {e}"
            )

    name_lower = template_name.lower()
    if name_lower not in templates:
        UI.die(f"Unrecognized quickstart template: {template_name}")
        return
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

    # Cancellation Gate Hoisting (Issue #758)
    project_path = self.manager.detect_project_path(project_name, for_init=True)
    if project_path and project_path.exists():
        UI.warning(f"Project '{project_name}' already exists.")
        UI.interruptible_pause(3, "Press CTRL+C to cancel ")

    # Phase 1: Download & Provision
    UI.phase(1, 4, f"Provisioning '{template_name}' from GitHub")
    self.cmd_import(repo_url, project_id=project_name, no_run=True)

    # Detect paths after import
    project_path = self.manager.detect_project_path(project_name)
    if not project_path or not project_path.exists():
        UI.die(f"Failed to locate imported workspace directory for {project_name}.")
        return

    project_meta = self.manager.read_meta(project_path)

    # Let downstream components know this was initiated via quickstart
    project_meta["is_quickstart"] = True
    self.manager.write_meta(project_path, project_meta)

    tag = project_meta.get("tag")
    db_type = project_meta.get("db_type", "postgresql")

    if not tag:
        tag = "2026.q1.4-lts"  # sensible fallback version
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

    restored_from_pkg = (
        str(project_meta.get("restored_from_package", "false")).lower() == "true"
    )
    pkg_has_db = (
        str(project_meta.get("package_includes_database", "false")).lower() == "true"
    )

    # Phase 3: Infrastructure (Database Seeding)
    UI.phase(3, 4, "Setting up Infrastructure")
    if restored_from_pkg and pkg_has_db:
        UI.detail(
            "Project was restored from LDM package snapshot. Skipping database seeding."
        )
    else:
        UI.detail(f"Seeding database for {tag} ({db_type}/{search_mode})...")
        paths = self.manager.setup_paths(project_path)
        if not self.manager.assets._fetch_seed(tag, db_type, search_mode, paths):
            UI.detail(
                "No pre-warmed seed applied. Liferay will initialize the database schema on first boot (this may take several minutes)."
            )

    # Phase 4: Start Stack
    UI.phase(4, 4, "Starting Services Stack")

    if share:
        UI.detail("Exposing quickstart tunnel...")
        self.manager.share.cmd_start(project_name, subdomain=share_subdomain)

    self.manager.runtime.cmd_run(project_name, browser=True)
