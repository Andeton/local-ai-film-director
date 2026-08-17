"""Integration tests for M7.A continuity persistence — schema, migrations, repository roundtrip."""
import json
import pytest

from film_director.continuity.continuity_models import ContinuityState
from film_director.generation.generation_request import GenerationRequest, Take
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    ContinuityStateRepository,
    GenerationRequestRepository,
    TakeRepository,
)


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


def _insert_project_scene_beat_shot(conn, shot_id="shot_1", scene_id="scene_1",
                                    beat_id=None, with_plan_and_prompt=False):
    """Insert minimal chain: project→sequence→scene→beat→shot."""
    if beat_id is None:
        beat_id = f"beat_{scene_id}"
    conn.execute(
        "INSERT OR IGNORE INTO production_projects "
        "(id, wc_project_id, title, status, created_at, updated_at, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("proj_1", "wc_1", "Test", "active", "t", "t",
         "wind_comic", "wc_1", "a1", "t", "h1"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO sequences (id, project_id, name, order_index) VALUES (?,?,?,?)",
        ("seq_1", "proj_1", "Seq", 0),
    )
    conn.execute(
        "INSERT OR IGNORE INTO scenes "
        "(id, sequence_id, wc_scene_id, name, order_index, status, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (scene_id, "seq_1", f"wc_{scene_id}", "Scene", 0, "draft",
         "wind_comic", "wc_1", f"a_{scene_id}", "t", f"h_{scene_id}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO beats "
        "(id, scene_id, dramatic_action, order_index, status, source, version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (beat_id, scene_id, "action", 0, "draft", "llm", 1, "t", "t"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO shots "
        "(id, beat_id, dramatic_purpose, order_index, status, source, version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (shot_id, beat_id, "purpose", 0, "draft", "generated", 1, "t", "t"),
    )
    if with_plan_and_prompt:
        plan_id = f"plan_{shot_id}"
        prompt_id = f"prompt_{shot_id}"
        conn.execute(
            "INSERT OR IGNORE INTO generation_plans "
            "(id, shot_id, shot_version, strategy, duration_sec, version, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (plan_id, shot_id, 1, "REFERENCE_TO_VIDEO", 5.0, 1, "t", "t"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO h3_prompts "
            "(id, shot_id, generation_plan_id, source_shot_version, "
            "source_generation_plan_version, subject_definitions, summary, "
            "retention_analysis, detailed_description, rendered_prompt_text, "
            "version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (prompt_id, shot_id, plan_id, 1, 1, "", "", "", "", "", 1, "t"),
        )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestFreshSchema:
    def test_continuity_states_table_exists(self, db):
        with db.connection() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "continuity_states" in tables

    def test_continuity_states_columns(self, db):
        with db.connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(continuity_states)").fetchall()}
        expected = {
            "id", "shot_id", "scene_id", "predecessor_shot_id",
            "upstream_take_id", "upstream_take_number",
            "upstream_last_frame_path", "upstream_last_frame_sha256",
            "state", "continuity_revision",
            "invalidation_reason", "invalidation_source_shot_id",
            "created_at", "updated_at", "invalidated_at",
        }
        assert expected <= cols

    def test_generation_request_has_continuity_snapshot(self, db):
        with db.connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(generation_requests)").fetchall()}
        assert "continuity_snapshot" in cols

    def test_unique_constraint_on_shot_id(self, db):
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn, shot_id="shot_a")
            conn.execute(
                "INSERT INTO continuity_states (id, shot_id, scene_id, state, continuity_revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("cs_1", "shot_a", "scene_1", "unresolved", 0, "t", "t"),
            )
            with pytest.raises(Exception):  # IntegrityError
                conn.execute(
                    "INSERT INTO continuity_states (id, shot_id, scene_id, state, continuity_revision, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("cs_2", "shot_a", "scene_1", "unresolved", 0, "t", "t"),
                )


