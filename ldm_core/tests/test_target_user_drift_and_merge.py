"""A target's SSH user drifts, and `target add` used to erase what it kept (LDM-#1797).

Two defects with one surface. Both were found live while preparing the
`v2.23.0-pre.5` remote-node verification, on a node that `ldm target ls`
reported as correctly configured.

## 1. The SSH user is stored twice

`~/.ldmrc` holds `user`; the Docker context holds `ssh://<user>@<host>`.
`cmd_target_add` writes both, so they agree *at the moment it runs*, and
nothing afterwards keeps them in step or notices divergence:

```
$ docker context inspect aws-1 --format '{{.Endpoints.docker.Host}}'
ssh://ldm-automation@13.49.210.78

$ ldm target ls    # ~/.ldmrc
aws-1  host=13.49.210.78  user=ec2-user

$ docker --context aws-1 version
ldm-automation@13.49.210.78: Permission denied (publickey,...).
```

`ldm doctor` already compares configured state against reality for the MAC pin
(LDM-#1789). This is the same shape, so it lives beside it.

## 2. `target add` replaced the node wholesale

`save_target_node` does `raw_targets[target.name] = target.to_dict()`, and the
handler built a fresh `TargetNode` from only the flags it was given. So the
obvious repair for defect 1 --

    ldm target add aws-1 --host 13.49.210.78 --user ec2-user

-- reported "registered successfully" and silently destroyed the node's MAC
pin, after which Liferay refuses the licence and the operator gets the
LDM-#1752 symptom. LDM-#1789 exists specifically to *detect* a dropped pin;
this dropped one using the documented command during routine maintenance.

`add` now merges. An omitted flag keeps the stored value; a flag passed with an
empty value clears it.

Every test here drives the real code. The `target add` ones go through the real
argparse parser and the real dispatch table, because the sentinel that makes
merging possible (`None` == "not given") lives in both, and a test that called
the handler directly would pass against a dispatch that coerced it away -- that
is exactly how LDM-#1759 shipped.
"""

import argparse
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetNode
from ldm_core.docker_service import DockerService
from ldm_core.ui import UI

CONFIGURED = "ec2-user"
DIALLED = "ldm-automation"
PIN = "06:ff:c5:f9:cf:a5"


class TheContextUserIsReadBack(unittest.TestCase):
    """Half of the drift lives in Docker, so it has to be asked for."""

    def _endpoint(self, value):
        return patch("ldm_core.docker_service.run_command", return_value=value)

    def test_it_returns_the_user_an_ssh_endpoint_dials(self):
        with self._endpoint("ssh://ldm-automation@13.49.210.78"):
            self.assertEqual(DIALLED, DockerService.get_context_endpoint_user("aws-1"))

    def test_a_port_does_not_confuse_it(self):
        with self._endpoint("ssh://ec2-user@13.49.210.78:2222"):
            self.assertEqual(
                CONFIGURED, DockerService.get_context_endpoint_user("aws-1")
            )

    def test_an_ipv6_literal_does_not_confuse_it(self):
        """The address is full of colons but contains no '@'."""
        with self._endpoint("ssh://ec2-user@[2001:db8::1]:2222"):
            self.assertEqual(
                CONFIGURED, DockerService.get_context_endpoint_user("aws-1")
            )

    def test_an_ssh_endpoint_with_no_user_is_empty_not_unknown(self):
        """SSH then falls back to the *local* username, which is itself a
        divergence from a configured user and must stay reportable."""
        with self._endpoint("ssh://13.49.210.78"):
            self.assertEqual("", DockerService.get_context_endpoint_user("aws-1"))

    def test_a_non_ssh_endpoint_dials_no_user(self):
        with self._endpoint("unix:///var/run/docker.sock"):
            self.assertIsNone(DockerService.get_context_endpoint_user("aws-1"))

    def test_an_unreadable_context_is_none(self):
        for value in ("", "   ", None):
            with self._endpoint(value):
                self.assertIsNone(DockerService.get_context_endpoint_user("aws-1"))

    def test_the_host_half_still_works(self):
        """Both halves now parse one inspect result; the older reader (LDM-#1346)
        must be unaffected by that."""
        with self._endpoint("ssh://ldm-automation@13.49.210.78"):
            self.assertEqual(
                "13.49.210.78", DockerService.get_context_endpoint_host("aws-1")
            )


