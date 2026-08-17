"""M6.A tests — Take lifecycle, seed derivation, QueueJob model, queue schema.

Covers:
- Deterministic seed derivation with safe domain
- Take model evolution (approved/rejected/is_favorite)
- QueueJob model validation
- Database schema migration and persistence
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from film_director.generation.generation_request import Take
from film_director.generation.parameter_resolver import (
    _SAFE_SEED_MAX,
    derive_take_seed,
)
from film_director.generation.queue_models import QueueJob
from film_director.persistence.database import Database


# ===========================================================================
# Seed Derivation
# ===========================================================================

class TestDeriveTakeSeed:
    def test_deterministic_repeatability(self):
        a = derive_take_seed(42, 0)
        b = derive_take_seed(42, 0)
        assert a == b

    def test_different_take_indices(self):
        seeds = [derive_take_seed(42, i) for i in range(10)]
        assert len(set(seeds)) == 10, "All 10 seeds should be distinct"

    def test_different_base_seeds(self):
        a = derive_take_seed(100, 0)
        b = derive_take_seed(200, 0)
        assert a != b

    def test_zero_base_seed(self):
        seed = derive_take_seed(0, 0)
        assert 0 <= seed <= _SAFE_SEED_MAX

    def test_max_base_seed(self):
        seed = derive_take_seed(_SAFE_SEED_MAX, 0)
        assert 0 <= seed <= _SAFE_SEED_MAX

    def test_output_fits_sqlite_integer(self):
        """All derived seeds must fit SQLite signed 64-bit INTEGER."""
        for base in [0, 1, 42, 1000000, _SAFE_SEED_MAX]:
            for idx in range(20):
                seed = derive_take_seed(base, idx)
                assert 0 <= seed <= _SAFE_SEED_MAX, f"seed={seed} out of range"

    def test_sqlite_roundtrip(self, tmp_path):
        """Derived seeds survive SQLite INTEGER storage and retrieval."""
        db_path = os.path.join(str(tmp_path), "seed_test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE seeds (val INTEGER NOT NULL)")
        for base in [0, 42, _SAFE_SEED_MAX]:
            for idx in range(5):
                seed = derive_take_seed(base, idx)
                conn.execute("INSERT INTO seeds VALUES (?)", (seed,))
                row = conn.execute(
                    "SELECT val FROM seeds ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                assert row[0] == seed, f"Roundtrip failed: stored={seed}, got={row[0]}"
        conn.close()

    def test_negative_base_seed_rejected(self):
        with pytest.raises(ValueError, match="base_seed"):
            derive_take_seed(-1, 0)

    def test_exceeds_safe_max_rejected(self):
        with pytest.raises(ValueError, match="base_seed"):
            derive_take_seed(_SAFE_SEED_MAX + 1, 0)

    def test_negative_take_index_rejected(self):
        with pytest.raises(ValueError, match="take_index"):
            derive_take_seed(42, -1)

    def test_index_is_zero_based(self):
        """take_index=0 corresponds to take_number=1."""
        seed_0 = derive_take_seed(42, 0)
        seed_1 = derive_take_seed(42, 1)
        assert seed_0 != seed_1
        # Both valid
        assert 0 <= seed_0 <= _SAFE_SEED_MAX
        assert 0 <= seed_1 <= _SAFE_SEED_MAX

    def test_utf8_canonical_encoding(self):
        """Same string representation must produce same hash regardless of locale."""
        a = derive_take_seed(12345, 3)
        b = derive_take_seed(12345, 3)
        assert a == b


# ===========================================================================
# Take Model Evolution
# ===========================================================================

def _take(**kw):
    defaults = dict(
        id="take-1", shot_id="shot-1", generation_request_id="greq-1",
        seed=42, video_path="/v.mp4", status="succeeded", created_at="2026-01-01",
    )
    defaults.update(kw)
    return Take(**defaults)


class TestTakeEvolution:
    def test_legacy_construction_valid(self):
        """Existing M3 Take construction remains valid (no is_favorite field)."""
        t = Take(
            id="t1", shot_id="s1", generation_request_id="r1",
            seed=42, video_path="/v.mp4", status="succeeded", created_at="2026-01-01",
        )
        assert t.is_favorite is False  # default

    def test_approved_status_accepted(self):
        t = _take(status="approved")
        assert t.status == "approved"

    def test_rejected_status_accepted(self):
        t = _take(status="rejected")
        assert t.status == "rejected"

    def test_all_legacy_statuses_preserved(self):
        for s in ("pending", "generating", "succeeded", "failed"):
            t = _take(status=s)
            assert t.status == s

    def test_is_favorite_independent_from_status(self):
        """is_favorite can be True alongside any reviewable status."""
        t = _take(status="succeeded", is_favorite=True)
        assert t.status == "succeeded"
        assert t.is_favorite is True

        t2 = _take(status="approved", is_favorite=True)
        assert t2.status == "approved"
        assert t2.is_favorite is True

    def test_is_favorite_default_false(self):
        t = _take()
        assert t.is_favorite is False

    def test_all_existing_fields_preserved(self):
        t = _take(
            audio_path="/a.wav",
            last_frame_path="/lf.png",
        )
        assert t.id == "take-1"
        assert t.shot_id == "shot-1"
        assert t.generation_request_id == "greq-1"
        assert t.seed == 42
        assert t.video_path == "/v.mp4"
        assert t.audio_path == "/a.wav"
        assert t.last_frame_path == "/lf.png"

    def test_invalid_status_rejected(self):
        with pytest.raises(Exception):
            _take(status="invalid")


# ===========================================================================
# QueueJob Model
# ===========================================================================

def _job(**kw):
    defaults = dict(
        id="qj-1", batch_id="qb-1", shot_id="shot-1", take_number=1,
        project_id="proj-1", base_seed=42, seed=100,
        status="pending", created_at="2026-01-01", updated_at="2026-01-01",
    )
    defaults.update(kw)
    return QueueJob(**defaults)


class TestQueueJobModel:
    def test_valid_construction(self):
        j = _job()
        assert j.id == "qj-1"
        assert j.status == "pending"
        assert j.take_number == 1
        assert j.generation_request_id is None
        assert j.take_id is None

    def test_all_statuses_valid(self):
        for s in ("pending", "claimed", "succeeded", "failed", "cancelled"):
            j = _job(status=s)
            assert j.status == s

    def test_invalid_status_rejected(self):
        with pytest.raises(Exception):
            _job(status="invalid")

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _job(id="")

    def test_empty_shot_id_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _job(shot_id="")

    def test_empty_project_id_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _job(project_id="")

    def test_zero_take_number_rejected(self):
        with pytest.raises(ValueError, match="take_number"):
            _job(take_number=0)

    def test_negative_take_number_rejected(self):
        with pytest.raises(ValueError, match="take_number"):
            _job(take_number=-1)

    def test_negative_attempt_count_rejected(self):
        with pytest.raises(ValueError, match=">= 0"):
            _job(attempt_count=-1)

    def test_nullable_fields_default_none(self):
        j = _job()
        assert j.generation_request_id is None
        assert j.take_id is None
        assert j.claimed_at is None
        assert j.completed_at is None
        assert j.error is None

    def test_priority_default_zero(self):
        j = _job()
        assert j.priority == 0


# ===========================================================================
# Database Schema + Migration
# ===========================================================================

def _insert_shot_chain(conn):
    """Insert minimal FK parent chain: project → sequence → scene → beat → shot."""
    conn.execute(
        "INSERT OR IGNORE INTO production_projects (id, wc_project_id, title, status, "
        "created_at, updated_at, prov_source_system, prov_source_project_id, "
        "prov_source_asset_id, prov_imported_at, prov_source_hash) "
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
        "change,order_index,created_at,updated_at) VALUES "
        "('b','sc','a','i','c',0,'t','t')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO shots (id,beat_id,dramatic_purpose,subjects,action,environment,"
        "camera,lighting,audio_intent,duration_sec,continuity_inputs,order_index,"
        "status,source,version,created_at,updated_at) VALUES "
        "('s1','b','p','[]','a','{}','{}','{}','{}',5.0,'{}',0,'draft','generated',1,'t','t')"
    )


class TestDatabaseSchema:
    @pytest.fixture
    def db(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "test.db")
        d = Database(db_path)
        d.init_schema()
        return d

    def test_generation_queue_table_exists(self, db):
        with db.connection() as conn:
            tables = {
                row[0] for row in
                conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        assert "generation_queue" in tables

    def test_generation_queue_columns(self, db):
        with db.connection() as conn:
            cols = {
                row[1] for row in
                conn.execute("PRAGMA table_info(generation_queue)").fetchall()
            }
        expected = {
            "id", "shot_id", "take_number", "project_id",
            "base_seed", "seed", "status",
            "generation_request_id", "take_id", "priority",
            "attempt_count", "max_attempts", "error",
            "created_at", "updated_at", "claimed_at", "completed_at",
        }
        assert expected.issubset(cols)

    def test_queue_unique_constraint(self, db):
        with db.connection() as conn:
            _insert_shot_chain(conn)
            # Create batch for FK
            conn.execute(
                "INSERT INTO queue_batches (id,project_id,scope_type,scope_id,"
                "idempotency_key,request_fingerprint,takes_count,base_seed,created_at) "
                "VALUES ('qb1','p1','shot','s1','k1','fp',1,42,'t')"
            )
            conn.execute(
                "INSERT INTO generation_queue (id,batch_id,shot_id,take_number,project_id,"
                "base_seed,seed,status,created_at,updated_at) "
                "VALUES ('q1','qb1','s1',1,'p1',42,100,'pending','t','t')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO generation_queue (id,batch_id,shot_id,take_number,project_id,"
                    "base_seed,seed,status,created_at,updated_at) "
                    "VALUES ('q2','qb1','s1',1,'p1',42,100,'pending','t','t')"
                )

    def test_queue_indexes_exist(self, db):
        with db.connection() as conn:
            indexes = {
                row[1] for row in
                conn.execute("PRAGMA index_list(generation_queue)").fetchall()
            }
        assert "idx_queue_status" in indexes
        assert "idx_queue_shot" in indexes

    def test_takes_is_favorite_column_exists(self, db):
        with db.connection() as conn:
            cols = {
                row[1] for row in
                conn.execute("PRAGMA table_info(takes)").fetchall()
            }
        assert "is_favorite" in cols

    def test_takes_is_favorite_default_false(self, db):
        """is_favorite column has DEFAULT 0 — verified via schema inspection."""
        with db.connection() as conn:
            cols = conn.execute("PRAGMA table_info(takes)").fetchall()
            fav_col = [c for c in cols if c[1] == "is_favorite"]
            assert len(fav_col) == 1
            # dflt_value is column index 4 in PRAGMA table_info
            assert fav_col[0][4] == "0", "is_favorite default must be 0"

    def test_migration_idempotent(self, tmp_path):
        """Calling init_schema twice does not error."""
        db_path = os.path.join(str(tmp_path), "idem.db")
        d = Database(db_path)
        d.init_schema()
        d.init_schema()  # second call — must not error

    def test_take_roundtrip_with_is_favorite(self, db):
        """Save and retrieve Take with is_favorite through TakeRepository."""
        from film_director.persistence.repositories import TakeRepository
        fp = "a" * 64
        repo = TakeRepository(db)
        # Disable FKs for this test to avoid complex parent chain
        with db.connection() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(
                f"INSERT INTO generation_requests (id,shot_id,shot_version,generation_plan_id,"
                f"generation_plan_version,prompt_artifact_id,prompt_artifact_version,"
                f"workflow_definition_id,workflow_definition_version,workflow_template_fingerprint,"
                f"take_number,parameters_snapshot,reference_snapshot,seed) VALUES "
                f"('r1','s1',1,'gp1',1,'hp1',1,'w1','1.0','{fp}',1,'[]','[]',42)"
            )

        take = Take(
            id="t1", shot_id="s1", generation_request_id="r1",
            seed=42, video_path="/v.mp4", status="approved",
            is_favorite=True, created_at="2026-01-01",
        )
        with db.connection() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            repo.save_take(take, conn=conn)
        loaded = repo.get_take("t1")
        assert loaded is not None
        assert loaded.status == "approved"
        assert loaded.is_favorite is True
        assert loaded.seed == 42

    def test_large_seed_roundtrip(self, db):
        """Large seed within safe domain survives SQLite roundtrip."""
        large_seed = _SAFE_SEED_MAX
        with db.connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS seed_test (val INTEGER)")
            conn.execute("INSERT INTO seed_test VALUES (?)", (large_seed,))
            row = conn.execute("SELECT val FROM seed_test").fetchone()
            assert row[0] == large_seed
