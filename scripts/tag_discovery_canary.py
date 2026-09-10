#!/usr/bin/env python3
"""Tag discovery canary (LDM-#1650).

Every other test of tag discovery mocks the registry, which is the right shape
for parsing and ranking logic but means nothing observes the three things that
actually broke in LDM-#1647/#1648/#1649:

- what Docker Hub's `ordering` parameter does (it inverts the DRF convention,
  so `-last_updated` returns the OLDEST tags -- the whole of #1647);
- whether a `&name=` sweep still matches anything (`-qr` matched zero tags for
  who knows how long, making that channel unsatisfiable by construction);
- whether a tag family still fits inside `TAG_DISCOVERY_MAX_PAGES`.

All three are properties of upstream data, and all three fail silently. This
script asserts only invariants that cannot go stale -- deliberately no
assertions on specific tag values, because a canary needing an update on every
Liferay release is a canary that gets switched off.

Run it directly to check the live registry:

    python3 scripts/tag_discovery_canary.py

Exit 0 = every invariant holds. Exit 1 = at least one failed. Exit 2 = the
script could not run (import or environment problem), which is not a finding
about the registry.
"""

import re
import sys
import tempfile

# Isolate LDM's state directory BEFORE importing ldm_core: discovery caches
# results for 24h under `<home>/.liferay_docker_cache.json`, and a canary must
# never read -- or write -- the real one. `refresh=True` below covers the read;
# this covers the write, and keeps a CI runner's home clean either way.
_STATE_DIR = tempfile.TemporaryDirectory(prefix="ldm-canary-")
import os  # noqa: E402

os.environ["LDM_HOME"] = _STATE_DIR.name

try:
    import requests

    from ldm_core.constants import (
        API_BASE_DXP,
        API_BASE_PORTAL,
        LEGACY_TAG_PATTERN,
        NIGHTLY_TAG_PATTERN,
        TAG_DISCOVERY_MAX_PAGES,
        TAG_PATTERN,
    )
    from ldm_core.utils import discover_latest_tag
except ImportError as exc:  # pragma: no cover - environment problem, not a finding
    print(f"CANARY DID NOT RUN: {exc}", file=sys.stderr)
    sys.exit(2)

# Docker Hub caps `page_size` at 100 whatever LDM asks for, so this is the real
# ceiling on a single sweep. A family that outgrows it silently stops being
# fully enumerable, and the ranking then depends on the order the registry
# happens to return -- which is exactly how `lts` was one growth spurt away
# from breaking the same way `any` already had.
SWEEP_CEILING = TAG_DISCOVERY_MAX_PAGES * 100

# Report a family that is close to the ceiling but has not crossed it. The
# families grow monotonically -- tags are added, never removed -- so this is
# the only warning anyone gets, and it needs to arrive well before the failure.
WARN_AT_PERCENT = 70

# (label, api base, release type). Every channel LDM advertises that can
# resolve for the repository in question. `--portal` is deliberately only
# checked for `latest`: liferay/portal publishes no quarterly, -lts, -u, -qr or
# nightly tags at all, so the other channels correctly resolve to nothing.
CHANNELS = [
    ("dxp lts", API_BASE_DXP, "lts"),
    ("dxp qr", API_BASE_DXP, "qr"),
    ("dxp latest", API_BASE_DXP, "any"),
    ("dxp nightly", API_BASE_DXP, "nightly"),
    ("dxp u", API_BASE_DXP, "u"),
    ("portal latest", API_BASE_PORTAL, "any"),
]

# (label, repository, `&name=` filter) for each sweep LDM issues. Mirrors
# `_tag_discovery_sweeps`; the point is the size of what comes back, so the
# filters are listed rather than imported as functions.
SWEEPS = [
    ("dxp quarterly", "dxp", ".q"),
    ("dxp lts", "dxp", "-lts"),
    ("dxp legacy u", "dxp", "-u"),
    ("portal (unfiltered)", "portal", None),
]

PATTERNS = (TAG_PATTERN, LEGACY_TAG_PATTERN, NIGHTLY_TAG_PATTERN)


def _tag_is_recognised(tag):
    return any(re.match(pattern, tag) for pattern in PATTERNS)


