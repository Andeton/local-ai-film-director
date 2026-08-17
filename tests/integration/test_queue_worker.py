"""Integration tests for M6.C — QueueWorker claim, execute, recovery, concurrency.

Uses real SQLite, mocked ComfyUI. No live generation.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from unittest.mock import MagicMock

import pytest

from film_director.errors import GenerationError
from film_director.generation.comfyui_adapter import (
    ComfyUIAdapter,
    ComfyUIGenerationResult,
    ComfyUIOutputRef,
    ComfyUIResultError,
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
        comfyui=mock_comfyui,
    )

    return {
        "db": db, "queue_repo": queue_repo, "queue_svc": queue_svc,
        "worker": worker, "request_repo": request_repo,
        "take_repo": take_repo, "mock_comfyui": mock_comfyui,
        "gen_service": gen_service, "storage_root": storage_root,
    }


# ---------------------------------------------------------------------------
# Atomic claim
# ---------------------------------------------------------------------------

class TestAtomicClaim:
    def test_claims_pending_job(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        with env["db"].connection() as conn:
            job = env["queue_repo"].claim_next(conn)
        assert job is not None
        assert job.status == "claimed"
        assert job.attempt_count == 1

    def test_empty_queue_returns_none(self, env):
        with env["db"].connection() as conn:
            job = env["queue_repo"].claim_next(conn)
        assert job is None

    def test_max_attempts_respected(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        with env["db"].connection() as conn:
            env["queue_repo"].claim_next(conn)
        # Reset to pending but attempt_count=1, max_attempts=1
        jobs = env["queue_repo"].list_by_shot("shot-1")
        env["queue_repo"].update_status(jobs[0].id, "pending")
        with env["db"].connection() as conn:
            job2 = env["queue_repo"].claim_next(conn)
        assert job2 is None


# ---------------------------------------------------------------------------
# Queued execution with atomic finalization
# ---------------------------------------------------------------------------

class TestQueuedExecution:
    def test_worker_executes_enqueued_job(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        completed = env["worker"].run_once()
        assert completed == 1
        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "succeeded"
        assert jobs[0].generation_request_id is not None
        assert jobs[0].take_id is not None

    def test_persisted_seed_reaches_request(self, env):
        jobs = env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        expected_seed = jobs[0].seed
        env["worker"].run_once()
        req = env["request_repo"].get_requests_by_shot("shot-1")
        assert req[-1].seed == expected_seed

    def test_persisted_take_number_reaches_request(self, env):
        jobs = env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        env["worker"].run_once()
        req = env["request_repo"].get_requests_by_shot("shot-1")
        assert req[-1].take_number == jobs[0].take_number

    def test_multiple_takes_different_seeds(self, env):
        jobs = env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=3, base_seed=42)
        seeds = {j.seed for j in jobs}
        for _ in range(3):
            env["worker"].run_once()
        takes = env["take_repo"].get_takes_by_shot("shot-1")
        assert len(takes) == 3
        assert {t.seed for t in takes} == seeds

    def test_legacy_generate_shot_still_works(self, env):
        take = env["gen_service"].generate_shot("shot-1")
        assert take.status == "succeeded"

    def test_atomic_finalization_includes_queue_job(self, env):
        """Take, request, and QueueJob all committed atomically."""
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        env["worker"].run_once()
        jobs = env["queue_repo"].list_by_shot("shot-1")
        job = jobs[0]
        assert job.status == "succeeded"
        # Verify Take exists and links back
        take = env["take_repo"].get_take(job.take_id)
        assert take is not None
        assert take.generation_request_id == job.generation_request_id


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
        assert env["worker"].run_once() == 0


# ---------------------------------------------------------------------------
# Recovery: false-success prevention
# ---------------------------------------------------------------------------

class TestRecoveryFalseSuccess:
    def test_request_succeeded_no_take_is_invariant_failure(self, env):
        """Request succeeded but no Take → NEVER succeeded, invariant failure."""
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        env["worker"].run_once()
        jobs = env["queue_repo"].list_by_shot("shot-1")
        job = jobs[0]
        # Delete the Take to simulate crash between request success and Take insert
        with env["db"].connection() as conn:
            conn.execute("DELETE FROM takes WHERE id = ?", (job.take_id,))
        # Reset job to claimed
        env["queue_repo"].update_status(job.id, "claimed")
        env["worker"].recover()
        recovered = env["queue_repo"].get(job.id)
        assert recovered.status == "failed"
        assert "invariant" in recovered.error.lower()

    def test_request_succeeded_valid_take_reconciles(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        env["worker"].run_once()
        jobs = env["queue_repo"].list_by_shot("shot-1")
        env["queue_repo"].update_status(jobs[0].id, "claimed")
        env["worker"].recover()
        assert env["queue_repo"].get(jobs[0].id).status == "succeeded"

    def test_request_succeeded_missing_video_is_failure(self, env):
        """Request succeeded, Take exists, but video file missing → failure."""
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        env["worker"].run_once()
        jobs = env["queue_repo"].list_by_shot("shot-1")
        job = jobs[0]
        take = env["take_repo"].get_take(job.take_id)
        # Delete the video file
        if os.path.exists(take.video_path):
            os.unlink(take.video_path)
        env["queue_repo"].update_status(job.id, "claimed")
        env["worker"].recover()
        assert env["queue_repo"].get(job.id).status == "failed"
        assert "missing" in env["queue_repo"].get(job.id).error.lower()


# ---------------------------------------------------------------------------
# Recovery: general matrix
# ---------------------------------------------------------------------------

class TestRecoveryMatrix:
    def test_claimed_no_request_fails(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        with env["db"].connection() as conn:
            env["queue_repo"].claim_next(conn)
        env["worker"].recover()
        assert env["queue_repo"].list_by_shot("shot-1")[0].status == "failed"

    def test_claimed_request_failed_fails(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        env["mock_comfyui"].submit.side_effect = Exception("fail")
        env["worker"].run_once()
        # Reset to claimed
        jobs = env["queue_repo"].list_by_shot("shot-1")
        env["queue_repo"].update_status(jobs[0].id, "claimed")
        env["worker"].recover()
        assert env["queue_repo"].get(jobs[0].id).status == "failed"

    def test_recovery_idempotent(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        with env["db"].connection() as conn:
            env["queue_repo"].claim_next(conn)
        env["worker"].recover()
        env["worker"].recover()
        assert env["queue_repo"].list_by_shot("shot-1")[0].status == "failed"

    def test_recovery_after_db_reopen(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        with env["db"].connection() as conn:
            env["queue_repo"].claim_next(conn)
        db2 = Database(env["db"]._db_path)
        worker2 = QueueWorker(
            db=db2, queue_repo=QueueJobRepository(db2),
            generation_service=env["gen_service"],
            request_repo=GenerationRequestRepository(db2),
            take_repo=TakeRepository(db2),
        )
        assert worker2.recover() == 1


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_default_concurrency_1(self):
        from film_director.config import Settings
        s = Settings(wc_database_path="/tmp/test.db")
        assert s.queue_worker_concurrency == 1

    def test_invalid_concurrency_zero(self):
        with pytest.raises(ValueError, match="concurrency"):
            QueueWorker(
                db=MagicMock(), queue_repo=MagicMock(),
                generation_service=MagicMock(),
                request_repo=MagicMock(), take_repo=MagicMock(),
                concurrency=0,
            )

    def test_invalid_concurrency_over_max(self):
        with pytest.raises(ValueError, match="concurrency"):
            QueueWorker(
                db=MagicMock(), queue_repo=MagicMock(),
                generation_service=MagicMock(),
                request_repo=MagicMock(), take_repo=MagicMock(),
                concurrency=99,
            )

    def test_run_available_processes_all(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=3, base_seed=42)
        total = env["worker"].run_available()
        assert total == 3
        takes = env["take_repo"].get_takes_by_shot("shot-1")
        assert len(takes) == 3

    def test_stop_prevents_new_claims(self, env):
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=3, base_seed=42)
        env["worker"].run_once()
        env["worker"].stop()
        assert env["worker"].run_once() == 0
        # Only 1 take created
        takes = env["take_repo"].get_takes_by_shot("shot-1")
        assert len(takes) == 1

    def test_recovery_runs_before_claims_in_run_available(self, env):
        """run_available calls recover() before claiming."""
        env["queue_svc"].enqueue_shot("shot-1", "key-1", takes_count=1, base_seed=42)
        with env["db"].connection() as conn:
            env["queue_repo"].claim_next(conn)
        # run_available should recover the orphan, then find no pending
        total = env["worker"].run_available()
        assert total == 0
        assert env["queue_repo"].list_by_shot("shot-1")[0].status == "failed"


# ---------------------------------------------------------------------------
# Prompt-ID recovery
# ---------------------------------------------------------------------------

class TestPromptIdRecovery:
    def _setup_claimed_with_prompt(self, env):
        """Enqueue, run (creates request+prompt_id), reset to claimed.

        Simulates crash after ComfyUI completed but before finalization:
        Take row deleted, request reset to queued, job reset to claimed,
        final media directory cleaned up.
        """
        env["queue_svc"].enqueue_shot("shot-1", "key-prompt", takes_count=1, base_seed=42)
        env["worker"].run_once()
        jobs = env["queue_repo"].list_by_shot("shot-1")
        job = jobs[0]
        take = env["take_repo"].get_take(job.take_id)
        # Clean up final media directory
        if take and take.video_path:
            final_dir = os.path.dirname(take.video_path)
            if os.path.isdir(final_dir):
                import shutil
                shutil.rmtree(final_dir)
        # Delete Take + reset request to queued + reset job to claimed
        with env["db"].connection() as conn:
            conn.execute("DELETE FROM takes WHERE id = ?", (job.take_id,))
            conn.execute(
                "UPDATE generation_requests SET status = 'queued' WHERE id = ?",
                (job.generation_request_id,),
            )
        env["queue_repo"].update_status(job.id, "claimed", take_id=None)
        return job

    def test_completed_prompt_creates_take(self, env):
        """ComfyUI prompt completed → download/validate/finalize automatically."""
        job = self._setup_claimed_with_prompt(env)
        # ComfyUI says succeeded
        env["mock_comfyui"].check_prompt_status.return_value = "succeeded"
        env["worker"].recover()
        recovered = env["queue_repo"].get(job.id)
        assert recovered.status == "succeeded"
        assert recovered.take_id is not None
        # Verify Take exists
        take = env["take_repo"].get_take(recovered.take_id)
        assert take is not None
        assert take.seed == job.seed

    def test_completed_prompt_submit_never_called(self, env):
        """Recovery NEVER calls submit() — only get_result/download."""
        job = self._setup_claimed_with_prompt(env)
        env["mock_comfyui"].check_prompt_status.return_value = "succeeded"
        env["mock_comfyui"].submit.reset_mock()
        env["worker"].recover()
        env["mock_comfyui"].submit.assert_not_called()

    def test_queued_prompt_leaves_claimed(self, env):
        """Prompt still queued → leave job claimed for later recovery."""
        job = self._setup_claimed_with_prompt(env)
        env["mock_comfyui"].check_prompt_status.return_value = "queued"
        env["worker"].recover()
        assert env["queue_repo"].get(job.id).status == "claimed"

    def test_running_prompt_leaves_claimed(self, env):
        """Prompt still running → leave job claimed for later recovery."""
        job = self._setup_claimed_with_prompt(env)
        env["mock_comfyui"].check_prompt_status.return_value = "running"
        env["worker"].recover()
        assert env["queue_repo"].get(job.id).status == "claimed"

    def test_failed_prompt_marks_failed(self, env):
        """ComfyUI reports prompt failed → mark job failed."""
        job = self._setup_claimed_with_prompt(env)
        env["mock_comfyui"].check_prompt_status.return_value = "failed"
        env["worker"].recover()
        assert env["queue_repo"].get(job.id).status == "failed"
        assert "failed externally" in env["queue_repo"].get(job.id).error.lower()

    def test_unknown_prompt_marks_failed(self, env):
        """Prompt not found in reachable ComfyUI → external state lost."""
        job = self._setup_claimed_with_prompt(env)
        env["mock_comfyui"].check_prompt_status.return_value = "unknown"
        env["worker"].recover()
        assert env["queue_repo"].get(job.id).status == "failed"
        assert "not found" in env["queue_repo"].get(job.id).error.lower()

    def test_comfyui_unreachable_leaves_claimed(self, env):
        """ComfyUI temporarily unreachable → leave claimed, no false failure."""
        job = self._setup_claimed_with_prompt(env)
        from film_director.errors import ComfyUIUnavailableError
        env["mock_comfyui"].check_prompt_status.side_effect = \
            ComfyUIUnavailableError("Cannot reach ComfyUI")
        env["worker"].recover()
        assert env["queue_repo"].get(job.id).status == "claimed"

    def test_recovery_idempotent_after_finalization(self, env):
        """Running recovery twice after finalization doesn't duplicate Take."""
        job = self._setup_claimed_with_prompt(env)
        env["mock_comfyui"].check_prompt_status.return_value = "succeeded"
        env["worker"].recover()
        # Now it's succeeded — running recover again should be safe
        env["worker"].recover()
        takes = env["take_repo"].get_takes_by_shot("shot-1")
        # Only one Take should exist
        take_for_req = [t for t in takes if t.generation_request_id == job.generation_request_id]
        assert len(take_for_req) == 1
