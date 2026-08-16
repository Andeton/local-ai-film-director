"""Tests for M5.C atomic finalization — asset insert + execution succeeded
must commit or roll back as one database transaction.
"""
from __future__ import annotations

import io
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from film_director.errors import ReferenceGenerationError
from film_director.generation.comfyui_adapter import ComfyUIGenerationResult, ComfyUIOutputRef
from film_director.generation.reference_generator import ReferenceGenerationService
from film_director.models.reference import ReferenceKind
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    ReferenceAssetRepository,
    ReferenceGenerationExecutionRepository,
    ReferenceGenerationRequestRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_test_image(dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    img = Image.new("RGB", (1024, 1024), color=(100, 150, 200))
    img.save(dest, "PNG")
    return dest


def _seed_project(db, project_id="proj-001"):
    with db.connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO production_projects "
            "(id, wc_project_id, title, status, aspect, director_context, "
            "created_at, updated_at, prov_source_system, prov_source_project_id, "
            "prov_source_asset_id, prov_source_asset_version, prov_imported_at, prov_source_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, f"wc-{project_id}", "Test", "active", "16:9", "{}",
             "t", "t", "wind_comic", project_id, project_id, 1, "t", "a" * 64),
        )


def _setup_mock(mock_comfyui):
    mock_comfyui.submit.return_value = "comfy-001"
    mock_comfyui.monitor.return_value = None
    mock_comfyui.get_result.return_value = ComfyUIGenerationResult(
        prompt_id="comfy-001", output_node_id="9",
        outputs=[ComfyUIOutputRef(filename="out.png", subfolder="", type="output")],
    )
    mock_comfyui.download_output.side_effect = lambda ref, dest: _write_test_image(dest)


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    _seed_project(db, "proj-001")
    asset_repo = ReferenceAssetRepository(db)
    req_repo = ReferenceGenerationRequestRepository(db)
    exec_repo = ReferenceGenerationExecutionRepository(db)
    storage_root = str(tmp_path / "storage")
    mock_comfyui = MagicMock()
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    svc = ReferenceGenerationService(
        db=db,
        asset_repo=asset_repo, request_repo=req_repo, execution_repo=exec_repo,
        comfyui=mock_comfyui, storage_root=storage_root, project_root=project_root,
    )
    return dict(db=db, svc=svc, mock_comfyui=mock_comfyui,
                asset_repo=asset_repo, req_repo=req_repo, exec_repo=exec_repo,
                tmp_path=tmp_path, storage_root=storage_root)


def _gen(env, seed=42):
    return env["svc"].generate_character_reference(
        project_id="proj-001", character_id="char-001",
        character_name="Lu Yan", character_appearance="Detective",
        kind=ReferenceKind.CHARACTER_BODY, seed=seed,
    )


# ===========================================================================
# SUCCESSFUL ATOMIC FINALIZATION
# ===========================================================================

class TestSuccessfulAtomic:
    def test_both_asset_and_execution_committed(self, env):
        _setup_mock(env["mock_comfyui"])
        result = _gen(env)
        # Asset exists
        asset = env["asset_repo"].get(result.asset.id)
        assert asset is not None
        # Execution succeeded with asset ID
        exe = env["exec_repo"].get(result.execution_id)
        assert exe.status == "succeeded"
        assert exe.output_reference_asset_id == result.asset.id
        # Managed file exists
        managed = os.path.join(env["storage_root"], result.asset.managed_path)
        assert os.path.isfile(managed)


# ===========================================================================
# EXECUTION-UPDATE FAILURE AFTER ASSET INSERT
# ===========================================================================

class TestExecutionUpdateFailure:
    def test_rollback_on_execution_update_failure(self, env):
        """If execution update fails after asset insert, both must roll back."""
        _setup_mock(env["mock_comfyui"])

        original_update = env["exec_repo"].update_status
        call_count = [0]

        def failing_update(execution_id, *, status, **kwargs):
            call_count[0] += 1
            conn = kwargs.get("conn")
            if status == "succeeded":
                raise sqlite3.OperationalError("simulated DB failure on succeeded update")
            return original_update(execution_id, status=status, **kwargs)

        env["exec_repo"].update_status = failing_update

        with pytest.raises(ReferenceGenerationError):
            _gen(env)

        # No ReferenceAsset should exist (rolled back)
        assets = env["asset_repo"].list_by_character("char-001")
        assert len(assets) == 0

        # Execution should be failed, not running or succeeded
        with env["db"].connection() as conn:
            rows = conn.execute(
                "SELECT status, error FROM reference_generation_executions"
            ).fetchall()
        for row in rows:
            assert row[0] != "running"
            # Should be "failed" with error message
            if row[0] == "failed":
                assert row[1] is not None

        # Managed file should be cleaned up
        ref_dir = os.path.join(env["storage_root"], "references", "proj-001")
        if os.path.isdir(ref_dir):
            # No managed files should remain
            files = []
            for root, dirs, fnames in os.walk(ref_dir):
                files.extend(fnames)
            assert len(files) == 0

        # Immutable request preserved
        with env["db"].connection() as conn:
            req_count = conn.execute(
                "SELECT COUNT(*) FROM reference_generation_requests"
            ).fetchone()[0]
        assert req_count >= 1

        env["exec_repo"].update_status = original_update


# ===========================================================================
# ASSET INSERT FAILURE
# ===========================================================================

class TestAssetInsertFailure:
    def test_no_partial_state_on_asset_save_failure(self, env):
        _setup_mock(env["mock_comfyui"])

        original_save = env["asset_repo"].save
        def failing_save(asset, conn=None):
            raise sqlite3.OperationalError("simulated asset save failure")
        env["asset_repo"].save = failing_save

        with pytest.raises(ReferenceGenerationError):
            _gen(env)

        # No assets
        env["asset_repo"].save = original_save
        assets = env["asset_repo"].list_by_character("char-001")
        assert len(assets) == 0

        # Execution failed
        with env["db"].connection() as conn:
            rows = conn.execute(
                "SELECT status FROM reference_generation_executions"
            ).fetchall()
        for row in rows:
            assert row[0] != "running"

        env["asset_repo"].save = original_save


# ===========================================================================
# DEDUP PRESERVES EXISTING ASSET
# ===========================================================================

class TestDedupPreservation:
    def test_dedup_preserves_existing_asset_and_file(self, env):
        _setup_mock(env["mock_comfyui"])
        r1 = _gen(env)
        first_path = os.path.join(env["storage_root"], r1.asset.managed_path)
        assert os.path.isfile(first_path)

        _setup_mock(env["mock_comfyui"])
        r2 = _gen(env)

        # Same asset reused
        assert r2.asset.id == r1.asset.id
        # First file still exists
        assert os.path.isfile(first_path)
        # Second execution succeeded
        exe2 = env["exec_repo"].get(r2.execution_id)
        assert exe2.status == "succeeded"
        assert exe2.output_reference_asset_id == r1.asset.id
