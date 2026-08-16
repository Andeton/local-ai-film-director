"""TDD tests for M5.F — reference staleness on character appearance changes.

Tests the shared fingerprint helper, repository stale transition, and
source-specific policies.
"""
from __future__ import annotations

import os

import pytest

from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
    compute_appearance_fingerprint,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import ReferenceAssetRepository


# ---------------------------------------------------------------------------
# Shared fingerprint helper
# ---------------------------------------------------------------------------

class TestComputeAppearanceFingerprint:
    def test_same_appearance_same_fingerprint(self):
        a = compute_appearance_fingerprint("dark hair, trench coat")
        b = compute_appearance_fingerprint("dark hair, trench coat")
        assert a == b

    def test_changed_appearance_different_fingerprint(self):
        a = compute_appearance_fingerprint("dark hair, trench coat")
        b = compute_appearance_fingerprint("blond hair, leather jacket")
        assert a != b

    def test_returns_64_hex_chars(self):
        fp = compute_appearance_fingerprint("any appearance")
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_generation_and_stale_use_same_helper(self):
        """Verify reference_generator delegates to shared helper."""
        from film_director.generation.reference_generator import _compute_appearance_hash
        shared = compute_appearance_fingerprint("test appearance")
        generator = _compute_appearance_hash("test appearance")
        assert shared == generator

    def test_empty_appearance(self):
        fp = compute_appearance_fingerprint("")
        assert len(fp) == 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_asset(
    asset_id: str,
    character_id: str = "char-1",
    project_id: str = "proj-1",
    source: ReferenceSource = ReferenceSource.GENERATED,
    source_state: ReferenceSourceState = ReferenceSourceState.CURRENT,
    status: ReferenceStatus = ReferenceStatus.APPROVED,
    pinned: bool = False,
    source_fingerprint: str | None = None,
) -> ReferenceAsset:
    return ReferenceAsset(
        id=asset_id,
        project_id=project_id,
        character_id=character_id,
        kind=ReferenceKind.CHARACTER_BODY,
        source=source,
        managed_path=f"/storage/references/{project_id}/{asset_id}/original.png",
        content_sha256="a" * 64,
        source_provenance="gen-req-1",
        source_fingerprint=source_fingerprint,
        status=status,
        source_state=source_state,
        pinned=pinned,
        width=1024,
        height=1024,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


@pytest.fixture
def db(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    d = Database(db_path)
    d.init_schema()
    # Insert FK-required parent entities
    with d.connection() as conn:
        conn.execute(
            "INSERT INTO production_projects (id, wc_project_id, title, status, "
            "created_at, updated_at, prov_source_system, prov_source_project_id, "
            "prov_source_asset_id, prov_imported_at, prov_source_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj-1", "wc-p1", "Test", "active", "2026-01-01", "2026-01-01",
             "test", "p1", "a1", "2026-01-01", "h"),
        )
        conn.execute(
            "INSERT INTO production_projects (id, wc_project_id, title, status, "
            "created_at, updated_at, prov_source_system, prov_source_project_id, "
            "prov_source_asset_id, prov_imported_at, prov_source_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj-2", "wc-p2", "Test2", "active", "2026-01-01", "2026-01-01",
             "test", "p2", "a2", "2026-01-01", "h"),
        )
    return d


# ---------------------------------------------------------------------------
# Repository stale transition
# ---------------------------------------------------------------------------

