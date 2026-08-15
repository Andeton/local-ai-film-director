"""Tests for GenerationRequest and Take Pydantic models + DB schema."""
import pytest
import sqlite3
import tempfile
import os
from pydantic import ValidationError

from film_director.generation.generation_request import GenerationRequest, Take
from film_director.persistence.database import Database

VALID_SHA256 = "b" * 64


def _valid_request(**overrides) -> GenerationRequest:
    defaults = dict(
        id="req-1",
        shot_id="shot-1",
        shot_version=1,
        generation_plan_id="plan-1",
        generation_plan_version=1,
        prompt_artifact_id="prompt-1",
        prompt_artifact_version=1,
        workflow_definition_id="wf-h3-r2v",
        workflow_definition_version="v1.0",
        workflow_template_fingerprint=VALID_SHA256,
        take_number=1,
        seed=42,
    )
    defaults.update(overrides)
    return GenerationRequest(**defaults)


def _valid_take(**overrides) -> Take:
    defaults = dict(
        id="take-1",
        shot_id="shot-1",
        generation_request_id="req-1",
        seed=42,
        video_path="/outputs/take-1.mp4",
    )
    defaults.update(overrides)
    return Take(**defaults)


# --- GenerationRequest ---

def test_generation_request_valid_construction():
    r = _valid_request()
    assert r.id == "req-1"
    assert r.status == "pending"
    assert r.parameters_snapshot == []
    assert r.reference_snapshot == []
    assert r.comfyui_prompt_id is None
    assert r.error is None


def test_generation_request_all_valid_statuses():
    for status in ("pending", "queued", "running", "succeeded", "failed", "cancelled"):
        r = _valid_request(status=status)
        assert r.status == status


def test_generation_request_invalid_status_rejected():
    with pytest.raises(ValidationError):
        _valid_request(status="complete")


def test_generation_request_take_number_below_one_rejected():
    with pytest.raises(ValidationError):
        _valid_request(take_number=0)


def test_generation_request_invalid_fingerprint_rejected():
    with pytest.raises(ValidationError):
        _valid_request(workflow_template_fingerprint="tooshort")


def test_generation_request_parameters_snapshot_roundtrip():
    injections = [
        {"name": "prompt", "node_id": "6", "field": "text", "value": "Alice sits"},
        {"name": "seed", "node_id": "3", "field": "seed", "value": 42},
    ]
    r = _valid_request(parameters_snapshot=injections)
    assert len(r.parameters_snapshot) == 2
    assert r.parameters_snapshot[0]["name"] == "prompt"
    assert r.parameters_snapshot[1]["value"] == 42


def test_generation_request_reference_snapshot_with_sha256():
    refs = [
        {
            "subject_index": 1,
            "character_id": "char-1",
            "character_name": "Alice",
            "content_sha256": VALID_SHA256,
            "uploaded_filename": "alice.jpg",
        }
    ]
    r = _valid_request(reference_snapshot=refs)
    assert r.reference_snapshot[0]["content_sha256"] == VALID_SHA256


def test_generation_request_empty_id_rejected():
    with pytest.raises(ValidationError):
        _valid_request(id="")


def test_generation_request_version_fields_required_positive():
    with pytest.raises(ValidationError):
        _valid_request(shot_version=0)
    with pytest.raises(ValidationError):
        _valid_request(generation_plan_version=0)
    with pytest.raises(ValidationError):
        _valid_request(prompt_artifact_version=0)


# --- Take ---

def test_take_valid_construction():
    t = _valid_take()
    assert t.id == "take-1"
    assert t.status == "pending"
    assert t.audio_path is None
    assert t.last_frame_path is None


def test_take_empty_video_path_rejected():
    with pytest.raises(ValidationError):
        _valid_take(video_path="")


def test_take_empty_video_path_whitespace_rejected():
    with pytest.raises(ValidationError):
        _valid_take(video_path="   ")


# --- DB Schema ---

def _make_db():
    tmp = tempfile.mktemp(suffix=".db")
    db = Database(tmp)
    db.init_schema()
    return db, tmp


