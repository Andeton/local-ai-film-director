"""Tests for Location Slice 2 — legacy migration/backfill.

Verifies that existing projects are correctly migrated to the canonical
Location model while preserving all historical data.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from film_director.models.canonical import Location
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
    ReferenceAssetRepository,
    SceneRepository,
    SequenceRepository,
)

NOW = "2026-08-21T00:00:00+00:00"
SHA = "a" * 64


def _deterministic_loc_id(project_id: str) -> str:
    """Mirror the migration's deterministic ID algorithm."""
    return "loc_" + hashlib.sha256(project_id.encode()).hexdigest()[:12]


def _seed_project(conn, project_id, env_desc=None, title="Test"):
    """Insert a minimal project with optional environment_description."""
    dc = {}
    if env_desc:
        dc["environment_description"] = env_desc
    conn.execute(
        "INSERT INTO production_projects "
        "(id, wc_project_id, title, status, aspect, director_context, "
        " created_at, updated_at, prov_source_system, prov_source_project_id, "
        " prov_source_asset_id, prov_source_asset_version, prov_imported_at, "
        " prov_source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (project_id, f"wc_{project_id}", title, "active", "16:9",
         json.dumps(dc), NOW, NOW,
         "wind_comic", f"wc_{project_id}", f"wc_{project_id}", 1, NOW, SHA),
    )


def _seed_sequence(conn, seq_id, project_id):
    conn.execute(
        "INSERT INTO sequences (id, project_id, name, order_index) "
        "VALUES (?,?,?,?)",
        (seq_id, project_id, "Main", 0),
    )


def _seed_scene(conn, scene_id, seq_id, location_str="", location_id=None):
    conn.execute(
        "INSERT INTO scenes "
        "(id, sequence_id, wc_scene_id, name, location, description, "
        " order_index, status, prov_source_system, prov_source_project_id, "
        " prov_source_asset_id, prov_source_asset_version, prov_imported_at, "
        " prov_source_hash, location_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (scene_id, seq_id, f"wc_{scene_id}", f"Scene {scene_id}",
         location_str, "", 0, "draft",
         "wind_comic", "wc1", f"wc_{scene_id}", 1, NOW, SHA, location_id),
    )


def _seed_env_ref(conn, ref_id, project_id, location_id=None):
    conn.execute(
        "INSERT INTO reference_assets "
        "(id, project_id, character_id, shot_id, kind, source, "
        " managed_path, content_sha256, source_provenance, "
        " source_fingerprint, status, source_state, pinned, "
        " width, height, created_at, updated_at, location_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ref_id, project_id, None, None, "environment", "generated",
         "refs/env.png", SHA, "rgreq_1", SHA, "approved", "current",
         0, 1024, 1024, NOW, NOW, location_id),
    )


def _seed_char_ref(conn, ref_id, project_id, char_id):
    conn.execute(
        "INSERT INTO reference_assets "
        "(id, project_id, character_id, shot_id, kind, source, "
        " managed_path, content_sha256, source_provenance, "
        " source_fingerprint, status, source_state, pinned, "
        " width, height, created_at, updated_at, location_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ref_id, project_id, char_id, None, "character_body", "generated",
         "refs/char.png", SHA, "rgreq_2", SHA, "approved", "current",
         0, 1024, 1024, NOW, NOW, None),
    )


def _seed_generation_request(conn, req_id, shot_id):
    conn.execute(
        "INSERT INTO generation_requests "
        "(id, shot_id, shot_version, generation_plan_id, generation_plan_version, "
        " prompt_artifact_id, prompt_artifact_version, workflow_definition_id, "
        " workflow_definition_version, workflow_template_fingerprint, "
        " take_number, parameters_snapshot, reference_snapshot, seed, "
        " status, submitted_at, completed_at, continuity_snapshot) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (req_id, shot_id, 1, "plan_1", 1, "h3p_1", 1, "h3_r2v_image_pack_v1",
         "1.0.0", SHA, 1, "[]",
         json.dumps([{"reference_kind": "environment", "reference_asset_id": "ref_env_1"}]),
         42, "succeeded", NOW, NOW, None),
    )


