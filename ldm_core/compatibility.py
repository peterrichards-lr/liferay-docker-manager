"""What a package claims to have been TESTED on (LDM-#1791).

`tag` in an `.ldmp` manifest -- and `liferay.workspace.product` in a workspace
-- is a fact about how the package was **built**. Every consumer reads it as a
claim about what the package **supports**. Those are different, and until this
module nothing distinguished them: a consumer deviating from the pin could not
tell whether they were doing something the publisher tested and rejected,
something nobody has tried, or something fine.

**A pin is not a lag indicator, and there is no such thing as a "stale" pin.**
A package legitimately pins an older line because a later one was tried and
failed, or because nobody has had time to qualify one. The pin is the statement
of what is known to work.

## The shape

The claim lives under one key, `compatibility`, in the package manifest (and,
once imported, in the project meta). Both lists are optional and both default
to empty -- which is the whole point, see *The default* below::

    "compatibility": {
        "verified": [
            {
                "tag": "2026.q1.7-lts",
                "evidence": "https://github.com/owner/repo/actions/runs/123",
                "tested_on": "2026-09-18",
                "ldm_version": "2.15.44",
                "platform": null
            }
        ],
        "refuted": [
            {
                "tag": "2026.q1.12-lts",
                "evidence": "https://github.com/owner/repo/issues/1782",
                "tested_on": "2026-09-18",
                "ldm_version": "2.15.44",
                "platform": null
            }
        ]
    }

Three properties of that shape were chosen deliberately, and each answers one
of the three things LDM-#1791 said to settle before any code:

* **The default decides everything.** Absent means *no claim*, because every
  package already in the wild has no such key and inherits it. Nothing here
  ever renders absence as reassuring -- `describe()` has no wording for "fine",
  only for "verified", "unverified" and "no claim". LDM-#1782 was silence being
  read as success; a field defaulting to anything optimistic would recreate
  that with more ceremony.

* **Architecture is a second dimension**, and `2026.q1.7-lts` booting green
  three times on `aarch64` while failing on an `x86_64` runner is the proof.
  Every entry therefore carries a `platform` slot. LDM-#1791's first cut does
  not *read* it -- a claim with no platform is a claim about every platform --
  but the shape does not have to change to start.

* **Provenance, or it is noise within a month.** `evidence` is mandatory at the
  point a claim is produced (`ldm package` refuses a claim without it), the
  same instinct the compatibility matrix already has: point at evidence rather
  than assert. `tested_on` and `ldm_version` record when, and with what -- a
  January claim is weaker in December.

Lists, not scalars, because the ontology LDM-#1791 sketched (verified ranges,
non-contiguous known-bad, floors, exact-version equality) extends these entries
rather than replacing them. None of that is implemented here.

## What is implemented

Exactly the two behaviours LDM-#1791 called the first cut:

1. **A declared ceiling.** A refutation is a tested fact, so refusing above the
   last verified line is defensible. It has an explicit override, because
   packages get fixed and a consumer may know more than the publisher did, and
   the override is *recorded* rather than silent.
2. **Unknown -- the default**, announced at the moment the tag is resolved.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

#: Manifest / project-meta key holding the claim.
CLAIM_KEY = "compatibility"

#: Project-meta key recording that a consumer overrode a declared ceiling.
OVERRIDE_KEY = "compatibility_override"

#: The flag that performs that override. Named here so the refusal message,
#: the CLI declaration and the tests cannot drift apart.
OVERRIDE_FLAG = "--ignore-verified-ceiling"

#: `argparse` dest for the same flag.
OVERRIDE_ARG = "ignore_verified_ceiling"

_ENTRY_FIELDS = ("tag", "evidence", "tested_on", "ldm_version", "platform")


def version_key(tag: Any) -> tuple[int, ...]:
    """A Liferay tag as a sortable tuple of its integers.

    `2026.q1.7-lts` -> `(2026, 1, 7)`, `2026.q1.12-lts` -> `(2026, 1, 12)`, so
    the two order the way a human reads them -- which plain string comparison
    does not (`"2026.q1.12" < "2026.q1.7"`).

    Mirrors `BaseHandler.parse_version`; kept here so nothing in this module
    needs a handler instance to answer a question about two strings.
    """
    if not tag:
        return ()
    return tuple(int(part) for part in re.findall(r"\d+", str(tag)))


def compare_tags(left: Any, right: Any) -> int | None:
    """`-1`/`0`/`1`, or **None when either tag carries no version at all**.

    `nightly`, `master` and `latest` are real tags and order against nothing.
    Returning None rather than guessing is what keeps a ceiling from refusing a
    tag it cannot actually reason about -- an unorderable tag is *unknown*, and
    unknown is announced, never refused.
    """
    a, b = version_key(left), version_key(right)
    if not a or not b:
        return None
    if a == b:
        return 0
    return -1 if a < b else 1


def _entry(raw: Any) -> dict[str, Any] | None:
    """One claim entry, normalised, or None if it names no tag.

    Accepts a bare string as shorthand for `{"tag": ...}` so a hand-written
    manifest is not obliged to spell out an object -- but such an entry carries
    no evidence, and `has_evidence()` reports that honestly.
    """
    if isinstance(raw, str):
        raw = {"tag": raw}
    if not isinstance(raw, dict):
        return None
    tag = raw.get("tag")
    if not tag:
        return None
    entry: dict[str, Any] = {"tag": str(tag)}
    for field in _ENTRY_FIELDS[1:]:
        value = raw.get(field)
        if value not in (None, ""):
            entry[field] = value
    return entry


def normalise_claim(raw: Any) -> dict[str, list[dict[str, Any]]]:
    """A claim in canonical form. Anything unrecognised becomes *no claim*.

    A malformed `compatibility` key must not be louder than an absent one: the
    consequence of misreading it is either a spurious refusal or a false
    reassurance, and both are worse than falling back to "no claim was made".
    """
    claim: dict[str, list[dict[str, Any]]] = {"verified": [], "refuted": []}
    if not isinstance(raw, dict):
        return claim
    for kind in ("verified", "refuted"):
        entries = raw.get(kind)
        if isinstance(entries, (str, dict)):
            entries = [entries]
        if not isinstance(entries, list):
            continue
        for item in entries:
            entry = _entry(item)
            if entry:
                claim[kind].append(entry)
    return claim


def read_claim(meta: Any) -> dict[str, list[dict[str, Any]]]:
    """The claim recorded in a manifest or project meta. Never raises."""
    if not isinstance(meta, dict):
        return normalise_claim(None)
    return normalise_claim(meta.get(CLAIM_KEY))


def has_claim(claim: dict[str, list[dict[str, Any]]]) -> bool:
    return bool(claim.get("verified") or claim.get("refuted"))


def has_evidence(entry: dict[str, Any]) -> bool:
    return bool(entry.get("evidence"))


def tags(claim: dict[str, list[dict[str, Any]]], kind: str) -> list[str]:
    return [e["tag"] for e in claim.get(kind, [])]


def ceiling(claim: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    """The declared ceiling, or None.

    A ceiling exists **only when something was actually refuted**. That is the
    hinge of the whole design: `verified` on its own is a point claim and says
    nothing about anything above it, so promoting it to a refusal would invent
    a tested fact that nobody tested. "We tried a later line and it failed" is
    what makes refusing defensible, so the refutation is what creates the
    ceiling.

    Returns the highest verified entry, with `refuted_by` naming the lowest
    refuted tag above it -- the evidence for the ceiling being where it is.
    """
    verified = [e for e in claim.get("verified", []) if version_key(e["tag"])]
    refuted = [e for e in claim.get("refuted", []) if version_key(e["tag"])]
    if not verified or not refuted:
        return None

    highest = max(verified, key=lambda e: version_key(e["tag"]))
    above = [e for e in refuted if compare_tags(e["tag"], highest["tag"]) == 1]
    if not above:
        # Everything refuted sits at or below the highest verified line. That
        # is a non-contiguous known-bad -- LDM-#1791 defers it explicitly --
        # and it is emphatically not a ceiling. Say nothing rather than refuse
        # a tag on evidence that does not concern it.
        return None

    lowest_refuted = min(above, key=lambda e: version_key(e["tag"]))
    result = dict(highest)
    result["refuted_by"] = lowest_refuted
    return result


def exceeds_ceiling(claim: dict[str, list[dict[str, Any]]], tag: Any) -> bool:
    """Is `tag` above the declared ceiling?

    False when there is no ceiling, and false when the two cannot be ordered.
    """
    top = ceiling(claim)
    if not top:
        return False
    return compare_tags(tag, top["tag"]) == 1


def _provenance(entry: dict[str, Any]) -> str:
    bits = []
    if entry.get("evidence"):
        bits.append(f"evidence: {entry['evidence']}")
    if entry.get("tested_on"):
        bits.append(f"tested {entry['tested_on']}")
    if entry.get("ldm_version"):
        bits.append(f"with LDM {entry['ldm_version']}")
    if entry.get("platform"):
        bits.append(f"on {entry['platform']}")
    return f" ({', '.join(bits)})" if bits else ""


def describe(claim: dict[str, list[dict[str, Any]]], tag: Any) -> str:
    """The one line said at the moment the tag is resolved.

    There is no wording here for "fine". A tag is *verified* (a claim was made
    and it covers this tag), *refuted*, or it has no claim -- and no-claim is
    phrased so it cannot be mistaken for reassurance, because LDM-#1782 was
    exactly silence being read as success.
    """
    tag = str(tag)

    for entry in claim.get("refuted", []):
        if compare_tags(entry["tag"], tag) == 0:
            return (
                f"Liferay tag {tag} is declared REFUTED by the package -- "
                f"it was tried and it failed{_provenance(entry)}."
            )

    for entry in claim.get("verified", []):
        if compare_tags(entry["tag"], tag) == 0:
            return (
                f"Liferay tag {tag} is declared verified by the package"
                f"{_provenance(entry)}."
            )

    verified = tags(claim, "verified")
    if verified:
        return (
            f"The package declares {', '.join(verified)} verified; this run "
            f"resolved {tag}, unverified -- nothing has been tested for it."
        )

    return (
        f"Liferay tag {tag} carries no compatibility claim: the package "
        "records the tag it was built with, not one it has been tested "
        "against."
    )


def build_claim(
    verified: Any = None,
    refuted: Any = None,
    evidence: Any = None,
    platform: Any = None,
    tested_on: Any = None,
    ldm_version: Any = None,
) -> dict[str, list[dict[str, Any]]] | None:
    """Assemble a claim for `ldm package`, or None when nothing was declared.

    `evidence` is the caller's responsibility to supply; `handlers/snapshot.py`
    refuses a claim without it, and this function records exactly what it is
    given rather than inventing a default. A claim LDM could manufacture on its
    own would be worth nothing.
    """
    if not verified and not refuted:
        return None

    stamp = tested_on or datetime.now().date().isoformat()

    def entry(tag: Any) -> dict[str, Any]:
        record: dict[str, Any] = {"tag": str(tag), "tested_on": stamp}
        if evidence:
            record["evidence"] = str(evidence)
        if ldm_version:
            record["ldm_version"] = str(ldm_version)
        if platform:
            record["platform"] = str(platform)
        return record

    claim: dict[str, list[dict[str, Any]]] = {"verified": [], "refuted": []}
    if verified:
        claim["verified"].append(entry(verified))
    if refuted:
        claim["refuted"].append(entry(refuted))
    return claim


def override_record(
    tag: Any, top: dict[str, Any], ldm_version: Any = None
) -> dict[str, Any]:
    """What gets written into the project meta when a ceiling is overridden.

    Recorded, not silent: the next person to look at this project can see that
    it is running above the line its publisher verified, which tag it was, and
    on whose say-so.
    """
    record: dict[str, Any] = {
        "tag": str(tag),
        "ceiling": str(top.get("tag")),
        "overridden_at": datetime.now().isoformat(),
        "flag": OVERRIDE_FLAG,
    }
    refuted_by = top.get("refuted_by") or {}
    if refuted_by.get("tag"):
        record["refuted"] = str(refuted_by["tag"])
    if refuted_by.get("evidence"):
        record["evidence"] = str(refuted_by["evidence"])
    if ldm_version:
        record["ldm_version"] = str(ldm_version)
    return record


def refusal_message(tag: Any, top: dict[str, Any]) -> str:
    """Why LDM will not boot `tag`, and how to say you know better."""
    refuted_by = top.get("refuted_by") or {}
    lines = [
        f"The package declares {top.get('tag')} as its verified ceiling, and "
        f"{tag} is above it.",
        f"  {refuted_by.get('tag')} was tried and it failed{_provenance(refuted_by)}.",
        "  A pin records the version that has been TESTED -- this one is a "
        "tested negative result, not a package lagging behind.",
        f"  Pass {OVERRIDE_FLAG} to proceed anyway. The override is recorded "
        "in the project metadata.",
    ]
    return "\n".join(lines)
