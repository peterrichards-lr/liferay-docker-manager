"""Tests for LDM-#1703 (config tombstone) and LDM-#1704 (dry-run mount check).

Both defects share a shape: something that could not have run was judged as
though it had. LDM-#1704 compared a sentinel never written against a container
never started; LDM-#1703 threw away the one part of a project that cannot be
rebuilt along with the part that trivially can.
"""

import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ldm_core.utils import (
    TOMBSTONE_MEMBERS,
    archive_project_config,
)


@pytest.fixture
def project(tmp_path):
    """A project directory shaped like a real one, heavy parts included."""
    root = tmp_path / "proj-a"
    (root / "files").mkdir(parents=True)
    (root / "files" / "portal-ext.properties").write_text(
        "feature.flag.LPD-56718=true\nfeature.flag.LPD-74328=true\n"
    )
    (root / "osgi" / "configs").mkdir(parents=True)
    (root / "osgi" / "configs" / "com.example.cfg").write_text("x=1\n")
    (root / "routes").mkdir()
    (root / "routes" / "route.json").write_text("{}\n")
    (root / "meta").write_text("TAG=2026.q3.2\nPORT=8081\n")

    # The parts that must NOT be archived: reproducible, and the reason the
    # user asked for the space back.
    (root / "osgi" / "state").mkdir(parents=True)
    (root / "osgi" / "state" / "bundle.bin").write_text("x" * 4096)
    (root / "data").mkdir()
    (root / "data" / "db.bin").write_text("y" * 4096)
    (root / "logs").mkdir()
    (root / "logs" / "liferay.log").write_text("noise\n")
    return root


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Redirects ~/.ldm/removed at the Path.home() level."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    return home / ".ldm" / "removed"


class TestArchiveProjectConfig:
    def test_keeps_the_config_and_none_of_the_bulk(self, project, store):
        archive = archive_project_config(project, timestamp="20260914-000000")

        assert archive == store / "proj-a-20260914-000000.tar.gz"
        with tarfile.open(archive) as tar:
            names = set(tar.getnames())

        assert "meta" in names
        assert "files/portal-ext.properties" in names
        assert "osgi/configs/com.example.cfg" in names
        assert "routes/route.json" in names

        # The whole argument for the tombstone: these are reproducible from the
        # image and the seed, and archiving them would defeat the delete.
        assert not any(n.startswith("osgi/state") for n in names)
        assert not any(n.startswith("data") for n in names)
        assert not any(n.startswith("logs") for n in names)

    def test_the_flags_survive_a_round_trip(self, project, store, tmp_path):
        # The concrete loss this prevents: two feature flags obtained by
        # decompiling a jar, recorded nowhere else.
        archive = archive_project_config(project)
        restored = tmp_path / "restored"
        restored.mkdir()

        # safe_extract, not extractall: the repo's own semgrep rule forbids the
        # latter (tar slip), and a test that reached around the guard would be
        # exercising a path no caller is allowed to take.
        from ldm_core.utils import safe_extract

        with tarfile.open(archive) as tar:
            safe_extract(tar, restored)

        text = (restored / "files" / "portal-ext.properties").read_text()
        assert "feature.flag.LPD-56718=true" in text
        assert "feature.flag.LPD-74328=true" in text

    def test_is_far_smaller_than_the_project(self, project, store):
        archive = archive_project_config(project)

        bulk = sum(f.stat().st_size for f in project.rglob("*") if f.is_file())
        assert archive.stat().st_size < bulk / 4

    def test_returns_none_when_there_is_nothing_to_archive(self, tmp_path, store):
        empty = tmp_path / "empty"
        (empty / "logs").mkdir(parents=True)

        assert archive_project_config(empty) is None
        assert not store.exists()

    def test_archives_only_the_members_that_exist(self, tmp_path, store):
        partial = tmp_path / "partial"
        (partial / "files").mkdir(parents=True)
        (partial / "files" / "portal-ext.properties").write_text("a=b\n")

        archive = archive_project_config(partial)

        with tarfile.open(archive) as tar:
            assert tar.getnames() == ["files", "files/portal-ext.properties"]

    def test_a_failed_archive_never_blocks_the_deletion(self, project, store):
        # The user asked for the space back. A convenience that cannot be
        # written is not a reason to refuse.
        with patch("tarfile.open", side_effect=OSError("disk full")):
            assert archive_project_config(project) is None

    def test_dry_run_writes_nothing(self, project, store, monkeypatch):
        monkeypatch.setenv("LDM_DRY_RUN", "true")

        target = archive_project_config(project)

        assert target is not None
        assert not target.exists()
        assert not store.exists()

    def test_prunes_to_the_newest_entries(self, project, store):
        from ldm_core.utils import TOMBSTONE_KEEP

        for i in range(TOMBSTONE_KEEP + 5):
            archive_project_config(project, timestamp=f"20260914-{i:06d}")

        kept = sorted(store.glob("*.tar.gz"))
        assert len(kept) == TOMBSTONE_KEEP
        # Newest by name, which is newest by time given the stamp format.
        assert kept[-1].name.endswith(f"{TOMBSTONE_KEEP + 4:06d}.tar.gz")

    def test_declares_only_config_shaped_members(self):
        assert TOMBSTONE_MEMBERS == ("meta", "files", "osgi/configs", "routes")
        assert "data" not in TOMBSTONE_MEMBERS
        assert "osgi/state" not in TOMBSTONE_MEMBERS


class TestDryRunMountCheck:
    """LDM-#1704: the check must not run under dry run, and must say so."""

    def test_the_guard_precedes_the_sentinel_and_the_probe(self):
        # Asserted against the source rather than by driving the CLI, which
        # needs Docker and a registry. The ordering is the whole fix: the
        # sentinel write and the probe both no-op under dry run, so anything
        # judging their result is judging nothing.
        source = Path("ldm_core/handlers/base.py").read_text()

        guard = source.index("[DRY RUN] Skipping Docker mount verification")
        sentinel = source.index("LDM_VERIFY_", guard - 4000)
        broken = source.index("FATAL: VOLUME MOUNTING IS BROKEN")

        assert guard < sentinel
        assert guard < broken

    def test_reports_skipped_rather_than_passed(self):
        source = Path("ldm_core/handlers/base.py").read_text()
        message_at = source.index("[DRY RUN] Skipping Docker mount verification")
        window = source[message_at : message_at + 200]

        # "Skipping", not "OK": the check did not run, and claiming it passed
        # would be a different false claim.
        assert "Skipping" in window
        assert "nothing is written for it to probe" in window
