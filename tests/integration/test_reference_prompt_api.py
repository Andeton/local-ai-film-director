"""Integration tests for P4.2/P4.3 — Reference prompt preview and provenance API.

Tests new endpoints: prompt preview, override passthrough, generation request lookup.
Uses real SQLite, mock ComfyUI services.
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
from film_director.generation.reference_generator import (
    ReferenceGenerationService,
    DEFAULT_CHARACTER_NEGATIVE,
    DEFAULT_ENVIRONMENT_NEGATIVE,
)
from film_director.main import _ERROR_STATUS
from film_director.models.canonical import (
    CharacterReference,
    ProductionProject,
    Sequence,
    Scene,
    Beat,
    ShotSpecificationV1,
    ShotSubject,
    CameraIntent,
    GenerationPlan,
    ReferenceRequirements,
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
    ReferenceAssetRepository,
    ReferenceGenerationRequestRepository,
    ReferenceGenerationExecutionRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)
from film_director.services.reference_lifecycle import ReferenceLifecycleService
from film_director.services.reference_service import ReferenceIngestService

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


def _create_synthetic_image(path: str):
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=64x64:d=0.1",
        "-frames:v", "1", path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=10)
    if result.returncode != 0:
        pytest.skip("FFmpeg not available")


@pytest.fixture
def env(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()

    storage = os.path.join(str(tmp_path), "storage")
    os.makedirs(storage, exist_ok=True)

    project_repo = ProjectRepository(db)
    seq_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)
    char_repo = CharacterRepository(db)
    ref_repo = ReferenceAssetRepository(db)
    ref_gen_req_repo = ReferenceGenerationRequestRepository(db)
    ref_gen_exec_repo = ReferenceGenerationExecutionRepository(db)

    with db.connection() as conn:
        project_repo.save_project(ProductionProject(
            id="proj-1", wc_project_id="wc-p1", title="Test",
            status="active", created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
            director_context={
                "description": "A detective enters an abandoned hospital at night.",
                "environment_description": "A dimly lit hospital lobby with crumbling walls.",
            },
        ), conn=conn)
        seq_repo.save_sequence(Sequence(
            id="seq-1", project_id="proj-1", name="Main", order_index=0,
        ), conn=conn)
        scene_repo.save_scene(Scene(
            id="scene-1", sequence_id="seq-1", wc_scene_id="wc-s1",
            name="S1", location="", description="", order_index=0,
            provenance=_prov(),
        ), conn=conn)
        char_repo.save_character(CharacterReference(
            id="char-1", project_id="proj-1", wc_character_id="wc-c1",
            name="Detective", description="Main character",
            appearance="Tall, dark coat, weathered face",
            provenance=_prov(),
        ), conn=conn)

    # Mock ComfyUI for reference generation
    mock_comfyui = MagicMock(spec=ComfyUIAdapter)
    image_path = os.path.join(str(tmp_path), "fake_ref.png")
    _create_synthetic_image(image_path)
    mock_comfyui.submit.return_value = "ref-prompt-id"
    mock_comfyui.monitor.return_value = None
    mock_comfyui.get_result.return_value = ComfyUIGenerationResult(
        prompt_id="ref-prompt-id", output_node_id="9",
        outputs=[ComfyUIOutputRef("out.png", "", "output")],
    )
    mock_comfyui.download_output.side_effect = lambda ref, dest: shutil.copy2(image_path, dest) or dest

    ref_gen_service = ReferenceGenerationService(
        asset_repo=ref_repo,
        request_repo=ref_gen_req_repo,
        execution_repo=ref_gen_exec_repo,
        comfyui=mock_comfyui,
        storage_root=storage,
        project_root=WORKTREE,
        db=db,
    )

    ref_ingest = ReferenceIngestService(repo=ref_repo, storage_root=storage)
    ref_lifecycle = ReferenceLifecycleService(ref_repo)

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
        ref_asset_repo=ref_repo,
        ref_ingest_service=ref_ingest,
        ref_generation_service=ref_gen_service,
        ref_lifecycle_service=ref_lifecycle,
    )
    app.include_router(router)
    client = TestClient(app)

    return {
        "client": client, "db": db, "ref_repo": ref_repo,
        "ref_gen_req_repo": ref_gen_req_repo,
    }


# ---------------------------------------------------------------------------
# Character prompt preview
# ---------------------------------------------------------------------------

class TestCharacterPromptPreview:
    def test_returns_default_prompt(self, env):
        r = env["client"].get("/characters/char-1/reference-prompt-preview?kind=character_body")
        assert r.status_code == 200
        d = r.json()
        assert "Detective" in d["prompt"]
        assert "Tall, dark coat" in d["prompt"]
        assert "full body standing pose" in d["prompt"]
        assert d["negative_prompt"] == DEFAULT_CHARACTER_NEGATIVE
        assert d["character_id"] == "char-1"
        assert d["kind"] == "character_body"

    def test_face_kind_uses_headshot(self, env):
        r = env["client"].get("/characters/char-1/reference-prompt-preview?kind=character_face")
        assert r.status_code == 200
        assert "portrait headshot" in r.json()["prompt"]

    def test_missing_character_404(self, env):
        r = env["client"].get("/characters/nonexistent/reference-prompt-preview")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Environment prompt preview
# ---------------------------------------------------------------------------

class TestEnvironmentPromptPreview:
    def test_returns_default_prompt(self, env):
        r = env["client"].get("/projects/proj-1/environment-reference-prompt-preview")
        assert r.status_code == 200
        d = r.json()
        assert "dimly lit hospital lobby" in d["prompt"]
        assert "production design reference" in d["prompt"]
        assert d["negative_prompt"] == DEFAULT_ENVIRONMENT_NEGATIVE
        assert d["project_id"] == "proj-1"

    def test_default_negative_is_generic(self, env):
        """P4.2a: preview endpoint returns generic negative without P3 story terms."""
        r = env["client"].get("/projects/proj-1/environment-reference-prompt-preview")
        neg = r.json()["negative_prompt"].lower()
        assert "people" in neg  # generic exclusion present
        for term in ["police car", "blood", "envelope", "weapon"]:
            assert term not in neg, f"Story-specific term in preview: {term}"

    def test_missing_project_404(self, env):
        r = env["client"].get("/projects/nonexistent/environment-reference-prompt-preview")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Prompt override reaches generation request
# ---------------------------------------------------------------------------

class TestPromptOverridePassthrough:
    def test_character_prompt_override_persisted(self, env):
        r = env["client"].post("/characters/char-1/references/generate", json={
            "kind": "character_body",
            "prompt_override": "Custom character prompt text",
            "negative_prompt_override": "Custom negative text",
        })
        assert r.status_code == 200
        request_id = r.json()["request_id"]
        assert request_id.startswith("rgreq_")

        # Verify prompt stored in generation request
        req = env["ref_gen_req_repo"].get(request_id)
        assert req.prompt == "Custom character prompt text"
        assert req.negative_prompt == "Custom negative text"

    def test_environment_prompt_override_persisted(self, env):
        r = env["client"].post("/projects/proj-1/environment-references/generate", json={
            "prompt_override": "Custom env prompt",
            "negative_prompt_override": "Custom env negative",
        })
        assert r.status_code == 200
        request_id = r.json()["request_id"]
        assert request_id.startswith("rgreq_")

        req = env["ref_gen_req_repo"].get(request_id)
        assert req.prompt == "Custom env prompt"
        assert req.negative_prompt == "Custom env negative"

    def test_environment_negative_override_accepted(self, env):
        """Environment generation now accepts negative_prompt_override."""
        r = env["client"].post("/projects/proj-1/environment-references/generate", json={
            "negative_prompt_override": "only negative override",
        })
        assert r.status_code == 200
        req = env["ref_gen_req_repo"].get(r.json()["request_id"])
        assert req.negative_prompt == "only negative override"


# ---------------------------------------------------------------------------
# Generated reference exposes prompt used (P4.3)
# ---------------------------------------------------------------------------

class TestGenerationRequestLookup:
    def test_character_ref_links_to_request(self, env):
        """Generated character ref's source_provenance is a real request_id."""
        r = env["client"].post("/characters/char-1/references/generate", json={
            "kind": "character_body",
        })
        assert r.status_code == 200
        asset = r.json()["asset"]
        request_id = r.json()["request_id"]
        assert asset["source_provenance"] == request_id

    def test_environment_ref_links_to_request(self, env):
        """Generated environment ref's source_provenance is now a real request_id."""
        r = env["client"].post("/projects/proj-1/environment-references/generate", json={})
        assert r.status_code == 200
        asset = r.json()["asset"]
        request_id = r.json()["request_id"]
        assert request_id.startswith("rgreq_")
        assert asset["source_provenance"] == request_id

    def test_reference_generation_request_endpoint(self, env):
        """GET /reference-generation-requests/{id} returns stored prompt."""
        r = env["client"].post("/characters/char-1/references/generate", json={
            "kind": "character_body",
        })
        request_id = r.json()["request_id"]

        r2 = env["client"].get(f"/reference-generation-requests/{request_id}")
        assert r2.status_code == 200
        d = r2.json()
        assert "Detective" in d["prompt"]
        assert d["negative_prompt"] == DEFAULT_CHARACTER_NEGATIVE
        assert d["requested_kind"] == "character_body"

    def test_missing_request_404(self, env):
        r = env["client"].get("/reference-generation-requests/nonexistent")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# User upload has no generation prompt
# ---------------------------------------------------------------------------

class TestUserUploadNoPrompt:
    def test_upload_ref_has_no_rgreq_provenance(self, env):
        """User-uploaded refs have source_provenance that is not a rgreq_ ID."""
        refs = env["ref_repo"].list_by_project("proj-1")
        uploads = [r for r in refs if r.source == ReferenceSource.USER_UPLOAD]
        for u in uploads:
            assert not u.source_provenance.startswith("rgreq_")


# ---------------------------------------------------------------------------
# Historical ref without prompt metadata degrades gracefully
# ---------------------------------------------------------------------------

class TestHistoricalRefDegradation:
    def test_old_provenance_request_lookup_returns_404(self, env):
        """Pre-P4 refs with pseudo source_provenance return 404 on request lookup."""
        r = env["client"].get("/reference-generation-requests/env_gen_abc12345")
        assert r.status_code == 404

    def test_old_provenance_request_lookup_returns_404_for_pseudo_env(self, env):
        r = env["client"].get("/reference-generation-requests/env_ref_xyz")
        assert r.status_code == 404
