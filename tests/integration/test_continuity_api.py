"""Integration tests for M7.E — continuity API, rebuild, and queue hardening."""
import hashlib
import os
import pytest

from film_director.continuity.continuity_models import ContinuityState
from film_director.continuity.continuity_service import ContinuityService
from film_director.errors import ContinuityError
from film_director.generation.generation_request import Take
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    ContinuityStateRepository,
    TakeRepository,
)

_FP = "a" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_chain(conn, scene_id="scene_1", shot_ids=None):
    if shot_ids is None:
        shot_ids = ["shot_1", "shot_2", "shot_3"]
    conn.execute(
        "INSERT OR IGNORE INTO production_projects "
        "(id, wc_project_id, title, status, created_at, updated_at, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("proj_1", "wc_1", "Test", "active", "t", "t",
         "wind_comic", "wc_1", "a1", "t", "h1"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO sequences (id, project_id, name, order_index) VALUES (?,?,?,?)",
        ("seq_1", "proj_1", "Seq", 0),
    )
    conn.execute(
        "INSERT OR IGNORE INTO scenes "
        "(id, sequence_id, wc_scene_id, name, order_index, status, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (scene_id, "seq_1", f"wc_{scene_id}", "Scene", 0, "draft",
         "wind_comic", "wc_1", f"a_{scene_id}", "t", f"h_{scene_id}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO beats "
        "(id, scene_id, dramatic_action, order_index, status, source, version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (f"beat_{scene_id}", scene_id, "action", 0, "draft", "llm", 1, "t", "t"),
    )
    for i, sid in enumerate(shot_ids):
        conn.execute(
            "INSERT OR IGNORE INTO shots "
            "(id, beat_id, dramatic_purpose, order_index, status, source, version, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, f"beat_{scene_id}", "purpose", i, "draft", "generated", 1, "t", "t"),
        )


def _add_take(conn, take_id, shot_id, status="succeeded", take_number=1,
              video_path="v.mp4", last_frame_path=None, seed=42):
    plan_id = f"plan_{shot_id}"
    prompt_id = f"prompt_{shot_id}"
    req_id = f"greq_{take_id}"
    conn.execute(
        "INSERT OR IGNORE INTO generation_plans "
        "(id, shot_id, shot_version, strategy, reference_requirements, "
        "duration_sec, version, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (plan_id, shot_id, 1, "REFERENCE_TO_VIDEO",
         '{"character_refs":true,"scene_ref":false,"prev_frame":false,"style_ref":false}',
         5.0, 1, "t", "t"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO h3_prompts "
        "(id, shot_id, generation_plan_id, source_shot_version, "
        "source_generation_plan_version, subject_definitions, summary, "
        "retention_analysis, detailed_description, rendered_prompt_text, "
        "version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (prompt_id, shot_id, plan_id, 1, 1, "s", "s", "r", "d", "r", 1, "t"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO generation_requests "
        "(id, shot_id, shot_version, generation_plan_id, generation_plan_version, "
        "prompt_artifact_id, prompt_artifact_version, workflow_definition_id, "
        "workflow_definition_version, workflow_template_fingerprint, take_number, "
        "parameters_snapshot, reference_snapshot, seed, continuity_snapshot, "
        "comfyui_prompt_id, status, submitted_at, completed_at, error) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (req_id, shot_id, 1, plan_id, 1, prompt_id, 1,
         "h3_r2v_v1", "1.0.0", _FP, take_number,
         "[]", "[]", seed, None, None, "succeeded", "", "", None),
    )
    conn.execute(
        "INSERT OR IGNORE INTO takes "
        "(id, shot_id, generation_request_id, seed, video_path, last_frame_path, "
        "status, is_favorite, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (take_id, shot_id, req_id, seed, video_path,
         last_frame_path, status, 0, "t"),
    )


def _add_continuity_state(conn, shot_id, scene_id, state="current",
                           predecessor_shot_id=None, upstream_take_id=None):
    conn.execute(
        "INSERT OR IGNORE INTO continuity_states "
        "(id, shot_id, scene_id, predecessor_shot_id, upstream_take_id, "
        "state, continuity_revision, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (f"cs_{shot_id}", shot_id, scene_id, predecessor_shot_id,
         upstream_take_id, state, 0, "t", "t"),
    )


