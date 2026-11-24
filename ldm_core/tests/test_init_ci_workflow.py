"""Tests for the workflow ``ldm system init-ci`` scaffolds (LDM-#2447).

The render is checked four ways: as YAML (structure and step order), against
the real CLI parser (every ``ldm`` command it emits must parse), against
``ldm import`` on a directory (the undocumented entry point it used to emit),
and with ``actionlint`` (the workflow is valid GitHub Actions).
"""

import os
import re
import shlex
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from ldm_core.cli import _build_command_map, get_parser
from ldm_core.handlers.system import render_ci_workflow

SETUP = ["setup/rebuild.sh", "./scripts/seed.sh --site 'Demo: One' # seed"]
VERIFY = ["setup/step7-verify.sh", "curl -fsS http://localhost:8080 >/dev/null"]

LDM_COMMANDS = ("link", "wait", "package", "down")

RENDER_CASES: dict[str, dict[str, Any]] = {
    "bare": {},
    "project": {"project_id": "demo"},
    "hooks": {"setup_steps": SETUP, "verify_steps": VERIFY},
    "project+hooks": {
        "project_id": "demo",
        "setup_steps": SETUP,
        "verify_steps": VERIFY,
    },
    # LDM-#2493
    "stateless": {"stateless": True},
}


def _render(**kwargs):
    return render_ci_workflow("acme/demo", version="9.9.9", **kwargs)


def _steps(text):
    doc = yaml.safe_load(text)
    return doc, doc["jobs"]["build-and-package"]["steps"]


def _ldm_commands(text):
    """Every command line in a ``run:`` block that invokes ldm."""
    _, steps = _steps(text)
    lines = []
    for step in steps:
        for line in step.get("run", "").splitlines():
            if line.strip().startswith("ldm "):
                lines.append(line.strip())
    return lines


def _command_for(text, sub):
    matches = [c for c in _ldm_commands(text) if shlex.split(c)[1] == sub]
    assert len(matches) == 1, f"expected one 'ldm {sub}', got {matches}"
    return matches[0]


# --- 1. Structure ---------------------------------------------------------


def test_step_order_with_hooks():
    _, steps = _steps(_render(setup_steps=SETUP, verify_steps=VERIFY))
    names = [s["name"] for s in steps]
    runs = [s.get("run", "").strip() for s in steps]

    start = names.index("Start Liferay Environment")
    wait = names.index("Wait for Liferay & Deployments")
    package = names.index("Create LDM Package")
    down = names.index("Stop Liferay Environment")

    hooks = steps[wait + 1 : package]
    assert [h["name"] for h in hooks] == [
        f"Setup 1/2: {SETUP[0]}",
        f"Setup 2/2: {SETUP[1]}",
        f"Verify 1/2: {VERIFY[0]}",
        f"Verify 2/2: {VERIFY[1]}",
    ]
    # Each hook runs its command verbatim, one step per command.
    assert [h["run"] for h in hooks] == [f"{c}\n" for c in SETUP + VERIFY]
    assert start < wait < package < down
    assert runs[start].startswith("ldm link ")


def test_no_hook_steps_when_none_given():
    _, steps = _steps(_render())
    names = [s["name"] for s in steps]
    wait = names.index("Wait for Liferay & Deployments")
    assert names[wait + 1] == "Create LDM Package"
    assert not [n for n in names if n.startswith(("Setup ", "Verify "))]


def test_multiline_hook_is_reindented_verbatim():
    command = 'set -e\nfor f in setup/[0-9]*.sh; do\n  "$f"\ndone'
    _, steps = _steps(_render(setup_steps=[command]))
    hook = next(s for s in steps if s["name"].startswith("Setup 1/1"))
    assert hook["run"] == command + "\n"


def test_empty_hook_command_is_refused():
    with pytest.raises(ValueError, match="--verify-step"):
        _render(verify_steps=["   "])


def test_teardown_always_runs_and_uploads_do_not():
    _, steps = _steps(_render(setup_steps=SETUP, verify_steps=VERIFY))
    by_name = {s["name"]: s for s in steps}
    assert by_name["Stop Liferay Environment"]["if"] == "always()"
    # Packaging and upload stay success-only, so a failed verify ships nothing.
    assert "if" not in by_name["Create LDM Package"]
    assert "if" not in by_name["Upload Package as Action Artifact"]
    for step in steps:
        if step["name"].startswith(("Setup ", "Verify ")):
            assert "if" not in step


def test_job_runs_bash():
    doc, _ = _steps(_render())
    job = doc["jobs"]["build-and-package"]
    assert job["defaults"]["run"]["shell"] == "bash"
    # PyYAML loads the bare `on:` key as boolean True.
    assert "release" in doc[True]


