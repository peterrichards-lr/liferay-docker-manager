"""A pin states a version but makes no claim (LDM-#1791).

`tag` in an `.ldmp` manifest is a fact about how the package was **built**, and
every consumer reads it as a claim about what it **supports**. LDM-#1782 was
that gap: silence being read as success.

These drive the real code for the two behaviours LDM-#1791 scoped as the first
cut, and for the constraints around them.

1. **A declared ceiling refuses**, because a refutation is a tested fact -- and
   the override is explicit and recorded.
2. **No claim is the default and is announced**, never silent, never worded so
   that it could be mistaken for approval.

Per LDM-#1770, each behaviour was broken deliberately and these were confirmed
to fail against the broken code before being trusted. The neutering probes are
in `TheseTestsCanActuallyFail` at the bottom: they disable the behaviour while
leaving every symbol and message in place, which is the shape a test suite has
to survive to be worth anything.
"""

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core import compatibility
from ldm_core.handlers.snapshot import SnapshotService
from ldm_core.pipelines.import_pipeline import ProjectSetupStage
from ldm_core.pipelines.run import ConfigResolutionStage

CEILING = "2026.q1.7-lts"
REFUTED = "2026.q1.12-lts"
EVIDENCE = "https://github.com/peterrichards-lr/liferay-docker-manager/issues/1782"


def a_claim(verified=CEILING, refuted=REFUTED, evidence=EVIDENCE):
    """A real claim, built by the real producer helper."""
    return compatibility.build_claim(
        verified=verified, refuted=refuted, evidence=evidence, ldm_version="2.15.44"
    )


class _Args:
    """A plain object, not a MagicMock.

    Every attribute of a MagicMock is truthy, so an unset
    `ignore_verified_ceiling` would silently send the ceiling check down the
    override branch and the refusal would never be measured at all.
    """

    def __init__(self, **kwargs):
        self.ignore_verified_ceiling = False
        for key, value in kwargs.items():
            setattr(self, key, value)


class _Manager:
    def __init__(self, **kwargs):
        self.args = _Args(**kwargs)
        self.written: list[dict] = []

    def write_meta(self, _root, meta):
        self.written.append(dict(meta))


class _Speaks:
    """Captures what LDM said, at which verbosity."""

    def __init__(self):
        self.info: list[str] = []
        self.detail: list[str] = []
        self.warning: list[str] = []

    @contextlib.contextmanager
    def listening(self):
        def record(bucket):
            def sink(message, *_args, **_kwargs):
                bucket.append(str(message))

            return sink

        with (
            patch("ldm_core.ui.UI.info", side_effect=record(self.info)),
            patch("ldm_core.ui.UI.detail", side_effect=record(self.detail)),
            patch("ldm_core.ui.UI.warning", side_effect=record(self.warning)),
        ):
            yield self


def resolve(tag, meta=None, override=False):
    """Drive the real `_apply_compatibility_claim` for a resolved tag.

    Returns `(what was said, the project meta afterwards, the manager)`.
    """
    manager = _Manager(ignore_verified_ceiling=override)
    project_meta = dict(meta or {})
    with tempfile.TemporaryDirectory() as tmp:
        paths = {"root": Path(tmp)}
        speaks = _Speaks()
        with speaks.listening():
            ConfigResolutionStage._apply_compatibility_claim(
                manager, paths, project_meta, tag
            )
    return speaks, project_meta, manager


