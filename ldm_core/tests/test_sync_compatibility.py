import os
import shutil
import sys
import typing
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ldm_core.ui import UI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import sync_compatibility
from sync_compatibility import (
    _VERIFY_SCRIPT_SAFE_LINE,
    _is_verify_script_diff_cosmetic_only,
)


def _mock_diff_result(diff_text, returncode=0):
    res = MagicMock()
    res.returncode = returncode
    res.stdout = diff_text
    return res


class TestVerifyScriptSafeLine:
    """LDM-#1058: unit coverage for the line-classification regex itself,
    independent of the higher-level diff function."""

    def test_bash_comment_is_safe(self):
        assert _VERIFY_SCRIPT_SAFE_LINE.match("-# some comment")
        assert _VERIFY_SCRIPT_SAFE_LINE.match("+# updated comment")

    def test_script_version_assignment_is_safe(self):
        assert _VERIFY_SCRIPT_SAFE_LINE.match('-SCRIPT_VERSION="2.15.26"')
        assert _VERIFY_SCRIPT_SAFE_LINE.match('+SCRIPT_VERSION="2.15.27-pre.3"')

    def test_powershell_script_version_assignment_is_safe(self):
        assert _VERIFY_SCRIPT_SAFE_LINE.match('-$SCRIPT_VERSION = "2.15.26"')
        assert _VERIFY_SCRIPT_SAFE_LINE.match('+$SCRIPT_VERSION = "2.15.27-pre.3"')

    def test_plain_message_lines_are_safe(self):
        assert _VERIFY_SCRIPT_SAFE_LINE.match('+        echo "new message"')
        assert _VERIFY_SCRIPT_SAFE_LINE.match('-Write-Output "old message"')
        assert _VERIFY_SCRIPT_SAFE_LINE.match('+Write-Host "new message"')

    def test_bare_quoted_array_element_is_safe(self):
        # Regression: the real #1049 fix built its PowerShell message as a
        # bare string element in a $warnLines = @(...) array, looped over
        # separately with Write-Output/Write-Host rather than called
        # inline -- this first tripped the original, narrower pattern.
        assert _VERIFY_SCRIPT_SAFE_LINE.match(
            '+                "  re-pull this script: Invoke-WebRequest -Uri '
            '`"https://example.com/script.ps1`" -OutFile `"script.ps1`""'
        )

    def test_control_flow_line_is_not_safe(self):
        assert not _VERIFY_SCRIPT_SAFE_LINE.match('+if [ "$FOO" = "baz" ]; then')

    def test_arbitrary_command_line_is_not_safe(self):
        assert not _VERIFY_SCRIPT_SAFE_LINE.match(
            "+docker ps -a --filter status=running"
        )


class TestIsVerifyScriptDiffCosmeticOnly:
    """LDM-#1058: the standalone verify-script's own version can lag the
    installed binary's version between refreshes (see #1049) even when
    nothing it checks changed -- these tests lock in that a provably
    cosmetic-only diff (comments, the version line, message text) is
    accepted, while any real logic change is rejected, and that any git
    error fails safe (rejected), never open."""

    @patch("sync_compatibility.subprocess.run")
    def test_cosmetic_only_diff_accepted(self, mock_run):
        diff = (
            "diff --git a/scripts/verify_e2e_refactor.sh b/scripts/verify_e2e_refactor.sh\n"
            "--- a/scripts/verify_e2e_refactor.sh\n"
            "+++ b/scripts/verify_e2e_refactor.sh\n"
            "@@ -9,2 +9,2 @@\n"
            '-SCRIPT_VERSION="2.15.26"\n'
            '+SCRIPT_VERSION="2.15.27-pre.3"\n'
            "@@ -80,1 +80,1 @@\n"
            '-        echo "old re-pull hint"\n'
            '+        echo "new re-pull hint"\n'
        )
        mock_run.return_value = _mock_diff_result(diff)
        assert _is_verify_script_diff_cosmetic_only("v2.15.26") is True

    @patch("sync_compatibility.subprocess.run")
    def test_no_diff_at_all_is_cosmetic(self, mock_run):
        mock_run.return_value = _mock_diff_result("")
        assert _is_verify_script_diff_cosmetic_only("v2.15.27-pre.3") is True

    @patch("sync_compatibility.subprocess.run")
    def test_logic_change_diff_rejected(self, mock_run):
        diff = (
            "diff --git a/scripts/verify_e2e_refactor.sh b/scripts/verify_e2e_refactor.sh\n"
            "--- a/scripts/verify_e2e_refactor.sh\n"
            "+++ b/scripts/verify_e2e_refactor.sh\n"
            "@@ -50,1 +50,1 @@\n"
            '-if [ "$FOO" = "bar" ]; then\n'
            '+if [ "$FOO" = "baz" ]; then\n'
        )
        mock_run.return_value = _mock_diff_result(diff)
        assert _is_verify_script_diff_cosmetic_only("v2.15.26") is False

    @patch("sync_compatibility.subprocess.run")
    def test_mixed_diff_with_one_logic_line_rejected(self, mock_run):
        diff = (
            "--- a/scripts/verify_e2e_refactor.sh\n"
            "+++ b/scripts/verify_e2e_refactor.sh\n"
            '-SCRIPT_VERSION="2.15.26"\n'
            '+SCRIPT_VERSION="2.15.27-pre.3"\n'
            "+NEW_CHECK_ENABLED=true\n"
        )
        mock_run.return_value = _mock_diff_result(diff)
        assert _is_verify_script_diff_cosmetic_only("v2.15.26") is False

    @patch("sync_compatibility.subprocess.run")
    def test_git_error_fails_safe(self, mock_run):
        mock_run.return_value = _mock_diff_result("", returncode=1)
        assert _is_verify_script_diff_cosmetic_only("badref") is False

    @patch("sync_compatibility.subprocess.run", side_effect=Exception("boom"))
    def test_exception_fails_safe(self, mock_run):
        assert _is_verify_script_diff_cosmetic_only("v2.15.26") is False


class TestArgumentHandling:
    """LDM-#1252: the script had no argument parsing, so `--help` -- and any
    typo -- fell through to a full sync, archiving reports and rewriting the
    compatibility table. Asking a destructive tool what it does must be safe."""

    def teardown_method(self):
        # DRY_RUN is module-level state; never leak it into another test.
        sync_compatibility.DRY_RUN = False

    @patch("sync_compatibility.sync_reports")
    def test_help_prints_usage_without_syncing(self, mock_sync):
        with pytest.raises(SystemExit) as exc:
            sync_compatibility.main(["--help"])
        assert exc.value.code == 0
        mock_sync.assert_not_called()

    @patch("sync_compatibility.sync_reports")
    def test_unknown_argument_aborts_instead_of_syncing(self, mock_sync):
        """A typo must fail loudly, not silently rewrite the matrix."""
        with pytest.raises(SystemExit) as exc:
            sync_compatibility.main(["--drynrun"])
        assert exc.value.code != 0
        mock_sync.assert_not_called()

    @patch("sync_compatibility.sync_reports")
    def test_dry_run_sets_the_flag(self, mock_sync):
        sync_compatibility.main(["--dry-run"])
        assert sync_compatibility.DRY_RUN is True
        mock_sync.assert_called_once()

    @patch("sync_compatibility.sync_reports")
    def test_default_run_is_not_dry(self, mock_sync):
        sync_compatibility.main([])
        assert sync_compatibility.DRY_RUN is False
        mock_sync.assert_called_once()

    def test_mutate_skips_the_action_when_dry(self):
        called = []
        sync_compatibility.DRY_RUN = True
        sync_compatibility._mutate("do a thing", lambda: called.append(1))
        assert called == []

    def test_mutate_performs_the_action_when_not_dry(self):
        called = []
        sync_compatibility.DRY_RUN = False
        sync_compatibility._mutate("do a thing", lambda: called.append(1))
        assert called == [1]


class TestMismatchedCheckoutGuard:
    """LDM-#1390: a raw report whose version does not match this checkout used to
    be archived after nothing but a `UI.warning`. A warning is easy to miss in a
    long run, and by then the file has moved -- while each report can represent
    hours of real multi-platform testing. The mismatch almost always means the
    operator is on the wrong ref, not that the report is obsolete."""

    def teardown_method(self):
        sync_compatibility.DRY_RUN = False
        sync_compatibility.ARCHIVE_STALE = False

    STALE: typing.ClassVar[list[tuple[str, str]]] = [
        ("verify-linux-ubuntu-pass.txt", "binary version 9.9.9 != 2.18.0")
    ]

    def test_it_exits_rather_than_archiving(self):
        sync_compatibility.DRY_RUN = False
        with pytest.raises(SystemExit) as exc:
            sync_compatibility._report_mismatched_checkout(self.STALE)
        assert exc.value.code != 0, "must be a failing exit so CI/tooling notices"

    def test_dry_run_reports_without_failing(self):
        """A preview must stay safe to run from tooling."""
        sync_compatibility.DRY_RUN = True
        sync_compatibility._report_mismatched_checkout(self.STALE, fatal=False)

    def test_the_message_names_the_report_and_both_versions(self, capsys):
        """The whole point is that the operator can see what to fix."""
        with pytest.raises(SystemExit):
            sync_compatibility._report_mismatched_checkout(self.STALE)
        out = capsys.readouterr()
        combined = out.out + out.err
        assert "verify-linux-ubuntu-pass.txt" in combined
        assert "9.9.9" in combined and "2.18.0" in combined
        assert "--archive-stale" in combined, "must name the deliberate opt-out"

    @patch("sync_compatibility.sync_reports")
    def test_archive_stale_flag_is_off_by_default(self, mock_sync):
        sync_compatibility.main([])
        assert sync_compatibility.ARCHIVE_STALE is False

    @patch("sync_compatibility.sync_reports")
    def test_archive_stale_flag_can_be_opted_into(self, mock_sync):
        sync_compatibility.main(["--archive-stale"])
        assert sync_compatibility.ARCHIVE_STALE is True


