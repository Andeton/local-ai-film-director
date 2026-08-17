"""Tests for M6.D — TakeService approve/reject/favorite/unfavorite."""
from __future__ import annotations

import os

import pytest

from film_director.errors import (
    TakeConflictError,
    TakeLifecycleError,
    TakeNotFoundError,
)
from film_director.generation.generation_request import Take
from film_director.persistence.database import Database
from film_director.persistence.repositories import TakeRepository
from film_director.services.take_service import TakeService


def _insert_take_chain(conn, take_id="t1", status="succeeded", is_favorite=False,
                       shot_id="s1", video_path="/v.mp4"):
    """Insert FK parents + take."""
    fp = "a" * 64
    conn.execute(
        "INSERT OR IGNORE INTO production_projects (id,wc_project_id,title,status,"
        "created_at,updated_at,prov_source_system,prov_source_project_id,"
        "prov_source_asset_id,prov_imported_at,prov_source_hash) "
        "VALUES ('p1','wc','T','active','t','t','s','p','a','t','h')"
    )
    conn.execute("INSERT OR IGNORE INTO sequences (id,project_id,name,order_index) VALUES ('sq','p1','S',0)")
    conn.execute(
        "INSERT OR IGNORE INTO scenes (id,sequence_id,wc_scene_id,name,location,description,"
        "order_index,prov_source_system,prov_source_project_id,prov_source_asset_id,"
        "prov_imported_at,prov_source_hash) VALUES "
        "('sc','sq','ws','S','','',0,'s','p','a','t','h')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO beats (id,scene_id,dramatic_action,character_intention,"
        "change,order_index,created_at,updated_at) VALUES ('b','sc','a','i','c',0,'t','t')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO shots (id,beat_id,dramatic_purpose,subjects,action,environment,"
        f"camera,lighting,audio_intent,duration_sec,continuity_inputs,order_index,"
        f"status,source,version,created_at,updated_at) VALUES "
        f"('{shot_id}','b','p','[]','a','{{}}','{{}}','{{}}','{{}}',5.0,'{{}}',0,'draft','generated',1,'t','t')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO generation_plans (id,shot_id,shot_version,strategy,"
        f"reference_requirements,duration_sec,status,version,created_at,updated_at) "
        f"VALUES ('gp1','{shot_id}',1,'REFERENCE_TO_VIDEO','{{}}',5.0,'draft',1,'t','t')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO h3_prompts (id,shot_id,generation_plan_id,source_shot_version,"
        f"source_generation_plan_version,subject_definitions,summary,retention_analysis,"
        f"detailed_description,rendered_prompt_text,version,created_at) VALUES "
        f"('hp1','{shot_id}','gp1',1,1,'','','','','',1,'t')"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO generation_requests (id,shot_id,shot_version,generation_plan_id,"
        f"generation_plan_version,prompt_artifact_id,prompt_artifact_version,"
        f"workflow_definition_id,workflow_definition_version,workflow_template_fingerprint,"
        f"take_number,parameters_snapshot,reference_snapshot,seed) VALUES "
        f"('r_{take_id}','{shot_id}',1,'gp1',1,'hp1',1,'w1','1.0','{fp}',1,'[]','[]',42)"
    )
    conn.execute(
        f"INSERT INTO takes (id,shot_id,generation_request_id,seed,video_path,"
        f"status,is_favorite,created_at) VALUES "
        f"('{take_id}','{shot_id}','r_{take_id}',42,'{video_path}','{status}',{int(is_favorite)},'2026-01-01')"
    )


@pytest.fixture
def env(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()
    repo = TakeRepository(db)

    # Create a valid video file for media validation
    storage = os.path.join(str(tmp_path), "storage")
    os.makedirs(storage, exist_ok=True)
    video = os.path.join(storage, "video.mp4")
    with open(video, "wb") as f:
        f.write(b"fake video")

    svc = TakeService(repo, db, storage_root=storage)

    with db.connection() as conn:
        _insert_take_chain(conn, "t1", "succeeded", video_path=video)

    return {"svc": svc, "repo": repo, "db": db, "video": video, "storage": storage}


# ---------------------------------------------------------------------------
# Transition matrix
# ---------------------------------------------------------------------------

class TestTransitionMatrix:
    def test_approve_succeeded(self, env):
        take = env["svc"].approve("t1")
        assert take.status == "approved"

    def test_reject_succeeded(self, env):
        take = env["svc"].reject("t1")
        assert take.status == "rejected"

    def test_approve_idempotent(self, env):
        env["svc"].approve("t1")
        take = env["svc"].approve("t1")
        assert take.status == "approved"

    def test_reject_idempotent(self, env):
        env["svc"].reject("t1")
        take = env["svc"].reject("t1")
        assert take.status == "rejected"

    def test_approved_terminal_cannot_reject(self, env):
        env["svc"].approve("t1")
        with pytest.raises(TakeLifecycleError):
            env["svc"].reject("t1")

    def test_rejected_terminal_cannot_approve(self, env):
        env["svc"].reject("t1")
        with pytest.raises(TakeLifecycleError):
            env["svc"].approve("t1")

    def test_pending_cannot_approve(self, env):
        with env["db"].connection() as conn:
            _insert_take_chain(conn, "t-pend", "pending", video_path=env["video"])
        with pytest.raises(TakeLifecycleError):
            env["svc"].approve("t-pend")

    def test_failed_cannot_approve(self, env):
        with env["db"].connection() as conn:
            _insert_take_chain(conn, "t-fail", "failed", video_path=env["video"])
        with pytest.raises(TakeLifecycleError):
            env["svc"].approve("t-fail")

    def test_missing_take_404(self, env):
        with pytest.raises(TakeNotFoundError):
            env["svc"].approve("nonexistent")


# ---------------------------------------------------------------------------
# Single-approved invariant
# ---------------------------------------------------------------------------

class TestSingleApproved:
    def test_first_approval(self, env):
        take = env["svc"].approve("t1")
        assert take.status == "approved"

    def test_second_take_conflict(self, env):
        env["svc"].approve("t1")
        with env["db"].connection() as conn:
            _insert_take_chain(conn, "t2", "succeeded", video_path=env["video"])
        with pytest.raises(TakeConflictError, match="already has approved"):
            env["svc"].approve("t2")

    def test_partial_index_enforced(self, env):
        """DB-level partial unique index prevents duplicate approved."""
        env["svc"].approve("t1")
        # Try raw SQL bypass
        with pytest.raises(Exception):
            with env["db"].connection() as conn:
                conn.execute(
                    "INSERT INTO takes (id,shot_id,generation_request_id,seed,video_path,"
                    "status,is_favorite,created_at) VALUES "
                    "('t-bypass','s1','r_t-bypass',42,'/v.mp4','approved',0,'2026-01-01')"
                )

    def test_approved_resolution(self, env):
        assert env["svc"].get_approved_for_shot("s1") is None
        env["svc"].approve("t1")
        approved = env["svc"].get_approved_for_shot("s1")
        assert approved is not None
        assert approved.id == "t1"

    def test_no_approved_returns_none(self, env):
        assert env["svc"].get_approved_for_shot("nonexistent") is None


# ---------------------------------------------------------------------------
# Favorite
# ---------------------------------------------------------------------------

class TestFavorite:
    def test_favorite_succeeded(self, env):
        take = env["svc"].favorite("t1")
        assert take.is_favorite is True

    def test_unfavorite(self, env):
        env["svc"].favorite("t1")
        take = env["svc"].unfavorite("t1")
        assert take.is_favorite is False

    def test_favorite_idempotent(self, env):
        env["svc"].favorite("t1")
        take = env["svc"].favorite("t1")
        assert take.is_favorite is True

    def test_unfavorite_idempotent(self, env):
        take = env["svc"].unfavorite("t1")
        assert take.is_favorite is False

    def test_multiple_favorites_per_shot(self, env):
        with env["db"].connection() as conn:
            _insert_take_chain(conn, "t2", "succeeded", video_path=env["video"])
        env["svc"].favorite("t1")
        env["svc"].favorite("t2")
        t1 = env["repo"].get_take("t1")
        t2 = env["repo"].get_take("t2")
        assert t1.is_favorite is True
        assert t2.is_favorite is True

    def test_favorite_does_not_approve(self, env):
        env["svc"].favorite("t1")
        assert env["repo"].get_take("t1").status == "succeeded"

    def test_approve_preserves_favorite(self, env):
        env["svc"].favorite("t1")
        env["svc"].approve("t1")
        take = env["repo"].get_take("t1")
        assert take.status == "approved"
        assert take.is_favorite is True

    def test_reject_preserves_favorite(self, env):
        env["svc"].favorite("t1")
        env["svc"].reject("t1")
        take = env["repo"].get_take("t1")
        assert take.status == "rejected"
        assert take.is_favorite is True

    def test_favorite_approved_take(self, env):
        env["svc"].approve("t1")
        take = env["svc"].favorite("t1")
        assert take.is_favorite is True
        assert take.status == "approved"

    def test_favorite_rejected_take(self, env):
        env["svc"].reject("t1")
        take = env["svc"].favorite("t1")
        assert take.is_favorite is True

    def test_pending_cannot_favorite(self, env):
        with env["db"].connection() as conn:
            _insert_take_chain(conn, "t-pend", "pending", video_path=env["video"])
        with pytest.raises(TakeLifecycleError):
            env["svc"].favorite("t-pend")

    def test_failed_cannot_favorite(self, env):
        with env["db"].connection() as conn:
            _insert_take_chain(conn, "t-fail", "failed", video_path=env["video"])
        with pytest.raises(TakeLifecycleError):
            env["svc"].favorite("t-fail")


# ---------------------------------------------------------------------------
# Media safety
# ---------------------------------------------------------------------------

class TestMediaSafety:
    def test_missing_video_cannot_approve(self, env):
        missing_path = os.path.join(env["storage"], "nonexistent.mp4")
        with env["db"].connection() as conn:
            _insert_take_chain(conn, "t-missing", "succeeded",
                               video_path=missing_path)
        with pytest.raises(TakeLifecycleError, match="missing"):
            env["svc"].approve("t-missing")

    def test_reject_missing_video_allowed(self, env):
        """Reject is valid even if media is missing — marking unusable artifact."""
        missing_path = os.path.join(env["storage"], "nonexistent.mp4")
        with env["db"].connection() as conn:
            _insert_take_chain(conn, "t-missing", "succeeded",
                               video_path=missing_path)
        take = env["svc"].reject("t-missing")
        assert take.status == "rejected"


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------

class TestImmutability:
    def test_approval_preserves_fields(self, env):
        before = env["repo"].get_take("t1")
        env["svc"].approve("t1")
        after = env["repo"].get_take("t1")
        assert after.seed == before.seed
        assert after.video_path == before.video_path
        assert after.generation_request_id == before.generation_request_id
        assert after.shot_id == before.shot_id
        assert after.created_at == before.created_at


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class TestMigration:
    def test_partial_index_exists(self, env):
        with env["db"].connection() as conn:
            indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(takes)").fetchall()
            }
        assert "idx_takes_one_approved_per_shot" in indexes

    def test_migration_idempotent(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "idem.db")
        db = Database(db_path)
        db.init_schema()
        db.init_schema()  # second call — must not error
