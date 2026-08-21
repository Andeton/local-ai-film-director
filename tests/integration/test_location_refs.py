"""Integration tests for Location Slice 4 — reference management, resolution, readiness.

Tests Location-scoped reference selection, generation preview/execution
parity, per-shot readiness, legacy fallback, and pinned selection.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from film_director.models.canonical import (
    Beat, CameraIntent, GenerationPlan, Location, ProductionProject,
    ReferenceRequirements, Scene, Sequence, ShotSpecificationV1, ShotSubject,
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

def _setup_project(db, pid="proj_1"):
    ProjectRepository(db).save_project(ProductionProject(
        id=pid, wc_project_id=f"wc_{pid}", title="Test",
        created_at=NOW, updated_at=NOW, provenance=_prov(f"wc_{pid}")))

def _setup_loc(db, lid="loc_1", pid="proj_1", desc="Kitchen"):
    LocationRepository(db).save(Location(
        id=lid, project_id=pid, name=desc, description=desc,
        source="human", version=1, created_at=NOW, updated_at=NOW))

def _setup_seq(db, sid="seq_1", pid="proj_1"):
    SequenceRepository(db).save_sequence(Sequence(id=sid, project_id=pid, name="Main", order_index=0))

def _setup_scene(db, scid="sc_1", sid="seq_1", lid=None):
    SceneRepository(db).save_scene(Scene(
        id=scid, sequence_id=sid, wc_scene_id=f"wc_{scid}", name=f"Scene {scid}",
        location="legacy", description="", location_id=lid,
        order_index=0, provenance=_prov(f"wc_{scid}")))

def _setup_beat(db, bid="beat_1", scid="sc_1"):
    BeatRepository(db).save_beat(Beat(
        id=bid, scene_id=scid, dramatic_action="Act", character_intention="",
        change="", order_index=0, version=1, created_at=NOW, updated_at=NOW))

def _setup_shot(db, shid="shot_1", bid="beat_1"):
    ShotRepository(db).save_shot(ShotSpecificationV1(
        id=shid, beat_id=bid, action="Man enters", dramatic_purpose="Establish",
        camera=CameraIntent(shot_size="medium"), order_index=0,
        subjects=[ShotSubject(character_id="char_1", name="The Man")],
        version=1, created_at=NOW, updated_at=NOW))

def _setup_plan(db, plid="plan_1", shid="shot_1"):
    GenerationPlanRepository(db).save_plan(GenerationPlan(
        id=plid, shot_id=shid, shot_version=1, strategy="REFERENCE_TO_VIDEO",
        reference_requirements=ReferenceRequirements(character_refs=True),
        duration_sec=5.0, version=1, created_at=NOW, updated_at=NOW))

def _setup_char(db, cid="char_1", pid="proj_1"):
    from film_director.models.canonical import CharacterReference
    CharacterRepository(db).save_character(CharacterReference(
        id=cid, project_id=pid, wc_character_id=f"wc_{cid}", name="The Man",
        description="", appearance="tall man",
        provenance=_prov(f"wc_{cid}")))

def _make_env_ref(rid="ref_env_1", pid="proj_1", lid=None, status="approved",
                  state="current", pinned=False, ts=NOW):
    return ReferenceAsset(
        id=rid, project_id=pid, location_id=lid,
        kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
        managed_path="refs/env.png", content_sha256=SHA,
        source_provenance="rgreq_1", source_fingerprint=SHA,
        status=ReferenceStatus(status), source_state=ReferenceSourceState(state),
        pinned=pinned, width=1024, height=1024, created_at=ts, updated_at=ts)

def _make_char_ref(rid="ref_char_1", pid="proj_1", cid="char_1"):
    return ReferenceAsset(
        id=rid, project_id=pid, character_id=cid,
        kind=ReferenceKind.CHARACTER_BODY, source=ReferenceSource.GENERATED,
        managed_path="refs/char.png", content_sha256=SHA,
        source_provenance="rgreq_2", source_fingerprint=SHA,
        status=ReferenceStatus.APPROVED, source_state=ReferenceSourceState.CURRENT,
        width=1024, height=1024, created_at=NOW, updated_at=NOW)


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


# ---------------------------------------------------------------------------
# ReferenceSelector.select_location_ref
# ---------------------------------------------------------------------------

class TestSelectLocationRef:
    def test_approved_current_selected(self, db):
        _setup_project(db); _setup_loc(db)
        ref = _make_env_ref(lid="loc_1")
        ReferenceAssetRepository(db).save(ref)
        sel = ReferenceSelector()
        result = sel.select_location_ref("loc_1", "proj_1",
            ReferenceAssetRepository(db).list_by_project("proj_1"))
        assert result is not None
        assert result.id == "ref_env_1"

    def test_stale_excluded(self, db):
        _setup_project(db); _setup_loc(db)
        ref = _make_env_ref(lid="loc_1", state="stale")
        ReferenceAssetRepository(db).save(ref)
        sel = ReferenceSelector()
        result = sel.select_location_ref("loc_1", "proj_1",
            ReferenceAssetRepository(db).list_by_project("proj_1"))
        assert result is None

    def test_rejected_excluded(self, db):
        _setup_project(db); _setup_loc(db)
        ref = _make_env_ref(lid="loc_1", status="rejected")
        ReferenceAssetRepository(db).save(ref)
        sel = ReferenceSelector()
        result = sel.select_location_ref("loc_1", "proj_1",
            ReferenceAssetRepository(db).list_by_project("proj_1"))
        assert result is None

    def test_candidate_excluded(self, db):
        _setup_project(db); _setup_loc(db)
        ref = _make_env_ref(lid="loc_1", status="candidate")
        ReferenceAssetRepository(db).save(ref)
        sel = ReferenceSelector()
        result = sel.select_location_ref("loc_1", "proj_1",
            ReferenceAssetRepository(db).list_by_project("proj_1"))
        assert result is None

    def test_pinned_wins(self, db):
        _setup_project(db); _setup_loc(db)
        repo = ReferenceAssetRepository(db)
        repo.save(_make_env_ref("ref_a", lid="loc_1", ts="2026-08-20T00:00:00"))
        repo.save(_make_env_ref("ref_b", lid="loc_1", pinned=True, ts="2026-08-19T00:00:00"))
        sel = ReferenceSelector()
        result = sel.select_location_ref("loc_1", "proj_1",
            repo.list_by_project("proj_1"))
        assert result.id == "ref_b"

    def test_deterministic_after_unpin(self, db):
        _setup_project(db); _setup_loc(db)
        repo = ReferenceAssetRepository(db)
        repo.save(_make_env_ref("ref_a", lid="loc_1", ts="2026-08-19T00:00:00"))
        repo.save(_make_env_ref("ref_b", lid="loc_1", ts="2026-08-20T00:00:00"))
        sel = ReferenceSelector()
        result = sel.select_location_ref("loc_1", "proj_1",
            repo.list_by_project("proj_1"))
        # Most recent (ref_b at 08-20) wins when none pinned
        assert result.id == "ref_b"

    def test_other_location_not_selected(self, db):
        _setup_project(db)
        _setup_loc(db, "loc_1"); _setup_loc(db, "loc_2")
        repo = ReferenceAssetRepository(db)
        repo.save(_make_env_ref("ref_a", lid="loc_1"))
        repo.save(_make_env_ref("ref_b", lid="loc_2"))
        sel = ReferenceSelector()
        result = sel.select_location_ref("loc_1", "proj_1",
            repo.list_by_project("proj_1"))
        assert result.id == "ref_a"

    def test_none_when_empty(self, db):
        _setup_project(db); _setup_loc(db)
        sel = ReferenceSelector()
        result = sel.select_location_ref("loc_1", "proj_1", [])
        assert result is None


# ---------------------------------------------------------------------------
# Preview/execution parity (via generation preview API)
# ---------------------------------------------------------------------------

def _make_preview_client(db):
    from film_director.api.routes import create_router
    from film_director.generation.generation_service import GenerationService
    app = FastAPI()
    mock_comfyui = MagicMock()
    gen_svc = GenerationService(
        db=db, comfyui=mock_comfyui,
        storage_root="storage", project_root=".",
        generation_timeout=60,
    )
    router = create_router(
        adapter=MagicMock(), import_service=MagicMock(),
        project_repo=ProjectRepository(db), seq_repo=SequenceRepository(db),
        scene_repo=SceneRepository(db), char_repo=CharacterRepository(db),
        llm_provider=MagicMock(),
        shot_repo=ShotRepository(db), plan_repo=GenerationPlanRepository(db),
        generation_service=gen_svc, comfyui_adapter=mock_comfyui,
        request_repo=GenerationRequestRepository(db),
        ref_asset_repo=ReferenceAssetRepository(db),
        ref_selector=ReferenceSelector(),
        location_repo=LocationRepository(db),
        take_repo=MagicMock(get_takes_by_shot=MagicMock(return_value=[]),
                           get_approved_for_shot=MagicMock(return_value=None)),
        take_service=MagicMock(get_approved_for_shot=MagicMock(return_value=None)),
    )
    app.include_router(router)
    return TestClient(app)


class TestPreviewLocationResolution:
    def test_preview_uses_location_ref(self, db):
        _setup_project(db); _setup_seq(db); _setup_loc(db, "loc_k", desc="Kitchen")
        _setup_scene(db, "sc_1", lid="loc_k"); _setup_beat(db); _setup_shot(db)
        _setup_plan(db); _setup_char(db)
        ReferenceAssetRepository(db).save(_make_char_ref())
        ReferenceAssetRepository(db).save(_make_env_ref("ref_env_k", lid="loc_k"))
        client = _make_preview_client(db)
        resp = client.get("/shots/shot_1/generation-preview")
        assert resp.status_code == 200
        data = resp.json()
        env_pic = next(p for p in data["pictures"] if "LOCATION" in p["role"] or p["kind"] == "environment")
        assert env_pic["reference_id"] == "ref_env_k"
        assert data["location_id"] == "loc_k"

    def test_two_locations_resolve_differently(self, db):
        _setup_project(db); _setup_seq(db)
        _setup_loc(db, "loc_k", desc="Kitchen"); _setup_loc(db, "loc_r", desc="Rooftop")
        _setup_scene(db, "sc_1", lid="loc_k"); _setup_scene(db, "sc_2", lid="loc_r")
        _setup_beat(db, "beat_1", "sc_1"); _setup_beat(db, "beat_2", "sc_2")
        _setup_shot(db, "shot_1", "beat_1"); _setup_shot(db, "shot_2", "beat_2")
        _setup_plan(db, "plan_1", "shot_1"); _setup_plan(db, "plan_2", "shot_2")
        _setup_char(db)
        repo = ReferenceAssetRepository(db)
        repo.save(_make_char_ref()); repo.save(_make_env_ref("ref_k", lid="loc_k"))
        repo.save(_make_env_ref("ref_r", lid="loc_r"))
        client = _make_preview_client(db)
        r1 = client.get("/shots/shot_1/generation-preview").json()
        r2 = client.get("/shots/shot_2/generation-preview").json()
        env1 = next(p for p in r1["pictures"] if p["kind"] == "environment")
        env2 = next(p for p in r2["pictures"] if p["kind"] == "environment")
        assert env1["reference_id"] == "ref_k"
        assert env2["reference_id"] == "ref_r"

    def test_legacy_fallback_when_no_location_id(self, db):
        _setup_project(db); _setup_seq(db)
        _setup_scene(db, "sc_1", lid=None)  # no location_id
        _setup_beat(db); _setup_shot(db); _setup_plan(db); _setup_char(db)
        ReferenceAssetRepository(db).save(_make_char_ref())
        ReferenceAssetRepository(db).save(_make_env_ref("ref_legacy", lid=None))
        client = _make_preview_client(db)
        resp = client.get("/shots/shot_1/generation-preview")
        data = resp.json()
        env_pic = next(p for p in data["pictures"] if p["kind"] == "environment")
        assert env_pic["reference_id"] == "ref_legacy"

    def test_missing_location_ref_blocks_generation(self, db):
        _setup_project(db); _setup_seq(db); _setup_loc(db, "loc_k")
        _setup_scene(db, "sc_1", lid="loc_k"); _setup_beat(db); _setup_shot(db)
        _setup_plan(db); _setup_char(db)
        ReferenceAssetRepository(db).save(_make_char_ref())
        # No env ref for loc_k
        client = _make_preview_client(db)
        resp = client.get("/shots/shot_1/generation-preview")
        data = resp.json()
        assert data["can_generate"] is False

    def test_stale_location_ref_not_used(self, db):
        _setup_project(db); _setup_seq(db); _setup_loc(db, "loc_k")
        _setup_scene(db, "sc_1", lid="loc_k"); _setup_beat(db); _setup_shot(db)
        _setup_plan(db); _setup_char(db)
        ReferenceAssetRepository(db).save(_make_char_ref())
        ReferenceAssetRepository(db).save(_make_env_ref("ref_stale", lid="loc_k", state="stale"))
        client = _make_preview_client(db)
        resp = client.get("/shots/shot_1/generation-preview")
        data = resp.json()
        env_pics = [p for p in data["pictures"] if p["kind"] == "environment"]
        assert len(env_pics) == 0
        assert data["can_generate"] is False


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

class TestReadiness:
    def test_ready_with_location_ref(self, db):
        _setup_project(db); _setup_seq(db); _setup_loc(db, "loc_k")
        _setup_scene(db, "sc_1", lid="loc_k"); _setup_beat(db); _setup_shot(db)
        _setup_plan(db); _setup_char(db)
        ReferenceAssetRepository(db).save(_make_char_ref())
        ReferenceAssetRepository(db).save(_make_env_ref("ref_k", lid="loc_k"))
        client = _make_preview_client(db)
        resp = client.get("/projects/proj_1/readiness")
        data = resp.json()
        assert data["ready"] is True
        assert data["shots_missing_env_ref"] == 0

    def test_missing_location_ref_blocks(self, db):
        _setup_project(db); _setup_seq(db); _setup_loc(db, "loc_k")
        _setup_scene(db, "sc_1", lid="loc_k"); _setup_beat(db); _setup_shot(db)
        _setup_plan(db); _setup_char(db)
        ReferenceAssetRepository(db).save(_make_char_ref())
        # No env ref
        client = _make_preview_client(db)
        resp = client.get("/projects/proj_1/readiness")
        data = resp.json()
        assert data["ready"] is False
        assert data["shots_missing_env_ref"] == 1

    def test_partial_readiness_mixed_scenes(self, db):
        _setup_project(db); _setup_seq(db)
        _setup_loc(db, "loc_k", desc="Kitchen"); _setup_loc(db, "loc_r", desc="Rooftop")
        _setup_scene(db, "sc_1", lid="loc_k"); _setup_scene(db, "sc_2", lid="loc_r")
        _setup_beat(db, "b1", "sc_1"); _setup_beat(db, "b2", "sc_2")
        _setup_shot(db, "s1", "b1"); _setup_shot(db, "s2", "b2")
        _setup_plan(db, "p1", "s1"); _setup_plan(db, "p2", "s2")
        _setup_char(db)
        repo = ReferenceAssetRepository(db)
        repo.save(_make_char_ref())
        repo.save(_make_env_ref("ref_k", lid="loc_k"))
        # Rooftop has no ref
        client = _make_preview_client(db)
        resp = client.get("/projects/proj_1/readiness")
        data = resp.json()
        assert data["shots_with_env_ref"] == 1
        assert data["shots_missing_env_ref"] == 1
        # Scene 1 shot is individually generatable
        r1 = client.get("/shots/s1/generation-preview").json()
        assert r1["can_generate"] is True


# ---------------------------------------------------------------------------
# Location reference API
# ---------------------------------------------------------------------------

class TestLocationRefAPI:
    def test_list_refs(self, db):
        _setup_project(db); _setup_loc(db)
        ReferenceAssetRepository(db).save(_make_env_ref(lid="loc_1"))
        client = _make_preview_client(db)
        resp = client.get("/locations/loc_1/references")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_refs_missing_location_404(self, db):
        client = _make_preview_client(db)
        assert client.get("/locations/no_such/references").status_code == 404

    def test_prompt_preview(self, db):
        _setup_project(db); _setup_loc(db, desc="A dark subway platform")
        client = _make_preview_client(db)
        resp = client.get("/locations/loc_1/reference-prompt-preview")
        assert resp.status_code == 200
        data = resp.json()
        assert "subway" in data["prompt"].lower()
        assert data["location_id"] == "loc_1"

    def test_prompt_preview_no_description_422(self, db):
        _setup_project(db)
        LocationRepository(db).save(Location(
            id="loc_empty", project_id="proj_1", name="Empty",
            description="", version=1, created_at=NOW, updated_at=NOW))
        client = _make_preview_client(db)
        assert client.get("/locations/loc_empty/reference-prompt-preview").status_code == 422


# ---------------------------------------------------------------------------
# Historical immutability
# ---------------------------------------------------------------------------

class TestHistoricalImmutability:
    def test_existing_generation_request_unchanged(self, db):
        """Location resolution changes do not mutate historical GenerationRequests."""
        _setup_project(db); _setup_seq(db); _setup_loc(db, "loc_k")
        _setup_scene(db, "sc_1", lid="loc_k"); _setup_beat(db); _setup_shot(db)
        _setup_plan(db)
        # Seed a historical generation request
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO h3_prompts (id, shot_id, generation_plan_id, "
                "source_shot_version, source_generation_plan_version, "
                "subject_definitions, summary, retention_analysis, "
                "detailed_description, rendered_prompt_text, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("h3p_1", "shot_1", "plan_1", 1, 1, "", "", "", "", "prompt", NOW))
            conn.execute(
                "INSERT INTO generation_requests "
                "(id, shot_id, shot_version, generation_plan_id, generation_plan_version, "
                "prompt_artifact_id, prompt_artifact_version, workflow_definition_id, "
                "workflow_definition_version, workflow_template_fingerprint, "
                "take_number, parameters_snapshot, reference_snapshot, seed, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("greq_1", "shot_1", 1, "plan_1", 1, "h3p_1", 1, "wf_1", "1", SHA,
                 1, "[]", json.dumps([{"reference_kind":"environment","reference_asset_id":"ref_old"}]),
                 42, "succeeded"))
            pre = dict(conn.execute("SELECT * FROM generation_requests WHERE id='greq_1'").fetchone())

        # The generation request must be unchanged after Location resolution is active
        with db.connection() as conn:
            post = dict(conn.execute("SELECT * FROM generation_requests WHERE id='greq_1'").fetchone())
        assert pre == post
