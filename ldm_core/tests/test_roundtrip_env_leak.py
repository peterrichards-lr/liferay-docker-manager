"""The .ldmp round trip must see a package carrying LDM's rendered env (LDM-#2372).

LDM-#2370: `ldm package` captured every `LIFERAY_*` key of the publisher's
rendered compose into the package's `custom_env`, and import replayed them on
the consumer. The E2E round trip runs source and copy on `localhost`, so a
leaked virtual host had the value the copy would have rendered anyway, and the
round trip passed.

`scripts/check_roundtrip_env_leak.py` asserts what is visible whatever the
host, and both halves of the E2E suite shell out to it. These run it as a
subprocess because the exit code IS the contract the shell halves consume:
0 clean, 1 defect, anything else means nothing was asserted.
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
CHECKER = REPO / "scripts" / "check_roundtrip_env_leak.py"

CLEAN, DEFECT, CANNOT_READ = 0, 1, 4

VHOST = "LIFERAY_COMPANY_PERIOD_DEFAULT_PERIOD_VIRTUAL_PERIOD_HOST_PERIOD_NAME"
VHOST_SYNC = (
    "LIFERAY_COMPANY_PERIOD_DEFAULT_PERIOD_VIRTUAL_PERIOD_HOST_PERIOD_SYNC"
    "_PERIOD_ON_PERIOD_STARTUP"
)
LOG4J = "LIFERAY_LOG4J2_CONFIGURATION_FILE"

#: The shape of a real rendered liferay environment, in LDM's own order: the
#: run pipeline's entries first, the composer's after, custom_env last.
RENDERED = [
    "LIFERAY_HOME=/opt/liferay",
    f"{VHOST}=localhost",
    f"{VHOST_SYNC}=true",
    "UMASK=0022",
    "LIFERAY_JVM_OPTS=-Xms2g -Xmx4g",
    f"{LOG4J}=/opt/liferay/osgi/log4j/portal-log4j-ext.xml",
]


def _check(env, custom_env=..., *, write_meta=True):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        compose = {"services": {"liferay": {"image": "x", "environment": env}}}
        (root / "docker-compose.yml").write_text(yaml.safe_dump(compose))
        if write_meta:
            meta = {"project_name": "p", "host_name": "localhost"}
            if custom_env is not ...:
                meta["custom_env"] = custom_env
            (root / "meta").write_text(json.dumps(meta))
        result = subprocess.run(
            [sys.executable, str(CHECKER), str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, result.stdout + result.stderr


class TheDefectIsReported(unittest.TestCase):
    def test_a_key_appended_twice_is_named(self):
        # LDM-#2370 as the composer renders it: custom_env after LDM's entries.
        code, out = _check([*RENDERED, f"{LOG4J}=/elsewhere.xml"], "{}")
        self.assertEqual(code, DEFECT, out)
        self.assertIn(f"{LOG4J} (x2)", out)

    def test_the_virtual_host_in_custom_env_is_named(self):
        # The shape `meta` actually stores (LDM-#2315): a JSON string.
        polluted = json.dumps({VHOST: "aica-e2e.demo", "TEST_SECRET": "s"})
        code, out = _check(RENDERED, polluted)
        self.assertEqual(code, DEFECT, out)
        self.assertIn(VHOST, out)
        self.assertNotIn("TEST_SECRET", out)

    def test_a_dict_custom_env_is_read_too(self):
        code, out = _check(RENDERED, {VHOST_SYNC: "true"})
        self.assertEqual(code, DEFECT, out)

    def test_values_are_never_printed(self):
        polluted = json.dumps({VHOST: "aica-e2e.demo"})
        _, out = _check([*RENDERED, f"{VHOST}=aica-e2e.demo"], polluted)
        self.assertNotIn("aica-e2e.demo", out)


class ACleanCopyPasses(unittest.TestCase):
    def test_a_declared_key_ldm_never_renders(self):
        code, out = _check(RENDERED, json.dumps({"TEST_SECRET": "s"}))
        self.assertEqual(code, CLEAN, out)

    def test_empty_and_absent_custom_env(self):
        for custom_env in ("", None, ...):
            with self.subTest(custom_env=custom_env):
                code, out = _check(RENDERED, custom_env)
                self.assertEqual(code, CLEAN, out)


class NothingAssertedIsNeverAPass(unittest.TestCase):
    def test_missing_meta(self):
        code, out = _check(RENDERED, write_meta=False)
        self.assertEqual(code, CANNOT_READ, out)

    def test_a_mapping_environment_cannot_be_counted(self):
        # A parsed mapping has already dropped its duplicates.
        code, out = _check({"LIFERAY_HOME": "/opt/liferay"}, "{}")
        self.assertEqual(code, CANNOT_READ, out)


class ThePackageIsReadWithoutExtracting(unittest.TestCase):
    """`--package`: the capture side, which the copy checks cannot see once
    the #2370 restore strip cleans a dirty package on import."""

    def _ldmp(self, root, members):
        # The shape `cmd_package` writes: a w:gz tar of the snapshot dir with
        # `arcname=item.name`, so `meta` is a top-level member.
        path = Path(root) / "p.ldmp"
        with tarfile.open(path, "w:gz") as tar:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return path

    def _meta(self, custom_env):
        return json.dumps({"project_name": "p", "custom_env": custom_env}).encode()

    def _check(self, members):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cwd:
            package = self._ldmp(tmp, members)
            result = subprocess.run(
                [sys.executable, str(CHECKER), "--package", str(package)],
                capture_output=True,
                text=True,
                check=False,
                cwd=cwd,
            )
            # Streamed into memory: nothing lands beside it or in the cwd.
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["p.ldmp"])
            self.assertEqual(list(Path(cwd).iterdir()), [])
            return result.returncode, result.stdout + result.stderr

    def test_a_polluted_package_is_named(self):
        code, out = self._check(
            [("meta", self._meta(json.dumps({VHOST: "aica-e2e.demo"})))]
        )
        self.assertEqual(code, DEFECT, out)
        self.assertIn(VHOST, out)

    def test_meta_is_found_wherever_it_sits(self):
        filler = ("files.tar.gz", os.urandom(256 * 1024))
        for clean, custom_env in ((True, {"TEST_SECRET": "s"}), (False, {LOG4J: "x"})):
            with self.subTest(clean=clean):
                code, out = self._check([filler, ("meta", self._meta(custom_env))])
                self.assertEqual(code, CLEAN if clean else DEFECT, out)

    def test_no_top_level_meta_is_never_a_pass(self):
        nested = ("snapshot/meta", self._meta({VHOST: "x"}))
        code, out = self._check([("db.sql", b"--"), nested])
        self.assertEqual(code, CANNOT_READ, out)


