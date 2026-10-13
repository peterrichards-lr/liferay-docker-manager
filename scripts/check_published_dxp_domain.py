#!/usr/bin/env python3
"""Assert what Liferay PUBLISHED into routes/default/dxp, not merely that it exists.

LDM-#2154. Everything this suite knew about that tree was topological: that the
directories are scaffolded (LDM-#1928), that the mount is anchored at
`routes/default` (LDM-#1944), and that the files are readable by a client
extension (LDM-#1946). Three assertions walk those files and not one of them
reads a byte of their contents.

So a tree containing the wrong domain passed every check. Measured on
2026-10-13: a project created with `--host-name aica-e2e.demo` published

    com.liferay.lxc.dxp.main.domain = localhost:8080

and the suite would have called that a pass, because the file existed, sat
under the right mount, and was world-readable. An extension reading it is sent
to localhost, and the portal registers its OAuth redirect URIs against the same
wrong value -- which is how twelve OAuth specs fail with
`No redirectURI in http://localhost/o/oauth2/redirect matching origin https://...`.

**What this asserts, and what it deliberately does not.** Liferay writes these
files, not LDM, so asserting Liferay's internal consistency would make the
suite red for someone else's change. The only claim made here is the one LDM is
answerable for: *the domain Liferay published is the host name LDM was
configured with*. Every other value is recorded and not judged.

**Expect this to pass today and to start failing the moment the suite gains a
project with a custom host name.** The E2E runs as `localhost`, where
publishing `localhost` is correct, so this guards the mechanism rather than
exposing LDM-#2154. The custom-host-name variant belongs with the fix for
#2154, as the regression test observed failing first -- adding it here would
ship a suite that is red on every environment for a defect already tracked.

Shared by `verify_e2e_refactor.sh` and `verify_e2e_refactor.ps1` deliberately,
following `check_cx_fixture_realism.py`: the two halves have drifted before,
when a `.ps1` edit silently dropped a check the `.sh` kept (LDM-#1982). One
implementation cannot drift from itself, and a real module is covered by ruff
and mypy, which a heredoc never is.

Usage:
    check_published_dxp_domain.py <project_dir>

Exit codes:
    0  the published domain agrees with the configured host name
    1  it disagrees -- the message names both values
    3  nothing to evaluate (no tree, or no files in it); the CALLER must
       announce this as SKIPPED, never as a pass

    Anything else means this check could not run, and the caller must treat it
    as a hard error. 3 is deliberately not 2: `python` itself exits 2 when it
    cannot open the script, and the first execution of the PowerShell call site
    reported exactly that as "SKIPPED (not run)" -- a missing checker reading
    as a benign skip is the failure mode this whole module exists to remove.
"""

import json
import sys
from pathlib import Path

#: The file Liferay writes the portal's own notion of its domain into. The
#: `mainDomain` spelling beside it carries the same fact; `domains` may carry
#: several. Only this one is asserted -- see the module docstring.
MAIN_DOMAIN_FILE = "com.liferay.lxc.dxp.main.domain"


def read_configured_host_name(project_dir: Path) -> str | None:
    """The host name LDM was told to use, from the project's own metadata.

    Read rather than assumed: a caller that hardcoded "localhost" would assert
    nothing the day the suite starts passing `--host-name`, which is exactly
    the day this check becomes interesting.
    """
    meta_path = project_dir / "meta"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    host_name = meta.get("host_name")
    return str(host_name) if host_name else None


def read_published_values(project_dir: Path) -> dict[str, str]:
    """Every file Liferay published under routes/default/dxp, as name -> value."""
    dxp_dir = project_dir / "routes" / "default" / "dxp"
    if not dxp_dir.is_dir():
        return {}
    published = {}
    for entry in sorted(dxp_dir.iterdir()):
        if not entry.is_file():
            continue
        try:
            published[entry.name] = entry.read_text(encoding="utf-8").strip()
        except OSError as e:
            published[entry.name] = f"<unreadable: {e}>"
    return published


def domain_host(value: str) -> str:
    """The host part of a published domain, without any `:port` suffix.

    Liferay appends the published port when the host is not one it considers
    virtual -- `localhost:8080` in the measurement above -- so a bare string
    comparison would report a mismatch for a reason that is not the defect.
    """
    return value.split(":", 1)[0].strip().lower()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_published_dxp_domain.py <project_dir>")
        # 2, not 3: bad usage means this check did not run at all, which the
        # caller must treat as an error rather than as a skip.
        return 2
    project_dir = Path(argv[1])

    published = read_published_values(project_dir)
    if not published:
        print(
            "SKIPPED (not run): Liferay published no files under "
            "routes/default/dxp, so there is no domain to assert. Liferay "
            "writes them during boot (LDM-#2042)."
        )
        return 3

    # Recorded before anything is judged, so a future disagreement is readable
    # from the report without a re-run -- which is what the reporting team asked
    # for, having had to infer the value from a failure rather than read it.
    print("   published DXP config tree:")
    for name, value in published.items():
        print(f"     {name} = {value}")

    host_name = read_configured_host_name(project_dir)
    if not host_name:
        print(
            "SKIPPED (not run): could not read 'host_name' from the project's "
            "meta, so there is nothing to compare the published domain against."
        )
        return 3

    code, lines = judge(published, host_name)
    for line in lines:
        print(line)
    return code


def judge(published: dict[str, str], host_name: str) -> tuple[int, list[str]]:
    """The verdict itself: 0 when the published domain is the configured one.

    Split from `main` so the decision is one function with one job, and so the
    reporting above it stays printing rather than deciding.
    """
    if MAIN_DOMAIN_FILE not in published:
        return 1, [
            f"ERROR: Liferay published a DXP config tree without "
            f"'{MAIN_DOMAIN_FILE}'. A client extension resolves the portal "
            f"through that file, so an extension in this project cannot find "
            f"Liferay."
        ]

    value = published[MAIN_DOMAIN_FILE]
    if not value:
        return 1, [f"ERROR: '{MAIN_DOMAIN_FILE}' was published empty."]

    if domain_host(value) != host_name.strip().lower():
        return 1, [
            "ERROR: the portal published a domain that is not the host name "
            "LDM was configured with (LDM-#2154).",
            f"     configured host name : {host_name}",
            f"     {MAIN_DOMAIN_FILE} : {value}",
            "   Liferay derives this from its own virtualhost table, which LDM "
            "writes only on the restore path (snapshot/database.py), so a "
            "project created by init/run, seeded, or imported without a "
            "database publishes 'localhost' however correct --host-name was.",
            "   Every client extension reads this value, and the portal "
            "registers its OAuth redirect URIs against it.",
        ]

    return 0, [
        f"ASSERTED: the portal published '{value}' for a project configured as "
        f"'{host_name}'."
    ]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