def _seed_take(conn, take_id, shot_id, req_id):
    conn.execute(
        "INSERT INTO takes "
        "(id, shot_id, generation_request_id, seed, video_path, "
        " status, is_favorite, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (take_id, shot_id, req_id, 42, "storage/takes/vid.mp4",
         "approved", 0, NOW),
    )


def _seed_continuity_state(conn, state_id, shot_id, scene_id):
    conn.execute(
        "INSERT INTO continuity_states "
        "(id, shot_id, scene_id, predecessor_shot_id, upstream_take_id, "
        " state, continuity_revision, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (state_id, shot_id, scene_id, None, None,
         "unresolved", 0, NOW, NOW),
    )


def _make_db_with_data(tmp_path, *, run_migration=True):
    """Create a DB with legacy data, optionally running full init_schema."""
    db_path = str(tmp_path / "test.db")
    if run_migration:
        db = Database(db_path)
        # Create schema WITHOUT Slice 2 migration first
        # We insert data, then re-run init_schema to trigger migration
        # But since init_schema runs migrations atomically, we need to
        # seed data in a pre-migration state.
        # Approach: create tables manually, seed, then run init_schema.
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return db_path, conn
    return db_path, None


@pytest.fixture
def legacy_db(tmp_path):
    """Fixture: DB with legacy project data, BEFORE migration runs."""
    db_path = str(tmp_path / "legacy.db")

    # Create a Database and init schema — this will also run migration,
    # but since there's no data yet, migration is a no-op.
    db = Database(db_path)
    db.init_schema()

    # Now seed legacy data (projects with environment_description but
    # no Location records, scenes with location_id=NULL, env refs with
    # location_id=NULL).
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    _seed_project(conn, "proj_1", env_desc="A dimly-lit New York apartment kitchen")
    _seed_sequence(conn, "seq_1", "proj_1")
    _seed_scene(conn, "sc_1", "seq_1", location_str="apartment kitchen")
    _seed_scene(conn, "sc_2", "seq_1", location_str="apartment hallway")
    _seed_env_ref(conn, "ref_env_1", "proj_1")

    conn.commit()
    conn.close()

    # Re-init schema to trigger migration on existing data
    db2 = Database(db_path)
    db2.init_schema()

    return db2


@pytest.fixture
def multi_project_db(tmp_path):
    """Fixture: DB with multiple projects, some with env_desc, some without."""
    db_path = str(tmp_path / "multi.db")
    db = Database(db_path)
    db.init_schema()

    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Project A: has environment_description
    _seed_project(conn, "proj_a", env_desc="Sunny rooftop terrace")
    _seed_sequence(conn, "seq_a", "proj_a")
    _seed_scene(conn, "sc_a1", "seq_a", location_str="rooftop")
    _seed_env_ref(conn, "ref_env_a", "proj_a")

    # Project B: NO environment_description
    _seed_project(conn, "proj_b", env_desc=None)
    _seed_sequence(conn, "seq_b", "proj_b")
    _seed_scene(conn, "sc_b1", "seq_b", location_str="office")

    # Project C: has environment_description + char ref (not env)
    _seed_project(conn, "proj_c", env_desc="Dark subway platform")
    _seed_sequence(conn, "seq_c", "proj_c")
    _seed_scene(conn, "sc_c1", "seq_c")
    _seed_char_ref(conn, "ref_char_c", "proj_c", "char_1")

    conn.commit()
    conn.close()

    db2 = Database(db_path)
    db2.init_schema()
    return db2


# ---------------------------------------------------------------------------
# Case A: Project with environment_description creates one Location
# ---------------------------------------------------------------------------

