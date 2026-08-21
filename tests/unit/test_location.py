"""Tests for Location entity — Slice 1.

Covers:
- Location model validation
- LocationRepository CRUD and versioning
- Scene.location_id nullable FK persistence
- ReferenceAsset.location_id persistence
- Backward compatibility (existing Scene.location string, legacy ENVIRONMENT refs)
"""
from __future__ import annotations

import pytest

from film_director.models.canonical import Location, Scene
from film_director.models.provenance import Provenance
from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    LocationRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
    ReferenceAssetRepository,
)

NOW = "2026-08-21T00:00:00+00:00"
SHA = "a" * 64


def _prov(asset_id: str = "wc_1") -> Provenance:
    return Provenance(
        source_system="wind_comic",
        source_project_id="wc_proj_1",
        source_asset_id=asset_id,
        source_asset_version=1,
        imported_at=NOW,
        source_hash=SHA,
    )


def _make_project(db: Database, project_id: str = "proj_1") -> None:
    from film_director.models.canonical import ProductionProject

    repo = ProjectRepository(db)
    repo.save_project(ProductionProject(
        id=project_id,
        wc_project_id=f"wc_{project_id}",
        title="Test",
        created_at=NOW,
        updated_at=NOW,
        provenance=_prov(f"wc_{project_id}"),
    ))


def _make_sequence(db: Database, project_id: str = "proj_1", seq_id: str = "seq_1") -> None:
    from film_director.models.canonical import Sequence

    SequenceRepository(db).save_sequence(Sequence(
        id=seq_id, project_id=project_id, name="Main", order_index=0,
    ))


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


# ---------------------------------------------------------------------------
# Location model validation
# ---------------------------------------------------------------------------

class TestLocationModel:
    def test_minimal_construction(self):
        loc = Location(id="loc_1", project_id="proj_1", name="Kitchen")
        assert loc.id == "loc_1"
        assert loc.description == ""
        assert loc.source == "human"
        assert loc.version == 1
        assert loc.created_at == ""

    def test_full_construction(self):
        loc = Location(
            id="loc_1", project_id="proj_1", name="Kitchen",
            description="1970s linoleum kitchen", source="llm",
            version=3, created_at=NOW, updated_at=NOW,
        )
        assert loc.version == 3
        assert loc.source == "llm"

    def test_source_values(self):
        for src in ("wind_comic", "llm", "human"):
            loc = Location(id="loc_1", project_id="proj_1", name="X", source=src)
            assert loc.source == src

    def test_invalid_source_rejected(self):
        with pytest.raises(Exception):
            Location(id="loc_1", project_id="proj_1", name="X", source="invalid")


# ---------------------------------------------------------------------------
# LocationRepository CRUD
# ---------------------------------------------------------------------------

class TestLocationRepository:
    def test_create_and_get(self, db):
        _make_project(db)
        repo = LocationRepository(db)
        loc = Location(
            id="loc_1", project_id="proj_1", name="Kitchen",
            description="A small kitchen", source="human",
            version=1, created_at=NOW, updated_at=NOW,
        )
        repo.save(loc)
        loaded = repo.get("loc_1")
        assert loaded is not None
        assert loaded.id == "loc_1"
        assert loaded.name == "Kitchen"
        assert loaded.description == "A small kitchen"
        assert loaded.source == "human"
        assert loaded.version == 1

    def test_get_nonexistent(self, db):
        repo = LocationRepository(db)
        assert repo.get("nonexistent") is None

    def test_list_by_project(self, db):
        _make_project(db)
        repo = LocationRepository(db)
        repo.save(Location(
            id="loc_a", project_id="proj_1", name="Rooftop",
            created_at=NOW, updated_at=NOW,
        ))
        repo.save(Location(
            id="loc_b", project_id="proj_1", name="Kitchen",
            created_at=NOW, updated_at=NOW,
        ))
        locs = repo.list_by_project("proj_1")
        assert len(locs) == 2
        assert locs[0].name == "Kitchen"  # sorted by name
        assert locs[1].name == "Rooftop"

    def test_list_by_project_empty(self, db):
        repo = LocationRepository(db)
        assert repo.list_by_project("proj_none") == []

    def test_list_by_project_scoped(self, db):
        _make_project(db, "proj_1")
        _make_project(db, "proj_2")
        repo = LocationRepository(db)
        repo.save(Location(
            id="loc_1", project_id="proj_1", name="A",
            created_at=NOW, updated_at=NOW,
        ))
        repo.save(Location(
            id="loc_2", project_id="proj_2", name="B",
            created_at=NOW, updated_at=NOW,
        ))
        assert len(repo.list_by_project("proj_1")) == 1
        assert len(repo.list_by_project("proj_2")) == 1

    def test_multiple_locations_per_project(self, db):
        _make_project(db)
        repo = LocationRepository(db)
        for i in range(4):
            repo.save(Location(
                id=f"loc_{i}", project_id="proj_1", name=f"Place {i}",
                created_at=NOW, updated_at=NOW,
            ))
        assert len(repo.list_by_project("proj_1")) == 4


