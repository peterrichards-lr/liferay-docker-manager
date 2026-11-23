"""LDM-#2109: the Elasticsearch readiness window is overridable.

The automatic search volume repair -- and the LDM-#2083 guard in front of it --
is reached only when Elasticsearch misses its readiness window. That window was
a hardcoded `range(60)` of 5-second sleeps, so the E2E suite could reach the
guard only by breaking Elasticsearch and waiting five minutes, and a machine
fast enough to start it inside the window skipped the path silently.

`LDM_SEARCH_READY_TIMEOUT` lets the suite reach it at once. These tests drive
`setup_global_search` itself, not only the resolver, so they cross the seam: a
resolver nobody calls would leave every one of them red.
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from ldm_core.handlers.infra import InfraService
from ldm_core.tests.test_infra_silent_failures import _Manager, _no_real_docker
from ldm_core.tests.test_verify_scripts import (
    BASH_SCRIPT,
    PS1_SCRIPT,
    _extract_function,
    _pwsh_binary,
)

_ENV = "LDM_SEARCH_READY_TIMEOUT"


class TestTheSearchReadinessWindow(unittest.TestCase):
    def _run(self, env_value):
        """Run setup_global_search against an Elasticsearch that never answers.

        Returns (number of readiness probes, sleep durations, warnings). The
        repair is stopped by the LDM-#2083 refusal -- a project is "running" --
        so nothing is wiped and the run ends at the first not-ready verdict.
        """
        calls = []

        def responder(cmd, **_kwargs):
            calls.append(list(cmd))
            return ""

        manager = _Manager(responder=responder)
        infra = InfraService(manager)
        env = {k: v for k, v in os.environ.items() if k != _ENV}
        if env_value is not None:
            env[_ENV] = env_value

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, env, clear=True),
            _no_real_docker(),
            patch("ldm_core.docker_service.DockerService.exists", return_value=False),
            patch(
                "ldm_core.docker_service.DockerService.is_running", return_value=False
            ),
            patch("ldm_core.handlers.infra.get_actual_home", return_value=Path(tmp)),
            patch("ldm_core.utils.reclaim_volume_permissions"),
            patch.object(
                InfraService,
                "scan_running_projects",
                MagicMock(return_value=[("devcon2026", "local")]),
            ),
            patch("shutil.rmtree"),
            patch("time.sleep") as sleep,
            patch("ldm_core.handlers.infra.UI.warning") as warning,
            self.assertRaises(SystemExit) as ctx,
        ):
            infra.setup_global_search()

        # The refusal, not some earlier failure, is what ended the run.
        self.assertEqual(ctx.exception.code, 3)
        probes = [c for c in calls if "exec" in c and "localhost:9200" in c]
        sleeps = [c.args[0] for c in sleep.call_args_list]
        warnings = [str(c.args[0]) for c in warning.call_args_list]
        return len(probes), sleeps, warnings

    def test_the_default_is_still_sixty_probes_five_seconds_apart(self):
        probes, sleeps, _ = self._run(None)
        self.assertEqual(probes, 60)
        self.assertEqual(sleeps, [5] * 60)

    def test_zero_reaches_the_repair_without_probing(self):
        """What the E2E uses for the refusal: deterministic, no wait at all."""
        probes, sleeps, _ = self._run("0")
        self.assertEqual(probes, 0)
        self.assertEqual(sleeps, [])

    def test_the_override_is_in_seconds_rounded_up_to_whole_probes(self):
        probes, sleeps, _ = self._run("12")
        self.assertEqual(probes, 3)
        self.assertEqual(sleeps, [5, 5, 5])

    def test_a_malformed_value_warns_and_keeps_the_default(self):
        for bad in ("soon", "-5", "nan"):
            with self.subTest(value=bad):
                probes, _, warnings = self._run(bad)
                self.assertEqual(probes, 60)
                self.assertTrue(
                    any(f"Malformed {_ENV}" in w for w in warnings), warnings
                )


# --- The E2E halves (scripts/verify_e2e_refactor.{sh,ps1}) -------------------
#
# Driven against stubs, because a name-grep on a script function passes against
# a call site that cannot run. The stub `ldm` mimics the real messages, which
# are taken from handlers/infra.py -- TestTheScriptsAssertRealMessages below
# keeps the two from drifting. The stub `docker` is FIRST on PATH so nothing
# here can reach a real daemon.

INFRA = Path(__file__).resolve().parent.parent / "handlers" / "infra.py"

REFUSAL = "Refusing to wipe the shared search data while other LDM projects are running"
FORCE = "--force given: deleting the SHARED search data at"
SAVED = "Saved the failing container's logs to"
REPAIR = "Attempting automatic search volume repair"

_STUB_LDM = f"""#!/bin/sh
echo "$LDM_SEARCH_READY_TIMEOUT|$*" >> "$STUB_LOG"
case "$LDM_HOME" in *searchwipe-home) ;; *) exit "${{RESTORE_RC:-0}}" ;; esac
sd="$LDM_HOME/.ldm/infra/search"
mode="${{STUB_MODE:-good}}"
force=no
for a in "$@"; do [ "$a" = "--force" ] && force=yes; done
if [ "$force" = no ]; then
  [ "$mode" = wipe_on_refusal ] && rm -rf "$sd/data"
  echo "[X] {REFUSAL} (1)."
  echo "Tip: Stop them first, then re-run:"
  echo "      ldm stop ldmsearchwipe"
  [ "$mode" = refusal_exit1 ] && exit 1
  exit 3