class ADeclaredCeilingRefuses(unittest.TestCase):
    """ "Tested above this and it failed" is a tested fact, so refusing is
    defensible where refusing on a bare pin would not be."""

    def test_a_tag_above_the_ceiling_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            resolve(REFUTED, {"compatibility": a_claim()})

        self.assertEqual(
            caught.exception.code,
            1,
            "a requested tag colliding with a declared precondition is an "
            "input validation problem -- exit 1, not 3 (external data) or 4 "
            "(LDM-internal orchestration)",
        )

    def test_a_tag_between_the_ceiling_and_the_refutation_is_refused_too(self):
        """The ceiling is a ceiling. `2026.q1.9-lts` was never tested, and the
        package has said a later line broke -- LDM does not walk past the last
        line anyone verified on a guess."""
        with self.assertRaises(SystemExit):
            resolve("2026.q1.9-lts", {"compatibility": a_claim()})

    def test_the_refusal_names_what_failed_and_how_to_override(self):
        speaks = _Speaks()
        errors: list[str] = []

        def record_error(message, *_args, **_kwargs):
            errors.append(str(message))

        with (
            speaks.listening(),
            patch("ldm_core.ui.UI.error", side_effect=record_error),
            self.assertRaises(SystemExit),
        ):
            ConfigResolutionStage._apply_compatibility_claim(
                _Manager(ignore_verified_ceiling=False),
                {"root": Path(tempfile.gettempdir())},
                {"compatibility": a_claim()},
                REFUTED,
            )

        said = " ".join(errors)
        self.assertIn(CEILING, said, "the refusal must name the ceiling")
        self.assertIn(REFUTED, said, "and the tag that was tried and failed")
        self.assertIn(compatibility.OVERRIDE_FLAG, said, "and the way past it")

    def test_the_ceiling_at_or_below_it_is_not_refused(self):
        speaks, _, _ = resolve(CEILING, {"compatibility": a_claim()})
        self.assertTrue(speaks.info)

    def test_a_verified_claim_with_no_refutation_is_not_a_ceiling(self):
        """A point claim says nothing about anything above it. Promoting it to
        a refusal would invent a tested fact nobody tested."""
        claim = compatibility.build_claim(verified=CEILING, evidence=EVIDENCE)
        speaks, _, _ = resolve("2027.q1.0-lts", {"compatibility": claim})
        self.assertTrue(speaks.info, "announced, not refused")

    def test_an_unorderable_tag_is_never_refused(self):
        """`nightly` orders against nothing. Unknown is announced, not refused."""
        speaks, _, _ = resolve("nightly", {"compatibility": a_claim()})
        self.assertTrue(speaks.info)


class TheOverrideIsExplicitAndRecorded(unittest.TestCase):
    """Packages get fixed, and a consumer may know more than the publisher did
    -- but nobody should be able to discover later that it happened silently."""

    def test_the_flag_lets_the_run_proceed(self):
        speaks, _, _ = resolve(REFUTED, {"compatibility": a_claim()}, override=True)
        self.assertTrue(speaks.info, "the run proceeded and still announced")

    def test_the_override_is_written_into_the_project_meta(self):
        _, meta, _ = resolve(REFUTED, {"compatibility": a_claim()}, override=True)

        record = meta.get(compatibility.OVERRIDE_KEY)
        self.assertIsNotNone(record, "an unrecorded override is a silent one")
        self.assertEqual(record["tag"], REFUTED)
        self.assertEqual(record["ceiling"], CEILING)
        self.assertEqual(record["refuted"], REFUTED)
        self.assertIn("overridden_at", record)

    def test_the_override_is_persisted_not_merely_held_in_memory(self):
        _, _, manager = resolve(REFUTED, {"compatibility": a_claim()}, override=True)

        self.assertTrue(manager.written, "the meta was never written to disk")
        self.assertIn(compatibility.OVERRIDE_KEY, manager.written[-1])

    def test_the_override_is_announced_as_a_warning(self):
        speaks, _, _ = resolve(REFUTED, {"compatibility": a_claim()}, override=True)

        said = " ".join(speaks.warning)
        self.assertIn(CEILING, said)
        self.assertIn(compatibility.OVERRIDE_KEY, said)

    def test_an_absent_flag_is_not_an_override(self):
        """`--ignore-verified-ceiling` lives on the shared sub-parser, which
        uses `argparse.SUPPRESS` -- so when it is not passed the attribute does
        not exist at all rather than being False. Reading it as anything but
        "no override" would turn every run into a silent override."""
        manager = _Manager()
        del manager.args.ignore_verified_ceiling
        self.assertFalse(hasattr(manager.args, compatibility.OVERRIDE_ARG))

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            with _Speaks().listening(), patch("ldm_core.ui.UI.error"):
                ConfigResolutionStage._apply_compatibility_claim(
                    manager,
                    {"root": Path(tmp)},
                    {"compatibility": a_claim()},
                    REFUTED,
                )

    def test_nothing_is_recorded_when_no_ceiling_was_crossed(self):
        """The flag is not a stamp. Passing it on an ordinary run must not
        fabricate an override record."""
        _, meta, _ = resolve(CEILING, {"compatibility": a_claim()}, override=True)
        self.assertNotIn(compatibility.OVERRIDE_KEY, meta)


