"""Integration tests for M7.D — approved-Take replacement and downstream invalidation."""
import hashlib
import os
import pytest

from film_director.continuity.continuity_models import ContinuityState
from film_director.errors import TakeConflictError, TakeLifecycleError, TakeNotFoundError
from film_director.generation.generation_request import Take
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    ContinuityStateRepository,
    TakeRepository,
)
from film_director.services.take_service import TakeService

_FP = "a" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_chain(conn, scene_id="scene_1", shot_ids=None):
    if shot_ids is None:
        shot_ids = ["shot_1", "shot_2", "shot_3", "shot_4", "shot_5"]
    conn.execute(
        "INSERT OR IGNORE INTO production_projects "
        "(id, wc_project_id, title, status, created_at, updated_at, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
        "prov_imported_at, prov_source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
              video_path=None, last_frame_path=None, seed=42):
    plan_id = f"plan_{shot_id}"
    prompt_id = f"prompt_{shot_id}"
    req_id = f"greq_{take_id}"
    conn.execute(
        "INSERT OR IGNORE INTO generation_plans "
        "(id, shot_id, shot_version, strategy, reference_requirements, "
        "duration_sec, version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (plan_id, shot_id, 1, "REFERENCE_TO_VIDEO",
         '{"character_refs":true,"scene_ref":false,"prev_frame":false,"style_ref":false}',
         5.0, 1, "t", "t"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO h3_prompts "
        "(id, shot_id, generation_plan_id, source_shot_version, "
        "source_generation_plan_version, subject_definitions, summary, "
        "retention_analysis, detailed_description, rendered_prompt_text, "
        "version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
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
        "status, is_favorite, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (take_id, shot_id, req_id, seed, video_path or "v.mp4",
         last_frame_path, status, 0, "t"),
    )


def _create_media(storage_root, shot_id, take_id):
    """Create valid video and last-frame files. Returns (video_path, frame_path)."""
    d = os.path.join(storage_root, "takes", "proj_1", shot_id, take_id)
    os.makedirs(d, exist_ok=True)
    vp = os.path.join(d, "video.mp4")
    fp = os.path.join(d, "last_frame.png")
    with open(vp, "wb") as f:
        f.write(b"\x00\x00\x00\x1cftypisom" + b"\x00" * 100)  # minimal mp4-like
    # Valid PNG
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


def _make_svc(db, storage_root):
    return TakeService(TakeRepository(db), db, storage_root=storage_root)


# ---------------------------------------------------------------------------
# Successful Replacement
# ---------------------------------------------------------------------------

