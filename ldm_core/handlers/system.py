from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI


class SystemService(BaseHandler):
    """Handler for system-level lifecycle commands like nuke and rescue."""

    def __init__(self, manager):
        super().__init__(manager.args)
        self.manager = manager

    def cmd_nuke(self, force=False, keep_config=False):
        """Completely wipes LDM state, caches, certificates, hosts, and containers."""
        if not force:
            confirm = UI.ask(
                "Are you absolutely sure you want to nuke LDM? "
                "This will destroy all containers, caches, SSL certs, and hosts entries. (y/n)",
                "n",
            )
            if str(confirm).lower() != "y":
                UI.info("Nuke aborted.")
                return False

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
        if not project_id:
            # 1. Global Traefik SSL/Host rescue
            UI.info("Executing LDM Global Infrastructure Rescue...")

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
                        f"⚠️ Port {port} is already in use by another process on your host."
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
