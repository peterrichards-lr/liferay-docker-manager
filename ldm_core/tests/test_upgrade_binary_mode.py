"""The self-upgrade must leave the binary 0755, never world-writable (GHSA-cq82-rxm7-vw5c).

Every Unix self-upgrade from v2.7.6 to v2.26.8 installed the binary through
`utils.safe_move`, whose copy step ORs 0o666 into the mode so Liferay's
auto-deploy scanner can write deployed files. On an executable on the user's
PATH that is `-rwxrwxrwx`: any local user could replace `ldm` and run code as
whoever ran it next. Found on an Amazon Linux 2023 host after
`ldm system upgrade --version v2.26.8`.

These drive the real replacement against real files. The mode assertions are
made under umask 0, the setting that would expose a mode taken from the
umask rather than set explicitly.
"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.diagnostics import upgrade
from ldm_core.diagnostics.upgrade import (
    _replace_binary,
    _sudo_install_argv,
    repair_binary_mode,
)

_REAL_WARN = upgrade._warn


def _mode(path):
    return stat.S_IMODE(Path(path).stat().st_mode)


@unittest.skipIf(sys.platform == "win32", "Unix file modes")
class TestReplaceBinary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        self.exe = self.bin_dir / "ldm"
        self.exe.write_bytes(b"old binary")
        # lint: executable-stub -- the installed binary's mode is under test; text content, in a private tempdir not on PATH
        os.chmod(self.exe, 0o755)
        self.download = root / "ldm-upgrade-download"
        self.download.write_bytes(b"new binary")
        os.chmod(self.download, 0o600)  # what tempfile.mkstemp gives the download
        old_umask = os.umask(0)
        self.addCleanup(os.umask, old_umask)

    def _leftovers(self):
        return sorted(p.name for p in self.bin_dir.iterdir() if p.name != "ldm")

    def test_the_new_binary_is_exactly_0755(self):
        _replace_binary(self.download, self.exe)
        self.assertEqual(b"new binary", self.exe.read_bytes())
        self.assertEqual(
            0o755,
            _mode(self.exe),
            f"binary left at {oct(_mode(self.exe))}; anything wider lets "
            "another local user replace it (GHSA-cq82-rxm7-vw5c)",
        )

    def test_a_binary_an_earlier_upgrade_left_world_writable_is_repaired(self):
        # lint: executable-stub -- reproduces the 0777 binary the bug left; text content, private tempdir not on PATH
        os.chmod(self.exe, 0o777)
        _replace_binary(self.download, self.exe)
        self.assertEqual(0o755, _mode(self.exe))

    def test_nothing_is_left_behind(self):
        _replace_binary(self.download, self.exe)
        self.assertEqual([], self._leftovers(), "staging file left beside the binary")
        self.assertFalse(self.download.exists(), "the download should be consumed")

    def test_a_failed_rename_leaves_the_old_binary_and_the_download(self):
        """The caller falls back to sudo with the same download, and the user
        must still have a working `ldm` if that fails too."""
        with patch.object(upgrade.os, "replace", side_effect=OSError("EXDEV")):
            with self.assertRaises(OSError):
                _replace_binary(self.download, self.exe)
        self.assertEqual(b"old binary", self.exe.read_bytes())
        self.assertEqual(0o755, _mode(self.exe))
        self.assertEqual([], self._leftovers())
        self.assertTrue(self.download.exists())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores modes")
    def test_an_unwritable_directory_raises_permission_error(self):
        """PermissionError is what sends `run_upgrade` to the sudo fallback,
        e.g. for a binary in a root-owned /usr/local/bin."""
        # lint: executable-stub -- a directory made read-only, not an executable file
        os.chmod(self.bin_dir, 0o555)
        self.addCleanup(os.chmod, self.bin_dir, 0o755)
        with self.assertRaises(PermissionError):
            _replace_binary(self.download, self.exe)
        self.assertEqual(b"old binary", self.exe.read_bytes())
        self.assertTrue(self.download.exists())


class TestTheUpgradeUsesIt(unittest.TestCase):
    """`run_upgrade` downloads, prompts and re-execs, so its wiring is asserted
    against the source, as `test_upgrade_rotation_notice` does."""

    def _source(self):
        import inspect

        return inspect.getsource(upgrade)

    def test_the_upgrade_never_goes_through_the_deploy_helpers(self):
        """`safe_move`/`safe_copy` widen modes for Liferay's deploy folder;
        that is the defect, so neither may reach the binary again."""
        src = self._source()
        for helper in (
            "safe_move",
            "safe_copy",
            "atomic_copy",
            "_fixup_unix_permissions",
        ):
            with self.subTest(helper=helper):
                self.assertFalse(hasattr(upgrade, helper), f"upgrade imports {helper}")
                self.assertNotIn(f"{helper}(", src, f"upgrade calls {helper}")

    def test_the_unix_path_calls_replace_binary(self):
        self.assertIn("_replace_binary(temp_new, exe_path)", self._source())

    def test_both_sudo_fallbacks_and_the_printed_command_use_install(self):
        """`cp` onto an existing file keeps its mode AND owner, so a binary an
        affected release left 0777 (or chowned to 1000) stayed that way. The two
        fallbacks are os.system (interactive) and subprocess (non-interactive);
        the third use is the command printed when both fail."""
        import inspect

        src = inspect.getsource(upgrade.run_upgrade)
        self.assertEqual(3, src.count("_sudo_install_argv("))
        self.assertNotIn('"cp"', src)
        self.assertNotIn("sudo cp", src)


@unittest.skipIf(sys.platform == "win32", "Unix file modes")
class TestSudoInstallCommand(unittest.TestCase):
    """The fallback's command, run for real without the `sudo` prefix: the
    claim it rests on is that `install` replaces a 0777 file with a 0755 one."""

    def test_the_argv_sets_root_ownership_and_mode_755(self):
        """Owner and group explicitly: busybox `install` writes into the
        existing file and would otherwise keep a uid-1000 owner."""
        argv = _sudo_install_argv(["sudo", "-n"], "/tmp/new", "/usr/local/bin/ldm")
        self.assertEqual(
            ["sudo", "-n", "install", "-o", "0", "-g", "0", "-m", "755"],
            argv[:9],
        )
        self.assertEqual(["/tmp/new", "/usr/local/bin/ldm"], argv[9:])

    def test_install_turns_a_0777_binary_into_0755(self):
        """The same flags, run for real; owner and group are the current
        user's so no root is needed."""
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "ldm"
            exe.write_bytes(b"old binary")
            # lint: executable-stub -- reproduces the 0777 binary; text content, private tempdir, never executed
            os.chmod(exe, 0o777)
            new = Path(tmp) / "download"
            new.write_bytes(b"new binary")
            os.chmod(new, 0o600)
            argv = _sudo_install_argv([], new, exe, os.getuid(), os.getgid())
            subprocess.run(argv, check=True)
            self.assertEqual(b"new binary", exe.read_bytes())
            self.assertEqual(0o755, _mode(exe))


