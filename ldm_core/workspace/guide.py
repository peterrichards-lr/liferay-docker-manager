"""Interactive Onboarding Guide and LDM Conventions walkthrough (Sub-Issue #1206)."""

import sys

from ldm_core.ui import UI


def cmd_guide(manager):
    """Execute the interactive onboarding guide or print non-interactive summary."""
    non_interactive = (
        getattr(manager.args, "non_interactive", False) or not sys.stdin.isatty()
    )

    UI.heading("LDM Developer Onboarding & Interactive Guide")

    topics = {
        "1": ("🚀 Quickstart Workflow", _print_quickstart_workflow),
        "2": ("⚙️ LDM Conventions & Defaults", _print_conventions_defaults),
        "3": ("🔧 Customizing Defaults (DBs, Ports)", _print_customizing_defaults),
        "4": ("💾 Data Management & Snapshots", _print_data_snapshots),
        "5": ("🌐 Compute & Sharing (Advanced)", _print_compute_sharing),
        "6": ("☁️ Liferay Cloud PaaS Deployment", _print_cloud_deployment),
    }

    if non_interactive:
        for idx in sorted(topics.keys()):
            title, fn = topics[idx]
            UI.info(f"\n--- {title} ---")
            fn()
        return

    while True:
        print("\nSelect an Onboarding Topic:")
        for idx in sorted(topics.keys()):
            print(f"  [{idx}] {topics[idx][0]}")
        print("  [A] Print All Topics")
        print("  [Q] Exit Guide")

        try:
            choice = input("\nEnter choice [1-6/A/Q]: ").strip().upper()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if choice in topics:
            UI.info(f"\n=== {topics[choice][0]} ===")
            topics[choice][1]()
        elif choice == "A":
            for idx in sorted(topics.keys()):
                title, fn = topics[idx]
                UI.info(f"\n=== {title} ===")
                fn()
        elif choice in ("Q", ""):
            UI.info(
                "Exiting LDM Onboarding Guide. Run 'ldm run' to start your first environment!"
            )
            break
        else:
            UI.warning(
                "Unrecognized selection. Please enter a choice between 1 and 5, A, or Q."
            )


def _print_quickstart_workflow():
    print("""
  1. Start Liferay Environment:
     $ ldm run
     (Spins up a local Liferay DXP/Portal instance with auto-provisioned database)

  2. Connect Client Extensions & Modules:
     $ ldm link ../my-client-extension
     (Hot-reloads local CX builds directly into your running container)

  3. Tail Container Logs:
     $ ldm logs -f

  4. Stop Container:
     $ ldm stop
""")


def _print_conventions_defaults():
    print("""
  LDM relies on sane defaults to minimize initial setup overhead:

  • Database: Shared PostgreSQL container on localhost:5432 (--db postgresql)
  • Search: Shared Elasticsearch 7 sidecar on localhost:9200 (--search-mode sidecar)
  • Web Server: Resolved at http://localhost:8080 (--port 8080)
  • OSGi State: Volume-backed persistent state across restarts
  • Internationalization: Transcodes German umlauts (ä->ae, ö->oe, ü->ue, ß->ss),
    accents (é->e, ñ->n), and CJK/Arabic scripts to valid RFC-1123 container IDs.
""")


def _print_customizing_defaults():
    """Prints the settings precedence, highest-priority first.

    LDM-#1824: this used to advertise three levels, one of which
    (`.ldm/config.json` in the project root) does not exist, and omitted the
    project `meta` file -- the level that actually freezes a project's
    settings. It also told the user to run `ldm config set database_mode`,
    which `handlers/config.py` refuses outright for any key in
    CONVENTION_DEFAULTS because `ldm config set` writes the root of ~/.ldmrc
    where the defaults resolver never looks.

    That mattered more than a stale help string: `ldm guide` is step 1 of
    docs/tutorials/first_5_minutes.md, so this was the first thing a new user
    saw.

    Kept in step with `DefaultsManager.get_resolved` (CONVENTION_DEFAULTS <
    /etc/ldmrc < ~/.ldmrc) and `resolve_infrastructure_mode` (CLI > project
    meta > defaults). `test_guide_matches_resolution_order` asserts it.
    """
    print("""
  Customizing LDM behavior (5 Precedence Levels, highest first):

  1. Runtime Flags (this run only):
     $ ldm run --db mysql
     $ ldm run --port 9090
     $ ldm run --database-mode isolated

  2. Project Metadata (the 'meta' file in the project root):
     Written when the project is created, and frozen thereafter.

  3. User Defaults (~/.ldmrc):
     $ ldm defaults default_db mysql
     $ ldm defaults database_mode isolated
     (Applies across all LDM projects for the current user)

  4. Global Defaults (/etc/ldmrc):
     $ ldm defaults database_mode isolated --global
     (System-wide; typically set by an administrator or CI provisioning)

  5. Convention Defaults:
     LDM's built-in fallbacks. Nothing to configure.

  Note: use 'ldm defaults', not 'ldm config set', for any of the above.
  'ldm config set' writes a different part of ~/.ldmrc that the defaults
  resolver ignores, and LDM refuses it for these keys rather than silently
  having no effect.
""")


def _print_data_snapshots():
    print("""
  Instant Checkpoints & Reproducible Archives:

  • Save Instant DB & Volume Snapshot:
    $ ldm snapshot save my-checkpoint

  • Restore Checkpoint:
    $ ldm snapshot restore my-checkpoint

  • Package Workspace into Hydrated Archive (.ldmp):
    $ ldm package

  • Import Hydrated Package:
    $ ldm import my-package.ldmp
""")


def _print_compute_sharing():
    print("""
  Remote Compute & Tunnel Sharing:

  • Add Remote Compute Node:
    $ ldm target add aws-1 --host 192.168.1.50

  • Run Environment on Remote Target:
    $ ldm run --node aws-1

  • Share Local Instance via Public Tunnel:
    $ ldm share start --subdomain my-demo
""")


def _print_cloud_deployment():
    print("""
  Liferay Cloud PaaS Deployment (ldm cloud deploy):

  • Git-Driven Jenkins Deployment (Default):
    $ ldm cloud deploy my-project -e dev

  • Direct Fast-Path CLI Deployment (--direct):
    $ ldm cloud deploy my-project -e dev --direct --service liferay

  • Production Deployments (--force / Safety Lock):
    $ ldm cloud deploy my-project -e prd --force -y
""")
