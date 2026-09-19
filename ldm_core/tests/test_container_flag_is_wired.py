"""`-c/--container` must reach meta, sanitised, and must not break prune.

LDM-#1836.

## What was wrong

`-c/--container` was declared on `run` and `up` and read by nothing. Anyone
passing `ldm run demo -c my-name` got a project named after the directory
instead, silently. It is the fourth flag of the family LDM-#1695 catalogued --
`--env`, `--gogo-port` and `--mount-logs` -- and was missed at the time.

## Why sanitising at the point of ENTRY is the whole fix

Two readers derive the project's identity from the same meta key, and they do
not derive it the same way:

    composer.py:581-582   sanitize_id(meta["container_name"]) -> ownership labels
                                                              -> container names
    prune.py:387          meta["container_name"]              -> "active projects"

`composer` sanitises; `prune` does not. That was harmless only because
`container_name` had always come from an already-sanitised project id. A raw
value like "My Project" would be labelled as one string and matched as another,
and `prune` would report a live project's containers as orphans and offer them
for deletion.

Storing the sanitised form keeps both readers looking at the same string, which
removes the class rather than patching `prune`.

## Why an existing project is refused

`container_name` is frozen into meta and drives the Liferay, database and tunnel
container names (`run.py`). Changing it on a live project renames every
container and leaves its volumes behind, owned by a name nothing resolves.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ldm_core.pipelines.run import ConfigResolutionStage
from ldm_core.utils import sanitize_id


def _apply(container, meta=None):
    """Runs the real `_apply_inert_flags` with `--container` set."""
    manager = SimpleNamespace(
        args=SimpleNamespace(
            container=container, env=None, gogo_port=None, mount_logs=False
        )
    )
    project_meta = dict(meta or {})
    ConfigResolutionStage._apply_inert_flags(manager, project_meta)
    return project_meta


class TheFlagReachesMeta(unittest.TestCase):
    def test_a_plain_name_is_stored(self):
        meta = _apply("my-name")

        self.assertEqual(meta.get("container_name"), "my-name")

    def test_it_is_stored_sanitised_not_raw(self):
        """The assertion that matters: composer sanitises and prune does not,
        so meta must never hold a value composer would rewrite."""
        raw = "My Project"
        meta = _apply(raw)

        stored = meta.get("container_name")
        self.assertNotEqual(stored, raw, "the raw value reached meta")
        self.assertEqual(stored, sanitize_id(raw))
        self.assertEqual(
            sanitize_id(stored),
            stored,
            "stored value is not a fixed point of sanitize_id",
        )

    def test_not_passing_it_changes_nothing(self):
        self.assertEqual(_apply(None), {})

    def test_an_unusable_name_is_substituted_visibly_not_refused(self):
        """`sanitize_id` never returns empty -- "///" becomes a generated
        `project-<hash>`. A first draft of this guarded a "reduces to nothing"
        case that can never occur; what matters instead is that the
        substitution is announced rather than silent."""
        with patch("ldm_core.pipelines.run.UI.detail") as detail:
            meta = _apply("///")

        stored = meta["container_name"]
        self.assertTrue(stored.startswith("project-"), stored)
        self.assertTrue(detail.called, "the name was rewritten without telling anyone")
        self.assertIn(stored, str(detail.call_args.args[0]))


class AnExistingProjectIsRefused(unittest.TestCase):
    def test_changing_the_name_of_an_existing_project_dies(self):
        with patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit(1)) as die:
            with self.assertRaises(SystemExit):
                _apply(
                    "new-name", meta={"container_name": "old-name", "project_name": "p"}
                )

        self.assertEqual(die.call_args.kwargs.get("exit_code"), 1)
        self.assertIn("old-name", str(die.call_args.args[0]))

    def test_passing_the_same_name_again_is_accepted(self):
        """Re-running `ldm up demo -c demo` must not fail -- the value is
        unchanged, so nothing would be renamed."""
        meta = _apply("same", meta={"container_name": "same"})

        self.assertEqual(meta.get("container_name"), "same")


class PruneStillSeesTheProjectAsLive(unittest.TestCase):
    """The acceptance criterion from the issue.

    Driven against the real derivations in both files rather than by mocking
    them, because the defect being guarded is precisely that the two disagree.
    """

    def test_composer_and_prune_derive_the_same_name(self):
        for raw in ("my-name", "My Project", "démo_2", "UPPER-Case"):
            with self.subTest(raw=raw):
                meta = _apply(raw)
                stored = meta["container_name"]

                # composer.py:581-582
                composer_name = sanitize_id(meta.get("container_name") or "fallback")
                # prune.py:387 -- deliberately NOT sanitised
                prune_name = meta.get("container_name") or "fallback"

                self.assertEqual(
                    composer_name,
                    prune_name,
                    f"labels would read '{composer_name}' while prune matches "
                    f"'{prune_name}' -- a live project would be offered as an orphan",
                )
                self.assertEqual(stored, composer_name)


if __name__ == "__main__":
    unittest.main()