class TestLocationUpdate:
    def test_update_increments_version(self, db):
        _make_project(db)
        repo = LocationRepository(db)
        repo.save(Location(
            id="loc_1", project_id="proj_1", name="Kitchen",
            description="Old desc", version=1,
            created_at=NOW, updated_at=NOW,
        ))
        updated = repo.update("loc_1", description="New desc")
        assert updated is not None
        assert updated.version == 2
        assert updated.description == "New desc"
        assert updated.name == "Kitchen"  # unchanged

    def test_update_name_only(self, db):
        _make_project(db)
        repo = LocationRepository(db)
        repo.save(Location(
            id="loc_1", project_id="proj_1", name="Old",
            description="Desc", version=1,
            created_at=NOW, updated_at=NOW,
        ))
        updated = repo.update("loc_1", name="New")
        assert updated.name == "New"
        assert updated.description == "Desc"
        assert updated.version == 2

    def test_update_nonexistent_returns_none(self, db):
        repo = LocationRepository(db)
        assert repo.update("nonexistent", name="X") is None

    def test_update_persists(self, db):
        _make_project(db)
        repo = LocationRepository(db)
        repo.save(Location(
            id="loc_1", project_id="proj_1", name="K",
            version=1, created_at=NOW, updated_at=NOW,
        ))
        repo.update("loc_1", description="Updated")
        reloaded = repo.get("loc_1")
        assert reloaded.description == "Updated"
        assert reloaded.version == 2

    def test_sequential_updates_increment(self, db):
        _make_project(db)
        repo = LocationRepository(db)
        repo.save(Location(
            id="loc_1", project_id="proj_1", name="K",
            version=1, created_at=NOW, updated_at=NOW,
        ))
        repo.update("loc_1", description="V2")
        repo.update("loc_1", description="V3")
        reloaded = repo.get("loc_1")
        assert reloaded.version == 3
        assert reloaded.description == "V3"


class TestLocationDelete:
    def test_delete_existing(self, db):
        _make_project(db)
        repo = LocationRepository(db)
        repo.save(Location(
            id="loc_1", project_id="proj_1", name="K",
            created_at=NOW, updated_at=NOW,
        ))
        assert repo.delete("loc_1") is True
        assert repo.get("loc_1") is None

    def test_delete_nonexistent(self, db):
        repo = LocationRepository(db)
        assert repo.delete("nonexistent") is False


# ---------------------------------------------------------------------------
# Scene.location_id persistence
# ---------------------------------------------------------------------------

class TestSceneLocationId:
    def test_scene_with_location_id_none(self, db):
        """Existing behavior: Scene without location_id loads correctly."""
        _make_project(db)
        _make_sequence(db)
        repo = SceneRepository(db)
        scene = Scene(
            id="sc_1", sequence_id="seq_1", wc_scene_id="wc_sc_1",
            name="Scene 1", location="hospital", description="desc",
            order_index=0, provenance=_prov("wc_sc_1"),
        )
        repo.save_scene(scene)
        loaded = repo.get_scene("sc_1")
        assert loaded is not None
        assert loaded.location_id is None
        assert loaded.location == "hospital"  # legacy string preserved

    def test_scene_with_location_id_set(self, db):
        """Scene with location_id persists and loads correctly."""
        _make_project(db)
        _make_sequence(db)
        loc_repo = LocationRepository(db)
        loc_repo.save(Location(
            id="loc_1", project_id="proj_1", name="Kitchen",
            created_at=NOW, updated_at=NOW,
        ))
        scene_repo = SceneRepository(db)
        scene = Scene(
            id="sc_1", sequence_id="seq_1", wc_scene_id="wc_sc_1",
            name="Scene 1", location="kitchen", description="desc",
            location_id="loc_1", order_index=0, provenance=_prov("wc_sc_1"),
        )
        scene_repo.save_scene(scene)
        loaded = scene_repo.get_scene("sc_1")
        assert loaded.location_id == "loc_1"
        assert loaded.location == "kitchen"  # legacy string still present

    def test_multiple_scenes_share_location(self, db):
        """Two scenes can reference the same Location."""
        _make_project(db)
        _make_sequence(db)
        loc_repo = LocationRepository(db)
        loc_repo.save(Location(
            id="loc_1", project_id="proj_1", name="Kitchen",
            created_at=NOW, updated_at=NOW,
        ))
        scene_repo = SceneRepository(db)
        for i in range(2):
            scene_repo.save_scene(Scene(
                id=f"sc_{i}", sequence_id="seq_1", wc_scene_id=f"wc_sc_{i}",
                name=f"Scene {i}", location="kitchen", description="",
                location_id="loc_1", order_index=i, provenance=_prov(f"wc_sc_{i}"),
            ))
        scenes = scene_repo.get_scenes_by_sequence("seq_1")
        assert len(scenes) == 2
        assert all(s.location_id == "loc_1" for s in scenes)

    def test_legacy_scene_location_string_preserved(self, db):
        """Scene.location string is not affected by location_id."""
        _make_project(db)
        _make_sequence(db)
        scene_repo = SceneRepository(db)
        scene_repo.save_scene(Scene(
            id="sc_1", sequence_id="seq_1", wc_scene_id="wc_sc_1",
            name="S1", location="abandoned hospital corridor",
            description="long dark corridor",
            order_index=0, provenance=_prov("wc_sc_1"),
        ))
        loaded = scene_repo.get_scene("sc_1")
        assert loaded.location == "abandoned hospital corridor"
        assert loaded.location_id is None


