"""M6 live acceptance tests — read-only evidence verification.

Run with: pytest tests/integration/test_m6_live.py -m live

Verifies persisted evidence from the M6.F live run without
submitting generations, mutating lifecycle, or requiring ComfyUI.
"""
from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.live

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _skip_unless_m6_db():
    db_path = os.path.join(WORKTREE, "data", "m6_live.db")
    if not os.path.isfile(db_path):
        pytest.skip("M6 live database not available")
    return db_path


class TestM6LiveThreeTakes:
    """Verify three queued Takes with different seeds exist."""

    def test_three_succeeded_takes(self):
        db_path = _skip_unless_m6_db()
        sys.path.insert(0, os.path.join(WORKTREE, "src"))
        from film_director.persistence.database import Database
        from film_director.persistence.repositories import TakeRepository, GenerationRequestRepository

        db = Database(db_path)
        take_repo = TakeRepository(db)
        req_repo = GenerationRequestRepository(db)

        takes = take_repo.get_takes_by_shot("shot60190e252ed4")
        # M5 Take 1 + 3 new M6 Takes = at least 4
        assert len(takes) >= 4

        # Takes 2-4 should exist with distinct seeds
        m6_takes = []
        for t in takes:
            req = req_repo.get_request(t.generation_request_id)
            if req and req.take_number in (2, 3, 4):
                m6_takes.append((req.take_number, t, req))

        assert len(m6_takes) == 3
        seeds = {req.seed for _, _, req in m6_takes}
        assert len(seeds) == 3, "All three seeds must be distinct"

    def test_approved_take_exists(self):
        db_path = _skip_unless_m6_db()
        sys.path.insert(0, os.path.join(WORKTREE, "src"))
        from film_director.persistence.database import Database
        from film_director.persistence.repositories import TakeRepository

        db = Database(db_path)
        take_repo = TakeRepository(db)
        approved = take_repo.get_approved_for_shot("shot60190e252ed4")
        assert approved is not None
        assert approved.status == "approved"
        assert approved.is_favorite is False

    def test_queue_batch_persisted(self):
        db_path = _skip_unless_m6_db()
        sys.path.insert(0, os.path.join(WORKTREE, "src"))
        from film_director.persistence.database import Database
        from film_director.persistence.repositories import QueueBatchRepository

        db = Database(db_path)
        batch_repo = QueueBatchRepository(db)
        batch = batch_repo.get_by_key("proj_b8b1b8ab40b5", "m6f-live-shot60190e252ed4-v1")
        assert batch is not None
        assert batch.takes_count == 3
        assert batch.base_seed == 600600