fi
if [ "$mode" = good ] || [ "$mode" = keeps_data ]; then
  echo boot-log > "$sd/search-failure-1.log"
  echo "[i] {SAVED} $sd/search-failure-1.log"
fi
echo "[!] {REPAIR}..."
if [ "$mode" = capture_after ]; then
  echo boot-log > "$sd/search-failure-1.log"
  echo "[i] {SAVED} $sd/search-failure-1.log"
fi
echo "[!] {FORCE} $sd/data. 1 running project(s) will lose their index and must reindex."
[ "$mode" = keeps_data ] || rm -rf "$sd/data"
exit 3
"""


def _stubs(directory):
    bin_dir = directory / "bin"
    bin_dir.mkdir()
    ldm = bin_dir / "ldm-stub"
    ldm.write_text(_STUB_LDM)
    # lint: executable-stub -- the verify function must exec it; named ldm-stub, which no agent watches (LDM-#1899)
    ldm.chmod(0o755)
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\necho "$*" >> "$DOCKER_LOG"\nexit 0\n')
    # lint: executable-stub -- shadows the real docker so nothing reaches a daemon
    docker.chmod(0o755)
    return bin_dir, ldm


def _env(directory, bin_dir, mode, restore_rc=0):
    env = {k: v for k, v in os.environ.items() if k not in ("LDM_HOME", _ENV)}
    env.update(
        PATH=f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        STUB_MODE=mode,
        STUB_LOG=str(directory / "ldm.log"),
        DOCKER_LOG=str(directory / "docker.log"),
        RESTORE_RC=str(restore_rc),
    )
    return env


# A mixin, so the shared cases are not collected on their own; typed as a
# TestCase for mypy only.
if TYPE_CHECKING:
    _Base = unittest.TestCase
else:
    _Base = object


@unittest.skipIf(os.name == "nt", "POSIX stubs")
class _GuardHarness(_Base):
    """Shared cases; each subclass runs one script half."""

    def _run(self, work, ldm, env):  # pragma: no cover - overridden
        raise NotImplementedError

    def _drive(self, mode, restore_rc=0):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            work = tmp_path / "work"
            work.mkdir()
            bin_dir, ldm = _stubs(tmp_path)
            ok, out = self._run(work, ldm, _env(tmp_path, bin_dir, mode, restore_rc))
            calls = (tmp_path / "ldm.log").read_text().splitlines()
            docker = (tmp_path / "docker.log").read_text()
            leftovers = sorted(p.name for p in work.iterdir())
        return ok, out, calls, docker, leftovers

    def test_correct_behaviour_passes_and_cleans_up(self):
        ok, out, calls, docker, leftovers = self._drive("good")
        self.assertTrue(ok, out)
        # The window each run used, then the restore on the real home.
        self.assertEqual([c.split("|")[0] for c in calls], ["0", "5", ""], calls)
        self.assertIn("--force", calls[1])
        self.assertIn("rm -f liferay-search-global", docker)
        self.assertIn("run -d --name ldmsearchwipe alpine", docker)
        self.assertEqual(leftovers, [])

    def test_a_refusal_with_the_wrong_exit_code_fails(self):
        ok, out, *_ = self._drive("refusal_exit1")
        self.assertFalse(ok, out)

    def test_a_refusal_that_wipes_anyway_fails(self):
        ok, out, *_ = self._drive("wipe_on_refusal")
        self.assertFalse(ok, out)

    def test_force_that_leaves_the_data_fails(self):
        ok, out, *_ = self._drive("keeps_data")
        self.assertFalse(ok, out)

    def test_no_capture_fails(self):
        ok, out, *_ = self._drive("no_capture")
        self.assertFalse(ok, out)

    def test_a_capture_after_the_repair_fails(self):
        ok, out, *_ = self._drive("capture_after")
        self.assertFalse(ok, out)

    def test_a_failed_restore_fails(self):
        ok, out, *_ = self._drive("good", restore_rc=1)
        self.assertFalse(ok, out)


class TestBashSearchWipeGuard(_GuardHarness, unittest.TestCase):
    def _run(self, work, ldm, env):
        helper = _extract_function(
            BASH_SCRIPT,
            re.compile(r"^remove_workspace_dir\(\)\s*\{.*?^\}", re.M | re.S),
        )
        func = _extract_function(
            BASH_SCRIPT,
            re.compile(r"^verify_search_wipe_guard\(\)\s*\{.*?^\}", re.M | re.S),
        )
        script = f"{helper}\n{func}\nverify_search_wipe_guard '{ldm}' '{work}'\n"
        res = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, env=env
        )
        return res.returncode == 0, res.stdout + res.stderr


@unittest.skipUnless(_pwsh_binary(), "PowerShell not installed")
class TestPowerShellSearchWipeGuard(_GuardHarness, unittest.TestCase):
    def _run(self, work, ldm, env):
        func = _extract_function(
            PS1_SCRIPT,
            re.compile(r"^function Test-SearchWipeGuard\s*\{.*?^\}", re.M | re.S),
        )
        script = (
            f"{func}\n"
            f"$r = Test-SearchWipeGuard -LdmCmd '{ldm}' -WorkDir '{work}'\n"
            "Write-Output ('Ok=' + $r.Ok)\n"
            "Write-Output ('Message=' + $r.Message)\n"
        )
        res = subprocess.run(
            [_pwsh_binary(), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        return "Ok=True" in res.stdout, res.stdout + res.stderr


class TestTheScriptsAssertRealMessages(unittest.TestCase):
    """The stub's messages, and both halves' assertions, are the real ones."""

    def test_every_asserted_message_is_printed_by_infra(self):
        source = INFRA.read_text(encoding="utf-8")
        # The refusal is split over two f-string literals in the source.
        joined = re.sub(r'"\s*\n\s*f?"', "", source)
        for text in (REFUSAL, FORCE, SAVED, REPAIR):
            with self.subTest(text=text):
                self.assertIn(text, joined)

    def test_both_halves_assert_the_same_messages(self):
        for script in (BASH_SCRIPT, PS1_SCRIPT):
            body = script.read_text(encoding="utf-8")
            for text in (REFUSAL, FORCE, SAVED, REPAIR, "ldm stop "):
                with self.subTest(script=script.name, text=text):
                    self.assertIn(text, body)


if __name__ == "__main__":
    unittest.main()
