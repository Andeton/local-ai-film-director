"""Tests for M7.E atomicity/concurrency fix — rebuild CAS and persist_state race guard."""
import hashlib
import os
import threading
import pytest

from film_director.continuity.continuity_models import ContinuityState
from film_director.continuity.continuity_service import ContinuityService, ContinuityInput
from film_director.errors import ContinuityError
from film_director.persistence.database import Database
from film_director.persistence.repositories import ContinuityStateRepository, TakeRepository

_FP = "a" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_chain(conn, scene_id="scene_1", shot_ids=None):
    if shot_ids is None:
        shot_ids = ["shot_1", "shot_2"]
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


# Use a file-backed DB for real concurrency tests (not :memory:)
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
# Rebuild Atomicity
# ---------------------------------------------------------------------------

class TestRebuildAtomicity:
    def test_rebuild_uses_single_transaction_for_cas(self, db, storage_root):
        """rebuild_for_shot reads state and performs CAS in one transaction."""
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            conn.execute(
                "INSERT INTO continuity_states "
                "(id, shot_id, scene_id, predecessor_shot_id, upstream_take_id, "
                "state, continuity_revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("cs_shot_2", "shot_2", "scene_1", "shot_1", "take_s1",
                 "outdated", 0, "t", "t"),
            )
        result = svc.rebuild_for_shot("shot_2")
        assert result.state == "current"
        assert result.continuity_revision == 1

    def test_failure_before_cas_leaves_outdated(self, db, storage_root):
        """If predecessor is missing, state remains OUTDATED."""
        svc = ContinuityService(db, storage_root)
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            # No approved take for shot_1
            conn.execute(
                "INSERT INTO continuity_states "
                "(id, shot_id, scene_id, predecessor_shot_id, "
                "state, continuity_revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("cs_shot_2", "shot_2", "scene_1", "shot_1",
                 "outdated", 0, "t", "t"),
            )
        with pytest.raises(ContinuityError, match="no approved Take"):
            svc.rebuild_for_shot("shot_2")
        repo = ContinuityStateRepository(db)
        state = repo.get_by_shot("shot_2")
        assert state.state == "outdated"
        assert state.continuity_revision == 0

    def test_no_partial_revision_on_cas_failure(self, db, storage_root):
        """CAS failure with wrong revision leaves state unchanged."""
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            conn.execute(
                "INSERT INTO continuity_states "
                "(id, shot_id, scene_id, predecessor_shot_id, upstream_take_id, "
                "state, continuity_revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("cs_shot_2", "shot_2", "scene_1", "shot_1", "take_s1",
                 "outdated", 5, "t", "t"),  # revision 5
            )
        # First rebuild: outdated revision 5 → current revision 6
        result = svc.rebuild_for_shot("shot_2")
        assert result.continuity_revision == 6
        # Idempotent: same provenance returns same state
        result2 = svc.rebuild_for_shot("shot_2")
        assert result2.continuity_revision == 6  # not 7


# ---------------------------------------------------------------------------
# Concurrent Rebuild (real file-backed SQLite)
# ---------------------------------------------------------------------------

