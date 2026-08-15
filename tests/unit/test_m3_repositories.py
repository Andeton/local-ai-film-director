"""Tests for M3 repositories: H3PromptRepository, GenerationRequestRepository, TakeRepository."""
import json
import os
import sqlite3
import tempfile

import pytest

from film_director.errors import PersistenceError
from film_director.generation.generation_request import GenerationRequest, Take
from film_director.generation.h3_prompt import H3PromptV1
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    GenerationRequestRepository,
    H3PromptRepository,
    TakeRepository,
)

VALID_SHA = "a" * 64


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    database = Database(db_path)
    database.init_schema()
    _insert_fk_parents(database)
    return database


def _prompt(**kw) -> H3PromptV1:
    defaults = dict(
        id="h3p-1",
        shot_id="shot-1",
        generation_plan_id="plan-1",
        source_shot_version=1,
        source_generation_plan_version=1,
        subject_definitions="<Subject 1> is Alice",
        summary="Alice walks forward. Tension.",
        retention_analysis="<Subject 1> preserved",
        detailed_description="Camera: medium. Action: walks.",
        overall_soundscape="rain",
        non_diegetic_music="piano",
        rendered_prompt_text="full prompt text",
        status="current",
        version=1,
        created_at="2026-08-16T10:00:00",
    )
    defaults.update(kw)
    return H3PromptV1(**defaults)


def _request(**kw) -> GenerationRequest:
    defaults = dict(
        id="req-1",
        shot_id="shot-1",
        shot_version=1,
        generation_plan_id="plan-1",
        generation_plan_version=1,
        prompt_artifact_id="h3p-fk",
        prompt_artifact_version=1,
        workflow_definition_id="h3_r2v_v1",
        workflow_definition_version="1.0.0",
        workflow_template_fingerprint=VALID_SHA,
        take_number=1,
        parameters_snapshot=[{"name": "seed", "node_id": "15", "field": "noise_seed", "value": 42}],
        reference_snapshot=[{
            "subject_index": 1,
            "character_id": "char_1",
            "character_name": "Alice",
            "picture_index": 1,
            "local_path": "/refs/alice.png",
            "content_sha256": VALID_SHA,
            "uploaded_filename": "m3_ref.png",
        }],
        seed=42,
        status="pending",
    )
    defaults.update(kw)
    return GenerationRequest(**defaults)