@pytest.mark.parametrize("case", sorted(RENDER_CASES))
def test_project_reaches_every_ldm_command(case):
    kwargs = RENDER_CASES[case]
    text = _render(**kwargs)
    project = kwargs.get("project_id")
    for sub in LDM_COMMANDS:
        tokens = shlex.split(_command_for(text, sub))
        positional = [t for t in tokens[2:] if not t.startswith("-")]
        if sub == "link":
            # `ldm link <source> [project]`
            assert positional[0] == "."
            positional = positional[1:]
        if sub == "package":
            # Option values (`--repo X`, `--output Y`) are not positionals.
            positional = [p for p in positional if p not in ("acme/demo", "./dist")]
        expected = [project] if project else []
        assert positional == expected, f"ldm {sub}: {tokens}"


def test_header_names_version_and_upgrade_path():
    text = _render()
    header = text.split("name: LDM Package Release")[0]
    assert "9.9.9" in header
    assert "init-ci" in header
    assert "--setup-step" in header
    assert "--verify-step" in header


# --- 2. Never `ldm import <dir>` -------------------------------------------


@pytest.mark.parametrize("case", sorted(RENDER_CASES))
def test_never_calls_ldm_import(case):
    text = _render(**RENDER_CASES[case])
    _, steps = _steps(text)
    for step in steps:
        for line in step.get("run", "").splitlines():
            assert not re.match(r"^\s*ldm\s+import\b", line), line


# --- 3. Every emitted ldm command parses with the real parser ---------------


@pytest.mark.parametrize("case", sorted(RENDER_CASES))
def test_ldm_commands_parse_with_real_parser(case):
    parser, _ = get_parser()
    text = _render(**RENDER_CASES[case])
    commands = _ldm_commands(text)
    assert sorted(shlex.split(c)[1] for c in commands) == sorted(LDM_COMMANDS)

    for command in commands:
        argv = shlex.split(command)[1:]
        try:
            args = parser.parse_args(argv)
        except SystemExit:
            pytest.fail(f"generated command does not parse: {command}")
        assert args.command == argv[0], command
        assert args.non_interactive is True, command

        if argv[0] == "link":
            assert args.source == "."
            assert args.no_monitor is True
            assert args.build is True
        if argv[0] == "wait":
            assert args.wait_for_deployables is True
        assert args.project == RENDER_CASES[case].get("project_id"), command


def test_init_ci_parser_collects_hooks_in_order():
    parser, _ = get_parser()
    args = parser.parse_args(
        [
            "system",
            "init-ci",
            "--setup-step",
            "a.sh",
            "--verify-step",
            "v.sh",
            "--setup-step",
            "b.sh",
        ]
    )
    assert args.setup_steps == ["a.sh", "b.sh"]
    assert args.verify_steps == ["v.sh"]


def test_dispatch_passes_hooks_to_cmd_init_ci():
    parser, _ = get_parser()
    args = parser.parse_args(
        ["system", "init-ci", "demo", "--setup-step", "a.sh", "--verify-step", "v.sh"]
    )
    manager = MagicMock()
    _build_command_map(args, manager)[("system", "init-ci")]()
    kwargs = manager.system.cmd_init_ci.call_args.kwargs
    assert kwargs["project_id"] == "demo"
    assert kwargs["setup_steps"] == ["a.sh"]
    assert kwargs["verify_steps"] == ["v.sh"]


@pytest.mark.parametrize("case", sorted(RENDER_CASES))
def test_only_a_stateless_scaffold_packages_stateless(case):
    """LDM-#2493: `--stateless` reaches the emitted `ldm package`, and parses."""
    command = _command_for(_render(**RENDER_CASES[case]), "package")
    args = get_parser()[0].parse_args(shlex.split(command)[1:])
    assert args.stateless is bool(RENDER_CASES[case].get("stateless")), command


def test_dispatch_passes_stateless_to_init_ci_and_package():
    parser, _ = get_parser()
    manager = MagicMock()
    args = parser.parse_args(["system", "init-ci", "--stateless"])
    _build_command_map(args, manager)[("system", "init-ci")]()
    assert manager.system.cmd_init_ci.call_args.kwargs["stateless"] is True

    args = parser.parse_args(["package", "demo", "--stateless"])
    _build_command_map(args, manager)[("package", None)]()
    assert manager.snapshot.cmd_package.call_args.kwargs["stateless"] is True

    args = parser.parse_args(["package", "demo"])
    _build_command_map(args, manager)[("package", None)]()
    assert manager.snapshot.cmd_package.call_args.kwargs["stateless"] is False