class DoctorHarness(unittest.TestCase):
    """Drives the real `_check_target_ssh_users`."""

    def check(self, *, host="13.49.210.78", configured=CONFIGURED, dialled=DIALLED):
        from ldm_core.diagnostics.doctor import DoctorRunner

        doctor = DoctorRunner.__new__(DoctorRunner)
        doctor.results = []
        hints: list[str] = []

        def record_hint(text, *_args, **_kwargs):
            hints.append(str(text))

        doctor.add_hint = record_hint  # type: ignore[method-assign]

        targets = {"aws-1": TargetNode(name="aws-1", host=host, user=configured)}

        with (
            patch("ldm_core.config.load_targets", return_value=targets),
            patch(
                "ldm_core.docker_service.DockerService.get_context_endpoint_user",
                return_value=dialled,
            ),
        ):
            doctor._check_target_ssh_users()

        return doctor.results, "\n".join(hints)


class DriftIsDetectedAndNamed(DoctorHarness):
    def test_a_disagreeing_context_is_flagged(self):
        results, _ = self.check()

        self.assertTrue(results, "doctor said nothing about the drift")
        self.assertEqual("warn", results[0][2])
        self.assertIn("aws-1", results[0][0])
        self.assertIn(DIALLED, results[0][1])

    def test_the_hint_names_both_users_and_the_repair(self):
        """ "SSH user drift" is not actionable. Both values and the fix are."""
        _, hint = self.check()

        self.assertIn(CONFIGURED, hint)
        self.assertIn(DIALLED, hint)
        self.assertIn("ldm target add aws-1 --user ec2-user", strip(hint))

    def test_it_explains_why_target_ls_looked_correct(self):
        """The whole difficulty of this failure is that the configuration reads
        as correct while every command goes somewhere else."""
        _, hint = self.check()

        self.assertIn("ldm target ls", hint)
        self.assertIn("Permission denied", hint)

    def test_a_context_with_no_user_at_all_is_drift_too(self):
        """SSH then uses the local username -- not the configured one."""
        results, hint = self.check(dialled="")

        self.assertEqual("warn", results[0][2])
        self.assertIn("local username", results[0][1])
        self.assertIn("local username", hint)


class AgreementIsSilent(DoctorHarness):
    def test_matching_users_raise_nothing(self):
        results, hint = self.check(dialled=CONFIGURED)

        self.assertEqual("", hint, "doctor warned about a node that agrees")
        self.assertTrue(all(r[2] is True for r in results), results)

    def test_a_local_target_is_not_checked(self):
        for host in ("localhost", "127.0.0.1", ""):
            self.assertEqual(
                ([], ""), self.check(host=host), f"a {host!r} node has no SSH user"
            )

    def test_a_node_with_no_configured_user_is_not_checked(self):
        """Nothing was configured, so there is nothing to disagree with."""
        self.assertEqual(([], ""), self.check(configured=""))

    def test_an_unreadable_context_is_not_drift(self):
        """A node may be registered before its context exists. Reporting drift
        against a configuration that is merely absent is a false alarm."""
        self.assertEqual(([], ""), self.check(dialled=None))


class ItIsWiredIntoDoctor(unittest.TestCase):
    """A check nobody calls is the LDM-#1774 shape -- and the LDM-#1789 review
    found exactly that in new code, because every test called the check
    directly. This one drives `run()`."""

    def test_doctor_run_invokes_the_ssh_user_check(self):
        from ldm_core.diagnostics.doctor import DoctorRunner

        doctor = DoctorRunner.__new__(DoctorRunner)
        doctor.results = []
        doctor.hints = []
        doctor.handler = MagicMock()
        doctor.project_paths = []
        doctor.all_projects = False
        doctor.project_id = None
        # A MagicMock's attributes are all truthy, so `--slug` would look set
        # and `run()` would return before reaching any check at all.
        doctor.args = argparse.Namespace(
            slug=False, ssl=False, skip_project=True, all=False
        )

        called: list[bool] = []

        def record(*_args, **_kwargs):
            called.append(True)

        with (
            patch(
                "ldm_core.diagnostics.doctor._get_env_info",
                return_value=("x86", "linux", "docker", "virtiofs"),
            ),
            patch.object(DoctorRunner, "_check_tooling_and_integrity"),
            patch.object(DoctorRunner, "_check_docker_runtime"),
            patch.object(DoctorRunner, "_check_global_config_and_network"),
            patch.object(DoctorRunner, "_check_project_specific"),
            patch.object(DoctorRunner, "_check_dangling_and_print"),
            patch.object(DoctorRunner, "_check_target_ssh_users", side_effect=record),
            patch("ldm_core.ui.UI.heading"),
        ):
            doctor.run()

        self.assertTrue(called, "doctor completed without checking any SSH user")


def strip(text: str) -> str:
    from ldm_core.utils import strip_ansi

    return strip_ansi(text)