class TestSuccessfulReplacement:
    def test_old_becomes_superseded(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_2", "take_old")
        vp2, fp2 = _create_media(storage_root, "shot_2", "take_new")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_old", "shot_2", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "take_new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2, seed=99)
        result = svc.replace_approved("take_new")
        assert result.status == "approved"
        old = TakeRepository(db).get_take("take_old")
        assert old.status == "superseded"

    def test_new_becomes_approved(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_2", "take_old")
        vp2, fp2 = _create_media(storage_root, "shot_2", "take_new")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "take_old", "shot_2", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "take_new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2)
        result = svc.replace_approved("take_new")
        assert result.id == "take_new"
        assert result.status == "approved"

    def test_exactly_one_approved_remains(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_2", "old")
        vp2, fp2 = _create_media(storage_root, "shot_2", "new")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2)
        svc.replace_approved("new")
        repo = TakeRepository(db)
        assert repo.count_approved_for_shot("shot_2") == 1

    def test_favorites_preserved(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_2", "old")
        vp2, fp2 = _create_media(storage_root, "shot_2", "new")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2)
            conn.execute("UPDATE takes SET is_favorite = 1 WHERE id = 'old'")
        svc.replace_approved("new")
        repo = TakeRepository(db)
        old = repo.get_take("old")
        assert old.is_favorite is True
        assert old.status == "superseded"


class TestIdempotency:
    def test_already_approved_is_idempotent(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_2", "take_a")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "take_a", "shot_2", status="approved",
                      video_path=vp, last_frame_path=fp)
        result = svc.replace_approved("take_a")
        assert result.status == "approved"


# ---------------------------------------------------------------------------
# Validation Failures
# ---------------------------------------------------------------------------

class TestValidationFailures:
    def test_missing_take(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        with pytest.raises(TakeNotFoundError):
            svc.replace_approved("nonexistent")

    def test_wrong_status_pending(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_2", "old")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp, last_frame_path=fp)
            _add_take(conn, "bad", "shot_2", status="pending",
                      video_path=vp, last_frame_path=fp, take_number=2)
        with pytest.raises(TakeLifecycleError, match="pending"):
            svc.replace_approved("bad")

    def test_wrong_status_failed(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_2", "old")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp, last_frame_path=fp)
            _add_take(conn, "bad", "shot_2", status="failed",
                      video_path=vp, last_frame_path=fp, take_number=2)
        with pytest.raises(TakeLifecycleError, match="failed"):
            svc.replace_approved("bad")

    def test_wrong_status_superseded(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_2", "old")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp, last_frame_path=fp)
            _add_take(conn, "bad", "shot_2", status="superseded",
                      video_path=vp, last_frame_path=fp, take_number=2)
        with pytest.raises(TakeLifecycleError, match="superseded"):
            svc.replace_approved("bad")

    def test_no_existing_approved(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_2", "new")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=vp, last_frame_path=fp)
        with pytest.raises(TakeLifecycleError, match="No existing approved"):
            svc.replace_approved("new")

    def test_missing_video(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp_old, fp_old = _create_media(storage_root, "shot_2", "old")
        # Create a video path inside storage_root that doesn't exist
        missing_video = os.path.join(storage_root, "takes", "proj_1", "shot_2", "new", "missing.mp4")
        _, fp_new = _create_media(storage_root, "shot_2", "new")
        os.remove(os.path.join(storage_root, "takes", "proj_1", "shot_2", "new", "video.mp4"))
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp_old, last_frame_path=fp_old)
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=missing_video,
                      last_frame_path=fp_new, take_number=2)
        with pytest.raises(TakeLifecycleError, match="Video file missing"):
            svc.replace_approved("new")

    def test_missing_last_frame(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp, _ = _create_media(storage_root, "shot_2", "old")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp, last_frame_path=os.path.join(storage_root, "x.png"))
            # Create media for old but not for new
            vp2, _ = _create_media(storage_root, "shot_2", "new")
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=None, take_number=2)
        with pytest.raises(TakeLifecycleError, match="no last_frame_path"):
            svc.replace_approved("new")

    def test_non_png_last_frame(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_2", "old")
        vp2, fp2 = _create_media(storage_root, "shot_2", "new")
        # Corrupt the new frame
        with open(fp2, "wb") as f:
            f.write(b"NOT A PNG")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2)
        with pytest.raises(TakeLifecycleError, match="not a valid PNG"):
            svc.replace_approved("new")


# ---------------------------------------------------------------------------
# Downstream Invalidation
# ---------------------------------------------------------------------------

class TestDownstreamInvalidation:
    def test_five_shot_chain_invalidates_3_to_5(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_2", "old")
        vp2, fp2 = _create_media(storage_root, "shot_2", "new")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2", "shot_3", "shot_4", "shot_5"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2, seed=99)
            # Add current continuity states for shots 3-5
            for sid in ["shot_3", "shot_4", "shot_5"]:
                _add_continuity_state(conn, sid, "scene_1", state="current",
                                      predecessor_shot_id="shot_2", upstream_take_id="old")

        svc.replace_approved("new")

        cs_repo = ContinuityStateRepository(db)
        for sid in ["shot_3", "shot_4", "shot_5"]:
            state = cs_repo.get_by_shot(sid)
            assert state is not None, f"No state for {sid}"
            assert state.state == "outdated", f"{sid} should be outdated"
            assert state.invalidation_reason == "upstream_take_changed"
            assert state.invalidation_source_shot_id == "shot_2"

    def test_shot_1_unaffected(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_2", "old")
        vp2, fp2 = _create_media(storage_root, "shot_2", "new")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2", "shot_3"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2)
            _add_continuity_state(conn, "shot_1", "scene_1", state="current")
            _add_continuity_state(conn, "shot_3", "scene_1", state="current",
                                  predecessor_shot_id="shot_2")

        svc.replace_approved("new")
        cs_repo = ContinuityStateRepository(db)
        s1 = cs_repo.get_by_shot("shot_1")
        assert s1.state == "current"  # upstream of replacement, unaffected

    def test_already_outdated_stays_stable(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_2", "old")
        vp2, fp2 = _create_media(storage_root, "shot_2", "new")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2", "shot_3"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2)
            _add_continuity_state(conn, "shot_3", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_2")

        svc.replace_approved("new")
        cs_repo = ContinuityStateRepository(db)
        s3 = cs_repo.get_by_shot("shot_3")
        assert s3.state == "outdated"  # was already outdated

    def test_no_cross_scene_invalidation(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_2", "old")
        vp2, fp2 = _create_media(storage_root, "shot_2", "new")
        with db.connection() as conn:
            _setup_chain(conn, scene_id="scene_1", shot_ids=["shot_1", "shot_2"])
            # Second scene
            conn.execute(
                "INSERT INTO scenes "
                "(id, sequence_id, wc_scene_id, name, order_index, status, "
                "prov_source_system, prov_source_project_id, prov_source_asset_id, "
                "prov_imported_at, prov_source_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("scene_2", "seq_1", "wc_s2", "Scene2", 1, "draft",
                 "wind_comic", "wc_1", "a_s2", "t", "h_s2"),
            )
            conn.execute(
                "INSERT INTO beats "
                "(id, scene_id, dramatic_action, order_index, status, source, version, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("beat_scene_2", "scene_2", "action", 0, "draft", "llm", 1, "t", "t"),
            )
            conn.execute(
                "INSERT INTO shots "
                "(id, beat_id, dramatic_purpose, order_index, status, source, version, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("shot_other", "beat_scene_2", "purpose", 0, "draft", "generated", 1, "t", "t"),
            )
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2)
            _add_continuity_state(conn, "shot_other", "scene_2", state="current")

        svc.replace_approved("new")
        cs_repo = ContinuityStateRepository(db)
        other = cs_repo.get_by_shot("shot_other")
        assert other.state == "current"  # different scene, unaffected

    def test_downstream_takes_and_media_preserved(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_2", "old")
        vp2, fp2 = _create_media(storage_root, "shot_2", "new")
        vp3, fp3 = _create_media(storage_root, "shot_3", "take_3")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2", "shot_3"])
            _add_take(conn, "old", "shot_2", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "new", "shot_2", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2)
            _add_take(conn, "take_3", "shot_3", status="approved",
                      video_path=vp3, last_frame_path=fp3)
            _add_continuity_state(conn, "shot_3", "scene_1", state="current",
                                  predecessor_shot_id="shot_2", upstream_take_id="old")

        svc.replace_approved("new")

        # Take 3 still exists and is still approved (but continuity is outdated)
        repo = TakeRepository(db)
        t3 = repo.get_take("take_3")
        assert t3 is not None
        assert t3.status == "approved"
        assert os.path.isfile(vp3)
        assert os.path.isfile(fp3)


# ---------------------------------------------------------------------------
# Outdated State Blocks Generation
# ---------------------------------------------------------------------------

class TestOutdatedBlocksGeneration:
    def test_outdated_predecessor_rejected(self, db, storage_root):
        """ContinuityService.resolve_for_generation rejects outdated state."""
        from film_director.continuity.continuity_service import ContinuityService
        svc = ContinuityService(db, storage_root)
        frame_dir = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1")
        frame_path = os.path.join(frame_dir, "last_frame.png")
        _create_png(frame_path)

        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2", "shot_3"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=frame_path)
            _add_continuity_state(conn, "shot_2", "scene_1", state="outdated",
                                  predecessor_shot_id="shot_1")

        from film_director.errors import ContinuityError
        with pytest.raises(ContinuityError, match="outdated"):
            svc.resolve_for_generation("shot_2")


def _create_png(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    with open(path, "wb") as f:
        f.write(png)


# ---------------------------------------------------------------------------
# Superseded Status in Model
# ---------------------------------------------------------------------------

class TestSupersededStatus:
    def test_superseded_is_valid_status(self):
        take = Take(
            id="t1", shot_id="s1", generation_request_id="r1",
            seed=42, video_path="v.mp4", status="superseded", created_at="t",
        )
        assert take.status == "superseded"

    def test_superseded_take_favorite_allowed(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_2", "t")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "t", "shot_2", status="superseded",
                      video_path=vp, last_frame_path=fp)
        # Should be able to favorite a superseded take
        result = svc.favorite("t")
        assert result.is_favorite is True

    def test_superseded_cannot_be_approved(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_2", "t")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_2"])
            _add_take(conn, "t", "shot_2", status="superseded",
                      video_path=vp, last_frame_path=fp)
        with pytest.raises(TakeLifecycleError, match="superseded"):
            svc.approve("t")


# ---------------------------------------------------------------------------
# M6 Backward Compatibility
# ---------------------------------------------------------------------------

class TestM6Compatibility:
    def test_ordinary_approve_still_works(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "t1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1"])
            _add_take(conn, "t1", "shot_1", status="succeeded",
                      video_path=vp, last_frame_path=fp)
        result = svc.approve("t1")
        assert result.status == "approved"

    def test_ordinary_approve_does_not_replace(self, db, storage_root):
        """approve() must not silently replace an existing approved Take."""
        svc = _make_svc(db, storage_root)
        vp1, fp1 = _create_media(storage_root, "shot_1", "t1")
        vp2, fp2 = _create_media(storage_root, "shot_1", "t2")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1"])
            _add_take(conn, "t1", "shot_1", status="approved",
                      video_path=vp1, last_frame_path=fp1)
            _add_take(conn, "t2", "shot_1", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2)
        with pytest.raises(TakeConflictError):
            svc.approve("t2")

    def test_reject_still_works(self, db, storage_root):
        svc = _make_svc(db, storage_root)
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1"])
            _add_take(conn, "t1", "shot_1", status="succeeded")
        result = svc.reject("t1")
        assert result.status == "rejected"

    def test_partial_unique_index_enforced(self, db, storage_root):
        """The partial unique index on (shot_id) WHERE status='approved' must still work."""
        repo = TakeRepository(db)
        vp, fp = _create_media(storage_root, "shot_1", "t1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1"])
            _add_take(conn, "t1", "shot_1", status="approved",
                      video_path=vp, last_frame_path=fp)
        # Directly trying to set another to approved should fail
        vp2, fp2 = _create_media(storage_root, "shot_1", "t2")
        with db.connection() as conn:
            _add_take(conn, "t2", "shot_1", status="succeeded",
                      video_path=vp2, last_frame_path=fp2, take_number=2)
        with pytest.raises(Exception):
            with db.connection() as conn:
                conn.execute("UPDATE takes SET status = 'approved' WHERE id = 't2'")
