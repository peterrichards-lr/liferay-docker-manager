"""A local or downloaded .ldmp is verified too, and how much (LDM-#1621).

`_verify_ldm_package_manifest` used to be reachable from exactly one input -- a
GitHub repo URL whose latest release carries a `.ldmp` asset (measured in
`test_package_listing_recovery.py`, `TestOnlyOneInputReachesThisCode`). So
`ldm import ./thing.ldmp`, and the `.ldmp` URL that downloads and re-enters
`cmd_import` with a local path, were verified by nothing at all.

`PackageVerificationStage` closes that, and deliberately does not apply every
control the release path applies. This file is where that split is held, so it
asserts as hard on what must still import as on what must now be refused:

* an unparseable manifest is REFUSED -- exit 1, nothing restored, no project
  metadata written
* the LDM-#1579 recovery runs, so the AICA shape imports and is corrected
* a manifest that claims a category it neither lists nor ships WARNS and still
  imports -- the release path refuses this, and doing the same here would break
  an import that works today over a defect nothing downstream reads
* an absent or mismatched `github_repository` does not refuse anything; a local
  file has no fetch origin for the declaration to contradict
* a `.zip`/`.tgz` workspace archive, which has no manifest at all, is untouched

Every test drives the real `cmd_import` through the real pipeline. Only the
boundaries that would reach Docker, git or the network are stubbed --
`snapshot.cmd_restore`, `runtime.cmd_run`, and the shared preflight stage,
which runs the doctor's tooling checks. `read_meta`/`write_meta` are the real
implementations against a temp directory, so the project metadata asserted on
here is genuinely written and read back.
"""

import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.pipelines.validation import ValidationStage as SharedValidationStage
from ldm_core.workspace.importer import cmd_import

REPO = "widget"
ORIGIN = f"acme/{REPO}"


def _build_ldmp(root, manifest_text, payload_members, suffix=".ldmp"):
    """A package file as `ldm package` writes one: meta + files.tar.gz, tarred.

    `manifest_text` is written verbatim rather than dumped, so a test can hand
    over a manifest that is not valid JSON.
    """
    inner = root / "inner"
    inner.mkdir(parents=True, exist_ok=True)
    if manifest_text is not None:
        (inner / "meta").write_text(manifest_text, encoding="utf-8")

    staging = root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    for member in payload_members:
        target = staging / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PK\x03\x04")
    with tarfile.open(inner / "files.tar.gz", "w:gz") as tar:
        for member in payload_members:
            tar.add(staging / member, arcname=member)

    package = root / f"{REPO}{suffix}"
    with tarfile.open(package, "w:gz") as tar:
        for item in sorted(inner.iterdir()):
            tar.add(item, arcname=item.name)
    return package


class _PipelineManager:
    """The manager surface the import pipeline touches, and nothing else."""

    def __init__(self, project_path):
        self.project_path = Path(project_path)
        self.non_interactive = True
        self.args = MagicMock()
        # MagicMock attributes are truthy, and several of these are read with
        # `or` fallbacks. Pin them to the real "absent" value.
        self.args.project = None
        self.args.project_flag = None
        self.args.tag = None
        self.args.host_name = None
        self.args.ssl = None
        self.args.port = None
        self.args.build = False
        self.args.no_run = True
        # The checksum path is exercised by test_import_pipeline.py; skipping it
        # here keeps these tests about the manifest.
        self.args.verify = False
        self.snapshot = MagicMock()
        self.runtime = MagicMock()
        self.workspace = MagicMock()
        self.diagnostics = MagicMock()
        self.verify_runtime_environment = MagicMock()
        self.check_uncommitted_changes = MagicMock()
        self.safe_rmtree = MagicMock()

    def _check_java_version(self, _expected):
        return True

    def detect_project_path(self, _project_name, for_init=False):
        return self.project_path

    def setup_paths(self, root):
        root = Path(root)
        return {
            "root": root,
            "cx": root / "osgi" / "client-extensions",
            "configs": root / "configs",
            "deploy": root / "deploy",
            "files": root / "files",
            "scripts": root / "scripts",
            "modules": root / "modules",
            "ce_dir": root / "client-extensions",
        }

    def read_meta(self, path, strict=False):
        """Mirrors LiferayDockerManager.read_meta's directory handling."""
        from ldm_core.utils import MetaReadError, read_meta

        p = Path(path)
        if p.is_dir():
            manifest = p / "meta"
            if not manifest.exists():
                if strict:
                    raise MetaReadError(f"No metadata file found in {p}")
                return {}
            return read_meta(manifest, strict=strict)
        return read_meta(p, strict=strict)

    def write_meta(self, path, meta):
        from ldm_core.utils import resolve_meta_file_path, write_meta

        write_meta(resolve_meta_file_path(path), meta)


