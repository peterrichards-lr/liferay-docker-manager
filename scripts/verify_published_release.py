#!/usr/bin/env python3
"""Verify a PUBLISHED release by downloading it (LDM-#1768).

CI proves the commit builds. It does not prove the release is usable, and the
gap between those two is where four separate defects hid during the v2.22.0
cycle -- every one of them found by a human downloading the artifact, none by
any check:

  * `v2.22.0-pre.7` published with **no installer**. Two commits had been lost
    to a merged-PR push, so `ci.yml` never gained the step. CI was green: there
    was nothing new to test.
  * The verification bundle shipped without `common/`, so the suite ran
    degraded -- LDM only warns -- and reported success having applied neither
    the activation key nor the search configuration (LDM-#1718).
  * `MANIFEST.txt` then asserted it *did* carry the activation key, while
    listing contents that showed none (LDM-#1733). The test that should have
    caught it passed, because `collect()` walks the filesystem and a
    developer's checkout has the key sitting there untracked -- it verified the
    developer's machine, not the artifact users download.
  * The installer was absent from `checksums.txt`, so the one file a verifier
    runs before anything else was itself unverifiable.

Each was invisible to CI by construction, and each is trivial to catch once you
hold the published bytes. That is all this does: fetch the release, open it, and
assert what a user would find.

Run against any tag:

    python3 scripts/verify_published_release.py --tag v2.23.0-pre.2
    python3 scripts/verify_published_release.py --tag v2.23.0-pre.2 --json

Exits non-zero when the release is not usable, so it can gate a workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess  # nosec B404 - shells out to `gh` to download the release
import sys
import tempfile
import zipfile
from pathlib import Path

# Standard library only, deliberately. This runs inside `ci.yml`'s release job
# immediately after the release publishes, where nothing has been pip-installed
# -- importing `ldm_core` would pull in `requests` and fail there (LDM-#1774).
REPO = "peterrichards-lr/liferay-docker-manager"

# Everything `ci.yml` claims to publish. Keep in step with its `files:` block;
# a name here that the release lacks is the pre.7 failure, and the whole point
# is that nothing else notices.
REQUIRED_ASSETS = (
    "ldm-linux",
    "ldm-macos-arm64",
    "ldm-macos-x86_64",
    "ldm-windows.exe",
    "checksums.txt",
    "LICENSE",
    "verification-bundle.zip",
    "install_verification.sh",
    "install_verification.ps1",
    "compatibility.json",
)

# What the suite cannot run without. `common/` is the one the bundle exists to
# carry: without it LDM only WARNS, so the run completes, exits 0 and reports
# success having verified a smaller system than it claims.
REQUIRED_BUNDLE_MEMBERS = (
    "verify_e2e_refactor.sh",
    "verify_e2e_refactor.ps1",
    "MANIFEST.txt",
    "SHA256SUMS",
)

# Assets whose integrity a verifier depends on before running anything.
MUST_BE_CHECKSUMMED = (
    "ldm-linux",
    "ldm-macos-arm64",
    "ldm-macos-x86_64",
    "ldm-windows.exe",
    "verification-bundle.zip",
    "install_verification.sh",
    "install_verification.ps1",
)


class Findings:
    """Collects every problem rather than stopping at the first.

    A release with three faults should report three, not send someone round
    the loop once per fault.
    """

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)

    @property
    def ok(self) -> bool:
        return not self.failures


def download_release(tag: str, dest: Path) -> Findings:
    findings = Findings()
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["gh", "release", "download", tag, "--repo", REPO, "--dir", str(dest)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        # A missing `gh` is a failure of this checker, not the release under
        # test -- but it must still be reported as a finding (valid JSON,
        # non-zero exit) rather than an uncaught traceback on stdout, which is
        # what silently turned into three skipped assertions (LDM-#1869).
        findings.fail(
            f"required tool not found: {exc.filename or 'gh'} -- install the "
            f"GitHub CLI (https://cli.github.com/) so this checker can run"
        )
        return findings
    if result.returncode != 0:
        findings.fail(
            f"could not download release {tag}: "
            f"{(result.stderr or result.stdout).strip()[:200]}"
        )
    return findings


def check_assets_present(dest: Path, findings: Findings) -> None:
    present = {p.name for p in dest.iterdir() if p.is_file()}
    for asset in REQUIRED_ASSETS:
        if asset not in present:
            findings.fail(f"asset missing from the release: {asset}")
    extra = present - set(REQUIRED_ASSETS)
    if extra:
        findings.note(f"additional assets present: {', '.join(sorted(extra))}")


def check_checksums(dest: Path, findings: Findings) -> None:
    sums_file = dest / "checksums.txt"
    if not sums_file.is_file():
        return  # already reported as a missing asset

    recorded: dict[str, str] = {}
    for line in sums_file.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            recorded[Path(parts[-1]).name] = parts[0].lower()

    for asset in MUST_BE_CHECKSUMMED:
        if asset not in recorded:
            findings.fail(
                f"{asset} is not in checksums.txt, so it cannot be verified before use"
            )
            continue
        path = dest / asset
        if not path.is_file():
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != recorded[asset]:
            findings.fail(
                f"{asset} does not match its recorded checksum "
                f"(recorded {recorded[asset][:12]}…, actual {actual[:12]}…)"
            )


def check_bundle(dest: Path, findings: Findings) -> None:
    bundle = dest / "verification-bundle.zip"
    if not bundle.is_file():
        return  # already reported

    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        manifest = (
            archive.read("MANIFEST.txt").decode("utf-8", errors="replace")
            if "MANIFEST.txt" in names
            else ""
        )

    for member in REQUIRED_BUNDLE_MEMBERS:
        if member not in names:
            findings.fail(f"verification-bundle.zip is missing {member}")

    if not any(n.startswith("common/") for n in names):
        findings.fail(
            "verification-bundle.zip carries no common/ -- the suite would run "
            "without the Elasticsearch configuration and report success anyway "
            "(LDM-#1718)"
        )

    # LDM-#1733: the manifest asserted an activation key the bundle could not
    # contain. The claim and the contents must agree, whichever way round.
    has_key = any("activation-key" in n for n in names)
    declares_absent = "NOT INCLUDED: the DXP activation key" in manifest
    if has_key and declares_absent:
        findings.fail(
            "MANIFEST.txt says the activation key is absent, but the bundle "
            "contains one"
        )
    if not has_key and not declares_absent:
        findings.fail(
            "the bundle carries no activation key and MANIFEST.txt does not "
            "say so -- a verifier is told the gap is closed when it is open "
            "(LDM-#1733)"
        )


def check_bundle_self_checksums(dest: Path, findings: Findings) -> None:
    """The bundle's own SHA256SUMS must cover and match its members.

    Members are read straight out of the archive and never written to disk.
    That removes the Zip Slip question entirely rather than guarding against it
    -- this archive was DOWNLOADED, and a verification tool runs against
    artifacts it did not build, so the safest extraction is the one that does
    not happen. It also keeps this script to the standard library, which is
    what lets the release job run it without installing anything.
    """
    bundle = dest / "verification-bundle.zip"
    if not bundle.is_file():
        return

    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        if "SHA256SUMS" not in names:
            return  # already reported as a missing member

        sums = archive.read("SHA256SUMS").decode("utf-8", errors="replace")
        for line in sums.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            expected, name = parts[0].lower(), parts[-1].lstrip("*./")
            if name not in names:
                findings.fail(f"SHA256SUMS names {name}, which is not in the bundle")
                continue
            actual = hashlib.sha256(archive.read(name)).hexdigest()
            if actual != expected:
                findings.fail(f"bundle member {name} does not match SHA256SUMS")


def verify(tag: str) -> Findings:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        findings = download_release(tag, dest)
        if not findings.ok:
            return findings

        check_assets_present(dest, findings)
        check_checksums(dest, findings)
        check_bundle(dest, findings)
        check_bundle_self_checksums(dest, findings)
        return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Release tag, e.g. v2.23.0-pre.2")
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable output"
    )
    args = parser.parse_args()

    findings = verify(args.tag)

    if args.json:
        print(
            json.dumps(
                {
                    "tag": args.tag,
                    "ok": findings.ok,
                    "failures": findings.failures,
                    "notes": findings.notes,
                },
                indent=2,
            )
        )
    else:
        print(f"Verifying published release {args.tag}")
        for note in findings.notes:
            print(f"  note: {note}")
        if findings.ok:
            print("✅ The published release is complete and internally consistent.")
        else:
            print(f"❌ {len(findings.failures)} problem(s) with the PUBLISHED release:")
            for failure in findings.failures:
                print(f"   - {failure}")
            print(
                "\n   CI being green does not cover this: these are properties of "
                "the artifact, not of the build."
            )

    return 0 if findings.ok else 1


if __name__ == "__main__":
    sys.exit(main())
