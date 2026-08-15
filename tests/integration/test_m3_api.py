"""Deterministic API tests for M3 generation routes.

Uses real SQLite, fake ComfyUIAdapter. No real ComfyUI needed.
"""
import os
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from film_director.errors import (
    ComfyUIUnavailableError,
    GenerationError,
    UnsupportedStrategyError,
)
from film_director.generation.comfyui_adapter import (
    ComfyUIAdapter,
    ComfyUIGenerationResult,
    ComfyUIOutputRef,
)
from film_director.generation.generation_service import GenerationService
from film_director.api.routes import create_router
from film_director.models.canonical import (
    Beat,
    CameraIntent,
    CharacterReference,
    GenerationPlan,
    ProductionProject,
    ReferenceRequirements,
    Scene,
    Sequence,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    GenerationRequestRepository,
    H3PromptRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
    TakeRepository,
)

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


def _create_synthetic_video(path: str):
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "color=c=green:s=64x64:d=0.5:r=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        pytest.skip("FFmpeg not available")


@pytest.fixture
def api_env(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()
    storage = os.path.join(str(tmp_path), "storage")
    os.makedirs(storage)

    ref_dir = os.path.join(str(tmp_path), "refs")
    os.makedirs(ref_dir)
    ref_path = os.path.join(ref_dir, "alice.png")
    with open(ref_path, "wb") as f:
        f.write(b"fake ref")

    with db.connection() as conn:
        ProjectRepository(db).save_project(ProductionProject(
            id="proj-1", wc_project_id="wc-p1", title="Test",
            status="active", created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        ), conn=conn)
        SequenceRepository(db).save_sequence(Sequence(
            id="seq-1", project_id="proj-1", name="S", order_index=0,
        ), conn=conn)
        SceneRepository(db).save_scene(Scene(
            id="scene-1", sequence_id="seq-1", wc_scene_id="wc-s1",
            name="S1", location="", description="", order_index=0,
            provenance=_prov(),
        ), conn=conn)
        BeatRepository(db).save_beat(Beat(
            id="beat-1", scene_id="scene-1", dramatic_action="enters",
            character_intention="investigate", change="finds clue",
            order_index=0, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        ShotRepository(db).save_shot(ShotSpecificationV1(
            id="shot-1", beat_id="beat-1", dramatic_purpose="tension",
            subjects=[ShotSubject(character_id="char-1", name="Alice", ref_images=[ref_path])],
            action="walks forward", camera=CameraIntent(shot_size="medium"),
            duration_sec=5.0, order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        CharacterRepository(db).save_character(CharacterReference(
            id="char-1", project_id="proj-1", wc_character_id="wc-c1",
            name="Alice", description="Detective", appearance="dark hair",
            provenance=_prov(),
        ), conn=conn)
        GenerationPlanRepository(db).save_plan(GenerationPlan(
            id="plan-1", shot_id="shot-1", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, resolution_intent={"aspect": "16:9"},
            seed_policy="fixed", seed=42,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)

    # Create mock ComfyUI
    video_path = os.path.join(str(tmp_path), "fake_output.mp4")
    _create_synthetic_video(video_path)

    mock_comfyui = MagicMock(spec=ComfyUIAdapter)
    mock_comfyui.upload_image.return_value = "uploaded.png"
    mock_comfyui.submit.return_value = "prompt-123"
    mock_comfyui.monitor.return_value = None
    mock_comfyui.get_result.return_value = ComfyUIGenerationResult(
        prompt_id="prompt-123", output_node_id="92",
        outputs=[ComfyUIOutputRef("out.mp4", "m3", "output")],
    )
    mock_comfyui.download_output.side_effect = lambda ref, dest: shutil.copy2(video_path, dest) or dest
    mock_comfyui.health.return_value = {"system": {"os": "nt"}, "devices": []}

    gen_service = GenerationService(
        db=db, comfyui=mock_comfyui, storage_root=storage, project_root=WORKTREE,
    )
    request_repo = GenerationRequestRepository(db)

    from fastapi import FastAPI
    from film_director.errors import FilmDirectorError
    from film_director.main import _ERROR_STATUS

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
        project_repo=ProjectRepository(db), seq_repo=SequenceRepository(db),
        scene_repo=SceneRepository(db), char_repo=CharacterRepository(db),
        llm_provider=MagicMock(),
        generation_service=gen_service,
        comfyui_adapter=mock_comfyui,
        request_repo=request_repo,
    )
    app.include_router(router)
    client = TestClient(app)

    return {"client": client, "db": db, "mock_comfyui": mock_comfyui}


# ---------------------------------------------------------------------------
# POST /shots/{shot_id}/generate
# ---------------------------------------------------------------------------

class TestGenerateShot:
    def test_successful_generation(self, api_env):
        resp = api_env["client"].post("/shots/shot-1/generate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["shot_id"] == "shot-1"
        assert data["status"] == "succeeded"
        assert data["seed"] == 42
        assert "take_id" in data
        assert "generation_request_id" in data
        assert "video_path" in data

    def test_missing_shot(self, api_env):
        resp = api_env["client"].post("/shots/nonexistent/generate")
        assert resp.status_code == 500  # GenerationError

    def test_unsupported_strategy(self, api_env):
        with api_env["db"].connection() as conn:
            conn.execute(
                "UPDATE generation_plans SET strategy = 'TEXT_TO_VIDEO' WHERE id = 'plan-1'"
            )
        resp = api_env["client"].post("/shots/shot-1/generate")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /generation-requests/{request_id}
# ---------------------------------------------------------------------------

class TestGetGenerationRequest:
    def test_existing_request(self, api_env):
        # Generate first to create a request
        api_env["client"].post("/shots/shot-1/generate")
        req_repo = GenerationRequestRepository(api_env["db"])
        reqs = req_repo.get_requests_by_shot("shot-1")
        assert len(reqs) > 0
        resp = api_env["client"].get(f"/generation-requests/{reqs[0].id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == reqs[0].id

    def test_missing_request(self, api_env):
        resp = api_env["client"].get("/generation-requests/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /integrations/comfyui/health
# ---------------------------------------------------------------------------

class TestComfyUIHealth:
    def test_healthy(self, api_env):
        resp = api_env["client"].get("/integrations/comfyui/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True

    def test_unavailable(self, api_env):
        api_env["mock_comfyui"].health.side_effect = ComfyUIUnavailableError("down")
        resp = api_env["client"].get("/integrations/comfyui/health")
        assert resp.status_code == 503
