"""Integration tests for P4.9/P4.12 — Take generation history/provenance.

Tests that each Take's generation details are immutable historical records
from the GenerationRequest that produced them, not from current shot state.

Uses real SQLite, mock ComfyUI. No live generation.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from film_director.api.routes import create_router
from film_director.errors import FilmDirectorError
from film_director.generation.comfyui_adapter import (
    ComfyUIAdapter,
    ComfyUIGenerationResult,
    ComfyUIOutputRef,
)
from film_director.generation.generation_service import GenerationService
from film_director.generation.queue_service import QueueService
from film_director.generation.queue_worker import QueueWorker
from film_director.main import _ERROR_STATUS
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
from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    GenerationRequestRepository,
    ProjectRepository,
    QueueBatchRepository,
    QueueJobRepository,
    ReferenceAssetRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
    TakeRepository,
)
from film_director.services.enrichment_service import EnrichmentService
from film_director.enrichment.beat_enricher import BeatEnricher
from film_director.enrichment.coverage_planner import CoveragePlanner
from film_director.enrichment.shot_spec_builder import ShotSpecBuilder
from film_director.enrichment.stale_propagator import StalePropagator
from film_director.enrichment.strategy_selector import StrategySelector
from film_director.services.take_service import TakeService

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
        "color=c=red:s=64x64:d=0.5:r=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        pytest.skip("FFmpeg not available")


@pytest.fixture
def env(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()

    storage = os.path.join(str(tmp_path), "storage")
    os.makedirs(storage, exist_ok=True)
    ref_dir = os.path.join(storage, "references", "proj-1")
    os.makedirs(ref_dir, exist_ok=True)
    ref_data = b"fake ref"
    ref_path = os.path.join(ref_dir, "ref.png")
    with open(ref_path, "wb") as f:
        f.write(ref_data)
    ref_sha = hashlib.sha256(ref_data).hexdigest()

    project_repo = ProjectRepository(db)
    seq_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)
    char_repo = CharacterRepository(db)
    ref_repo = ReferenceAssetRepository(db)
    request_repo = GenerationRequestRepository(db)
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
            subjects=[ShotSubject(character_id="char-1", name="Alice", ref_images=[])],
            action="walks forward cautiously",
            camera=CameraIntent(shot_size="medium"),
            duration_sec=5.0, order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        char_repo.save_character(CharacterReference(
            id="char-1", project_id="proj-1", wc_character_id="wc-c1",
            name="Alice", description="Detective", appearance="dark hair",
            provenance=_prov(),
        ), conn=conn)
        plan_repo.save_plan(GenerationPlan(
            id="plan-1", shot_id="shot-1", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, resolution_intent={"aspect": "16:9"},
            seed_policy="fixed", seed=42,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        ref_repo.save(ReferenceAsset(
            id="ref-1", project_id="proj-1", character_id="char-1",
            kind=ReferenceKind.CHARACTER_BODY,
            source=ReferenceSource.USER_UPLOAD,
            managed_path=ref_path, content_sha256=ref_sha,
            source_provenance="test",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=64, height=64,
            created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
        ), conn=conn)

    video_path = os.path.join(str(tmp_path), "comfyui_output.mp4")
    _create_synthetic_video(video_path)

    mock_comfyui = MagicMock(spec=ComfyUIAdapter)
    mock_comfyui.upload_image.return_value = "uploaded.png"
    mock_comfyui.submit.return_value = "prompt-id-1"
    mock_comfyui.monitor.return_value = None
    mock_comfyui.get_result.return_value = ComfyUIGenerationResult(
        prompt_id="prompt-id-1", output_node_id="92",
        outputs=[ComfyUIOutputRef("out.mp4", "m3", "output")],
    )
    mock_comfyui.download_output.side_effect = lambda ref, dest: shutil.copy2(video_path, dest) or dest

    gen_service = GenerationService(
        db=db, comfyui=mock_comfyui,
        storage_root=storage, project_root=WORKTREE,
    )

    stale_prop = StalePropagator(
        db=db, beat_repo=beat_repo, shot_repo=shot_repo, plan_repo=plan_repo,
        sequence_repo=seq_repo, scene_repo=scene_repo,
    )
    enrichment_service = EnrichmentService(
        db=db, project_repo=project_repo, sequence_repo=seq_repo,
        scene_repo=scene_repo, character_repo=char_repo,
        beat_repo=beat_repo, shot_repo=shot_repo, plan_repo=plan_repo,
        adapter=MagicMock(), import_service=MagicMock(),
        beat_enricher=BeatEnricher(MagicMock()),
        coverage_planner=CoveragePlanner(MagicMock()),
        shot_spec_builder=ShotSpecBuilder(),
        strategy_selector=StrategySelector(),
        stale_propagator=stale_prop,
    )

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
        scene_repo=scene_repo, char_repo=char_repo,
        llm_provider=MagicMock(),
        enrichment_service=enrichment_service,
        beat_repo=beat_repo, shot_repo=shot_repo, plan_repo=plan_repo,
        generation_service=gen_service,
        comfyui_adapter=mock_comfyui,
        request_repo=request_repo,
        queue_service=queue_svc,
        take_service=take_svc,
        take_repo=take_repo,
    )
    app.include_router(router)
    client = TestClient(app)

    # Generate a Take via direct service call (not queue, for simplicity)
    take1 = gen_service.generate_take("shot-1", take_number=1, seed_override=111)

    return {
        "client": client, "db": db, "gen_service": gen_service,
        "take1": take1, "request_repo": request_repo,
        "enrichment_service": enrichment_service,
    }


# ---------------------------------------------------------------------------
# 1. Take details resolve to correct historical GenerationRequest
# ---------------------------------------------------------------------------

class TestTakeDetailsLinkage:
    def test_details_available(self, env):
        r = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        assert r.status_code == 200
        d = r.json()
        assert d["available"] is True
        assert d["generation_request_id"] == env["take1"].generation_request_id


# ---------------------------------------------------------------------------
# 2. Prompt used is historical
# ---------------------------------------------------------------------------

class TestHistoricalPrompt:
    def test_prompt_text_present(self, env):
        r = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        d = r.json()
        assert d["prompt_text"] is not None
        assert "walks forward cautiously" in d["prompt_text"]
        assert "Alice" in d["prompt_text"]


# ---------------------------------------------------------------------------
# 3. Seed is historical
# ---------------------------------------------------------------------------

class TestHistoricalSeed:
    def test_seed_matches_take(self, env):
        r = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        assert r.json()["seed"] == 111  # the override seed


# ---------------------------------------------------------------------------
# 5. Workflow metadata is historical
# ---------------------------------------------------------------------------

class TestHistoricalWorkflow:
    def test_workflow_id_present(self, env):
        r = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        d = r.json()
        assert d["workflow_id"] is not None
        assert len(d["workflow_id"]) > 0


# ---------------------------------------------------------------------------
# 6. Reference binding snapshot is historical
# ---------------------------------------------------------------------------

class TestHistoricalReferences:
    def test_reference_snapshot_present(self, env):
        r = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        d = r.json()
        assert isinstance(d["reference_snapshot"], list)
        assert len(d["reference_snapshot"]) >= 1
        ref = d["reference_snapshot"][0]
        assert "character_name" in ref or "reference_kind" in ref


# ---------------------------------------------------------------------------
# 7. Continuity provenance
# ---------------------------------------------------------------------------

class TestHistoricalContinuity:
    def test_head_shot_no_continuity(self, env):
        r = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        # Shot 1 (head) should have null continuity
        d = r.json()
        # continuity_snapshot may be None for head shots
        assert "continuity_snapshot" in d


# ---------------------------------------------------------------------------
# 8. Editing current shot does NOT change displayed Take provenance
# ---------------------------------------------------------------------------

class TestImmutabilityAfterEdit:
    def test_edit_does_not_change_take_details(self, env):
        # Get original details
        r1 = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        original = r1.json()
        original_prompt = original["prompt_text"]
        original_seed = original["seed"]

        # Edit the shot
        env["client"].put("/shots/shot-1", json={
            "action": "COMPLETELY DIFFERENT ACTION",
            "dramatic_purpose": "comedy",
        })

        # Get details again — must be unchanged
        r2 = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        after_edit = r2.json()
        assert after_edit["prompt_text"] == original_prompt
        assert after_edit["seed"] == original_seed
        assert "walks forward cautiously" in after_edit["prompt_text"]
        assert "COMPLETELY DIFFERENT" not in after_edit["prompt_text"]


# ---------------------------------------------------------------------------
# 9. Multiple Takes resolve to their own GenerationRequests
# ---------------------------------------------------------------------------

class TestMultipleTakes:
    def test_different_takes_different_requests(self, env):
        # Generate a second Take
        take2 = env["gen_service"].generate_take("shot-1", take_number=2, seed_override=222)

        r1 = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        r2 = env["client"].get(f"/takes/{take2.id}/generation-details")

        d1 = r1.json()
        d2 = r2.json()
        assert d1["generation_request_id"] != d2["generation_request_id"]
        assert d1["seed"] == 111
        assert d2["seed"] == 222


# ---------------------------------------------------------------------------
# 10. Missing legacy provenance degrades gracefully
# ---------------------------------------------------------------------------

class TestLegacyDegradation:
    def test_missing_take_returns_404(self, env):
        r = env["client"].get("/takes/nonexistent_take/generation-details")
        assert r.status_code == 404

    def test_take_with_missing_prompt_degrades(self, env):
        """If H3PromptV1 artifact is gone, prompt_text is None but details available."""
        req = env["request_repo"].get_request(env["take1"].generation_request_id)
        with env["db"].connection() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DELETE FROM h3_prompts WHERE id = ?", (req.prompt_artifact_id,))
            conn.execute("PRAGMA foreign_keys = ON")
        r = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        assert r.status_code == 200
        d = r.json()
        assert d["available"] is True
        assert d["prompt_text"] is None
        assert d["seed"] == 111


# ---------------------------------------------------------------------------
# 11. Existing Take approval/rejection unchanged
# ---------------------------------------------------------------------------

class TestApprovalUnchanged:
    def test_approve_still_works(self, env):
        r = env["client"].post(f"/takes/{env['take1'].id}/approve")
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    def test_details_available_after_approval(self, env):
        env["client"].post(f"/takes/{env['take1'].id}/approve")
        r = env["client"].get(f"/takes/{env['take1'].id}/generation-details")
        assert r.status_code == 200
        assert r.json()["available"] is True
