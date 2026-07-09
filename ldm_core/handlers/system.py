from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI


class SystemService(BaseHandler):
    """Handler for system-level lifecycle commands like nuke and rescue."""

    def __init__(self, manager):
        super().__init__(manager.args)
        self.manager = manager

    def cmd_nuke(self, force=False, keep_config=False):
        """Completely wipes LDM state, caches, certificates, hosts, and containers."""
        if getattr(self.manager, "dry_run", False):
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would stop and tear down all registered projects.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would tear down global Traefik infrastructure.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would clean up LDM entries in hosts file.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would prune dangling Docker volumes and networks.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would delete global certificates, registry, and cache directories.{UI.COLOR_OFF}"
            )
            if not keep_config:
                UI.info(
                    f"{UI.BYELLOW}[Dry Run] Would delete ~/.ldmrc config file.{UI.COLOR_OFF}"
                )
            UI.success("💥 [Dry Run] Nuke completed (no changes made).")
            return True

        if not force:
            confirm = UI.ask(
                "Are you absolutely sure you want to nuke LDM? "
                "This will destroy all containers, caches, SSL certs, and hosts entries. (y/n)",
                "n",
            )
            if str(confirm).lower() != "y":
                UI.info("Nuke aborted.")
                return False

        drop_global_vols = force
        from ldm_core.utils import has_shared_projects

        if not force and has_shared_projects(self.manager):
            drop_global_vols = UI.confirm(
                "Do you also want to drop global data volumes (liferay-db-global-data, liferay-search-global-data)? "
                "This deletes data for ALL shared projects.",
                default="N",
            )

        # 1. Stop all registered projects
        UI.info("Stopping and tearing down all registered projects...")
        try:
            roots = self.manager.find_dxp_roots()
            for r in roots:
                path = r["path"]
                meta = self.manager.read_meta(path)
                name = (
                    meta.get("liferay_container_name")
                    or meta.get("container_name")
                    or path.name
                )
                UI.info(f"Tearing down project stack: {name}...")
                self.manager.runtime.cmd_down(project_id=path.name, delete=False)
        except Exception as e:
            UI.warning(f"Error stopping projects: {e}")

        # 2. Stop global Traefik infrastructure
        UI.info("Tearing down global Traefik infrastructure...")
        try:
            self.manager.runtime.cmd_down(infra=True)
        except Exception as e:
            UI.warning(f"Error stopping Traefik: {e}")

        # 3. Remove all hosts file entries
        UI.info("Cleaning up hosts file...")
        try:
            self._remove_hosts_entries(all_ldm=True)
        except Exception as e:
            UI.warning(f"Error cleaning hosts file: {e}")

        # 4. Prune Docker networks/volumes
        UI.info("Pruning dangling Docker resources...")
        try:
            self.manager.run_command(
                ["docker", "network", "rm", "liferay-net"], check=False
            )
            if drop_global_vols:
                self.manager.run_command(
                    [
                        "docker",
                        "volume",
                        "rm",
                        "-f",
                        "liferay-db-global-data",
                        "liferay-search-global-data",
                    ],
                    check=False,
                )
            self.manager.run_command(["docker", "volume", "prune", "-f"], check=False)
            self.manager.run_command(["docker", "network", "prune", "-f"], check=False)
        except Exception as e:
            UI.warning(f"Error pruning docker: {e}")

        # 5. Wipe global certificates and caches
        from ldm_core.utils import get_actual_home, safe_rmtree

        ldm_dir = get_actual_home() / ".ldm"

        if ldm_dir.exists():
            for folder in ["certs", "cache", "registry.json"]:
                target = ldm_dir / folder
                if target.exists():
                    UI.info(f"Deleting {target}...")
                    try:
                        if target.is_dir():
                            safe_rmtree(target)
                        else:
                            target.unlink()
                    except Exception as e:
                        UI.warning(f"Error deleting {target}: {e}")

        # 6. Delete global config ~/.ldmrc if requested
        if not keep_config:
            rc_file = get_actual_home() / ".ldmrc"
            if rc_file.exists():
                UI.info("Deleting global config ~/.ldmrc...")
                try:
                    rc_file.unlink()
                except Exception as e:
                    UI.warning(f"Error deleting .ldmrc: {e}")

        UI.success(
            "💥 LDM successfully nuked! All Docker stacks, caches, SSL certs, and hosts entries have been wiped clean."
        )
        return True

    def cmd_rescue(self, project_id=None):
        """Active self-healing and recovery for LDM local environments."""
        is_dry_run = getattr(self.manager, "dry_run", False)
        if not project_id:
            # 1. Global Traefik SSL/Host rescue
            UI.info("Executing LDM Global Infrastructure Rescue...")
            if is_dry_run:
                UI.info(
                    f"{UI.BYELLOW}[Dry Run] Would check and auto-repair global common portal-ext.properties.{UI.COLOR_OFF}"
                )
                UI.info(
                    f"{UI.BYELLOW}[Dry Run] Would check and auto-repair workspace common portal-ext.properties.{UI.COLOR_OFF}"
                )
                UI.info(
                    f"{UI.BYELLOW}[Dry Run] Would scan for port 80/443 conflicts.{UI.COLOR_OFF}"
                )
                UI.info(
                    f"{UI.BYELLOW}[Dry Run] Would recreate shared Docker network 'liferay-net'.{UI.COLOR_OFF}"
                )
                UI.info(
                    f"{UI.BYELLOW}[Dry Run] Would regenerate global infrastructure SSL certificates.{UI.COLOR_OFF}"
                )
                UI.info(
                    f"{UI.BYELLOW}[Dry Run] Would force-recreate Traefik infrastructure container.{UI.COLOR_OFF}"
                )
                UI.success("🏥 [Dry Run] Global rescue completed (no changes made).")
                return True

            # Auto-repair global properties
            from pathlib import Path

            from ldm_core.utils import get_actual_home

            actual_home = get_actual_home()
            global_pe = actual_home / ".ldm" / "common" / "portal-ext.properties"
            if global_pe.exists():
                UI.info("Checking global common properties syntax...")
                self._rescue_properties_file(global_pe)

            local_common_pe = Path.cwd() / "common" / "portal-ext.properties"
            if local_common_pe.exists():
                UI.info("Checking workspace common properties syntax...")
                self._rescue_properties_file(local_common_pe)

            # Port conflict scan
            UI.info("Checking for port conflicts on 80 and 443...")
            import socket

            for port in [80, 443]:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.0)
                try:
                    s.bind(("127.0.0.1", port))
                    s.close()
                except OSError:
                    UI.warning(
                        f"Port {port} is already in use by another process on your host."
                    )
                    UI.info(
                        f"Please ensure no local IIS, Apache, Nginx, or Skype process is binding to {port}."
                    )

            # Recreate shared network
            UI.info("Restoring shared docker bridge network 'liferay-net'...")
            self.manager.run_command(
                ["docker", "network", "create", "liferay-net"], check=False
            )

            # Traefik SSL refresh
            UI.info("Regenerating global infrastructure SSL certificates...")
            try:
                self.manager.runtime.cmd_renew_ssl(all_projects=True)
            except Exception as e:
                UI.warning(f"SSL certificate renewal failed: {e}")

            # Recreate Traefik container
            UI.info("Force-recreating Traefik infrastructure container...")
            try:
                self.manager.infra.cmd_infra_setup()
            except Exception as e:
                UI.warning(f"Infrastructure setup failed: {e}")

            UI.success("Global LDM infrastructure rescued successfully!")
            return True

        # 2. Project-Specific Rescue
        root = self.detect_project_path(project_id, fatal=False)
        if not root:
            UI.die(f"Project '{project_id}' not found.")

        if is_dry_run:
            meta = self.read_meta(root)
            name = (
                meta.get("liferay_container_name")
                or meta.get("container_name")
                or root.name
            )
            UI.info(f"Executing LDM Rescue for project: {name}...")
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would check and auto-repair project portal-ext.properties syntax.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would check and auto-repair project workspace common properties syntax.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would stop project containers.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would scan for and remove PostgreSQL postmaster.pid lock files.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would scan for and remove OSGi state .lock files.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would regenerate SSL certificates for project.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would recreate project containers with clean volume initialization.{UI.COLOR_OFF}"
            )
            UI.info(
                f"{UI.BYELLOW}[Dry Run] Would run post-rescue verification checks.{UI.COLOR_OFF}"
            )
            UI.success(
                f"🏥 [Dry Run] Project '{root.name}' rescue completed (no changes made)."
            )
            return True

        # Check and rescue properties files for this project
        project_pe = root / "files" / "portal-ext.properties"
        if project_pe.exists():
            UI.info("Checking project portal-ext.properties syntax...")
            self._rescue_properties_file(project_pe)

        workspace_pe = root.parent / "common" / "portal-ext.properties"
        if workspace_pe.exists():
            UI.info("Checking project's workspace common properties syntax...")
            self._rescue_properties_file(workspace_pe)

        meta = self.read_meta(root)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or root.name
        )
        UI.info(f"Executing LDM Rescue for project: {name}...")

        # Stop project containers first
        UI.info("Stopping project containers safely...")
        try:
            self.manager.runtime.cmd_down(project_id=root.name, delete=False)
        except Exception:
            pass

        # Clear common postgres crash-loop lock files (postmaster.pid)
        UI.info("Scanning for postgres postmaster.pid lock files...")
        postgres_data_dir = root / "data"
        if postgres_data_dir.exists():
            for p_file in postgres_data_dir.rglob("postmaster.pid"):
                UI.info(f"Removing PostgreSQL lock file: {p_file.name}")
                try:
                    p_file.unlink()
                except Exception as e:
                    UI.warning(f"Failed to remove pg lock file: {e}")

        # Clear OSGi state locks
        UI.info("Scanning for OSGi state lock files...")
        osgi_state_dir = root / "osgi" / "state"
        if osgi_state_dir.exists():
            for lock in osgi_state_dir.rglob(".lock"):
                UI.info(f"Removing OSGi state lock file: {lock.name}")
                try:
                    lock.unlink()
                except Exception as e:
                    UI.warning(f"Failed to remove OSGi lock: {e}")

        # Renew SSL for project
        UI.info(f"Regenerating SSL certificates for project '{root.name}'...")
        try:
            self.manager.runtime.cmd_renew_ssl(project_id=root.name)
        except Exception as e:
            UI.warning(f"Failed to renew SSL: {e}")

        # Force recreate containers on start
        UI.info("Recreating project containers with clean volume initialization...")
        try:
            self.manager.runtime.cmd_run(project_id=root.name)
        except Exception as e:
            UI.warning(f"Failed to run project: {e}")

        # Run doctor check to verify
        UI.info("Running post-rescue verification checks...")
        self.manager.cmd_doctor(project_id=root.name)

        UI.success(f"Project '{root.name}' rescue completed successfully!")
        return True

    def _rescue_properties_file(self, file_path):
        """Checks and auto-repairs broken trailing backslash continuations in a properties file."""
        import re
        from pathlib import Path

        from ldm_core.utils import is_continuation_line

        file_path = Path(file_path)
        if not file_path.exists():
            return False

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            UI.warning(f"Could not read {file_path.name} for properties rescue: {e}")
            return False

        lines = content.splitlines()
        if not lines:
            return False

        new_prop_regex = re.compile(r"^[a-zA-Z0-9._-]+\s*=")
        modified = False
        new_lines = []

        for i in range(len(lines)):
            line = lines[i]
            stripped_line = line.rstrip()
            # Check if this line ends with a backslash
            if is_continuation_line(stripped_line):
                is_broken = False
                # Condition 1: Last line of file
                if i + 1 >= len(lines):
                    is_broken = True
                else:
                    next_line = lines[i + 1]
                    next_stripped = next_line.strip()
                    # Condition 2: Followed by an empty line
                    if (
                        not next_stripped
                        or next_stripped.startswith(("#", "!"))
                        or (
                            new_prop_regex.match(next_stripped)
                            and not next_line.startswith((" ", "\t"))
                        )
                    ):
                        is_broken = True

                if is_broken:
                    # Strip the trailing backslash and trailing whitespace
                    idx = line.rfind("\\")
                    line = line[:idx].rstrip()
                    modified = True
                    UI.success(
                        f"🔧 [Self-Healing] Repaired broken properties continuation at {file_path.name}:{i + 1}"
                    )
            new_lines.append(line)

        if modified:
            try:
                from ldm_core.utils import safe_write_text

                safe_write_text(file_path, "\n".join(new_lines).strip() + "\n")
                return True
            except Exception as e:
                UI.warning(
                    f"Failed to write repaired properties to {file_path.name}: {e}"
                )
        return False

    def cmd_init_ci(
        self,
        repo=None,
        workflow_name="ldm-package-release.yml",
        trigger=None,
        project_id=None,
    ):
        """Scaffolds a GitHub Actions workflow to build and package LDM releases."""
        from pathlib import Path

        from ldm_core.constants import VERSION

        # 1. Resolve repository root
        repo_root = None
        try:
            res = self.manager.run_command(
                ["git", "rev-parse", "--show-toplevel"], check=False
            )
            if res and res.strip():
                repo_root = Path(res.strip())
        except Exception:
            pass

        if not repo_root:
            curr = Path.cwd().resolve()
            for parent in [curr, *curr.parents]:
                if (parent / ".git").exists():
                    repo_root = parent
                    break

        if not repo_root:
            repo_root = Path.cwd().resolve()

        # 2. Auto-detect origin repository identifier
        if not repo:
            try:
                origin_url = self.manager.run_command(
                    ["git", "remote", "get-url", "origin"],
                    cwd=str(repo_root),
                    check=False,
                ).strip()
                if origin_url:
                    parsed = self.manager.workspace._parse_github_repo(origin_url)
                    if parsed:
                        repo = f"{parsed[0]}/{parsed[1]}"
            except Exception:
                pass

        if not repo:
            if self.manager.non_interactive:
                repo = "owner/repo"
            else:
                repo = UI.ask(
                    "Enter GitHub Repository Identifier (owner/repo)",
                    "my-org/my-repo",
                )
                if not repo:
                    repo = "owner/repo"

        # 3. Resolve Trigger preset (using defaults configuration as fallback)
        resolved_trigger = (
            trigger or self.manager.defaults.get("ci_trigger") or "release"
        )

        triggers_yaml = {
            "release": """on:
  release:
    types: [published]
  workflow_dispatch:""",
            "tag": """on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:""",
            "push": """on:
  push:
    branches:
      - master
  workflow_dispatch:""",
            "manual": """on:
  workflow_dispatch:""",
        }
        trigger_block = triggers_yaml.get(resolved_trigger, triggers_yaml["release"])

        # 4. Resolve project argument string
        project_arg = ""
        if project_id:
            project_arg = f"{project_id} "

        # 5. Build workflow YAML content
        workflow_content = f"""name: LDM Package Release

{trigger_block}

permissions:
  contents: write

jobs:
  build-and-package:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Set up JDK 21
        uses: actions/setup-java@v4
        with:
          distribution: 'temurin'
          java-version: '21'

      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '22'

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install LDM
        run: |
          python -m pip install --upgrade pip
          pip install liferay-docker-manager=={VERSION}

      - name: Start Liferay Environment
        run: |
          ldm import . --non-interactive --build

      - name: Wait for Liferay & Deployments
        run: |
          ldm wait --non-interactive --wait-for-deployables

      - name: Create LDM Package
        run: |
          mkdir -p ./dist
          ldm package {project_arg}--non-interactive --repo "{repo}" --output ./dist

      - name: Stop Liferay Environment
        run: |
          ldm down --non-interactive

      - name: Upload Package as Action Artifact
        uses: actions/upload-artifact@v4
        with:
          name: ldm-package
          path: |
            dist/*.ldmp
            dist/*.ldmp.sha256

      - name: Upload LDM Package to Release
        uses: softprops/action-gh-release@v2
        if: startsWith(github.ref, 'refs/tags/') || github.event_name == 'release'
        with:
          files: |
            dist/*.ldmp
            dist/*.ldmp.sha256
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
"""

        # 6. Save workflow file with collision check
        workflows_dir = repo_root / ".github" / "workflows"
        target_file = workflows_dir / workflow_name

        if target_file.exists():
            if self.manager.non_interactive:
                UI.warning(
                    f"Workflow file '{workflow_name}' already exists. Overwriting in non-interactive mode."
                )
            else:
                confirm = UI.confirm(
                    f"Workflow file '{workflow_name}' already exists. Overwrite?",
                    "N",
                )
                if not confirm:
                    UI.info("Aborted. Scaffolding canceled.")
                    return False

        try:
            from ldm_core.utils import safe_write_text

            # Ensure parent directories exist
            workflows_dir.mkdir(parents=True, exist_ok=True)
            safe_write_text(target_file, workflow_content)
            UI.success(
                f"Successfully generated GitHub Actions release workflow: {target_file}"
            )
            return True
        except Exception as e:
            UI.die(f"Failed to write workflow file: {e}")
            return False
