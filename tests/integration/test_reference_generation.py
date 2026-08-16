"""Tests for M5.C — character reference generation service.

Uses mocked ComfyUI adapter. No live ComfyUI dependency.
"""
from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from PIL import Image

from film_director.generation.reference_generator import (
    ReferenceGenerationService,
    ReferenceGeneratorProfile,
    get_reference_generator_profiles,
)
from film_director.models.reference import (
    ReferenceGenerationExecution,
    ReferenceGenerationRequest,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    ReferenceAssetRepository,
    ReferenceGenerationExecutionRepository,
    ReferenceGenerationRequestRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_image_bytes(width=1024, height=1024) -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


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
    # Use the real project root so workflow templates are found
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    svc = ReferenceGenerationService(
        db=db,
        asset_repo=asset_repo,
        request_repo=req_repo,
        execution_repo=exec_repo,
        comfyui=mock_comfyui,
        storage_root=storage_root,
        project_root=project_root,
    )
    return dict(
        db=db, svc=svc, mock_comfyui=mock_comfyui,
        asset_repo=asset_repo, req_repo=req_repo, exec_repo=exec_repo,
        tmp_path=tmp_path, storage_root=storage_root, project_root=project_root,
    )


# ===========================================================================
# PROFILE REGISTRY
# ===========================================================================

class TestProfileRegistry:
    def test_z_image_profile_exists(self):
        profiles = get_reference_generator_profiles()
        assert "z_image_turbo_v1" in profiles

    def test_krea2_profile_exists(self):
        profiles = get_reference_generator_profiles()
        assert "krea2_turbo_v1" in profiles

    def test_profiles_have_required_fields(self):
        for pid, p in get_reference_generator_profiles().items():
            assert isinstance(p, ReferenceGeneratorProfile)
            assert p.id
            assert p.version
            assert p.display_name
            assert p.template_filename
            assert p.template_fingerprint
            assert len(p.template_fingerprint) == 64
            assert p.default_steps > 0
            assert p.default_cfg > 0

    def test_z_image_is_default_for_character_body(self):
        profiles = get_reference_generator_profiles()
        defaults = [pid for pid, p in profiles.items() if p.recommended_default]
        assert "z_image_turbo_v1" in defaults

    def test_profiles_coexist(self):
        profiles = get_reference_generator_profiles()
        assert len(profiles) >= 2

    def test_profile_versions_distinct(self):
        profiles = get_reference_generator_profiles()
        ids = [p.id for p in profiles.values()]
        assert len(ids) == len(set(ids))


# ===========================================================================
# GENERATION SERVICE — success
# ===========================================================================

class TestGenerationSuccess:
    def _setup_mock_comfyui(self, env, output_filename="ref_gen/z_image_00001_.png"):
        from film_director.generation.comfyui_adapter import ComfyUIOutputRef, ComfyUIGenerationResult
        img_bytes = _create_test_image_bytes()

        env["mock_comfyui"].submit.return_value = "comfy-prompt-001"
        env["mock_comfyui"].monitor.return_value = None  # WebSocket monitor returns nothing
        env["mock_comfyui"].get_result.return_value = ComfyUIGenerationResult(
            prompt_id="comfy-prompt-001",
            output_node_id="9",
            outputs=[ComfyUIOutputRef(filename=output_filename, subfolder="", type="output")],
        )
        env["mock_comfyui"].download_output.side_effect = lambda ref, dest: _write_test_image(dest)
        return img_bytes

    def test_generates_candidate_asset(self, env):
        self._setup_mock_comfyui(env)
        result = env["svc"].generate_character_reference(
            project_id="proj-001",
            character_id="char-001",
            character_name="Lu Yan",
            character_appearance="Middle-aged detective, trench coat",
            kind=ReferenceKind.CHARACTER_BODY,
        )
        assert result.asset is not None
        assert result.asset.source == ReferenceSource.GENERATED
        assert result.asset.status == ReferenceStatus.CANDIDATE
        assert result.asset.source_state == ReferenceSourceState.CURRENT

    def test_creates_immutable_request(self, env):
        self._setup_mock_comfyui(env)
        result = env["svc"].generate_character_reference(
            project_id="proj-001",
            character_id="char-001",
            character_name="Lu Yan",
            character_appearance="Detective",
            kind=ReferenceKind.CHARACTER_BODY,
        )
        req = env["req_repo"].get(result.request_id)
        assert req is not None
        assert req.character_id == "char-001"
        assert req.requested_kind == ReferenceKind.CHARACTER_BODY
        assert "Lu Yan" in req.prompt or "Detective" in req.prompt
        assert req.source_appearance_hash
        assert req.workflow_definition_id
        assert req.workflow_definition_version
        assert len(req.workflow_template_fingerprint) == 64

    def test_creates_succeeded_execution(self, env):
        self._setup_mock_comfyui(env)
        result = env["svc"].generate_character_reference(
            project_id="proj-001",
            character_id="char-001",
            character_name="Lu Yan",
            character_appearance="Detective",
            kind=ReferenceKind.CHARACTER_BODY,
        )
        exe = env["exec_repo"].get(result.execution_id)
        assert exe is not None
        assert exe.status == "succeeded"
        assert exe.output_reference_asset_id == result.asset.id
        assert exe.comfyui_prompt_id == "comfy-prompt-001"
        assert exe.error is None

    def test_asset_has_sha_and_dimensions(self, env):
        self._setup_mock_comfyui(env)
        result = env["svc"].generate_character_reference(
            project_id="proj-001",
            character_id="char-001",
            character_name="Lu Yan",
            character_appearance="Detective",
            kind=ReferenceKind.CHARACTER_BODY,
        )
        assert len(result.asset.content_sha256) == 64
        assert result.asset.width == 1024
        assert result.asset.height == 1024

    def test_explicit_profile_selection(self, env):
        self._setup_mock_comfyui(env)
        result = env["svc"].generate_character_reference(
            project_id="proj-001",
            character_id="char-001",
            character_name="Lu Yan",
            character_appearance="Detective",
            kind=ReferenceKind.CHARACTER_BODY,
            profile_id="krea2_turbo_v1",
        )
        req = env["req_repo"].get(result.request_id)
        assert req.workflow_definition_id == "krea2_turbo_v1"

    def test_default_profile_is_z_image(self, env):
        self._setup_mock_comfyui(env)
        result = env["svc"].generate_character_reference(
            project_id="proj-001",
            character_id="char-001",
            character_name="Lu Yan",
            character_appearance="Detective",
            kind=ReferenceKind.CHARACTER_BODY,
        )
        req = env["req_repo"].get(result.request_id)
        assert req.workflow_definition_id == "z_image_turbo_v1"

    def test_managed_file_exists(self, env):
        self._setup_mock_comfyui(env)
        result = env["svc"].generate_character_reference(
            project_id="proj-001",
            character_id="char-001",
            character_name="Lu Yan",
            character_appearance="Detective",
            kind=ReferenceKind.CHARACTER_BODY,
        )
        managed = os.path.join(env["storage_root"], result.asset.managed_path)
        assert os.path.isfile(managed)


# ===========================================================================
# GENERATION — failure
# ===========================================================================

class TestGenerationFailure:
    def test_comfyui_error_creates_failed_execution(self, env):
        from film_director.errors import ReferenceGenerationError
        env["mock_comfyui"].submit.side_effect = Exception("ComfyUI exploded")
        with pytest.raises(ReferenceGenerationError):
            env["svc"].generate_character_reference(
                project_id="proj-001",
                character_id="char-001",
                character_name="Lu Yan",
                character_appearance="Detective",
                kind=ReferenceKind.CHARACTER_BODY,
            )

    def test_failed_execution_has_no_output_asset(self, env):
        from film_director.errors import ComfyUIExecutionError, ReferenceGenerationError
        env["mock_comfyui"].submit.return_value = "comfy-fail"
        env["mock_comfyui"].monitor.side_effect = ComfyUIExecutionError("node failed")
        with pytest.raises(ReferenceGenerationError):
            env["svc"].generate_character_reference(
                project_id="proj-001",
                character_id="char-001",
                character_name="Lu Yan",
                character_appearance="Detective",
                kind=ReferenceKind.CHARACTER_BODY,
            )
        # Check no asset was created in repo
        assets = env["asset_repo"].list_by_character("char-001")
        assert len(assets) == 0


def _write_test_image(dest):
    """Helper: write a valid test image to destination path."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    img = Image.new("RGB", (1024, 1024), color=(100, 150, 200))
    img.save(dest, "PNG")
    return dest
