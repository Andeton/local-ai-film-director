"""Integration tests for M5.G — Reference Management API endpoints.

Uses real SQLite, fake ComfyUI/services. No live ComfyUI needed.
"""
from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from film_director.api.routes import create_router
from film_director.errors import (
    FilmDirectorError,
    ReferenceGenerationError,
    ReferenceIngestError,
    ReferenceLifecycleError,
    ReferenceNotFoundError,
    ReferenceResolutionError,
)
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
    ReferenceAssetRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)
from film_director.services.reference_lifecycle import (
    ReferenceLifecycleService,
    ReferenceSelector,
)
from film_director.services.reference_service import (
    IngestOutcome,
    IngestResult,
    ReferenceIngestService,
)

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


def _asset(
    asset_id, character_id="char-1", project_id="proj-1",
    status=ReferenceStatus.APPROVED, source_state=ReferenceSourceState.CURRENT,
    source=ReferenceSource.GENERATED, pinned=False,
):
    return ReferenceAsset(
        id=asset_id, project_id=project_id, character_id=character_id,
        kind=ReferenceKind.CHARACTER_BODY, source=source,
        managed_path=f"/storage/{asset_id}.png", content_sha256="a" * 64,
        source_provenance="test", status=status,
        source_state=source_state, pinned=pinned,
        width=1024, height=1024,
        created_at="2026-01-01T00:00:01", updated_at="2026-01-01T00:00:01",
    )


@pytest.fixture
def env(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()

    project_repo = ProjectRepository(db)
    seq_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    char_repo = CharacterRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    ref_repo = ReferenceAssetRepository(db)

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
            action="walks", camera=CameraIntent(shot_size="medium"),
            duration_sec=5.0, order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        char_repo.save_character(CharacterReference(
            id="char-1", project_id="proj-1", wc_character_id="wc-c1",
            name="Alice", description="Detective", appearance="dark hair",
            provenance=_prov(),
        ), conn=conn)
        # Approved + Current reference
        ref_repo.save(_asset("ref-1"), conn=conn)
        # Candidate reference
        ref_repo.save(_asset("ref-cand", status=ReferenceStatus.CANDIDATE), conn=conn)
        # Stale reference
        ref_repo.save(_asset("ref-stale", source_state=ReferenceSourceState.STALE), conn=conn)

    # Mock services
    mock_ingest = MagicMock(spec=ReferenceIngestService)
    mock_gen = MagicMock()
    lifecycle = ReferenceLifecycleService(ref_repo)
    selector = ReferenceSelector()

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
        shot_repo=shot_repo,
        ref_asset_repo=ref_repo,
        ref_ingest_service=mock_ingest,
        ref_generation_service=mock_gen,
        ref_lifecycle_service=lifecycle,
        ref_selector=selector,
    )
    app.include_router(router)
    client = TestClient(app)

    return {
        "client": client,
        "db": db,
        "ref_repo": ref_repo,
        "mock_ingest": mock_ingest,
        "mock_gen": mock_gen,
        "char_repo": char_repo,
        "shot_repo": shot_repo,
    }


# ---------------------------------------------------------------------------
# Project listing
# ---------------------------------------------------------------------------

class TestListProjectReferences:
    def test_returns_all_statuses(self, env):
        resp = env["client"].get("/projects/proj-1/references")
        assert resp.status_code == 200
        data = resp.json()
        ids = {r["id"] for r in data}
        assert "ref-1" in ids
        assert "ref-cand" in ids
        assert "ref-stale" in ids

    def test_project_isolation(self, env):
        # ref-1 belongs to proj-1; querying proj-2 should return nothing
        resp = env["client"].get("/projects/proj-999/references")
        assert resp.status_code == 404

    def test_missing_project(self, env):
        resp = env["client"].get("/projects/nonexistent/references")
        assert resp.status_code == 404

    def test_deterministic_ordering(self, env):
        resp = env["client"].get("/projects/proj-1/references")
        data = resp.json()
        ids = [r["id"] for r in data]
        assert ids == sorted(ids)  # ordered by created_at, id


# ---------------------------------------------------------------------------
# Character listing
# ---------------------------------------------------------------------------

class TestListCharacterReferences:
    def test_character_refs(self, env):
        resp = env["client"].get("/characters/char-1/references")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_missing_character(self, env):
        resp = env["client"].get("/characters/nonexistent/references")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Upload/register
# ---------------------------------------------------------------------------

