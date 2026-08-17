"""Integration tests for M6.E — Take Management and Queue API.

Uses real SQLite. No live ComfyUI.
"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from film_director.api.routes import create_router
from film_director.errors import FilmDirectorError
from film_director.main import _ERROR_STATUS
from film_director.models.canonical import (
    Beat, CameraIntent, CharacterReference, GenerationPlan,
    ProductionProject, ReferenceRequirements, Scene, Sequence,
    ShotSpecificationV1, ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository, CharacterRepository, GenerationPlanRepository,
    GenerationRequestRepository, ProjectRepository, QueueBatchRepository,
    QueueJobRepository, SceneRepository, SequenceRepository,
    ShotRepository, TakeRepository,
)
from film_director.generation.queue_service import QueueService
from film_director.services.take_service import TakeService
from film_director.generation.generation_request import Take
from unittest.mock import MagicMock


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


@pytest.fixture
def env(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()

    storage = os.path.join(str(tmp_path), "storage")
    os.makedirs(storage, exist_ok=True)
    video = os.path.join(storage, "v.mp4")
    with open(video, "wb") as f:
        f.write(b"fake video")

    project_repo = ProjectRepository(db)
    seq_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)
    take_repo = TakeRepository(db)
    queue_repo = QueueJobRepository(db)
    batch_repo = QueueBatchRepository(db)

    with db.connection() as conn:
        project_repo.save_project(ProductionProject(
            id="proj-1", wc_project_id="wc-p1", title="Test",
            status="active", created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        ), conn=conn)
        seq_repo.save_sequence(Sequence(
            id="seq-1", project_id="proj-1", name="Main", order_index=0,
        ), conn=conn)
        scene_repo.save_scene(Scene(
            id="scene-1", sequence_id="seq-1", wc_scene_id="wc-s1",
            name="S1", location="", description="", order_index=0,
            provenance=_prov(),
        ), conn=conn)
        beat_repo.save_beat(Beat(
            id="beat-1", scene_id="scene-1", dramatic_action="enters",
            character_intention="investigate", change="finds clue",
            order_index=0, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        shot_repo.save_shot(ShotSpecificationV1(
            id="shot-1", beat_id="beat-1", dramatic_purpose="tension",
            subjects=[ShotSubject(character_id="c1", name="A", ref_images=[])],
            action="walks", camera=CameraIntent(shot_size="medium"),
            duration_sec=5.0, order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        plan_repo.save_plan(GenerationPlan(
            id="plan-1", shot_id="shot-1", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, seed_policy="fixed", seed=42,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        # Insert h3_prompt and generation_request for take FK chain
        fp = "a" * 64
        conn.execute(
            "INSERT INTO h3_prompts (id,shot_id,generation_plan_id,source_shot_version,"
            "source_generation_plan_version,subject_definitions,summary,retention_analysis,"
            "detailed_description,rendered_prompt_text,version,created_at) VALUES "
            "('hp1','shot-1','plan-1',1,1,'','','','','',1,'t')"
        )
        conn.execute(
            f"INSERT INTO generation_requests (id,shot_id,shot_version,generation_plan_id,"
            f"generation_plan_version,prompt_artifact_id,prompt_artifact_version,"
            f"workflow_definition_id,workflow_definition_version,workflow_template_fingerprint,"
            f"take_number,parameters_snapshot,reference_snapshot,seed) VALUES "
            f"('r1','shot-1',1,'plan-1',1,'hp1',1,'w1','1.0','{fp}',1,'[]','[]',42)"
        )
        take_repo.save_take(Take(
            id="take-1", shot_id="shot-1", generation_request_id="r1",
            seed=42, video_path=video, status="succeeded", created_at="2026-01-01",
        ), conn=conn)

    queue_svc = QueueService(
        db=db, queue_repo=queue_repo, batch_repo=batch_repo,
        shot_repo=shot_repo, plan_repo=plan_repo, scene_repo=scene_repo,
        seq_repo=seq_repo, beat_repo=beat_repo,
    )
    take_svc = TakeService(take_repo, db, storage_root=storage)

    app = FastAPI()

    @app.exception_handler(FilmDirectorError)
    def handle_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=_ERROR_STATUS.get(type(exc), 500),
            content={"error": exc.message, "detail": exc.detail},
        )

    router = create_router(
        adapter=MagicMock(), import_service=MagicMock(),
        project_repo=project_repo, seq_repo=seq_repo,
        scene_repo=scene_repo, char_repo=MagicMock(),
        llm_provider=MagicMock(),
        shot_repo=shot_repo,
        queue_service=queue_svc,
        take_service=take_svc,
        take_repo=take_repo,
    )
    app.include_router(router)
    client = TestClient(app)

    return {"client": client, "db": db, "queue_repo": queue_repo, "take_repo": take_repo}


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

class TestRouteRegistration:
    def test_all_m6_routes(self, env):
        routes = [r.path for r in env["client"].app.routes if hasattr(r, "path")]
        expected = [
            "/shots/{shot_id}/takes/enqueue",
            "/scenes/{scene_id}/takes/enqueue",
            "/queue/jobs",
            "/queue/jobs/{job_id}",
            "/queue/jobs/{job_id}/cancel",
            "/shots/{shot_id}/takes",
            "/shots/{shot_id}/approved-take",
            "/takes/{take_id}/approve",
            "/takes/{take_id}/reject",
            "/takes/{take_id}/favorite",
            "/takes/{take_id}/unfavorite",
        ]
        for route in expected:
            assert route in routes, f"Missing route: {route}"


# ---------------------------------------------------------------------------
# Shot enqueue
# ---------------------------------------------------------------------------

class TestShotEnqueueAPI:
    def test_enqueue_default(self, env):
        resp = env["client"].post(
            "/shots/shot-1/takes/enqueue",
            json={},
            headers={"Idempotency-Key": "key-1"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["takes_count"] == 3
        assert len(data["jobs"]) == 3

    def test_idempotent_replay(self, env):
        resp1 = env["client"].post(
            "/shots/shot-1/takes/enqueue",
            json={"takes_count": 2, "base_seed": 42},
            headers={"Idempotency-Key": "key-idem"},
        )
        resp2 = env["client"].post(
            "/shots/shot-1/takes/enqueue",
            json={"takes_count": 2, "base_seed": 42},
            headers={"Idempotency-Key": "key-idem"},
        )
        assert resp1.json()["jobs"][0]["id"] == resp2.json()["jobs"][0]["id"]

    def test_conflict_different_payload(self, env):
        env["client"].post(
            "/shots/shot-1/takes/enqueue",
            json={"takes_count": 2, "base_seed": 42},
            headers={"Idempotency-Key": "key-conf"},
        )
        resp = env["client"].post(
            "/shots/shot-1/takes/enqueue",
            json={"takes_count": 5, "base_seed": 42},
            headers={"Idempotency-Key": "key-conf"},
        )
        assert resp.status_code == 409

    def test_missing_idempotency_key(self, env):
        resp = env["client"].post(
            "/shots/shot-1/takes/enqueue", json={},
        )
        assert resp.status_code == 422

    def test_missing_shot(self, env):
        resp = env["client"].post(
            "/shots/nonexistent/takes/enqueue",
            json={},
            headers={"Idempotency-Key": "key-miss"},
        )
        assert resp.status_code == 404

    def test_invalid_takes_count(self, env):
        resp = env["client"].post(
            "/shots/shot-1/takes/enqueue",
            json={"takes_count": 0},
            headers={"Idempotency-Key": "key-inv"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scene enqueue
# ---------------------------------------------------------------------------

class TestSceneEnqueueAPI:
    def test_enqueue_scene(self, env):
        resp = env["client"].post(
            "/scenes/scene-1/takes/enqueue",
            json={"takes_per_shot": 2, "base_seed": 42},
            headers={"Idempotency-Key": "skey-1"},
        )
        assert resp.status_code == 202
        assert resp.json()["total_jobs"] == 2  # 1 eligible shot × 2

    def test_missing_scene(self, env):
        resp = env["client"].post(
            "/scenes/nonexistent/takes/enqueue",
            json={},
            headers={"Idempotency-Key": "skey-miss"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Queue listing/cancel
# ---------------------------------------------------------------------------

class TestQueueAPI:
    def test_list_jobs(self, env):
        env["client"].post(
            "/shots/shot-1/takes/enqueue", json={},
            headers={"Idempotency-Key": "key-list"},
        )
        resp = env["client"].get("/queue/jobs?status=pending")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_get_job(self, env):
        enqueue = env["client"].post(
            "/shots/shot-1/takes/enqueue", json={},
            headers={"Idempotency-Key": "key-get"},
        )
        job_id = enqueue.json()["jobs"][0]["id"]
        resp = env["client"].get(f"/queue/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == job_id

    def test_missing_job(self, env):
        resp = env["client"].get("/queue/jobs/nonexistent")
        assert resp.status_code == 404

    def test_cancel_pending(self, env):
        enqueue = env["client"].post(
            "/shots/shot-1/takes/enqueue", json={},
            headers={"Idempotency-Key": "key-cancel"},
        )
        job_id = enqueue.json()["jobs"][0]["id"]
        resp = env["client"].post(f"/queue/jobs/{job_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_missing(self, env):
        resp = env["client"].post("/queue/jobs/nonexistent/cancel")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Take listing
# ---------------------------------------------------------------------------

class TestTakeListingAPI:
    def test_list_takes(self, env):
        resp = env["client"].get("/shots/shot-1/takes")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == "take-1"

    def test_empty_shot(self, env):
        # shot-1 has a take, but a non-existent shot returns 404
        resp = env["client"].get("/shots/nonexistent/takes")
        assert resp.status_code == 404

    def test_approved_take_none(self, env):
        resp = env["client"].get("/shots/shot-1/approved-take")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Take lifecycle
# ---------------------------------------------------------------------------

class TestTakeLifecycleAPI:
    def test_approve(self, env):
        resp = env["client"].post("/takes/take-1/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_approved_take_returned(self, env):
        env["client"].post("/takes/take-1/approve")
        resp = env["client"].get("/shots/shot-1/approved-take")
        assert resp.status_code == 200
        assert resp.json()["id"] == "take-1"

    def test_reject(self, env):
        resp = env["client"].post("/takes/take-1/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_favorite(self, env):
        resp = env["client"].post("/takes/take-1/favorite")
        assert resp.status_code == 200
        assert resp.json()["is_favorite"] is True

    def test_unfavorite(self, env):
        env["client"].post("/takes/take-1/favorite")
        resp = env["client"].post("/takes/take-1/unfavorite")
        assert resp.status_code == 200
        assert resp.json()["is_favorite"] is False

    def test_missing_take(self, env):
        resp = env["client"].post("/takes/nonexistent/approve")
        assert resp.status_code == 404

    def test_illegal_transition(self, env):
        env["client"].post("/takes/take-1/approve")
        resp = env["client"].post("/takes/take-1/reject")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# M3 backward compat
# ---------------------------------------------------------------------------

class TestM3BackwardCompat:
    def test_existing_generate_route_present(self, env):
        routes = [r.path for r in env["client"].app.routes if hasattr(r, "path")]
        assert "/shots/{shot_id}/generate" in routes