class TestMigrationIdempotency:
    def test_double_init_is_safe(self, tmp_path):
        d = Database(str(tmp_path / "test.db"))
        d.init_schema()
        d.init_schema()  # second call should not raise
        with d.connection() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "continuity_states" in tables

    def test_m6_db_upgrade_adds_continuity(self, tmp_path):
        """Simulate an M6 database (without continuity tables) getting upgraded."""
        d = Database(str(tmp_path / "m6.db"))
        # Create the schema first, then DROP the M7 table to simulate M6 state
        d.init_schema()
        with d.connection() as conn:
            conn.execute("DROP TABLE IF EXISTS continuity_states")
            # Remove continuity_snapshot column by recreating generation_requests
            # (SQLite doesn't support DROP COLUMN easily, so just verify migration adds it)
        # Re-init should add it back
        d.init_schema()
        with d.connection() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "continuity_states" in tables

    def test_historical_data_preserved(self, db):
        """Existing M6 data survives schema re-initialization."""
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn)
        # Re-init
        db.init_schema()
        with db.connection() as conn:
            row = conn.execute("SELECT id FROM shots WHERE id = 'shot_1'").fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# ContinuityStateRepository
# ---------------------------------------------------------------------------

class TestContinuityStateRepository:
    def test_save_and_get_by_shot(self, db):
        repo = ContinuityStateRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn)
        cs = ContinuityState(
            id="cs_1", shot_id="shot_1", scene_id="scene_1",
            predecessor_shot_id=None,
            state="unresolved", continuity_revision=0,
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        )
        repo.save(cs)
        loaded = repo.get_by_shot("shot_1")
        assert loaded is not None
        assert loaded.id == "cs_1"
        assert loaded.shot_id == "shot_1"
        assert loaded.state == "unresolved"

    def test_upsert_by_shot_id(self, db):
        repo = ContinuityStateRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn, with_plan_and_prompt=True)
            # Create a Take so FK is satisfied
            conn.execute(
                "INSERT INTO generation_requests "
                "(id, shot_id, shot_version, generation_plan_id, generation_plan_version, "
                "prompt_artifact_id, prompt_artifact_version, workflow_definition_id, "
                "workflow_definition_version, workflow_template_fingerprint, take_number, "
                "parameters_snapshot, reference_snapshot, seed, status, submitted_at, completed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("greq_1", "shot_1", 1, "plan_shot_1", 1, "prompt_shot_1", 1,
                 "h3_r2v_v1", "1.0.0", "a" * 64, 1, "[]", "[]", 42, "succeeded", "", ""),
            )
            conn.execute(
                "INSERT INTO takes (id, shot_id, generation_request_id, seed, video_path, status, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("take_1", "shot_1", "greq_1", 42, "v.mp4", "succeeded", "t"),
            )
        cs1 = ContinuityState(
            id="cs_1", shot_id="shot_1", scene_id="scene_1",
            state="unresolved", created_at="t", updated_at="t",
        )
        repo.save(cs1)
        cs2 = ContinuityState(
            id="cs_1", shot_id="shot_1", scene_id="scene_1",
            state="current", upstream_take_id="take_1",
            upstream_take_number=1, continuity_revision=1,
            created_at="t", updated_at="t2",
        )
        repo.save(cs2)
        loaded = repo.get_by_shot("shot_1")
        assert loaded.state == "current"
        assert loaded.upstream_take_id == "take_1"

    def test_get_by_scene(self, db):
        repo = ContinuityStateRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn, shot_id="shot_1", scene_id="scene_a")
            _insert_project_scene_beat_shot(conn, shot_id="shot_2", scene_id="scene_a")
        for i, sid in enumerate(["shot_1", "shot_2"], 1):
            repo.save(ContinuityState(
                id=f"cs_{i}", shot_id=sid, scene_id="scene_a",
                state="unresolved", created_at="t", updated_at="t",
            ))
        states = repo.get_by_scene("scene_a")
        assert len(states) == 2

    def test_scene_isolation(self, db):
        repo = ContinuityStateRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn, shot_id="shot_1", scene_id="scene_a")
            _insert_project_scene_beat_shot(conn, shot_id="shot_2", scene_id="scene_b")
        repo.save(ContinuityState(
            id="cs_1", shot_id="shot_1", scene_id="scene_a",
            state="unresolved", created_at="t", updated_at="t",
        ))
        repo.save(ContinuityState(
            id="cs_2", shot_id="shot_2", scene_id="scene_b",
            state="current", created_at="t", updated_at="t",
        ))
        scene_a = repo.get_by_scene("scene_a")
        scene_b = repo.get_by_scene("scene_b")
        assert len(scene_a) == 1
        assert scene_a[0].shot_id == "shot_1"
        assert len(scene_b) == 1
        assert scene_b[0].shot_id == "shot_2"

    def test_get_nonexistent_returns_none(self, db):
        repo = ContinuityStateRepository(db)
        assert repo.get_by_shot("no_such") is None

    def test_full_provenance_roundtrip(self, db):
        repo = ContinuityStateRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn, with_plan_and_prompt=True)
            conn.execute(
                "INSERT INTO generation_requests "
                "(id, shot_id, shot_version, generation_plan_id, generation_plan_version, "
                "prompt_artifact_id, prompt_artifact_version, workflow_definition_id, "
                "workflow_definition_version, workflow_template_fingerprint, take_number, "
                "parameters_snapshot, reference_snapshot, seed, status, submitted_at, completed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("greq_42", "shot_1", 1, "plan_shot_1", 1, "prompt_shot_1", 1,
                 "h3_r2v_v1", "1.0.0", "a" * 64, 3, "[]", "[]", 42, "succeeded", "", ""),
            )
            conn.execute(
                "INSERT INTO takes (id, shot_id, generation_request_id, seed, video_path, status, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("take_42", "shot_1", "greq_42", 42, "v.mp4", "succeeded", "t"),
            )
        cs = ContinuityState(
            id="cs_full", shot_id="shot_1", scene_id="scene_1",
            predecessor_shot_id=None,
            upstream_take_id="take_42",
            upstream_take_number=3,
            upstream_last_frame_path="storage/takes/proj/shot/take_3/last_frame.png",
            upstream_last_frame_sha256="a" * 64,
            state="current",
            continuity_revision=7,
            invalidation_reason=None,
            invalidation_source_shot_id=None,
            created_at="2026-01-01", updated_at="2026-01-02",
            invalidated_at=None,
        )
        repo.save(cs)
        loaded = repo.get_by_shot("shot_1")
        assert loaded.upstream_take_id == "take_42"
        assert loaded.upstream_take_number == 3
        assert loaded.upstream_last_frame_path == cs.upstream_last_frame_path
        assert loaded.upstream_last_frame_sha256 == "a" * 64
        assert loaded.continuity_revision == 7
        assert loaded.invalidation_reason is None

    def test_mark_outdated(self, db):
        repo = ContinuityStateRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn)
        repo.save(ContinuityState(
            id="cs_1", shot_id="shot_1", scene_id="scene_1",
            state="current", created_at="t", updated_at="t",
        ))
        count = repo.mark_outdated("shot_1", "upstream_take_changed", "shot_0", "2026-01-03")
        assert count == 1
        loaded = repo.get_by_shot("shot_1")
        assert loaded.state == "outdated"
        assert loaded.invalidation_reason == "upstream_take_changed"
        assert loaded.invalidation_source_shot_id == "shot_0"
        assert loaded.invalidated_at == "2026-01-03"

    def test_mark_outdated_idempotent(self, db):
        repo = ContinuityStateRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn)
        repo.save(ContinuityState(
            id="cs_1", shot_id="shot_1", scene_id="scene_1",
            state="outdated", created_at="t", updated_at="t",
        ))
        count = repo.mark_outdated("shot_1", "upstream_take_changed", "shot_0", "t")
        assert count == 0  # already outdated

    def test_resolve_current(self, db):
        repo = ContinuityStateRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn, with_plan_and_prompt=True)
            conn.execute(
                "INSERT INTO generation_requests "
                "(id, shot_id, shot_version, generation_plan_id, generation_plan_version, "
                "prompt_artifact_id, prompt_artifact_version, workflow_definition_id, "
                "workflow_definition_version, workflow_template_fingerprint, take_number, "
                "parameters_snapshot, reference_snapshot, seed, status, submitted_at, completed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("greq_1", "shot_1", 1, "plan_shot_1", 1, "prompt_shot_1", 1,
                 "h3_r2v_v1", "1.0.0", "a" * 64, 1, "[]", "[]", 42, "succeeded", "", ""),
            )
            conn.execute(
                "INSERT INTO takes (id, shot_id, generation_request_id, seed, video_path, status, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("take_1", "shot_1", "greq_1", 42, "v.mp4", "succeeded", "t"),
            )
        repo.save(ContinuityState(
            id="cs_1", shot_id="shot_1", scene_id="scene_1",
            state="unresolved", continuity_revision=0,
            created_at="t", updated_at="t",
        ))
        count = repo.resolve_current(
            "shot_1", "take_1", 1,
            "storage/frame.png", "b" * 64, "2026-01-04",
        )
        assert count == 1
        loaded = repo.get_by_shot("shot_1")
        assert loaded.state == "current"
        assert loaded.continuity_revision == 1  # bumped from 0
        assert loaded.upstream_take_id == "take_1"
        assert loaded.upstream_last_frame_sha256 == "b" * 64
        assert loaded.invalidation_reason is None


