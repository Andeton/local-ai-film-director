"""Tests for preview subject binding — character refs must match shot subjects.

Regression test for the bug where the preview showed The Man (first project
character ref) for Shot 3, but Shot 3's subject was The Woman.
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
    Beat, CameraIntent, CharacterReference, GenerationPlan,
    ProductionProject, ReferenceRequirements, Scene, Sequence,
    ShotSpecificationV1, ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.models.reference import (
    ReferenceAsset, ReferenceKind, ReferenceSource,
    ReferenceSourceState, ReferenceStatus,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository, CharacterRepository, GenerationPlanRepository,
    ProjectRepository, ReferenceAssetRepository, SceneRepository,
    SequenceRepository, ShotRepository, TakeRepository,
)


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
            name="S1", location="Apartment", description="Dark apartment",
            order_index=0, provenance=_prov(),
        ), conn=conn)

        # Two characters
        char_repo.save_character(CharacterReference(
            id="char-man", project_id="proj-1", wc_character_id="wc-c1",
            name="The Man", description="", appearance="dark hair, 40s",
            provenance=_prov(),
        ), conn=conn)
        char_repo.save_character(CharacterReference(
            id="char-woman", project_id="proj-1", wc_character_id="wc-c2",
            name="The Woman", description="", appearance="blonde, 30s",
            provenance=_prov(),
        ), conn=conn)

        # Shot 1: only The Man
        beat_repo.save_beat(Beat(
            id="beat-1", scene_id="scene-1", dramatic_action="sits",
            character_intention="wait", change="alert",
            order_index=0, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        shot_repo.save_shot(ShotSpecificationV1(
            id="shot-1", beat_id="beat-1", dramatic_purpose="establish",
            subjects=[ShotSubject(character_id="char-man", name="The Man", ref_images=[])],
            action="Man sits at table", camera=CameraIntent(shot_size="wide"),
            duration_sec=5.0, order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        plan_repo.save_plan(GenerationPlan(
            id="plan-1", shot_id="shot-1", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, status="draft", version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)

        # Shot 3: only The Woman (POV from man)
        beat_repo.save_beat(Beat(
            id="beat-3", scene_id="scene-1", dramatic_action="woman enters",
            character_intention="approach", change="reveal",
            order_index=2, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        shot_repo.save_shot(ShotSpecificationV1(
            id="shot-3", beat_id="beat-3", dramatic_purpose="suspense",
            subjects=[ShotSubject(character_id="char-woman", name="The Woman", ref_images=[])],
            action="From the man's POV, the woman enters the hallway",
            camera=CameraIntent(shot_size="medium_wide"),
            duration_sec=5.0, order_index=2, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        plan_repo.save_plan(GenerationPlan(
            id="plan-3", shot_id="shot-3", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, status="draft", version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)

        # Shot 4: both characters
        beat_repo.save_beat(Beat(
            id="beat-4", scene_id="scene-1", dramatic_action="face each other",
            character_intention="", change="",
            order_index=3, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        shot_repo.save_shot(ShotSpecificationV1(
            id="shot-4", beat_id="beat-4", dramatic_purpose="confrontation",
            subjects=[
                ShotSubject(character_id="char-man", name="The Man", ref_images=[]),
                ShotSubject(character_id="char-woman", name="The Woman", ref_images=[]),
            ],
            action="Both face each other at the table",
            camera=CameraIntent(shot_size="close_up"),
            duration_sec=5.0, order_index=3, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        plan_repo.save_plan(GenerationPlan(
            id="plan-4", shot_id="shot-4", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, status="draft", version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)

        # Approved character refs
        ref_repo.save(ReferenceAsset(
            id="ref-man", project_id="proj-1", character_id="char-man",
            kind=ReferenceKind.CHARACTER_BODY, source=ReferenceSource.GENERATED,
            managed_path="references/proj-1/ref-man/original.png",
            content_sha256="a" * 64, source_provenance="test",
            status=ReferenceStatus.APPROVED, source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        ref_repo.save(ReferenceAsset(
            id="ref-woman", project_id="proj-1", character_id="char-woman",
            kind=ReferenceKind.CHARACTER_BODY, source=ReferenceSource.GENERATED,
            managed_path="references/proj-1/ref-woman/original.png",
            content_sha256="b" * 64, source_provenance="test",
            status=ReferenceStatus.APPROVED, source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        ref_repo.save(ReferenceAsset(
            id="ref-env", project_id="proj-1",
            kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
            managed_path="references/proj-1/ref-env/original.png",
            content_sha256="e" * 64, source_provenance="test",
            status=ReferenceStatus.APPROVED, source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)

    mock_gen_svc = MagicMock()
    mock_gen_svc._prompt_repo = MagicMock()
    mock_gen_svc._prompt_repo.get_current_prompt.return_value = None
    mock_gen_svc._db = db

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
        ref_asset_repo=ref_repo, take_repo=take_repo,
        generation_service=mock_gen_svc,
    )
    app.include_router(router)
    return {"client": TestClient(app)}


class TestPreviewSubjectBinding:
    """Preview must show the shot's actual character subjects, not just the first project character."""

    def test_shot1_shows_the_man(self, env):
        resp = env["client"].get("/shots/shot-1/generation-preview")
        assert resp.status_code == 200
        data = resp.json()
        pics = data["pictures"]
        char_pics = [p for p in pics if "CHARACTER" in p["role"]]
        assert len(char_pics) == 1
        assert char_pics[0]["reference_id"] == "ref-man"
        assert "The Man" in char_pics[0]["role"]

    def test_shot3_shows_the_woman_not_the_man(self, env):
        """Shot 3 POV from man → only The Woman visible → preview shows The Woman."""
        resp = env["client"].get("/shots/shot-3/generation-preview")
        assert resp.status_code == 200
        data = resp.json()
        pics = data["pictures"]
        char_pics = [p for p in pics if "CHARACTER" in p["role"]]
        assert len(char_pics) == 1
        assert char_pics[0]["reference_id"] == "ref-woman"
        assert "The Woman" in char_pics[0]["role"]

    def test_shot4_shows_both_characters(self, env):
        """Shot 4: both characters visible → preview shows both."""
        resp = env["client"].get("/shots/shot-4/generation-preview")
        assert resp.status_code == 200
        data = resp.json()
        pics = data["pictures"]
        char_pics = [p for p in pics if "CHARACTER" in p["role"]]
        assert len(char_pics) == 2
        ref_ids = {p["reference_id"] for p in char_pics}
        assert "ref-man" in ref_ids
        assert "ref-woman" in ref_ids

    def test_preview_picture_order(self, env):
        """Pictures follow: char1, env, (continuity), char2+."""
        resp = env["client"].get("/shots/shot-4/generation-preview")
        data = resp.json()
        pics = data["pictures"]
        # Shot 4 is at index 3 (head=shot-1), so it's downstream
        # Expected order: char1(1), env(2), continuity(3), char2(4)
        assert pics[0]["role"].startswith("CHARACTER")
        assert pics[1]["role"] == "ENVIRONMENT"

    def test_env_always_present(self, env):
        resp = env["client"].get("/shots/shot-1/generation-preview")
        data = resp.json()
        env_pics = [p for p in data["pictures"] if p["role"] == "ENVIRONMENT"]
        assert len(env_pics) == 1
        assert env_pics[0]["reference_id"] == "ref-env"