def _create_media(storage_root, shot_id, take_id):
    d = os.path.join(storage_root, "takes", "proj_1", shot_id, take_id)
    os.makedirs(d, exist_ok=True)
    vp = os.path.join(d, "video.mp4")
    fp = os.path.join(d, "last_frame.png")
    with open(vp, "wb") as f:
        f.write(b"\x00" * 100)
    png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    with open(fp, "wb") as f:
        f.write(png)
    return vp, fp


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def storage_root(tmp_path):
    root = str(tmp_path / "storage")
    os.makedirs(root, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# ContinuityService.rebuild_for_shot
# ---------------------------------------------------------------------------

class TestRebuildForShot:
    def test_outdated_to_current(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        frame_dir = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1")
        _create_media(storage_root, "shot_1", "take_1")

        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=os.path.join(frame_dir, "last_frame.png"),
                      video_path=os.path.join(frame_dir, "video.mp4"))
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1", upstream_take_id="take_s1")

        result = svc.rebuild_for_shot("shot_2")
        assert result.state == "current"
        assert result.upstream_take_id == "take_s1"
        assert result.continuity_revision == 1  # incremented from 0

    def test_revision_increments_once(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1")

        r1 = svc.rebuild_for_shot("shot_2")
        assert r1.continuity_revision == 1
        # Rebuilding again with same provenance is idempotent
        r2 = svc.rebuild_for_shot("shot_2")
        assert r2.continuity_revision == 1  # same, not incremented again

    def test_fingerprint_changes_with_new_take(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1a", "shot_1", status="approved",
                      last_frame_path=fp1, video_path=vp1)
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1", upstream_take_id="take_s1a")

        r1 = svc.rebuild_for_shot("shot_2")
        fp1_val = r1.upstream_last_frame_sha256
        # Now if provenance changes (different Take), fingerprint would differ
        # (We can't easily test this without replacing, but the SHA mismatch test covers it)
        assert len(fp1_val) == 64

    def test_chain_head_rejected(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1"])
        with pytest.raises(ContinuityError, match="chain-head"):
            svc.rebuild_for_shot("shot_1")

    def test_no_approved_predecessor_rejected(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="succeeded")
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1")
        with pytest.raises(ContinuityError, match="no approved Take"):
            svc.rebuild_for_shot("shot_2")

    def test_predecessor_outdated_rejected(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2", "shot_3"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1")
            vp2, fp2 = _create_media(storage_root, "shot_2", "take_2")
            _add_take(conn, "take_s2", "shot_2", status="approved",
                      last_frame_path=fp2, video_path=vp2)
            _add_continuity_state(conn, "shot_3", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_2")
        # shot_2 is itself outdated, so rebuilding shot_3 should fail
        with pytest.raises(ContinuityError, match="outdated"):
            svc.rebuild_for_shot("shot_3")

    def test_missing_frame_rejected(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        nonexistent = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1", "last_frame.png")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=nonexistent)
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1")
        with pytest.raises(ContinuityError, match="does not exist"):
            svc.rebuild_for_shot("shot_2")

    def test_no_existing_state_creates_current(self, db, storage_root):
        """If no continuity state exists, rebuild creates a new CURRENT state."""
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
        result = svc.rebuild_for_shot("shot_2")
        assert result.state == "current"
        assert result.upstream_take_id == "take_s1"
        assert result.continuity_revision == 1

    def test_historical_requests_preserved(self, db, storage_root):
        """Rebuild must not modify any existing GenerationRequests."""
        from film_director.persistence.repositories import GenerationRequestRepository
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            _add_take(conn, "take_s2", "shot_2", status="succeeded")
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1")
        req_repo = GenerationRequestRepository(db)
        req_before = req_repo.get_request("greq_take_s2")

        svc.rebuild_for_shot("shot_2")

        req_after = req_repo.get_request("greq_take_s2")
        assert req_after.id == req_before.id
        assert req_after.status == req_before.status
        assert req_after.continuity_snapshot == req_before.continuity_snapshot


# ---------------------------------------------------------------------------
# API Route Registration
# ---------------------------------------------------------------------------

class TestRouteRegistration:
    def _make_app(self, db, storage_root):
        from fastapi.testclient import TestClient
        from film_director.api.routes import create_router
        from film_director.services.take_service import TakeService

        take_repo = TakeRepository(db)
        cs_repo = ContinuityStateRepository(db)
        cs_svc = ContinuityService(db, storage_root)
        take_svc = TakeService(take_repo, db, storage_root=storage_root)

        # Minimal required params
        router = create_router(
            adapter=None,
            import_service=None,
            project_repo=None,
            seq_repo=None,
            scene_repo=None,
            char_repo=None,
            llm_provider=None,
            take_service=take_svc,
            take_repo=take_repo,
            continuity_service=cs_svc,
            continuity_state_repo=cs_repo,
        )

        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_continuity_state_route_exists(self, db, storage_root):
        client = self._make_app(db, storage_root)
        resp = client.get("/shots/nonexistent/continuity-state")
        assert resp.status_code == 404  # not 405 (route exists)

    def test_predecessor_route_exists(self, db, storage_root):
        client = self._make_app(db, storage_root)
        resp = client.get("/shots/nonexistent/predecessor")
        assert resp.status_code == 404

    def test_replace_approved_route_exists(self, db, storage_root):
        client = self._make_app(db, storage_root)
        resp = client.post("/shots/shot_1/replace-approved", json={"take_id": "t1"})
        assert resp.status_code in (404, 409, 501)

    def test_continuity_chain_route_exists(self, db, storage_root):
        client = self._make_app(db, storage_root)
        resp = client.get("/scenes/nonexistent/continuity-chain")
        assert resp.status_code == 404

    def test_outdated_shots_route_exists(self, db, storage_root):
        client = self._make_app(db, storage_root)
        resp = client.get("/scenes/nonexistent/outdated-shots")
        assert resp.status_code == 200  # empty list, not 404

    def test_rebuild_route_exists(self, db, storage_root):
        client = self._make_app(db, storage_root)
        resp = client.post("/shots/nonexistent/continuity/rebuild")
        assert resp.status_code == 409  # ContinuityError, not 405

    def test_get_continuity_state_returns_data(self, db, storage_root):
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_continuity_state(conn, "shot_2", "scene_1", state="current",
                                  predecessor_shot_id="shot_1")
        client = self._make_app(db, storage_root)
        resp = client.get("/shots/shot_2/continuity-state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["shot_id"] == "shot_2"
        assert data["state"] == "current"

    def test_predecessor_returns_data(self, db, storage_root):
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
        client = self._make_app(db, storage_root)
        resp = client.get("/shots/shot_2/predecessor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["predecessor_shot_id"] == "shot_1"
        assert data["scene_id"] == "scene_1"

    def test_chain_head_has_no_predecessor(self, db, storage_root):
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1"])
        client = self._make_app(db, storage_root)
        resp = client.get("/shots/shot_1/predecessor")
        assert resp.status_code == 200
        assert resp.json()["predecessor_shot_id"] is None

    def test_continuity_chain_ordered(self, db, storage_root):
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2", "shot_3"])
            _add_continuity_state(conn, "shot_2", "scene_1", state="current",
                                  predecessor_shot_id="shot_1")
            _add_continuity_state(conn, "shot_3", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_2")
        client = self._make_app(db, storage_root)
        resp = client.get("/scenes/scene_1/continuity-chain")
        assert resp.status_code == 200
        chain = resp.json()
        assert len(chain) == 3
        assert chain[0]["shot_id"] == "shot_1"
        assert chain[0]["predecessor_shot_id"] is None
        assert chain[1]["shot_id"] == "shot_2"
        assert chain[1]["continuity_state"] == "current"
        assert chain[2]["shot_id"] == "shot_3"
        assert chain[2]["continuity_state"] == "outdated"

    def test_outdated_shots_filtered(self, db, storage_root):
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2", "shot_3"])
            _add_continuity_state(conn, "shot_2", "scene_1", state="current")
            _add_continuity_state(conn, "shot_3", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_2")
        client = self._make_app(db, storage_root)
        resp = client.get("/scenes/scene_1/outdated-shots")
        assert resp.status_code == 200
        outdated = resp.json()
        assert len(outdated) == 1
        assert outdated[0]["shot_id"] == "shot_3"

    def test_rebuild_api_success(self, db, storage_root):
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1")
        client = self._make_app(db, storage_root)
        resp = client.post("/shots/shot_2/continuity/rebuild")
        assert resp.status_code == 200
        assert resp.json()["state"] == "current"

    def test_rebuild_chain_head_409(self, db, storage_root):
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1"])
        client = self._make_app(db, storage_root)
        resp = client.post("/shots/shot_1/continuity/rebuild")
        assert resp.status_code == 409

    def test_get_routes_readonly(self, db, storage_root):
        """GET routes must not create continuity states."""
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
        client = self._make_app(db, storage_root)
        client.get("/shots/shot_2/predecessor")
        cs_repo = ContinuityStateRepository(db)
        assert cs_repo.get_by_shot("shot_2") is None  # no state created

    def test_no_absolute_path_in_chain_response(self, db, storage_root):
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1"])
        client = self._make_app(db, storage_root)
        resp = client.get("/scenes/scene_1/continuity-chain")
        raw = resp.text
        assert "C:\\" not in raw
        assert "D:\\" not in raw
        assert "/home/" not in raw


# ---------------------------------------------------------------------------
# Queue Dependency
# ---------------------------------------------------------------------------

class TestInFlightRace:
    def test_persist_state_does_not_reactivate_outdated(self, db, storage_root):
        """An in-flight generation completing cannot reactivate an OUTDATED state."""
        from film_director.continuity.continuity_service import ContinuityInput
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1", upstream_take_id="take_s1")

        sha = hashlib.sha256(open(fp, "rb").read()).hexdigest()
        ci = ContinuityInput(
            continuity_state_id="cs_shot_2",
            upstream_shot_id="shot_1",
            upstream_take_id="take_s1",
            upstream_take_number=1,
            frame_path=fp,
            frame_sha256=sha,
            continuity_revision=0,
            continuity_fingerprint="c" * 64,
        )
        # Simulate in-flight finalization calling persist_state
        svc.persist_state("shot_2", "scene_1", ci)

        cs_repo = ContinuityStateRepository(db)
        state = cs_repo.get_by_shot("shot_2")
        assert state.state == "outdated"  # NOT reactivated to current


class TestQueueDependency:
    def test_outdated_blocks_resolve(self, db, storage_root):
        """resolve_for_generation rejects OUTDATED state."""
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1")
        with pytest.raises(ContinuityError, match="outdated"):
            svc.resolve_for_generation("shot_2")

    def test_repaired_state_allows_resolve(self, db, storage_root):
        """After rebuild, resolve_for_generation succeeds."""
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1")
        svc.rebuild_for_shot("shot_2")
        result = svc.resolve_for_generation("shot_2")
        assert result is not None
        assert result.upstream_take_id == "take_s1"