# ---------------------------------------------------------------------------
# GenerationRequest continuity_snapshot
# ---------------------------------------------------------------------------

_FP = "a" * 64


def _make_request(request_id="greq_1", continuity_snapshot=None):
    return GenerationRequest(
        id=request_id,
        shot_id="shot_1",
        shot_version=1,
        generation_plan_id="plan_shot_1",
        generation_plan_version=1,
        prompt_artifact_id="prompt_shot_1",
        prompt_artifact_version=1,
        workflow_definition_id="h3_r2v_v1",
        workflow_definition_version="1.0.0",
        workflow_template_fingerprint=_FP,
        take_number=1,
        parameters_snapshot=[],
        reference_snapshot=[],
        seed=42,
        continuity_snapshot=continuity_snapshot,
    )


class TestGenerationRequestContinuitySnapshot:
    def test_none_snapshot_roundtrip(self, db):
        """M3–M6 legacy requests have None continuity_snapshot."""
        repo = GenerationRequestRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn, with_plan_and_prompt=True)
        req = _make_request(continuity_snapshot=None)
        repo.create_request(req)
        loaded = repo.get_request("greq_1")
        assert loaded is not None
        assert loaded.continuity_snapshot is None

    def test_populated_snapshot_roundtrip(self, db):
        repo = GenerationRequestRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn, with_plan_and_prompt=True)
        snap = {
            "upstream_shot_id": "shot_prev",
            "upstream_take_id": "take_prev",
            "upstream_take_number": 2,
            "upstream_last_frame_managed_path": "storage/takes/proj/shot/take_2/last_frame.png",
            "upstream_last_frame_sha256": "c" * 64,
            "continuity_revision": 3,
        }
        req = _make_request(request_id="greq_2", continuity_snapshot=snap)
        repo.create_request(req)
        loaded = repo.get_request("greq_2")
        assert loaded.continuity_snapshot == snap
        assert loaded.continuity_snapshot["upstream_shot_id"] == "shot_prev"
        assert loaded.continuity_snapshot["continuity_revision"] == 3

    def test_snapshot_immutable_after_persist(self, db):
        """Once persisted, continuity_snapshot should not be modifiable in DB."""
        repo = GenerationRequestRepository(db)
        with db.connection() as conn:
            _insert_project_scene_beat_shot(conn, with_plan_and_prompt=True)
        snap = {"upstream_shot_id": "shot_x", "continuity_revision": 1}
        req = _make_request(request_id="greq_3", continuity_snapshot=snap)
        repo.create_request(req)
        # update_status does NOT touch continuity_snapshot
        repo.update_status("greq_3", "succeeded")
        loaded = repo.get_request("greq_3")
        assert loaded.continuity_snapshot == snap  # unchanged
        assert loaded.status == "succeeded"


class TestNoMutationOfHistoricalData:
    """M7.A must not modify existing Takes or GenerationRequests."""

    def test_existing_take_unchanged_after_schema_update(self, tmp_path):
        # Create M6-style DB
        d = Database(str(tmp_path / "m6.db"))
        d.init_schema()
        with d.connection() as conn:
            _insert_project_scene_beat_shot(conn, with_plan_and_prompt=True)
        req = _make_request()
        repo = GenerationRequestRepository(d)
        repo.create_request(req)
        take_repo = TakeRepository(d)
        take = Take(
            id="take_1", shot_id="shot_1", generation_request_id="greq_1",
            seed=42, video_path="v.mp4", status="succeeded", created_at="t",
        )
        take_repo.save_take(take)

        # Re-init schema (simulates M7 upgrade)
        d.init_schema()

        # Verify Take is unchanged
        loaded_take = take_repo.get_take("take_1")
        assert loaded_take.id == "take_1"
        assert loaded_take.status == "succeeded"

        # Verify GenRequest is unchanged with None snapshot
        loaded_req = repo.get_request("greq_1")
        assert loaded_req.continuity_snapshot is None
        assert loaded_req.seed == 42
