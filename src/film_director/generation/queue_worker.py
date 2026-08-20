"""QueueWorker — claim, execute, recover, and run queued generation jobs (M6.C).

Owns the claim→execute→finalize lifecycle. Uses GenerationService for
actual generation. Never holds a SQLite transaction during ComfyUI work.

max_attempts=1 for M6: no retry. A failed job stays failed.

Recovery matrix (12 states):
 1. pending → unchanged
 2. claimed, no request → failed
 3. claimed, request created, no prompt_id → failed (ambiguous, no resubmit)
 4. claimed, prompt_id exists, still running → resume exact prompt (no submit)
 5. claimed, prompt_id exists, completed → retrieve/finalize (no submit)
 6. claimed, prompt_id exists, ComfyUI failure → failed
 7. claimed, prompt_id lost/unknown → failed
 8. claimed, request succeeded, valid Take+media → reconcile succeeded
 9. claimed, request succeeded, no Take → invariant failure (never succeeded)
10. claimed, Take exists but mismatch → invariant failure
11. QueueJob succeeded but Take/media missing → invariant failure
12. claimed, request failed → failed
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable

from film_director.errors import ComfyUIExecutionError, GenerationError
from film_director.generation.comfyui_adapter import ComfyUIAdapter
from film_director.generation.generation_service import GenerationService
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    GenerationRequestRepository,
    QueueJobRepository,
    TakeRepository,
)

logger = logging.getLogger(__name__)

_MAX_CONCURRENCY = 4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueueWorker:
    """Claims and executes queued generation jobs with bounded concurrency."""

    def __init__(
        self,
        db: Database,
        queue_repo: QueueJobRepository,
        generation_service: GenerationService,
        request_repo: GenerationRequestRepository,
        take_repo: TakeRepository,
        comfyui: ComfyUIAdapter | None = None,
        concurrency: int = 1,
    ) -> None:
        if concurrency < 1 or concurrency > _MAX_CONCURRENCY:
            raise ValueError(f"concurrency must be 1–{_MAX_CONCURRENCY}, got {concurrency}")
        self._db = db
        self._queue_repo = queue_repo
        self._gen_service = generation_service
        self._request_repo = request_repo
        self._take_repo = take_repo
        self._comfyui = comfyui
        self._concurrency = concurrency
        self._stopped = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run_once(self) -> int:
        """Claim and execute one pending job. Returns 1 on execution, 0 if empty."""
        if self._stopped:
            return 0
        job = self._claim_next()
        if job is None:
            return 0
        self._execute_job(job)
        return 1

    def run_available(self) -> int:
        """Execute pending jobs up to concurrency limit until queue is empty.

        Recovery runs before first claim. Returns total jobs completed.
        """
        self.recover()
        total = 0
        if self._concurrency == 1:
            while not self._stopped:
                n = self.run_once()
                if n == 0:
                    break
                total += n
        else:
            total = self._run_concurrent()
        return total

    def stop(self):
        """Prevent new claims. In-progress jobs continue to completion."""
        self._stopped = True

    def recover(self) -> int:
        """Resolve orphaned claimed jobs after restart. Call before claiming."""
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
        with self._db.connection() as conn:
            return self._queue_repo.claim_next(conn)

    # ------------------------------------------------------------------
    # Execute with atomic finalization
    # ------------------------------------------------------------------

    def _execute_job(self, job) -> None:
        """Execute a claimed job through GenerationService with atomic finalization."""
        # Set up atomic finalization callback
        def finalize_cb(take, conn):
            self._queue_repo.update_status(
                job.id, "succeeded",
                generation_request_id=take.generation_request_id,
                take_id=take.id,
                completed_at=_now_iso(),
                conn=conn,
            )

        # Temporarily install callback on GenerationService
        old_cb = self._gen_service._finalize_callback
        self._gen_service._finalize_callback = finalize_cb
        try:
            kwargs: dict = {
                "shot_id": job.shot_id,
                "take_number": job.take_number,
                "seed_override": job.seed,
            }
            if job.overrides:
                if "prompt_override" in job.overrides:
                    kwargs["prompt_override"] = job.overrides["prompt_override"]
                if "duration_override" in job.overrides:
                    kwargs["duration_override"] = job.overrides["duration_override"]
            take = self._gen_service.generate_take(**kwargs)
            logger.info("Job %s succeeded: take=%s", job.id, take.id)
        except (ComfyUIExecutionError, GenerationError) as e:
            error_msg = str(e)[:1000]
            if "timeout" in error_msg.lower():
                # Monitoring timed out — ComfyUI may still complete.
                # Leave job claimed so recovery can check ComfyUI history.
                logger.warning(
                    "Job %s monitoring timed out, leaving claimed for recovery: %s",
                    job.id, error_msg,
                )
                return
            logger.error("Job %s failed: %s", job.id, error_msg)
            gen_req_id = self._find_request_for_job(job)
            with self._db.connection() as conn:
                self._queue_repo.update_status(
                    job.id, "failed",
                    error=error_msg,
                    generation_request_id=gen_req_id,
                    completed_at=_now_iso(),
                    conn=conn,
                )
        except Exception as e:
            error_msg = str(e)[:1000]
            logger.error("Job %s failed: %s", job.id, error_msg)
            gen_req_id = self._find_request_for_job(job)
            with self._db.connection() as conn:
                self._queue_repo.update_status(
                    job.id, "failed",
                    error=error_msg,
                    generation_request_id=gen_req_id,
                    completed_at=_now_iso(),
                    conn=conn,
                )
        finally:
            self._gen_service._finalize_callback = old_cb

    # ------------------------------------------------------------------
    # Concurrent execution
    # ------------------------------------------------------------------

    def _run_concurrent(self) -> int:
        total = 0
        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            while not self._stopped:
                futures = []
                for _ in range(self._concurrency - len(futures)):
                    if self._stopped:
                        break
                    job = self._claim_next()
                    if job is None:
                        break
                    futures.append(pool.submit(self._execute_job, job))

                if not futures:
                    break

                for f in as_completed(futures):
                    f.result()  # propagate exceptions
                    total += 1
        return total

    # ------------------------------------------------------------------
    # Recovery (full 12-state matrix)
    # ------------------------------------------------------------------

    def _recover_one(self, job) -> None:
        # Find the matching request (linked or by matching fields)
        req = None
        if job.generation_request_id:
            req = self._request_repo.get_request(job.generation_request_id)
        if req is None:
            reqs = self._request_repo.get_requests_by_shot(job.shot_id)
            matching = [r for r in reqs if r.take_number == job.take_number and r.seed == job.seed]
            req = matching[0] if matching else None

        if req is None:
            # State 2: claimed, no request → failed
            self._mark_failed(job, "Orphaned: no generation request found after crash")
            return

        # Validate ownership
        if req.shot_id != job.shot_id or req.take_number != job.take_number or req.seed != job.seed:
            self._mark_failed(job, f"Invariant: request {req.id} ownership mismatch", gen_req_id=req.id)
            return

        if req.status == "succeeded":
            self._recover_succeeded_request(job, req)
        elif req.status == "failed" and req.comfyui_prompt_id:
            # State 12b: request failed (e.g. timeout) but has prompt_id —
            # ComfyUI may have completed after our timeout. Check history.
            self._recover_with_prompt_id(job, req)
        elif req.status == "failed":
            # State 12: request failed, no prompt_id → genuinely failed
            self._mark_failed(job, req.error or "Generation request failed", gen_req_id=req.id)
        elif req.comfyui_prompt_id:
            # States 4-7: has prompt_id, request still pending/queued/running
            self._recover_with_prompt_id(job, req)
        else:
            # State 3: request created, no prompt_id → ambiguous, no resubmit
            self._mark_failed(job, "Ambiguous: request created but no ComfyUI prompt_id", gen_req_id=req.id)

    def _recover_succeeded_request(self, job, req) -> None:
        """States 8-10: request succeeded — validate Take+media exist."""
        takes = self._take_repo.get_takes_by_shot(job.shot_id)
        matching_take = None
        for t in takes:
            if t.generation_request_id == req.id:
                matching_take = t
                break

        if matching_take is None:
            # State 9: request succeeded but no Take → INVARIANT FAILURE
            self._mark_failed(
                job,
                f"Invariant failure: request {req.id} succeeded but no Take exists",
                gen_req_id=req.id,
            )
            return

        # Validate Take ownership
        if matching_take.shot_id != job.shot_id:
            self._mark_failed(
                job,
                f"Invariant failure: Take {matching_take.id} shot mismatch",
                gen_req_id=req.id,
            )
            return

        # Validate physical media exists
        if not os.path.isfile(matching_take.video_path):
            self._mark_failed(
                job,
                f"Invariant failure: video file missing: {matching_take.video_path}",
                gen_req_id=req.id,
            )
            return

        # State 8: all valid → reconcile succeeded
        logger.info("Recovery: job %s reconciled succeeded (take=%s)", job.id, matching_take.id)
        self._queue_repo.update_status(
            job.id, "succeeded",
            generation_request_id=req.id,
            take_id=matching_take.id,
            completed_at=_now_iso(),
        )

    def _recover_with_prompt_id(self, job, req) -> None:
        """States 1-8: resume exact ComfyUI prompt_id — NEVER submit."""
        if self._comfyui is None:
            # ComfyUI unavailable — leave claimed for later recovery
            logger.info("Recovery: job %s ComfyUI unavailable, leaving claimed", job.id)
            return

        prompt_id = req.comfyui_prompt_id

        try:
            status = self._comfyui.check_prompt_status(prompt_id)
        except Exception as e:
            # Any exception checking status → leave claimed for later recovery.
            # We cannot distinguish "ComfyUI unreachable" from other transient
            # errors without risking false-failing a completed prompt.
            logger.info("Recovery: job %s status check failed (%s), leaving claimed", job.id, e)
            return

        if status == "succeeded":
            # State 3: completed — download/validate/finalize (no submit)
            self._finalize_recovered_prompt(job, req)
        elif status == "failed":
            # State 7: definitively failed
            self._mark_failed(job, f"ComfyUI prompt {prompt_id} failed externally", gen_req_id=req.id)
        elif status in ("queued", "running"):
            # States 1-2: still active — leave claimed for later recovery
            logger.info("Recovery: job %s prompt %s still %s, leaving claimed", job.id, prompt_id, status)
        elif status == "unknown":
            # State 8: missing from reachable ComfyUI
            self._mark_failed(
                job,
                f"External state lost: prompt {prompt_id} not found in ComfyUI",
                gen_req_id=req.id,
            )

    def _finalize_recovered_prompt(self, job, req) -> None:
        """Download, validate, and finalize a completed ComfyUI result.

        Uses GenerationService.finalize_from_result — never calls submit().
        """
        # Install atomic finalization callback
        def finalize_cb(take, conn):
            self._queue_repo.update_status(
                job.id, "succeeded",
                generation_request_id=take.generation_request_id,
                take_id=take.id,
                completed_at=_now_iso(),
                conn=conn,
            )

        old_cb = self._gen_service._finalize_callback
        self._gen_service._finalize_callback = finalize_cb
        try:
            take = self._gen_service.finalize_from_result(
                request_id=req.id,
                shot_id=job.shot_id,
                take_number=job.take_number,
                seed=job.seed,
                prompt_id=req.comfyui_prompt_id,
            )
            logger.info("Recovery: job %s finalized from prompt %s → take %s",
                        job.id, req.comfyui_prompt_id, take.id)
        except Exception as e:
            error_msg = f"Recovery finalization failed: {e}"[:1000]
            logger.error("Recovery: job %s finalization error: %s", job.id, error_msg)
            self._mark_failed(job, error_msg, gen_req_id=req.id)
        finally:
            self._gen_service._finalize_callback = old_cb

    def _mark_failed(self, job, error: str, gen_req_id: str | None = None) -> None:
        logger.info("Recovery: job %s → failed: %s", job.id, error)
        self._queue_repo.update_status(
            job.id, "failed",
            error=error[:1000],
            generation_request_id=gen_req_id or job.generation_request_id,
            completed_at=_now_iso(),
        )

    def _find_request_for_job(self, job) -> str | None:
        reqs = self._request_repo.get_requests_by_shot(job.shot_id)
        for req in reqs:
            if req.take_number == job.take_number and req.seed == job.seed:
                return req.id
        return None