def _seed_prerequisites(conn):
    """Insert minimal rows required by FK constraints."""
    conn.execute(
        "INSERT INTO production_projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("proj-1", "wc-1", "Test Project", "draft", "16:9",
         "2024-01-01", "2024-01-01",
         "wind_comic", "wc-1", "asset-1", 1, "2024-01-01", "abc"),
    )
    conn.execute(
        "INSERT INTO sequences VALUES (?,?,?,?)",
        ("seq-1", "proj-1", "Seq 1", 1),
    )
    conn.execute(
        "INSERT INTO scenes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("scene-1", "seq-1", "wc-scene-1", "Scene 1", "", "", 1, "draft",
         "wind_comic", "wc-1", "asset-1", 1, "2024-01-01", "abc"),
    )
    conn.execute(
        "INSERT INTO beats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("beat-1", "scene-1", "Action", "", "", "[]", 1, "draft", "llm", 1,
         "2024-01-01", "2024-01-01"),
    )
    conn.execute(
        "INSERT INTO shots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("shot-1", "beat-1", None, None, "", "[]", "", "{}", "{}", "{}", "{}", 5.0,
         "{}", None, 1, "draft", "generated", 1, "2024-01-01", "2024-01-01"),
    )
    conn.execute(
        "INSERT INTO generation_plans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("plan-1", "shot-1", 1, "TEXT_TO_VIDEO", "{}", 5.0, "{}", "random", None,
         "none", "", "draft", 1, "2024-01-01", "2024-01-01"),
    )


def test_db_schema_creates_m3_tables():
    db, tmp = _make_db()
    try:
        with db.connection() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "h3_prompts" in tables
        assert "generation_requests" in tables
        assert "takes" in tables
    finally:
        os.unlink(tmp)


def test_db_take_unique_generation_request_id_constraint():
    db, tmp = _make_db()
    try:
        with db.connection() as conn:
            _seed_prerequisites(conn)
            conn.execute(
                "INSERT INTO h3_prompts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("prompt-1", "shot-1", "plan-1", 1, 1,
                 "Subject 1", "Summary", "Retention", "Desc", "", "",
                 "rendered text", "current", 1, "2024-01-01"),
            )
            conn.execute(
                "INSERT INTO generation_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("req-1", "shot-1", 1, "plan-1", 1, "prompt-1", 1,
                 "wf-h3", "v1", VALID_SHA256, 1, "[]", "[]", 42,
                 None, "pending", "", "", None),
            )
            conn.execute(
                "INSERT INTO takes VALUES (?,?,?,?,?,?,?,?,?)",
                ("take-1", "shot-1", "req-1", 42, "/out/take-1.mp4", None, None,
                 "pending", "2024-01-01"),
            )

        with pytest.raises(Exception):
            with db.connection() as conn:
                conn.execute(
                    "INSERT INTO takes VALUES (?,?,?,?,?,?,?,?,?)",
                    ("take-2", "shot-1", "req-1", 42, "/out/take-2.mp4", None, None,
                     "pending", "2024-01-01"),
                )
    finally:
        os.unlink(tmp)


def test_db_multiple_prompts_and_requests_for_same_shot():
    db, tmp = _make_db()
    try:
        with db.connection() as conn:
            _seed_prerequisites(conn)
            for i in range(1, 4):
                conn.execute(
                    "INSERT INTO h3_prompts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"prompt-{i}", "shot-1", "plan-1", i, 1,
                     f"Subject {i}", f"Summary {i}", "Retention", "Desc", "", "",
                     f"rendered text {i}", "current" if i == 3 else "stale", i, "2024-01-01"),
                )
                conn.execute(
                    "INSERT INTO generation_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"req-{i}", "shot-1", i, "plan-1", 1, f"prompt-{i}", i,
                     "wf-h3", "v1", VALID_SHA256, i, "[]", "[]", 42 + i,
                     None, "pending", "", "", None),
                )

        with db.connection() as conn:
            prompt_count = conn.execute(
                "SELECT COUNT(*) FROM h3_prompts WHERE shot_id='shot-1'"
            ).fetchone()[0]
            req_count = conn.execute(
                "SELECT COUNT(*) FROM generation_requests WHERE shot_id='shot-1'"
            ).fetchone()[0]
        assert prompt_count == 3
        assert req_count == 3
    finally:
        os.unlink(tmp)
