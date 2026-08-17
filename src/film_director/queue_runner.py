"""Standalone queue worker entry point.

Usage:
    python -m film_director.queue_runner

Processes enqueued generation jobs using the production database,
ComfyUI, and configured concurrency. Runs until interrupted (CTRL+C).

The API server (uvicorn) and queue worker run as separate processes
sharing the same database and storage root.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time

from film_director.config import Settings
from film_director.generation.comfyui_adapter import ComfyUIAdapter
from film_director.generation.generation_service import GenerationService
from film_director.generation.queue_worker import QueueWorker
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

logger = logging.getLogger(__name__)

_LOCK_FILENAME = ".queue_worker.lock"


class WorkerLock:
    """OS-level single-instance lock using file locking.

    On Windows uses msvcrt; on POSIX uses fcntl.
    Lock auto-releases when process exits or crashes.
    """

    def __init__(self, lock_path: str) -> None:
        self._lock_path = lock_path
        self._fd = None

    def acquire(self) -> bool:
        """Acquire the lock. Returns True on success, False if another worker holds it."""
        os.makedirs(os.path.dirname(self._lock_path) or ".", exist_ok=True)
        try:
            self._fd = open(self._lock_path, "w")
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd.write(str(os.getpid()))
            self._fd.flush()
            return True
        except (OSError, IOError):
            if self._fd:
                self._fd.close()
                self._fd = None
            return False

    def release(self) -> None:
        if self._fd:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self._fd.seek(0)
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
            self._fd.close()
            self._fd = None


def build_worker(settings: Settings) -> QueueWorker:
    """Construct production QueueWorker from settings.

    Uses the same dependency construction as the API application factory.
    Does not process jobs during construction.
    """
    if settings.queue_worker_concurrency < 1 or settings.queue_worker_concurrency > 4:
        raise ValueError(f"queue_worker_concurrency must be 1–4, got {settings.queue_worker_concurrency}")
    if settings.queue_worker_poll_interval_seconds < 1 or settings.queue_worker_poll_interval_seconds > 300:
        raise ValueError(f"queue_worker_poll_interval_seconds must be 1–300")

    db = Database(settings.database_path)
    db.init_schema()

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    comfyui = ComfyUIAdapter(base_url=settings.comfyui_base_url)

    generation_service = GenerationService(
        db=db,
        comfyui=comfyui,
        storage_root=settings.storage_root,
        project_root=project_root,
        generation_timeout=settings.comfyui_generation_timeout,
    )

    queue_repo = QueueJobRepository(db)
    request_repo = GenerationRequestRepository(db)
    take_repo = TakeRepository(db)

    return QueueWorker(
        db=db,
        queue_repo=queue_repo,
        generation_service=generation_service,
        request_repo=request_repo,
        take_repo=take_repo,
        comfyui=comfyui,
        concurrency=settings.queue_worker_concurrency,
    )


def run_worker(settings: Settings | None = None) -> None:
    """Main worker loop. Runs until SIGINT/CTRL+C."""
    if settings is None:
        settings = Settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not settings.queue_worker_enabled:
        logger.info("Queue worker disabled (FILM_QUEUE_WORKER_ENABLED=false)")
        return

    # Single-instance lock
    lock_path = os.path.join(
        os.path.dirname(settings.database_path) or ".",
        _LOCK_FILENAME,
    )
    lock = WorkerLock(lock_path)
    if not lock.acquire():
        logger.error("Another queue worker is already running (lock: %s)", lock_path)
        sys.exit(1)

    try:
        worker = build_worker(settings)
        poll_interval = settings.queue_worker_poll_interval_seconds

        logger.info(
            "Queue worker started (concurrency=%d, poll=%ds, db=%s)",
            settings.queue_worker_concurrency,
            poll_interval,
            settings.database_path,
        )

        # Recovery before first claim
        recovered = worker.recover()
        if recovered > 0:
            logger.info("Recovered %d orphaned job(s)", recovered)

        # Shutdown handler
        def handle_signal(signum, frame):
            logger.info("Shutdown signal received, stopping worker...")
            worker.stop()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        # Main loop
        while not worker._stopped:
            try:
                completed = worker.run_available()
                if completed > 0:
                    logger.info("Completed %d job(s)", completed)
                else:
                    time.sleep(poll_interval)
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt, stopping...")
                worker.stop()
                break
            except Exception as e:
                logger.error("Worker loop error: %s", e)
                time.sleep(poll_interval)  # backoff on unexpected error

        logger.info("Queue worker stopped")

    finally:
        lock.release()


if __name__ == "__main__":
    run_worker()