def check_channels():
    """Every advertised channel resolves to a tag LDM would itself accept."""
    failures = []
    for label, api_base, release_type in CHANNELS:
        try:
            tag = discover_latest_tag(api_base, release_type=release_type, refresh=True)
        except Exception as exc:
            failures.append(f"{label}: discovery raised {exc!r}")
            continue

        if not tag:
            failures.append(
                f"{label}: resolved nothing. This is the LDM-#1647 failure mode -- "
                "an advertised channel that cannot produce a tag."
            )
            continue

        if not _tag_is_recognised(tag):
            failures.append(
                f"{label}: resolved {tag!r}, which matches none of LDM's tag "
                "patterns. Either the naming scheme moved or a pattern is wrong."
            )
            continue

        print(f"  OK   {label:16} -> {tag}")
    return failures


def check_sweep_headroom():
    """No tag family has outgrown what one sweep can enumerate."""
    failures = []
    for label, repo, name_filter in SWEEPS:
        url = (
            f"https://hub.docker.com/v2/repositories/liferay/{repo}"
            "/tags?page_size=1&ordering=last_updated"
        )
        if name_filter:
            url += f"&name={name_filter}"
        try:
            response = requests.get(url, timeout=30)
            count = response.json().get("count")
        except Exception as exc:
            failures.append(f"{label}: could not read the family size ({exc!r})")
            continue

        if not isinstance(count, int):
            failures.append(f"{label}: registry returned no usable count")
            continue

        if count > SWEEP_CEILING:
            failures.append(
                f"{label}: {count} tags exceeds the {SWEEP_CEILING}-tag sweep "
                f"ceiling ({TAG_DISCOVERY_MAX_PAGES} pages x 100). The family can "
                "no longer be fully enumerated, so ranking now depends on the "
                "order the registry returns. Raise TAG_DISCOVERY_MAX_PAGES or "
                "narrow the sweep."
            )
            continue

        used = 100 * count // SWEEP_CEILING
        if used >= WARN_AT_PERCENT:
            # Not a failure: nothing is broken yet. But this is the only
            # advance warning that exists, and the margin only moves one way.
            print(
                f"  WARN {label:16} -> {count} tags is {used}% of the "
                f"{SWEEP_CEILING}-tag ceiling. Plan to raise "
                "TAG_DISCOVERY_MAX_PAGES before it crosses."
            )
            continue
        print(f"  OK   {label:16} -> {count} tags ({used}% of ceiling)")
    return failures


def check_resolved_tags_are_pullable():
    """The winning tag is one the registry still serves.

    Catches the LDM-#1649 class generally rather than by name: a tag that
    exists, ranks highest and has been withdrawn upstream (`7.4.13-u999`), and
    equally a tag name LDM derived incorrectly and that therefore does not
    exist at all.
    """
    failures = []
    for label, api_base, release_type in CHANNELS:
        tag = discover_latest_tag(api_base, release_type=release_type, refresh=True)
        if not tag:
            continue  # already reported by check_channels

        repo = "portal" if "portal" in api_base else "dxp"
        url = f"https://hub.docker.com/v2/repositories/liferay/{repo}/tags/{tag}"
        try:
            response = requests.get(url, timeout=30)
        except Exception as exc:
            failures.append(f"{label}: could not confirm {tag} exists ({exc!r})")
            continue

        if response.status_code == 404:
            failures.append(
                f"{label}: resolved {tag!r}, which the registry does not have. "
                "LDM constructed a tag name that cannot be pulled."
            )
            continue
        if response.status_code != 200:
            failures.append(f"{label}: HTTP {response.status_code} confirming {tag}")
            continue

        status = str(response.json().get("tag_status", "")).lower()
        if status == "inactive":
            failures.append(
                f"{label}: resolved {tag!r}, which upstream reports as inactive "
                "(the LDM-#1649 class -- a withdrawn tag outranking a live one)."
            )
            continue

        print(f"  OK   {label:16} -> {tag} is {status or 'present'}")
    return failures


def main():
    print("LDM tag discovery canary")
    print("========================")
    print(f"State dir: {os.environ['LDM_HOME']}\n")

    failures = []

    print("Channels resolve to a recognised tag:")
    failures += check_channels()

    print("\nSweeps still enumerate a whole family:")
    failures += check_sweep_headroom()

    print("\nResolved tags are served by the registry:")
    failures += check_resolved_tags_are_pullable()

    if failures:
        print(f"\n{len(failures)} FAILED:\n")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nAll invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