class TargetAddHarness(unittest.TestCase):
    """Drives the real parser and the real dispatch table.

    `LDM_HOME` redirects `~/.ldmrc` to a temp directory (LDM-#1349), and
    `handlers.config.run_command` is patched so no Docker context on the
    machine is created, removed or inspected (LDM-#1409).
    """

    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)

        env = patch.dict(os.environ, {"LDM_HOME": self.home.name})
        env.start()
        self.addCleanup(env.stop)

        run = patch("ldm_core.handlers.config.run_command", return_value="")
        self.run_command = run.start()
        self.addCleanup(run.stop)

        # The MAC-interface probe is a live SSH round trip to the node.
        probe = patch(
            "ldm_core.handlers.config.ConfigService._warn_if_mac_matches_no_interface"
        )
        probe.start()
        self.addCleanup(probe.stop)

    def target_add(self, *argv: str) -> str:
        """Runs `ldm target add <argv>` through parser + dispatch, returns output."""
        from ldm_core.cli import _build_command_map, get_parser
        from ldm_core.handlers.config import ConfigService

        parser, _subparsers = get_parser()
        args = parser.parse_args(["target", "add", *argv, "-y"])

        service = ConfigService.__new__(ConfigService)
        service.manager = MagicMock()
        manager = MagicMock()
        manager.config = service

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            _build_command_map(args, manager)[("target", "add")]()
        return strip(buffer.getvalue())

    def stored(self, name: str) -> TargetNode:
        from ldm_core.config import load_targets

        node = load_targets().get(name)
        if node is None:
            self.fail(f"target {name!r} was not stored")
        return node

    def ldmrc(self) -> Path:
        return Path(self.home.name) / ".ldmrc"


class AnOmittedFlagKeepsTheStoredValue(TargetAddHarness):
    def test_re_adding_without_mac_address_keeps_the_pin(self):
        """The defect, exactly: the documented repair for a wrong SSH user.

        Under the old replace-everything behaviour this reported success and
        left the node unpinned, and the next `ldm run` gave the LDM-#1752
        symptom -- healthy container, `License registered`, Activation page.
        """
        self.target_add(
            "aws-1", "--host", "13.49.210.78", "--user", DIALLED, "--mac-address", PIN
        )

        self.target_add("aws-1", "--host", "13.49.210.78", "--user", CONFIGURED)

        node = self.stored("aws-1")
        self.assertEqual(PIN, node.mac_address, "target add destroyed the MAC pin")
        self.assertEqual(CONFIGURED, node.user)

    def test_the_pin_survives_in_the_file_on_disk(self):
        """`load_targets` is not the only reader of `~/.ldmrc`."""
        self.target_add("aws-1", "--host", "13.49.210.78", "--mac-address", PIN)
        self.target_add("aws-1", "--user", CONFIGURED)

        self.assertIn(PIN, self.ldmrc().read_text(encoding="utf-8"))

    def test_omitting_the_host_keeps_it(self):
        """The repair for a drifted user need not restate the address."""
        self.target_add("aws-1", "--host", "13.49.210.78", "--user", DIALLED)
        self.target_add("aws-1", "--user", CONFIGURED)

        self.assertEqual("13.49.210.78", self.stored("aws-1").host)

    def test_omitting_the_key_keeps_it(self):
        self.target_add("aws-1", "--host", "13.49.210.78", "--key", "~/.ssh/aws.pem")
        self.target_add("aws-1", "--user", CONFIGURED)

        self.assertEqual("~/.ssh/aws.pem", self.stored("aws-1").key_path)

    def test_omitting_default_does_not_demote_the_node(self):
        """`--default` is `store_true`, so an omission and a false are the same
        token to argparse unless the default is the None sentinel."""
        self.target_add("aws-1", "--host", "13.49.210.78", "--default")
        self.assertTrue(self.stored("aws-1").is_default)

        self.target_add("aws-1", "--user", CONFIGURED)

        self.assertTrue(
            self.stored("aws-1").is_default, "target add demoted the default node"
        )

    def test_registration_time_belongs_to_the_registration(self):
        self.target_add("aws-1", "--host", "13.49.210.78")
        first = self.stored("aws-1").created_at

        self.target_add("aws-1", "--user", CONFIGURED)

        self.assertEqual(first, self.stored("aws-1").created_at)

    def test_the_context_is_rebuilt_against_the_stored_host(self):
        """The whole repair: `--user` alone must still produce a context that
        dials the stored address as the new user."""
        self.target_add(
            "aws-1", "--host", "13.49.210.78", "--user", DIALLED, "--mac-address", PIN
        )
        self.run_command.reset_mock()

        self.target_add("aws-1", "--user", CONFIGURED)

        created = [
            call.args[0]
            for call in self.run_command.call_args_list
            if len(call.args) > 0
            and call.args[0][:3] == ["docker", "context", "create"]
        ]
        self.assertTrue(created, "no docker context was rebuilt")
        self.assertIn("host=ssh://ec2-user@13.49.210.78", created[-1])


