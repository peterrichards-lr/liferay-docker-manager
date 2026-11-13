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

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

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
