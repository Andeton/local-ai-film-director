"""QueueWorker — claim, execute, and recover queued generation jobs (M6.C).

Owns the claim→execute→finalize lifecycle. Uses GenerationService for
actual generation. Never holds a SQLite transaction during ComfyUI work.

max_attempts=1 for M6: no retry. A failed job stays failed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from film_director.errors import GenerationError
from film_director.generation.generation_service import GenerationService
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    GenerationRequestRepository,
    QueueJobRepository,
    TakeRepository,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueueWorker:
    """Claims and executes queued generation jobs.

    Concurrency is controlled by the caller (run_once processes one job).
    Recovery must be called before claiming to resolve orphaned claimed jobs.
    """

    def __init__(
        self,
        db: Database,
        queue_repo: QueueJobRepository,
        generation_service: GenerationService,
        request_repo: GenerationRequestRepository,
        take_repo: TakeRepository,
    ) -> None:
        self._db = db
        self._queue_repo = queue_repo
        self._gen_service = generation_service
        self._request_repo = request_repo
        self._take_repo = take_repo

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run_once(self) -> int:
        """Claim and execute one pending job. Returns 1 on execution, 0 if empty."""
        job = self._claim_next()
        if job is None:
            return 0

        self._execute_job(job)
        return 1

    def recover(self) -> int:
        """Resolve orphaned claimed jobs after restart. Call before claiming.

        Returns count of recovered jobs.
        """
        claimed = self._queue_repo.list_claimed()
        recovered = 0
        for job in claimed:
            self._recover_one(job)
            recovered += 1
        return recovered

    # ------------------------------------------------------------------
    # Claim
    # ------------------------------------------------------------------

    def _claim_next(self):
        """Atomically claim the highest-priority pending job."""
        with self._db.connection() as conn:
            return self._queue_repo.claim_next(conn)

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def _execute_job(self, job) -> None:
        """Execute a claimed job through GenerationService."""
        try:
            take = self._gen_service.generate_take(
                shot_id=job.shot_id,
                take_number=job.take_number,
                seed_override=job.seed,
            )
            # Finalize: mark job succeeded with request and take links
            with self._db.connection() as conn:
                self._queue_repo.update_status(
                    job.id, "succeeded",
                    generation_request_id=take.generation_request_id,
                    take_id=take.id,
                    completed_at=_now_iso(),
                    conn=conn,
                )
            logger.info("Job %s succeeded: take=%s", job.id, take.id)

        except Exception as e:
            # Mark job failed
            error_msg = str(e)[:1000]
            logger.error("Job %s failed: %s", job.id, error_msg)

            # Find the generation_request_id if one was created
            gen_req_id = None
            reqs = self._request_repo.get_requests_by_shot(job.shot_id)
            for req in reqs:
                if req.take_number == job.take_number and req.seed == job.seed:
                    gen_req_id = req.id
                    break

            with self._db.connection() as conn:
                self._queue_repo.update_status(
                    job.id, "failed",
                    error=error_msg,
                    generation_request_id=gen_req_id,
                    completed_at=_now_iso(),
                    conn=conn,
                )

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def _recover_one(self, job) -> None:
        """Recover a single orphaned claimed job.

        Recovery matrix (max_attempts=1, no retry):
        1. No generation_request_id → mark failed (never submitted)
        2. generation_request_id exists, request succeeded, Take exists → mark succeeded
        3. generation_request_id exists, request succeeded, no Take → mark succeeded (anomaly but safe)
        4. generation_request_id exists, request failed → mark failed
        5. generation_request_id exists, request pending/queued/running → mark failed (orphaned)
        """
        if job.generation_request_id is None:
            # Find if a request was created but not linked
            reqs = self._request_repo.get_requests_by_shot(job.shot_id)
            matching = [r for r in reqs if r.take_number == job.take_number and r.seed == job.seed]
            if matching:
                req = matching[0]
                self._recover_with_request(job, req)
            else:
                # Never submitted — mark failed
                logger.info("Recovery: job %s never submitted, marking failed", job.id)
                self._queue_repo.update_status(
                    job.id, "failed",
                    error="Orphaned: no generation request found after crash",
                    completed_at=_now_iso(),
                )
            return

        # Has generation_request_id — look up request
        req = self._request_repo.get_request(job.generation_request_id)
        if req is None:
            logger.warning("Recovery: job %s has dangling request_id %s", job.id, job.generation_request_id)
            self._queue_repo.update_status(
                job.id, "failed",
                error=f"Orphaned: generation request {job.generation_request_id} not found",
                completed_at=_now_iso(),
            )
            return

        self._recover_with_request(job, req)

    def _recover_with_request(self, job, req) -> None:
        """Recover job given a matching GenerationRequest."""
        if req.status == "succeeded":
            # Find Take
            takes = self._take_repo.get_takes_by_shot(job.shot_id)
            matching_take = None
            for t in takes:
                if t.generation_request_id == req.id:
                    matching_take = t
                    break

            take_id = matching_take.id if matching_take else None
            logger.info("Recovery: job %s request succeeded, reconciling", job.id)
            self._queue_repo.update_status(
                job.id, "succeeded",
                generation_request_id=req.id,
                take_id=take_id,
                completed_at=_now_iso(),
            )
        elif req.status == "failed":
            logger.info("Recovery: job %s request failed, marking failed", job.id)
            self._queue_repo.update_status(
                job.id, "failed",
                generation_request_id=req.id,
                error=req.error or "Generation request failed",
                completed_at=_now_iso(),
            )
        else:
            # pending/queued/running — ambiguous orphan, mark failed
            logger.warning(
                "Recovery: job %s request %s in ambiguous state %s, marking failed",
                job.id, req.id, req.status,
            )
            self._queue_repo.update_status(
                job.id, "failed",
                generation_request_id=req.id,
                error=f"Orphaned: request in ambiguous state '{req.status}' after crash",
                completed_at=_now_iso(),
            )