# --- 4. actionlint ---------------------------------------------------------


def _actionlint():
    local = Path(sys.executable).parent / "actionlint"
    if local.exists():
        return str(local)
    return shutil.which("actionlint")


@pytest.mark.parametrize("case", sorted(RENDER_CASES))
def test_render_passes_actionlint(case, tmp_path):
    binary = _actionlint()
    if not binary:
        if os.environ.get("CI"):
            pytest.fail("actionlint is required in CI but was not found")
        pytest.skip("actionlint not installed")

    workflow = tmp_path / ".github" / "workflows" / "x.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(_render(**RENDER_CASES[case]), encoding="utf-8")

    result = subprocess.run(  # nosec B603
        [binary, "-no-color", "-shellcheck=", "-pyflakes=", str(workflow)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- 5. Installing LDM ------------------------------------------------------
#
# The template ran `pip install liferay-docker-manager=={version}`, and PyPI
# has no such package, so every scaffolded workflow failed at its first LDM
# step. It now installs the release binary. The step is EXECUTED here with
# `curl` stubbed, because a grep for the URL passes against a script that
# cannot run.

_CURL_STUB = """#!/usr/bin/env bash
# Records the URL and serves the matching fixture for `curl -fsSL -o OUT URL`.
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
echo "$url" >> "$FIXTURES/curl.log"
cp "$FIXTURES/$(basename "$url")" "$out"
"""


def _install_step(text):
    _, steps = _steps(text)
    return next(s for s in steps if s["name"] == "Install LDM")


def _run_install(tmp_path, tamper=False):
    import hashlib

    if not shutil.which("sha256sum"):
        pytest.skip("sha256sum not available")
    fixtures = tmp_path / "fixtures"
    stubs = tmp_path / "stubs"
    home = tmp_path / "home"
    for d in (fixtures, stubs, home):
        d.mkdir()
    # Installed as `ldm` with mode 0755 but never run: `ldm` is a watched
    # name (LDM-#1899), and the step deliberately does not execute it.
    binary = b"not a real binary\n"
    (fixtures / "ldm-linux").write_bytes(binary)
    digest = hashlib.sha256(b"tampered" if tamper else binary).hexdigest()
    (fixtures / "checksums.txt").write_text(
        f"{'0' * 64}  ldm-macos-arm64\n{digest}  ldm-linux\n"
    )
    curl = stubs / "curl"
    curl.write_text(_CURL_STUB)
    # lint: executable-stub -- bash must exec it to run the install step offline; "curl" is not a watched name
    curl.chmod(0o755)
    github_path = tmp_path / "github_path"
    github_path.touch()

    step = _install_step(_render())
    env = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "GITHUB_PATH": str(github_path),
        "FIXTURES": str(fixtures),
        **{k: str(v) for k, v in step.get("env", {}).items()},
    }
    result = subprocess.run(  # nosec B603 B607
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=False,
    )
    return result, home, github_path, fixtures


def test_ldm_is_not_installed_from_pypi():
    text = _render()
    assert "pip install liferay-docker-manager" not in text


def test_the_release_binary_is_installed_and_put_on_path(tmp_path):
    from ldm_core.constants import GITHUB_REPO_URL

    result, home, github_path, fixtures = _run_install(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    installed = home / ".local" / "bin" / "ldm"
    assert installed.is_file()
    assert os.access(installed, os.X_OK)
    assert github_path.read_text().split() == [str(home / ".local" / "bin")]
    assert installed.read_bytes() == (fixtures / "ldm-linux").read_bytes()
    base = f"{GITHUB_REPO_URL}/releases/download/v9.9.9"
    assert (fixtures / "curl.log").read_text().split() == [
        f"{base}/ldm-linux",
        f"{base}/checksums.txt",
    ]


def test_a_binary_that_fails_its_checksum_is_not_installed(tmp_path):
    result, home, github_path, _ = _run_install(tmp_path, tamper=True)
    assert result.returncode != 0
    assert not (home / ".local" / "bin" / "ldm").exists()
    assert github_path.read_text() == ""


def test_nothing_shipped_points_at_pypi():
    """LDM is not on PyPI. The `ldm upgrade` no-binary tip and the docs for
    the template also sent users there."""
    repo = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"pip install[^\n'\"]*liferay-docker-manager|from PyPI", re.I)
    sources = [
        p
        for p in (repo / "ldm_core").rglob("*.py")
        if "tests" not in p.relative_to(repo).parts
    ]
    sources += list((repo / "docs").rglob("*.md"))
    offenders = [
        f"{p.relative_to(repo)}:{n}"
        for p in sources
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []
