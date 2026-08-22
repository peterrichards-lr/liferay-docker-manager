import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.dev import DevService


class MockDevManager:
    def __init__(self, args):
        self.args = args


class TestDevService(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp_dir.name)

        # Mock args
        self.args = MagicMock()
        self.args.yes = True
        self.manager = MockDevManager(self.args)
        self.handler = DevService(self.manager)

        # Create mock project structure
        (self.base / ".git").mkdir()
        (self.base / "pyproject.toml").write_text('version = "1.0.0"')

        # Override constants.VERSION for testing
        self.version_patch = patch("ldm_core.handlers.dev.VERSION", "2.4.26-beta.4")
        self.version_patch.start()

        # Mock DEV_MODE for tests
        self.dev_mode_patch = patch.dict("os.environ", {"LDM_DEV_MODE": "true"})
        self.dev_mode_patch.start()

    def tearDown(self):
        self.dev_mode_patch.stop()
        self.version_patch.stop()
        self.tmp_dir.cleanup()

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_bump_beta(self, mock_cwd):
        mock_cwd.return_value = self.base

        with patch("ldm_core.ui.UI.confirm", return_value=True):
            # Test 2.4.26-beta.4 -> 2.4.26-beta.5
            with patch.object(self.handler, "_apply_version_update") as mock_apply:
                self.handler.cmd_version(bump_type="beta")
                mock_apply.assert_called_with("2.4.26-beta.5", None)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_promote_stable(self, mock_cwd):
        mock_cwd.return_value = self.base

        with patch("ldm_core.ui.UI.confirm", return_value=True):
            # Test 2.4.26-beta.4 -> 2.4.26
            with patch.object(self.handler, "_apply_version_update") as mock_apply:
                self.handler.cmd_version(promote=True)
                mock_apply.assert_called_with("2.4.26", None)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_ensure_dev_env_blocks(self, mock_cwd):
        # Create a directory without .git
        with tempfile.TemporaryDirectory() as empty_dir:
            mock_cwd.return_value = Path(empty_dir)
            with self.assertRaises(SystemExit):
                with patch("ldm_core.ui.UI.die") as mock_die:
                    mock_die.side_effect = SystemExit
                    self.handler._ensure_dev_env()

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_bump_major_minor(self, mock_cwd):
        mock_cwd.return_value = self.base
        with patch("ldm_core.ui.UI.confirm", return_value=True):
            with patch.object(self.handler, "_apply_version_update") as mock_apply:
                self.handler.cmd_version(bump_type="major")
                mock_apply.assert_called_with("3.0.0", None)

                self.handler.cmd_version(bump_type="minor")
                mock_apply.assert_called_with("2.5.0", None)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_bump_preminor_opens_a_minor_pre_release_cycle(self, mock_cwd):
        """LDM-#1291: minor releases had no pre-release path at all.

        `beta` only ever opens the *next patch* cycle, and `minor` produces a
        stable version directly -- so a minor could not be exercised as a
        pre-release before reaching the wider user community.
        """
        mock_cwd.return_value = self.base
        with patch("ldm_core.ui.UI.confirm", return_value=True):
            with patch.object(self.handler, "_apply_version_update") as mock_apply:
                self.handler.cmd_version(bump_type="preminor")
                mock_apply.assert_called_with("2.5.0-pre.1", None)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_bump_premajor_opens_a_major_pre_release_cycle(self, mock_cwd):
        mock_cwd.return_value = self.base
        with patch("ldm_core.ui.UI.confirm", return_value=True):
            with patch.object(self.handler, "_apply_version_update") as mock_apply:
                self.handler.cmd_version(bump_type="premajor")
                mock_apply.assert_called_with("3.0.0-pre.1", None)

    def test_beta_continues_a_preminor_cycle(self):
        """The cycle must be continued by `beta`, not by `preminor` again.

        `beta` matches on the `-pre.N` suffix and increments N regardless of
        which component opened the cycle, so the existing continuing-cycle
        machinery -- tracking PR reuse, `--promote` -- works unchanged. If this
        regressed, a second increment would re-open the cycle at `-pre.1` and
        collide with an already-published tag, which the Burn Rule makes
        unrecoverable.
        """
        with patch.object(
            self.handler, "_version_from_disk", return_value="2.16.0-pre.1"
        ):
            with patch("ldm_core.ui.UI.confirm", return_value=True):
                with patch.object(self.handler, "_ensure_dev_env"):
                    with patch.object(
                        self.handler, "_apply_version_update"
                    ) as mock_apply:
                        self.handler.cmd_version(bump_type="beta")
                        mock_apply.assert_called_with("2.16.0-pre.2", None)

    def test_promote_from_a_preminor_cycle_yields_the_stable_minor(self):
        with patch.object(
            self.handler, "_version_from_disk", return_value="2.16.0-pre.3"
        ):
            with patch("ldm_core.ui.UI.confirm", return_value=True):
                with patch.object(self.handler, "_ensure_dev_env"):
                    with patch.object(
                        self.handler, "_apply_version_update"
                    ) as mock_apply:
                        self.handler.cmd_version(promote=True)
                        mock_apply.assert_called_with("2.16.0", None)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_version_is_read_from_disk_not_the_stale_import(self, mock_cwd):
        """LDM-#1290: the imported VERSION can lag what is on disk.

        Python validates cached bytecode on (source mtime in whole seconds,
        source size). A bump like `2.16.0-pre.1` -> `2.16.0-pre.2` changes
        neither, so a second bump inside the same second reuses stale bytecode.
        The bump then computes a replacement the file already contains, writes
        nothing and reports success -- and `scripts/release.py` reads this same
        value to decide what to TAG, which the Burn Rule makes permanent.

        Here the import is deliberately stale relative to the file, which is
        exactly the observed condition.
        """
        mock_cwd.return_value = self.base
        constants = self.base / "ldm_core" / "constants.py"
        constants.parent.mkdir(parents=True, exist_ok=True)
        constants.write_text('VERSION = "2.16.0-pre.7"\n')

        with patch("ldm_core.handlers.dev.VERSION", "2.16.0-pre.1"):
            self.assertEqual(self.handler._version_from_disk(), "2.16.0-pre.7")

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_version_falls_back_to_import_when_source_absent(self, mock_cwd):
        """A PyInstaller build has no constants.py on disk to parse."""
        with tempfile.TemporaryDirectory() as empty:
            mock_cwd.return_value = Path(empty)
            with patch("ldm_core.handlers.dev.VERSION", "2.15.33"):
                self.assertEqual(self.handler._version_from_disk(), "2.15.33")

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_update_that_changes_nothing_is_fatal(self, mock_cwd):
        """A bump that rewrites no files must not exit 0.

        Previously this emitted only per-file "Pattern mismatch?" warnings and
        still reported success, which is how a stale read could pass for a
        completed bump.
        """
        mock_cwd.return_value = self.base
        constants = self.base / "ldm_core" / "constants.py"
        constants.parent.mkdir(parents=True, exist_ok=True)
        # Already at the target version, so every replacement is a no-op.
        constants.write_text(
            'VERSION = "2.16.0-pre.2"\n# LDM_MAGIC_VERSION: 2.16.0-pre.2'
        )
        # setUp seeds pyproject.toml at 1.0.0, which would legitimately change
        # and so would keep the guard quiet. Put every target at the value.
        (self.base / "pyproject.toml").write_text('version = "2.16.0-pre.2"')

        with patch("ldm_core.ui.UI.die", side_effect=SystemExit) as mock_die:
            with self.assertRaises(SystemExit):
                self.handler._apply_version_update("2.16.0-pre.2", None)
            self.assertIn("changed no files", mock_die.call_args.args[0])

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_apply_version_update_writes_files(self, mock_cwd):
        mock_cwd.return_value = self.base

        # Setup files
        constants_path = self.base / "ldm_core" / "constants.py"
        constants_path.parent.mkdir(parents=True, exist_ok=True)
        constants_path.write_text(
            'VERSION = "2.4.26-beta.4"\nELASTICSEARCH_VERSION = "8.19.1"\n# LDM_MAGIC_VERSION: 2.4.26-beta.4'
        )

        pyproject_path = self.base / "pyproject.toml"
        pyproject_path.write_text(
            '# LDM_MAGIC_VERSION: 2.4.26-beta.4\nversion = "2.4.26-beta.4"'
        )

        scripts_dir = self.base / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        sh_path = scripts_dir / "verify_e2e_refactor.sh"
        sh_path.write_text(
            '#!/bin/bash\n# LDM_MAGIC_VERSION: 2.4.26-beta.4\nSCRIPT_VERSION="2.4.26-beta.4"\n'
        )
        ps1_path = scripts_dir / "verify_e2e_refactor.ps1"
        ps1_path.write_text(
            '# LDM_MAGIC_VERSION: 2.4.26-beta.4\n$SCRIPT_VERSION = "2.4.26-beta.4"\n'
        )

        self.handler._apply_version_update("2.4.26")

        content = constants_path.read_text()
        self.assertIn('VERSION = "2.4.26"', content)
        self.assertIn('ELASTICSEARCH_VERSION = "8.19.1"', content)  # UNCHANGED
        self.assertIn("LDM_MAGIC_VERSION: 2.4.26", content)

        pyproject_content = pyproject_path.read_text()
        self.assertIn('version = "2.4.26"', pyproject_content)
        # Regression test (#991): pyproject.toml's own magic comment was
        # never updated by the bump script, letting it drift silently.
        self.assertIn("LDM_MAGIC_VERSION: 2.4.26", pyproject_content)

        # Regression test (#1011): verify_e2e_refactor.sh/.ps1 embed their own
        # SCRIPT_VERSION so a locally-held copy can be checked against what
        # actually shipped, rather than guessing from a file mtime.
        sh_content = sh_path.read_text()
        self.assertIn('SCRIPT_VERSION="2.4.26"', sh_content)
        self.assertIn("LDM_MAGIC_VERSION: 2.4.26", sh_content)

        ps1_content = ps1_path.read_text()
        self.assertIn('$SCRIPT_VERSION = "2.4.26"', ps1_content)
        self.assertIn("LDM_MAGIC_VERSION: 2.4.26", ps1_content)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_promote_blocks_stable(self, mock_cwd):
        mock_cwd.return_value = self.base
        # Setup stable version
        with patch("ldm_core.handlers.dev.VERSION", "2.4.26"):
            with self.assertRaises(SystemExit):
                with patch("ldm_core.ui.UI.die") as mock_die:
                    mock_die.side_effect = SystemExit
                    self.handler.cmd_version(promote=True)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_version_set_and_build_info(self, mock_cwd):
        mock_cwd.return_value = self.base
        # Setup files
        constants_path = self.base / "ldm_core" / "constants.py"
        constants_path.parent.mkdir(parents=True, exist_ok=True)
        constants_path.write_text(
            'VERSION = "1.0.0"\nBUILD_INFO = None\n# LDM_MAGIC_VERSION: 1.0.0'
        )

        self.handler.cmd_version(set_version="2.0.0", build_info="CI-Build-123")

        content = constants_path.read_text()
        self.assertIn('VERSION = "2.0.0"', content)
        self.assertIn('BUILD_INFO = "CI-Build-123"', content)
        self.assertIn("LDM_MAGIC_VERSION: 2.0.0", content)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_version_check_sync(self, mock_cwd):
        mock_cwd.return_value = self.base
        pyproject_path = self.base / "pyproject.toml"

        # 1. Matching
        pyproject_path.write_text('version = "2.4.26-beta.4"')
        self.handler.cmd_version(check=True)  # Should not raise

        # 2. Mismatch
        pyproject_path.write_text('version = "mismatch"')
        with self.assertRaises(SystemExit):
            with patch("ldm_core.ui.UI.die") as mock_die:
                mock_die.side_effect = SystemExit
                self.handler.cmd_version(check=True)

    @patch("ldm_core.handlers.dev.Path.cwd")
    def test_version_print(self, mock_cwd):
        # LDM-#1290: version reads now prefer the on-disk source. This fixture
        # has no ldm_core/constants.py, so it exercises the fallback to the
        # imported constant -- the path a PyInstaller build takes.
        mock_cwd.return_value = self.base
        with patch("builtins.print") as mock_print:
            self.handler.cmd_version(print_only=True)
            mock_print.assert_called_with("2.4.26-beta.4")