class TestMarkGeneratedStaleForCharacter:
    def test_mismatched_generated_current_becomes_stale(self, db):
        repo = ReferenceAssetRepository(db)
        old_fp = compute_appearance_fingerprint("dark hair")
        new_fp = compute_appearance_fingerprint("blond hair")
        repo.save(_make_asset("ref-1", source_fingerprint=old_fp))

        count = repo.mark_generated_stale_for_character(
            project_id="proj-1", character_id="char-1",
            current_fingerprint=new_fp,
        )
        assert count == 1

        asset = repo.get("ref-1")
        assert asset.source_state == ReferenceSourceState.STALE
        assert asset.updated_at != "2026-01-01T00:00:00"

    def test_matching_generated_remains_current(self, db):
        repo = ReferenceAssetRepository(db)
        fp = compute_appearance_fingerprint("dark hair")
        repo.save(_make_asset("ref-1", source_fingerprint=fp))

        count = repo.mark_generated_stale_for_character(
            project_id="proj-1", character_id="char-1",
            current_fingerprint=fp,
        )
        assert count == 0

        asset = repo.get("ref-1")
        assert asset.source_state == ReferenceSourceState.CURRENT

    def test_missing_fingerprint_becomes_stale(self, db):
        """GENERATED ref with NULL source_fingerprint is not verifiably current."""
        repo = ReferenceAssetRepository(db)
        repo.save(_make_asset("ref-1", source_fingerprint=None))

        count = repo.mark_generated_stale_for_character(
            project_id="proj-1", character_id="char-1",
            current_fingerprint=compute_appearance_fingerprint("anything"),
        )
        assert count == 1

        asset = repo.get("ref-1")
        assert asset.source_state == ReferenceSourceState.STALE

    def test_user_upload_remains_current(self, db):
        repo = ReferenceAssetRepository(db)
        repo.save(_make_asset("ref-1", source=ReferenceSource.USER_UPLOAD,
                              source_fingerprint=None))

        count = repo.mark_generated_stale_for_character(
            project_id="proj-1", character_id="char-1",
            current_fingerprint=compute_appearance_fingerprint("new look"),
        )
        assert count == 0

        asset = repo.get("ref-1")
        assert asset.source_state == ReferenceSourceState.CURRENT

    def test_wind_comic_source_unchanged(self, db):
        repo = ReferenceAssetRepository(db)
        old_fp = compute_appearance_fingerprint("old appearance")
        repo.save(_make_asset("ref-1", source=ReferenceSource.WIND_COMIC,
                              source_fingerprint=old_fp))

        count = repo.mark_generated_stale_for_character(
            project_id="proj-1", character_id="char-1",
            current_fingerprint=compute_appearance_fingerprint("new appearance"),
        )
        assert count == 0

        asset = repo.get("ref-1")
        assert asset.source_state == ReferenceSourceState.CURRENT

    def test_already_stale_remains_stale(self, db):
        repo = ReferenceAssetRepository(db)
        old_fp = compute_appearance_fingerprint("old")
        repo.save(_make_asset("ref-1", source_fingerprint=old_fp,
                              source_state=ReferenceSourceState.STALE))

        count = repo.mark_generated_stale_for_character(
            project_id="proj-1", character_id="char-1",
            current_fingerprint=compute_appearance_fingerprint("new"),
        )
        # Already stale — not counted again
        assert count == 0

    def test_status_and_pin_preserved(self, db):
        repo = ReferenceAssetRepository(db)
        old_fp = compute_appearance_fingerprint("old")
        repo.save(_make_asset("ref-1", source_fingerprint=old_fp,
                              status=ReferenceStatus.APPROVED, pinned=True))

        repo.mark_generated_stale_for_character(
            project_id="proj-1", character_id="char-1",
            current_fingerprint=compute_appearance_fingerprint("new"),
        )

        asset = repo.get("ref-1")
        assert asset.source_state == ReferenceSourceState.STALE
        assert asset.status == ReferenceStatus.APPROVED  # preserved
        assert asset.pinned is True  # preserved

    def test_sha_path_provenance_dimensions_preserved(self, db):
        repo = ReferenceAssetRepository(db)
        old_fp = compute_appearance_fingerprint("old")
        original = _make_asset("ref-1", source_fingerprint=old_fp)
        repo.save(original)

        repo.mark_generated_stale_for_character(
            project_id="proj-1", character_id="char-1",
            current_fingerprint=compute_appearance_fingerprint("new"),
        )

        asset = repo.get("ref-1")
        assert asset.content_sha256 == original.content_sha256
        assert asset.managed_path == original.managed_path
        assert asset.source_provenance == original.source_provenance
        assert asset.source_fingerprint == original.source_fingerprint
        assert asset.width == original.width
        assert asset.height == original.height
        assert asset.created_at == original.created_at

    def test_only_correct_project_and_character_affected(self, db):
        repo = ReferenceAssetRepository(db)
        old_fp = compute_appearance_fingerprint("old")
        # Same project, same character
        repo.save(_make_asset("ref-target", source_fingerprint=old_fp))
        # Same project, different character
        repo.save(_make_asset("ref-other-char", character_id="char-2",
                              source_fingerprint=old_fp))
        # Different project, same character
        repo.save(_make_asset("ref-other-proj", project_id="proj-2",
                              source_fingerprint=old_fp))

        count = repo.mark_generated_stale_for_character(
            project_id="proj-1", character_id="char-1",
            current_fingerprint=compute_appearance_fingerprint("new"),
        )
        assert count == 1

        assert repo.get("ref-target").source_state == ReferenceSourceState.STALE
        assert repo.get("ref-other-char").source_state == ReferenceSourceState.CURRENT
        assert repo.get("ref-other-proj").source_state == ReferenceSourceState.CURRENT

    def test_idempotent(self, db):
        repo = ReferenceAssetRepository(db)
        old_fp = compute_appearance_fingerprint("old")
        repo.save(_make_asset("ref-1", source_fingerprint=old_fp))
        new_fp = compute_appearance_fingerprint("new")

        count1 = repo.mark_generated_stale_for_character(
            "proj-1", "char-1", new_fp,
        )
        count2 = repo.mark_generated_stale_for_character(
            "proj-1", "char-1", new_fp,
        )
        assert count1 == 1
        assert count2 == 0  # already stale, no change

    def test_revert_appearance_does_not_reactivate(self, db):
        """After staling, reverting appearance to original does NOT re-CURRENT."""
        repo = ReferenceAssetRepository(db)
        original_fp = compute_appearance_fingerprint("original look")
        repo.save(_make_asset("ref-1", source_fingerprint=original_fp))

        # Change appearance → stale
        repo.mark_generated_stale_for_character(
            "proj-1", "char-1", compute_appearance_fingerprint("new look"),
        )
        assert repo.get("ref-1").source_state == ReferenceSourceState.STALE

        # Revert appearance to original
        repo.mark_generated_stale_for_character(
            "proj-1", "char-1", original_fp,
        )
        # Still stale — no reactivation
        assert repo.get("ref-1").source_state == ReferenceSourceState.STALE