def _insert_fk_parents(db):
    """Insert minimal FK parent records for M3 repository tests."""
    with db.connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO production_projects (id, wc_project_id, title, status, aspect, "
            "created_at, updated_at, prov_source_system, prov_source_project_id, "
            "prov_source_asset_id, prov_source_asset_version, prov_imported_at, prov_source_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("proj-1", "wc-p1", "Test", "active", "16:9", "2026-01-01", "2026-01-01",
             "test", "p1", "a1", 1, "2026-01-01", "h"),
        )
        conn.execute("INSERT OR IGNORE INTO sequences (id, project_id, name, order_index) VALUES (?,?,?,?)",
                     ("seq-1", "proj-1", "S", 0))
        conn.execute(
            "INSERT OR IGNORE INTO scenes (id, sequence_id, wc_scene_id, name, order_index, "
            "prov_source_system, prov_source_project_id, prov_source_asset_id, "
            "prov_source_asset_version, prov_imported_at, prov_source_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("scene-1", "seq-1", "wc-s1", "S1", 0, "test", "p1", "s1", 1, "2026-01-01", "h"),
        )
        conn.execute("INSERT OR IGNORE INTO beats (id, scene_id, dramatic_action, order_index, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     ("beat-1", "scene-1", "a", 0, "2026-01-01", "2026-01-01"))
        for sid in ("shot-1", "shot-2"):
            conn.execute("INSERT OR IGNORE INTO shots (id, beat_id, dramatic_purpose, action, camera, order_index, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                         (sid, "beat-1", "t", "w", '{"shot_size":"medium"}', 0, "2026-01-01", "2026-01-01"))
        conn.execute("INSERT OR IGNORE INTO generation_plans (id, shot_id, shot_version, strategy, duration_sec, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                     ("plan-1", "shot-1", 1, "REFERENCE_TO_VIDEO", 5.0, "2026-01-01", "2026-01-01"))
        conn.execute(
            "INSERT OR IGNORE INTO h3_prompts (id, shot_id, generation_plan_id, source_shot_version, "
            "source_generation_plan_version, subject_definitions, summary, retention_analysis, "
            "detailed_description, rendered_prompt_text, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("h3p-fk", "shot-1", "plan-1", 1, 1, "s", "s", "r", "d", "p", "2026-01-01"),
        )


def _take(**kw) -> Take:
    defaults = dict(
        id="take-1",
        shot_id="shot-1",
        generation_request_id="req-1",
        seed=42,
        video_path="/takes/shot-1/take_1/video.mp4",
        audio_path=None,
        last_frame_path="/takes/shot-1/take_1/last.png",
        status="succeeded",
        created_at="2026-08-16T10:05:00",
    )
    defaults.update(kw)
    return Take(**defaults)


# ---------------------------------------------------------------------------
# H3PromptRepository
# ---------------------------------------------------------------------------

class TestH3PromptRepository:
    def test_save_and_get(self, db):
        repo = H3PromptRepository(db)
        p = _prompt()
        repo.save_prompt(p)
        loaded = repo.get_prompt("h3p-1")
        assert loaded is not None
        assert loaded.id == "h3p-1"
        assert loaded.rendered_prompt_text == "full prompt text"

    def test_duplicate_id_fails(self, db):
        repo = H3PromptRepository(db)
        repo.save_prompt(_prompt())
        with pytest.raises((sqlite3.IntegrityError, PersistenceError)):
            repo.save_prompt(_prompt())

    def test_get_current_exact_match(self, db):
        repo = H3PromptRepository(db)
        repo.save_prompt(_prompt())
        current = repo.get_current_prompt("shot-1", 1, "plan-1", 1)
        assert current is not None
        assert current.id == "h3p-1"

    def test_get_current_stale_not_returned(self, db):
        repo = H3PromptRepository(db)
        # Mark fixture prompt stale, then check it's not returned as current
        repo.mark_stale("h3p-fk")
        repo.save_prompt(_prompt(id="h3p-stale", status="stale"))
        current = repo.get_current_prompt("shot-1", 1, "plan-1", 1)
        assert current is None

    def test_get_current_wrong_shot_version(self, db):
        repo = H3PromptRepository(db)
        repo.save_prompt(_prompt(source_shot_version=1))
        current = repo.get_current_prompt("shot-1", 2, "plan-1", 1)
        assert current is None

    def test_get_current_wrong_plan_id(self, db):
        repo = H3PromptRepository(db)
        repo.save_prompt(_prompt(generation_plan_id="plan-1"))
        current = repo.get_current_prompt("shot-1", 1, "plan-OTHER", 1)
        assert current is None

    def test_get_current_wrong_plan_version(self, db):
        repo = H3PromptRepository(db)
        repo.save_prompt(_prompt(source_generation_plan_version=1))
        current = repo.get_current_prompt("shot-1", 1, "plan-1", 2)
        assert current is None

    def test_multiple_prompts_coexist(self, db):
        repo = H3PromptRepository(db)
        repo.save_prompt(_prompt(id="h3p-1", status="stale", created_at="2026-08-16T09:00:00"))
        repo.save_prompt(_prompt(id="h3p-2", status="current", created_at="2026-08-16T10:00:00"))
        assert repo.get_prompt("h3p-1") is not None
        assert repo.get_prompt("h3p-2") is not None

    def test_deterministic_selection_newest(self, db):
        repo = H3PromptRepository(db)
        repo.save_prompt(_prompt(id="h3p-old", created_at="2026-08-16T09:00:00"))
        repo.save_prompt(_prompt(id="h3p-new", created_at="2026-08-16T10:00:00"))
        current = repo.get_current_prompt("shot-1", 1, "plan-1", 1)
        assert current is not None
        assert current.id == "h3p-new"

    def test_mark_stale(self, db):
        repo = H3PromptRepository(db)
        repo.save_prompt(_prompt())
        repo.mark_stale("h3p-1")
        loaded = repo.get_prompt("h3p-1")
        assert loaded is not None
        assert loaded.status == "stale"
        # Content unchanged
        assert loaded.rendered_prompt_text == "full prompt text"

    def test_mark_stale_only_target(self, db):
        repo = H3PromptRepository(db)
        repo.save_prompt(_prompt(id="h3p-1"))
        repo.save_prompt(_prompt(id="h3p-2"))
        repo.mark_stale("h3p-1")
        assert repo.get_prompt("h3p-1").status == "stale"
        assert repo.get_prompt("h3p-2").status == "current"

    def test_restart_persistence(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "test.db")
        db1 = Database(db_path)
        db1.init_schema()
        _insert_fk_parents(db1)
        H3PromptRepository(db1).save_prompt(_prompt())
        del db1

        db2 = Database(db_path)
        db2.init_schema()
        loaded = H3PromptRepository(db2).get_prompt("h3p-1")
        assert loaded is not None
        assert loaded.id == "h3p-1"

    def test_all_fields_round_trip(self, db):
        repo = H3PromptRepository(db)
        p = _prompt(
            overall_soundscape="rain on windows",
            non_diegetic_music="soft piano",
        )
        repo.save_prompt(p)
        loaded = repo.get_prompt("h3p-1")
        assert loaded.shot_id == p.shot_id
        assert loaded.generation_plan_id == p.generation_plan_id
        assert loaded.source_shot_version == p.source_shot_version
        assert loaded.source_generation_plan_version == p.source_generation_plan_version
        assert loaded.subject_definitions == p.subject_definitions
        assert loaded.summary == p.summary
        assert loaded.retention_analysis == p.retention_analysis
        assert loaded.detailed_description == p.detailed_description
        assert loaded.overall_soundscape == "rain on windows"
        assert loaded.non_diegetic_music == "soft piano"
        assert loaded.version == p.version
        assert loaded.created_at == p.created_at


# ---------------------------------------------------------------------------
# GenerationRequestRepository
# ---------------------------------------------------------------------------

class TestGenerationRequestRepository:
    def test_create_and_get(self, db):
        repo = GenerationRequestRepository(db)
        req = _request()
        repo.create_request(req)
        loaded = repo.get_request("req-1")
        assert loaded is not None
        assert loaded.id == "req-1"
        assert loaded.seed == 42

    def test_duplicate_id_fails(self, db):
        repo = GenerationRequestRepository(db)
        repo.create_request(_request())
        with pytest.raises((sqlite3.IntegrityError, PersistenceError)):
            repo.create_request(_request())

    def test_get_by_shot(self, db):
        repo = GenerationRequestRepository(db)
        repo.create_request(_request(id="req-1", shot_id="shot-1"))
        repo.create_request(_request(id="req-2", shot_id="shot-1"))
        repo.create_request(_request(id="req-3", shot_id="shot-2"))
        results = repo.get_requests_by_shot("shot-1")
        assert len(results) == 2
        assert all(r.shot_id == "shot-1" for r in results)

    def test_update_status(self, db):
        repo = GenerationRequestRepository(db)
        repo.create_request(_request())
        repo.update_status("req-1", "running")
        loaded = repo.get_request("req-1")
        assert loaded.status == "running"

    def test_update_status_with_optional_fields(self, db):
        repo = GenerationRequestRepository(db)
        repo.create_request(_request())
        repo.update_status(
            "req-1", "succeeded",
            comfyui_prompt_id="comfy-abc",
            submitted_at="2026-08-16T10:01:00",
            completed_at="2026-08-16T10:05:00",
        )
        loaded = repo.get_request("req-1")
        assert loaded.status == "succeeded"
        assert loaded.comfyui_prompt_id == "comfy-abc"
        assert loaded.submitted_at == "2026-08-16T10:01:00"
        assert loaded.completed_at == "2026-08-16T10:05:00"

    def test_update_status_with_error(self, db):
        repo = GenerationRequestRepository(db)
        repo.create_request(_request())
        repo.update_status("req-1", "failed", error="OOM on node 104")
        loaded = repo.get_request("req-1")
        assert loaded.status == "failed"
        assert loaded.error == "OOM on node 104"

    def test_immutable_fields_not_changed_by_update(self, db):
        repo = GenerationRequestRepository(db)
        repo.create_request(_request())
        repo.update_status("req-1", "succeeded")
        loaded = repo.get_request("req-1")
        # All immutable input fields preserved
        assert loaded.shot_id == "shot-1"
        assert loaded.shot_version == 1
        assert loaded.generation_plan_id == "plan-1"
        assert loaded.generation_plan_version == 1
        assert loaded.prompt_artifact_id == "h3p-fk"
        assert loaded.prompt_artifact_version == 1
        assert loaded.workflow_definition_id == "h3_r2v_v1"
        assert loaded.workflow_definition_version == "1.0.0"
        assert loaded.workflow_template_fingerprint == VALID_SHA
        assert loaded.take_number == 1
        assert loaded.seed == 42

    def test_snapshot_round_trip_parameters(self, db):
        repo = GenerationRequestRepository(db)
        snapshot = [
            {"name": "prompt", "node_id": "104", "field": "prompt", "value": "test prompt"},
            {"name": "seed", "node_id": "15", "field": "noise_seed", "value": 42},
            {"name": "duration", "node_id": "111", "field": "value", "value": 5.0},
        ]
        repo.create_request(_request(parameters_snapshot=snapshot))
        loaded = repo.get_request("req-1")
        assert loaded.parameters_snapshot == snapshot

    def test_snapshot_round_trip_references(self, db):
        repo = GenerationRequestRepository(db)
        ref_snap = [{
            "subject_index": 1,
            "character_id": "char_1",
            "character_name": "Character 1",
            "picture_index": 1,
            "local_path": "ref.png",
            "content_sha256": VALID_SHA,
            "uploaded_filename": "m3_ref.png",
        }]
        repo.create_request(_request(reference_snapshot=ref_snap))
        loaded = repo.get_request("req-1")
        assert loaded.reference_snapshot == ref_snap

    def test_snapshot_preserves_order(self, db):
        repo = GenerationRequestRepository(db)
        snapshot = [
            {"name": "prompt", "node_id": "104", "field": "prompt", "value": "text"},
            {"name": "ref_image_0", "node_id": "200", "field": "image", "value": "ref.png"},
            {"name": "duration", "node_id": "111", "field": "value", "value": 5.0},
            {"name": "seed", "node_id": "15", "field": "noise_seed", "value": 42},
            {"name": "aspect", "node_id": "115", "field": "aspect_ratio", "value": "16:9 (Widescreen)"},
            {"name": "output_prefix", "node_id": "92", "field": "filename_prefix", "value": "test"},
        ]
        repo.create_request(_request(parameters_snapshot=snapshot))
        loaded = repo.get_request("req-1")
        assert [s["name"] for s in loaded.parameters_snapshot] == [
            "prompt", "ref_image_0", "duration", "seed", "aspect", "output_prefix"
        ]

    def test_restart_persistence(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "test.db")
        db1 = Database(db_path)
        db1.init_schema()
        _insert_fk_parents(db1)
        GenerationRequestRepository(db1).create_request(_request())
        del db1

        db2 = Database(db_path)
        db2.init_schema()
        loaded = GenerationRequestRepository(db2).get_request("req-1")
        assert loaded is not None


# ---------------------------------------------------------------------------
# TakeRepository
# ---------------------------------------------------------------------------

class TestTakeRepository:
    def _ensure_requests(self, db, *req_ids):
        req_repo = GenerationRequestRepository(db)
        for rid in req_ids:
            try:
                req_repo.create_request(_request(id=rid))
            except (sqlite3.IntegrityError, PersistenceError):
                pass  # already exists

    def test_save_and_get(self, db):
        self._ensure_requests(db, "req-1")
        repo = TakeRepository(db)
        repo.save_take(_take())
        loaded = repo.get_take("take-1")
        assert loaded is not None
        assert loaded.id == "take-1"
        assert loaded.video_path == "/takes/shot-1/take_1/video.mp4"

    def test_get_by_shot(self, db):
        self._ensure_requests(db, "req-1", "req-2", "req-3")
        repo = TakeRepository(db)
        repo.save_take(_take(id="take-1", shot_id="shot-1", generation_request_id="req-1"))
        repo.save_take(_take(id="take-2", shot_id="shot-1", generation_request_id="req-2"))
        repo.save_take(_take(id="take-3", shot_id="shot-2", generation_request_id="req-3"))
        results = repo.get_takes_by_shot("shot-1")
        assert len(results) == 2

    def test_duplicate_generation_request_id_fails(self, db):
        self._ensure_requests(db, "req-1")
        repo = TakeRepository(db)
        repo.save_take(_take(id="take-1", generation_request_id="req-1"))
        with pytest.raises((sqlite3.IntegrityError, PersistenceError)):
            repo.save_take(_take(id="take-2", generation_request_id="req-1"))

    def test_multiple_takes_different_requests_same_shot(self, db):
        self._ensure_requests(db, "req-1", "req-2")
        repo = TakeRepository(db)
        repo.save_take(_take(id="take-1", shot_id="shot-1", generation_request_id="req-1"))
        repo.save_take(_take(id="take-2", shot_id="shot-1", generation_request_id="req-2"))
        results = repo.get_takes_by_shot("shot-1")
        assert len(results) == 2

    def test_all_fields_round_trip(self, db):
        self._ensure_requests(db, "req-1")
        repo = TakeRepository(db)
        t = _take(audio_path="/audio.aac", last_frame_path="/last.png")
        repo.save_take(t)
        loaded = repo.get_take("take-1")
        assert loaded.shot_id == t.shot_id
        assert loaded.generation_request_id == t.generation_request_id
        assert loaded.seed == t.seed
        assert loaded.video_path == t.video_path
        assert loaded.audio_path == "/audio.aac"
        assert loaded.last_frame_path == "/last.png"
        assert loaded.status == t.status
        assert loaded.created_at == t.created_at

    def test_restart_persistence(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "test.db")
        db1 = Database(db_path)
        db1.init_schema()
        _insert_fk_parents(db1)
        GenerationRequestRepository(db1).create_request(_request())
        TakeRepository(db1).save_take(_take())
        del db1

        db2 = Database(db_path)
        db2.init_schema()
        loaded = TakeRepository(db2).get_take("take-1")
        assert loaded is not None



# ---------------------------------------------------------------------------
# Transaction support
# ---------------------------------------------------------------------------

class TestTransactionSupport:
    def test_caller_owned_connection(self, db):
        """Repository calls work inside a caller-owned transaction."""
        req_repo = GenerationRequestRepository(db)
        take_repo = TakeRepository(db)

        with db.connection() as conn:
            req_repo.create_request(_request(), conn=conn)
            take_repo.save_take(_take(), conn=conn)

        # Both committed
        assert req_repo.get_request("req-1") is not None
        assert take_repo.get_take("take-1") is not None

    def test_rollback_prevents_partial_state(self, db):
        """BLOCKING: rollback prevents Take without succeeded request."""
        req_repo = GenerationRequestRepository(db)
        take_repo = TakeRepository(db)

        # Create request first (committed)
        req_repo.create_request(_request(status="running"))

        # Attempt finalization in a transaction that fails
        try:
            with db.connection() as conn:
                take_repo.save_take(_take(), conn=conn)
                req_repo.update_status("req-1", "succeeded", conn=conn)
                raise RuntimeError("Simulated failure before commit")
        except RuntimeError:
            pass

        # Take must NOT exist, request must NOT be succeeded
        assert take_repo.get_take("take-1") is None
        loaded = req_repo.get_request("req-1")
        assert loaded.status == "running"

    def test_successful_atomic_finalization(self, db):
        """BLOCKING: Take + request succeeded commit atomically."""
        req_repo = GenerationRequestRepository(db)
        take_repo = TakeRepository(db)

        req_repo.create_request(_request(status="running"))

        with db.connection() as conn:
            take_repo.save_take(_take(), conn=conn)
            req_repo.update_status("req-1", "succeeded",
                                   completed_at="2026-08-16T10:05:00", conn=conn)

        assert take_repo.get_take("take-1") is not None
        loaded = req_repo.get_request("req-1")
        assert loaded.status == "succeeded"

    def test_duplicate_take_cannot_leave_succeeded(self, db):
        """BLOCKING: duplicate Take insertion must not leave request succeeded."""
        req_repo = GenerationRequestRepository(db)
        take_repo = TakeRepository(db)

        req_repo.create_request(_request(status="running"))
        # First take committed
        take_repo.save_take(_take(id="take-first"))

        # Try to insert duplicate gen_request_id take + succeed request
        try:
            with db.connection() as conn:
                take_repo.save_take(_take(id="take-dup", generation_request_id="req-1"), conn=conn)
                req_repo.update_status("req-1", "succeeded", conn=conn)
        except (sqlite3.IntegrityError, PersistenceError):
            pass

        loaded = req_repo.get_request("req-1")
        assert loaded.status == "running"  # NOT succeeded

    def test_h3_prompt_shared_connection(self, db):
        repo = H3PromptRepository(db)
        with db.connection() as conn:
            repo.save_prompt(_prompt(), conn=conn)
        assert repo.get_prompt("h3p-1") is not None
