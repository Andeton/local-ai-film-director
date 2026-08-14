"""Tests for persistence layer (Task 6 — M1.E).

TDD: written before implementation. Tests cover:
- Save/get round-trips for all entities
- UPSERT semantics (ON CONFLICT DO UPDATE, not INSERT OR REPLACE)
- UNIQUE constraint violations via raw SQL
- Foreign key enforcement
- mark_outdated() on all repos
- Collection serialisation (JSON round-trips)
- Restart persistence (data survives Database A → Database B)
- Idempotent schema init
- Transaction rollback
"""
import json
import sqlite3

import pytest

from film_director.models.canonical import (
    CharacterReference,
    ProductionProject,
    Scene,
    Sequence,
)
from film_director.models.provenance import Provenance
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    CharacterRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prov(**kw) -> Provenance:
    d = dict(
        source_system="wind_comic",
        source_project_id="p",
        source_asset_id="a",
        source_asset_version=1,
        imported_at="t",
        source_hash="a" * 64,
    )
    d.update(kw)
    return Provenance(**d)


def _project(id="p1", wc="wc1", title="Film", **kw) -> ProductionProject:
    return ProductionProject(
        id=id, wc_project_id=wc, title=title,
        created_at="t", updated_at="t",
        provenance=_prov(source_asset_id=wc),
        **kw,
    )


def _sequence(id="sq1", project_id="p1", name="Seq", order_index=0) -> Sequence:
    return Sequence(id=id, project_id=project_id, name=name, order_index=order_index)


def _scene(id="s1", sequence_id="sq1", wc_scene_id="ws1", name="Scene", **kw) -> Scene:
    return Scene(
        id=id, sequence_id=sequence_id, wc_scene_id=wc_scene_id,
        name=name, location="L", description="D", order_index=0,
        provenance=_prov(),
        **kw,
    )


