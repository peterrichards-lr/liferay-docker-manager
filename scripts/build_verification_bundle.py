#!/usr/bin/env python3
"""Assemble the verification bundle published with each release (LDM-#1718).

`docs/TESTING.md` told a verifier to fetch a single file:

    curl -fsSL ".../${LDM_REF}/scripts/verify_e2e_refactor.sh" -o verify_e2e_refactor.sh

But LDM looks for a `common/` folder beside it, holding the Elasticsearch
and the Elasticsearch configuration. When it is absent, `handlers/config.py`
emits a **warning**, not a failure:

    Global or local 'common/' folder not found. Some baseline assets may be missing.

So the suite ran to completion, exited 0 and reported success -- having never
applied the activation key or the search configuration. Every assertion
genuinely passed; they just tested a smaller system than the report claimed.

That is the same family as the defect LDM-#1662 fixed, where arms swallowed the
exit status and reported success while uploading a failure report --
"manufacturing coverage that did not exist". This one is quieter: a *degraded*
run reported as passed rather than a *failed* one.

The bundle also removes a second hazard in that command. `${LDM_REF}` is chosen
by the verifier, so the script could come from a different commit than the
binary under test -- v2.21's suite against a v2.22 binary, entirely by accident.
A bundle published per tag makes the pairing structural.

WHAT GOES IN, AND WHY EACH
    verify_e2e_refactor.sh / .ps1   both halves, so one bundle serves either
                                    platform and the pair cannot drift apart in
                                    a verifier's download
    common/                         the actual gap: Elasticsearch
                                    and session configuration
    fragment_override_harness.py     the optional LDM-#1618 arm, so it travels
                                    with the suite it belongs to
    MANIFEST.txt                    what this bundle is, which tag it came from,
                                    and what each member is for
    SHA256SUMS                      so the contents can be checked independently

Usage:
    python3 scripts/build_verification_bundle.py --tag v2.22.0 --out dist/
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files the bundle must contain. A missing entry is a hard error: shipping a
# bundle without `common/` would recreate the exact hole this exists to close,
# and silently.
REQUIRED_FILES = (
    "scripts/verify_e2e_refactor.sh",
    "scripts/verify_e2e_refactor.ps1",
)
REQUIRED_DIRS = ("common",)
OPTIONAL_FILES = ("scripts/fragment_override_harness.py",)

BUNDLE_NAME = "verification-bundle.zip"


def collect(root: Path) -> list[tuple[Path, str]]:
    """Every (source, archive-name) pair the bundle carries.

    Raises if a required member is absent -- see REQUIRED_* above.
    """
    members: list[tuple[Path, str]] = []
    missing: list[str] = []

    for rel in REQUIRED_FILES:
        path = root / rel
        if path.is_file():
            members.append((path, Path(rel).name))
        else:
            missing.append(rel)

    for rel in REQUIRED_DIRS:
        directory = root / rel
        if not directory.is_dir():
            missing.append(f"{rel}/")
            continue
        found = [p for p in sorted(directory.rglob("*")) if p.is_file()]
        if not found:
            missing.append(f"{rel}/ (present but empty)")
            continue
        for path in found:
            members.append((path, f"{rel}/{path.relative_to(directory).as_posix()}"))

    if missing:
        raise FileNotFoundError(
            "the verification bundle is missing required members: "
            + ", ".join(missing)
            + ". Publishing without them would ship a bundle that verifies less "
            "than it claims (LDM-#1718)."
        )

    for rel in OPTIONAL_FILES:
        path = root / rel
        if path.is_file():
            members.append((path, Path(rel).name))

    return members


def manifest(tag: str, members: list[tuple[Path, str]]) -> str:
    lines = [
        "LDM verification bundle",
        f"Tag: {tag}",
        "",
        "Run the suite for YOUR platform from the directory you unzip this into,",
        "so that `common/` sits beside the script:",
        "",
        "    bash verify_e2e_refactor.sh            # Linux / macOS",
        "    pwsh -File verify_e2e_refactor.ps1     # Windows",
        "",
        "Use the binary from this same release. The bundle and the binary are",
        "published together so the pair cannot drift -- fetching the script by",
        "branch ref, as the old instructions did, could pair one release's suite",
        "with another release's binary.",
        "",
        "`common/` carries the Elasticsearch and session configuration. Without",
        "it LDM only WARNS and the suite still reports success, having verified",
        "a smaller system than it claims. Keep it beside the script.",
        "",
    ]
    # LDM-#1733: the manifest used to assert that the activation key travelled
    # in this bundle. It cannot: `.gitignore` excludes `common/activation-key-*.xml`,
    # correctly -- it is licensed and must not be published -- so no CI
    # checkout has one to package. Saying otherwise told a verifier the
    # licensed half was covered when it was not, which is the same
    # reported-better-than-reality failure the bundle exists to stop.
    if any(arc.startswith("common/activation-key-") for _src, arc in members):
        lines.append("The DXP activation key is included.")
    else:
        lines += [
            "NOT INCLUDED: the DXP activation key. `common/activation-key-*.xml`",
            "is gitignored -- it is licensed and cannot be published -- so no",
            "release bundle can carry one. Liferay will run unlicensed and LDM",
            "will only warn. Copy your own key into `common/` before running:",
            "",
            "    cp /path/to/activation-key-*.xml ./common/",
        ]
    lines += [
        "",
        "Contents:",
    ]
    lines.extend(f"  {arc}" for _src, arc in sorted(members, key=lambda m: m[1]))
    return "\n".join(lines) + "\n"


def checksums(members: list[tuple[Path, str]]) -> str:
    lines = []
    for src, arc in sorted(members, key=lambda m: m[1]):
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        lines.append(f"{digest}  {arc}")
    return "\n".join(lines) + "\n"


def build(tag: str, out_dir: Path, root: Path = REPO_ROOT) -> Path:
    members = collect(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / BUNDLE_NAME

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for src, arc in members:
            archive.write(src, arcname=arc)
        archive.writestr("MANIFEST.txt", manifest(tag, members))
        archive.writestr("SHA256SUMS", checksums(members))

    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="the release tag being built")
    parser.add_argument("--out", default="dist", help="directory to write the zip to")
    args = parser.parse_args()

    try:
        target = build(args.tag, Path(args.out))
    except FileNotFoundError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(target) as archive:
        count = len(archive.namelist())
    print(f"Built {target} ({count} members, {target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
