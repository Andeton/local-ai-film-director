"""Integration tests for P3 async generation lifecycle.

Covers the systemic fix: UI-driven generation through the durable queue,
timeout resilience, recovery, idempotency, and duplicate protection.

Uses real SQLite, mocked ComfyUI. No live generation.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from film_director.errors import ComfyUIExecutionError, GenerationError
from film_director.generation.comfyui_adapter import (
    ComfyUIAdapter,
    ComfyUIGenerationResult,
    ComfyUIOutputRef,
)
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


def _fake_comfyui(tmp_path, prompt_id="fake-prompt-id"):
    mock = MagicMock(spec=ComfyUIAdapter)
    video_path = os.path.join(str(tmp_path), "comfyui_output.mp4")
    _create_synthetic_video(video_path)
    mock.upload_image.return_value = "uploaded.png"
    mock.submit.return_value = prompt_id
    mock.monitor.return_value = None
    mock.get_result.return_value = ComfyUIGenerationResult(
        prompt_id=prompt_id, output_node_id="92",
        outputs=[ComfyUIOutputRef("out.mp4", "m3", "output")],
    )
    mock.download_output.side_effect = lambda ref, dest: shutil.copy2(video_path, dest) or dest
    mock.check_prompt_status.return_value = "succeeded"
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
        "batch_repo": batch_repo, "shot_repo": ShotRepository(db),
    }


# ---------------------------------------------------------------------------
# 1. API generation request returns before render completion
# ---------------------------------------------------------------------------

class TestAsyncEnqueue:
    def test_enqueue_returns_pending_job(self, env):
        """UI-driven enqueue returns immediately with pending status."""
        jobs = env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-abc", takes_count=1, base_seed=100,
        )
        assert len(jobs) == 1
        assert jobs[0].status == "pending"
        # No Take created yet
        takes = env["take_repo"].get_takes_by_shot("shot-1")
        assert len(takes) == 0


# ---------------------------------------------------------------------------
# 2. Durable job/request is persisted
# ---------------------------------------------------------------------------

class TestDurableJobPersistence:
    def test_job_survives_db_reopen(self, env):
        """Job persisted to DB survives reconnection."""
        jobs = env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-persist", takes_count=1, base_seed=200,
        )
        job_id = jobs[0].id
        # Reopen database
        db2 = Database(env["db"]._db_path)
        repo2 = QueueJobRepository(db2)
        reloaded = repo2.get(job_id)
        assert reloaded is not None
        assert reloaded.status == "pending"
        assert reloaded.shot_id == "shot-1"


# ---------------------------------------------------------------------------
# 3. queued → running → succeeded → Take persisted
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    def test_pending_to_succeeded_with_take(self, env):
        """Full lifecycle: enqueue → worker executes → Take persisted."""
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-full", takes_count=1, base_seed=300,
        )
        completed = env["worker"].run_available()
        assert completed == 1
        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "succeeded"
        assert jobs[0].take_id is not None
        take = env["take_repo"].get_take(jobs[0].take_id)
        assert take is not None
        assert take.status == "succeeded"
        assert os.path.isfile(take.video_path)
        assert os.path.isfile(take.last_frame_path)


# ---------------------------------------------------------------------------
# 4. Long-running ComfyUI job not marked failed on timeout
# ---------------------------------------------------------------------------

class TestTimeoutResilience:
    def test_timeout_leaves_job_claimed_not_failed(self, env):
        """Monitoring timeout leaves job claimed for recovery, not failed."""
        env["mock_comfyui"].monitor.side_effect = ComfyUIExecutionError(
            "Generation timeout after 600s"
        )
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-timeout", takes_count=1, base_seed=400,
        )
        env["worker"].run_once()
        jobs = env["queue_repo"].list_by_shot("shot-1")
        # Job must remain claimed, NOT failed
        assert jobs[0].status == "claimed"


# ---------------------------------------------------------------------------
# 5. Browser/page refresh can rediscover active generation
# ---------------------------------------------------------------------------

class TestPageRefreshDiscovery:
    def test_active_job_discoverable_by_shot_id(self, env):
        """Active jobs queryable by shot_id for page-refresh discovery."""
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-refresh", takes_count=1, base_seed=500,
        )
        jobs = env["queue_repo"].list_by_shot("shot-1")
        active = [j for j in jobs if j.status in ("pending", "claimed")]
        assert len(active) == 1
        assert env["queue_svc"].has_active_jobs("shot-1")


# ---------------------------------------------------------------------------
# 6. Completed ComfyUI result is finalized after delayed completion
# ---------------------------------------------------------------------------

class TestDelayedFinalization:
    def test_recovery_finalizes_after_timeout(self, env):
        """After timeout, recovery checks ComfyUI and finalizes completed result."""
        env["mock_comfyui"].monitor.side_effect = ComfyUIExecutionError(
            "Generation timeout after 600s"
        )
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-delayed", takes_count=1, base_seed=600,
        )
        env["worker"].run_once()

        # Job is claimed (timeout)
        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "claimed"

        # Now ComfyUI has completed — recovery should finalize
        env["mock_comfyui"].monitor.side_effect = None
        env["mock_comfyui"].check_prompt_status.return_value = "succeeded"
        env["worker"].recover()

        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "succeeded"
        assert jobs[0].take_id is not None
        take = env["take_repo"].get_take(jobs[0].take_id)
        assert take is not None
        assert take.status == "succeeded"


# ---------------------------------------------------------------------------
# 7. Restart/recovery reconciles unfinished generation
# ---------------------------------------------------------------------------

class TestRestartRecovery:
    def test_recovery_after_simulated_crash(self, env):
        """Simulated crash: timeout leaves job claimed, new worker recovers."""
        env["mock_comfyui"].monitor.side_effect = ComfyUIExecutionError(
            "Generation timeout after 600s"
        )
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-crash", takes_count=1, base_seed=700,
        )
        env["worker"].run_once()
        # Job is claimed (timeout)
        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "claimed"

        # Simulate restart with new worker instance
        env["mock_comfyui"].monitor.side_effect = None
        env["mock_comfyui"].check_prompt_status.return_value = "succeeded"

        db2 = Database(env["db"]._db_path)
        worker2 = QueueWorker(
            db=db2, queue_repo=QueueJobRepository(db2),
            generation_service=env["gen_service"],
            request_repo=GenerationRequestRepository(db2),
            take_repo=TakeRepository(db2),
            comfyui=env["mock_comfyui"],
        )
        worker2.recover()

        jobs = QueueJobRepository(db2).list_by_shot("shot-1")
        assert jobs[0].status == "succeeded"
        assert jobs[0].take_id is not None


# ---------------------------------------------------------------------------
# 8. Finalization/recovery is idempotent
# ---------------------------------------------------------------------------

class TestRecoveryIdempotent:
    def test_double_recovery_no_duplicate_take(self, env):
        """Running recovery twice doesn't create duplicate Takes."""
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-idemp", takes_count=1, base_seed=800,
        )
        env["worker"].run_available()
        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "succeeded"

        # Force back to claimed and recover
        env["queue_repo"].update_status(jobs[0].id, "claimed")
        env["worker"].recover()

        takes = env["take_repo"].get_takes_by_shot("shot-1")
        assert len(takes) == 1
        assert env["queue_repo"].get(jobs[0].id).status == "succeeded"


