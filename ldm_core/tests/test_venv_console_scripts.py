"""Regression coverage for LDM-#1244 and LDM-#1245.

Both issues share one root cause: project tooling invoked Python packages via
their generated `.venv/bin/<tool>` console scripts. Those wrappers are not
reliably present -- endpoint protection removes them by name after installation,
leaving the package itself fully functional. The evidence that this is deletion
rather than a packaging quirk is that each package's `dist-info/RECORD` still
lists the file, and the removal is name-selective across byte-identical
siblings: `pytest` removed while `py.test` survives, `pip` removed while `pip3`
and `pip3.14` survive, same SHA-256 and byte count in each pair.

The durable remedy is to invoke the module (`python -m <pkg>`), which does not
depend on the wrapper existing. These tests pin that in place.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import release

# Every console script observed removed from .venv/bin on an affected machine.
# No project tooling may invoke any of these by path.
DELETED_WRAPPERS = (
    "bandit",
    "detect-secrets",
    "detect-secrets-hook",
    "mypy",
    "pip",
    "pre-commit",
    "pysemgrep",
    "pytest",
    "semgrep",
)


def _flatten(call_args_list):
    """Renders every argv passed to a mock into one searchable string."""
    parts: list[str] = []
    for call in call_args_list:
        argv = call[0][0] if call[0] else []
        if isinstance(argv, (list, tuple)):
            parts.extend(str(a) for a in argv)
        else:
            parts.append(str(argv))
    return " ".join(parts)


class TestReleaseGateNeverSilentlyDowngrades(unittest.TestCase):
    """LDM-#1244: the release gate must not fall back to ./lint.sh."""

    def test_resolves_pre_commit_as_a_module_not_a_console_script(self):
        """The resolved command must be `python -m pre_commit`, never a wrapper path."""
        cmd = release.resolve_pre_commit_cmd()

        self.assertIsNotNone(
            cmd, "pre-commit should be resolvable in the project venv or on PATH"
        )
        self.assertIn(
            "-m",
            cmd,
            f"Expected module-form invocation, got {cmd}",
        )
        self.assertIn("pre_commit", cmd)
        # The specific probe that caused #1244.
        self.assertNotIn(
            str(Path(".venv") / "bin" / "pre-commit"),
            " ".join(cmd),
            "Must not resolve to the .venv/bin/pre-commit console script",
        )

    @patch("release.sys.exit", side_effect=SystemExit)
    @patch("release.run_cmd")
    @patch("release.resolve_pre_commit_cmd", return_value=None)
    def test_aborts_rather_than_falling_back_to_lint_sh(
        self, _mock_resolve, mock_run_cmd, _mock_exit
    ):
        """When pre-commit is unavailable the release must abort, not downgrade.

        Previously this fell through to `./lint.sh`, which does not run
        check-version-sync, gitleaks, mypy, check-cli-drift or validate-compose
        -- and which auto-fixes rather than validates, rewriting the working
        tree mid-release. Worst of all it printed a green "quality gate passed".
        """
        with self.assertRaises(SystemExit):
            release.run_pre_commit_checks("release/v9.9.9")

        issued = _flatten(mock_run_cmd.call_args_list)
        self.assertNotIn(
            "lint.sh",
            issued,
            f"Regression (#1244): silently downgraded to ./lint.sh -- {issued}",
        )

    @patch("release.run_cmd")
    @patch(
        "release.resolve_pre_commit_cmd",
        return_value=["/venv/bin/python3", "-m", "pre_commit"],
    )
    def test_runs_all_files_and_mirrors_the_agent_push_skip_list(
        self, _mock_resolve, mock_run_cmd
    ):
        """The gate runs --all-files, with the same SKIP list the push wrapper uses."""
        mock_run_cmd.return_value = MagicMock(returncode=0, stdout="", stderr="")

        release.run_pre_commit_checks("release/v9.9.9")

        gate_call = mock_run_cmd.call_args_list[0]
        self.assertEqual(
            ["/venv/bin/python3", "-m", "pre_commit", "run", "--all-files"],
            gate_call[0][0],
        )

        env = gate_call[1]["env"]
        self.assertIn("bump-docs-timestamps", env["SKIP"])

        # LDM-#1246: no security or lint scanner may be skipped at release time.
        # These were skipped for years on the false premise that their binaries
        # "may only be installed in CI"; they are repo:-based hooks that
        # provision their own environments and pass in seconds.
        for hook in ("semgrep", "detect-secrets", "actionlint", "gitleaks"):
            self.assertNotIn(
                hook,
                env["SKIP"],
                f"Regression (#1246): '{hook}' must not be skipped for a release",
            )

    def test_agent_push_and_release_skip_lists_agree(self):
        """The wrapper and the release script must not drift apart (#1246).

        `release.py` mirrors `agent_push.sh`; when the two disagree, one of the
        two paths silently runs a weaker gate than the other.
        """
        import re

        wrapper = (
            Path(__file__).resolve().parent.parent.parent / "scripts" / "agent_push.sh"
        ).read_text(encoding="utf-8")

        found = set(re.findall(r"SKIP=(\S+)", wrapper))
        self.assertTrue(found, "No SKIP= assignment found in agent_push.sh")
        self.assertEqual(
            {release.PRE_COMMIT_SKIP},
            found,
            "agent_push.sh and release.py PRE_COMMIT_SKIP have drifted apart",
        )