class TestLegacyLocationCreation:
    def test_creates_one_location(self, legacy_db):
        repo = LocationRepository(legacy_db)
        locs = repo.list_by_project("proj_1")
        assert len(locs) == 1

    def test_location_description_matches(self, legacy_db):
        repo = LocationRepository(legacy_db)
        loc = repo.list_by_project("proj_1")[0]
        assert loc.description == "A dimly-lit New York apartment kitchen"

    def test_location_name_is_main_location(self, legacy_db):
        repo = LocationRepository(legacy_db)
        loc = repo.list_by_project("proj_1")[0]
        assert loc.name == "Main Location"

    def test_location_source_is_llm(self, legacy_db):
        repo = LocationRepository(legacy_db)
        loc = repo.list_by_project("proj_1")[0]
        assert loc.source == "llm"

    def test_location_version_is_1(self, legacy_db):
        repo = LocationRepository(legacy_db)
        loc = repo.list_by_project("proj_1")[0]
        assert loc.version == 1

    def test_location_id_is_deterministic(self, legacy_db):
        repo = LocationRepository(legacy_db)
        loc = repo.list_by_project("proj_1")[0]
        expected = _deterministic_loc_id("proj_1")
        assert loc.id == expected

    def test_location_project_id_correct(self, legacy_db):
        repo = LocationRepository(legacy_db)
        loc = repo.list_by_project("proj_1")[0]
        assert loc.project_id == "proj_1"


# ---------------------------------------------------------------------------
# Scene assignment
# ---------------------------------------------------------------------------

class TestSceneAssignment:
    def test_all_scenes_assigned(self, legacy_db):
        repo = SceneRepository(legacy_db)
        expected_loc = _deterministic_loc_id("proj_1")
        for sc_id in ("sc_1", "sc_2"):
            scene = repo.get_scene(sc_id)
            assert scene.location_id == expected_loc

    def test_scene_location_string_unchanged(self, legacy_db):
        repo = SceneRepository(legacy_db)
        sc1 = repo.get_scene("sc_1")
        sc2 = repo.get_scene("sc_2")
        assert sc1.location == "apartment kitchen"
        assert sc2.location == "apartment hallway"


# ---------------------------------------------------------------------------
# Environment ref assignment
# ---------------------------------------------------------------------------

class TestEnvRefAssignment:
    def test_env_ref_assigned(self, legacy_db):
        repo = ReferenceAssetRepository(legacy_db)
        ref = repo.get("ref_env_1")
        expected_loc = _deterministic_loc_id("proj_1")
        assert ref.location_id == expected_loc

    def test_env_ref_id_unchanged(self, legacy_db):
        repo = ReferenceAssetRepository(legacy_db)
        ref = repo.get("ref_env_1")
        assert ref.id == "ref_env_1"

    def test_env_ref_kind_unchanged(self, legacy_db):
        repo = ReferenceAssetRepository(legacy_db)
        ref = repo.get("ref_env_1")
        assert ref.kind == ReferenceKind.ENVIRONMENT

    def test_env_ref_status_unchanged(self, legacy_db):
        repo = ReferenceAssetRepository(legacy_db)
        ref = repo.get("ref_env_1")
        assert ref.status == ReferenceStatus.APPROVED
        assert ref.source_state == ReferenceSourceState.CURRENT


# ---------------------------------------------------------------------------
# Case B: Project without environment_description
# ---------------------------------------------------------------------------

class TestNoEnvDesc:
    def test_no_location_created(self, multi_project_db):
        repo = LocationRepository(multi_project_db)
        assert repo.list_by_project("proj_b") == []

    def test_scene_location_id_stays_null(self, multi_project_db):
        repo = SceneRepository(multi_project_db)
        sc = repo.get_scene("sc_b1")
        assert sc.location_id is None


# ---------------------------------------------------------------------------
# Non-ENVIRONMENT refs untouched
# ---------------------------------------------------------------------------

