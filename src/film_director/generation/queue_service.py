"""QueueService — enqueue take-generation jobs for shots and scenes (M6.B).

Handles atomic enqueue, idempotency (returns existing jobs for already-enqueued
shots), deterministic seed derivation at enqueue time, and pending-job cancellation.

No worker execution. No ComfyUI calls. No claiming. Those belong to M6.C.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from film_director.errors import (
    QueueConflictError,
    QueueJobNotFoundError,
    QueueTransitionError,
    QueueValidationError,
)
from film_director.generation.parameter_resolver import _SAFE_SEED_MAX, derive_take_seed
from film_director.generation.queue_models import QueueJob
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    GenerationPlanRepository,
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
    """Enqueue take-generation jobs and cancel pending jobs."""

    def __init__(
        self,
        db: Database,
        queue_repo: QueueJobRepository,
        shot_repo: ShotRepository,
        plan_repo: GenerationPlanRepository,
        scene_repo: SceneRepository,
        seq_repo: SequenceRepository,
        beat_repo: BeatRepository,
    ) -> None:
        self._db = db
        self._queue_repo = queue_repo
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
        takes_count: int = 3,
        base_seed: int | None = None,
    ) -> list[QueueJob]:
        """Enqueue takes_count generation jobs for one shot.

        Idempotency: If the shot already has non-cancelled pending/claimed
        jobs, returns those existing jobs instead of creating duplicates.
        New jobs are only created for take_numbers that don't exist yet.

        Seeds are derived deterministically at enqueue time and persisted.
        """
        if takes_count < _MIN_TAKES or takes_count > _MAX_TAKES:
            raise QueueValidationError(
                f"takes_count must be {_MIN_TAKES}–{_MAX_TAKES}",
                detail=f"takes_count={takes_count}",
            )

        if base_seed is None:
            import secrets
            base_seed = secrets.randbelow(_SAFE_SEED_MAX + 1)

        if base_seed < 0 or base_seed > _SAFE_SEED_MAX:
            raise QueueValidationError(
                f"base_seed must be in [0, {_SAFE_SEED_MAX}]",
                detail=f"base_seed={base_seed}",
            )

        with self._db.connection() as conn:
            # Validate shot exists
            shot = self._shot_repo.get_shot(shot_id, conn=conn)
            if shot is None:
                raise QueueValidationError(
                    f"Shot not found: {shot_id}",
                    detail=f"shot_id={shot_id}",
                )

            # Validate current generation plan exists
            plan = self._plan_repo.get_current_plan_by_shot(shot_id, conn=conn)
            if plan is None:
                raise QueueValidationError(
                    f"No current GenerationPlan for shot {shot_id}",
                    detail=f"shot_id={shot_id}",
                )

            # Derive project_id from shot
            row = conn.execute(
                """SELECT seq.project_id FROM shots s
                   JOIN beats b ON s.beat_id = b.id
                   JOIN scenes sc ON b.scene_id = sc.id
                   JOIN sequences seq ON sc.sequence_id = seq.id
                   WHERE s.id = ?""",
                (shot_id,),
            ).fetchone()
            if row is None:
                raise QueueValidationError(
                    f"Cannot find project for shot {shot_id}",
                )
            project_id = row["project_id"]

            # Check existing non-cancelled jobs for idempotency
            existing = self._queue_repo.list_by_shot(shot_id, conn=conn)
            active = [j for j in existing if j.status not in ("cancelled",)]
            if len(active) >= takes_count:
                return active[:takes_count]

            # Find the highest historical take_number
            max_tn = self._queue_repo.max_take_number_for_shot(shot_id, conn=conn)

            now = _now_iso()
            # Determine which take_numbers to create
            existing_tns = {j.take_number for j in existing}
            jobs: list[QueueJob] = []
            next_tn = max_tn + 1

            for _ in range(takes_count - len(active)):
                while next_tn in existing_tns:
                    next_tn += 1
                take_index = next_tn - 1  # 0-based
                seed = derive_take_seed(base_seed, take_index)

                job = QueueJob(
                    id=_gen_id("qj_"),
                    shot_id=shot_id,
                    take_number=next_tn,
                    project_id=project_id,
                    base_seed=base_seed,
                    seed=seed,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
                self._queue_repo.insert(job, conn=conn)
                jobs.append(job)
                next_tn += 1

            return list(active) + jobs

    # ------------------------------------------------------------------
    # Scene enqueue
    # ------------------------------------------------------------------

    def enqueue_scene(
        self,
        scene_id: str,
        takes_per_shot: int = 3,
        base_seed: int | None = None,
    ) -> dict:
        """Enqueue takes for all eligible shots in a scene atomically.

        An eligible shot has a current non-outdated GenerationPlan with
        strategy REFERENCE_TO_VIDEO.

        Returns { total_jobs: int, shots_enqueued: int }.
        """
        if takes_per_shot < _MIN_TAKES or takes_per_shot > _MAX_TAKES:
            raise QueueValidationError(
                f"takes_per_shot must be {_MIN_TAKES}–{_MAX_TAKES}",
                detail=f"takes_per_shot={takes_per_shot}",
            )

        if base_seed is None:
            import secrets
            base_seed = secrets.randbelow(_SAFE_SEED_MAX + 1)

        if base_seed < 0 or base_seed > _SAFE_SEED_MAX:
            raise QueueValidationError(
                f"base_seed must be in [0, {_SAFE_SEED_MAX}]",
            )

        with self._db.connection() as conn:
            scene = self._scene_repo.get_scene(scene_id, conn=conn)
            if scene is None:
                raise QueueValidationError(
                    f"Scene not found: {scene_id}",
                    detail=f"scene_id={scene_id}",
                )

            # Load all shots for this scene's beats
            beats = self._beat_repo.get_beats_by_scene(scene_id, conn=conn)
            all_shots = []
            for beat in beats:
                all_shots.extend(
                    self._shot_repo.get_shots_by_beat(beat.id, conn=conn)
                )

            # Filter eligible: has current R2V plan
            eligible = []
            for shot in all_shots:
                plan = self._plan_repo.get_current_plan_by_shot(shot.id, conn=conn)
                if plan is not None and plan.strategy == "REFERENCE_TO_VIDEO":
                    eligible.append(shot)

            if not eligible:
                raise QueueValidationError(
                    f"No eligible shots in scene {scene_id}",
                    detail=f"scene_id={scene_id}",
                )

            # Derive project_id from scene
            row = conn.execute(
                """SELECT seq.project_id FROM scenes sc
                   JOIN sequences seq ON sc.sequence_id = seq.id
                   WHERE sc.id = ?""",
                (scene_id,),
            ).fetchone()
            project_id = row["project_id"] if row else None
            if project_id is None:
                raise QueueValidationError(
                    f"Cannot find project for scene {scene_id}",
                )

            total_jobs = 0
            shots_enqueued = 0
            now = _now_iso()

            for shot in eligible:
                existing = self._queue_repo.list_by_shot(shot.id, conn=conn)
                active = [j for j in existing if j.status != "cancelled"]
                if len(active) >= takes_per_shot:
                    continue  # already fully enqueued

                max_tn = self._queue_repo.max_take_number_for_shot(shot.id, conn=conn)
                existing_tns = {j.take_number for j in existing}
                next_tn = max_tn + 1
                created = 0

                for _ in range(takes_per_shot - len(active)):
                    while next_tn in existing_tns:
                        next_tn += 1
                    take_index = next_tn - 1
                    seed = derive_take_seed(base_seed, take_index)

                    job = QueueJob(
                        id=_gen_id("qj_"),
                        shot_id=shot.id,
                        take_number=next_tn,
                        project_id=project_id,
                        base_seed=base_seed,
                        seed=seed,
                        status="pending",
                        created_at=now,
                        updated_at=now,
                    )
                    self._queue_repo.insert(job, conn=conn)
                    created += 1
                    next_tn += 1

                if created > 0:
                    shots_enqueued += 1
                    total_jobs += created

        return {"total_jobs": total_jobs, "shots_enqueued": shots_enqueued}

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel_job(self, job_id: str) -> QueueJob:
        """Cancel a pending queue job. Idempotent for already-cancelled jobs."""
        with self._db.connection() as conn:
            job = self._queue_repo.get(job_id, conn=conn)
            if job is None:
                raise QueueJobNotFoundError(
                    f"Queue job not found: {job_id}",
                )

            if job.status == "cancelled":
                return job  # idempotent

            if job.status != "pending":
                raise QueueTransitionError(
                    f"Cannot cancel job in status {job.status!r}",
                    detail=f"job_id={job_id}, status={job.status}",
                )

            now = _now_iso()
            self._queue_repo.update_status(
                job_id, "cancelled",
                completed_at=now,
                conn=conn,
            )

        return self._queue_repo.get(job_id)
