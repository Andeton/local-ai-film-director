"""QueueService — persistent idempotent enqueue and cancellation (M6.B).

Every enqueue request requires a client-supplied idempotency_key.
Same key + same payload → returns original batch and jobs.
Same key + different payload → QueueConflictError.
New key → creates new batch with new take numbers.

No worker execution. No ComfyUI calls. No claiming. Those belong to M6.C.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from film_director.errors import (
    QueueConflictError,
    QueueJobNotFoundError,
    QueueTransitionError,
    QueueValidationError,
)
from film_director.generation.parameter_resolver import _SAFE_SEED_MAX, derive_take_seed
from film_director.generation.queue_models import QueueBatch, QueueJob, compute_request_fingerprint
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    GenerationPlanRepository,
    QueueBatchRepository,
    QueueJobRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)

_MIN_TAKES = 1
_MAX_TAKES = 10


def _gen_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueueService:
    """Idempotent enqueue and pending-job cancellation."""

    def __init__(
        self,
        db: Database,
        queue_repo: QueueJobRepository,
        batch_repo: QueueBatchRepository,
        shot_repo: ShotRepository,
        plan_repo: GenerationPlanRepository,
        scene_repo: SceneRepository,
        seq_repo: SequenceRepository,
        beat_repo: BeatRepository,
    ) -> None:
        self._db = db
        self._queue_repo = queue_repo
        self._batch_repo = batch_repo
        self._shot_repo = shot_repo
        self._plan_repo = plan_repo
        self._scene_repo = scene_repo
        self._seq_repo = seq_repo
        self._beat_repo = beat_repo

    # ------------------------------------------------------------------
    # Shot enqueue
    # ------------------------------------------------------------------

    def enqueue_shot(
        self,
        shot_id: str,
        idempotency_key: str,
        takes_count: int = 3,
        base_seed: int | None = None,
        priority: int = 0,
        overrides: dict | None = None,
    ) -> list[QueueJob]:
        """Enqueue takes_count generation jobs for one shot.

        Idempotency: same (project_id, idempotency_key) + same fingerprint
        returns the original jobs. Different fingerprint raises QueueConflictError.
        """
        if not idempotency_key or not idempotency_key.strip():
            raise QueueValidationError("idempotency_key must not be empty")
        if takes_count < _MIN_TAKES or takes_count > _MAX_TAKES:
            raise QueueValidationError(
                f"takes_count must be {_MIN_TAKES}–{_MAX_TAKES}",
                detail=f"takes_count={takes_count}",
            )

        with self._db.connection() as conn:
            # Validate shot + plan
            shot = self._shot_repo.get_shot(shot_id, conn=conn)
            if shot is None:
                raise QueueValidationError(f"Shot not found: {shot_id}")
            plan = self._plan_repo.get_current_plan_by_shot(shot_id, conn=conn)
            if plan is None:
                raise QueueValidationError(f"No current GenerationPlan for shot {shot_id}")

            # Derive project_id
            row = conn.execute(
                """SELECT seq.project_id FROM shots s
                   JOIN beats b ON s.beat_id = b.id
                   JOIN scenes sc ON b.scene_id = sc.id
                   JOIN sequences seq ON sc.sequence_id = seq.id
                   WHERE s.id = ?""",
                (shot_id,),
            ).fetchone()
            if row is None:
                raise QueueValidationError(f"Cannot find project for shot {shot_id}")
            project_id = row["project_id"]

            # Resolve base_seed before fingerprint
            if base_seed is None:
                # Check if this key already has a batch (use its seed)
                existing_batch = self._batch_repo.get_by_key(
                    project_id, idempotency_key, conn=conn,
                )
                if existing_batch is not None:
                    base_seed = existing_batch.base_seed
                else:
                    base_seed = secrets.randbelow(_SAFE_SEED_MAX + 1)

            if base_seed < 0 or base_seed > _SAFE_SEED_MAX:
                raise QueueValidationError(f"base_seed must be in [0, {_SAFE_SEED_MAX}]")

            fingerprint = compute_request_fingerprint(
                "shot", shot_id, takes_count, base_seed, priority,
            )

            # Idempotency check
            existing_batch = self._batch_repo.get_by_key(
                project_id, idempotency_key, conn=conn,
            )
            if existing_batch is not None:
                if existing_batch.request_fingerprint != fingerprint:
                    raise QueueConflictError(
                        f"Idempotency key {idempotency_key!r} already used with different parameters",
                        detail=f"batch_id={existing_batch.id}",
                    )
                return self._queue_repo.list_by_batch(existing_batch.id, conn=conn)

            # Create new batch + jobs
            return self._create_shot_batch(
                conn, project_id, shot_id, idempotency_key, fingerprint,
                takes_count, base_seed, priority, overrides=overrides,
            )

    def _create_shot_batch(
        self, conn, project_id, shot_id, idempotency_key, fingerprint,
        takes_count, base_seed, priority, overrides: dict | None = None,
    ) -> list[QueueJob]:
        now = _now_iso()
        batch_id = _gen_id("qb_")

        batch = QueueBatch(
            id=batch_id,
            project_id=project_id,
            scope_type="shot",
            scope_id=shot_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            takes_count=takes_count,
            base_seed=base_seed,
            priority=priority,
            created_at=now,
        )
        self._batch_repo.insert(batch, conn=conn)

        max_tn = self._queue_repo.max_take_number_for_shot(shot_id, conn=conn)
        jobs: list[QueueJob] = []

        for i in range(takes_count):
            tn = max_tn + 1 + i
            take_index = tn - 1
            seed = derive_take_seed(base_seed, take_index)

            job = QueueJob(
                id=_gen_id("qj_"),
                batch_id=batch_id,
                shot_id=shot_id,
                take_number=tn,
                project_id=project_id,
                base_seed=base_seed,
                seed=seed,
                status="pending",
                priority=priority,
                overrides=overrides,
                created_at=now,
                updated_at=now,
            )
            self._queue_repo.insert(job, conn=conn)
            jobs.append(job)

        return jobs

    # ------------------------------------------------------------------
    # Active job check
    # ------------------------------------------------------------------

    def has_active_jobs(self, shot_id: str) -> bool:
        """Check if shot has any pending or claimed (in-progress) queue jobs."""
        jobs = self._queue_repo.list_by_shot(shot_id)
        return any(j.status in ("pending", "claimed") for j in jobs)

    # ------------------------------------------------------------------
    # Scene enqueue
    # ------------------------------------------------------------------

    def enqueue_scene(
        self,
        scene_id: str,
        idempotency_key: str,
        takes_per_shot: int = 3,
        base_seed: int | None = None,
        priority: int = 0,
    ) -> list[QueueJob]:
        """Enqueue takes for all eligible shots in a scene atomically.

        Returns the complete list of jobs (original on retry).
        """
        if not idempotency_key or not idempotency_key.strip():
            raise QueueValidationError("idempotency_key must not be empty")
        if takes_per_shot < _MIN_TAKES or takes_per_shot > _MAX_TAKES:
            raise QueueValidationError(
                f"takes_per_shot must be {_MIN_TAKES}–{_MAX_TAKES}",
            )

        with self._db.connection() as conn:
            scene = self._scene_repo.get_scene(scene_id, conn=conn)
            if scene is None:
                raise QueueValidationError(f"Scene not found: {scene_id}")

            # Derive project_id
            row = conn.execute(
                """SELECT seq.project_id FROM scenes sc
                   JOIN sequences seq ON sc.sequence_id = seq.id
                   WHERE sc.id = ?""",
                (scene_id,),
            ).fetchone()
            if row is None:
                raise QueueValidationError(f"Cannot find project for scene {scene_id}")
            project_id = row["project_id"]

            # Resolve base_seed before fingerprint
            if base_seed is None:
                existing_batch = self._batch_repo.get_by_key(
                    project_id, idempotency_key, conn=conn,
                )
                if existing_batch is not None:
                    base_seed = existing_batch.base_seed
                else:
                    base_seed = secrets.randbelow(_SAFE_SEED_MAX + 1)

            if base_seed < 0 or base_seed > _SAFE_SEED_MAX:
                raise QueueValidationError(f"base_seed must be in [0, {_SAFE_SEED_MAX}]")

            fingerprint = compute_request_fingerprint(
                "scene", scene_id, takes_per_shot, base_seed, priority,
            )

            # Idempotency check
            existing_batch = self._batch_repo.get_by_key(
                project_id, idempotency_key, conn=conn,
            )
            if existing_batch is not None:
                if existing_batch.request_fingerprint != fingerprint:
                    raise QueueConflictError(
                        f"Idempotency key {idempotency_key!r} already used with different parameters",
                    )
                return self._queue_repo.list_by_batch(existing_batch.id, conn=conn)

            # Resolve eligible shots
            beats = self._beat_repo.get_beats_by_scene(scene_id, conn=conn)
            all_shots = []
            for beat in beats:
                all_shots.extend(self._shot_repo.get_shots_by_beat(beat.id, conn=conn))

            eligible = []
            for shot in all_shots:
                plan = self._plan_repo.get_current_plan_by_shot(shot.id, conn=conn)
                if plan is not None and plan.strategy == "REFERENCE_TO_VIDEO":
                    eligible.append(shot)

            if not eligible:
                raise QueueValidationError(f"No eligible shots in scene {scene_id}")

            # Create batch
            now = _now_iso()
            batch_id = _gen_id("qb_")
            batch = QueueBatch(
                id=batch_id,
                project_id=project_id,
                scope_type="scene",
                scope_id=scene_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                takes_count=takes_per_shot,
                base_seed=base_seed,
                priority=priority,
                created_at=now,
            )
            self._batch_repo.insert(batch, conn=conn)

            all_jobs: list[QueueJob] = []
            for shot in eligible:
                max_tn = self._queue_repo.max_take_number_for_shot(shot.id, conn=conn)
                for i in range(takes_per_shot):
                    tn = max_tn + 1 + i
                    take_index = tn - 1
                    seed = derive_take_seed(base_seed, take_index)

                    job = QueueJob(
                        id=_gen_id("qj_"),
                        batch_id=batch_id,
                        shot_id=shot.id,
                        take_number=tn,
                        project_id=project_id,
                        base_seed=base_seed,
                        seed=seed,
                        status="pending",
                        priority=priority,
                        created_at=now,
                        updated_at=now,
                    )
                    self._queue_repo.insert(job, conn=conn)
                    all_jobs.append(job)

            return all_jobs

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel_job(self, job_id: str) -> QueueJob:
        """Cancel a pending queue job. Idempotent for already-cancelled jobs."""
        with self._db.connection() as conn:
            job = self._queue_repo.get(job_id, conn=conn)
            if job is None:
                raise QueueJobNotFoundError(f"Queue job not found: {job_id}")

            if job.status == "cancelled":
                return job

            if job.status != "pending":
                raise QueueTransitionError(
                    f"Cannot cancel job in status {job.status!r}",
                    detail=f"job_id={job_id}, status={job.status}",
                )

            self._queue_repo.update_status(
                job_id, "cancelled",
                completed_at=_now_iso(),
                conn=conn,
            )

        return self._queue_repo.get(job_id)