class NoClaimIsTheDefaultAndIsAnnounced(unittest.TestCase):
    """Every package already in the wild inherits this. LDM-#1782 was silence
    being read as success, so the default gets a line of its own."""

    def test_a_package_with_no_claim_still_works(self):
        speaks, meta, _ = resolve("2026.q1.12-lts", {"tag": "2026.q1.7-lts"})

        self.assertNotIn(compatibility.OVERRIDE_KEY, meta)
        self.assertTrue(speaks.info, "no claim must not mean no output")

    def test_the_default_is_announced_not_silent(self):
        speaks, _, _ = resolve("2026.q1.12-lts", {"tag": "2026.q1.7-lts"})

        said = " ".join(speaks.info)
        self.assertIn("no compatibility claim", said.lower())
        self.assertIn("2026.q1.12-lts", said)

    def test_it_is_announced_at_default_verbosity(self):
        """`UI.detail` prints only under `--info`/`--verbose` (LDM-#1036),
        which is not the verbosity CI runs at -- the exact defect LDM-#1790
        fixed for the pin announcement."""
        speaks, _, _ = resolve("2026.q1.12-lts", {"tag": "2026.q1.7-lts"})

        self.assertTrue(speaks.info)
        self.assertNotIn(
            "no compatibility claim",
            " ".join(speaks.detail).lower(),
            "the default state is hidden behind a verbosity flag again",
        )

    def test_no_claim_is_never_worded_as_reassurance(self):
        line = compatibility.describe(
            compatibility.read_claim({"tag": "2026.q1.7-lts"}), "2026.q1.12-lts"
        )
        for reassuring in ("supported", "compatible", "ok", "fine", "verified"):
            self.assertNotIn(
                reassuring, line.lower(), f"'{reassuring}' reads as approval"
            )

    def test_a_malformed_claim_falls_back_to_no_claim(self):
        """Louder than absent is the wrong direction: misreading it produces
        either a spurious refusal or a false reassurance."""
        speaks, _, _ = resolve("2026.q1.12-lts", {"compatibility": "yes please"})
        self.assertIn("no compatibility claim", " ".join(speaks.info).lower())

    def test_a_tag_outside_a_real_claim_is_called_unverified(self):
        speaks, _, _ = resolve("2026.q1.6-lts", {"compatibility": a_claim()})

        said = " ".join(speaks.info).lower()
        self.assertIn("unverified", said)
        self.assertIn(CEILING.lower(), said)


class TheClaimSurvivesTheImport(unittest.TestCase):
    def test_a_manifest_claim_reaches_the_project_meta(self):
        claim = a_claim()
        meta: dict = {}
        ProjectSetupStage._carry_compatibility_claim(
            {"tag": CEILING, **{compatibility.CLAIM_KEY: claim}}, meta
        )
        self.assertEqual(meta[compatibility.CLAIM_KEY], claim)

    def test_a_manifest_without_one_stays_without_one(self):
        """Backward compatibility: every `.ldmp` in the wild has no such key,
        and must keep working with the default of no claim."""
        meta: dict = {"tag": CEILING}
        ProjectSetupStage._carry_compatibility_claim({"tag": CEILING}, meta)
        self.assertNotIn(compatibility.CLAIM_KEY, meta)
        self.assertFalse(compatibility.has_claim(compatibility.read_claim(meta)))


