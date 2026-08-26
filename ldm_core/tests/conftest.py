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


@pytest.fixture(autouse=True)
def isolate_ldm_home(tmp_path_factory, monkeypatch):
    """Points LDM_HOME at a temp directory for every test (#1342).

    Without this the suite wrote to the developer's real ``~/.ldm``:

    - project reconciliation registered pytest tempdirs as real projects, so
      ``ldm list`` grew entries like ``tmpb8i0z_zm`` after every run;
    - ``last-command.log`` was overwritten, destroying the trace of whatever
      the developer last ran -- the exact artefact needed to diagnose it.

    ``LDM_HOME`` is the only lever available: ``get_actual_home()`` rebuilds
    ``/Users/<user>`` from ``SUDO_USER``/``USER`` on macOS and ignores ``HOME``
    entirely (see #1349).

    Tests that patch ``get_actual_home`` themselves are unaffected, since a
    patch replaces the function and it never consults the environment.

    This does NOT isolate Docker: ``DockerService`` reaches the real daemon
    regardless of the filesystem, which has to be patched at the call site.
    """
    monkeypatch.setenv("LDM_HOME", str(tmp_path_factory.mktemp("ldm-home")))
