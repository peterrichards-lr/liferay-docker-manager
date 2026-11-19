"""`scripts/sync_reclaim_table.py` keeps the documented reclaim matrix honest.

LDM-#2151. The matrix was restated by hand in two documents and drifted from
the code (LDM-#2143). These tests drive the generator against a COPY of the
relevant files, never the real tree, and cover the three things the issue made
"done when": the committed tables equal the generated ones, an edit on either
side fails `--check`, and a list the parser cannot read fails loudly instead of
vanishing from the table.
"""

import shutil
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

import sync_reclaim_table  # noqa: E402
from reclaim_sites import (  # noqa: E402
    RECLAIM_SITES,
    ReclaimParseError,
    parse_reclaim_lists,
    site_columns,
)

_RUN_PY = "ldm_core/pipelines/run.py"
_RUN_LIST = '["deploy", "logs", "osgi", "files", "routes"]'


@pytest.fixture
def tree(tmp_path):
    """A copy of every input and output the generator touches."""
    for rel in [r for r, _ in RECLAIM_SITES] + list(sync_reclaim_table.TARGETS):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_PROJECT_ROOT / rel, dest)
    return tmp_path


def _edit(path: Path, old: str, new: str, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture text {old!r} not found in {path}"
    path.write_text(text.replace(old, new, count), encoding="utf-8")


class TestCommittedTablesAreCurrent:
    def test_check_passes_on_the_real_tree(self, capsys):
        """The committed tables equal the generator's output."""
        rc = sync_reclaim_table.main(["--check"])
        assert rc == 0, capsys.readouterr().err

    def test_every_target_carries_the_generated_block(self):
        block = sync_reclaim_table.render_block(
            sync_reclaim_table.render_table(site_columns(_PROJECT_ROOT))
        )
        for rel in sync_reclaim_table.TARGETS:
            text = (_PROJECT_ROOT / rel).read_text(encoding="utf-8")
            assert block in text, f"{rel} does not carry the generated block"


class TestDriftIsCaught:
    def test_hand_edited_table_fails_check(self, tree, capsys):
        doc = tree / "docs/explanation/permissions-and-reclaim.md"
        _edit(
            doc,
            "| `osgi` | no | yes | yes | yes |",
            "| `osgi` | yes | yes | yes | yes |",
        )

        assert sync_reclaim_table.main(["--check", "--root", str(tree)]) == 1
        err = capsys.readouterr().err
        assert "docs/explanation/permissions-and-reclaim.md" in err
        assert ".agents/skills" not in err, "the untouched file was reported"

    def test_changed_source_list_fails_check_in_both_docs(self, tree, capsys):
        """Dropping `routes` from rescue is LDM-#2144 reintroduced; the docs
        must not go on saying rescue reclaims it."""
        _edit(
            tree / "ldm_core/handlers/system.py",
            _RUN_LIST,
            '["deploy", "logs", "osgi", "files"]',
        )

        assert sync_reclaim_table.main(["--check", "--root", str(tree)]) == 1
        err = capsys.readouterr().err
        for rel in sync_reclaim_table.TARGETS:
            assert rel in err
        assert "-| `routes` | yes | yes | yes | yes |" in err
        assert "+| `routes` | yes | yes | no | yes |" in err

    def test_new_path_key_adds_a_row(self, tree):
        _edit(
            tree / "ldm_core/config.py",
            _RUN_LIST,
            '["deploy", "logs", "osgi", "files", "routes", "scripts"]',
        )
        assert sync_reclaim_table.main(["--root", str(tree)]) == 0
        doc = (tree / "docs/explanation/permissions-and-reclaim.md").read_text()
        assert "| `scripts` | no | no | no | yes |" in doc

    def test_regeneration_fixes_drift_and_is_idempotent(self, tree):
        _edit(tree / "ldm_core/handlers/system.py", _RUN_LIST, '["deploy", "logs"]')
        assert sync_reclaim_table.main(["--root", str(tree)]) == 0
        first = {r: (tree / r).read_text() for r in sync_reclaim_table.TARGETS}
        assert sync_reclaim_table.main(["--root", str(tree)]) == 0
        second = {r: (tree / r).read_text() for r in sync_reclaim_table.TARGETS}
        assert first == second
        assert sync_reclaim_table.main(["--check", "--root", str(tree)]) == 0

    def test_editorial_text_outside_the_markers_is_preserved(self, tree):
        doc = tree / "docs/explanation/permissions-and-reclaim.md"
        _edit(doc, "## The `routes` tree is a special case", "## EDITED HEADING")
        _edit(tree / "ldm_core/handlers/system.py", _RUN_LIST, '["deploy", "logs"]')
        assert sync_reclaim_table.main(["--root", str(tree)]) == 0
        assert "## EDITED HEADING" in doc.read_text()


class TestUnreadableInputIsAnError:
    def test_loop_over_a_variable_is_an_error_not_a_missing_row(self):
        src = "KEYS = ['deploy']\nfor k in KEYS:\n    reclaim_volume_permissions(k)\n"
        with pytest.raises(ReclaimParseError, match="not a literal list"):
            parse_reclaim_lists(src)

    def test_non_string_entry_is_an_error(self):
        src = "for k in ['deploy', OTHER]:\n    utils.reclaim_volume_permissions(k)\n"
        with pytest.raises(ReclaimParseError, match="OTHER"):
            parse_reclaim_lists(src)

    def test_unreadable_list_in_a_site_fails_the_script(self, tree, capsys):
        _edit(tree / _RUN_PY, f"for p_key in {_RUN_LIST}:", "for p_key in KEYS:")
        assert sync_reclaim_table.main(["--check", "--root", str(tree)]) == 2
        assert "pipelines/run.py" in capsys.readouterr().err

    def test_site_with_no_reclaim_loop_fails(self, tree, capsys):
        _edit(tree / "ldm_core/config.py", "reclaim_volume_permissions(", "other(", 99)
        assert sync_reclaim_table.main(["--check", "--root", str(tree)]) == 2
        assert "no reclaim loop found" in capsys.readouterr().err

    def test_disagreeing_loops_in_one_file_fail(self, tree, capsys):
        _edit(tree / _RUN_PY, _RUN_LIST, '["deploy", "logs"]')  # first loop only
        assert sync_reclaim_table.main(["--check", "--root", str(tree)]) == 2
        assert "disagree" in capsys.readouterr().err

    def test_creation_loop_without_the_call_is_ignored(self):
        """Keyed on the call: a safe_mkdir list is not a reclaim list."""
        src = (
            "for d in ['a', 'b']:\n"
            "    safe_mkdir(d)\n"
            "for d in ['deploy']:\n"
            "    reclaim_volume_permissions(d)\n"
        )
        assert parse_reclaim_lists(src) == [["deploy"]]

    def test_outer_loop_over_a_variable_is_not_blamed_for_an_inner_one(self):
        src = (
            "for project in projects:\n"
            "    for d in ['deploy']:\n"
            "        reclaim_volume_permissions(d)\n"
        )
        assert parse_reclaim_lists(src) == [["deploy"]]


class TestMarkers:
    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (lambda t: t.replace(sync_reclaim_table.START_MARKER, ""), "found 0 and 1"),
            (
                lambda t: t.replace(
                    sync_reclaim_table.END_MARKER,
                    sync_reclaim_table.END_MARKER
                    + "\n"
                    + sync_reclaim_table.END_MARKER,
                ),
                "found 1 and 2",
            ),
            (
                lambda t: (
                    t.replace(sync_reclaim_table.START_MARKER, "@@S@@")
                    .replace(
                        sync_reclaim_table.END_MARKER, sync_reclaim_table.START_MARKER
                    )
                    .replace("@@S@@", sync_reclaim_table.END_MARKER)
                ),
                "comes before",
            ),
        ],
        ids=["missing", "duplicated", "reversed"],
    )
    def test_bad_markers_are_a_configuration_error(self, tree, capsys, mutate, message):
        doc = tree / ".agents/skills/ldm-architecture/SKILL.md"
        doc.write_text(mutate(doc.read_text()), encoding="utf-8")
        before = doc.read_text()

        assert sync_reclaim_table.main(["--root", str(tree)]) == 2
        assert message in capsys.readouterr().err
        assert doc.read_text() == before, "a bad-marker file was rewritten"