class _ImportHost:
    def __init__(self, manager):
        self.manager = manager
        self._parse_github_repo = MagicMock(return_value=None)


class _Outcome:
    """What an import did, whether or not it survived."""

    def __init__(self, root, manager):
        self.root = Path(root)
        self.manager = manager
        self.aborted = False
        self.exit_code = None
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.details: list[str] = []

    @property
    def restore_calls(self):
        return self.manager.snapshot.cmd_restore.call_args_list

    @property
    def project_meta(self):
        meta_file = self.manager.project_path / "meta"
        if not meta_file.exists():
            return None
        return json.loads(meta_file.read_text(encoding="utf-8"))

    @property
    def scratch_left_behind(self):
        scratch = self.root / ".ldm_temp"
        if not scratch.exists():
            return []
        return sorted(p.name for p in scratch.iterdir())


def _import(root, manifest_text, payload_members, suffix=".ldmp"):
    """Run the real cmd_import over a package built in the test."""
    root = Path(root)
    package = _build_ldmp(root, manifest_text, payload_members, suffix=suffix)
    manager = _PipelineManager(root / "projects" / REPO)
    outcome = _Outcome(root, manager)

    cwd = Path.cwd()
    os.chdir(root)
    try:
        with (
            # LDM_HOME so nothing can reach the developer's real ~/.ldm;
            # LDM_DRY_RUN cleared because write_meta diverts to an in-memory
            # VFS when it is set, which would make project_meta vacuous, and
            # because cmd_import short-circuits to _handle_dry_run.
            patch.dict(
                os.environ,
                {"LDM_HOME": str(root / "ldm-home"), "LDM_DRY_RUN": ""},
            ),
            # The shared preflight runs the doctor's tooling checks, which
            # reach real binaries and Docker.
            patch.object(SharedValidationStage, "execute", lambda *_a, **_k: None),
            patch("ldm_core.ui.UI.warning", side_effect=outcome.warnings.append),
            patch("ldm_core.ui.UI.detail", side_effect=outcome.details.append),
            patch(
                "ldm_core.ui.UI.error",
                side_effect=lambda msg, *_a, **_k: outcome.errors.append(str(msg)),
            ),
        ):
            try:
                cmd_import(_ImportHost(manager), str(package), no_run=True)
            except SystemExit as exc:
                outcome.aborted = True
                outcome.exit_code = exc.code
    finally:
        os.chdir(cwd)

    return outcome


def _manifest(**extra):
    base = {
        "github_repository": ORIGIN,
        "tag": "7.4.13-u108",
        "db_type": "mysql",
        "includes_database": "true",
    }
    base.update(extra)
    return json.dumps(base)


