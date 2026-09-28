#!/usr/bin/env python3
"""Assert a client-extension test fixture has the shape real ones have.

LDM-#1975. Every boundary defect in the v2.26.0 cycle survived a 22-minute
gate that ran the relevant assertions and passed them. It passed because the
FIXTURE was the one shape the defect could not appear in -- not because the
assertions were absent or weak.

LDM-#1944 is the clearest case: 74 port assertions and a full routes-pairing
check all passed while the bug was live, because the fixture's LCP.json id
equalled its directory name. That equality collapses "mount the id" and "mount
the projectName" into the same string, so no assertion downstream of it can
tell which one LDM actually used.

So the invariants below are not taste. Each is measured against the real
corpus and each records a defect that a fixture violating it could not have
caught. The corpus is `ldm-cx-samples/client-extensions`: 17 directories, of
which 16 are client extensions. The odd one out, `common-theme-assets`, holds
shared source and carries no `client-extension.yaml` -- which is exactly what
makes it not an extension. The counts are over those 16:

    client-extension.yaml present        16/16   LDM-#1962
    LCP.json present                     16/16   LDM-#1962
    id differs from the directory        16/16   LDM-#1944  (0 have them equal)
    id is the directory, de-hyphenated   16/16   LDM-#1944
    a service declares its own port        4/4   LDM-#1996  (3001-3004, never 8080)

The port clause matters for the same reason as the id. 8080 is
`_resolve_container_port`'s fallback, so a fixture on 8080 yields the right
answer whether the declared port was read or the read failed -- which is
precisely what LDM-#1996 was.

This is a check on TEST DATA, not on LDM. It is shared by
`verify_e2e_refactor.sh` and `verify_e2e_refactor.ps1` deliberately: the two
suites had already drifted apart once, when a `.ps1` edit silently dropped a
check the `.sh` kept (LDM-#1982). One implementation cannot drift from itself.

Usage:
    check_cx_fixture_realism.py <label> <fixture_dir>

Exits 0 when the fixture is realistic, 1 when it is not, printing one
`ERROR:` line per violation.
"""

import json
import sys
from pathlib import Path

# `_resolve_container_port` (ldm_core/handlers/composer.py) returns this when
# the extension declares nothing. A fixture sitting on it cannot distinguish a
# successful read from a failed one.
_RESOLVER_FALLBACK_PORT = 8080


def _check_identifiers(lcp, folder, fails):
    """The id/directory relationship, which is what LDM-#1944 turned on."""
    ext_id = lcp.get("id")
    if not ext_id:
        fails.append("LCP.json declares no id")
        return
    if ext_id == folder:
        fails.append(
            f"LCP.json id ({ext_id!r}) EQUALS the directory name. Zero of the "
            "16 real samples do this, and it is one of the two conditions "
            "under which LDM-#1944 cannot manifest: it makes mounting the id "
            "and mounting the projectName produce the same path, so no "
            "assertion downstream can tell which one LDM used"
        )
    elif ext_id != folder.replace("-", ""):
        fails.append(
            f"LCP.json id ({ext_id!r}) is not the directory with its hyphens "
            f"dropped (expected {folder.replace('-', '')!r}). That is the "
            "relationship in 16 of 16 real samples"
        )


def _check_service_port(lcp, fixture_dir, fails):
    """A service must declare the port it listens on, and it must not be 8080.

    Only a service resolves a container port at all, and a Dockerfile is what
    makes an extension one -- together with `kind != "Job"`.
    """
    if not (fixture_dir / "Dockerfile").is_file() or lcp.get("kind") == "Job":
        return

    declared = [
        entry.get("port")
        for entry in (lcp.get("ports") or [])
        if isinstance(entry, dict) and entry.get("port")
    ]
    target = (lcp.get("loadBalancer") or {}).get("targetPort")

    if not declared and not target:
        fails.append(
            "a service fixture that declares no container port, in neither "
            "ports[] nor loadBalancer.targetPort. All four real service "
            "samples declare both, and a fixture without them exercises only "
            "_resolve_container_port's fallback"
        )
    elif _RESOLVER_FALLBACK_PORT in declared or target == _RESOLVER_FALLBACK_PORT:
        fails.append(
            f"the container port is {_RESOLVER_FALLBACK_PORT}, which is "
            "exactly _resolve_container_port's fallback -- so this fixture "
            "cannot distinguish reading the declared port from failing to "
            "read it (LDM-#1996). The real samples use 3001-3004"
        )


def _check_build_output(lcp, fixture_dir, folder, fails):
    """The CX build output carries both identifiers, and Liferay reads it.

    `projectName` is the field `BaseConfigurationFactory` publishes as the
    `ext.lxc.liferay.com/projectName` label, which
    `RoutesPortalK8sConfigMapModifier` then names the routes directory from.
    """
    for cfg_path in sorted(fixture_dir.glob("*.client-extension-config.json")):
        try:
            cfg = json.loads(cfg_path.read_text())
        except ValueError as exc:
            fails.append(f"{cfg_path.name} is not valid JSON: {exc}")
            continue
        for block in cfg.values():
            if not isinstance(block, dict):
                continue
            if "projectName" in block and block["projectName"] != folder:
                fails.append(
                    f"{cfg_path.name}: projectName={block['projectName']!r} is "
                    f"not the directory name ({folder!r}). Liferay names the "
                    "routes tree from this field"
                )
            if "projectId" in block and block["projectId"] != lcp.get("id"):
                fails.append(
                    f"{cfg_path.name}: projectId={block['projectId']!r} does "
                    f"not match the LCP.json id ({lcp.get('id')!r})"
                )


def check_fixture(fixture_dir):
    """Return a list of reasons the fixture is unrealistic; empty means good."""
    fixture_dir = Path(fixture_dir)
    folder = fixture_dir.name
    fails = []

    if not (fixture_dir / "client-extension.yaml").is_file():
        fails.append("no client-extension.yaml; all 16 real samples carry one")

    lcp_path = fixture_dir / "LCP.json"
    if not lcp_path.is_file():
        fails.append(
            "no LCP.json; all 16 real samples carry one, and LDM refuses to "
            "deploy a service extension without it (LDM-#1962)"
        )
        return fails

    try:
        lcp = json.loads(lcp_path.read_text())
    except ValueError as exc:
        fails.append(f"LCP.json is not valid JSON: {exc}")
        return fails

    _check_identifiers(lcp, folder, fails)
    _check_service_port(lcp, fixture_dir, fails)
    _check_build_output(lcp, fixture_dir, folder, fails)
    return fails


def main(argv):
    if len(argv) != 3:
        print("usage: check_cx_fixture_realism.py <label> <fixture_dir>")
        return 2
    label, fixture_dir = argv[1], argv[2]
    fails = check_fixture(fixture_dir)
    for failure in fails:
        print(f"ERROR: {label} fixture is unrealistic: {failure}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
