"""Integration tests for Location Slice 6 — operator UI API surface.

Tests the Location API endpoints as exercised by the operator UI,
covering CRUD, scene assignment, reference management, generation
preview, and readiness through the HTTP API.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from film_director.models.canonical import (
    Beat, CameraIntent, CharacterReference, GenerationPlan, Location,
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
    GenerationRequestRepository, LocationRepository, ProjectRepository,
    ReferenceAssetRepository, SceneRepository, SequenceRepository,
    ShotRepository,
)
from film_director.services.reference_lifecycle import ReferenceSelector

NOW = "2026-08-21T00:00:00+00:00"
SHA = "a" * 64

def _prov(aid="wc_1"):
    return Provenance(source_system="wind_comic", source_project_id="wc_p",
                      source_asset_id=aid, source_asset_version=1,
                      imported_at=NOW, source_hash=SHA)


def _make_client(db):
    from film_director.api.routes import create_router
    from film_director.services.reference_lifecycle import ReferenceLifecycleService
    app = FastAPI()
    ref_repo = ReferenceAssetRepository(db)
    router = create_router(
        adapter=MagicMock(), import_service=MagicMock(),
        project_repo=ProjectRepository(db), seq_repo=SequenceRepository(db),
        scene_repo=SceneRepository(db), char_repo=CharacterRepository(db),
        llm_provider=MagicMock(),
        shot_repo=ShotRepository(db), plan_repo=GenerationPlanRepository(db),
        generation_service=MagicMock(_db=db,
            _prompt_repo=MagicMock(get_current_prompt=MagicMock(return_value=None))),
        comfyui_adapter=MagicMock(),
        request_repo=GenerationRequestRepository(db),
        ref_asset_repo=ref_repo,
        ref_selector=ReferenceSelector(),
        ref_lifecycle_service=ReferenceLifecycleService(ref_repo),
        location_repo=LocationRepository(db),
        take_repo=MagicMock(get_takes_by_shot=MagicMock(return_value=[]),
                           get_approved_for_shot=MagicMock(return_value=None)),
        take_service=MagicMock(get_approved_for_shot=MagicMock(return_value=None)),
    )
    app.include_router(router)
    return TestClient(app)


def _setup(db, n_locations=3):
    """Create a project with Locations, Scenes, and Shots."""
    ProjectRepository(db).save_project(ProductionProject(
        id="proj_1", wc_project_id="wc_p1", title="Test",
        created_at=NOW, updated_at=NOW, provenance=_prov("wc_p1")))
    SequenceRepository(db).save_sequence(Sequence(
        id="seq_1", project_id="proj_1", name="Main", order_index=0))

    loc_repo = LocationRepository(db)
    scene_repo = SceneRepository(db)
    loc_names = ["Apartment", "Subway", "Office"][:n_locations]
    for i, name in enumerate(loc_names):
        loc_repo.save(Location(
            id=f"loc_{i}", project_id="proj_1", name=name,
            description=f"A {name.lower()}", source="llm", version=1,
            created_at=NOW, updated_at=NOW))

    scenes = [
        ("sc_0", "loc_0", "apartment"),
        ("sc_1", "loc_1", "subway"),
        ("sc_2", "loc_2", "office"),
        ("sc_3", "loc_0", "apartment"),
    ]
    for sc_id, loc_id, loc_str in scenes:
        scene_repo.save_scene(Scene(
            id=sc_id, sequence_id="seq_1", wc_scene_id=f"wc_{sc_id}",
            name=f"Scene {sc_id}", location=loc_str, description="",
            location_id=loc_id, order_index=int(sc_id[-1]),
            provenance=_prov(f"wc_{sc_id}")))

    # Beats and shots for sc_0
    BeatRepository(db).save_beat(Beat(
        id="beat_0", scene_id="sc_0", dramatic_action="Act",
        character_intention="", change="",
        order_index=0, version=1, created_at=NOW, updated_at=NOW))
    ShotRepository(db).save_shot(ShotSpecificationV1(
        id="shot_0", beat_id="beat_0", action="Enter",
        dramatic_purpose="Establish",
        camera=CameraIntent(shot_size="wide"),
        subjects=[ShotSubject(character_id="char_1", name="Man")],
        order_index=0, version=1, created_at=NOW, updated_at=NOW))
    GenerationPlanRepository(db).save_plan(GenerationPlan(
        id="plan_0", shot_id="shot_0", shot_version=1,
        strategy="REFERENCE_TO_VIDEO",
        reference_requirements=ReferenceRequirements(character_refs=True),
        duration_sec=5.0, version=1, created_at=NOW, updated_at=NOW))
    CharacterRepository(db).save_character(CharacterReference(
        id="char_1", project_id="proj_1", wc_character_id="wc_c1",
        name="The Man", description="", appearance="tall man",
        provenance=_prov("wc_c1")))


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


# ---------------------------------------------------------------------------
# Location CRUD through API
# ---------------------------------------------------------------------------

class TestLocationListUI:
    def test_list_all_project_locations(self, db):
        _setup(db)
        client = _make_client(db)
        resp = client.get("/projects/proj_1/locations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        names = {l["name"] for l in data}
        assert names == {"Apartment", "Subway", "Office"}

    def test_location_detail(self, db):
        _setup(db)
        client = _make_client(db)
        resp = client.get("/locations/loc_0")
        assert resp.status_code == 200
        d = resp.json()
        assert d["name"] == "Apartment"
        assert "apartment" in d["description"].lower()

    def test_edit_description_persists(self, db):
        _setup(db)
        client = _make_client(db)
        client.put("/locations/loc_0", json={"description": "New apartment desc"})
        resp = client.get("/locations/loc_0")
        assert resp.json()["description"] == "New apartment desc"
        assert resp.json()["version"] == 2

    def test_switching_locations_no_draft_leak(self, db):
        """Editing one Location does not affect another."""
        _setup(db)
        client = _make_client(db)
        client.put("/locations/loc_0", json={"description": "Edited apartment"})
        # loc_1 should be unchanged
        resp = client.get("/locations/loc_1")
        assert resp.json()["description"] == "A subway"

    def test_add_location(self, db):
        _setup(db, 0)
        client = _make_client(db)
        resp = client.post("/projects/proj_1/locations", json={"name": "Park", "description": "A park"})
        assert resp.status_code == 201
        locs = client.get("/projects/proj_1/locations").json()
        assert any(l["name"] == "Park" for l in locs)

    def test_delete_unused_location(self, db):
        _setup(db)
        # loc_2 is used by sc_2, but let's create an unused one
        LocationRepository(db).save(Location(
            id="loc_unused", project_id="proj_1", name="Unused",
            source="human", version=1, created_at=NOW, updated_at=NOW))
        client = _make_client(db)
        resp = client.delete("/locations/loc_unused")
        assert resp.status_code == 200
        assert client.get("/locations/loc_unused").status_code == 404

    def test_delete_scene_used_location_blocked(self, db):
        _setup(db)
        client = _make_client(db)
        resp = client.delete("/locations/loc_0")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Scene assignment
# ---------------------------------------------------------------------------

class TestSceneAssignmentUI:
    def test_scenes_show_location(self, db):
        """Scenes endpoint returns location_id for each scene."""
        _setup(db)
        client = _make_client(db)
        scenes = client.get("/projects/proj_1/scenes").json()
        apt_scenes = [s for s in scenes if s["location_id"] == "loc_0"]
        assert len(apt_scenes) == 2  # sc_0 and sc_3

    def test_reassign_persists(self, db):
        _setup(db)
        client = _make_client(db)
        resp = client.put("/scenes/sc_1/location", json={"location_id": "loc_0"})
        assert resp.json()["changed"] is True
        # Verify
        scenes = client.get("/projects/proj_1/scenes").json()
        sc1 = next(s for s in scenes if s["id"] == "sc_1")
        assert sc1["location_id"] == "loc_0"

    def test_reassignment_communicates_outdated(self, db):
        _setup(db)
        client = _make_client(db)
        # sc_0 has shot_0 → reassign sc_0 to loc_1
        resp = client.put("/scenes/sc_0/location", json={"location_id": "loc_1"})
        assert resp.json()["changed"] is True
        # shot_0 should be outdated
        shot = ShotRepository(db).get_shot("shot_0")
        assert shot.status == "outdated"

    def test_unassigned_scene_visible(self, db):
        """Create a scene without location_id → it appears as unassigned."""
        _setup(db, 0)
        SceneRepository(db).save_scene(Scene(
            id="sc_unassigned", sequence_id="seq_1", wc_scene_id="wc_u",
            name="Unassigned Scene", location="", description="",
            order_index=0, provenance=_prov("wc_u")))
        client = _make_client(db)
        scenes = client.get("/projects/proj_1/scenes").json()
        unassigned = [s for s in scenes if not s.get("location_id")]
        assert len(unassigned) == 1
        assert unassigned[0]["name"] == "Unassigned Scene"


# ---------------------------------------------------------------------------
# Location references
# ---------------------------------------------------------------------------

class TestLocationRefsUI:
    def test_refs_scoped_to_location(self, db):
        _setup(db)
        repo = ReferenceAssetRepository(db)
        repo.save(ReferenceAsset(
            id="ref_apt", project_id="proj_1", location_id="loc_0",
            kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
            managed_path="refs/apt.png", content_sha256=SHA,
            source_provenance="rgreq_a",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at=NOW, updated_at=NOW))
        repo.save(ReferenceAsset(
            id="ref_sub", project_id="proj_1", location_id="loc_1",
            kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
            managed_path="refs/sub.png", content_sha256=SHA,
            source_provenance="rgreq_b",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at=NOW, updated_at=NOW))
        client = _make_client(db)
        apt_refs = client.get("/locations/loc_0/references").json()
        assert len(apt_refs) == 1
        assert apt_refs[0]["id"] == "ref_apt"
        sub_refs = client.get("/locations/loc_1/references").json()
        assert len(sub_refs) == 1
        assert sub_refs[0]["id"] == "ref_sub"

    def test_prompt_preview_uses_location_description(self, db):
        _setup(db)
        client = _make_client(db)
        resp = client.get("/locations/loc_0/reference-prompt-preview")
        assert resp.status_code == 200
        assert "apartment" in resp.json()["prompt"].lower()
        assert resp.json()["location_id"] == "loc_0"

    def test_pinning_changes_selection(self, db):
        _setup(db)
        repo = ReferenceAssetRepository(db)
        repo.save(ReferenceAsset(
            id="ref_a", project_id="proj_1", location_id="loc_0",
            kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
            managed_path="refs/a.png", content_sha256=SHA,
            source_provenance="rg_a",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024,
            created_at="2026-08-19T00:00:00", updated_at="2026-08-19T00:00:00"))
        repo.save(ReferenceAsset(
            id="ref_b", project_id="proj_1", location_id="loc_0",
            kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
            managed_path="refs/b.png", content_sha256=SHA,
            source_provenance="rg_b",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024,
            created_at="2026-08-20T00:00:00", updated_at="2026-08-20T00:00:00"))
        client = _make_client(db)
        # Most recent (ref_b) selected by default
        sel = ReferenceSelector()
        all_refs = repo.list_by_project("proj_1")
        assert sel.select_location_ref("loc_0", "proj_1", all_refs).id == "ref_b"
        # Pin ref_a
        client.post("/references/ref_a/pin")
        all_refs = repo.list_by_project("proj_1")
        assert sel.select_location_ref("loc_0", "proj_1", all_refs).id == "ref_a"

    def test_lifecycle_actions_functional(self, db):
        _setup(db)
        repo = ReferenceAssetRepository(db)
        repo.save(ReferenceAsset(
            id="ref_test", project_id="proj_1", location_id="loc_0",
            kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
            managed_path="refs/t.png", content_sha256=SHA,
            source_provenance="rg_t",
            status=ReferenceStatus.CANDIDATE,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at=NOW, updated_at=NOW))
        client = _make_client(db)
        # Approve
        client.post("/references/ref_test/approve")
        assert repo.get("ref_test").status == ReferenceStatus.APPROVED
        # Archive
        client.post("/references/ref_test/archive")
        assert repo.get("ref_test").status == ReferenceStatus.ARCHIVED


# ---------------------------------------------------------------------------
# Generation preview with Locations
# ---------------------------------------------------------------------------

class TestGenerationPreviewUI:
    def test_preview_shows_location_name(self, db):
        _setup(db)
        repo = ReferenceAssetRepository(db)
        repo.save(ReferenceAsset(
            id="ref_apt", project_id="proj_1", location_id="loc_0",
            kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
            managed_path="refs/apt.png", content_sha256=SHA,
            source_provenance="rg",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at=NOW, updated_at=NOW))
        repo.save(ReferenceAsset(
            id="ref_char", project_id="proj_1", character_id="char_1",
            kind=ReferenceKind.CHARACTER_BODY, source=ReferenceSource.GENERATED,
            managed_path="refs/c.png", content_sha256=SHA,
            source_provenance="rg2",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at=NOW, updated_at=NOW))
        client = _make_client(db)
        resp = client.get("/shots/shot_0/generation-preview")
        data = resp.json()
        assert data["location_name"] == "Apartment"
        env_pic = next(p for p in data["pictures"] if p["kind"] == "environment")
        assert "Apartment" in env_pic["role"]

    def test_missing_location_ref_blocks(self, db):
        _setup(db)
        # No env ref for loc_0
        repo = ReferenceAssetRepository(db)
        repo.save(ReferenceAsset(
            id="ref_char", project_id="proj_1", character_id="char_1",
            kind=ReferenceKind.CHARACTER_BODY, source=ReferenceSource.GENERATED,
            managed_path="refs/c.png", content_sha256=SHA,
            source_provenance="rg",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at=NOW, updated_at=NOW))
        client = _make_client(db)
        resp = client.get("/shots/shot_0/generation-preview")
        assert resp.json()["can_generate"] is False

    def test_no_provider_wording_in_location_role(self, db):
        _setup(db)
        repo = ReferenceAssetRepository(db)
        repo.save(ReferenceAsset(
            id="ref_apt", project_id="proj_1", location_id="loc_0",
            kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
            managed_path="refs/apt.png", content_sha256=SHA,
            source_provenance="rg",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at=NOW, updated_at=NOW))
        repo.save(ReferenceAsset(
            id="ref_char", project_id="proj_1", character_id="char_1",
            kind=ReferenceKind.CHARACTER_BODY, source=ReferenceSource.GENERATED,
            managed_path="refs/c.png", content_sha256=SHA,
            source_provenance="rg2",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at=NOW, updated_at=NOW))
        client = _make_client(db)
        resp = client.get("/shots/shot_0/generation-preview")
        env_pic = next(p for p in resp.json()["pictures"] if p["kind"] == "environment")
        role = env_pic["role"].lower()
        assert "picture 2" not in role
        assert "h3" not in role
        assert "slot" not in role


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

class TestReadinessUI:
    def test_ready_shot_generatable_while_other_blocked(self, db):
        _setup(db)
        repo = ReferenceAssetRepository(db)
        # Only loc_0 has a ref → shots at loc_0 ready, loc_1/loc_2 blocked
        repo.save(ReferenceAsset(
            id="ref_apt", project_id="proj_1", location_id="loc_0",
            kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
            managed_path="refs/apt.png", content_sha256=SHA,
            source_provenance="rg",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at=NOW, updated_at=NOW))
        repo.save(ReferenceAsset(
            id="ref_char", project_id="proj_1", character_id="char_1",
            kind=ReferenceKind.CHARACTER_BODY, source=ReferenceSource.GENERATED,
            managed_path="refs/c.png", content_sha256=SHA,
            source_provenance="rg2",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024, created_at=NOW, updated_at=NOW))
        client = _make_client(db)
        # shot_0 is at sc_0 → loc_0 → has ref → should be generatable
        resp = client.get("/shots/shot_0/generation-preview")
        assert resp.json()["can_generate"] is True

    def test_character_ref_ui_remains_functional(self, db):
        _setup(db)
        client = _make_client(db)
        chars = client.get("/projects/proj_1/characters").json()
        assert len(chars) == 1
        assert chars[0]["name"] == "The Man"

    def test_page_refresh_retains_data(self, db):
        """Canonical data persists through simulated page refresh (re-fetch)."""
        _setup(db)
        client = _make_client(db)
        client.put("/locations/loc_0", json={"description": "Updated apartment"})
        # Simulated refresh: re-fetch
        loc = client.get("/locations/loc_0").json()
        assert loc["description"] == "Updated apartment"

    def test_legacy_env_controls_not_canonical_for_location_projects(self, db):
        """Project-level readiness should report per-Location status."""
        _setup(db)
        client = _make_client(db)
        resp = client.get("/projects/proj_1/readiness")
        data = resp.json()
        # Should report missing Location refs, not generic "Environment reference"
        assert "shots_missing_env_ref" in data
        assert data["shots_missing_env_ref"] > 0  # no refs generated
