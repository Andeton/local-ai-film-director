"""Integration tests for generation readiness with reference management.

Validates that readiness correctly checks for CHARACTER_BODY references
(matching what GenerationService actually requires) and that the reference
lifecycle actions update readiness accordingly.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from film_director.api.routes import create_router
from film_director.errors import FilmDirectorError
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
    ProjectRepository,
    ReferenceAssetRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
    TakeRepository,
)
from film_director.services.reference_lifecycle import (
    ReferenceLifecycleService,
    ReferenceSelector,
)


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


def _char_body_asset(asset_id, character_id="char-1", project_id="proj-1",
                     status=ReferenceStatus.CANDIDATE,
                     source_state=ReferenceSourceState.CURRENT, pinned=False):
    return ReferenceAsset(
        id=asset_id, project_id=project_id, character_id=character_id,
        kind=ReferenceKind.CHARACTER_BODY, source=ReferenceSource.GENERATED,
        managed_path=f"references/{project_id}/{asset_id}/original.png",
        content_sha256="a" * 64, source_provenance="test",
        status=status, source_state=source_state, pinned=pinned,
        width=1024, height=1024,
        created_at="2026-01-01T00:00:01", updated_at="2026-01-01T00:00:01",
    )


def _char_face_asset(asset_id, character_id="char-1", project_id="proj-1",
                     status=ReferenceStatus.APPROVED):
    return ReferenceAsset(
        id=asset_id, project_id=project_id, character_id=character_id,
        kind=ReferenceKind.CHARACTER_FACE, source=ReferenceSource.GENERATED,
        managed_path=f"references/{project_id}/{asset_id}/original.png",
        content_sha256="b" * 64, source_provenance="test",
        status=status, source_state=ReferenceSourceState.CURRENT, pinned=False,
        width=512, height=512,
        created_at="2026-01-01T00:00:02", updated_at="2026-01-01T00:00:02",
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
    plan_repo = GenerationPlanRepository(db)
    ref_repo = ReferenceAssetRepository(db)
    take_repo = TakeRepository(db)

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
            character_intention="investigate", change="discovers",
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

    lifecycle = ReferenceLifecycleService(ref_repo)

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
        shot_repo=shot_repo, plan_repo=plan_repo,
        ref_asset_repo=ref_repo,
        ref_lifecycle_service=lifecycle,
        ref_selector=ReferenceSelector(),
        take_repo=take_repo,
    )
    app.include_router(router)
    client = TestClient(app)

    return {
        "client": client, "db": db, "ref_repo": ref_repo,
        "shot_repo": shot_repo, "take_repo": take_repo,
    }


class TestReadinessChecksCharacterBody:
    """Readiness must check CHARACTER_BODY (what generation uses), not CHARACTER_FACE."""

    def test_not_ready_without_any_refs(self, env):
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is False
        assert data["has_character_ref"] is False
        assert any("character_body" in m.lower() for m in data["missing"])

    def test_not_ready_with_only_face_ref(self, env):
        """CHARACTER_FACE alone does not satisfy readiness."""
        env["ref_repo"].save(_char_face_asset("face-1"))
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["ready"] is False
        assert data["has_character_ref"] is False

    def test_ready_with_approved_body_ref(self, env):
        """An approved CHARACTER_BODY ref makes the project ready."""
        env["ref_repo"].save(_char_body_asset(
            "body-1", status=ReferenceStatus.APPROVED,
        ))
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["ready"] is True
        assert data["has_character_ref"] is True
        assert data["missing"] == []

    def test_not_ready_with_candidate_body_ref(self, env):
        """A candidate (unapproved) CHARACTER_BODY ref is NOT sufficient."""
        env["ref_repo"].save(_char_body_asset("body-cand"))
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["ready"] is False
        assert data["has_character_ref"] is False

    def test_not_ready_with_stale_body_ref(self, env):
        """A stale CHARACTER_BODY ref is NOT sufficient."""
        env["ref_repo"].save(_char_body_asset(
            "body-stale", status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.STALE,
        ))
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["ready"] is False
        assert data["has_character_ref"] is False

    def test_no_environment_ref_field_in_response(self, env):
        """Readiness response no longer includes has_environment_ref."""
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert "has_environment_ref" not in data


class TestReadinessAfterLifecycleActions:
    """Readiness updates correctly after approve/reject/archive lifecycle."""

    def test_approve_makes_ready(self, env):
        env["ref_repo"].save(_char_body_asset("body-2"))
        # Before: not ready (candidate)
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.json()["ready"] is False

        # Approve
        resp = env["client"].post("/references/body-2/approve")
        assert resp.status_code == 200

        # After: ready
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.json()["ready"] is True

    def test_archive_removes_readiness(self, env):
        env["ref_repo"].save(_char_body_asset(
            "body-3", status=ReferenceStatus.APPROVED,
        ))
        # Ready with approved ref
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.json()["ready"] is True

        # Archive it
        resp = env["client"].post("/references/body-3/archive")
        assert resp.status_code == 200

        # No longer ready
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.json()["ready"] is False

    def test_readiness_no_shots(self, env):
        """No shots → not ready, even with refs."""
        env["ref_repo"].save(_char_body_asset(
            "body-4", status=ReferenceStatus.APPROVED,
        ))
        # Delete the shot
        env["shot_repo"].delete_shot("shot-1")
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["ready"] is False
        assert data["shot_count"] == 0
