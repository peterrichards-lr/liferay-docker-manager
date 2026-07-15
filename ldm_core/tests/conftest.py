import os

import pytest


@pytest.fixture(autouse=True)
def suppress_browser():
    """Globally suppresses browser launching during tests."""
    os.environ["LDM_TEST_MODE"] = "true"
    yield
    # We don't necessarily need to unset it as it's just for the test process


@pytest.fixture(autouse=True)
def clear_dry_run_vfs():
    """Ensures that the dry-run VFS is cleared before and after each test."""
    from ldm_core.utils import reset_dry_run_vfs

    reset_dry_run_vfs()
    yield
    reset_dry_run_vfs()


@pytest.fixture(autouse=True)
def reset_singletons():
    """Resets UI and Benchmarker class singletons to prevent state pollution."""
    from ldm_core.ui import UI
    from ldm_core.utils import Benchmarker

    UI.reset()
    Benchmarker.reset()
    yield
    UI.reset()
    Benchmarker.reset()