class TestDevSetupUsesModuleForm(unittest.TestCase):
    """LDM-#1245: `ldm dev-setup` must not invoke the .venv/bin/pip wrapper."""

    def setUp(self):
        from ldm_core.handlers.dev import DevService

        self.manager = MagicMock()
        self.manager.non_interactive = True
        self.manager.args.non_interactive = True
        self.service = DevService(manager=self.manager)

    @patch.dict(os.environ, {"LDM_DEV_MODE": "true"})
    @patch("ldm_core.utils.run_command")
    def test_installs_dependencies_via_python_m_pip(self, mock_run_command):
        """Dependency installs must go through `python -m pip`.

        The wrapper form made dev-setup die at its first step on any machine
        whose `.venv/bin/pip` had been removed -- i.e. precisely the machines
        whose environment needed repairing. Note the same function already
        installed the git hooks correctly via `python -m pre_commit`; only the
        pip calls used the wrapper.

        Uses a real temporary venv layout rather than mocking `Path`: building
        the path graph out of MagicMocks needs a `__str__` assignment that mypy
        rejects, which is what turned PR #1238 red.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Populate both layouts so the assertion holds on any platform.
            for subdir, exe in (("bin", "python3"), ("Scripts", "python.exe")):
                target = root / ".venv" / subdir
                target.mkdir(parents=True, exist_ok=True)
                (target / exe).touch()

            with (
                patch("ldm_core.handlers.dev.Path.cwd", return_value=root),
                patch.object(self.service, "_ensure_dev_env"),
            ):
                self.service.cmd_dev_setup()

        issued = _flatten(mock_run_command.call_args_list)

        self.assertIn(
            "-m pip",
            issued,
            f"Expected `python -m pip` dependency installs, got: {issued}",
        )
        self.assertNotIn(
            f"{os.sep}pip ",
            issued + " ",
            f"Regression (#1245): invoked the .venv/bin/pip wrapper -- {issued}",
        )


class TestNoToolingDependsOnDeletedWrappers(unittest.TestCase):
    """Contract: no shipped tooling may invoke a removable console script by path."""

    def test_no_source_file_builds_a_venv_console_script_path(self):
        """Guards the pattern that made both issues invisible to a literal grep.

        `venv_dir / "bin" / "pip"` constructs the same broken path as the string
        ".venv/bin/pip" but is invisible to a search for the latter -- which is
        exactly why #1245 survived an audit that only grepped literals.
        """
        import re

        repo_root = Path(__file__).resolve().parent.parent.parent
        pattern = re.compile(
            r'"(?:bin|Scripts)"\s*/\s*"(' + "|".join(DELETED_WRAPPERS) + r')(?:\.exe)?"'
        )

        offenders = []
        for path in list(repo_root.glob("*.py")) + list(
            (repo_root / "ldm_core").rglob("*.py")
        ):
            if "tests" in path.parts:
                continue
            for num, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(repo_root)}:{num}: {line.strip()}"
                    )

        for path in (repo_root / "scripts").glob("*.py"):
            for num, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(repo_root)}:{num}: {line.strip()}"
                    )

        self.assertEqual(
            [],
            offenders,
            "Quality Gate Violation: tooling must invoke Python packages as "
            "modules (`python -m <pkg>`), not via `.venv/bin/<tool>` console "
            "scripts, which are removed by endpoint protection. See #1244/#1245:\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