class TestArchivePlanOutput:
    """LDM-#1390: --archive-stale is the deliberate path, and "deliberate" only
    means something if you can see what it is about to do. The moves used to be
    announced one line at a time as each happened, interleaved with the rest of
    the sync."""

    PLANNED: typing.ClassVar[list] = [
        (
            Path("verify-linux-ubuntu-20260827-pass.txt"),
            "verify-linux-ubuntu-fail-ab12cd34.txt",
            "binary version 9.9.9 != 2.18.0",
        ),
        (
            Path("verify-macos-colima-20260827-pass.txt"),
            "verify-macos-colima-fail-ef56ab78.txt",
            "verify script version 9.9.9 != 2.18.0",
        ),
    ]

    def teardown_method(self):
        UI.QUIET_MODE = False
        sync_compatibility.ARCHIVE_STALE = False

    def test_it_lists_each_source_and_its_destination(self, capsys):
        UI.QUIET_MODE = False
        sync_compatibility._announce_archive_plan(self.PLANNED)
        out = capsys.readouterr().out + capsys.readouterr().err
        for src, dest, reason in self.PLANNED:
            assert src.name in out, f"{src.name} not listed"
            assert dest in out, f"destination {dest} not shown"
            assert reason in out, "the reason for archiving must be visible"

    def test_it_says_how_many_and_which_version_it_matched_against(self, capsys):
        UI.QUIET_MODE = False
        sync_compatibility._announce_archive_plan(self.PLANNED)
        out = capsys.readouterr().out
        assert "2 raw report(s)" in out
        assert sync_compatibility.VERSION in out

    def test_quiet_suppresses_the_plan(self, capsys):
        UI.QUIET_MODE = True
        sync_compatibility._announce_archive_plan(self.PLANNED)
        assert capsys.readouterr().out.strip() == ""

    @patch("sync_compatibility.sync_reports")
    def test_quiet_flag_sets_the_ui_switch(self, mock_sync):
        sync_compatibility.main(["--quiet"])
        assert UI.QUIET_MODE is True

    @patch("sync_compatibility.sync_reports")
    def test_not_quiet_by_default(self, mock_sync):
        sync_compatibility.main([])
        assert UI.QUIET_MODE is False

    def test_quiet_never_hides_the_refusal(self, capsys):
        """Suppressing progress must not suppress a failure."""
        UI.QUIET_MODE = True
        with pytest.raises(SystemExit):
            sync_compatibility._report_mismatched_checkout(
                [("verify-linux-ubuntu-pass.txt", "binary version 9.9.9 != 2.18.0")]
            )
        captured = capsys.readouterr()
        assert "Refusing to archive" in (captured.out + captured.err)