class AValueCanStillBeCleared(TargetAddHarness):
    def test_an_explicitly_empty_mac_address_removes_the_pin(self):
        """Merging must not make "unset this" unsayable. `None` (absent) and
        `""` (given, empty) are different inputs, which is what buys both."""
        self.target_add("aws-1", "--host", "13.49.210.78", "--mac-address", PIN)

        self.target_add("aws-1", "--mac-address", "")

        self.assertEqual("", self.stored("aws-1").mac_address)

    def test_an_explicitly_empty_user_removes_the_user(self):
        self.target_add("aws-1", "--host", "13.49.210.78", "--user", CONFIGURED)

        self.target_add("aws-1", "--user", "")

        self.assertEqual("", self.stored("aws-1").user)


class ANewNodeStillGetsTheDefaults(TargetAddHarness):
    def test_a_bare_add_registers_a_local_node(self):
        self.target_add("wsl")

        node = self.stored("wsl")
        self.assertEqual("localhost", node.host)
        self.assertEqual("", node.user)
        self.assertEqual("", node.mac_address)
        self.assertFalse(node.is_default)

    def test_the_flags_are_still_recorded(self):
        self.target_add(
            "aws-1",
            "--host",
            "13.49.210.78",
            "--user",
            CONFIGURED,
            "--mac-address",
            "06:FF:C5:F9:CF:A5",
            "--default",
        )

        node = self.stored("aws-1")
        self.assertEqual("13.49.210.78", node.host)
        self.assertEqual(CONFIGURED, node.user)
        # LDM-#1752: lower-cased so casing cannot read as a mismatch against
        # `docker inspect`.
        self.assertEqual(PIN, node.mac_address)
        self.assertTrue(node.is_default)


class AnUpdateIsNeverSilent(TargetAddHarness):
    def test_it_names_what_changed(self):
        self.target_add("aws-1", "--host", "13.49.210.78", "--user", DIALLED)

        out = self.target_add("aws-1", "--user", CONFIGURED)

        self.assertIn("updated", out)
        self.assertIn(DIALLED, out)
        self.assertIn(CONFIGURED, out)

    def test_it_names_what_it_kept(self):
        """ "Did I just lose my MAC pin?" is the question this command used to
        answer wrong. The answer is now on screen, not in `~/.ldmrc`."""
        self.target_add("aws-1", "--host", "13.49.210.78", "--mac-address", PIN)

        out = self.target_add("aws-1", "--user", CONFIGURED)

        self.assertIn("Kept", out)
        self.assertIn(PIN, out)

    def test_it_says_when_the_stored_settings_did_not_move(self):
        """Silence would read as "it took my flags" -- the misreading that made
        the old behaviour so damaging."""
        self.target_add("aws-1", "--host", "13.49.210.78", "--user", CONFIGURED)

        out = self.target_add("aws-1", "--user", CONFIGURED)

        self.assertIn("stored settings unchanged", out)

    def test_it_does_not_claim_nothing_happened_when_the_context_was_rebuilt(self):
        """Observed live: repairing a drifted SSH user leaves `~/.ldmrc`
        untouched (it was right all along) and rebuilds the Docker context. A
        bare "nothing changed" there would be false, and would read as though
        the repair had not been applied."""
        self.target_add("aws-1", "--host", "13.49.210.78", "--user", CONFIGURED)

        out = self.target_add("aws-1", "--user", CONFIGURED)

        self.assertNotIn("nothing changed.", out)
        self.assertIn("rebuilt to dial ssh://ec2-user@13.49.210.78", out)

    def test_a_local_node_has_no_context_to_report(self):
        self.target_add("wsl")

        out = self.target_add("wsl", "--user", CONFIGURED)

        self.assertNotIn("Docker context", out)

    def test_a_first_registration_still_reads_as_a_registration(self):
        out = self.target_add("aws-1", "--host", "13.49.210.78")

        self.assertIn("registered successfully", out)
        self.assertNotIn("Kept", out)

    def test_the_announcement_is_not_gated_behind_info_mode(self):
        """LDM-#1036: `UI.detail` is invisible without --info/--verbose, which
        is exactly the ordinary invocation where this matters."""
        self.assertFalse(UI.INFO_MODE and UI.VERBOSE, "test ran in info mode")
        self.target_add("aws-1", "--host", "13.49.210.78", "--mac-address", PIN)

        out = self.target_add("aws-1", "--user", CONFIGURED)

        self.assertIn("Kept", out)


if __name__ == "__main__":
    unittest.main()