class TestManualCommand(unittest.TestCase):
    def test_it_installs_with_mode_755_instead_of_writing_over_the_binary(self):
        """`curl -o <binary>` keeps the old file's mode, and `chmod +x` does not
        remove other users' write access."""
        cmd = upgrade._get_manual_upgrade_cmd(
            None, "https://example.invalid/ldm-linux", Path("/usr/local/bin/ldm")
        )
        self.assertIn('install -m 755 "$t" /usr/local/bin/ldm', cmd)
        self.assertNotIn("chmod +x", cmd)
        self.assertNotIn("-o /usr/local/bin/ldm", cmd)


@unittest.skipIf(sys.platform == "win32", "Unix file modes")
class TestRepairOnFirstRun(unittest.TestCase):
    """The upgrade INTO the fix runs the affected release's code, so the fixed
    binary arrives 0777; the fixed code corrects that when it next runs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.exe = Path(self._tmp.name) / "ldm"
        self.exe.write_bytes(b"binary")
        warn = patch.object(upgrade, "_warn")
        self.warning = warn.start()
        self.addCleanup(warn.stop)

    def _warned(self):
        return " ".join(str(c.args[0]) for c in self.warning.call_args_list)

    def _exposed(self, loose, misowned):
        """`_exposed_binary`'s answer for a binary in a root-owned directory,
        which a test can't create without root."""
        st = self.exe.stat()
        return patch.object(
            upgrade, "_exposed_binary", return_value=(self.exe, st, loose, misowned)
        )

    def test_root_returns_a_misowned_binary_to_root(self):
        """An affected `sudo ldm system upgrade` chowned /usr/local/bin/ldm to
        uid 1000. Run as root, the fixed code gives it back."""
        with (
            self._exposed(0, True),
            patch.object(upgrade.os, "geteuid", return_value=0),
            patch.object(upgrade.os, "chown") as chown,
        ):
            self.assertTrue(repair_binary_mode(self.exe))
        chown.assert_called_once_with(self.exe, 0, 0)
        self.assertIn("now belongs to root", self._warned())

    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "needs a file owned by a non-root user",
    )
    def test_the_owner_of_a_misowned_binary_is_told_to_chown_it(self):
        """Not root, so it can't chown, but it still removes the write bits it
        owns: a `sudo ldm system upgrade` on an affected release leaves
        /usr/local/bin/ldm 0777 and owned by uid 1000."""
        # lint: executable-stub -- the 0777 binary under repair; text content, private tempdir, never executed
        os.chmod(self.exe, 0o777)
        with (
            self._exposed(0o022, True),
            patch.object(upgrade.os, "geteuid", return_value=self.exe.stat().st_uid),
        ):
            self.assertFalse(repair_binary_mode(self.exe))
        self.assertEqual(0o755, _mode(self.exe))
        self.assertIn("sudo chown root", self._warned())

    def test_a_failed_chown_still_warns(self):
        """A read-only or root-squashed filesystem must not turn into silence."""
        with (
            self._exposed(0, True),
            patch.object(upgrade.os, "geteuid", return_value=0),
            patch.object(upgrade.os, "chown", side_effect=OSError("EROFS")),
        ):
            self.assertFalse(repair_binary_mode(self.exe))
        self.assertIn("sudo chown root", self._warned())

    def test_dry_run_changes_nothing(self):
        # lint: executable-stub -- the 0777 binary under repair; text content, private tempdir, never executed
        os.chmod(self.exe, 0o777)
        with patch.dict(os.environ, {"LDM_DRY_RUN": "true"}):
            self.assertFalse(repair_binary_mode(self.exe))
        self.assertEqual(0o777, _mode(self.exe))
        self.assertIn("chmod 755", self._warned())

    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "needs a file owned by a non-root user",
    )
    def test_a_non_root_binary_in_a_root_owned_folder_is_misowned(self):
        """The detection itself, unpatched except for the folder's owner."""
        real_stat = Path.stat
        parent = self.exe.resolve().parent  # macOS: /var is /private/var

        def stat_with_root_parent(path, *args, **kwargs):
            st = real_stat(path, *args, **kwargs)
            if path == parent:
                return os.stat_result(
                    (
                        st.st_mode,
                        st.st_ino,
                        st.st_dev,
                        st.st_nlink,
                        0,
                        0,
                        st.st_size,
                        0,
                        0,
                        0,
                    )
                )
            return st

        # lint: executable-stub -- an otherwise-correct binary; text content, private tempdir, never executed
        os.chmod(self.exe, 0o755)
        with patch.object(
            Path, "stat", autospec=True, side_effect=stat_with_root_parent
        ):
            found = upgrade._exposed_binary(self.exe)
        self.assertIsNotNone(found)
        self.assertEqual((0, True), found[2:])

    def test_warnings_go_to_stderr_not_stdout(self):
        """The check runs before every command, including `--json` ones whose
        stdout a script parses."""
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            _REAL_WARN("binary check")  # setUp patches upgrade._warn
        self.assertIn("binary check", err.getvalue())
        self.assertEqual("", out.getvalue())

    def test_a_private_group_is_not_counted(self):
        """Under umask 002 a console script is 0775 with a group holding only
        its owner; warning about that would be a false alarm every release."""
        st = os.stat_result((0o100775, 0, 0, 1, 1000, 1000, 0, 0, 0, 0))
        owner = type("P", (), {"pw_name": "alice", "pw_gid": 1000})()
        own = type("G", (), {"gr_name": "alice", "gr_mem": []})()
        shared = type("G", (), {"gr_name": "staff", "gr_mem": ["alice", "bob"]})()
        with patch("pwd.getpwuid", return_value=owner):
            with patch("grp.getgrgid", return_value=own):
                self.assertEqual(0, upgrade._loose_write_bits(st))
            with patch("grp.getgrgid", return_value=shared):
                self.assertEqual(0o020, upgrade._loose_write_bits(st))

    def _group(self, private):
        """The binary's group as either the owner's own private group (Linux
        user-private groups, as on CI runners) or a shared one (macOS
        `staff`). The real answer differs by machine, so each test pins it."""
        gid = self.exe.stat().st_gid
        owner = type("P", (), {"pw_name": "alice", "pw_gid": gid})()
        if private:
            group = type("G", (), {"gr_name": "alice", "gr_mem": []})()
        else:
            group = type("G", (), {"gr_name": "staff", "gr_mem": ["alice", "bob"]})()
        return patch("pwd.getpwuid", return_value=owner), patch(
            "grp.getgrgid", return_value=group
        )

    def test_an_owned_world_writable_binary_is_tightened(self):
        # lint: executable-stub -- the 0777 binary under repair; text content, private tempdir, never executed
        os.chmod(self.exe, 0o777)
        pw, gr = self._group(private=False)
        with pw, gr:
            self.assertTrue(repair_binary_mode(self.exe))
        self.assertEqual(0o755, _mode(self.exe))
        self.assertIn("GHSA-cq82-rxm7-vw5c", self._warned())

    def test_with_a_private_group_only_other_write_is_removed(self):
        """Group write to a group holding only the owner exposes nothing, so
        it is left; other write is still removed."""
        # lint: executable-stub -- the 0777 binary under repair; text content, private tempdir, never executed
        os.chmod(self.exe, 0o777)
        pw, gr = self._group(private=True)
        with pw, gr:
            self.assertTrue(repair_binary_mode(self.exe))
        self.assertEqual(0o775, _mode(self.exe))

    def test_a_safe_binary_is_left_alone_silently(self):
        # lint: executable-stub -- an already-correct binary; text content, private tempdir, never executed
        os.chmod(self.exe, 0o755)
        self.assertTrue(repair_binary_mode(self.exe))
        self.assertEqual(0o755, _mode(self.exe))
        self.warning.assert_not_called()

    def test_someone_elses_binary_is_reported_not_changed(self):
        """Changing another account's file is not LDM's call, and may not be
        possible; the user must decide whether it was tampered with."""
        # lint: executable-stub -- the 0777 binary under repair; text content, private tempdir, never executed
        os.chmod(self.exe, 0o777)
        with patch.object(upgrade.os, "geteuid", return_value=os.geteuid() + 1):
            self.assertFalse(repair_binary_mode(self.exe))
        self.assertEqual(0o777, _mode(self.exe))
        self.assertIn("reinstall", self._warned())
        self.assertIn("chmod 755", self._warned())

    def test_a_source_checkout_is_skipped(self):
        script = Path(self._tmp.name) / "ldm.py"
        script.write_text("")
        os.chmod(script, 0o666)
        self.assertTrue(repair_binary_mode(script))
        self.assertEqual(0o666, _mode(script))

    def test_every_command_runs_it_before_the_tty_check(self):
        """A host driven by scripts or CI never has a TTY, and may never have
        an interactive first run of the new version."""
        import inspect

        from ldm_core import cli

        src = inspect.getsource(cli.check_and_display_upgrade_banner)
        call = src.index("repair_binary_mode()")
        self.assertLess(call, src.index("isatty()"))
        self.assertLess(call, src.index("last_run_version"))


if __name__ == "__main__":
    unittest.main()