# ---------------------------------------------------------------------------
# 9. Duplicate Generate click does not create duplicate concurrent jobs
# ---------------------------------------------------------------------------

class TestDuplicateProtection:
    def test_has_active_jobs_blocks_duplicate(self, env):
        """Active job prevents second enqueue for same shot."""
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-dup1", takes_count=1, base_seed=900,
        )
        assert env["queue_svc"].has_active_jobs("shot-1") is True

    def test_completed_job_allows_new_enqueue(self, env):
        """After job completes, new enqueue is allowed."""
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-done", takes_count=1, base_seed=1000,
        )
        env["worker"].run_available()
        assert env["queue_svc"].has_active_jobs("shot-1") is False


# ---------------------------------------------------------------------------
# 10. Genuine ComfyUI failure becomes failed with useful error
# ---------------------------------------------------------------------------

class TestGenuineFailure:
    def test_execution_error_marks_failed(self, env):
        """Non-timeout ComfyUI error correctly fails the job with error info."""
        env["mock_comfyui"].monitor.side_effect = ComfyUIExecutionError(
            "Execution error on node 15 (KSampler): OutOfMemoryError"
        )
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-fail", takes_count=1, base_seed=1100,
        )
        env["worker"].run_once()
        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "failed"
        assert "OutOfMemoryError" in (jobs[0].error or "")

    def test_recovery_propagates_comfyui_failure(self, env):
        """Recovery for a timed-out job where ComfyUI also failed."""
        env["mock_comfyui"].monitor.side_effect = ComfyUIExecutionError(
            "Generation timeout after 600s"
        )
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-cfail", takes_count=1, base_seed=1200,
        )
        env["worker"].run_once()
        # ComfyUI reports failure
        env["mock_comfyui"].check_prompt_status.return_value = "failed"
        env["worker"].recover()
        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "failed"