BUNDLE = "com.liferay.custom.dxp.routes.domain.jar"
SKIPPED = 3


def _gz_tar(members) -> bytes:
    """A gzipped tar of (name, data) members; data None makes a directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class ThePackageCarriesNoLdmBundle(unittest.TestCase):
    """`--package-bundles` (LDM-#2359): v2.26.9's `ldm package` shipped LDM's
    portal-workaround jar inside AICA's .ldmp, because a snapshot archived the
    whole `osgi` tree. The package is the shape `cmd_package` writes: a w:gz
    tar of the snapshot directory, holding `meta` and the nested
    `files.tar.gz` payload whose `osgi` block `tar.add` writes contiguously."""

    def _check(self, payload_members, *, osgi_modules="", source_has=True, order=None):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cwd:
            root = Path(tmp)
            source = root / "source"
            (source / "osgi" / "modules").mkdir(parents=True)
            if source_has:
                (source / "osgi" / "modules" / BUNDLE).write_bytes(b"PK")
            meta = json.dumps(
                {"project_name": "p", "osgi_modules": osgi_modules}
            ).encode()
            members = {"meta": meta, "files.tar.gz": _gz_tar(payload_members)}
            names = order or ["files.tar.gz", "meta"]
            package = root / "p.ldmp"
            package.write_bytes(_gz_tar([(n, members[n]) for n in names]))
            result = subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    "--package-bundles",
                    str(package),
                    str(source),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=cwd,
            )
            # Streamed into memory: nothing is extracted beside it or in the cwd.
            self.assertEqual(
                sorted(p.name for p in root.iterdir()), ["p.ldmp", "source"]
            )
            self.assertEqual(list(Path(cwd).iterdir()), [])
            return result.returncode, result.stdout + result.stderr

    CLEAN_PAYLOAD = (
        ("files", None),
        ("files/portal-ext.properties", b"x=1"),
        ("osgi", None),
        ("osgi/modules", None),
        ("osgi/modules/my.module.jar", b"PK"),
        ("data", None),
        ("data/document_library/blob", os.urandom(64 * 1024)),
    )

    def test_a_clean_package_from_a_source_holding_the_bundle_passes(self):
        code, out = self._check(self.CLEAN_PAYLOAD, osgi_modules="my.module.jar")
        self.assertEqual(code, CLEAN, out)
        self.assertIn("ASSERTED", out)
        self.assertIn(BUNDLE, out)

    def test_the_bundle_in_the_payload_is_named(self):
        payload = [*self.CLEAN_PAYLOAD[:5], (f"osgi/modules/{BUNDLE}", b"PK")]
        for order in (["files.tar.gz", "meta"], ["meta", "files.tar.gz"]):
            with self.subTest(order=order):
                code, out = self._check(payload, order=order)
                self.assertEqual(code, DEFECT, out)
                self.assertIn(f"files.tar.gz!osgi/modules/{BUNDLE}", out)

    def test_the_bundle_in_the_manifest_alone_is_named(self):
        code, out = self._check(
            self.CLEAN_PAYLOAD, osgi_modules=f"my.module.jar,{BUNDLE}"
        )
        self.assertEqual(code, DEFECT, out)
        self.assertIn(f"meta osgi_modules: {BUNDLE}", out)

    def test_a_bundle_is_a_failure_even_when_the_source_had_none(self):
        payload = [
            ("osgi", None),
            ("osgi/modules", None),
            (f"osgi/modules/{BUNDLE}", b"PK"),
        ]
        code, out = self._check(payload, source_has=False)
        self.assertEqual(code, DEFECT, out)

    def test_a_source_that_never_had_the_bundle_is_a_skip_not_a_pass(self):
        code, out = self._check(self.CLEAN_PAYLOAD, source_has=False)
        self.assertEqual(code, SKIPPED, out)
        self.assertIn("SKIPPED (not run)", out)
        self.assertNotIn("ASSERTED", out)

    def test_a_bundle_named_elsewhere_in_the_tree_is_not_the_defect(self):
        # Only `osgi/modules` is where `ldm run` places it and the filter acts.
        payload = [*self.CLEAN_PAYLOAD, (f"deploy/{BUNDLE}", b"PK")]
        code, out = self._check(payload)
        self.assertEqual(code, CLEAN, out)

    def test_a_package_without_a_payload_is_never_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source").mkdir()
            package = root / "p.ldmp"
            package.write_bytes(_gz_tar([("meta", b"{}")]))
            result = subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    "--package-bundles",
                    str(package),
                    str(root / "source"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, CANNOT_READ, result.stdout)

    def test_a_missing_source_is_never_a_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "p.ldmp"
            package.write_bytes(
                _gz_tar([("meta", b"{}"), ("files.tar.gz", _gz_tar([]))])
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    "--package-bundles",
                    str(package),
                    str(Path(tmp) / "nope"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, CANNOT_READ, result.stdout)

    def test_the_prefixes_match_the_shipped_workarounds(self):
        """The checker cannot import ldm_core; a new workaround must reach it."""
        from ldm_core.runtime.portal_workarounds import RETIRED_PREFIXES, WORKAROUNDS

        spec = importlib.util.spec_from_file_location("checker", CHECKER)
        assert spec is not None, f"could not load {CHECKER}"
        assert spec.loader is not None, f"no loader for {CHECKER}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(
            set(module.LDM_BUNDLE_PREFIXES),
            {w.prefix for w in WORKAROUNDS} | set(RETIRED_PREFIXES),
        )


def _run_checker(*args):
    result = subprocess.run(
        [sys.executable, str(CHECKER), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


class ThePackageKindIsAsserted(unittest.TestCase):
    """`--stateless-package` / `--stateful-manifest` (LDM-#2493, LDM-#2445).

    The archives are the shape `cmd_package` writes: a w:gz tar of the staging
    or snapshot directory, holding `meta`, the nested `files.tar.gz` payload
    and its checksum -- plus `database.sql` for a stateful package."""

    STATELESS_META: ClassVar[dict] = {
        "package_kind": "stateless",
        "container_hostname": None,
    }
    STATELESS_PAYLOAD = (
        ("client-extensions", None),
        ("client-extensions/my-cx/LCP.json", b"{}"),
        ("osgi", None),
        ("osgi/client-extensions", None),
        ("osgi/client-extensions/my-cx.zip", b"PK"),
        ("osgi/modules/my.module.jar", b"PK"),
        ("files/portal-ext.properties", b"x=1"),
        (".ldm/fragment-overrides.json", b"{}"),
    )

    def _package(self, tmp, meta, payload, extra=()):
        package = Path(tmp) / "p.ldmp"
        members = [
            ("meta", json.dumps(meta).encode()),
            ("files.tar.gz", _gz_tar(payload)),
            ("files.tar.gz.sha256", b"0" * 64),
            *extra,
        ]
        package.write_bytes(_gz_tar(members))
        return package

    def _stateless(self, meta=None, payload=None, extra=()):
        with tempfile.TemporaryDirectory() as tmp:
            package = self._package(
                tmp,
                self.STATELESS_META if meta is None else meta,
                self.STATELESS_PAYLOAD if payload is None else payload,
                extra,
            )
            code, out = _run_checker("--stateless-package", package)
            # Streamed: nothing is extracted beside the package.
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["p.ldmp"])
            return code, out

    def _stateful(self, meta):
        with tempfile.TemporaryDirectory() as tmp:
            package = self._package(
                tmp, meta, [("data", None)], extra=[("database.sql", b"--")]
            )
            return _run_checker("--stateful-manifest", package)

    def test_a_stateless_package_passes(self):
        code, out = self._stateless()
        self.assertEqual(code, CLEAN, out)
        self.assertIn("ASSERTED", out)

    def test_a_database_dump_is_named(self):
        code, out = self._stateless(extra=[("database.sql", b"-- dump")])
        self.assertEqual(code, DEFECT, out)
        self.assertIn("'database.sql'", out)

    def test_snapshot_state_in_the_payload_is_named(self):
        for member in (
            "data/document_library/blob",
            "osgi/state/org.eclipse.osgi/x",
            "search_backup/index-0",
            "routes/default/x",
            "logs/liferay.log",
            "files/tomcat/x",
        ):
            with self.subTest(member=member):
                code, out = self._stateless(
                    payload=[*self.STATELESS_PAYLOAD, (member, b"x")]
                )
                self.assertEqual(code, DEFECT, out)
                self.assertIn(f"files.tar.gz!{member}", out)

    def test_a_manifest_not_declaring_stateless_fails(self):
        for kind in (None, "stateful", "Stateless "):
            with self.subTest(kind=kind):
                meta = {**self.STATELESS_META, "package_kind": kind}
                code, out = self._stateless(meta=meta)
                self.assertEqual(code, DEFECT, out)

    def test_a_manifest_claiming_a_database_fails(self):
        meta = {**self.STATELESS_META, "includes_database": "true"}
        code, out = self._stateless(meta=meta)
        self.assertEqual(code, DEFECT, out)
        self.assertIn("includes_database", out)

    def test_a_stateful_manifest_passes_with_a_null_hostname(self):
        # LDM-#2445: the key must be present; null is the test project's value.
        code, out = self._stateful({"container_hostname": None})
        self.assertEqual(code, CLEAN, out)

    def test_a_stateful_package_declaring_stateless_fails(self):
        # The regression the stateful read exists for: every package stateless.
        code, out = self._stateful(
            {"container_hostname": None, "package_kind": "stateless"}
        )
        self.assertEqual(code, DEFECT, out)
        self.assertIn("package_kind", out)

    def test_a_stateful_manifest_without_the_hostname_key_fails(self):
        code, out = self._stateful({"package_kind": None})
        self.assertEqual(code, DEFECT, out)
        self.assertIn("container_hostname", out)

    def test_an_unreadable_package_is_never_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            no_meta = Path(tmp) / "a.ldmp"
            no_meta.write_bytes(_gz_tar([("files.tar.gz", _gz_tar([]))]))
            no_payload = Path(tmp) / "b.ldmp"
            no_payload.write_bytes(_gz_tar([("meta", b"{}")]))
            garbage = Path(tmp) / "c.ldmp"
            garbage.write_bytes(b"not a tar")
            for mode, package in (
                ("--stateful-manifest", no_meta),
                ("--stateless-package", no_meta),
                ("--stateless-package", no_payload),
                ("--stateless-package", garbage),
                ("--stateful-manifest", garbage),
            ):
                with self.subTest(mode=mode, package=package.name):
                    code, out = _run_checker(mode, package)
                    self.assertEqual(code, CANNOT_READ, out)

    def test_the_allowed_payload_matches_what_archive_writes(self):
        """The checker cannot import ldm_core; a new stateless tree must reach it."""
        from ldm_core.snapshot.archive import STATELESS_GLOBS, STATELESS_TREES

        spec = importlib.util.spec_from_file_location("checker", CHECKER)
        assert spec is not None, f"could not load {CHECKER}"
        assert spec.loader is not None, f"no loader for {CHECKER}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(set(module.STATELESS_PAYLOAD_TREES), set(STATELESS_TREES))
        self.assertEqual(
            set(module.STATELESS_PAYLOAD_FILES),
            {f"{d}/{pattern}" for d, pattern in STATELESS_GLOBS},
        )


#: Per half: the text each structural assertion locates.
HALVES = {
    "verify_e2e_refactor.sh": {
        "package_call": 'roundtrip_env_check "The package ${PROJECT_NAME}.ldmp" --package "$ROUNDTRIP_PKG"',
        "copy_call": '"The imported copy \'${ROUNDTRIP_PROJECT}\'" "$ROUNDTRIP_COPY_DIR"',
        "copy_resolve": '"$LDM_CMD" list --json',
        "copy_guessed": "${LDM_WORKSPACE}/${ROUNDTRIP_PROJECT}",
        "import": 'import "$ROUNDTRIP_PKG"',
        "boot": 'run "$ROUNDTRIP_PROJECT"',
        "booted_gate": 'if [ "$ROUNDTRIP_BOOTED" = true ]; then',
        "helper": ("roundtrip_env_check() {", "\n}\n"),
        "round_trip_flag_cleared": "ROUNDTRIP_OK=false",
        "final_gate": '[ "$ROUNDTRIP_ENV_OK" != true ]',
        "reset": 'reset "$ROUNDTRIP_PROJECT" all',
        "bundle_call": '--package-bundles "$ROUNDTRIP_PKG" "${LDM_WORKSPACE}/${PROJECT_NAME}"',
        "bundle_skip": '"$ROUNDTRIP_BUNDLE_RC" -eq 3',
        "stateful_kind_call": '--stateful-manifest "$ROUNDTRIP_PKG"',
        "stateless_pack": 'package "$PROJECT_NAME" --stateless',
        "stateless_kind_call": "--stateless-package",
        "stateless_cleanup": 'rm -rf "$ROUNDTRIP_STATELESS_DIR"',
        "stop": 'stop "$PROJECT_NAME"',
    },
    "verify_e2e_refactor.ps1": {
        "package_call": '-CheckerArgs @("--package", $roundTripPkg)',
        "copy_call": "-CheckerArgs @($roundTripCopyDir)",
        "copy_resolve": "& $LDM_CMD list --json",
        "copy_guessed": "Join-Path $LDM_WORKSPACE $ROUNDTRIP_PROJECT",
        "import": "import $roundTripPkg",
        "boot": "run $ROUNDTRIP_PROJECT",
        "booted_gate": "if ($roundTripBooted) {",
        "helper": ("function Invoke-RoundTripEnvCheck {", "\n    }\n"),
        "round_trip_flag_cleared": "$roundTripOk = $false",
        "final_gate": "if (-not $script:roundTripEnvOk) {",
        "reset": "reset $ROUNDTRIP_PROJECT all",
        "bundle_call": "--package-bundles $roundTripPkg (Join-Path $LDM_WORKSPACE $PROJECT_NAME)",
        "bundle_skip": "$bundleRc -eq 3",
        "stateful_kind_call": '@("--stateful-manifest", $roundTripPkg)',
        "stateless_pack": "package $PROJECT_NAME --stateless",
        "stateless_kind_call": "--stateless-package",
        "stateless_cleanup": "Remove-Item -Recurse -Force $roundTripStatelessDir",
        "stop": "stop $PROJECT_NAME",
    },
}


class BothHalvesRunItInTheRoundTrip(unittest.TestCase):
    """The checker proves nothing unless both suites call it, on the right
    artefact, at the right point -- and a leak must not skip what follows."""

    def _section(self, name):
        text = (REPO / "scripts" / name).read_text(encoding="utf-8")
        begin = text.index(">> Verifying a .ldmp round trip")
        return text[begin : text.index("LDM-#2372: an env leak fails", begin) + 400]

    def test_both_halves(self):
        for name, marks in HALVES.items():
            with self.subTest(script=name):
                section = self._section(name)
                for key in (
                    "package_call",
                    "copy_call",
                    "copy_resolve",
                    "booted_gate",
                    "final_gate",
                ):
                    self.assertTrue(marks[key] in section, f"{name}: no {key}")
                # LDM-#2372: the copy's directory comes from `ldm list --json`.
                # `ldm import` places it beside the working directory, so the
                # guessed LDM_WORKSPACE path made the check exit 4 on every
                # platform at v2.26.10-pre.2 -- and this test pinned the guess.
                at = section.index
                copy_block = section[
                    section.rindex(
                        marks["booted_gate"], 0, at(marks["copy_call"])
                    ) : at(marks["copy_call"]) + len(marks["copy_call"])
                ]
                self.assertNotIn(marks["copy_guessed"], copy_block, name)
                self.assertLess(at(marks["booted_gate"]), at(marks["copy_resolve"]))
                self.assertLess(at(marks["copy_resolve"]), at(marks["copy_call"]))
                # The package is checked before import can strip anything.
                self.assertLess(at(marks["package_call"]), at(marks["import"]))
                # The copy is checked after the boot that renders its compose,
                # and only when that boot happened.
                self.assertLess(at(marks["boot"]), at(marks["booted_gate"]))
                self.assertLess(at(marks["booted_gate"]), at(marks["copy_call"]))
                # The failing run is decided after the reset has had its turn.
                self.assertLess(at(marks["reset"]), at(marks["final_gate"]))

    def test_both_halves_check_the_package_for_ldm_bundles(self):
        """LDM-#2359: on the package, before import, against the SOURCE
        project; a skip is branched on; a bundle never clears the round-trip
        flag, which gates the reset check (LDM-#2118)."""
        for name, marks in HALVES.items():
            with self.subTest(script=name):
                section = self._section(name)
                at = section.index
                self.assertIn(marks["bundle_call"], section)
                self.assertLess(at(marks["package_call"]), at(marks["bundle_call"]))
                self.assertLess(at(marks["bundle_call"]), at(marks["import"]))
                block = section[
                    at(marks["bundle_call"]) : at(
                        "The sidecar checksum is the artefact"
                    )
                ]
                self.assertIn(marks["bundle_skip"], block)
                self.assertNotIn(marks["round_trip_flag_cleared"], block)

    def test_both_halves_check_the_package_kinds(self):
        """LDM-#2493/#2445: the stateful package's manifest is read, and a
        --stateless package is packed and read, while the source still exists
        (before the stop) and before import; neither clears the round-trip
        flag, so the round trip proceeds; the stateless package is removed."""
        for name, marks in HALVES.items():
            with self.subTest(script=name):
                section = self._section(name)
                at = section.index
                for key in (
                    "stateful_kind_call",
                    "stateless_pack",
                    "stateless_kind_call",
                    "stateless_cleanup",
                ):
                    self.assertIn(marks[key], section, f"{name}: no {key}")
                self.assertLess(at(marks["stateless_pack"]), at(marks["import"]))
                self.assertLess(at(marks["stateless_pack"]), at(marks["stop"]))
                self.assertLess(
                    at(marks["stateless_pack"]), at(marks["stateless_kind_call"])
                )
                self.assertLess(
                    at(marks["stateless_kind_call"]), at(marks["stateless_cleanup"])
                )
                block = section[
                    at(marks["stateful_kind_call"]) : at(marks["stateless_cleanup"])
                ]
                self.assertNotIn(marks["round_trip_flag_cleared"], block)

    def test_a_leak_never_clears_the_round_trip_flag(self):
        # The flag gates `ldm reset all` (LDM-#2118); a leak must not skip it.
        for name, marks in HALVES.items():
            with self.subTest(script=name):
                section = self._section(name)
                opener, closer = marks["helper"]
                body_at = section.index(opener)
                body = section[body_at : section.index(closer, body_at)]
                self.assertFalse(
                    marks["round_trip_flag_cleared"] in body,
                    f"{name}: the env-leak helper clears the round-trip flag",
                )


if __name__ == "__main__":
    unittest.main()
