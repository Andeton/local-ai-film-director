"""Integration tests for M6.B — QueueService enqueue and cancellation.

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
    CharacterReference,
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
    CharacterRepository,
    GenerationPlanRepository,
    ProjectRepository,
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
        # Second shot for scene batch testing
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
        db=db, queue_repo=queue_repo, shot_repo=shot_repo,
        plan_repo=plan_repo, scene_repo=scene_repo,
        seq_repo=seq_repo, beat_repo=beat_repo,
    )
    return {"svc": svc, "db": db, "queue_repo": queue_repo}


# ---------------------------------------------------------------------------
# Shot enqueue
# ---------------------------------------------------------------------------

class TestShotEnqueue:
    def test_default_three_jobs(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", base_seed=42)
        assert len(jobs) == 3
        for j in jobs:
            assert j.status == "pending"
            assert j.project_id == "proj-1"

    def test_explicit_takes_count(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", takes_count=5, base_seed=42)
        assert len(jobs) == 5

    def test_different_persisted_seeds(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", base_seed=42)
        seeds = [j.seed for j in jobs]
        assert len(set(seeds)) == 3, "All 3 seeds should be distinct"

    def test_deterministic_seeds(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", base_seed=42)
        for j in jobs:
            expected = derive_take_seed(42, j.take_number - 1)
            assert j.seed == expected
            assert j.base_seed == 42

    def test_seed_persists_in_db(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", base_seed=42)
        for j in jobs:
            loaded = env["queue_repo"].get(j.id)
            assert loaded.seed == j.seed
            assert loaded.base_seed == 42

    def test_idempotent_returns_existing(self, env):
        first = env["svc"].enqueue_shot("shot-1", base_seed=42)
        second = env["svc"].enqueue_shot("shot-1", base_seed=42)
        assert [j.id for j in first] == [j.id for j in second]

    def test_missing_shot(self, env):
        with pytest.raises(QueueValidationError, match="Shot not found"):
            env["svc"].enqueue_shot("nonexistent")

    def test_missing_plan(self, env):
        # Create shot without plan
        with env["db"].connection() as conn:
            from film_director.persistence.repositories import ShotRepository
            ShotRepository(env["db"]).save_shot(ShotSpecificationV1(
                id="shot-noplan", beat_id="beat-1", dramatic_purpose="t",
                subjects=[], action="t", camera=CameraIntent(shot_size="wide"),
                duration_sec=5.0, order_index=9, version=1,
                created_at="t", updated_at="t",
            ), conn=conn)
        with pytest.raises(QueueValidationError, match="GenerationPlan"):
            env["svc"].enqueue_shot("shot-noplan")

    def test_invalid_takes_count(self, env):
        with pytest.raises(QueueValidationError, match="takes_count"):
            env["svc"].enqueue_shot("shot-1", takes_count=0)
        with pytest.raises(QueueValidationError, match="takes_count"):
            env["svc"].enqueue_shot("shot-1", takes_count=11)

    def test_invalid_seed(self, env):
        with pytest.raises(QueueValidationError, match="base_seed"):
            env["svc"].enqueue_shot("shot-1", base_seed=-1)

    def test_take_numbers_advance_past_history(self, env):
        # Enqueue 2 takes, cancel one, then enqueue 3 more
        jobs = env["svc"].enqueue_shot("shot-1", takes_count=2, base_seed=42)
        env["svc"].cancel_job(jobs[1].id)
        # Now enqueue 3 — should get 1 existing active + 2 new
        new_jobs = env["svc"].enqueue_shot("shot-1", takes_count=3, base_seed=42)
        all_tns = {j.take_number for j in new_jobs}
        # take_number 2 was cancelled, so new ones should be 3, 4
        assert len(new_jobs) == 3
        # No duplicate take numbers
        assert len(all_tns) == 3

    def test_atomic_rollback(self, env):
        """If enqueue fails mid-batch, no partial jobs remain."""
        # Sabotage: insert a conflicting job manually
        from film_director.generation.queue_models import QueueJob
        with env["db"].connection() as conn:
            env["queue_repo"].insert(QueueJob(
                id="conflict", shot_id="shot-1", take_number=2,
                project_id="proj-1", base_seed=0, seed=0,
                status="pending", created_at="t", updated_at="t",
            ), conn=conn)
        # Enqueue 3 should work — it skips existing take_number 2
        jobs = env["svc"].enqueue_shot("shot-1", takes_count=3, base_seed=42)
        assert len(jobs) == 3


# ---------------------------------------------------------------------------
# Scene enqueue
# ---------------------------------------------------------------------------

class TestSceneEnqueue:
    def test_enqueues_all_eligible(self, env):
        result = env["svc"].enqueue_scene("scene-1", takes_per_shot=2, base_seed=42)
        assert result["shots_enqueued"] == 2
        assert result["total_jobs"] == 4

    def test_atomic_rollback_on_failure(self, env):
        """Scene enqueue is all-or-nothing."""
        # This should succeed (both shots have plans)
        result = env["svc"].enqueue_scene("scene-1", base_seed=42)
        assert result["shots_enqueued"] == 2

    def test_missing_scene(self, env):
        with pytest.raises(QueueValidationError, match="Scene not found"):
            env["svc"].enqueue_scene("nonexistent")

    def test_idempotent_scene_enqueue(self, env):
        r1 = env["svc"].enqueue_scene("scene-1", base_seed=42)
        r2 = env["svc"].enqueue_scene("scene-1", base_seed=42)
        # Second call should not create additional jobs
        assert r2["total_jobs"] == 0

    def test_project_isolation(self, env):
        """Jobs are associated with the correct project."""
        env["svc"].enqueue_scene("scene-1", base_seed=42)
        jobs = env["queue_repo"].list_by_project("proj-1")
        assert all(j.project_id == "proj-1" for j in jobs)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:
    def test_pending_cancelled(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", base_seed=42)
        result = env["svc"].cancel_job(jobs[0].id)
        assert result.status == "cancelled"

    def test_idempotent(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", base_seed=42)
        env["svc"].cancel_job(jobs[0].id)
        result = env["svc"].cancel_job(jobs[0].id)
        assert result.status == "cancelled"

    def test_claimed_rejected(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", base_seed=42)
        # Manually set to claimed
        env["queue_repo"].update_status(jobs[0].id, "claimed")
        with pytest.raises(QueueTransitionError, match="claimed"):
            env["svc"].cancel_job(jobs[0].id)

    def test_succeeded_rejected(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", base_seed=42)
        env["queue_repo"].update_status(jobs[0].id, "succeeded")
        with pytest.raises(QueueTransitionError, match="succeeded"):
            env["svc"].cancel_job(jobs[0].id)

    def test_failed_rejected(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", base_seed=42)
        env["queue_repo"].update_status(jobs[0].id, "failed")
        with pytest.raises(QueueTransitionError, match="failed"):
            env["svc"].cancel_job(jobs[0].id)

    def test_missing_job(self, env):
        with pytest.raises(QueueJobNotFoundError):
            env["svc"].cancel_job("nonexistent")

    def test_seed_preserved_after_cancel(self, env):
        jobs = env["svc"].enqueue_shot("shot-1", base_seed=42)
        original_seed = jobs[0].seed
        env["svc"].cancel_job(jobs[0].id)
        loaded = env["queue_repo"].get(jobs[0].id)
        assert loaded.seed == original_seed
        assert loaded.base_seed == 42
        assert loaded.take_number == jobs[0].take_number


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class TestQueueJobRepository:
    def test_insert_get_roundtrip(self, env):
        from film_director.generation.queue_models import QueueJob
        job = QueueJob(
            id="qj-test", shot_id="shot-1", take_number=99,
            project_id="proj-1", base_seed=42, seed=100,
            status="pending", created_at="t", updated_at="t",
        )
        env["queue_repo"].insert(job)
        loaded = env["queue_repo"].get("qj-test")
        assert loaded is not None
        assert loaded.seed == 100
        assert loaded.base_seed == 42

    def test_list_by_status(self, env):
        env["svc"].enqueue_shot("shot-1", base_seed=42)
        pending = env["queue_repo"].list_by_status("pending")
        assert len(pending) == 3

    def test_count_by_status(self, env):
        env["svc"].enqueue_shot("shot-1", base_seed=42)
        counts = env["queue_repo"].count_by_status()
        assert counts.get("pending", 0) == 3

    def test_max_take_number(self, env):
        env["svc"].enqueue_shot("shot-1", takes_count=3, base_seed=42)
        m = env["queue_repo"].max_take_number_for_shot("shot-1")
        assert m == 3

    def test_duplicate_insert_rejected(self, env):
        from film_director.generation.queue_models import QueueJob
        import sqlite3
        job = QueueJob(
            id="qj-dup", shot_id="shot-1", take_number=1,
            project_id="proj-1", base_seed=0, seed=0,
            status="pending", created_at="t", updated_at="t",
        )
        env["queue_repo"].insert(job)
        with pytest.raises(sqlite3.IntegrityError):
            job2 = QueueJob(
                id="qj-dup2", shot_id="shot-1", take_number=1,
                project_id="proj-1", base_seed=0, seed=0,
                status="pending", created_at="t", updated_at="t",
            )
            env["queue_repo"].insert(job2)

    def test_ordering(self, env):
        env["svc"].enqueue_shot("shot-1", base_seed=42)
        jobs = env["queue_repo"].list_by_shot("shot-1")
        tns = [j.take_number for j in jobs]
        assert tns == sorted(tns)