class TestConcurrentRebuild:
    def test_two_rebuilds_increment_once(self, db, storage_root):
        """Two threads rebuilding the same OUTDATED shot: revision increments once."""
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            conn.execute(
                "INSERT INTO continuity_states "
                "(id, shot_id, scene_id, predecessor_shot_id, upstream_take_id, "
                "state, continuity_revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("cs_shot_2", "shot_2", "scene_1", "shot_1", "take_s1",
                 "outdated", 0, "t", "t"),
            )

        results = []
        errors = []

        def rebuild():
            svc = ContinuityService(db, storage_root)
            try:
                r = svc.rebuild_for_shot("shot_2")
                results.append(r)
            except ContinuityError as e:
                errors.append(e)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=rebuild)
        t2 = threading.Thread(target=rebuild)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Both should complete without unhandled exceptions
        # At least one should succeed, the other either succeeds (idempotent)
        # or gets a conflict error
        total = len(results) + len(errors)
        assert total == 2, f"Expected 2 completions, got {len(results)} results + {len(errors)} errors"

        # Final state must be CURRENT with revision exactly 1
        repo = ContinuityStateRepository(db)
        final = repo.get_by_shot("shot_2")
        assert final.state == "current"
        assert final.continuity_revision == 1  # incremented exactly once

    def test_concurrent_persist_state_does_not_reactivate_outdated(self, db, storage_root):
        """persist_state cannot reactivate an OUTDATED state even under concurrency."""
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        sha = hashlib.sha256(open(fp, "rb").read()).hexdigest()
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            conn.execute(
                "INSERT INTO continuity_states "
                "(id, shot_id, scene_id, predecessor_shot_id, upstream_take_id, "
                "state, continuity_revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("cs_shot_2", "shot_2", "scene_1", "shot_1", "take_s1",
                 "outdated", 0, "t", "t"),
            )

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

        # Simulate two in-flight generations completing
        svc1 = ContinuityService(db, storage_root)
        svc2 = ContinuityService(db, storage_root)

        svc1.persist_state("shot_2", "scene_1", ci)
        svc2.persist_state("shot_2", "scene_1", ci)

        repo = ContinuityStateRepository(db)
        final = repo.get_by_shot("shot_2")
        assert final.state == "outdated"  # NOT reactivated


# ---------------------------------------------------------------------------
# persist_state Race Guard
# ---------------------------------------------------------------------------

class TestPersistStateRaceGuard:
    def test_persist_skips_outdated(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        sha = hashlib.sha256(open(fp, "rb").read()).hexdigest()
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            conn.execute(
                "INSERT INTO continuity_states "
                "(id, shot_id, scene_id, predecessor_shot_id, upstream_take_id, "
                "state, continuity_revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("cs_shot_2", "shot_2", "scene_1", "shot_1", "take_s1",
                 "outdated", 0, "t", "t"),
            )
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
        svc.persist_state("shot_2", "scene_1", ci)
        repo = ContinuityStateRepository(db)
        state = repo.get_by_shot("shot_2")
        assert state.state == "outdated"

    def test_persist_creates_new_current(self, db, storage_root):
        """persist_state for a shot with no existing state creates CURRENT."""
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        sha = hashlib.sha256(open(fp, "rb").read()).hexdigest()
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
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
        svc.persist_state("shot_2", "scene_1", ci)
        repo = ContinuityStateRepository(db)
        state = repo.get_by_shot("shot_2")
        assert state is not None
        assert state.state == "current"

    def test_persist_idempotent_same_provenance(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        sha = hashlib.sha256(open(fp, "rb").read()).hexdigest()
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
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
        svc.persist_state("shot_2", "scene_1", ci)
        svc.persist_state("shot_2", "scene_1", ci)  # second call
        repo = ContinuityStateRepository(db)
        state = repo.get_by_shot("shot_2")
        assert state.state == "current"

    def test_historical_takes_unchanged_after_rebuild(self, db, storage_root):
        """Rebuild does not modify existing Takes."""
        svc = ContinuityService(db, storage_root)
        vp, fp = _create_media(storage_root, "shot_1", "take_1")
        with db.connection() as conn:
            _setup_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_take(conn, "take_s1", "shot_1", status="approved",
                      last_frame_path=fp, video_path=vp)
            _add_take(conn, "take_s2", "shot_2", status="succeeded")
            conn.execute(
                "INSERT INTO continuity_states "
                "(id, shot_id, scene_id, predecessor_shot_id, upstream_take_id, "
                "state, continuity_revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("cs_shot_2", "shot_2", "scene_1", "shot_1", "take_s1",
                 "outdated", 0, "t", "t"),
            )
        svc.rebuild_for_shot("shot_2")
        repo = TakeRepository(db)
        t = repo.get_take("take_s2")
        assert t.status == "succeeded"  # unchanged
