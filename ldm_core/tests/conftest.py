import os

import pytest


@pytest.fixture(autouse=True)
def isolate_ci_environment(monkeypatch):
    """Runs every test as if it were NOT on CI (LDM-#1468).

    `GITHUB_ACTIONS` changes real behaviour in five places, and every one is
    invisible to a test that does not think to look:

    - `handlers/composer.py` applies the **lean JVM profile** -- different heap,
      metaspace and compiler level.
    - `cli.py` **permits running as root**, bypassing the guard.
    - `manager.py` flags the run as CI.
    - `utils.py` **skips the OS keyring**, on both read and write.

    Each is deliberate. The consequence is not: a test that does not control the
    variable asserts one thing locally and something else on CI, passing in both
    while exercising different code.

    Caught three times in this repository. The byte-identical golden table in
    `test_tuning_cascade.py` compared `lean=False` rows against output that was
    lean -- a table whose entire purpose is detecting drift in that behaviour,
    and which could never have passed on CI. `test_tuning_provenance.py` then
    attributed every value to `profile (lean)`, collapsing the distinctions it
    exists to check.

    Before this fixture, six test files each solved it independently in five
    different ways: `patch.dict` to "false", clearing a set of CI variables,
    popping it from a subprocess environment, and two local helpers. That is a
    fixture wanting to exist.

    A test that needs CI behaviour opts in explicitly -- see
    `TestImplicitLeanOnCI` in `test_tuning_cascade.py`, which patches the
    variable back on and asserts the lean profile is applied. Same shape as
    `isolate_ldm_home` (#1342) and `block_real_docker` (#1409): the environment
    must not silently change what the suite tests.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


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
    That remainder is what ``block_real_docker`` below now enforces (#1409).
    """
    monkeypatch.setenv("LDM_HOME", str(tmp_path_factory.mktemp("ldm-home")))


# --- LDM-#1409: the Docker boundary -----------------------------------------

_DOCKER_BINARIES = frozenset(
    {"docker", "docker.exe", "docker-compose", "docker-compose.exe"}
)


def _docker_argv(cmd) -> str | None:
    """Returns the command text if ``cmd`` invokes the Docker CLI, else None.

    Matches on the **basename** of argv[0], not on a substring of the whole
    command. A substring test looks equivalent and is not: this repository's
    own checkout path contains ``liferay-docker-manager``, so
    ``python .../liferay_docker.py`` matches "docker" and is reported as a
    daemon call. Measured while writing this guard -- 4 of 335 recorded hits
    were that false positive.
    """
    if isinstance(cmd, (list, tuple)):
        parts = [str(c) for c in cmd]
    elif isinstance(cmd, str):
        import shlex

        try:
            parts = shlex.split(cmd)
        except ValueError:
            parts = cmd.split()
    else:
        return None

    if not parts:
        return None

    from pathlib import Path as _Path

    if _Path(parts[0]).name in _DOCKER_BINARIES:
        return " ".join(parts)
    return None


def _patch_in_every_importer(monkeypatch, defining_module, attr, replacement):
    """Replaces ``attr`` in the module that defines it *and* in every module
    that imported it by value.

    ``from ldm_core.utils import get_compose_cmd`` binds the function object
    into the importing module's namespace, so patching only
    ``ldm_core.utils.get_compose_cmd`` leaves ten other modules holding the
    original. Rebinding the name wherever it points at the same object is the
    only way to cover them without naming each one and having the list rot.
    """
    import importlib
    import sys

    original = getattr(importlib.import_module(defining_module), attr)
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            if getattr(module, attr, None) is original:
                monkeypatch.setattr(module, attr, replacement, raising=False)
        except Exception:
            # A module whose attributes cannot be read (lazy or C extension)
            # cannot be holding a Python function reference anyway.
            continue