class _PackageHost(SnapshotService):
    """The real `SnapshotService`, wired to a fake manager.

    `__init__` is skipped rather than called: it builds six collaborating
    sub-services that none of these paths touch. Every method under test is the
    real one.
    """

    def __init__(self, **kwargs):
        self.manager = _Manager(**kwargs)  # type: ignore[assignment]
        self.args = self.manager.args
        self.non_interactive = True
        self.verbose = False


def declare(**kwargs):
    meta: dict = {"tag": CEILING}
    host = _PackageHost(**kwargs)
    speaks = _Speaks()
    with speaks.listening():
        SnapshotService._compatibility_claim_args(host)
        SnapshotService._record_compatibility_claim(host, meta)
    return meta, speaks


class AClaimMustPointAtEvidence(unittest.TestCase):
    """ "If `verified` can be produced by something that did not actually run,
    it becomes noise within a month" -- LDM-#1791."""

    def test_a_claim_without_evidence_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            declare(verified=CEILING, refuted=None, evidence=None)
        self.assertEqual(caught.exception.code, 1)

    def test_a_refutation_without_evidence_is_refused_too(self):
        with self.assertRaises(SystemExit):
            declare(verified=None, refuted=REFUTED, evidence=None)

    def test_the_refusal_comes_before_any_snapshot_is_taken(self):
        """Observed: stamping happens after a full snapshot has been created,
        so validating there would charge the user a snapshot to be told a flag
        is missing. `cmd_package` calls the validator on its first line."""
        host = _PackageHost(verified=CEILING, refuted=None, evidence=None)
        work: list[str] = []

        def note(label):
            def did(*_args, **_kwargs):
                work.append(label)

            return did

        host.cmd_snapshot = note("snapshot")  # type: ignore[method-assign]
        host.manager.detect_project_path = note(  # type: ignore[method-assign]
            "resolve project"
        )

        with self.assertRaises(SystemExit), patch("ldm_core.ui.UI.error"):
            SnapshotService.cmd_package(host, "anything")

        self.assertEqual(work, [], "work was done before the flag check")

    def test_declaring_nothing_leaves_no_claim(self):
        meta, _ = declare(verified=None, refuted=None, evidence=None)
        self.assertNotIn(compatibility.CLAIM_KEY, meta)

    def test_a_declared_claim_is_stamped_into_the_manifest(self):
        meta, _ = declare(verified=CEILING, refuted=REFUTED, evidence=EVIDENCE)

        claim = meta[compatibility.CLAIM_KEY]
        self.assertEqual([e["tag"] for e in claim["verified"]], [CEILING])
        self.assertEqual([e["tag"] for e in claim["refuted"]], [REFUTED])
        for entry in claim["verified"] + claim["refuted"]:
            self.assertEqual(entry["evidence"], EVIDENCE)
            self.assertIn("tested_on", entry)
            self.assertIn("ldm_version", entry)

    def test_the_manifest_round_trips_through_json(self):
        """It ships inside a `.ldmp` as the snapshot `meta`, which `write_meta`
        serialises as JSON."""
        meta, _ = declare(verified=CEILING, refuted=REFUTED, evidence=EVIDENCE)
        reloaded = json.loads(json.dumps(meta))
        self.assertTrue(compatibility.has_claim(compatibility.read_claim(reloaded)))