class TestAnUnparseableManifestIsRefused(unittest.TestCase):
    """The one refusal this change adds, and the reason it is a refusal.

    Measured before the stage existed: this exact package imported, reported
    `Project created/imported at: ...`, called cmd_restore, and wrote a project
    meta carrying neither `tag` nor `db_type` -- so the MySQL dump in the
    payload was restored into whichever engine the defaults happened to pick,
    and nothing said so beyond one read_meta warning. The import was already
    broken; the change only makes it say so.
    """

    def test_a_manifest_with_trailing_junk_aborts_the_import(self):
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(
                Path(d), _manifest() + "\ntrailing junk\n", ["data/dump.sql"]
            )

            self.assertTrue(outcome.aborted, "an unparseable manifest was accepted")
            # LDM's contract: 1 is validation.
            self.assertEqual(outcome.exit_code, 1)
            self.assertIn(
                "could not be parsed",
                " ".join(outcome.errors),
                f"refused for the wrong reason: {outcome.errors}",
            )

    def test_the_refusal_restores_nothing_and_writes_no_project(self):
        """Refusing after the restore would be worse than not refusing."""
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(
                Path(d), _manifest() + "\ntrailing junk\n", ["data/dump.sql"]
            )

            self.assertEqual(outcome.restore_calls, [])
            self.assertIsNone(
                outcome.project_meta, "a refused package still wrote project metadata"
            )
            self.assertEqual(
                outcome.scratch_left_behind,
                [],
                "the refusal left its extraction directory behind",
            )

    def test_a_flat_format_manifest_is_still_accepted(self):
        """`read_meta` supports a legacy `k=v` manifest, and strict must too.

        The refusal above must be a parse failure, not "is not JSON".
        """
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(
                Path(d),
                "tag=7.4.13-u108\ndb_type=mysql\ngithub_repository=" + ORIGIN + "\n",
                ["data/dump.sql"],
            )

            self.assertFalse(
                outcome.aborted, f"a legacy flat manifest was refused: {outcome.errors}"
            )
            self.assertEqual(outcome.project_meta["db_type"], "mysql")


class TestWhatMustStillImport(unittest.TestCase):
    """The habits this change must not break, asserted as hard as the refusal.

    Each of these imports today. Wiring the release-path verifier in as-is
    would refuse the last three of them.
    """

    def test_a_valid_package_imports_and_keeps_its_tag_and_db_type(self):
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(Path(d), _manifest(), ["data/dump.sql"])

            self.assertFalse(
                outcome.aborted, f"a valid package was refused: {outcome.errors}"
            )
            self.assertEqual(len(outcome.restore_calls), 1)
            self.assertEqual(outcome.project_meta["tag"], "7.4.13-u108")
            self.assertEqual(outcome.project_meta["db_type"], "mysql")

    def test_a_package_shipping_nothing_it_claims_warns_and_still_imports(self):
        """The release path refuses this. Here it must not.

        The listing keys are read by `ldm snapshot` output and the dashboard,
        never by cmd_restore, so the project this produces is correct -- there
        is nothing to half-install. Refusing would block a working import over
        a manifest defect nothing downstream consumes.
        """
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(
                Path(d),
                _manifest(includes_client_extensions="true", client_extensions=""),
                ["data/dump.sql"],
            )

            self.assertFalse(
                outcome.aborted,
                f"a contradictory manifest refused a local import: {outcome.errors}",
            )
            self.assertEqual(len(outcome.restore_calls), 1)
            self.assertIn(
                "self-contradictory",
                " ".join(outcome.warnings),
                "the contradiction was neither refused nor reported",
            )

    def test_a_manifest_declaring_no_origin_warns_and_still_imports(self):
        """The release path calls this a Security Violation. Here it cannot be.

        A hand-built package, or one produced before `ldm package` began
        writing the key, has no `github_repository` and nothing to compare one
        against. Warn, so the absence is visible, and import.
        """
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(
                Path(d),
                json.dumps({"tag": "7.4.13-u108", "db_type": "mysql"}),
                ["data/dump.sql"],
            )

            self.assertFalse(
                outcome.aborted,
                f"a package with no declared origin was refused: {outcome.errors}",
            )
            self.assertEqual(len(outcome.restore_calls), 1)
            self.assertIn(
                "declares no 'github_repository'",
                " ".join(outcome.warnings),
                "the missing origin was not reported",
            )

    def test_an_origin_naming_an_unrelated_repo_does_not_refuse(self):
        """There is no fetch origin here for the declaration to contradict."""
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(
                Path(d),
                _manifest(github_repository="someone-else/other-thing"),
                ["data/dump.sql"],
            )

            self.assertFalse(
                outcome.aborted,
                f"a local import was refused on origin: {outcome.errors}",
            )
            self.assertEqual(len(outcome.restore_calls), 1)
            self.assertNotIn(
                "Security Violation", " ".join(outcome.errors + outcome.warnings)
            )
            # Reported at the detail tier, not as a warning: this fires on every
            # legitimate local import of a packaged project, and a warning that
            # always fires is one users learn to skip.
            self.assertIn(
                "origin is unverified",
                " ".join(outcome.details),
                "the unverifiable origin was not reported at all",
            )

    def test_an_archive_with_no_manifest_is_not_treated_as_a_broken_package(self):
        """A .tgz workspace archive has no manifest, and that is normal.

        The release path dies on a missing manifest. Applying that here would
        reject every workspace archive, because the manifest's absence is what
        distinguishes the two kinds of source.
        """
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(Path(d), None, ["gradle.properties"], suffix=".tgz")

            self.assertFalse(
                outcome.aborted,
                f"a manifest-less workspace archive was refused: {outcome.errors}",
            )
            self.assertEqual(
                outcome.restore_calls, [], "a workspace archive triggered a restore"
            )