class TestSandboxedPaths:
    """LDM-#1391: the script used to hardcode the two paths it mutates, so there
    was no way to exercise it without operating on the real verification record --
    reports that are a verbatim account of what was actually tested, each
    representing hours of real multi-platform running."""

    def teardown_method(self):
        UI.QUIET_MODE = False
        sync_compatibility.ARCHIVE_STALE = False
        sync_compatibility.DRY_RUN = False

    @staticmethod
    def _report(directory, name, version):
        p = directory / name
        p.write_text(
            "LDM Verification Report\n"
            f"Version:      {version}\n"
            f"Script Ver:   {version}\n"
            "Platform:     sandbox-probe\n"
            "Result:       PASS\n"
        )
        return p

    def test_defaults_point_at_the_shipped_locations(self):
        """The override must not change real behaviour."""
        assert (
            Path("references/verification-results")
            == sync_compatibility.DEFAULT_RESULTS_DIR
        )
        assert (
            Path("docs/reference/compatibility.md")
            == sync_compatibility.DEFAULT_TABLE_FILE
        )

    def test_a_real_sync_stays_inside_the_sandbox(self, tmp_path):
        """The whole point: a full, non-dry sync that touches nothing real."""
        results = tmp_path / "results"
        results.mkdir()
        table = tmp_path / "compatibility.md"
        shutil.copy(sync_compatibility.DEFAULT_TABLE_FILE, table)
        stale = self._report(
            results, "verify-sandbox-20260827-000000-pass.txt", "9.9.9-pre.1"
        )

        real_before = sorted(
            p.name for p in sync_compatibility.DEFAULT_RESULTS_DIR.rglob("*")
        )

        sync_compatibility.ARCHIVE_STALE = True
        sync_compatibility.sync_reports(results_dir=results, table_file=table)

        assert not stale.exists(), "the stale report should have been archived"
        assert list((results / "archived_findings").glob("*.txt")), (
            "and archived inside the sandbox"
        )
        assert (
            sorted(p.name for p in sync_compatibility.DEFAULT_RESULTS_DIR.rglob("*"))
            == real_before
        ), "the real verification record must be untouched"

    def test_the_refusal_also_honours_the_sandbox(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        table = tmp_path / "compatibility.md"
        shutil.copy(sync_compatibility.DEFAULT_TABLE_FILE, table)
        stale = self._report(
            results, "verify-sandbox-20260827-000000-pass.txt", "9.9.9-pre.1"
        )

        sync_compatibility.ARCHIVE_STALE = False
        with pytest.raises(SystemExit):
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        assert stale.exists(), "refusing must leave the report where it was"


def _no_docs_sync():
    """Keeps a sandboxed sync from reaching the REAL docs.

    sync_reports() ends by calling sync_docs.sync_table(), which rewrites
    docs/TESTING.md and README.md from the real compatibility table regardless
    of --table. A test must never reach it (LDM-#1391).
    """
    return patch.dict(sys.modules, {"sync_docs": MagicMock()})


def _linux_report(directory, name, platform, version, env_label=None, passed=True):
    """Writes a report shaped like a real verify_e2e_refactor.sh one.

    The version matters: a report whose version does not match this checkout is
    diverted by the LDM-#1390 staleness path and never reaches the collision
    check, so these fixtures normally claim the current VERSION.
    """
    lines = [
        "=== LDM BINARY VERIFICATION REPORT ===",
        "Timestamp:    Sun Sep  6 22:16:18 UTC 2026",
        "Hostname:     runner",
        f"Platform:     {platform}",
    ]
    if env_label:
        lines.append(f"Env Label:    {env_label}")
    lines += [
        "Binary:       /usr/local/bin/ldm",
        f"Version:      ldm {version}",
        f"Script Ver:   {version}",
        "Docker:       28.0.4",
        "",
    ]
    if passed:
        lines.append("ALL E2E VERIFICATIONS PASSED!")
    path = directory / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestDeclaredEnvironmentLabel:
    """LDM-#1614: the environment name came from the report's content, and only
    Fedora and Ubuntu were recognised by name -- every other distro fell through
    to a generic "Linux Workstation / Linux". LDM_ENV_LABEL lets the runner that
    knows (the CI matrix key) declare the identity instead of leaving this
    script to infer one from a string it may not recognise."""

    def test_a_declared_label_names_the_distro(self, tmp_path):
        report = _linux_report(
            tmp_path,
            "verify-raw-20260906-000000-pass.txt",
            "Debian GNU/Linux 12 (bookworm)",
            sync_compatibility.VERSION,
            env_label="debian",
        )
        meta = sync_compatibility.get_report_metadata(report)
        assert meta["os"] == "Debian 12"
        assert meta["internal_slug"] == "linux-workstation-debian-12-native-docker"

    def test_the_matrix_key_is_translated_to_a_display_name(self, tmp_path):
        """`rockylinux` is what the workflow calls it; "Rocky Linux" is what a
        reader of the published matrix needs to see."""
        report = _linux_report(
            tmp_path,
            "verify-raw-20260906-000000-pass.txt",
            "Rocky Linux 9.6 (Blue Onyx)",
            sync_compatibility.VERSION,
            env_label="rockylinux",
        )
        assert sync_compatibility.get_report_metadata(report)["os"] == "Rocky Linux 9.6"

    def test_a_label_with_no_version_in_the_platform_line_still_resolves(
        self, tmp_path
    ):
        """A minimal image with no usable /etc/os-release is exactly where this
        mechanism matters most, so it must not depend on the platform line."""
        report = _linux_report(
            tmp_path,
            "verify-raw-20260906-000000-pass.txt",
            "linux-gnu",
            sync_compatibility.VERSION,
            env_label="alpine",
        )
        assert sync_compatibility.get_report_metadata(report)["os"] == "Alpine"

    def test_an_unlabelled_linux_report_keeps_its_historical_name(self, tmp_path):
        """Reports predating the label must produce the row they always have."""
        report = _linux_report(
            tmp_path,
            "verify-raw-20260906-000000-pass.txt",
            "Debian GNU/Linux 12 (bookworm)",
            sync_compatibility.VERSION,
        )
        meta = sync_compatibility.get_report_metadata(report)
        assert meta["os"] == "Linux"
        assert meta["internal_slug"] == "linux-workstation-linux-native-docker"

    @pytest.mark.parametrize(
        ("label", "platform", "expected_slug"),
        [
            (
                "ubuntu",
                "Ubuntu 24.04.4 LTS",
                "linux-workstation-ubuntu-24.04-native-docker",
            ),
            (
                "fedora",
                "Fedora Linux 44 (Container Image)",
                "linux-workstation-fedora-44-native-docker",
            ),
        ],
    )
    def test_the_two_already_working_distros_are_unchanged(
        self, tmp_path, label, platform, expected_slug
    ):
        """These slugs are the names of two committed canonical reports. If a
        label changed them, the next real sync would rename real evidence."""
        report = _linux_report(
            tmp_path,
            "verify-raw-20260906-000000-pass.txt",
            platform,
            sync_compatibility.VERSION,
            env_label=label,
        )
        assert (
            sync_compatibility.get_report_metadata(report)["internal_slug"]
            == expected_slug
        )


class TestNameCollisionGuard:
    """LDM-#1614: three containerised distros resolved to
    verify-linux-workstation-linux-native-docker-pass.txt and each silently
    overwrote the previous. Three genuine passes went in, one row came out --
    labelled "Linux Workstation / Linux" while actually being one of them -- and
    the two losers landed in archived_findings/ under hash names indistinguishable
    from each other. Following LDM-#1390: refuse, naming every report and the
    values that prove they are different environments, before moving anything."""

    def teardown_method(self):
        UI.QUIET_MODE = False
        sync_compatibility.ARCHIVE_STALE = False
        sync_compatibility.DRY_RUN = False

    @staticmethod
    def _sandbox(tmp_path):
        """LDM-#1391: never the real results dir or the real table."""
        results = tmp_path / "results"
        results.mkdir()
        table = tmp_path / "compatibility.md"
        shutil.copy(sync_compatibility.DEFAULT_TABLE_FILE, table)
        return results, table

    @staticmethod
    def _three_unlabelled_distros(results):
        """The real v2.21.0-pre.2 artifact set: Debian, Rocky and Alpine, each
        reporting its own PRETTY_NAME and none of them recognised by name."""
        return [
            _linux_report(
                results,
                "verify-linux-workstation-linux-native-docker-20260906-2201-pass.txt",
                "Debian GNU/Linux 12 (bookworm)",
                sync_compatibility.VERSION,
            ),
            _linux_report(
                results,
                "verify-linux-workstation-linux-native-docker-20260906-2215-pass.txt",
                "Rocky Linux 9.6 (Blue Onyx)",
                sync_compatibility.VERSION,
            ),
            _linux_report(
                results,
                "verify-linux-workstation-linux-native-docker-20260906-2230-pass.txt",
                "Alpine Linux v3.22.2",
                sync_compatibility.VERSION,
            ),
        ]

    def test_it_refuses_instead_of_letting_one_distro_displace_another(self, tmp_path):
        results, table = self._sandbox(tmp_path)
        reports = self._three_unlabelled_distros(results)
        table_before = table.read_text()

        with _no_docs_sync(), pytest.raises(SystemExit) as exc:
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        assert exc.value.code != 0, "must be a failing exit so CI/tooling notices"
        for report in reports:
            assert report.exists(), f"{report.name} was moved despite the refusal"
        assert not list((results / "archived_findings").glob("*.txt")), (
            "nothing may be archived before the refusal"
        )
        assert not (
            results / "verify-linux-workstation-linux-native-docker-pass.txt"
        ).exists(), "no canonical report may be written from an ambiguous set"
        assert table.read_text() == table_before, "the table must be untouched"

    def test_the_message_names_every_report_and_the_colliding_values(
        self, tmp_path, capsys
    ):
        """The whole point is that the operator can see which environments
        collided, and on what -- the archived hash names could not say."""
        results, table = self._sandbox(tmp_path)
        self._three_unlabelled_distros(results)

        with _no_docs_sync(), pytest.raises(SystemExit):
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Refusing to sync" in combined
        assert "linux-workstation-linux-native-docker" in combined
        for name in (
            "verify-linux-workstation-linux-native-docker-20260906-2201-pass.txt",
            "verify-linux-workstation-linux-native-docker-20260906-2215-pass.txt",
            "verify-linux-workstation-linux-native-docker-20260906-2230-pass.txt",
        ):
            assert name in combined, f"{name} not named"
        for platform in (
            "Debian GNU/Linux 12 (bookworm)",
            "Rocky Linux 9.6 (Blue Onyx)",
            "Alpine Linux v3.22.2",
        ):
            assert platform in combined, f"{platform} not shown as a colliding value"
        assert "LDM_ENV_LABEL" in combined, "must name the fix"

    def test_quiet_never_hides_the_refusal(self, tmp_path, capsys):
        results, table = self._sandbox(tmp_path)
        self._three_unlabelled_distros(results)
        UI.QUIET_MODE = True

        with _no_docs_sync(), pytest.raises(SystemExit):
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        captured = capsys.readouterr()
        assert "Refusing to sync" in (captured.out + captured.err)

    def test_dry_run_previews_the_collision_without_failing(self, tmp_path):
        """A preview must stay safe to run from tooling."""
        results, table = self._sandbox(tmp_path)
        reports = self._three_unlabelled_distros(results)
        sync_compatibility.DRY_RUN = True

        with _no_docs_sync():
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        for report in reports:
            assert report.exists()

    def test_archive_stale_does_not_move_anything_before_the_refusal(self, tmp_path):
        """--archive-stale is the LDM-#1390 opt-out for a version mismatch. It
        is not consent to resolve a collision by picking a survivor, and the
        moves it authorises must not happen before the collision is reported."""
        results, table = self._sandbox(tmp_path)
        reports = self._three_unlabelled_distros(results)
        stale = _linux_report(
            results,
            "verify-macos-raw-20260906-2240-pass.txt",
            "darwin25-arm64",
            "9.9.9-pre.1",
        )
        sync_compatibility.ARCHIVE_STALE = True

        with _no_docs_sync(), pytest.raises(SystemExit):
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        assert stale.exists(), "the stale report was archived before the refusal"
        for report in reports:
            assert report.exists()

    def test_repeat_runs_of_one_environment_still_supersede(self, tmp_path):
        """Latest-wins is correct for the same rig verified twice. Refusing
        there would break the ordinary contributor workflow, so the guard keys
        on the identity each report states, not merely on the shared name."""
        results, table = self._sandbox(tmp_path)
        older = _linux_report(
            results,
            "verify-linux-workstation-linux-native-docker-20260906-2201-pass.txt",
            "Debian GNU/Linux 12 (bookworm)",
            sync_compatibility.VERSION,
        )
        newer = _linux_report(
            results,
            "verify-linux-workstation-linux-native-docker-20260906-2230-pass.txt",
            "Debian GNU/Linux 12 (bookworm)",
            sync_compatibility.VERSION,
        )
        # Identical header timestamps, so the mtime fallback orders them.
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))

        with patch.dict(sys.modules, {"sync_docs": MagicMock()}):
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        assert (
            results / "verify-linux-workstation-linux-native-docker-pass.txt"
        ).exists()
        assert len(list((results / "archived_findings").glob("*.txt"))) == 1

    def test_an_existing_canonical_report_is_not_a_collision(self, tmp_path):
        """A fresh raw report superseding the committed one for its environment
        is the ordinary path, not an ambiguity."""
        results, table = self._sandbox(tmp_path)
        _linux_report(
            results,
            "verify-linux-workstation-linux-native-docker-pass.txt",
            "Debian GNU/Linux 12 (bookworm)",
            sync_compatibility.VERSION,
        )
        _linux_report(
            results,
            "verify-linux-workstation-linux-native-docker-20260906-2230-pass.txt",
            "Rocky Linux 9.6 (Blue Onyx)",
            sync_compatibility.VERSION,
        )

        with patch.dict(sys.modules, {"sync_docs": MagicMock()}):
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

    def test_labelled_distros_no_longer_collide_at_all(self, tmp_path):
        """The primary fix: once each run declares its distro, three passes
        produce three rows instead of one that misidentifies itself."""
        results, table = self._sandbox(tmp_path)
        fixtures = [
            (
                "verify-raw-a-20260906-2201-pass.txt",
                "Debian GNU/Linux 12 (bookworm)",
                "debian",
            ),
            (
                "verify-raw-b-20260906-2215-pass.txt",
                "Rocky Linux 9.6 (Blue Onyx)",
                "rockylinux",
            ),
            (
                "verify-raw-c-20260906-2230-pass.txt",
                "Alpine Linux v3.22.2",
                "alpine",
            ),
        ]
        for name, platform, label in fixtures:
            _linux_report(
                results, name, platform, sync_compatibility.VERSION, env_label=label
            )

        with patch.dict(sys.modules, {"sync_docs": MagicMock()}):
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        assert sorted(p.name for p in results.glob("*.txt")) == [
            "verify-linux-workstation-alpine-3.22.2-native-docker-pass.txt",
            "verify-linux-workstation-debian-12-native-docker-pass.txt",
            "verify-linux-workstation-rocky-linux-9.6-native-docker-pass.txt",
        ]
        assert not list((results / "archived_findings").glob("*.txt"))
        table_text = table.read_text()
        for os_name in ("Debian 12", "Rocky Linux 9.6", "Alpine 3.22.2"):
            assert os_name in table_text, f"{os_name} missing from the table"

    def test_the_real_verification_record_is_never_touched(self, tmp_path):
        """LDM-#1391. These reports are an honest account of what was actually
        tested; a test that rewrites them destroys real data."""
        results, table = self._sandbox(tmp_path)
        self._three_unlabelled_distros(results)
        real_before = sorted(
            p.name for p in sync_compatibility.DEFAULT_RESULTS_DIR.rglob("*")
        )

        with _no_docs_sync(), pytest.raises(SystemExit):
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        assert (
            sorted(p.name for p in sync_compatibility.DEFAULT_RESULTS_DIR.rglob("*"))
            == real_before
        )


