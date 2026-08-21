"""Tests for Location Slice 5 — multi-Location planning/enrichment.

Covers LLM-based location identification, deterministic fallback,
validation, deduplication, and integration with enrichment pipeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from film_director.enrichment.shot_planner import (
    ShotPlanner,
    _validate_location_plan,
    _fallback_location_plan,
    _normalize_location_string,
)
from film_director.models.canonical import (
    Beat, CameraIntent, CharacterReference, GenerationPlan, Location,
    ProductionProject, ReferenceRequirements, Scene, Sequence,
    ShotSpecificationV1, ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository, CharacterRepository, GenerationPlanRepository,
    LocationRepository, ProjectRepository, SceneRepository,
    SequenceRepository, ShotRepository,
)
from film_director.services.enrichment_service import EnrichmentService

NOW = "2026-08-21T00:00:00+00:00"
SHA = "a" * 64


def _prov(aid="wc_1"):
    return Provenance(source_system="wind_comic", source_project_id="wc_p",
                      source_asset_id=aid, source_asset_version=1,
                      imported_at=NOW, source_hash=SHA)


def _scene(sid, seq_id="seq_1", location="", name=""):
    return Scene(id=sid, sequence_id=seq_id, wc_scene_id=f"wc_{sid}",
                 name=name or f"Scene {sid}", location=location,
                 description="", order_index=0, provenance=_prov(f"wc_{sid}"))


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


# ---------------------------------------------------------------------------
# _normalize_location_string
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_basic(self):
        assert _normalize_location_string("Grandpa's Apartment") == "grandpas apartment"

    def test_strips_punctuation(self):
        assert _normalize_location_string("the office (lobby)") == "the office lobby"

    def test_empty(self):
        assert _normalize_location_string("") == ""

    def test_whitespace(self):
        assert _normalize_location_string("  subway   car  ") == "subway car"


# ---------------------------------------------------------------------------
# _validate_location_plan
# ---------------------------------------------------------------------------

class TestValidateLocationPlan:
    def _scenes(self):
        return [_scene("sc_1"), _scene("sc_2"), _scene("sc_3")]

    def test_valid_plan(self):
        parsed = {"locations": [
            {"name": "Kitchen", "description": "A kitchen", "scene_ids": ["sc_1", "sc_2"]},
            {"name": "Street", "description": "A street", "scene_ids": ["sc_3"]},
        ]}
        result, err = _validate_location_plan(parsed, self._scenes())
        assert err is None
        assert len(result) == 2
        assert result[0]["scene_ids"] == ["sc_1", "sc_2"]

    def test_missing_key(self):
        _, err = _validate_location_plan({}, self._scenes())
        assert err is not None
        assert "missing" in err.lower()

    def test_empty_list(self):
        _, err = _validate_location_plan({"locations": []}, self._scenes())
        assert err is not None

    def test_invalid_scene_id_skipped(self):
        parsed = {"locations": [
            {"name": "K", "description": "", "scene_ids": ["sc_1", "FAKE_ID", "sc_2"]},
        ]}
        result, err = _validate_location_plan(parsed, self._scenes())
        assert err is None
        assert "FAKE_ID" not in result[0]["scene_ids"]
        # sc_3 unassigned → appended to first location
        assert "sc_3" in result[0]["scene_ids"]

    def test_duplicate_scene_assignment_deduped(self):
        parsed = {"locations": [
            {"name": "A", "description": "", "scene_ids": ["sc_1"]},
            {"name": "B", "description": "", "scene_ids": ["sc_1", "sc_2"]},
        ]}
        result, err = _validate_location_plan(parsed, self._scenes())
        assert err is None
        # sc_1 assigned only to first location
        all_assigned = [sid for loc in result for sid in loc["scene_ids"]]
        assert all_assigned.count("sc_1") == 1

    def test_empty_name_rejected(self):
        parsed = {"locations": [
            {"name": "", "description": "", "scene_ids": ["sc_1"]},
        ]}
        _, err = _validate_location_plan(parsed, self._scenes())
        assert err is not None

    def test_unassigned_scenes_appended(self):
        parsed = {"locations": [
            {"name": "K", "description": "", "scene_ids": ["sc_1"]},
        ]}
        result, err = _validate_location_plan(parsed, [_scene("sc_1"), _scene("sc_2")])
        assert err is None
        assert "sc_2" in result[0]["scene_ids"]


# ---------------------------------------------------------------------------
# _fallback_location_plan
# ---------------------------------------------------------------------------

class TestFallback:
    def test_groups_identical_strings(self):
        scenes = [_scene("sc_1", location="apartment"),
                  _scene("sc_2", location="subway"),
                  _scene("sc_3", location="apartment")]
        result = _fallback_location_plan(scenes)
        assert len(result) == 2
        apartment = next(r for r in result if r["name"] == "apartment")
        assert set(apartment["scene_ids"]) == {"sc_1", "sc_3"}

    def test_different_strings_separate(self):
        scenes = [_scene("sc_1", location="kitchen"),
                  _scene("sc_2", location="rooftop")]
        result = _fallback_location_plan(scenes)
        assert len(result) == 2

    def test_empty_locations_grouped(self):
        scenes = [_scene("sc_1", location=""), _scene("sc_2", location="")]
        result = _fallback_location_plan(scenes)
        assert len(result) == 1
        assert result[0]["name"] == "Main Location"

    def test_single_scene(self):
        result = _fallback_location_plan([_scene("sc_1", location="office")])
        assert len(result) == 1
        assert result[0]["scene_ids"] == ["sc_1"]

    def test_no_description_in_fallback(self):
        result = _fallback_location_plan([_scene("sc_1", location="x")])
        assert result[0]["description"] == ""


# ---------------------------------------------------------------------------
# ShotPlanner.plan_locations — LLM integration
# ---------------------------------------------------------------------------

class TestPlanLocationsLLM:
    def _mock_planner(self, llm_response):
        llm = MagicMock()
        resp = MagicMock()
        resp.parsed = llm_response
        resp.content = json.dumps(llm_response)
        llm.chat = MagicMock(return_value=resp)
        return ShotPlanner(llm)

    def test_three_locations(self):
        planner = self._mock_planner({"locations": [
            {"name": "Apartment", "description": "A dim apartment", "scene_ids": ["sc_1", "sc_4"]},
            {"name": "Street", "description": "Manhattan street", "scene_ids": ["sc_2"]},
            {"name": "Subway", "description": "Underground platform", "scene_ids": ["sc_3"]},
        ]})
        scenes = [_scene("sc_1", location="apartment"), _scene("sc_2", location="street"),
                  _scene("sc_3", location="subway"), _scene("sc_4", location="apartment")]
        result = planner.plan_locations(scenes, "A story about an old man")
        assert len(result) == 3
        apt = next(r for r in result if r["name"] == "Apartment")
        assert set(apt["scene_ids"]) == {"sc_1", "sc_4"}

    def test_repeated_location_reused(self):
        planner = self._mock_planner({"locations": [
            {"name": "The Kitchen", "description": "Kitchen desc", "scene_ids": ["sc_1", "sc_3"]},
            {"name": "Park", "description": "Park desc", "scene_ids": ["sc_2"]},
        ]})
        scenes = [_scene("sc_1"), _scene("sc_2"), _scene("sc_3")]
        result = planner.plan_locations(scenes, "story")
        kitchen = next(r for r in result if r["name"] == "The Kitchen")
        assert set(kitchen["scene_ids"]) == {"sc_1", "sc_3"}

    def test_llm_failure_falls_back(self):
        llm = MagicMock()
        llm.chat = MagicMock(side_effect=Exception("LLM error"))
        planner = ShotPlanner(llm)
        scenes = [_scene("sc_1", location="apt"), _scene("sc_2", location="street")]
        result = planner.plan_locations(scenes, "story")
        assert len(result) == 2  # fallback groups by string

    def test_malformed_response_falls_back(self):
        planner = self._mock_planner({"bad": "data"})
        # repair also returns bad data
        scenes = [_scene("sc_1", location="x")]
        result = planner.plan_locations(scenes, "story")
        assert len(result) == 1  # fallback

    def test_no_scenes_returns_empty(self):
        planner = self._mock_planner({})
        assert planner.plan_locations([], "story") == []

    def test_no_h3_concepts_in_description(self):
        planner = self._mock_planner({"locations": [
            {"name": "K", "description": "Kitchen set", "scene_ids": ["sc_1"]},
        ]})
        result = planner.plan_locations([_scene("sc_1")], "story")
        desc = result[0]["description"]
        assert "picture" not in desc.lower()
        assert "h3" not in desc.lower()
        assert "slot" not in desc.lower()


# ---------------------------------------------------------------------------
# EnrichmentService integration — multi-Location planning
# ---------------------------------------------------------------------------

def _setup_enrichment_db(db, n_scenes=4, locations=None):
    """Create project with n_scenes. locations is a list of location strings."""
    if locations is None:
        locations = ["apartment", "subway", "office", "apartment"][:n_scenes]
    ProjectRepository(db).save_project(ProductionProject(
        id="proj_1", wc_project_id="wc_proj_1", title="Test",
        director_context={"description": "A story about an old man in NYC"},
        created_at=NOW, updated_at=NOW, provenance=_prov("wc_proj_1")))
    SequenceRepository(db).save_sequence(Sequence(
        id="seq_1", project_id="proj_1", name="Main", order_index=0))
    scene_repo = SceneRepository(db)
    for i in range(n_scenes):
        loc_str = locations[i] if i < len(locations) else ""
        scene_repo.save_scene(Scene(
            id=f"sc_{i}", sequence_id="seq_1", wc_scene_id=f"wc_sc_{i}",
            name=f"Scene {i}", location=loc_str, description="",
            order_index=i, provenance=_prov(f"wc_sc_{i}")))
    CharacterRepository(db).save_character(CharacterReference(
        id="char_1", project_id="proj_1", wc_character_id="wc_c1",
        name="Old Man", description="", appearance="elderly man",
        provenance=_prov("wc_c1")))


def _make_enrichment_service(db, llm_location_response=None, llm_shot_response=None):
    """Create EnrichmentService with mocked LLM."""
    from film_director.enrichment.beat_enricher import BeatEnricher
    from film_director.enrichment.coverage_planner import CoveragePlanner
    from film_director.enrichment.shot_spec_builder import ShotSpecBuilder
    from film_director.enrichment.strategy_selector import StrategySelector
    from film_director.enrichment.stale_propagator import StalePropagator

    llm = MagicMock()
    call_count = [0]
    def _fake_chat(messages, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        # Determine what's being asked by system prompt content
        system_msg = messages[0]["content"] if messages else ""
        if "identifying distinct physical location" in system_msg.lower():
            resp.parsed = llm_location_response or {"locations": []}
            resp.content = json.dumps(resp.parsed)
        elif "character visual description" in system_msg.lower():
            resp.parsed = {"characters": []}
            resp.content = json.dumps(resp.parsed)
        elif "physical set/location" in system_msg.lower() or "physical space" in system_msg.lower():
            resp.parsed = {"environment_description": "A dimly lit apartment"}
            resp.content = json.dumps(resp.parsed)
        else:
            # Default: return valid shot plan (covers plan_scene + repair calls)
            resp.parsed = llm_shot_response or {"shots": [
                {"action": "Man enters the room", "dramatic_purpose": "Establish the scene",
                 "shot_size": "wide", "angle": "eye_level", "movement": "static",
                 "characters": ["Old Man"], "duration_sec": 5.0},
            ]}
            resp.content = json.dumps(resp.parsed)
        return resp

    llm.chat = _fake_chat

    shot_planner = ShotPlanner(llm)

    return EnrichmentService(
        db=db,
        project_repo=ProjectRepository(db),
        sequence_repo=SequenceRepository(db),
        scene_repo=SceneRepository(db),
        character_repo=CharacterRepository(db),
        beat_repo=BeatRepository(db),
        shot_repo=ShotRepository(db),
        plan_repo=GenerationPlanRepository(db),
        adapter=MagicMock(read_project_bundle=MagicMock(side_effect=Exception("no WC"))),
        import_service=MagicMock(),
        beat_enricher=BeatEnricher(llm),
        coverage_planner=CoveragePlanner(llm),
        shot_spec_builder=ShotSpecBuilder(),
        strategy_selector=StrategySelector(),
        stale_propagator=StalePropagator(
            db, BeatRepository(db), ShotRepository(db),
            GenerationPlanRepository(db), SequenceRepository(db), SceneRepository(db),
        ),
        shot_planner=shot_planner,
        location_repo=LocationRepository(db),
    )


class TestEnrichmentLocations:
    def test_creates_three_locations_from_four_scenes(self, db):
        _setup_enrichment_db(db, 4, ["apartment", "subway", "office", "apartment"])
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "Apartment", "description": "A dim NYC apartment", "scene_ids": ["sc_0", "sc_3"]},
            {"name": "Subway", "description": "Underground platform", "scene_ids": ["sc_1"]},
            {"name": "Office", "description": "Corporate office", "scene_ids": ["sc_2"]},
        ]})
        svc.enrich_project("proj_1")
        locs = LocationRepository(db).list_by_project("proj_1")
        assert len(locs) == 3

    def test_scenes_assigned_to_locations(self, db):
        _setup_enrichment_db(db, 4, ["apt", "street", "subway", "apt"])
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "Apartment", "description": "Apt", "scene_ids": ["sc_0", "sc_3"]},
            {"name": "Street", "description": "St", "scene_ids": ["sc_1"]},
            {"name": "Subway", "description": "Sub", "scene_ids": ["sc_2"]},
        ]})
        svc.enrich_project("proj_1")
        scene_repo = SceneRepository(db)
        sc0 = scene_repo.get_scene("sc_0")
        sc3 = scene_repo.get_scene("sc_3")
        assert sc0.location_id is not None
        assert sc0.location_id == sc3.location_id  # same Location

    def test_scene_location_text_preserved(self, db):
        _setup_enrichment_db(db, 2, ["apartment", "subway"])
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "A", "description": "d", "scene_ids": ["sc_0"]},
            {"name": "B", "description": "d", "scene_ids": ["sc_1"]},
        ]})
        svc.enrich_project("proj_1")
        sc = SceneRepository(db).get_scene("sc_0")
        assert sc.location == "apartment"  # original WC string

    def test_each_location_gets_own_description(self, db):
        _setup_enrichment_db(db, 2, ["x", "y"])
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "Kitchen", "description": "A small kitchen", "scene_ids": ["sc_0"]},
            {"name": "Rooftop", "description": "An open rooftop", "scene_ids": ["sc_1"]},
        ]})
        svc.enrich_project("proj_1")
        locs = LocationRepository(db).list_by_project("proj_1")
        descs = {loc.name: loc.description for loc in locs}
        assert descs["Kitchen"] == "A small kitchen"
        assert descs["Rooftop"] == "An open rooftop"

    def test_single_scene_creates_one_location(self, db):
        _setup_enrichment_db(db, 1, ["office"])
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "Office", "description": "A corporate office", "scene_ids": ["sc_0"]},
        ]})
        svc.enrich_project("proj_1")
        assert len(LocationRepository(db).list_by_project("proj_1")) == 1

    def test_llm_failure_uses_fallback(self, db):
        _setup_enrichment_db(db, 3, ["apartment", "subway", "apartment"])
        # LLM returns garbage → fallback
        svc = _make_enrichment_service(db, llm_location_response={"bad": "data"})
        svc.enrich_project("proj_1")
        locs = LocationRepository(db).list_by_project("proj_1")
        # Fallback groups "apartment" together
        assert len(locs) == 2

    def test_retry_does_not_duplicate(self, db):
        _setup_enrichment_db(db, 2, ["apt", "street"])
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "Apt", "description": "d", "scene_ids": ["sc_0"]},
            {"name": "Street", "description": "d", "scene_ids": ["sc_1"]},
        ]})
        svc.enrich_project("proj_1")
        svc.enrich_project("proj_1")  # retry
        locs = LocationRepository(db).list_by_project("proj_1")
        assert len(locs) == 2  # not duplicated

    def test_initial_assignment_does_not_outdate_shots(self, db):
        _setup_enrichment_db(db, 1, ["apt"])
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "Apt", "description": "d", "scene_ids": ["sc_0"]},
        ]})
        svc.enrich_project("proj_1")
        shots = ShotRepository(db).get_current_shots_by_project("proj_1")
        for s in shots:
            assert s.status != "outdated"

    def test_legacy_project_unchanged(self, db):
        """Existing legacy project with one migrated Location is not re-planned."""
        _setup_enrichment_db(db, 1, ["apt"])
        loc_repo = LocationRepository(db)
        loc_repo.save(Location(
            id="loc_legacy", project_id="proj_1", name="Legacy",
            description="Migrated", source="llm", version=1,
            created_at=NOW, updated_at=NOW))
        SceneRepository(db).save_scene(Scene(
            id="sc_0", sequence_id="seq_1", wc_scene_id="wc_sc_0",
            name="Scene 0", location="apt", description="",
            location_id="loc_legacy", order_index=0, provenance=_prov("wc_sc_0")))
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "New", "description": "d", "scene_ids": ["sc_0"]},
        ]})
        svc.enrich_project("proj_1")
        locs = loc_repo.list_by_project("proj_1")
        assert len(locs) == 1
        assert locs[0].id == "loc_legacy"  # not replaced

    def test_no_evidence_no_fabricated_description(self, db):
        _setup_enrichment_db(db, 2, ["", ""])
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "Main", "description": "", "scene_ids": ["sc_0", "sc_1"]},
        ]})
        svc.enrich_project("proj_1")
        locs = LocationRepository(db).list_by_project("proj_1")
        # Source should reflect WC origin when description is empty
        assert len(locs) == 1

    def test_foreign_scene_id_rejected(self):
        """Validation rejects scene IDs not in the scene list."""
        scenes = [_scene("sc_1")]
        parsed = {"locations": [
            {"name": "K", "description": "", "scene_ids": ["sc_1", "sc_FOREIGN"]},
        ]}
        result, err = _validate_location_plan(parsed, scenes)
        assert err is None  # foreign IDs silently skipped
        assert "sc_FOREIGN" not in result[0]["scene_ids"]

    def test_location_source_llm_when_described(self, db):
        _setup_enrichment_db(db, 1, ["apt"])
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "Apartment", "description": "A nice apartment", "scene_ids": ["sc_0"]},
        ]})
        svc.enrich_project("proj_1")
        locs = LocationRepository(db).list_by_project("proj_1")
        assert locs[0].source == "llm"

    def test_location_source_wc_when_no_description(self, db):
        _setup_enrichment_db(db, 1, ["apt"])
        svc = _make_enrichment_service(db, llm_location_response={"locations": [
            {"name": "Apartment", "description": "", "scene_ids": ["sc_0"]},
        ]})
        svc.enrich_project("proj_1")
        locs = LocationRepository(db).list_by_project("proj_1")
        assert locs[0].source == "wind_comic"
