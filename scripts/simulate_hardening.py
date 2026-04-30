import os
from pathlib import Path

from ldm_core.handlers.base import BaseHandler


class MockArgs:
    def __init__(self):
        self.verbose = True
        self.non_interactive = True
        self.project = None
        self.project_flag = None
        self.tag = "latest"
        self.db = "hypersonic"
        self.name = "Test Snapshot"
        self.files_only = False
        self.no_stop = True


def simulate_harden_checks():
    print("=== LDM HARDENING SIMULATION ===")
    args = MockArgs()
    handler = BaseHandler(args)

    test_root = Path("./simulation-project").resolve()
    if test_root.exists():
        handler.safe_rmtree(test_root)
    test_root.mkdir(parents=True, exist_ok=True)

    paths = {
        "root": test_root,
        "files": test_root / "files",
        "data": test_root / "data",
        "backups": test_root / "snapshots",
    }

    print(f"\n1. Testing Recursive Permission Fix on: {test_root}")
    # Create a 'protected' file
    protected_dir = test_root / "data" / "nested"
    protected_dir.mkdir(parents=True, exist_ok=True)
    protected_file = protected_dir / "secret.txt"
    try:
        protected_file.write_text("docker-created-content")
    except PermissionError:
        # If it already has restricted permissions from a previous failed run,
        # we proceed to the reclamation phase.
        pass

    # Simulate Docker-like restriction (make it read-only for current user)
    try:
        os.chmod(protected_file, 0o000)
        os.chmod(protected_dir, 0o500)  # Read-only/Navigate-only
    except PermissionError:
        pass

    print("   [Before] Permission Check: Restricted")

    # Run the reclamation
    handler.verify_runtime_environment(paths)

    print("   [After] Permission Check: Success")
    if os.access(protected_file, os.R_OK):
        print("   ✅ SUCCESS: Recursive permissions reclaimed.")
    else:
        print("   ❌ FAILURE: Permissions still restricted.")

    print("\n2. Testing Dot Preservation in ID Handling")
    from ldm_core.utils import sanitize_id

    id_with_dots = "project.2025.q1.4"
    sanitized = sanitize_id(id_with_dots)
    print(f"   Original: {id_with_dots}")
    print(f"   Sanitized: {sanitized}")
    if sanitized == id_with_dots:
        print("   ✅ SUCCESS: Dots preserved.")
    else:
        print(f"   ❌ FAILURE: Dots stripped (Got: {sanitized})")

    # Cleanup
    handler.safe_rmtree(test_root)
    print("\n=== SIMULATION COMPLETE ===")


if __name__ == "__main__":
    simulate_harden_checks()
