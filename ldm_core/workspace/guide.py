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
            choice = input("\nEnter choice [1-5/A/Q]: ").strip().upper()
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
        elif choice == "Q" or choice == "":
            UI.info(
                "Exiting LDM Onboarding Guide. Run 'ldm run' to start your first environment!"
            )
            break
        else:
            UI.warn(
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
    print("""
  Customizing LDM behavior (3 Precedence Levels):

  1. Runtime Flags (Single Execution):
     $ ldm run --db mysql
     $ ldm run --port 9090
     $ ldm run --database-mode isolated

  2. Workspace Overrides (.ldm/config.json in project root):
     Lock settings for a specific repository folder.

  3. Global User Defaults (~/.ldmrc):
     $ ldm config set default_db mysql
     $ ldm config set database_mode isolated
     (Applies across all LDM workspaces for the current user)
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
