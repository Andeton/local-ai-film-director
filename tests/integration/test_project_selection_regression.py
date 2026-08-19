"""Regression test for stale project selection after create.

Verifies that after creating a new project, enrichment targets the new
project — not the previously selected one. Tests the API path that the
UI follows.

The root cause was a missing `await` on selectProject() in createProject(),
allowing the user to interact before the new project was fully selected.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

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
)
from film_director.services.enrichment_service import EnrichmentService


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


@pytest.fixture
def env(tmp_path):
    """Set up two projects: old-proj and new-proj."""
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

    # Create project A (the "old" project)
    with db.connection() as conn:
        project_repo.save_project(ProductionProject(
            id="proj-A", wc_project_id="wc-A", title="Old Project",
            status="active", created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        ), conn=conn)
        seq_repo.save_sequence(Sequence(
            id="seq-A", project_id="proj-A", name="Main", order_index=0,
        ), conn=conn)
        scene_repo.save_scene(Scene(
            id="scene-A", sequence_id="seq-A", wc_scene_id="wc-sA",
            name="Old Scene", location="Old Place", description="Old",
            order_index=0, provenance=_prov(),
        ), conn=conn)

    # Create project B (the "new" project)
    with db.connection() as conn:
        project_repo.save_project(ProductionProject(
            id="proj-B", wc_project_id="wc-B", title="New Project",
            status="active", created_at="2026-01-02", updated_at="2026-01-02",
            provenance=_prov(),
        ), conn=conn)
        seq_repo.save_sequence(Sequence(
            id="seq-B", project_id="proj-B", name="Main", order_index=0,
        ), conn=conn)
        scene_repo.save_scene(Scene(
            id="scene-B", sequence_id="seq-B", wc_scene_id="wc-sB",
            name="New Scene", location="New Place", description="New",
            order_index=0, provenance=_prov(),
        ), conn=conn)

    # Mock enrichment to track which project ID was enriched
    enrichment_calls = []
    mock_enrichment = MagicMock(spec=EnrichmentService)

    def _track_enrich(project_id):
        from film_director.services.enrichment_service import EnrichmentResult
        enrichment_calls.append(project_id)
        return EnrichmentResult(
            project_id=project_id, beats_created=0,
            shots_created=0, plans_created=0,
        )

    mock_enrichment.enrich_project.side_effect = _track_enrich

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
        enrichment_service=mock_enrichment,
        beat_repo=beat_repo, shot_repo=shot_repo,
        plan_repo=plan_repo,
    )
    app.include_router(router)
    client = TestClient(app)

    return {
        "client": client,
        "enrichment_calls": enrichment_calls,
    }


class TestEnrichmentTargetsCorrectProject:
    """After creating project B, enrichment must target B, not A."""

    def test_enrich_project_b(self, env):
        """Simulate the UI flow: select A, create B, enrich B."""
        client = env["client"]
        calls = env["enrichment_calls"]

        # Step 1: "Select" project A — fetch its details
        resp = client.get("/projects/proj-A")
        assert resp.status_code == 200
        assert resp.json()["id"] == "proj-A"

        # Step 2: Enrich project A (verifies A exists)
        resp = client.post("/projects/proj-A/enrich")
        assert resp.status_code == 200
        assert calls[-1] == "proj-A"

        # Step 3: "New project created" — now enrich B
        resp = client.post("/projects/proj-B/enrich")
        assert resp.status_code == 200
        assert calls[-1] == "proj-B"

    def test_enrich_url_determines_target(self, env):
        """The project ID in the URL is the sole target — no ambient state."""
        client = env["client"]
        calls = env["enrichment_calls"]

        # Enrich B directly without touching A
        resp = client.post("/projects/proj-B/enrich")
        assert resp.status_code == 200
        assert calls == ["proj-B"]

        # Enrich A
        resp = client.post("/projects/proj-A/enrich")
        assert resp.status_code == 200
        assert calls == ["proj-B", "proj-A"]

    def test_enrich_nonexistent_project(self, env):
        """Enrichment on a missing project returns empty result, not crash."""
        client = env["client"]
        resp = client.post("/projects/proj-MISSING/enrich")
        assert resp.status_code == 200
        data = resp.json()
        assert data["beats_created"] == 0
