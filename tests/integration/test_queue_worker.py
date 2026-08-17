"""Integration tests for M6.C — QueueWorker claim, execute, and recovery.

Uses real SQLite, mocked ComfyUI. No live generation.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from film_director.errors import GenerationError
from film_director.generation.comfyui_adapter import (
    ComfyUIAdapter,
    ComfyUIGenerationResult,
    ComfyUIOutputRef,
)
from film_director.generation.generation_request import GenerationRequest, Take
from film_director.generation.generation_service import GenerationService
from film_director.generation.queue_service import QueueService
from film_director.generation.queue_worker import QueueWorker
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
from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    GenerationRequestRepository,
    H3PromptRepository,
    ProjectRepository,
    QueueBatchRepository,
    QueueJobRepository,
    ReferenceAssetRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
    TakeRepository,
)

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


def _compute_sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _create_synthetic_video(path: str):
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "color=c=green:s=64x64:d=0.5:r=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        pytest.skip("FFmpeg not available")


def _fake_comfyui(tmp_path):
    mock = MagicMock(spec=ComfyUIAdapter)
    video_path = os.path.join(str(tmp_path), "comfyui_output.mp4")
    _create_synthetic_video(video_path)
    mock.upload_image.return_value = "uploaded.png"
    mock.submit.return_value = "fake-prompt-id"
    mock.monitor.return_value = None
    mock.get_result.return_value = ComfyUIGenerationResult(
        prompt_id="fake-prompt-id", output_node_id="92",
        outputs=[ComfyUIOutputRef("out.mp4", "m3", "output")],
    )
    mock.download_output.side_effect = lambda ref, dest: shutil.copy2(video_path, dest) or dest
    return mock


@pytest.fixture
def env(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()

    storage_root = os.path.join(str(tmp_path), "storage")
    os.makedirs(storage_root, exist_ok=True)

    # Create ref image inside storage root
    ref_dir = os.path.join(storage_root, "references", "proj-1")
    os.makedirs(ref_dir, exist_ok=True)
    ref_data = b"fake ref image"
    ref_path = os.path.join(ref_dir, "ref.png")
    with open(ref_path, "wb") as f:
        f.write(ref_data)
    ref_sha = _compute_sha(ref_data)

    project_repo = ProjectRepository(db)
    seq_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)
    char_repo = CharacterRepository(db)
    ref_repo = ReferenceAssetRepository(db)
    queue_repo = QueueJobRepository(db)
    batch_repo = QueueBatchRepository(db)
    request_repo = GenerationRequestRepository(db)
    take_repo = TakeRepository(db)

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
            subjects=[ShotSubject(character_id="char-1", name="Alice", ref_images=[])],
            action="walks", camera=CameraIntent(shot_size="medium"),
            audio_intent={"ambient": "silence"}, duration_sec=5.0,
            order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        char_repo.save_character(CharacterReference(
            id="char-1", project_id="proj-1", wc_character_id="wc-c1",
            name="Alice", description="Detective", appearance="dark hair",
            provenance=_prov(),
        ), conn=conn)
        plan_repo.save_plan(GenerationPlan(
            id="plan-1", shot_id="shot-1", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, resolution_intent={"aspect": "16:9"},
            seed_policy="fixed", seed=42,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        ref_repo.save(ReferenceAsset(
            id="ref-1", project_id="proj-1", character_id="char-1",
            kind=ReferenceKind.CHARACTER_BODY,
            source=ReferenceSource.USER_UPLOAD,
            managed_path=ref_path, content_sha256=ref_sha,
            source_provenance="test",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=64, height=64,
            created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
        ), conn=conn)

    mock_comfyui = _fake_comfyui(tmp_path)
    gen_service = GenerationService(
        db=db, comfyui=mock_comfyui,
        storage_root=storage_root, project_root=WORKTREE,
    )
    queue_svc = QueueService(
        db=db, queue_repo=queue_repo, batch_repo=batch_repo,
        shot_repo=shot_repo, plan_repo=plan_repo, scene_repo=scene_repo,
        seq_repo=seq_repo, beat_repo=beat_repo,
    )
    worker = QueueWorker(
        db=db, queue_repo=queue_repo,
        generation_service=gen_service,
        request_repo=request_repo, take_repo=take_repo,
    )

    return {
        "db": db, "queue_repo": queue_repo, "queue_svc": queue_svc,
        "worker": worker, "request_repo": request_repo,
        "take_repo": take_repo, "mock_comfyui": mock_comfyui,
        "gen_service": gen_service,
    }


# ---------------------------------------------------------------------------
# Atomic claim
# ---------------------------------------------------------------------------

class TestAtomicClaim:
    def test_claims_pending_job(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=2, base_seed=42)
        with env["db"].connection() as conn:
            job = env["queue_repo"].claim_next(conn)
        assert job is not None
        assert job.status == "claimed"
        assert job.attempt_count == 1

    def test_empty_queue_returns_none(self, env):
        with env["db"].connection() as conn:
            job = env["queue_repo"].claim_next(conn)
        assert job is None

    def test_priority_ordering(self, env):
        # Low priority
        env["queue_svc"].enqueue_shot("shot-1", "key-lo", takes_count=1, base_seed=42, priority=0)
        # High priority (needs different shot to avoid take_number conflict)
        # Actually, same shot different key gives different take_numbers
        env["queue_svc"].enqueue_shot("shot-1", "key-hi", takes_count=1, base_seed=42, priority=10)

        with env["db"].connection() as conn:
            job = env["queue_repo"].claim_next(conn)
        assert job.priority == 10  # high priority first

    def test_max_attempts_respected(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        # Claim it (attempt_count becomes 1, max_attempts=1)
        with env["db"].connection() as conn:
            job = env["queue_repo"].claim_next(conn)
        assert job is not None
        # Mark failed and try to reclaim
        env["queue_repo"].update_status(job.id, "pending")
        # attempt_count=1, max_attempts=1 → should NOT be claimable
        with env["db"].connection() as conn:
            job2 = env["queue_repo"].claim_next(conn)
        assert job2 is None


# ---------------------------------------------------------------------------
# Queued generation
# ---------------------------------------------------------------------------

class TestQueuedGeneration:
    def test_worker_executes_enqueued_job(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        completed = env["worker"].run_once()
        assert completed == 1

        # Verify job succeeded
        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "succeeded"
        assert jobs[0].generation_request_id is not None
        assert jobs[0].take_id is not None

    def test_persisted_seed_reaches_request(self, env):
        jobs = env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        expected_seed = jobs[0].seed
        env["worker"].run_once()

        req = env["request_repo"].get_requests_by_shot("shot-1")
        assert len(req) >= 1
        assert req[-1].seed == expected_seed

    def test_persisted_take_number_reaches_request(self, env):
        jobs = env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        expected_tn = jobs[0].take_number
        env["worker"].run_once()

        req = env["request_repo"].get_requests_by_shot("shot-1")
        assert req[-1].take_number == expected_tn

    def test_multiple_takes_different_seeds(self, env):
        jobs = env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=3, base_seed=42)
        seeds = {j.seed for j in jobs}
        assert len(seeds) == 3

        for _ in range(3):
            env["worker"].run_once()

        takes = env["take_repo"].get_takes_by_shot("shot-1")
        assert len(takes) == 3
        take_seeds = {t.seed for t in takes}
        assert take_seeds == seeds

    def test_empty_queue_returns_zero(self, env):
        assert env["worker"].run_once() == 0

    def test_legacy_generate_shot_still_works(self, env):
        """M3 backward compat: generate_shot still works."""
        take = env["gen_service"].generate_shot("shot-1")
        assert take.status == "succeeded"


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

class TestFailureHandling:
    def test_comfyui_failure_marks_job_failed(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        env["mock_comfyui"].submit.side_effect = Exception("ComfyUI down")
        env["worker"].run_once()

        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "failed"
        assert "ComfyUI down" in (jobs[0].error or "")

    def test_failed_job_not_reclaimable(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        env["mock_comfyui"].submit.side_effect = Exception("fail")
        env["worker"].run_once()

        # No more claimable jobs
        assert env["worker"].run_once() == 0


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

class TestRecovery:
    def test_no_claimed_jobs(self, env):
        assert env["worker"].recover() == 0

    def test_claimed_no_request_marks_failed(self, env):
        """Job claimed but no request created → mark failed."""
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        with env["db"].connection() as conn:
            env["queue_repo"].claim_next(conn)

        recovered = env["worker"].recover()
        assert recovered == 1

        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "failed"
        assert "no generation request" in jobs[0].error.lower()

    def test_claimed_request_succeeded_take_exists(self, env):
        """Job claimed, request succeeded, Take exists → reconcile succeeded."""
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        # Execute normally (creates request + Take)
        env["worker"].run_once()

        # Manually reset job to claimed to simulate crash after finalization
        jobs = env["queue_repo"].list_by_shot("shot-1")
        env["queue_repo"].update_status(jobs[0].id, "claimed")

        recovered = env["worker"].recover()
        assert recovered == 1

        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "succeeded"

    def test_recovery_idempotent(self, env):
        """Running recovery twice produces the same result."""
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        with env["db"].connection() as conn:
            env["queue_repo"].claim_next(conn)

        env["worker"].recover()
        env["worker"].recover()

        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "failed"

    def test_recovery_after_db_reopen(self, env):
        """Recovery works after process restart (DB reopen)."""
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        with env["db"].connection() as conn:
            env["queue_repo"].claim_next(conn)

        # Simulate restart: new DB connection + new worker
        db2 = Database(env["db"]._db_path)
        queue_repo2 = QueueJobRepository(db2)
        worker2 = QueueWorker(
            db=db2, queue_repo=queue_repo2,
            generation_service=env["gen_service"],
            request_repo=GenerationRequestRepository(db2),
            take_repo=TakeRepository(db2),
        )
        recovered = worker2.recover()
        assert recovered == 1


# ---------------------------------------------------------------------------
# Concurrency setting
# ---------------------------------------------------------------------------

class TestConcurrencySetting:
    def test_default_concurrency(self):
        from film_director.config import Settings
        s = Settings(wc_database_path="/tmp/test.db")
        assert s.queue_worker_concurrency == 1
