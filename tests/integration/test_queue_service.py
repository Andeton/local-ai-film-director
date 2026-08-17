"""Integration tests for M6.B — QueueService persistent idempotent enqueue.

Uses real SQLite. No ComfyUI, no generation.
"""
from __future__ import annotations

import os

import pytest

from film_director.errors import (
    QueueConflictError,
    QueueJobNotFoundError,
    QueueTransitionError,
    QueueValidationError,
)
from film_director.generation.parameter_resolver import _SAFE_SEED_MAX, derive_take_seed
from film_director.generation.queue_service import QueueService
from film_director.models.canonical import (
    Beat,
    CameraIntent,
    GenerationPlan,
    ProductionProject,
    ReferenceRequirements,
    Scene,
    Sequence,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    GenerationPlanRepository,
    ProjectRepository,
    QueueBatchRepository,
    QueueJobRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


@pytest.fixture
def env(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()

    project_repo = ProjectRepository(db)
    seq_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)
    queue_repo = QueueJobRepository(db)
    batch_repo = QueueBatchRepository(db)

    with db.connection() as conn:
        project_repo.save_project(ProductionProject(
            id="proj-1", wc_project_id="wc-p1", title="Test",
            status="active", created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        ), conn=conn)
        seq_repo.save_sequence(Sequence(
            id="seq-1", project_id="proj-1", name="Main", order_index=0,
        ), conn=conn)
        scene_repo.save_scene(Scene(
            id="scene-1", sequence_id="seq-1", wc_scene_id="wc-s1",
            name="S1", location="", description="", order_index=0,
            provenance=_prov(),
        ), conn=conn)
        beat_repo.save_beat(Beat(
            id="beat-1", scene_id="scene-1", dramatic_action="enters",
            character_intention="investigate", change="finds clue",
            order_index=0, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        shot_repo.save_shot(ShotSpecificationV1(
            id="shot-1", beat_id="beat-1", dramatic_purpose="tension",
            subjects=[ShotSubject(character_id="c1", name="A", ref_images=[])],
            action="walks", camera=CameraIntent(shot_size="medium"),
            duration_sec=5.0, order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        plan_repo.save_plan(GenerationPlan(
            id="plan-1", shot_id="shot-1", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, seed_policy="fixed", seed=42,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        shot_repo.save_shot(ShotSpecificationV1(
            id="shot-2", beat_id="beat-1", dramatic_purpose="reveal",
            subjects=[ShotSubject(character_id="c1", name="A", ref_images=[])],
            action="looks up", camera=CameraIntent(shot_size="close_up"),
            duration_sec=5.0, order_index=1, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        plan_repo.save_plan(GenerationPlan(
            id="plan-2", shot_id="shot-2", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, seed_policy="random",
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)

    svc = QueueService(
        db=db, queue_repo=queue_repo, batch_repo=batch_repo,
        shot_repo=shot_repo, plan_repo=plan_repo, scene_repo=scene_repo,
        seq_repo=seq_repo, beat_repo=beat_repo,
    )
    return {"svc": svc, "db": db, "queue_repo": queue_repo, "batch_repo": batch_repo}


# ---------------------------------------------------------------------------
# Shot enqueue
# ---------------------------------------------------------------------------

class TestShotEnqueue:
    def test_default_three_jobs(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        assert len(jobs) == 3
        for j in jobs:
            assert j.status == "pending"
            assert j.project_id == "proj-1"
            assert j.batch_id != ""

    def test_explicit_takes_count(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", "key-1", takes_count=5, base_seed=42)
        assert len(jobs) == 5

    def test_different_persisted_seeds(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        seeds = [j.seed for j in jobs]
        assert len(set(seeds)) == 3

    def test_deterministic_seeds(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        for j in jobs:
            expected = derive_take_seed(42, j.take_number - 1)
            assert j.seed == expected
            assert j.base_seed == 42

    def test_missing_shot(self, env):
        with pytest.raises(QueueValidationError, match="Shot not found"):
            env["svc"].enqueue_shot("nonexistent", "key-1")

    def test_missing_plan(self, env):
        with env["db"].connection() as conn:
            ShotRepository(env["db"]).save_shot(ShotSpecificationV1(
                id="shot-noplan", beat_id="beat-1", dramatic_purpose="t",
                subjects=[], action="t", camera=CameraIntent(shot_size="wide"),
                duration_sec=5.0, order_index=9, version=1,
                created_at="t", updated_at="t",
            ), conn=conn)
        with pytest.raises(QueueValidationError, match="GenerationPlan"):
            env["svc"].enqueue_shot("shot-noplan", "key-np")

    def test_invalid_takes_count(self, env):
        with pytest.raises(QueueValidationError):
            env["svc"].enqueue_shot("shot-1", "key-1", takes_count=0)
        with pytest.raises(QueueValidationError):
            env["svc"].enqueue_shot("shot-1", "key-1", takes_count=11)

    def test_new_key_creates_additional_jobs(self, env):
        j1 = env["svc"].enqueue_shot("shot-1", "key-1", takes_count=2, base_seed=42)
        j2 = env["svc"].enqueue_shot("shot-1", "key-2", takes_count=2, base_seed=42)
        all_ids = {j.id for j in j1} | {j.id for j in j2}
        assert len(all_ids) == 4
        # Take numbers must not overlap
        tns = {j.take_number for j in j1} | {j.take_number for j in j2}
        assert len(tns) == 4


# ---------------------------------------------------------------------------
# Shot idempotency
# ---------------------------------------------------------------------------

class TestShotIdempotency:
    def test_same_key_same_payload_returns_same_jobs(self, env):
        first = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        second = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        assert [j.id for j in first] == [j.id for j in second]

    def test_same_key_after_db_reopen(self, env):
        first = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        # Simulate restart: create new service with same DB
        db2 = Database(env["db"]._db_path)
        svc2 = QueueService(
            db=db2, queue_repo=QueueJobRepository(db2),
            batch_repo=QueueBatchRepository(db2),
            shot_repo=ShotRepository(db2), plan_repo=GenerationPlanRepository(db2),
            scene_repo=SceneRepository(db2), seq_repo=SequenceRepository(db2),
            beat_repo=BeatRepository(db2),
        )
        second = svc2.enqueue_shot("shot-1", "key-1", base_seed=42)
        assert [j.id for j in first] == [j.id for j in second]

    def test_same_key_after_jobs_claimed(self, env):
        first = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        env["queue_repo"].update_status(first[0].id, "claimed")
        second = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        assert [j.id for j in first] == [j.id for j in second]

    def test_same_key_after_jobs_succeeded(self, env):
        first = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        env["queue_repo"].update_status(first[0].id, "succeeded")
        second = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        assert [j.id for j in first] == [j.id for j in second]

    def test_same_key_after_jobs_cancelled(self, env):
        first = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        env["svc"].cancel_job(first[0].id)
        second = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        assert [j.id for j in first] == [j.id for j in second]

    def test_no_replacement_inserts(self, env):
        env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        all_jobs = env["queue_repo"].list_by_shot("shot-1")
        assert len(all_jobs) == 3


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

class TestConflictDetection:
    def test_same_key_different_takes_count(self, env):
        env["svc"].enqueue_shot("shot-1", "key-1", takes_count=3, base_seed=42)
        with pytest.raises(QueueConflictError):
            env["svc"].enqueue_shot("shot-1", "key-1", takes_count=5, base_seed=42)

    def test_same_key_different_base_seed(self, env):
        env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        with pytest.raises(QueueConflictError):
            env["svc"].enqueue_shot("shot-1", "key-1", base_seed=99)

    def test_same_key_different_priority(self, env):
        env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42, priority=0)
        with pytest.raises(QueueConflictError):
            env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42, priority=5)

    def test_conflict_creates_nothing(self, env):
        env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        try:
            env["svc"].enqueue_shot("shot-1", "key-1", base_seed=99)
        except QueueConflictError:
            pass
        assert len(env["queue_repo"].list_by_shot("shot-1")) == 3


# ---------------------------------------------------------------------------
# Scene enqueue + idempotency
# ---------------------------------------------------------------------------

class TestSceneEnqueue:
    def test_enqueues_all_eligible(self, env):
        jobs = env["svc"].enqueue_scene("scene-1", "skey-1", takes_per_shot=2, base_seed=42)
        assert len(jobs) == 4  # 2 shots x 2 takes

    def test_scene_retry_returns_original_jobs(self, env):
        first = env["svc"].enqueue_scene("scene-1", "skey-1", base_seed=42)
        second = env["svc"].enqueue_scene("scene-1", "skey-1", base_seed=42)
        assert [j.id for j in first] == [j.id for j in second]
        assert len(second) > 0  # NOT empty

    def test_scene_retry_after_status_changes(self, env):
        first = env["svc"].enqueue_scene("scene-1", "skey-1", base_seed=42)
        env["queue_repo"].update_status(first[0].id, "succeeded")
        second = env["svc"].enqueue_scene("scene-1", "skey-1", base_seed=42)
        assert [j.id for j in first] == [j.id for j in second]

    def test_scene_conflict(self, env):
        env["svc"].enqueue_scene("scene-1", "skey-1", takes_per_shot=3, base_seed=42)
        with pytest.raises(QueueConflictError):
            env["svc"].enqueue_scene("scene-1", "skey-1", takes_per_shot=5, base_seed=42)

    def test_new_scene_key_creates_new_batch(self, env):
        j1 = env["svc"].enqueue_scene("scene-1", "skey-1", takes_per_shot=2, base_seed=42)
        j2 = env["svc"].enqueue_scene("scene-1", "skey-2", takes_per_shot=2, base_seed=42)
        ids1 = {j.id for j in j1}
        ids2 = {j.id for j in j2}
        assert ids1.isdisjoint(ids2)

    def test_missing_scene(self, env):
        with pytest.raises(QueueValidationError, match="Scene not found"):
            env["svc"].enqueue_scene("nonexistent", "skey-1")

    def test_project_isolation(self, env):
        jobs = env["svc"].enqueue_scene("scene-1", "skey-1", base_seed=42)
        assert all(j.project_id == "proj-1" for j in jobs)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:
    def test_pending_cancelled(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        result = env["svc"].cancel_job(jobs[0].id)
        assert result.status == "cancelled"

    def test_idempotent(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        env["svc"].cancel_job(jobs[0].id)
        result = env["svc"].cancel_job(jobs[0].id)
        assert result.status == "cancelled"

    def test_claimed_rejected(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        env["queue_repo"].update_status(jobs[0].id, "claimed")
        with pytest.raises(QueueTransitionError):
            env["svc"].cancel_job(jobs[0].id)

    def test_seed_preserved_after_cancel(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        original_seed = jobs[0].seed
        env["svc"].cancel_job(jobs[0].id)
        loaded = env["queue_repo"].get(jobs[0].id)
        assert loaded.seed == original_seed
        assert loaded.base_seed == 42

    def test_missing_job(self, env):
        with pytest.raises(QueueJobNotFoundError):
            env["svc"].cancel_job("nonexistent")


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class TestRepository:
    def test_list_by_batch(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        batch_id = jobs[0].batch_id
        by_batch = env["queue_repo"].list_by_batch(batch_id)
        assert len(by_batch) == 3
        assert all(j.batch_id == batch_id for j in by_batch)

    def test_batch_roundtrip(self, env):
        env["svc"].enqueue_shot("shot-1", "key-1", base_seed=42)
        batch = env["batch_repo"].get_by_key("proj-1", "key-1")
        assert batch is not None
        assert batch.scope_type == "shot"
        assert batch.scope_id == "shot-1"
        assert batch.base_seed == 42