@pytest.fixture(autouse=True)
def stub_docker_environment_probes(request, monkeypatch):
    """Answers "what does this machine have?" without asking it (#1409).

    Two helpers in ``ldm_core/utils.py`` shell out purely to discover the
    environment, and both are reached constantly and incidentally:

    - ``get_compose_cmd()`` runs ``docker compose version`` -- on **every
      call**, with no caching, from ten modules.
    - ``get_docker_socket_path()`` runs ``docker context inspect``, inside a
      bare ``except Exception`` that hides whatever it did.

    Neither carries test value: no assertion in this suite depends on which
    Compose binary the developer happens to have. They were simply the most
    frequent way the suite reached the daemon -- 22 of the 117 measured calls
    were ``docker compose version`` alone.

    A test that wants to exercise the discovery logic itself patches these
    back, or marks itself ``needs_docker``.
    """
    if request.node.get_closest_marker("needs_docker"):
        return

    _patch_in_every_importer(
        monkeypatch, "ldm_core.utils", "get_compose_cmd", lambda: ["docker", "compose"]
    )
    _patch_in_every_importer(
        monkeypatch,
        "ldm_core.utils",
        "get_docker_socket_path",
        lambda: "/var/run/docker.sock",
    )

    # `reclaim_volume_permissions()` mutates the filesystem via
    # `docker run --rm ... alpine chown/chmod`, and is reached from ~20 call
    # sites across snapshot/, pipelines/ and handlers/.
    #
    # It is gated behind `platform.system() == "linux"` (pipelines/run.py:1818),
    # so it is invisible to a macOS measurement and fires on every Linux CI run
    # -- three tests reached the daemon through it while this suite measured
    # zero. A platform-gated Docker call is the one offender a single-platform
    # baseline cannot see; see docs/TESTING.md.
    #
    # Every call site imports it *inside* the function, so the name is
    # re-resolved from `ldm_core.utils` at call time -- patching the importing
    # module would do nothing, and patching the source module reaches all of
    # them. True is the success path callers branch on.
    if not request.node.get_closest_marker("exercises_docker_helper"):
        _patch_in_every_importer(
            monkeypatch,
            "ldm_core.utils",
            "reclaim_volume_permissions",
            lambda *_a, **_k: True,
        )

    # DockerService is LDM's Docker facade: every static on it
    # (is_running/exists/get_status/inspect/stop/rm/start/logs/exec/...) funnels
    # through the module-scope `run_command` imported in docker_service.py.
    # That single name is how 26 of the 36 remaining offenders reached the
    # daemon, asking the developer's machine questions like
    # `docker ps -q -f name=^liferay-search-global$`.
    #
    # Stubbing it here rather than in 26 tests across 12 files is deliberate:
    # the boundary belongs at the facade, and a per-test patch is a thing to
    # forget -- #1365 fixed exactly one of these by hand and the other ten in
    # the same file survived another eight months.
    #
    # "" is the honest default: no container, not running, no such context.
    # A test that needs a different answer patches the DockerService method it
    # cares about, which replaces the method outright and is unaffected by this.
    def _no_docker(*_args, **_kwargs):
        return ""

    monkeypatch.setattr("ldm_core.docker_service.run_command", _no_docker, raising=True)


@pytest.fixture(autouse=True)
def block_real_docker(request, monkeypatch):
    """Fails any test that reaches the real Docker daemon (#1409).

    Measured before this existed: **117 Docker invocations from 74 tests** in a
    single run of ``ldm_core/tests``. Among them a unit test running
    ``docker exec`` inside the developer's own ``liferay-search-global``, four
    doing ``docker rm -f liferay-proxy-global``, and one creating a real
    ``wsl`` Docker context pointing at ``ssh://dev@192.168.1.10``.

    Two design points, both from measurement rather than from reading:

    - **The hook belongs at the ``subprocess`` boundary, not at
      ``CommandRunner``.** Of those 117 calls only 81 went through
      ``CommandRunner.run``; the other 36 come from the ~20 places in
      ``ldm_core/{snapshot,runtime,workspace}`` that call ``subprocess``
      directly. A guard on the wrapper alone would police two thirds of the
      problem and report success.
    - **It intercepts, it does not mock.** ``docs/TESTING.md`` warns that
      globally replacing stdlib machinery corrupts unrelated tests. Anything
      that is not the Docker CLI is passed straight through to the real
      function untouched; only a Docker argv is stopped.

    A test that genuinely needs the daemon marks itself::

        @pytest.mark.needs_docker

    and is then allowed through. Run without them via ``-m "not needs_docker"``.
    """
    if request.node.get_closest_marker("needs_docker"):
        return

    import subprocess

    real_run = subprocess.run
    real_popen = subprocess.Popen

    def _fail(cmd):
        pytest.fail(
            "This test reached the real Docker daemon (LDM-#1409).\n"
            f"    test:    {request.node.nodeid}\n"
            f"    command: {_docker_argv(cmd)}\n"
            "\n"
            "Tests must not touch the machine's containers, volumes or "
            "contexts. Patch at the call site -- note that DockerService's "
            "static methods call a module-scope run_command, so patching the "
            "manager's copy does not reach them (see test_infra.py, #1365).\n"
            "If the test genuinely requires a daemon, mark it "
            "@pytest.mark.needs_docker.",
            pytrace=False,
        )

    def guarded_run(cmd, *args, **kwargs):
        if _docker_argv(cmd):
            _fail(cmd)
        return real_run(cmd, *args, **kwargs)

    class GuardedPopen(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, cmd, *args, **kwargs):
            if _docker_argv(cmd):
                _fail(cmd)
            super().__init__(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