class TestProviderDerivedFromTheDeclaredLabel:
    """LDM-#1631: the Docker Provider column was derived by looking for the
    literal word "linux" in the report's `Platform:` line, falling back to
    "Unknown".

    Every containerised verify-linux arm satisfied that only by luck, because
    each image's /etc/os-release PRETTY_NAME happens to contain the word --
    `Fedora Linux 44 (Container Image)`, `Rocky Linux 9.3 (Blue Onyx)`,
    `Alpine Linux v3.24`, `Debian GNU/Linux 12 (bookworm)` (all read off the
    v2.21.0 tag run's artifacts, run 34063433007). Alpine is one upstream
    rewording away from `Alpine 3.25`, at which point the published,
    user-facing matrix grows a row reading provider `Unknown` and the canonical
    report is renamed ...-alpine-3.25-unknown-pass.txt.

    LDM-#1614 already plumbs the distro identity through explicitly as an
    `Env Label:` header, so the provider can be derived from what the runner
    declared instead of from an upstream vendor's marketing string.
    """

    def teardown_method(self):
        UI.QUIET_MODE = False
        sync_compatibility.ARCHIVE_STALE = False
        sync_compatibility.DRY_RUN = False

    # Read off the five artifacts of run 34063433007 (the v2.21.0 tag run), not
    # invented: `gh run download 34063433007` then the `Platform:` header of
    # each verify-*.txt. The same five strings are recorded in
    # test_ci_verification_reporting.OBSERVED_PLATFORM_LINES.
    REAL_CONTAINERISED_ARMS = (
        ("fedora", "Fedora Linux 44 (Container Image)"),
        ("rockylinux", "Rocky Linux 9.3 (Blue Onyx)"),
        ("alpine", "Alpine Linux v3.24"),
        ("debian", "Debian GNU/Linux 12 (bookworm)"),
    )

    def test_a_platform_string_without_the_word_linux_still_names_the_provider(
        self, tmp_path
    ):
        """The regression this exists for. `Alpine 3.25` is the shortening the
        issue names; the label says which distro, so the provider no longer
        depends on the vendor spelling it out."""
        report = _linux_report(
            tmp_path,
            "verify-raw-20260906-000000-pass.txt",
            "Alpine 3.25",
            sync_compatibility.VERSION,
            env_label="alpine",
        )
        meta = sync_compatibility.get_report_metadata(report)
        assert meta["provider"] == "Native Docker"
        assert meta["internal_slug"] == "linux-workstation-alpine-3.25-native-docker"

    @pytest.mark.parametrize(("label", "platform"), REAL_CONTAINERISED_ARMS)
    def test_the_real_containerised_arms_resolve_exactly_as_before(
        self, tmp_path, label, platform
    ):
        """Preservation, not a new capability: these four already resolved to
        Native Docker off the word "linux", and must still."""
        report = _linux_report(
            tmp_path,
            "verify-raw-20260906-000000-pass.txt",
            platform,
            sync_compatibility.VERSION,
            env_label=label,
        )
        assert sync_compatibility.get_report_metadata(report)["provider"] == (
            "Native Docker"
        )

    def test_an_unlabelled_report_still_falls_back_to_the_platform_string(
        self, tmp_path
    ):
        """The label is an additional signal, never a replacement. A contributor
        running scripts/verify_e2e_refactor.sh by hand sets no LDM_ENV_LABEL, so
        removing the string match would have broken every hand-run report and
        renamed the two committed Linux rows."""
        report = _linux_report(
            tmp_path,
            "verify-raw-20260906-000000-pass.txt",
            "Alpine Linux v3.24",
            sync_compatibility.VERSION,
        )
        meta = sync_compatibility.get_report_metadata(report)
        assert meta["provider"] == "Native Docker"
        assert meta["internal_slug"] == "linux-workstation-linux-native-docker"

    def test_a_hand_run_with_no_label_is_never_refused(self, tmp_path):
        """With neither a label nor the word "linux" there is nothing to derive
        from, so the historical "Unknown" stands -- deliberately. Refusing, or
        guessing, would break the hand-run path that has no LDM_ENV_LABEL."""
        results = tmp_path / "results"
        results.mkdir()
        table = tmp_path / "compatibility.md"
        shutil.copy(sync_compatibility.DEFAULT_TABLE_FILE, table)
        report = _linux_report(
            results,
            "verify-raw-20260906-000000-pass.txt",
            "Alpine 3.25",
            sync_compatibility.VERSION,
        )

        meta = sync_compatibility.get_report_metadata(report)
        assert meta["arch"] == "Linux Workstation"
        assert meta["provider"] == "Unknown"

        with _no_docs_sync():
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        assert list(results.glob("*.txt")), "the hand-run report must still sync"

    @pytest.mark.parametrize(
        ("filename", "platform", "label", "expected_provider"),
        [
            # Real platform lines from run 34063433007's macOS and Windows
            # artifacts. A label must not pull any of these onto the Linux
            # branch: the macOS (Colima/OrbStack/Docker Desktop), Windows
            # (Docker Desktop) and WSL2 rows are published documentation.
            (
                "verify-apple-silicon-macos-16-tahoe-colima-20260906-2215-pass.txt",
                "darwin25",
                "macos",
                "Colima",
            ),
            (
                "verify-windows-pc-windows-11-docker-desktop-20260906-2215-pass.txt",
                "Microsoft Windows 10.0.26100",
                "windows",
                "Docker Desktop",
            ),
            (
                "verify-windows-pc-windows-11-native-wsl2-20260906-2215-pass.txt",
                "Ubuntu 24.04.4 LTS",
                "wsl2",
                "Native WSL2",
            ),
        ],
    )
    def test_a_label_does_not_pull_a_non_linux_row_onto_the_linux_branch(
        self, tmp_path, filename, platform, label, expected_provider
    ):
        report = _linux_report(
            tmp_path,
            filename,
            platform,
            sync_compatibility.VERSION,
            env_label=label,
        )
        assert (
            sync_compatibility.get_report_metadata(report)["provider"]
            == expected_provider
        )

    def test_the_published_row_and_canonical_filename_say_native_docker(self, tmp_path):
        """End to end, through a sandboxed sync (LDM-#1391): the visible effect
        of the defect is a matrix row and a report filename, so assert those."""
        results = tmp_path / "results"
        results.mkdir()
        table = tmp_path / "compatibility.md"
        shutil.copy(sync_compatibility.DEFAULT_TABLE_FILE, table)
        _linux_report(
            results,
            "verify-raw-20260906-000000-pass.txt",
            "Alpine 3.25",
            sync_compatibility.VERSION,
            env_label="alpine",
        )
        real_before = sorted(
            p.name for p in sync_compatibility.DEFAULT_RESULTS_DIR.rglob("*")
        )

        with _no_docs_sync():
            sync_compatibility.sync_reports(results_dir=results, table_file=table)

        assert [p.name for p in results.glob("*.txt")] == [
            "verify-linux-workstation-alpine-3.25-native-docker-pass.txt"
        ]
        row = next(
            line
            for line in table.read_text().splitlines()
            if "Alpine 3.25" in line and line.startswith("|")
        )
        assert "**Native Docker**" in row
        assert "Unknown" not in row
        assert (
            sorted(p.name for p in sync_compatibility.DEFAULT_RESULTS_DIR.rglob("*"))
            == real_before
        ), "LDM-#1391: the real verification record must be untouched"
