"""M5 live acceptance tests — opt-in, requires real ComfyUI + production DB.

Run with: pytest tests/integration/test_m5_live.py -m live

Prerequisites:
- ComfyUI running at http://127.0.0.1:8188
- Z-Image Turbo models installed
- data/production.db with M4 live project (proj_b8b1b8ab40b5)
- storage/ directory writable

These tests verify the production reference generation path. They do NOT
perform automatic human approval — human visual verdicts are documented
separately (see git history for M5 live acceptance evidence).
"""
from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.live

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _skip_unless_production_db():
    db_path = os.path.join(WORKTREE, "data", "production.db")
    if not os.path.isfile(db_path):
        pytest.skip("Production database not available")
    return db_path


def _skip_unless_comfyui():
    try:
        import urllib.request
        import json
        resp = urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=5)
        json.loads(resp.read())
    except Exception:
        pytest.skip("ComfyUI not available")


class TestM5LiveReferenceGeneration:
    """Verify that the M5 reference generation path works end-to-end.

    This test verifies persisted evidence from the actual M5.H1 live run.
    It does NOT trigger a new generation — the real generation was performed
    during M5.H1 acceptance and its results are in the production database.
    """

    def test_approved_reference_exists(self):
        """Verify the human-approved reference asset is persisted correctly."""
        db_path = _skip_unless_production_db()
        sys.path.insert(0, os.path.join(WORKTREE, "src"))

        from film_director.persistence.database import Database
        from film_director.persistence.repositories import ReferenceAssetRepository
        from film_director.models.reference import (
            ReferenceKind, ReferenceSource, ReferenceSourceState, ReferenceStatus,
        )

        db = Database(db_path)
        repo = ReferenceAssetRepository(db)
        asset = repo.get("ref_e11cf946d0cb")

        assert asset is not None, "Approved reference asset must exist"
        assert asset.project_id == "proj_b8b1b8ab40b5"
        assert asset.character_id == "char_df6a4feecb8d"
        assert asset.kind == ReferenceKind.CHARACTER_BODY
        assert asset.source == ReferenceSource.GENERATED
        assert asset.status == ReferenceStatus.APPROVED
        assert asset.source_state == ReferenceSourceState.CURRENT
        assert asset.pinned is True
        assert asset.content_sha256 == "734dff2744b98953dabb87424a783ffca6eb25a8c95364ea4518cbcaf10ed099"
        assert asset.width == 1024
        assert asset.height == 1024

    def test_h3_take_exists(self):
        """Verify the H3 video take using the approved reference is persisted."""
        db_path = _skip_unless_production_db()
        sys.path.insert(0, os.path.join(WORKTREE, "src"))

        from film_director.persistence.database import Database
        from film_director.persistence.repositories import TakeRepository, GenerationRequestRepository

        db = Database(db_path)
        take_repo = TakeRepository(db)
        takes = take_repo.get_takes_by_shot("shot60190e252ed4")

        assert len(takes) >= 1, "At least one take must exist"
        take = takes[0]
        assert take.status == "succeeded"
        assert take.seed == 42

        # Verify the generation request has the approved reference in its snapshot
        req_repo = GenerationRequestRepository(db)
        req = req_repo.get_request(take.generation_request_id)
        assert req is not None
        assert req.workflow_definition_id == "h3_r2v_v1"
        assert len(req.reference_snapshot) == 1
        assert req.reference_snapshot[0]["reference_asset_id"] == "ref_e11cf946d0cb"
        assert req.reference_snapshot[0]["content_sha256"] == "734dff2744b98953dabb87424a783ffca6eb25a8c95364ea4518cbcaf10ed099"
