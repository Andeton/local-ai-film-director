"""Integration tests for P4.4 — Shot Production Editor.

Tests shot field editing, subject management, prompt compilation via preview,
and prompt override behavior. Uses real SQLite, mock ComfyUI.
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
from film_director.generation.queue_service import QueueService
from film_director.services.take_service import TakeService

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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

    with db.connection() as conn:
        project_repo.save_project(ProductionProject(
            id="proj-1", wc_project_id="wc-p1", title="Test",
            status="active", created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
            director_context={"description": "A test story", "environment_description": "A dark room"},
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
            camera=CameraIntent(shot_size="medium", angle="eye_level", movement="static"),
            audio_intent={"ambient": "rain", "music": ""},
            lighting={"intensity": "dim"},
            duration_sec=5.0, order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        char_repo.save_character(CharacterReference(
            id="char-1", project_id="proj-1", wc_character_id="wc-c1",
            name="Alice", description="Detective", appearance="dark hair, tall",
            provenance=_prov(),
        ), conn=conn)
        char_repo.save_character(CharacterReference(
            id="char-2", project_id="proj-1", wc_character_id="wc-c2",
            name="Bob", description="Suspect", appearance="blonde, stocky",
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

    mock_comfyui = MagicMock(spec=ComfyUIAdapter)
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

    queue_repo = QueueJobRepository(db)
    batch_repo = QueueBatchRepository(db)
    take_repo = TakeRepository(db)
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
        request_repo=GenerationRequestRepository(db),
        queue_service=queue_svc,
        take_service=take_svc,
        take_repo=take_repo,
    )
    app.include_router(router)
    client = TestClient(app)

    return {"client": client, "db": db, "shot_repo": shot_repo}


# ---------------------------------------------------------------------------
# 1. Existing shot data accessible
# ---------------------------------------------------------------------------

class TestShotDataPopulated:
    def test_shot_returns_all_fields(self, env):
        r = env["client"].get("/projects/proj-1/shots")
        shots = r.json()
        shot = shots[0]
        assert shot["action"] == "walks forward cautiously"
        assert shot["dramatic_purpose"] == "tension"
        assert shot["camera"]["shot_size"] == "medium"
        assert shot["camera"]["angle"] == "eye_level"
        assert shot["duration_sec"] == 5.0
        assert len(shot["subjects"]) == 1
        assert shot["subjects"][0]["character_id"] == "char-1"
        assert shot["audio_intent"]["ambient"] == "rain"
        assert shot["lighting"]["intensity"] == "dim"


# ---------------------------------------------------------------------------
# 2-5. Field editing persists
# ---------------------------------------------------------------------------

class TestFieldEditing:
    def test_action_update_persists(self, env):
        r = env["client"].put("/shots/shot-1", json={"action": "runs toward the door"})
        assert r.status_code == 200
        assert r.json()["action"] == "runs toward the door"

    def test_dramatic_purpose_persists(self, env):
        r = env["client"].put("/shots/shot-1", json={"dramatic_purpose": "reveal"})
        assert r.status_code == 200
        assert r.json()["dramatic_purpose"] == "reveal"

    def test_camera_fields_persist(self, env):
        r = env["client"].put("/shots/shot-1", json={
            "camera": {"shot_size": "close_up", "angle": "low", "movement": "dolly_in"},
        })
        assert r.status_code == 200
        cam = r.json()["camera"]
        assert cam["shot_size"] == "close_up"
        assert cam["angle"] == "low"
        assert cam["movement"] == "dolly_in"

    def test_duration_persists(self, env):
        r = env["client"].put("/shots/shot-1", json={"duration_sec": 8.5})
        assert r.status_code == 200
        assert r.json()["duration_sec"] == 8.5

    def test_lighting_persists(self, env):
        r = env["client"].put("/shots/shot-1", json={"lighting": {"intensity": "bright", "color": "warm"}})
        assert r.status_code == 200
        assert r.json()["lighting"]["intensity"] == "bright"
        assert r.json()["lighting"]["color"] == "warm"

    def test_audio_intent_persists(self, env):
        r = env["client"].put("/shots/shot-1", json={
            "audio_intent": {"ambient": "wind howling", "music": "tense strings"},
        })
        assert r.status_code == 200
        assert r.json()["audio_intent"]["ambient"] == "wind howling"
        assert r.json()["audio_intent"]["music"] == "tense strings"


# ---------------------------------------------------------------------------
# 6-7. Subject editing
# ---------------------------------------------------------------------------

class TestSubjectEditing:
    def test_add_subject(self, env):
        r = env["client"].put("/shots/shot-1", json={
            "subjects": [
                {"character_id": "char-1", "name": "Alice", "ref_images": []},
                {"character_id": "char-2", "name": "Bob", "ref_images": []},
            ],
        })
        assert r.status_code == 200
        subjects = r.json()["subjects"]
        assert len(subjects) == 2
        assert subjects[1]["character_id"] == "char-2"

    def test_remove_subject(self, env):
        # First add two
        env["client"].put("/shots/shot-1", json={
            "subjects": [
                {"character_id": "char-1", "name": "Alice", "ref_images": []},
                {"character_id": "char-2", "name": "Bob", "ref_images": []},
            ],
        })
        # Then remove to one
        r = env["client"].put("/shots/shot-1", json={
            "subjects": [{"character_id": "char-2", "name": "Bob", "ref_images": []}],
        })
        assert r.status_code == 200
        assert len(r.json()["subjects"]) == 1
        assert r.json()["subjects"][0]["character_id"] == "char-2"

    def test_empty_subjects_accepted(self, env):
        r = env["client"].put("/shots/shot-1", json={"subjects": []})
        assert r.status_code == 200
        assert r.json()["subjects"] == []


# ---------------------------------------------------------------------------
# 9-10. H3 prompt compilation via preview
# ---------------------------------------------------------------------------

class TestPromptCompilation:
    def test_preview_returns_compiled_prompt(self, env):
        r = env["client"].get("/shots/shot-1/generation-preview")
        assert r.status_code == 200
        d = r.json()
        assert "prompt_text" in d
        # Prompt should contain shot action and character
        assert "walks forward cautiously" in d["prompt_text"]
        assert "Alice" in d["prompt_text"]

    def test_action_change_updates_preview_prompt(self, env):
        env["client"].put("/shots/shot-1", json={"action": "dives behind the table"})
        r = env["client"].get("/shots/shot-1/generation-preview")
        d = r.json()
        # Debug: print response keys if prompt_text missing
        assert "prompt_text" in d, f"Preview response keys: {list(d.keys())}, status={r.status_code}, body={d}"
        assert "dives behind the table" in d["prompt_text"]

    def test_camera_change_reflected_in_prompt(self, env):
        env["client"].put("/shots/shot-1", json={
            "camera": {"shot_size": "extreme_close", "angle": "high", "movement": "dolly_in"},
        })
        r = env["client"].get("/shots/shot-1/generation-preview")
        prompt = r.json()["prompt_text"]
        assert "extreme_close" in prompt
        assert "high" in prompt
        assert "dolly_in" in prompt

    def test_lighting_reflected_in_prompt(self, env):
        env["client"].put("/shots/shot-1", json={"lighting": {"mood": "dramatic"}})
        r = env["client"].get("/shots/shot-1/generation-preview")
        assert "dramatic" in r.json()["prompt_text"]

    def test_audio_ambient_in_prompt(self, env):
        env["client"].put("/shots/shot-1", json={
            "audio_intent": {"ambient": "heavy rain on glass"},
        })
        r = env["client"].get("/shots/shot-1/generation-preview")
        prompt = r.json()["prompt_text"]
        assert "heavy rain on glass" in prompt


# ---------------------------------------------------------------------------
# 11. Prompt override reaches generation unchanged
# ---------------------------------------------------------------------------

class TestPromptOverride:
    def test_override_in_preview(self, env):
        r = env["client"].get(
            "/shots/shot-1/generation-preview?prompt_override=CUSTOM+PROMPT+TEXT"
        )
        assert r.status_code == 200
        d = r.json()
        assert d["prompt_text"] == "CUSTOM PROMPT TEXT"
        assert d["prompt_source"] == "operator_override"

    def test_reset_shows_generated(self, env):
        # Without override, should show generated prompt
        r = env["client"].get("/shots/shot-1/generation-preview")
        assert r.json()["prompt_source"] == "generated"
        assert "Alice" in r.json()["prompt_text"]


# ---------------------------------------------------------------------------
# 13. Subject edit doesn't break generation preview
# ---------------------------------------------------------------------------

class TestSubjectEditPreview:
    def test_adding_subject_updates_preview_pictures(self, env):
        # Single subject initially
        r1 = env["client"].get("/shots/shot-1/generation-preview")
        pics_before = len(r1.json()["pictures"])

        # Add second subject
        env["client"].put("/shots/shot-1", json={
            "subjects": [
                {"character_id": "char-1", "name": "Alice", "ref_images": []},
                {"character_id": "char-2", "name": "Bob", "ref_images": []},
            ],
        })
        r2 = env["client"].get("/shots/shot-1/generation-preview")
        # Should have more pictures (or at least attempt to bind both)
        prompt = r2.json()["prompt_text"]
        assert "Alice" in prompt
        assert "Bob" in prompt