# ---------------------------------------------------------------------------
# ReferenceAsset.location_id persistence
# ---------------------------------------------------------------------------

def _make_env_ref(
    asset_id: str = "ref_1",
    project_id: str = "proj_1",
    location_id: str | None = None,
) -> ReferenceAsset:
    return ReferenceAsset(
        id=asset_id, project_id=project_id,
        location_id=location_id,
        kind=ReferenceKind.ENVIRONMENT,
        source=ReferenceSource.GENERATED,
        managed_path="refs/env.png",
        content_sha256=SHA,
        source_provenance="rgreq_1",
        status=ReferenceStatus.CANDIDATE,
        source_state=ReferenceSourceState.CURRENT,
        width=1024, height=1024,
        created_at=NOW, updated_at=NOW,
    )


class TestReferenceAssetLocationId:
    def test_env_ref_with_location_id_none(self, db):
        """Legacy ENVIRONMENT ref with location_id=None loads correctly."""
        _make_project(db)
        repo = ReferenceAssetRepository(db)
        ref = _make_env_ref()
        repo.save(ref)
        loaded = repo.get("ref_1")
        assert loaded is not None
        assert loaded.location_id is None
        assert loaded.kind == ReferenceKind.ENVIRONMENT

    def test_env_ref_with_location_id_set(self, db):
        """ENVIRONMENT ref with location_id persists and loads."""
        _make_project(db)
        loc_repo = LocationRepository(db)
        loc_repo.save(Location(
            id="loc_1", project_id="proj_1", name="Kitchen",
            created_at=NOW, updated_at=NOW,
        ))
        ref_repo = ReferenceAssetRepository(db)
        ref = _make_env_ref(location_id="loc_1")
        ref_repo.save(ref)
        loaded = ref_repo.get("ref_1")
        assert loaded.location_id == "loc_1"

    def test_character_ref_location_id_none(self, db):
        """CHARACTER_BODY ref has location_id=None (unchanged behavior)."""
        _make_project(db)
        ref_repo = ReferenceAssetRepository(db)
        ref = ReferenceAsset(
            id="ref_char", project_id="proj_1",
            character_id="char_1",
            kind=ReferenceKind.CHARACTER_BODY,
            source=ReferenceSource.GENERATED,
            managed_path="refs/char.png",
            content_sha256=SHA,
            source_provenance="rgreq_2",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024,
            created_at=NOW, updated_at=NOW,
        )
        ref_repo.save(ref)
        loaded = ref_repo.get("ref_char")
        assert loaded.location_id is None
        assert loaded.character_id == "char_1"

    def test_list_by_project_includes_location_id(self, db):
        """list_by_project returns refs with location_id populated."""
        _make_project(db)
        loc_repo = LocationRepository(db)
        loc_repo.save(Location(
            id="loc_1", project_id="proj_1", name="K",
            created_at=NOW, updated_at=NOW,
        ))
        ref_repo = ReferenceAssetRepository(db)
        ref_repo.save(_make_env_ref(location_id="loc_1"))
        refs = ref_repo.list_by_project("proj_1")
        assert len(refs) == 1
        assert refs[0].location_id == "loc_1"


# ---------------------------------------------------------------------------
# Backward compatibility — existing schema migration
# ---------------------------------------------------------------------------

class TestSchemaMigration:
    def test_init_schema_creates_locations_table(self, db):
        """locations table exists after init_schema."""
        with db.connection() as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "locations" in tables

    def test_init_schema_adds_location_id_to_scenes(self, db):
        """scenes table has location_id column after init_schema."""
        with db.connection() as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(scenes)").fetchall()
            }
        assert "location_id" in cols

    def test_init_schema_adds_location_id_to_reference_assets(self, db):
        """reference_assets table has location_id column after init_schema."""
        with db.connection() as conn:
            cols = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(reference_assets)"
                ).fetchall()
            }
        assert "location_id" in cols

    def test_double_init_schema_idempotent(self, db):
        """Calling init_schema twice does not raise."""
        db.init_schema()  # second call
        with db.connection() as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "locations" in tables