class TestNonEnvRefsUntouched:
    def test_char_ref_location_id_null(self, multi_project_db):
        repo = ReferenceAssetRepository(multi_project_db)
        ref = repo.get("ref_char_c")
        assert ref.location_id is None
        assert ref.kind == ReferenceKind.CHARACTER_BODY


# ---------------------------------------------------------------------------
# Multiple projects
# ---------------------------------------------------------------------------

class TestMultipleProjects:
    def test_each_project_gets_own_location(self, multi_project_db):
        repo = LocationRepository(multi_project_db)
        locs_a = repo.list_by_project("proj_a")
        locs_c = repo.list_by_project("proj_c")
        assert len(locs_a) == 1
        assert len(locs_c) == 1
        assert locs_a[0].id != locs_c[0].id

    def test_no_cross_project_assignment(self, multi_project_db):
        loc_repo = LocationRepository(multi_project_db)
        scene_repo = SceneRepository(multi_project_db)
        loc_a = loc_repo.list_by_project("proj_a")[0]
        sc_c1 = scene_repo.get_scene("sc_c1")
        assert sc_c1.location_id != loc_a.id

    def test_descriptions_match_project(self, multi_project_db):
        repo = LocationRepository(multi_project_db)
        loc_a = repo.list_by_project("proj_a")[0]
        loc_c = repo.list_by_project("proj_c")[0]
        assert loc_a.description == "Sunny rooftop terrace"
        assert loc_c.description == "Dark subway platform"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_rerun_creates_no_duplicate(self, legacy_db):
        # Re-init schema (triggers migration again)
        legacy_db.init_schema()
        repo = LocationRepository(legacy_db)
        assert len(repo.list_by_project("proj_1")) == 1

    def test_rerun_no_destructive_changes(self, legacy_db):
        repo = LocationRepository(legacy_db)
        before = repo.list_by_project("proj_1")[0]
        legacy_db.init_schema()
        after = repo.list_by_project("proj_1")[0]
        assert before.id == after.id
        assert before.description == after.description
        assert before.version == after.version


# ---------------------------------------------------------------------------
# Non-NULL location_id not overwritten
# ---------------------------------------------------------------------------

class TestPreExistingLocationId:
    def test_scene_with_existing_location_id_not_overwritten(self, tmp_path):
        db_path = str(tmp_path / "preexist.db")
        db = Database(db_path)
        db.init_schema()

        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")

        _seed_project(conn, "proj_x", env_desc="Some place")
        _seed_sequence(conn, "seq_x", "proj_x")

        # Create a Location first
        conn.execute(
            "INSERT INTO locations (id, project_id, name, description, source, "
            "version, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("loc_custom", "proj_x", "Custom", "Custom location", "human", 1, NOW, NOW),
        )
        # Scene already has a non-NULL location_id
        _seed_scene(conn, "sc_x1", "seq_x", location_id="loc_custom")
        conn.commit()
        conn.close()

        # Re-init triggers migration
        db2 = Database(db_path)
        db2.init_schema()

        scene_repo = SceneRepository(db2)
        sc = scene_repo.get_scene("sc_x1")
        assert sc.location_id == "loc_custom"  # NOT overwritten

    def test_env_ref_with_existing_location_id_not_overwritten(self, tmp_path):
        db_path = str(tmp_path / "preexist2.db")
        db = Database(db_path)
        db.init_schema()

        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")

        _seed_project(conn, "proj_y", env_desc="A place")
        conn.execute(
            "INSERT INTO locations (id, project_id, name, description, source, "
            "version, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("loc_existing", "proj_y", "Existing", "Desc", "human", 1, NOW, NOW),
        )
        _seed_env_ref(conn, "ref_existing", "proj_y", location_id="loc_existing")
        conn.commit()
        conn.close()

        db2 = Database(db_path)
        db2.init_schema()

        ref_repo = ReferenceAssetRepository(db2)
        ref = ref_repo.get("ref_existing")
        assert ref.location_id == "loc_existing"  # NOT overwritten


