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
    assert "pip install liferay-docker-manager==9.9.9" in text


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
