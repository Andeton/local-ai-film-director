"""Tests for M6.E — queue runner, worker factory, singleton lock, settings."""
from __future__ import annotations

import os
import threading
import time

import pytest

from film_director.config import Settings
from film_director.queue_runner import WorkerLock, build_worker


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class TestSettings:
    def test_default_concurrency(self):
        s = Settings(wc_database_path="/tmp/t.db")
        assert s.queue_worker_concurrency == 1

    def test_default_poll_interval(self):
        s = Settings(wc_database_path="/tmp/t.db")
        assert s.queue_worker_poll_interval_seconds == 5

    def test_default_enabled(self):
        s = Settings(wc_database_path="/tmp/t.db")
        assert s.queue_worker_enabled is True


# ---------------------------------------------------------------------------
# Worker factory
# ---------------------------------------------------------------------------

class TestBuildWorker:
    def test_constructs_without_processing(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "test.db")
        s = Settings(
            database_path=db_path,
            storage_root=os.path.join(str(tmp_path), "storage"),
            wc_database_path="/tmp/t.db",
            comfyui_base_url="http://127.0.0.1:8188",
        )
        os.makedirs(os.path.join(str(tmp_path), "storage"), exist_ok=True)
        worker = build_worker(s)
        assert worker is not None
        # Does not process anything
        assert worker.run_once() == 0

    def test_invalid_concurrency_rejected(self, tmp_path):
        s = Settings(
            database_path=os.path.join(str(tmp_path), "t.db"),
            storage_root=str(tmp_path),
            wc_database_path="/tmp/t.db",
            queue_worker_concurrency=0,
        )
        with pytest.raises(ValueError, match="concurrency"):
            build_worker(s)

    def test_invalid_poll_rejected(self, tmp_path):
        s = Settings(
            database_path=os.path.join(str(tmp_path), "t.db"),
            storage_root=str(tmp_path),
            wc_database_path="/tmp/t.db",
            queue_worker_poll_interval_seconds=0,
        )
        with pytest.raises(ValueError, match="poll_interval"):
            build_worker(s)


# ---------------------------------------------------------------------------
# Singleton lock
# ---------------------------------------------------------------------------

class TestWorkerLock:
    def test_first_acquire_succeeds(self, tmp_path):
        lock = WorkerLock(os.path.join(str(tmp_path), "test.lock"))
        assert lock.acquire() is True
        lock.release()

    def test_second_acquire_fails(self, tmp_path):
        lock_path = os.path.join(str(tmp_path), "test.lock")
        lock1 = WorkerLock(lock_path)
        assert lock1.acquire() is True

        lock2 = WorkerLock(lock_path)
        assert lock2.acquire() is False

        lock1.release()

    def test_release_permits_reacquire(self, tmp_path):
        lock_path = os.path.join(str(tmp_path), "test.lock")
        lock1 = WorkerLock(lock_path)
        lock1.acquire()
        lock1.release()

        lock2 = WorkerLock(lock_path)
        assert lock2.acquire() is True
        lock2.release()

    def test_lock_path_confined(self, tmp_path):
        lock = WorkerLock(os.path.join(str(tmp_path), "subdir", "test.lock"))
        assert lock.acquire() is True
        assert os.path.isfile(os.path.join(str(tmp_path), "subdir", "test.lock"))
        lock.release()


# ---------------------------------------------------------------------------
# API 404 corrections
# ---------------------------------------------------------------------------

class TestAPI404:
    @pytest.fixture
    def client(self, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from film_director.api.routes import create_router
        from film_director.persistence.database import Database
        from film_director.persistence.repositories import (
            ShotRepository, SceneRepository, QueueJobRepository,
            QueueBatchRepository, GenerationPlanRepository,
            SequenceRepository, BeatRepository, TakeRepository,
        )
        from film_director.generation.queue_service import QueueService
        from film_director.services.take_service import TakeService
        from unittest.mock import MagicMock

        db = Database(os.path.join(str(tmp_path), "t.db"))
        db.init_schema()
        shot_repo = ShotRepository(db)
        scene_repo = SceneRepository(db)
        queue_repo = QueueJobRepository(db)
        batch_repo = QueueBatchRepository(db)
        take_repo = TakeRepository(db)

        queue_svc = QueueService(
            db=db, queue_repo=queue_repo, batch_repo=batch_repo,
            shot_repo=shot_repo, plan_repo=GenerationPlanRepository(db),
            scene_repo=scene_repo, seq_repo=SequenceRepository(db),
            beat_repo=BeatRepository(db),
        )
        take_svc = TakeService(take_repo, db)

        app = FastAPI()
        router = create_router(
            adapter=MagicMock(), import_service=MagicMock(),
            project_repo=MagicMock(), seq_repo=SequenceRepository(db),
            scene_repo=scene_repo, char_repo=MagicMock(),
            llm_provider=MagicMock(),
            shot_repo=shot_repo,
            queue_service=queue_svc,
            take_service=take_svc,
            take_repo=take_repo,
        )
        app.include_router(router)
        return TestClient(app)

    def test_missing_shot_enqueue_404(self, client):
        resp = client.post(
            "/shots/nonexistent/takes/enqueue",
            json={},
            headers={"Idempotency-Key": "k1"},
        )
        assert resp.status_code == 404

    def test_missing_scene_enqueue_404(self, client):
        resp = client.post(
            "/scenes/nonexistent/takes/enqueue",
            json={},
            headers={"Idempotency-Key": "k1"},
        )
        assert resp.status_code == 404
