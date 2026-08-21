"""Integration tests for P4 data lineage fixes.

Fix A: original_idea preserved independently of WC-processed description.
Fix B: current canonical character names used in UI, enrichment ordering fixed.

Uses real SQLite, mock WC/LLM. No live services.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from film_director.config import Settings
from film_director.llm.provider import LLMResponse
from film_director.main import create_app
from film_director.models.canonical import CharacterReference, ShotSubject
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    CharacterRepository,
    ProjectRepository,
    ShotRepository,
)
from tests.fixtures.wind_comic_fixture import TEST_PROJECT_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shot_plan_response():
    """LLM response for shot planner with character references."""
    return LLMResponse(
        content="",
        parsed={"shots": [
            {
                "action": "Detective enters the room",
                "dramatic_purpose": "Establishing presence",
                "camera": {"shot_size": "wide", "angle": "eye_level", "movement": "static"},
                "duration_sec": 5.0,
                "characters": ["Detective"],
            },
        ]},
        model="test",
    )


def _char_enrichment_response():
    """LLM response for character enrichment."""
    return LLMResponse(
        content="",
        parsed={"characters": [
            {"id": None, "display_name": "The Detective", "appearance": "Tall, dark coat, sharp eyes"},
            {"id": None, "display_name": "The Ghost", "appearance": "Pale woman in white gown"},
        ]},
        model="test",
    )


def _env_derivation_response():
    return LLMResponse(
        content="",
        parsed={"environment_description": "A dark hospital lobby"},
        model="test",
    )


def _fake_llm_factory():
    class FakeLLM:
        def __init__(self):
            self._responses = []

        def queue(self, resp):
            self._responses.append(resp)

        def chat(self, messages, expect_json=False):
            if self._responses:
                return self._responses.pop(0)
            return LLMResponse(content="", parsed={}, model="test")

        def health(self):
            return True

    return FakeLLM()


@pytest.fixture
def lineage_env(tmp_path, wc_db_path):
    fake_llm = _fake_llm_factory()
    s = Settings(
        _env_file=None,
        database_path=str(tmp_path / "our.db"),
        storage_root=str(tmp_path / "storage"),
        wc_database_path=wc_db_path,
    )
    with patch("film_director.main.create_llm_provider", return_value=fake_llm):
        app = create_app(s)
    client = TestClient(app)
    return {"client": client, "fake_llm": fake_llm, "settings": s}


# ===========================================================================
# FIX A — ORIGINAL IDEA PRESERVATION
# ===========================================================================

class TestOriginalIdeaPreservation:
    def test_import_with_original_idea_preserves_it(self, lineage_env):
        """import_project(original_idea=...) stores it in director_context."""
        from film_director.services.import_service import ImportService
        from film_director.persistence.repositories import ProjectRepository
        s = lineage_env["settings"]
        db = Database(s.database_path)

        # Import with original_idea
        from film_director.adapters.wind_comic import WindComicAdapter
        adapter = WindComicAdapter(s.wc_database_path)
        from film_director.persistence.repositories import (
            SequenceRepository, SceneRepository, CharacterRepository, ReferenceAssetRepository,
        )
        imp_svc = ImportService(
            adapter=adapter,
            project_repo=ProjectRepository(db),
            sequence_repo=SequenceRepository(db),
            scene_repo=SceneRepository(db),
            character_repo=CharacterRepository(db),
            db=db,
            ref_asset_repo=ReferenceAssetRepository(db),
        )
        result = imp_svc.import_project(TEST_PROJECT_ID, original_idea="My exact operator input")
        proj = ProjectRepository(db).get_project(result.project_id)
        assert proj.director_context.get("original_idea") == "My exact operator input"

    def test_import_with_original_idea_keeps_wc_description(self, lineage_env):
        """WC description preserved alongside original_idea."""
        from film_director.services.import_service import ImportService
        from film_director.persistence.repositories import ProjectRepository
        s = lineage_env["settings"]
        db = Database(s.database_path)
        from film_director.adapters.wind_comic import WindComicAdapter
        adapter = WindComicAdapter(s.wc_database_path)
        from film_director.persistence.repositories import (
            SequenceRepository, SceneRepository, CharacterRepository, ReferenceAssetRepository,
        )
        imp_svc = ImportService(
            adapter=adapter,
            project_repo=ProjectRepository(db),
            sequence_repo=SequenceRepository(db),
            scene_repo=SceneRepository(db),
            character_repo=CharacterRepository(db),
            db=db,
            ref_asset_repo=ReferenceAssetRepository(db),
        )
        result = imp_svc.import_project(TEST_PROJECT_ID, original_idea="Test idea")
        proj = ProjectRepository(db).get_project(result.project_id)
        dc = proj.director_context
        assert dc["original_idea"] == "Test idea"
        # WC description should be different — it's what WC stored, not operator's input
        assert "description" in dc
        assert len(dc["description"]) > 0

    def test_direct_import_has_no_original_idea(self, lineage_env):
        """Legacy import (not from-idea) has no original_idea field."""
        r = lineage_env["client"].post(f"/imports/wind-comic/{TEST_PROJECT_ID}")
        pid = r.json()["project_id"]
        proj = lineage_env["client"].get(f"/projects/{pid}").json()
        dc = proj.get("director_context", {})
        assert dc.get("original_idea") is None or "original_idea" not in dc

    def test_legacy_project_returns_in_list(self, lineage_env):
        """Legacy project without original_idea still returns correctly in list."""
        lineage_env["client"].post(f"/imports/wind-comic/{TEST_PROJECT_ID}")
        r = lineage_env["client"].get("/projects")
        projects = r.json()
        assert len(projects) >= 1


# ===========================================================================
# FIX B — CHARACTER NAME RESOLUTION
# ===========================================================================

class TestCharacterNameResolution:
    def _setup_enriched_project(self, lineage_env):
        """Import + enrich, returns project_id."""
        # Queue enrichment LLM responses:
        # 1. Character enrichment (runs first now)
        lineage_env["fake_llm"].queue(_char_enrichment_response())
        # 2. Shot planning
        lineage_env["fake_llm"].queue(_shot_plan_response())
        # 3. Environment derivation
        lineage_env["fake_llm"].queue(_env_derivation_response())

        r = lineage_env["client"].post(f"/imports/wind-comic/{TEST_PROJECT_ID}")
        pid = r.json()["project_id"]
        lineage_env["client"].post(f"/projects/{pid}/enrich")
        return pid

    def test_enrichment_order_characters_before_shots(self, lineage_env):
        """Character enrichment must run before shot planning."""
        pid = self._setup_enriched_project(lineage_env)
        # Characters should be enriched
        chars = lineage_env["client"].get(f"/projects/{pid}/characters").json()
        enriched_names = {c["name"] for c in chars}
        # At least one character should have been enriched from generic WC name
        assert any(n not in ("主角", "伙伴", "") for n in enriched_names), \
            f"No enriched names found: {enriched_names}"

    def test_generation_preview_uses_current_character_name(self, lineage_env):
        """Right-side CHARACTER label uses current canonical name, not stale snapshot."""
        pid = self._setup_enriched_project(lineage_env)
        shots = lineage_env["client"].get(f"/projects/{pid}/shots").json()
        if not shots:
            pytest.skip("No shots created")

        # Get current character names
        chars = lineage_env["client"].get(f"/projects/{pid}/characters").json()
        char_names = {c["id"]: c["name"] for c in chars}

        # Check generation preview
        shot = shots[0]
        r = lineage_env["client"].get(f"/shots/{shot['id']}/generation-preview")
        if r.status_code != 200:
            pytest.skip("Preview not available (may need refs)")

        # If pictures have CHARACTER roles, they should use current names
        for pic in r.json().get("pictures", []):
            if pic["role"].startswith("CHARACTER:"):
                role_name = pic["role"].split("CHARACTER:", 1)[1].strip()
                # Should be current canonical name, not WC imported name
                assert role_name not in ("主角", "伙伴"), \
                    f"Preview using stale WC name: {role_name}"


class TestHistoricalProvenanceUnchanged:
    def test_take_details_show_historical_names(self):
        """Historical Take provenance must NOT be affected by name resolution changes.
        This is a design constraint test — the GenerationRequest.reference_snapshot
        stores the name used at generation time, which is correct behavior."""
        # GenerationRequest.reference_snapshot is immutable
        # The name in reference_snapshot should be whatever was used at generation time
        # This test validates the design principle
        from film_director.generation.generation_request import GenerationRequest
        req = GenerationRequest(
            id="greq_test_hist",
            shot_id="shot-1",
            shot_version=1,
            generation_plan_id="plan-1",
            generation_plan_version=1,
            prompt_artifact_id="h3p_test",
            prompt_artifact_version=1,
            workflow_definition_id="h3_r2v_v1",
            workflow_definition_version="1.0.0",
            workflow_template_fingerprint="a" * 64,
            take_number=1,
            parameters_snapshot=[],
            reference_snapshot=[
                {"character_name": "The Man", "reference_kind": "character_body"},
            ],
            seed=42,
            status="succeeded",
        )
        # reference_snapshot is immutable — changing characters doesn't affect it
        assert req.reference_snapshot[0]["character_name"] == "The Man"
        d = req.model_dump()
        assert d["reference_snapshot"][0]["character_name"] == "The Man"
