"""Integration tests for generation readiness with reference management.

Validates that readiness correctly checks for:
- CHARACTER_BODY references (matching what GenerationService R2V path requires)
- ENVIRONMENT references (matching what image-pack production path requires)
and that reference lifecycle actions update readiness accordingly.
"""
from __future__ import annotations

import io
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
from film_director.services.reference_service import (
    IngestOutcome,
    IngestResult,
    ReferenceIngestService,
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


def _env_asset(asset_id, project_id="proj-1",
               status=ReferenceStatus.CANDIDATE,
               source_state=ReferenceSourceState.CURRENT):
    return ReferenceAsset(
        id=asset_id, project_id=project_id,
        kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.USER_UPLOAD,
        managed_path=f"references/{project_id}/{asset_id}/original.png",
        content_sha256="e" * 64, source_provenance="test",
        status=status, source_state=source_state,
        width=1920, height=1080,
        created_at="2026-01-01T00:00:03", updated_at="2026-01-01T00:00:03",
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
    mock_ingest = MagicMock(spec=ReferenceIngestService)

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
        ref_ingest_service=mock_ingest,
        ref_lifecycle_service=lifecycle,
        ref_selector=ReferenceSelector(),
        take_repo=take_repo,
    )
    app.include_router(router)
    client = TestClient(app)

    return {
        "client": client, "db": db, "ref_repo": ref_repo,
        "shot_repo": shot_repo, "take_repo": take_repo,
        "mock_ingest": mock_ingest,
    }


# -----------------------------------------------------------------------
# Character reference readiness
# -----------------------------------------------------------------------

class TestReadinessCharacterBody:
    """Readiness must check CHARACTER_BODY (what GenerationService R2V uses)."""

    def test_not_ready_without_any_refs(self, env):
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is False
        assert data["has_character_ref"] is False
        assert data["has_environment_ref"] is False

    def test_not_ready_with_only_face_ref(self, env):
        """CHARACTER_FACE alone does not satisfy character readiness."""
        env["ref_repo"].save(_char_face_asset("face-1"))
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["has_character_ref"] is False

    def test_char_ready_with_approved_body_ref(self, env):
        """Approved CHARACTER_BODY satisfies the character requirement."""
        env["ref_repo"].save(_char_body_asset(
            "body-1", status=ReferenceStatus.APPROVED,
        ))
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["has_character_ref"] is True
        # Still not fully ready — no env ref
        assert data["ready"] is False

    def test_not_ready_with_candidate_body_ref(self, env):
        env["ref_repo"].save(_char_body_asset("body-cand"))
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.json()["has_character_ref"] is False

    def test_not_ready_with_stale_body_ref(self, env):
        env["ref_repo"].save(_char_body_asset(
            "body-stale", status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.STALE,
        ))
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.json()["has_character_ref"] is False


# -----------------------------------------------------------------------
# Environment reference readiness
# -----------------------------------------------------------------------

class TestReadinessEnvironment:
    """Readiness must check ENVIRONMENT ref (image-pack Picture 2)."""

    def test_env_missing_in_readiness(self, env):
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["has_environment_ref"] is False
        assert any("environment" in m.lower() for m in data["missing"])

    def test_candidate_env_does_not_satisfy(self, env):
        env["ref_repo"].save(_env_asset("env-cand"))
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.json()["has_environment_ref"] is False

    def test_approved_env_satisfies(self, env):
        env["ref_repo"].save(_env_asset(
            "env-ok", status=ReferenceStatus.APPROVED,
        ))
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.json()["has_environment_ref"] is True

    def test_archived_env_does_not_satisfy(self, env):
        env["ref_repo"].save(_env_asset(
            "env-arch", status=ReferenceStatus.ARCHIVED,
        ))
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.json()["has_environment_ref"] is False

    def test_stale_env_does_not_satisfy(self, env):
        env["ref_repo"].save(_env_asset(
            "env-stale", status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.STALE,
        ))
        resp = env["client"].get("/projects/proj-1/readiness")
        assert resp.json()["has_environment_ref"] is False


# -----------------------------------------------------------------------
# Full readiness requires both char + env
# -----------------------------------------------------------------------

class TestReadinessBothRequired:
    """Project is only READY when it has shots + character_body + environment."""

    def test_char_only_not_ready(self, env):
        env["ref_repo"].save(_char_body_asset("body-1", status=ReferenceStatus.APPROVED))
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["has_character_ref"] is True
        assert data["has_environment_ref"] is False
        assert data["ready"] is False

    def test_env_only_not_ready(self, env):
        env["ref_repo"].save(_env_asset("env-1", status=ReferenceStatus.APPROVED))
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["has_character_ref"] is False
        assert data["has_environment_ref"] is True
        assert data["ready"] is False

    def test_both_approved_makes_ready(self, env):
        env["ref_repo"].save(_char_body_asset("body-1", status=ReferenceStatus.APPROVED))
        env["ref_repo"].save(_env_asset("env-1", status=ReferenceStatus.APPROVED))
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["ready"] is True
        assert data["missing"] == []

    def test_no_shots_not_ready(self, env):
        env["ref_repo"].save(_char_body_asset("body-1", status=ReferenceStatus.APPROVED))
        env["ref_repo"].save(_env_asset("env-1", status=ReferenceStatus.APPROVED))
        env["shot_repo"].delete_shot("shot-1")
        resp = env["client"].get("/projects/proj-1/readiness")
        data = resp.json()
        assert data["ready"] is False
        assert data["shot_count"] == 0


# -----------------------------------------------------------------------
# Lifecycle actions update readiness
# -----------------------------------------------------------------------

class TestReadinessAfterLifecycle:
    """Readiness updates after approve/archive lifecycle."""

    def test_approve_env_updates_readiness(self, env):
        env["ref_repo"].save(_char_body_asset("body-1", status=ReferenceStatus.APPROVED))
        env["ref_repo"].save(_env_asset("env-1"))  # candidate
        # Not ready yet
        assert env["client"].get("/projects/proj-1/readiness").json()["ready"] is False

        # Approve env
        resp = env["client"].post("/references/env-1/approve")
        assert resp.status_code == 200

        # Now ready
        assert env["client"].get("/projects/proj-1/readiness").json()["ready"] is True

    def test_archive_env_removes_readiness(self, env):
        env["ref_repo"].save(_char_body_asset("body-1", status=ReferenceStatus.APPROVED))
        env["ref_repo"].save(_env_asset("env-1", status=ReferenceStatus.APPROVED))
        assert env["client"].get("/projects/proj-1/readiness").json()["ready"] is True

        # Archive env
        resp = env["client"].post("/references/env-1/archive")
        assert resp.status_code == 200

        assert env["client"].get("/projects/proj-1/readiness").json()["ready"] is False

    def test_approve_char_updates_readiness(self, env):
        env["ref_repo"].save(_char_body_asset("body-1"))  # candidate
        env["ref_repo"].save(_env_asset("env-1", status=ReferenceStatus.APPROVED))
        assert env["client"].get("/projects/proj-1/readiness").json()["ready"] is False

        resp = env["client"].post("/references/body-1/approve")
        assert resp.status_code == 200

        assert env["client"].get("/projects/proj-1/readiness").json()["ready"] is True


# -----------------------------------------------------------------------
# Environment reference model validation
# -----------------------------------------------------------------------

class TestEnvironmentReferenceModel:
    """ENVIRONMENT kind has correct ownership: project-level, no char/shot."""

    def test_env_asset_no_character_id(self):
        """ENVIRONMENT must not have character_id."""
        with pytest.raises(ValueError, match="environment must not have character_id"):
            ReferenceAsset(
                id="env-bad", project_id="p1", character_id="c1",
                kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.USER_UPLOAD,
                managed_path="test/path.png", content_sha256="a" * 64,
                source_provenance="test", width=100, height=100,
            )

    def test_env_asset_no_shot_id(self):
        """ENVIRONMENT must not have shot_id."""
        with pytest.raises(ValueError, match="environment must not have shot_id"):
            ReferenceAsset(
                id="env-bad", project_id="p1", shot_id="s1",
                kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.USER_UPLOAD,
                managed_path="test/path.png", content_sha256="a" * 64,
                source_provenance="test", width=100, height=100,
            )

    def test_env_asset_valid(self):
        """ENVIRONMENT with only project_id is valid."""
        asset = ReferenceAsset(
            id="env-ok", project_id="p1",
            kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.USER_UPLOAD,
            managed_path="test/path.png", content_sha256="a" * 64,
            source_provenance="test", width=100, height=100,
        )
        assert asset.kind == ReferenceKind.ENVIRONMENT
        assert asset.character_id is None
        assert asset.shot_id is None


# -----------------------------------------------------------------------
# Environment reference API routes
# -----------------------------------------------------------------------

class TestEnvironmentReferenceAPI:
    """Project-level environment reference routes."""

    def test_list_environment_references_empty(self, env):
        resp = env["client"].get("/projects/proj-1/environment-references")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_environment_references_filters_by_kind(self, env):
        env["ref_repo"].save(_char_body_asset("body-1", status=ReferenceStatus.APPROVED))
        env["ref_repo"].save(_env_asset("env-1", status=ReferenceStatus.APPROVED))
        resp = env["client"].get("/projects/proj-1/environment-references")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "env-1"
        assert data[0]["kind"] == "environment"

    def test_lifecycle_on_environment_ref(self, env):
        env["ref_repo"].save(_env_asset("env-2"))

        # Approve
        resp = env["client"].post("/references/env-2/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

        # Pin
        resp = env["client"].post("/references/env-2/pin")
        assert resp.status_code == 200
        assert resp.json()["pinned"] is True

        # Unpin
        resp = env["client"].post("/references/env-2/unpin")
        assert resp.status_code == 200
        assert resp.json()["pinned"] is False

        # Archive
        resp = env["client"].post("/references/env-2/archive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

    def test_register_env_ref_404_project(self, env):
        resp = env["client"].post("/projects/nonexistent/environment-references/register")
        assert resp.status_code in (404, 422)
