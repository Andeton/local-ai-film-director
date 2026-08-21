"""Integration tests for Location API — Slice 3.

Covers Location CRUD, Scene assignment, outdated propagation,
description-driven staleness, and ownership integrity.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from film_director.models.canonical import Location
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    GenerationPlanRepository,
    LocationRepository,
    ProjectRepository,
    ReferenceAssetRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)

NOW = "2026-08-21T00:00:00+00:00"
SHA = "a" * 64


def _seed_project(db, project_id="proj_1"):
    repo = ProjectRepository(db)
    from film_director.models.canonical import ProductionProject
    from film_director.models.provenance import Provenance
    repo.save_project(ProductionProject(
        id=project_id, wc_project_id=f"wc_{project_id}", title="Test",
        created_at=NOW, updated_at=NOW,
        provenance=Provenance(
            source_system="wind_comic", source_project_id=f"wc_{project_id}",
            source_asset_id=f"wc_{project_id}", source_asset_version=1,
            imported_at=NOW, source_hash=SHA,
        ),
    ))


def _seed_sequence(db, seq_id="seq_1", project_id="proj_1"):
    from film_director.models.canonical import Sequence
    SequenceRepository(db).save_sequence(Sequence(
        id=seq_id, project_id=project_id, name="Main", order_index=0,
    ))


def _seed_scene(db, scene_id="sc_1", seq_id="seq_1", location_id=None):
    from film_director.models.canonical import Scene
    from film_director.models.provenance import Provenance
    SceneRepository(db).save_scene(Scene(
        id=scene_id, sequence_id=seq_id, wc_scene_id=f"wc_{scene_id}",
        name=f"Scene {scene_id}", location="legacy_loc", description="",
        location_id=location_id, order_index=0,
        provenance=Provenance(
            source_system="wind_comic", source_project_id="wc_proj",
            source_asset_id=f"wc_{scene_id}", source_asset_version=1,
            imported_at=NOW, source_hash=SHA,
        ),
    ))


def _seed_beat(db, beat_id="beat_1", scene_id="sc_1"):
    from film_director.models.canonical import Beat
    BeatRepository(db).save_beat(Beat(
        id=beat_id, scene_id=scene_id, dramatic_action="Action",
        character_intention="", change="",
        order_index=0, version=1, created_at=NOW, updated_at=NOW,
    ))


def _seed_shot(db, shot_id="shot_1", beat_id="beat_1"):
    from film_director.models.canonical import ShotSpecificationV1, CameraIntent
    ShotRepository(db).save_shot(ShotSpecificationV1(
        id=shot_id, beat_id=beat_id, action="Man enters",
        dramatic_purpose="Establish", camera=CameraIntent(shot_size="medium"),
        order_index=0, version=1, created_at=NOW, updated_at=NOW,
    ))


def _seed_plan(db, plan_id="plan_1", shot_id="shot_1"):
    from film_director.models.canonical import GenerationPlan, ReferenceRequirements
    GenerationPlanRepository(db).save_plan(GenerationPlan(
        id=plan_id, shot_id=shot_id, shot_version=1,
        strategy="REFERENCE_TO_VIDEO",
        reference_requirements=ReferenceRequirements(),
        duration_sec=5.0, version=1, created_at=NOW, updated_at=NOW,
    ))


def _seed_env_ref(db, ref_id="ref_env_1", project_id="proj_1", location_id=None):
    from film_director.models.reference import (
        ReferenceAsset, ReferenceKind, ReferenceSource,
        ReferenceSourceState, ReferenceStatus,
    )
    ReferenceAssetRepository(db).save(ReferenceAsset(
        id=ref_id, project_id=project_id, location_id=location_id,
        kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
        managed_path="refs/env.png", content_sha256=SHA,
        source_provenance="rgreq_1", source_fingerprint=SHA,
        status=ReferenceStatus.APPROVED,
        source_state=ReferenceSourceState.CURRENT,
        width=1024, height=1024, created_at=NOW, updated_at=NOW,
    ))


def _seed_upload_env_ref(db, ref_id="ref_upload_1", project_id="proj_1", location_id=None):
    from film_director.models.reference import (
        ReferenceAsset, ReferenceKind, ReferenceSource,
        ReferenceSourceState, ReferenceStatus,
    )
    ReferenceAssetRepository(db).save(ReferenceAsset(
        id=ref_id, project_id=project_id, location_id=location_id,
        kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.USER_UPLOAD,
        managed_path="refs/env_upload.png", content_sha256=SHA,
        source_provenance="upload-1",
        status=ReferenceStatus.APPROVED,
        source_state=ReferenceSourceState.CURRENT,
        width=1024, height=1024, created_at=NOW, updated_at=NOW,
    ))


def _make_client(db) -> TestClient:
    from film_director.api.routes import create_router
    from fastapi import FastAPI
    from unittest.mock import MagicMock

    app = FastAPI()
    adapter = MagicMock()
    llm = MagicMock()

    router = create_router(
        adapter=adapter,
        import_service=MagicMock(),
        project_repo=ProjectRepository(db),
        seq_repo=SequenceRepository(db),
        scene_repo=SceneRepository(db),
        char_repo=MagicMock(),
        llm_provider=llm,
        shot_repo=ShotRepository(db),
        plan_repo=GenerationPlanRepository(db),
        ref_asset_repo=ReferenceAssetRepository(db),
        location_repo=LocationRepository(db),
    )
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    _seed_project(d)
    _seed_sequence(d)
    return d


@pytest.fixture
def client(db):
    return _make_client(db)


# ---------------------------------------------------------------------------
# Location CRUD
# ---------------------------------------------------------------------------

class TestCreateLocation:
    def test_create_returns_201(self, client):
        resp = client.post("/projects/proj_1/locations", json={"name": "Kitchen", "description": "Small kitchen"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Kitchen"
        assert data["description"] == "Small kitchen"
        assert data["source"] == "human"
        assert data["version"] == 1
        assert data["project_id"] == "proj_1"
        assert data["id"].startswith("loc_")

    def test_create_nonexistent_project_404(self, client):
        resp = client.post("/projects/no_such/locations", json={"name": "X"})
        assert resp.status_code == 404

    def test_create_empty_description_ok(self, client):
        resp = client.post("/projects/proj_1/locations", json={"name": "Kitchen"})
        assert resp.status_code == 201
        assert resp.json()["description"] == ""


class TestListLocations:
    def test_list_empty(self, client):
        resp = client.get("/projects/proj_1/locations")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created(self, client):
        client.post("/projects/proj_1/locations", json={"name": "Kitchen"})
        client.post("/projects/proj_1/locations", json={"name": "Rooftop"})
        resp = client.get("/projects/proj_1/locations")
        names = [loc["name"] for loc in resp.json()]
        assert "Kitchen" in names
        assert "Rooftop" in names

    def test_list_scoped_to_project(self, db):
        _seed_project(db, "proj_2")
        client = _make_client(db)
        client.post("/projects/proj_1/locations", json={"name": "A"})
        client.post("/projects/proj_2/locations", json={"name": "B"})
        assert len(client.get("/projects/proj_1/locations").json()) == 1
        assert len(client.get("/projects/proj_2/locations").json()) == 1

    def test_list_nonexistent_project_404(self, client):
        resp = client.get("/projects/no_such/locations")
        assert resp.status_code == 404


class TestGetLocation:
    def test_get_existing(self, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K"}).json()["id"]
        resp = client.get(f"/locations/{loc_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "K"

    def test_get_missing_404(self, client):
        assert client.get("/locations/no_such").status_code == 404


class TestEditLocation:
    def test_edit_name(self, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "Old"}).json()["id"]
        resp = client.put(f"/locations/{loc_id}", json={"name": "New"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"
        assert resp.json()["version"] == 2

    def test_edit_description(self, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K", "description": "Old desc"}).json()["id"]
        resp = client.put(f"/locations/{loc_id}", json={"description": "New desc"})
        assert resp.json()["description"] == "New desc"
        assert resp.json()["version"] == 2

    def test_noop_edit_no_version_increment(self, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K", "description": "D"}).json()["id"]
        resp = client.put(f"/locations/{loc_id}", json={"name": "K", "description": "D"})
        assert resp.json()["version"] == 1  # no change

    def test_edit_missing_404(self, client):
        assert client.put("/locations/no_such", json={"name": "X"}).status_code == 404

    def test_edit_requires_at_least_one_field(self, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K"}).json()["id"]
        resp = client.put(f"/locations/{loc_id}", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDeleteLocation:
    def test_delete_unreferenced(self, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K"}).json()["id"]
        resp = client.delete(f"/locations/{loc_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == loc_id
        assert client.get(f"/locations/{loc_id}").status_code == 404

    def test_delete_referenced_by_scene_blocked(self, db, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K"}).json()["id"]
        _seed_scene(db, "sc_1", "seq_1", location_id=loc_id)
        resp = client.delete(f"/locations/{loc_id}")
        assert resp.status_code == 409
        assert "referenced" in resp.json()["detail"].lower()

    def test_delete_with_owned_refs_blocked(self, db, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K"}).json()["id"]
        _seed_env_ref(db, "ref_e", "proj_1", location_id=loc_id)
        resp = client.delete(f"/locations/{loc_id}")
        assert resp.status_code == 409
        assert "reference asset" in resp.json()["detail"].lower()

    def test_delete_missing_404(self, client):
        assert client.delete("/locations/no_such").status_code == 404


# ---------------------------------------------------------------------------
# Scene assignment
# ---------------------------------------------------------------------------

class TestSceneAssignment:
    def test_assign_scene_to_location(self, db, client):
        _seed_scene(db, "sc_1", "seq_1")
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K"}).json()["id"]
        resp = client.put("/scenes/sc_1/location", json={"location_id": loc_id})
        assert resp.status_code == 200
        assert resp.json()["changed"] is True
        # Verify persisted
        scene = SceneRepository(db).get_scene("sc_1")
        assert scene.location_id == loc_id

    def test_reassign_scene(self, db, client):
        _seed_scene(db, "sc_1", "seq_1")
        loc_a = client.post("/projects/proj_1/locations", json={"name": "A"}).json()["id"]
        loc_b = client.post("/projects/proj_1/locations", json={"name": "B"}).json()["id"]
        client.put("/scenes/sc_1/location", json={"location_id": loc_a})
        resp = client.put("/scenes/sc_1/location", json={"location_id": loc_b})
        assert resp.json()["changed"] is True
        scene = SceneRepository(db).get_scene("sc_1")
        assert scene.location_id == loc_b

    def test_same_assignment_idempotent(self, db, client):
        _seed_scene(db, "sc_1", "seq_1")
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K"}).json()["id"]
        client.put("/scenes/sc_1/location", json={"location_id": loc_id})
        resp = client.put("/scenes/sc_1/location", json={"location_id": loc_id})
        assert resp.json()["changed"] is False

    def test_cross_project_rejected(self, db, client):
        _seed_project(db, "proj_2")
        _seed_sequence(db, "seq_2", "proj_2")
        _seed_scene(db, "sc_2", "seq_2")
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K"}).json()["id"]
        resp = client.put("/scenes/sc_2/location", json={"location_id": loc_id})
        assert resp.status_code == 422
        assert "different projects" in resp.json()["detail"].lower()

    def test_legacy_scene_location_unchanged(self, db, client):
        _seed_scene(db, "sc_1", "seq_1")
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K"}).json()["id"]
        client.put("/scenes/sc_1/location", json={"location_id": loc_id})
        scene = SceneRepository(db).get_scene("sc_1")
        assert scene.location == "legacy_loc"  # unchanged

    def test_scene_provenance_unchanged(self, db, client):
        _seed_scene(db, "sc_1", "seq_1")
        before = SceneRepository(db).get_scene("sc_1")
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K"}).json()["id"]
        client.put("/scenes/sc_1/location", json={"location_id": loc_id})
        after = SceneRepository(db).get_scene("sc_1")
        assert before.provenance == after.provenance

    def test_missing_scene_404(self, client):
        resp = client.put("/scenes/no_such/location", json={"location_id": "loc_1"})
        assert resp.status_code == 404

    def test_missing_location_404(self, db, client):
        _seed_scene(db, "sc_1", "seq_1")
        resp = client.put("/scenes/sc_1/location", json={"location_id": "no_such"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Outdated propagation on Scene reassignment
# ---------------------------------------------------------------------------

class TestOutdatedPropagation:
    def _setup_scene_with_shots(self, db, client):
        _seed_scene(db, "sc_1", "seq_1")
        _seed_beat(db, "beat_1", "sc_1")
        _seed_shot(db, "shot_1", "beat_1")
        _seed_shot(db, "shot_2", "beat_1")
        _seed_plan(db, "plan_1", "shot_1")
        _seed_plan(db, "plan_2", "shot_2")
        loc_a = client.post("/projects/proj_1/locations", json={"name": "A"}).json()["id"]
        loc_b = client.post("/projects/proj_1/locations", json={"name": "B"}).json()["id"]
        client.put("/scenes/sc_1/location", json={"location_id": loc_a})
        return loc_a, loc_b

    def test_reassignment_marks_shots_outdated(self, db, client):
        loc_a, loc_b = self._setup_scene_with_shots(db, client)
        # Reset shot status to non-outdated after initial assignment
        with db.connection() as conn:
            conn.execute("UPDATE shots SET status = 'draft'")
            conn.execute("UPDATE generation_plans SET status = 'draft'")
        client.put("/scenes/sc_1/location", json={"location_id": loc_b})
        s1 = ShotRepository(db).get_shot("shot_1")
        s2 = ShotRepository(db).get_shot("shot_2")
        assert s1.status == "outdated"
        assert s2.status == "outdated"

    def test_reassignment_marks_plans_outdated(self, db, client):
        loc_a, loc_b = self._setup_scene_with_shots(db, client)
        with db.connection() as conn:
            conn.execute("UPDATE shots SET status = 'draft'")
            conn.execute("UPDATE generation_plans SET status = 'draft'")
        client.put("/scenes/sc_1/location", json={"location_id": loc_b})
        p1 = GenerationPlanRepository(db).get_current_plan_by_shot("shot_1")
        p2 = GenerationPlanRepository(db).get_current_plan_by_shot("shot_2")
        # get_current_plan_by_shot excludes outdated plans
        assert p1 is None
        assert p2 is None

    def test_takes_unchanged_after_reassignment(self, db, client):
        loc_a, loc_b = self._setup_scene_with_shots(db, client)
        # Seed a take (with full FK chain)
        with db.connection() as conn:
            conn.execute("UPDATE shots SET status = 'draft'")
            conn.execute("UPDATE generation_plans SET status = 'draft'")
            conn.execute(
                "INSERT INTO h3_prompts "
                "(id, shot_id, generation_plan_id, source_shot_version, "
                " source_generation_plan_version, subject_definitions, summary, "
                " retention_analysis, detailed_description, rendered_prompt_text, "
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("h3p_x", "shot_1", "plan_1", 1, 1, "", "", "", "", "prompt", NOW),
            )
            conn.execute(
                "INSERT INTO generation_requests "
                "(id, shot_id, shot_version, generation_plan_id, generation_plan_version, "
                " prompt_artifact_id, prompt_artifact_version, workflow_definition_id, "
                " workflow_definition_version, workflow_template_fingerprint, "
                " take_number, parameters_snapshot, reference_snapshot, seed, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("greq_1", "shot_1", 1, "plan_1", 1, "h3p_x", 1, "wf_1", "1", SHA,
                 1, "[]", "[]", 42, "succeeded"),
            )
            conn.execute(
                "INSERT INTO takes (id, shot_id, generation_request_id, seed, video_path, "
                "status, is_favorite, created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("take_1", "shot_1", "greq_1", 42, "vid.mp4", "approved", 0, NOW),
            )
            # Capture pre-state
            pre_take = dict(conn.execute("SELECT * FROM takes WHERE id = 'take_1'").fetchone())
            pre_greq = dict(conn.execute("SELECT * FROM generation_requests WHERE id = 'greq_1'").fetchone())

        client.put("/scenes/sc_1/location", json={"location_id": loc_b})

        with db.connection() as conn:
            post_take = dict(conn.execute("SELECT * FROM takes WHERE id = 'take_1'").fetchone())
            post_greq = dict(conn.execute("SELECT * FROM generation_requests WHERE id = 'greq_1'").fetchone())

        assert pre_take == post_take
        assert pre_greq == post_greq

    def test_other_scene_unaffected(self, db, client):
        _seed_scene(db, "sc_1", "seq_1")
        _seed_scene(db, "sc_2", "seq_1")
        _seed_beat(db, "beat_1", "sc_1")
        _seed_beat(db, "beat_2", "sc_2")
        _seed_shot(db, "shot_1", "beat_1")
        _seed_shot(db, "shot_2", "beat_2")
        loc_a = client.post("/projects/proj_1/locations", json={"name": "A"}).json()["id"]
        loc_b = client.post("/projects/proj_1/locations", json={"name": "B"}).json()["id"]
        client.put("/scenes/sc_1/location", json={"location_id": loc_a})
        client.put("/scenes/sc_2/location", json={"location_id": loc_a})
        # Reset statuses
        with db.connection() as conn:
            conn.execute("UPDATE shots SET status = 'draft'")
        # Reassign only sc_1
        client.put("/scenes/sc_1/location", json={"location_id": loc_b})
        s2 = ShotRepository(db).get_shot("shot_2")
        assert s2.status == "draft"  # unaffected

    def test_same_assignment_no_outdated(self, db, client):
        _seed_scene(db, "sc_1", "seq_1")
        _seed_beat(db, "beat_1", "sc_1")
        _seed_shot(db, "shot_1", "beat_1")
        loc_a = client.post("/projects/proj_1/locations", json={"name": "A"}).json()["id"]
        client.put("/scenes/sc_1/location", json={"location_id": loc_a})
        with db.connection() as conn:
            conn.execute("UPDATE shots SET status = 'draft'")
        # Same assignment
        client.put("/scenes/sc_1/location", json={"location_id": loc_a})
        s1 = ShotRepository(db).get_shot("shot_1")
        assert s1.status == "draft"  # unchanged


# ---------------------------------------------------------------------------
# Description staleness
# ---------------------------------------------------------------------------

class TestDescriptionStaleness:
    def test_description_edit_stales_generated_ref(self, db, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K", "description": "Old"}).json()["id"]
        _seed_env_ref(db, "ref_e", "proj_1", location_id=loc_id)
        client.put(f"/locations/{loc_id}", json={"description": "New"})
        ref = ReferenceAssetRepository(db).get("ref_e")
        assert ref.source_state.value == "stale"

    def test_upload_ref_not_staled(self, db, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K", "description": "Old"}).json()["id"]
        _seed_upload_env_ref(db, "ref_u", "proj_1", location_id=loc_id)
        client.put(f"/locations/{loc_id}", json={"description": "New"})
        ref = ReferenceAssetRepository(db).get("ref_u")
        assert ref.source_state.value == "current"

    def test_other_location_ref_not_staled(self, db, client):
        loc_a = client.post("/projects/proj_1/locations", json={"name": "A", "description": "DescA"}).json()["id"]
        loc_b = client.post("/projects/proj_1/locations", json={"name": "B", "description": "DescB"}).json()["id"]
        _seed_env_ref(db, "ref_a", "proj_1", location_id=loc_a)
        _seed_env_ref(db, "ref_b", "proj_1", location_id=loc_b)
        client.put(f"/locations/{loc_a}", json={"description": "Changed"})
        ref_b = ReferenceAssetRepository(db).get("ref_b")
        assert ref_b.source_state.value == "current"

    def test_name_only_edit_no_staleness(self, db, client):
        loc_id = client.post("/projects/proj_1/locations", json={"name": "Old", "description": "D"}).json()["id"]
        _seed_env_ref(db, "ref_e", "proj_1", location_id=loc_id)
        client.put(f"/locations/{loc_id}", json={"name": "New"})
        ref = ReferenceAssetRepository(db).get("ref_e")
        assert ref.source_state.value == "current"

    def test_legacy_env_ref_null_location_not_staled(self, db, client):
        """ENVIRONMENT ref with location_id=None must not be staled by Location edit."""
        loc_id = client.post("/projects/proj_1/locations", json={"name": "K", "description": "Old"}).json()["id"]
        _seed_env_ref(db, "ref_legacy", "proj_1", location_id=None)
        client.put(f"/locations/{loc_id}", json={"description": "New"})
        ref = ReferenceAssetRepository(db).get("ref_legacy")
        assert ref.source_state.value == "current"