class TestTheRecoveryReachesThisInputToo(unittest.TestCase):
    """LDM-#1579's recovery, on the input LDM-#1588 could not reach.

    This is the half of the LDM-#1568 gate that costs nobody anything: the
    package ships the archives its manifest fails to list, so the listing is
    corrected from the payload and the import carries on.
    """

    def test_the_aica_shape_is_recovered_on_a_local_import(self):
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(
                Path(d),
                _manifest(includes_client_extensions="true", client_extensions=""),
                [
                    "osgi/client-extensions/ai-commerce-accelerator-batch.zip",
                    "osgi/client-extensions/site-initializer.zip",
                    "data/dump.sql",
                ],
            )

            self.assertFalse(
                outcome.aborted, f"a recoverable package was refused: {outcome.errors}"
            )
            self.assertEqual(len(outcome.restore_calls), 1)
            self.assertIn(
                "Recovered 2 from the package contents",
                " ".join(outcome.warnings),
            )

    def test_osgi_modules_are_recovered_on_this_input_as_well(self):
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(
                Path(d),
                _manifest(includes_osgi_modules="true", osgi_modules=""),
                ["osgi/modules/com.acme.endpoint.jar", "data/dump.sql"],
            )

            self.assertFalse(outcome.aborted)
            self.assertIn(
                "Recovered 1 from the package contents",
                " ".join(outcome.warnings),
            )

    def test_an_honest_manifest_produces_no_recovery_warning(self):
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(
                Path(d),
                _manifest(
                    includes_client_extensions="true",
                    client_extensions="declared.zip",
                ),
                ["osgi/client-extensions/declared.zip"],
            )

            self.assertFalse(outcome.aborted)
            self.assertEqual(
                [w for w in outcome.warnings if "Recovered" in w],
                [],
                "an honest manifest was reported as recovered",
            )


class TestDbTypeWasAlreadyEnforcedHere(unittest.TestCase):
    """LDM-#1621 lists db_type as a gap on this input. It measurably is not.

    ProjectSetupStage has always applied the same check with the same message,
    so this asserts the existing behaviour rather than new behaviour -- and
    guards against the check being removed as "now covered by the new stage",
    which it is not: ProjectSetupStage also covers the `.ldmrc` manifest of a
    directory source, which the new stage never sees.
    """

    def test_an_unknown_engine_aborts_the_import(self):
        with tempfile.TemporaryDirectory() as d:
            outcome = _import(Path(d), _manifest(db_type="oracle"), ["data/dump.sql"])

            self.assertTrue(outcome.aborted)
            self.assertEqual(outcome.exit_code, 1)
            self.assertIn("Unsupported database type", " ".join(outcome.errors))
            self.assertEqual(outcome.restore_calls, [])


if __name__ == "__main__":
    unittest.main()