class TheShapeDoesNotForecloseWhatWasDeferred(unittest.TestCase):
    """LDM-#1791 defers the wider ontology but names one thing the first cut
    must not make impossible: architecture as a second dimension.
    `2026.q1.7-lts` booted green three times on `aarch64` while failing on an
    `x86_64` runner, so a one-dimensional claim would have recorded a fact that
    is true for one platform and false for the other."""

    def test_a_platform_qualified_entry_survives_being_read_back(self):
        claim = compatibility.read_claim(
            {
                compatibility.CLAIM_KEY: {
                    "verified": [
                        {
                            "tag": CEILING,
                            "evidence": EVIDENCE,
                            "platform": "aarch64",
                        }
                    ]
                }
            }
        )
        self.assertEqual(claim["verified"][0]["platform"], "aarch64")

    def test_several_claims_of_each_kind_are_held(self):
        """Ranges and non-contiguous known-bad extend these lists rather than
        replacing the shape."""
        claim = compatibility.read_claim(
            {
                compatibility.CLAIM_KEY: {
                    "verified": [{"tag": "2026.q1.7-lts"}, {"tag": "2026.q1.10-lts"}],
                    "refuted": [{"tag": "2026.q1.8-lts"}, {"tag": "2026.q1.12-lts"}],
                }
            }
        )
        self.assertEqual(len(claim["verified"]), 2)
        self.assertEqual(len(claim["refuted"]), 2)
        top = compatibility.ceiling(claim)
        assert top is not None
        self.assertEqual(
            top["tag"], "2026.q1.10-lts", "the ceiling is the highest verified"
        )
        self.assertEqual(
            top["refuted_by"]["tag"], "2026.q1.12-lts", "the lowest refutation above it"
        )

    def test_tags_order_numerically_not_lexically(self):
        """`"2026.q1.12" < "2026.q1.7"` as strings, which would invert the
        ceiling entirely."""
        self.assertEqual(
            compatibility.compare_tags("2026.q1.12-lts", "2026.q1.7-lts"), 1
        )
        self.assertIsNone(compatibility.compare_tags("nightly", "2026.q1.7-lts"))


class TheseTestsCanActuallyFail(unittest.TestCase):
    """LDM-#1770: a suite that passes against neutered code is not acceptable.

    Each probe disables one behaviour while leaving every symbol, flag and
    message string in place -- the shape a real regression takes -- and asserts
    the behaviour is gone. Both were also run as full-suite neuterings before
    being trusted; see the PR for the observed failure counts.

    `assert needle in s` on every anchor, because a `str.replace()` that does
    not match silently measures the unmodified code and "passes".
    """

    def test_neutering_the_ceiling_removes_the_refusal(self):
        """Behaviour 1: make `exceeds_ceiling` always say no -- the flag, the
        message and `refusal_message` all still exist."""
        original = compatibility.exceeds_ceiling

        def never_exceeds(_claim, _tag):
            return False

        compatibility.exceeds_ceiling = never_exceeds  # type: ignore[assignment]
        try:
            speaks, meta, _ = resolve(REFUTED, {"compatibility": a_claim()})
        finally:
            compatibility.exceeds_ceiling = original  # type: ignore[assignment]

        self.assertTrue(
            speaks.info and not speaks.warning,
            "neutered code refused anyway -- the probe missed its anchor",
        )
        self.assertNotIn(compatibility.OVERRIDE_KEY, meta)

        # And the real code, immediately afterwards, does refuse.
        with self.assertRaises(SystemExit):
            resolve(REFUTED, {"compatibility": a_claim()})

    def test_neutering_the_announcement_removes_the_default_line(self):
        """Behaviour 2: make `describe` return nothing to say. Every symbol and
        every wording stays; only the visibility goes."""
        original = compatibility.describe
        source = original(compatibility.read_claim({}), "2026.q1.12-lts")
        assert "no compatibility claim" in source.lower(), (
            "the anchor this probe removes is not in the real message"
        )

        def says_nothing(_claim, _tag):
            return ""

        compatibility.describe = says_nothing  # type: ignore[assignment]
        try:
            speaks, _, _ = resolve("2026.q1.12-lts", {"tag": CEILING})
        finally:
            compatibility.describe = original  # type: ignore[assignment]

        self.assertNotIn(
            "no compatibility claim",
            " ".join(speaks.info).lower(),
            "neutered code still announced -- the probe missed its anchor",
        )

        speaks, _, _ = resolve("2026.q1.12-lts", {"tag": CEILING})
        self.assertIn("no compatibility claim", " ".join(speaks.info).lower())


if __name__ == "__main__":
    unittest.main()