# ---------------------------------------------------------------------------
# Historical immutability
# ---------------------------------------------------------------------------

class TestHistoricalImmutability:
    def test_generation_request_unchanged(self, tmp_path):
        db_path = str(tmp_path / "hist.db")
        db = Database(db_path)
        db.init_schema()

        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")

        _seed_project(conn, "proj_h", env_desc="Hospital lobby")
        _seed_sequence(conn, "seq_h", "proj_h")
        _seed_scene(conn, "sc_h1", "seq_h")

        # Seed beat + shot + plan + prompt for FK chain
        conn.execute(
            "INSERT INTO beats (id, scene_id, dramatic_action, order_index, "
            "status, source, version, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("beat_h1", "sc_h1", "Action", 0, "draft", "llm", 1, NOW, NOW),
        )
        conn.execute(
            "INSERT INTO shots "
            "(id, beat_id, action, order_index, status, source, version, "
            " created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("shot_h1", "beat_h1", "Man enters", 0, "draft", "generated", 1, NOW, NOW),
        )
        conn.execute(
            "INSERT INTO generation_plans "
            "(id, shot_id, shot_version, strategy, duration_sec, status, version, "
            " created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("plan_1", "shot_h1", 1, "REFERENCE_TO_VIDEO", 5.0, "draft", 1, NOW, NOW),
        )
        conn.execute(
            "INSERT INTO h3_prompts "
            "(id, shot_id, generation_plan_id, source_shot_version, "
            " source_generation_plan_version, subject_definitions, summary, "
            " retention_analysis, detailed_description, rendered_prompt_text, "
            " created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("h3p_1", "shot_h1", "plan_1", 1, 1, "", "", "", "", "prompt", NOW),
        )
        _seed_env_ref(conn, "ref_env_h", "proj_h")

        # Snapshot the reference in a GenerationRequest
        ref_snapshot = json.dumps([{
            "reference_kind": "environment",
            "reference_asset_id": "ref_env_h",
            "picture_index": 2,
        }])
        _seed_generation_request(conn, "greq_h1", "shot_h1")

        # Update the snapshot to use our specific data
        conn.execute(
            "UPDATE generation_requests SET reference_snapshot = ? WHERE id = ?",
            (ref_snapshot, "greq_h1"),
        )

        _seed_take(conn, "take_h1", "shot_h1", "greq_h1")
        _seed_continuity_state(conn, "cont_h1", "shot_h1", "sc_h1")

        conn.commit()

        # Capture pre-migration state
        pre_greq = dict(conn.execute(
            "SELECT * FROM generation_requests WHERE id = ?", ("greq_h1",)
        ).fetchone())
        pre_take = dict(conn.execute(
            "SELECT * FROM takes WHERE id = ?", ("take_h1",)
        ).fetchone())
        pre_cont = dict(conn.execute(
            "SELECT * FROM continuity_states WHERE id = ?", ("cont_h1",)
        ).fetchone())

        conn.close()

        # Run migration
        db2 = Database(db_path)
        db2.init_schema()

        # Verify post-migration state matches
        with db2.connection() as c:
            post_greq = dict(c.execute(
                "SELECT * FROM generation_requests WHERE id = ?", ("greq_h1",)
            ).fetchone())
            post_take = dict(c.execute(
                "SELECT * FROM takes WHERE id = ?", ("take_h1",)
            ).fetchone())
            post_cont = dict(c.execute(
                "SELECT * FROM continuity_states WHERE id = ?", ("cont_h1",)
            ).fetchone())

        assert pre_greq == post_greq, "GenerationRequest mutated"
        assert pre_take == post_take, "Take mutated"
        assert pre_cont == post_cont, "ContinuityState mutated"

        # Verify reference_snapshot content unchanged
        snap = json.loads(post_greq["reference_snapshot"])
        assert snap[0]["reference_asset_id"] == "ref_env_h"
        assert snap[0]["reference_kind"] == "environment"