def _char(id="c1", project_id="p1", wc_character_id="wc1", name="Hero", **kw) -> CharacterReference:
    return CharacterReference(
        id=id, project_id=project_id, wc_character_id=wc_character_id,
        name=name, description="", appearance="",
        provenance=_prov(),
        **kw,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def project_repo(db):
    return ProjectRepository(db)


@pytest.fixture
def seq_repo(db):
    return SequenceRepository(db)


@pytest.fixture
def scene_repo(db):
    """SceneRepository with parent project+sequence pre-created (sq1 → p1)."""
    repo = SceneRepository(db)
    # Scenes FK → sequences → production_projects; create the parent hierarchy
    proj_repo = ProjectRepository(db)
    proj_repo.save_project(_project("p1", "wc1", "Film"))
    seq_repo = SequenceRepository(db)
    seq_repo.save_sequence(_sequence("sq1", "p1"))
    return repo


@pytest.fixture
def char_repo(db):
    """CharacterRepository with parent project pre-created (p1)."""
    repo = CharacterRepository(db)
    # Characters FK → production_projects; create the parent
    proj_repo = ProjectRepository(db)
    proj_repo.save_project(_project("p1", "wc1", "Film"))
    return repo


# ---------------------------------------------------------------------------
# ProjectRepository
# ---------------------------------------------------------------------------

class TestProjectRepo:
    def test_save_get_roundtrip(self, project_repo):
        project_repo.save_project(_project())
        p = project_repo.get_project("p1")
        assert p is not None
        assert p.title == "Film"
        assert p.wc_project_id == "wc1"
        assert p.provenance.source_hash == "a" * 64
        assert p.provenance.source_system == "wind_comic"

    def test_get_returns_none_if_missing(self, project_repo):
        assert project_repo.get_project("no_such_id") is None

    def test_list_projects_empty(self, project_repo):
        assert project_repo.list_projects() == []

    def test_list_projects(self, project_repo):
        project_repo.save_project(_project("p1", "wc1", "A"))
        project_repo.save_project(_project("p2", "wc2", "B"))
        results = project_repo.list_projects()
        assert len(results) == 2
        titles = {r.title for r in results}
        assert titles == {"A", "B"}

    def test_upsert_updates_title(self, project_repo):
        project_repo.save_project(_project(title="V1"))
        project_repo.save_project(_project(title="V2"))
        assert project_repo.get_project("p1").title == "V2"
        assert len(project_repo.list_projects()) == 1  # no duplicate rows

    def test_upsert_does_not_create_second_row(self, project_repo):
        for _ in range(5):
            project_repo.save_project(_project())
        assert len(project_repo.list_projects()) == 1

    def test_get_project_by_wc_id(self, project_repo):
        project_repo.save_project(_project())
        p = project_repo.get_project_by_wc_id("wc1")
        assert p is not None
        assert p.id == "p1"

    def test_get_project_by_wc_id_missing(self, project_repo):
        assert project_repo.get_project_by_wc_id("ghost") is None

    def test_wc_project_id_unique_constraint(self, db, project_repo):
        """Raw INSERT with duplicate wc_project_id must raise IntegrityError."""
        project_repo.save_project(_project("pA", "same", "A"))
        with pytest.raises(sqlite3.IntegrityError):
            with db.connection() as c:
                c.execute(
                    "INSERT INTO production_projects "
                    "(id,wc_project_id,title,status,aspect,created_at,updated_at,"
                    "prov_source_system,prov_source_project_id,prov_source_asset_id,"
                    "prov_source_asset_version,prov_imported_at,prov_source_hash) "
                    "VALUES ('pB','same','B','draft','16:9','t','t','w','p','a',1,'t','h')"
                )

    def test_mark_outdated(self, project_repo):
        project_repo.save_project(_project())
        project_repo.mark_outdated("p1")
        assert project_repo.get_project("p1").status == "outdated"

    def test_provenance_version_none_roundtrip(self, project_repo):
        prov = _prov(source_asset_version=None)
        project_repo.save_project(ProductionProject(
            id="px", wc_project_id="wx", title="T",
            created_at="t", updated_at="t", provenance=prov,
        ))
        p = project_repo.get_project("px")
        assert p.provenance.source_asset_version is None

    def test_status_field_roundtrip(self, project_repo):
        project_repo.save_project(_project())
        project_repo.mark_outdated("p1")
        p = project_repo.get_project("p1")
        assert p.status == "outdated"


# ---------------------------------------------------------------------------
# SequenceRepository
# ---------------------------------------------------------------------------

class TestSequenceRepo:
    def test_save_and_get_by_project(self, db, seq_repo):
        # Sequence FK references production_projects — disable FK for this unit
        # test so we can save without a parent project.
        with db.connection() as c:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute(
                "INSERT INTO sequences (id,project_id,name,order_index) VALUES ('sq1','p1','S',0)"
            )
        seqs = seq_repo.get_sequences_by_project("p1")
        assert len(seqs) == 1
        assert seqs[0].name == "S"

    def test_save_sequence_with_parent(self, db, project_repo, seq_repo):
        project_repo.save_project(_project())
        seq_repo.save_sequence(_sequence())
        seqs = seq_repo.get_sequences_by_project("p1")
        assert len(seqs) == 1
        assert seqs[0].order_index == 0

    def test_get_sequences_empty(self, seq_repo):
        assert seq_repo.get_sequences_by_project("no_project") == []

    def test_upsert_sequence(self, project_repo, seq_repo):
        project_repo.save_project(_project())
        seq_repo.save_sequence(_sequence(name="V1"))
        seq_repo.save_sequence(_sequence(name="V2"))
        seqs = seq_repo.get_sequences_by_project("p1")
        assert len(seqs) == 1
        assert seqs[0].name == "V2"

    def test_sequence_fk_violation(self, seq_repo):
        """Saving a sequence without a parent project must raise IntegrityError."""
        with pytest.raises(sqlite3.IntegrityError):
            seq_repo.save_sequence(_sequence(project_id="ghost_project"))

    def test_multiple_sequences_order(self, project_repo, seq_repo):
        project_repo.save_project(_project())
        seq_repo.save_sequence(_sequence("sq1", "p1", "First", 0))
        seq_repo.save_sequence(_sequence("sq2", "p1", "Second", 1))
        seqs = seq_repo.get_sequences_by_project("p1")
        assert len(seqs) == 2


# ---------------------------------------------------------------------------
# SceneRepository
# ---------------------------------------------------------------------------

class TestSceneRepo:
    def test_save_get(self, scene_repo):
        scene_repo.save_scene(_scene())
        scenes = scene_repo.get_scenes_by_sequence("sq1")
        assert len(scenes) == 1
        assert scenes[0].name == "Scene"

    def test_get_scene_by_id(self, scene_repo):
        scene_repo.save_scene(_scene())
        s = scene_repo.get_scene("s1")
        assert s is not None
        assert s.wc_scene_id == "ws1"

    def test_get_scene_missing(self, scene_repo):
        assert scene_repo.get_scene("no_such") is None

    def test_upsert(self, scene_repo):
        scene_repo.save_scene(_scene(name="V1"))
        scene_repo.save_scene(_scene(name="V2"))
        assert scene_repo.get_scenes_by_sequence("sq1")[0].name == "V2"
        assert len(scene_repo.get_scenes_by_sequence("sq1")) == 1

    def test_wc_scene_id_unique_per_sequence(self, db, scene_repo):
        scene_repo.save_scene(_scene(id="s1", sequence_id="sq1", wc_scene_id="ws_same"))
        with pytest.raises(sqlite3.IntegrityError):
            with db.connection() as c:
                c.execute(
                    "INSERT INTO scenes "
                    "(id,sequence_id,wc_scene_id,name,location,description,order_index,status,"
                    "prov_source_system,prov_source_project_id,prov_source_asset_id,"
                    "prov_source_asset_version,prov_imported_at,prov_source_hash) "
                    "VALUES ('s2','sq1','ws_same','B','','',1,'draft','w','p','a',1,'t','h')"
                )

    def test_same_wc_scene_id_allowed_in_different_sequences(self, db, scene_repo):
        # Need a second sequence (sq2) under p1
        SequenceRepository(db).save_sequence(_sequence("sq2", "p1", "Seq2", 1))
        scene_repo.save_scene(_scene(id="s1", sequence_id="sq1", wc_scene_id="ws1"))
        scene_repo.save_scene(_scene(id="s2", sequence_id="sq2", wc_scene_id="ws1"))
        assert len(scene_repo.get_scenes_by_sequence("sq1")) == 1
        assert len(scene_repo.get_scenes_by_sequence("sq2")) == 1

    def test_mark_outdated(self, scene_repo):
        scene_repo.save_scene(_scene())
        scene_repo.mark_outdated("s1")
        assert scene_repo.get_scene("s1").status == "outdated"

    def test_provenance_roundtrip(self, scene_repo):
        prov = _prov(source_system="test_sys", source_hash="b" * 64)
        scene_repo.save_scene(Scene(
            id="s1", sequence_id="sq1", wc_scene_id="ws1",
            name="N", location="L", description="D", order_index=0,
            provenance=prov,
        ))
        s = scene_repo.get_scene("s1")
        assert s.provenance.source_system == "test_sys"
        assert s.provenance.source_hash == "b" * 64


# ---------------------------------------------------------------------------
# CharacterRepository
# ---------------------------------------------------------------------------

class TestCharRepo:
    def test_save_get(self, char_repo):
        char_repo.save_character(_char())
        chars = char_repo.get_characters_by_project("p1")
        assert len(chars) == 1
        assert chars[0].name == "Hero"
        assert chars[0].status == "active"

    def test_upsert(self, char_repo):
        char_repo.save_character(_char(name="V1"))
        char_repo.save_character(_char(name="V2"))
        chars = char_repo.get_characters_by_project("p1")
        assert len(chars) == 1
        assert chars[0].name == "V2"

    def test_wc_char_id_unique_per_project(self, db, char_repo):
        char_repo.save_character(_char(id="c1", project_id="p1", wc_character_id="wc_same"))
        with pytest.raises(sqlite3.IntegrityError):
            with db.connection() as c:
                c.execute(
                    "INSERT INTO character_references "
                    "(id,project_id,wc_character_id,name,description,appearance,"
                    "face_ref_path,turnaround_paths,visual_anchors,status,"
                    "prov_source_system,prov_source_project_id,prov_source_asset_id,"
                    "prov_source_asset_version,prov_imported_at,prov_source_hash) "
                    "VALUES ('c2','p1','wc_same','B','','',NULL,'[]','[]','active','w','p','a',1,'t','h')"
                )

    def test_same_wc_char_id_allowed_in_different_projects(self, db, char_repo):
        # Need a second project (p2) as parent
        ProjectRepository(db).save_project(_project("p2", "wc2", "Film2"))
        char_repo.save_character(_char(id="c1", project_id="p1", wc_character_id="wc1"))
        char_repo.save_character(_char(id="c2", project_id="p2", wc_character_id="wc1"))
        assert len(char_repo.get_characters_by_project("p1")) == 1
        assert len(char_repo.get_characters_by_project("p2")) == 1

    def test_mark_outdated(self, char_repo):
        char_repo.save_character(_char())
        char_repo.mark_outdated("c1")
        assert char_repo.get_characters_by_project("p1")[0].status == "outdated"

    def test_turnaround_paths_roundtrip(self, char_repo):
        paths = ["path/a.png", "path/b.png"]
        char_repo.save_character(_char(turnaround_paths=paths))
        chars = char_repo.get_characters_by_project("p1")
        assert chars[0].turnaround_paths == paths

    def test_visual_anchors_roundtrip(self, char_repo):
        anchors = ["scar on left cheek", "red cape"]
        char_repo.save_character(_char(visual_anchors=anchors))
        chars = char_repo.get_characters_by_project("p1")
        assert chars[0].visual_anchors == anchors

    def test_face_ref_path_none_roundtrip(self, char_repo):
        char_repo.save_character(_char(face_ref_path=None))
        c = char_repo.get_characters_by_project("p1")[0]
        assert c.face_ref_path is None

    def test_face_ref_path_value_roundtrip(self, char_repo):
        char_repo.save_character(_char(face_ref_path="refs/hero.png"))
        c = char_repo.get_characters_by_project("p1")[0]
        assert c.face_ref_path == "refs/hero.png"

    def test_provenance_roundtrip(self, char_repo):
        prov = _prov(source_asset_version=42, source_hash="c" * 64)
        char_repo.save_character(CharacterReference(
            id="c1", project_id="p1", wc_character_id="wc1",
            name="Hero", description="", appearance="",
            provenance=prov,
        ))
        c = char_repo.get_characters_by_project("p1")[0]
        assert c.provenance.source_asset_version == 42
        assert c.provenance.source_hash == "c" * 64


# ---------------------------------------------------------------------------
# Restart persistence
# ---------------------------------------------------------------------------

class TestRestart:
    def test_data_survives_new_database_instance(self, tmp_path):
        path = str(tmp_path / "r.db")
        d1 = Database(path)
        d1.init_schema()
        ProjectRepository(d1).save_project(_project(title="Survives"))
        # Open a fresh Database instance on same file
        d2 = Database(path)
        d2.init_schema()
        p = ProjectRepository(d2).get_project("p1")
        assert p is not None
        assert p.title == "Survives"

    def test_scenes_survive_restart(self, tmp_path):
        path = str(tmp_path / "r2.db")
        d1 = Database(path)
        d1.init_schema()
        # Create parent hierarchy for FK enforcement
        ProjectRepository(d1).save_project(_project())
        SequenceRepository(d1).save_sequence(_sequence())
        SceneRepository(d1).save_scene(_scene(name="Persistent"))
        d2 = Database(path)
        d2.init_schema()
        s = SceneRepository(d2).get_scene("s1")
        assert s is not None
        assert s.name == "Persistent"


# ---------------------------------------------------------------------------
# Idempotent schema init
# ---------------------------------------------------------------------------

class TestIdempotentSchema:
    def test_init_schema_twice_ok(self, tmp_path):
        path = str(tmp_path / "idm.db")
        d = Database(path)
        d.init_schema()
        # Insert data
        ProjectRepository(d).save_project(_project())
        # Second init must not drop tables or raise
        d.init_schema()
        p = ProjectRepository(d).get_project("p1")
        assert p is not None and p.title == "Film"

    def test_init_schema_three_times_ok(self, tmp_path):
        path = str(tmp_path / "idm2.db")
        d = Database(path)
        for _ in range(3):
            d.init_schema()


# ---------------------------------------------------------------------------
# Transaction rollback
# ---------------------------------------------------------------------------

class TestTransactions:
    def test_rollback_on_exception(self, db, project_repo):
        """Data written inside a failed transaction must not persist."""
        project_repo.save_project(_project("p_before"))

        try:
            with db.connection() as conn:
                conn.execute(
                    "INSERT INTO production_projects "
                    "(id,wc_project_id,title,status,aspect,created_at,updated_at,"
                    "prov_source_system,prov_source_project_id,prov_source_asset_id,"
                    "prov_source_asset_version,prov_imported_at,prov_source_hash) "
                    "VALUES ('p_rolled_back','wc_rb','R','draft','16:9','t','t','w','p','a',1,'t','h')"
                )
                raise RuntimeError("intentional failure")
        except RuntimeError:
            pass

        # The rolled-back row must not exist
        assert project_repo.get_project("p_rolled_back") is None
        # Data saved before the failing transaction must still be present
        assert project_repo.get_project("p_before") is not None

    def test_shared_connection_commit(self, db, project_repo, seq_repo):
        """Repositories accept an explicit connection; commit happens once."""
        with db.connection() as conn:
            project_repo.save_project(_project(), conn=conn)
            seq_repo.save_sequence(_sequence(), conn=conn)
        # Both must be visible after commit
        assert project_repo.get_project("p1") is not None
        assert len(seq_repo.get_sequences_by_project("p1")) == 1