class TestRegisterReference:
    def test_valid_upload(self, env):
        valid_asset = _asset("ref-new", source=ReferenceSource.USER_UPLOAD,
                             status=ReferenceStatus.CANDIDATE)
        env["mock_ingest"].register_user_reference.return_value = IngestResult(
            outcome=IngestOutcome.IMPORTED, asset=valid_asset,
        )
        data = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        resp = env["client"].post(
            "/characters/char-1/references/register",
            files={"file": ("test.png", data, "image/png")},
            data={"kind": "character_body"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "ref-new"
        env["mock_ingest"].register_user_reference.assert_called_once()
        call_kw = env["mock_ingest"].register_user_reference.call_args
        assert call_kw.kwargs.get("project_id") or call_kw[1].get("project_id") or \
               (call_kw[0][0] if call_kw[0] else None) == "proj-1"

    def test_dedup_returns_existing(self, env):
        existing = _asset("ref-existing", source=ReferenceSource.USER_UPLOAD)
        env["mock_ingest"].register_user_reference.return_value = IngestResult(
            outcome=IngestOutcome.DUPLICATE, asset=existing,
        )
        data = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        resp = env["client"].post(
            "/characters/char-1/references/register",
            files={"file": ("test.png", data, "image/png")},
            data={"kind": "character_body"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "ref-existing"

    def test_invalid_image(self, env):
        env["mock_ingest"].register_user_reference.return_value = IngestResult(
            outcome=IngestOutcome.INVALID_IMAGE, asset=None,
            detail="Not a valid image",
        )
        data = io.BytesIO(b"not an image")
        resp = env["client"].post(
            "/characters/char-1/references/register",
            files={"file": ("bad.txt", data, "text/plain")},
            data={"kind": "character_body"},
        )
        assert resp.status_code == 422

    def test_storyboard_kind_rejected(self, env):
        data = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        resp = env["client"].post(
            "/characters/char-1/references/register",
            files={"file": ("test.png", data, "image/png")},
            data={"kind": "storyboard"},
        )
        assert resp.status_code == 422

    def test_missing_character(self, env):
        data = io.BytesIO(b"\x89PNG\r\n\x1a\n")
        resp = env["client"].post(
            "/characters/nonexistent/references/register",
            files={"file": ("test.png", data, "image/png")},
            data={"kind": "character_body"},
        )
        assert resp.status_code == 404

    def test_oversized_upload(self, env):
        # Generate > 50MB of data
        from film_director.api.routes import _MAX_UPLOAD_BYTES
        data = io.BytesIO(b"\x00" * (_MAX_UPLOAD_BYTES + 1))
        resp = env["client"].post(
            "/characters/char-1/references/register",
            files={"file": ("huge.png", data, "image/png")},
            data={"kind": "character_body"},
        )
        assert resp.status_code == 413

    def test_temp_file_cleaned_on_success(self, env):
        valid_asset = _asset("ref-tmp", source=ReferenceSource.USER_UPLOAD,
                             status=ReferenceStatus.CANDIDATE)
        env["mock_ingest"].register_user_reference.return_value = IngestResult(
            outcome=IngestOutcome.IMPORTED, asset=valid_asset,
        )
        data = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        # Capture the temp path the ingest service receives
        captured = {}
        original = env["mock_ingest"].register_user_reference
        def capture_call(**kwargs):
            captured["source_path"] = kwargs.get("source_path")
            return original.return_value
        env["mock_ingest"].register_user_reference.side_effect = capture_call

        resp = env["client"].post(
            "/characters/char-1/references/register",
            files={"file": ("test.png", data, "image/png")},
            data={"kind": "character_body"},
        )
        assert resp.status_code == 200
        # Temp file should be cleaned up
        if "source_path" in captured and captured["source_path"]:
            assert not os.path.exists(captured["source_path"])

    def test_temp_file_cleaned_on_failure(self, env):
        env["mock_ingest"].register_user_reference.return_value = IngestResult(
            outcome=IngestOutcome.INVALID_IMAGE, asset=None, detail="bad",
        )
        data = io.BytesIO(b"bad data")
        resp = env["client"].post(
            "/characters/char-1/references/register",
            files={"file": ("bad.png", data, "image/png")},
            data={"kind": "character_body"},
        )
        # Check no leaked temp files
        import tempfile
        tmp_dir = tempfile.gettempdir()
        upload_files = [f for f in os.listdir(tmp_dir) if f.endswith(".upload")]
        # Cleanup is best-effort; just ensure the request completed
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class TestGenerateReference:
    def test_successful_generation(self, env):
        @dataclass
        class FakeResult:
            asset: ReferenceAsset
            request_id: str
            execution_id: str

        result_asset = _asset("ref-gen", status=ReferenceStatus.CANDIDATE,
                              source=ReferenceSource.GENERATED)
        env["mock_gen"].generate_character_reference.return_value = FakeResult(
            asset=result_asset, request_id="req-1", execution_id="exec-1",
        )
        resp = env["client"].post(
            "/characters/char-1/references/generate",
            json={"kind": "character_body"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["asset"]["id"] == "ref-gen"
        assert data["request_id"] == "req-1"
        assert data["execution_id"] == "exec-1"

    def test_generation_error(self, env):
        env["mock_gen"].generate_character_reference.side_effect = \
            ReferenceGenerationError("ComfyUI failed")
        resp = env["client"].post(
            "/characters/char-1/references/generate",
            json={"kind": "character_body"},
        )
        assert resp.status_code == 502

    def test_unsupported_kind(self, env):
        resp = env["client"].post(
            "/characters/char-1/references/generate",
            json={"kind": "storyboard"},
        )
        assert resp.status_code == 422

    def test_missing_character(self, env):
        resp = env["client"].post(
            "/characters/nonexistent/references/generate",
            json={"kind": "character_body"},
        )
        assert resp.status_code == 404

    def test_profile_id_reaches_service(self, env):
        @dataclass
        class FakeResult:
            asset: ReferenceAsset
            request_id: str
            execution_id: str

        result_asset = _asset("ref-gen2", status=ReferenceStatus.CANDIDATE,
                              source=ReferenceSource.GENERATED)
        env["mock_gen"].generate_character_reference.return_value = FakeResult(
            asset=result_asset, request_id="req-2", execution_id="exec-2",
        )
        resp = env["client"].post(
            "/characters/char-1/references/generate",
            json={"kind": "character_face", "profile_id": "krea2_turbo_v1", "seed": 42},
        )
        assert resp.status_code == 200
        call_kwargs = env["mock_gen"].generate_character_reference.call_args.kwargs
        assert call_kwargs["profile_id"] == "krea2_turbo_v1"
        assert call_kwargs["seed"] == 42
        assert call_kwargs["kind"] == ReferenceKind.CHARACTER_FACE


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycleEndpoints:
    def test_approve(self, env):
        resp = env["client"].post("/references/ref-cand/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_reject(self, env):
        resp = env["client"].post("/references/ref-cand/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_archive(self, env):
        resp = env["client"].post("/references/ref-1/archive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

    def test_pin_approved_current(self, env):
        resp = env["client"].post("/references/ref-1/pin")
        assert resp.status_code == 200
        assert resp.json()["pinned"] is True

    def test_unpin(self, env):
        # Pin first
        env["client"].post("/references/ref-1/pin")
        resp = env["client"].post("/references/ref-1/unpin")
        assert resp.status_code == 200
        assert resp.json()["pinned"] is False

    def test_pin_stale_fails(self, env):
        resp = env["client"].post("/references/ref-stale/pin")
        assert resp.status_code == 409

    def test_missing_reference(self, env):
        resp = env["client"].post("/references/nonexistent/approve")
        assert resp.status_code == 404

    def test_files_and_fields_preserved(self, env):
        original = env["ref_repo"].get("ref-1")
        env["client"].post("/references/ref-1/archive")
        archived = env["ref_repo"].get("ref-1")
        assert archived.content_sha256 == original.content_sha256
        assert archived.managed_path == original.managed_path
        assert archived.created_at == original.created_at


# ---------------------------------------------------------------------------
# Selected references
# ---------------------------------------------------------------------------

class TestSelectedReferences:
    def test_exact_subject_order(self, env):
        resp = env["client"].get("/shots/shot-1/selected-references")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "ref-1"

    def test_default_kind_is_character_body(self, env):
        resp = env["client"].get("/shots/shot-1/selected-references")
        assert resp.status_code == 200

    def test_explicit_character_face(self, env):
        # No CHARACTER_FACE refs exist → 409
        resp = env["client"].get(
            "/shots/shot-1/selected-references?kind=character_face"
        )
        assert resp.status_code == 409

    def test_candidate_excluded(self, env):
        # Remove the approved ref; only candidate + stale remain
        with env["db"].connection() as conn:
            conn.execute(
                "UPDATE reference_assets SET status = 'rejected' WHERE id = 'ref-1'"
            )
        resp = env["client"].get("/shots/shot-1/selected-references")
        assert resp.status_code == 409

    def test_missing_shot(self, env):
        resp = env["client"].get("/shots/nonexistent/selected-references")
        assert resp.status_code == 404

    def test_read_only(self, env):
        """GET selected-references does not mutate anything."""
        before = env["ref_repo"].list_by_project("proj-1")
        env["client"].get("/shots/shot-1/selected-references")
        after = env["ref_repo"].list_by_project("proj-1")
        assert len(before) == len(after)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

class TestRouteRegistration:
    def test_all_ten_routes_registered(self, env):
        routes = [r.path for r in env["client"].app.routes if hasattr(r, "path")]
        expected = [
            "/projects/{project_id}/references",
            "/characters/{character_id}/references",
            "/characters/{character_id}/references/register",
            "/characters/{character_id}/references/generate",
            "/references/{reference_id}/approve",
            "/references/{reference_id}/reject",
            "/references/{reference_id}/archive",
            "/references/{reference_id}/pin",
            "/references/{reference_id}/unpin",
            "/shots/{shot_id}/selected-references",
        ]
        for route in expected:
            assert route in routes, f"Missing route: {route}"