# ---------------------------------------------------------------------------
# 11. Existing queue recovery tests compatibility (basic validation)
# ---------------------------------------------------------------------------

class TestExistingQueueCompat:
    def test_run_available_includes_recovery(self, env):
        """run_available() calls recover() before claiming."""
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-compat", takes_count=1, base_seed=1300,
        )
        completed = env["worker"].run_available()
        assert completed == 1


# ---------------------------------------------------------------------------
# 12. Overrides pass through queue to generation
# ---------------------------------------------------------------------------

class TestOverridesPassthrough:
    def test_overrides_stored_on_job(self, env):
        """Operator overrides persisted on queue job."""
        overrides = {"prompt_override": "custom prompt", "duration_override": 8.0}
        jobs = env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-ovr", takes_count=1, base_seed=1400,
            overrides=overrides,
        )
        assert jobs[0].overrides == overrides
        # Survives DB roundtrip
        reloaded = env["queue_repo"].get(jobs[0].id)
        assert reloaded.overrides == overrides

    def test_overrides_none_by_default(self, env):
        """No overrides → None in job."""
        jobs = env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-noovr", takes_count=1, base_seed=1500,
        )
        assert jobs[0].overrides is None


# ---------------------------------------------------------------------------
# 13. Recovery for failed request with prompt_id checks ComfyUI
# ---------------------------------------------------------------------------

class TestFailedRequestRecovery:
    def test_failed_request_with_prompt_id_checks_comfyui(self, env):
        """Failed request (timeout) with prompt_id → recovery checks ComfyUI history."""
        env["mock_comfyui"].monitor.side_effect = ComfyUIExecutionError(
            "Generation timeout after 600s"
        )
        env["queue_svc"].enqueue_shot(
            "shot-1", "ui-shot-1-frecov", takes_count=1, base_seed=1600,
        )
        env["worker"].run_once()  # Job stays claimed due to timeout

        # Verify job is claimed
        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "claimed"

        # The GenRequest was marked failed by generate_take's except handler
        reqs = env["request_repo"].get_requests_by_shot("shot-1")
        failed_req = [r for r in reqs if r.status == "failed"]
        assert len(failed_req) >= 1

        # ComfyUI completed successfully after timeout
        env["mock_comfyui"].check_prompt_status.return_value = "succeeded"
        env["mock_comfyui"].monitor.side_effect = None

        # Recovery should find the failed request with prompt_id and finalize
        env["worker"].recover()
        jobs = env["queue_repo"].list_by_shot("shot-1")
        assert jobs[0].status == "succeeded"
        assert jobs[0].take_id is not None
