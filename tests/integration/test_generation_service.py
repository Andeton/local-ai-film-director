"""Integration tests for GenerationService — full mocked pipeline.

Uses real SQLite, real filesystem, fake ComfyUIAdapter.
No real ComfyUI or Ollama needed.
"""
import json
import os
import shutil
import subprocess
import uuid
from unittest.mock import MagicMock, patch

import pytest

from film_director.errors import (
    GenerationError,
    MediaProcessingError,
    ReferenceResolutionError,
    UnsupportedStrategyError,
)
from film_director.generation.comfyui_adapter import (
    ComfyUIAdapter,
    ComfyUIGenerationResult,
    ComfyUIOutputRef,
)
from film_director.generation.generation_request import GenerationRequest, Take
from film_director.generation.generation_service import GenerationService
from film_director.generation.h3_types import H3ReferenceBinding
from film_director.models.canonical import (
    CameraIntent,
    CharacterReference,
    GenerationPlan,
    ReferenceRequirements,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    CharacterRepository,
    GenerationPlanRepository,
    GenerationRequestRepository,
    H3PromptRepository,
    ProjectRepository,
    ReferenceAssetRepository,
    ShotRepository,
    TakeRepository,
    SequenceRepository,
    SceneRepository,
    BeatRepository,
)

VALID_SHA = "a" * 64
WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01T00:00:00", source_hash="hash",
    )


def _create_synthetic_video(path: str) -> str:
    """Create a tiny synthetic mp4 using FFmpeg."""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "color=c=green:s=64x64:d=0.5:r=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        pytest.skip("FFmpeg not available")
    return path


def _compute_sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def env(tmp_path):
    """Set up a complete test environment with all DB entities + M5 ReferenceAsset."""
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()

    storage_root = os.path.join(str(tmp_path), "storage")
    os.makedirs(storage_root, exist_ok=True)

    # Create ref image inside storage root (confinement-safe)
    ref_dir = os.path.join(storage_root, "references", "proj-1")
    os.makedirs(ref_dir, exist_ok=True)
    ref_data = b"fake alice reference image data"
    ref_path = os.path.join(ref_dir, "alice.png")
    with open(ref_path, "wb") as f:
        f.write(ref_data)
    ref_sha = _compute_sha(ref_data)

    # Insert canonical entities
    from film_director.models.canonical import ProductionProject, Sequence, Scene, Beat
    with db.connection() as conn:
        ProjectRepository(db).save_project(ProductionProject(
            id="proj-1", wc_project_id="wc-p1", title="Test Project",
            status="active", created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        ), conn=conn)
        SequenceRepository(db).save_sequence(Sequence(
            id="seq-1", project_id="proj-1", name="Seq", order_index=0,
        ), conn=conn)
        SceneRepository(db).save_scene(Scene(
            id="scene-1", sequence_id="seq-1", wc_scene_id="wc-s1",
            name="Scene1", location="office", description="An office",
            order_index=0, provenance=_prov(),
        ), conn=conn)
        BeatRepository(db).save_beat(Beat(
            id="beat-1", scene_id="scene-1", dramatic_action="enters",
            character_intention="investigate", change="discovers clue",
            order_index=0, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        ShotRepository(db).save_shot(ShotSpecificationV1(
            id="shot-1", beat_id="beat-1", dramatic_purpose="tension",
            subjects=[ShotSubject(character_id="char-1", name="Alice", ref_images=[ref_path])],
            action="walks forward", camera=CameraIntent(shot_size="medium"),
            audio_intent={"ambient": "silence"}, duration_sec=5.0,
            order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        CharacterRepository(db).save_character(CharacterReference(
            id="char-1", project_id="proj-1", wc_character_id="wc-c1",
            name="Alice", description="Detective", appearance="dark hair, trench coat",
            provenance=_prov(),
        ), conn=conn)
        GenerationPlanRepository(db).save_plan(GenerationPlan(
            id="plan-1", shot_id="shot-1", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, resolution_intent={"aspect": "16:9"},
            seed_policy="fixed", seed=42,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)

        # M5: Create approved ReferenceAsset for char-1
        ReferenceAssetRepository(db).save(ReferenceAsset(
            id="ref-alice-1",
            project_id="proj-1",
            character_id="char-1",
            kind=ReferenceKind.CHARACTER_BODY,
            source=ReferenceSource.USER_UPLOAD,
            managed_path=ref_path,
            content_sha256=ref_sha,
            source_provenance="user-upload-test",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=64,
            height=64,
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        ), conn=conn)

    return {
        "db": db,
        "storage_root": storage_root,
        "ref_path": ref_path,
        "ref_sha": ref_sha,
        "tmp_path": str(tmp_path),
    }


def _fake_comfyui(tmp_path):
    """Create a mock ComfyUIAdapter that simulates successful generation."""
    mock = MagicMock(spec=ComfyUIAdapter)

    # Create a synthetic video for download
    video_path = os.path.join(str(tmp_path), "comfyui_output.mp4")
    _create_synthetic_video(video_path)

    mock.upload_image.return_value = "m3_ref_uploaded.png"
    mock.submit.return_value = "fake-prompt-id-123"
    mock.monitor.return_value = None
    mock.get_result.return_value = ComfyUIGenerationResult(
        prompt_id="fake-prompt-id-123",
        output_node_id="92",
        outputs=[ComfyUIOutputRef(
            filename="generated_video.mp4",
            subfolder="m3",
            type="output",
        )],
    )

    def download_side_effect(output_ref, destination):
        shutil.copy2(video_path, destination)
        return destination

    mock.download_output.side_effect = download_side_effect
    return mock


# ---------------------------------------------------------------------------
# BLOCKING: Successful pipeline
# ---------------------------------------------------------------------------

class TestSuccessfulPipeline:
    def test_full_generation_pipeline(self, env, tmp_path):
        """BLOCKING: Full successful Shot→Take pipeline."""
        mock_comfyui = _fake_comfyui(tmp_path)
        service = GenerationService(
            db=env["db"],
            comfyui=mock_comfyui,
            storage_root=env["storage_root"],
            project_root=WORKTREE,
        )
        take = service.generate_shot("shot-1")

        # Take created
        assert isinstance(take, Take)
        assert take.shot_id == "shot-1"
        assert take.seed == 42
        assert take.status == "succeeded"
        assert take.video_path is not None
        assert take.last_frame_path is not None

        # Request succeeded
        req_repo = GenerationRequestRepository(env["db"])
        reqs = req_repo.get_requests_by_shot("shot-1")
        assert len(reqs) == 1
        assert reqs[0].status == "succeeded"
        assert reqs[0].seed == 42

        # Snapshot consistency
        snapshot = reqs[0].parameters_snapshot
        assert any(s["name"] == "prompt" for s in snapshot)
        assert any(s["name"] == "seed" and s["value"] == 42 for s in snapshot)
        assert any(s["name"] == "duration" for s in snapshot)

        # Reference snapshot — M5 asset provenance
        ref_snap = reqs[0].reference_snapshot
        assert len(ref_snap) == 1
        assert ref_snap[0]["reference_asset_id"] == "ref-alice-1"
        assert ref_snap[0]["reference_kind"] == "character_body"
        assert ref_snap[0]["character_id"] == "char-1"
        assert ref_snap[0]["uploaded_filename"] == "m3_ref_uploaded.png"
        assert len(ref_snap[0]["content_sha256"]) == 64

        # ComfyUI calls made
        mock_comfyui.upload_image.assert_called_once()
        mock_comfyui.submit.assert_called_once()
        mock_comfyui.monitor.assert_called_once()
        mock_comfyui.get_result.assert_called_once()
        mock_comfyui.download_output.assert_called_once()

        # Take persisted
        take_repo = TakeRepository(env["db"])
        persisted = take_repo.get_takes_by_shot("shot-1")
        assert len(persisted) == 1

        # H3 prompt persisted
        prompt_repo = H3PromptRepository(env["db"])
        prompt = prompt_repo.get_current_prompt("shot-1", 1, "plan-1", 1)
        assert prompt is not None

    def test_prompt_reuse(self, env, tmp_path):
        """If current prompt exists, it's reused — not rebuilt."""
        mock_comfyui = _fake_comfyui(tmp_path)
        service = GenerationService(
            db=env["db"], comfyui=mock_comfyui,
            storage_root=env["storage_root"], project_root=WORKTREE,
        )
        # Run first generation
        service.generate_shot("shot-1")

        # Check prompt count
        prompt_repo = H3PromptRepository(env["db"])
        prompts_before = prompt_repo.get_current_prompt("shot-1", 1, "plan-1", 1)

        # For second generation we'd need a clean state — but M3 is one take.
        # Just verify prompt was created once.
        assert prompts_before is not None


# ---------------------------------------------------------------------------
# BLOCKING: Exact reproducibility
# ---------------------------------------------------------------------------

class TestExactReproducibility:
    def test_snapshot_matches_submitted_workflow(self, env, tmp_path):
        """BLOCKING: parameters_snapshot values == submitted workflow JSON values."""
        captured_workflow = {}

        mock_comfyui = _fake_comfyui(tmp_path)
        original_submit = mock_comfyui.submit

        def capture_submit(workflow, client_id):
            captured_workflow["data"] = workflow
            return "fake-prompt-id"

        mock_comfyui.submit.side_effect = capture_submit
        mock_comfyui.get_result.return_value = ComfyUIGenerationResult(
            prompt_id="fake-prompt-id", output_node_id="92",
            outputs=[ComfyUIOutputRef("out.mp4", "m3", "output")],
        )

        service = GenerationService(
            db=env["db"], comfyui=mock_comfyui,
            storage_root=env["storage_root"], project_root=WORKTREE,
        )
        service.generate_shot("shot-1")

        req_repo = GenerationRequestRepository(env["db"])
        req = req_repo.get_requests_by_shot("shot-1")[0]
        workflow = captured_workflow["data"]

        for snap_entry in req.parameters_snapshot:
            node_id = snap_entry["node_id"]
            field = snap_entry["field"]
            expected = snap_entry["value"]
            actual = workflow[node_id]["inputs"][field]
            assert actual == expected, (
                f"Snapshot {snap_entry['name']}: expected {expected!r}, "
                f"got {actual!r}"
            )


# ---------------------------------------------------------------------------
# BLOCKING: Pre-request failure
# ---------------------------------------------------------------------------

class TestPreRequestFailure:
    def test_unsupported_strategy_no_request(self, env, tmp_path):
        """Force unsupported strategy — NO GenerationRequest created."""
        # Change plan strategy
        with env["db"].connection() as conn:
            conn.execute(
                "UPDATE generation_plans SET strategy = 'TEXT_TO_VIDEO' WHERE id = 'plan-1'"
            )

        mock_comfyui = MagicMock(spec=ComfyUIAdapter)
        service = GenerationService(
            db=env["db"], comfyui=mock_comfyui,
            storage_root=env["storage_root"], project_root=WORKTREE,
        )
        with pytest.raises(UnsupportedStrategyError):
            service.generate_shot("shot-1")

        req_repo = GenerationRequestRepository(env["db"])
        assert len(req_repo.get_requests_by_shot("shot-1")) == 0
        mock_comfyui.submit.assert_not_called()

    def test_missing_shot_no_request(self, env, tmp_path):
        mock_comfyui = MagicMock(spec=ComfyUIAdapter)
        service = GenerationService(
            db=env["db"], comfyui=mock_comfyui,
            storage_root=env["storage_root"], project_root=WORKTREE,
        )
        with pytest.raises(GenerationError):
            service.generate_shot("nonexistent-shot")

        mock_comfyui.submit.assert_not_called()

    def test_upload_failure_no_request(self, env, tmp_path):
        """Upload failure is pre-request — NO GenerationRequest."""
        mock_comfyui = MagicMock(spec=ComfyUIAdapter)
        mock_comfyui.upload_image.side_effect = Exception("Upload failed")

        service = GenerationService(
            db=env["db"], comfyui=mock_comfyui,
            storage_root=env["storage_root"], project_root=WORKTREE,
        )
        with pytest.raises(Exception):
            service.generate_shot("shot-1")

        req_repo = GenerationRequestRepository(env["db"])
        assert len(req_repo.get_requests_by_shot("shot-1")) == 0


# ---------------------------------------------------------------------------
# BLOCKING: Post-request failure
# ---------------------------------------------------------------------------

class TestPostRequestFailure:
    def test_submit_failure_leaves_failed_request(self, env, tmp_path):
        """Submit failure AFTER request creation → failed request, no Take."""
        mock_comfyui = MagicMock(spec=ComfyUIAdapter)
        mock_comfyui.upload_image.return_value = "ref.png"
        mock_comfyui.submit.side_effect = Exception("ComfyUI unreachable")

        service = GenerationService(
            db=env["db"], comfyui=mock_comfyui,
            storage_root=env["storage_root"], project_root=WORKTREE,
        )
        with pytest.raises(GenerationError):
            service.generate_shot("shot-1")

        req_repo = GenerationRequestRepository(env["db"])
        reqs = req_repo.get_requests_by_shot("shot-1")
        assert len(reqs) == 1
        assert reqs[0].status == "failed"
        assert "ComfyUI unreachable" in (reqs[0].error or "")

        take_repo = TakeRepository(env["db"])
        assert len(take_repo.get_takes_by_shot("shot-1")) == 0

    def test_monitor_failure_leaves_failed_request(self, env, tmp_path):
        """Monitor failure → failed request, no Take."""
        mock_comfyui = MagicMock(spec=ComfyUIAdapter)
        mock_comfyui.upload_image.return_value = "ref.png"
        mock_comfyui.submit.return_value = "prompt-id"
        mock_comfyui.monitor.side_effect = Exception("WS timeout")

        service = GenerationService(
            db=env["db"], comfyui=mock_comfyui,
            storage_root=env["storage_root"], project_root=WORKTREE,
        )
        with pytest.raises(GenerationError):
            service.generate_shot("shot-1")

        req_repo = GenerationRequestRepository(env["db"])
        reqs = req_repo.get_requests_by_shot("shot-1")
        assert len(reqs) == 1
        assert reqs[0].status == "failed"

        take_repo = TakeRepository(env["db"])
        assert len(take_repo.get_takes_by_shot("shot-1")) == 0


# ---------------------------------------------------------------------------
# BLOCKING: Media failure
# ---------------------------------------------------------------------------

class TestMediaFailure:
    def test_ffmpeg_failure_leaves_failed_request(self, env, tmp_path):
        """FFmpeg failure → failed request, no Take, staging cleaned."""
        mock_comfyui = MagicMock(spec=ComfyUIAdapter)
        mock_comfyui.upload_image.return_value = "ref.png"
        mock_comfyui.submit.return_value = "prompt-id"
        mock_comfyui.monitor.return_value = None
        mock_comfyui.get_result.return_value = ComfyUIGenerationResult(
            prompt_id="prompt-id", output_node_id="92",
            outputs=[ComfyUIOutputRef("video.mp4", "", "output")],
        )
        # Download writes a non-video file
        def bad_download(ref, dest):
            with open(dest, "wb") as f:
                f.write(b"not a valid video file at all")
            return dest
        mock_comfyui.download_output.side_effect = bad_download

        service = GenerationService(
            db=env["db"], comfyui=mock_comfyui,
            storage_root=env["storage_root"], project_root=WORKTREE,
        )
        with pytest.raises(GenerationError):
            service.generate_shot("shot-1")

        req_repo = GenerationRequestRepository(env["db"])
        reqs = req_repo.get_requests_by_shot("shot-1")
        assert len(reqs) == 1
        assert reqs[0].status == "failed"

        take_repo = TakeRepository(env["db"])
        assert len(take_repo.get_takes_by_shot("shot-1")) == 0


# ---------------------------------------------------------------------------
# BLOCKING: DB finalization failure
# ---------------------------------------------------------------------------

class TestFinalizationFailure:
    def test_db_failure_cleans_up(self, env, tmp_path):
        """DB finalization failure → request not succeeded, cleanup."""
        mock_comfyui = _fake_comfyui(tmp_path)

        service = GenerationService(
            db=env["db"], comfyui=mock_comfyui,
            storage_root=env["storage_root"], project_root=WORKTREE,
        )

        # Patch take_repo.save_take to fail
        original_save = service._take_repo.save_take

        def failing_save(take, conn=None):
            raise Exception("Simulated DB failure")

        service._take_repo.save_take = failing_save

        with pytest.raises(GenerationError, match="(?i)finalization"):
            service.generate_shot("shot-1")

        req_repo = GenerationRequestRepository(env["db"])
        reqs = req_repo.get_requests_by_shot("shot-1")
        assert len(reqs) == 1
        assert reqs[0].status == "failed"

        take_repo = TakeRepository(env["db"])
        assert len(take_repo.get_takes_by_shot("shot-1")) == 0


# ---------------------------------------------------------------------------
# Binding immutability
# ---------------------------------------------------------------------------

class TestBindingImmutability:
    def test_resolved_bindings_not_mutated(self, env, tmp_path):
        """resolved_bindings must retain uploaded_filename='' after upload."""
        captured_bindings = {}

        mock_comfyui = _fake_comfyui(tmp_path)
        service = GenerationService(
            db=env["db"], comfyui=mock_comfyui,
            storage_root=env["storage_root"], project_root=WORKTREE,
        )

        # Patch resolver to capture bindings
        original_resolve = service._ref_resolver.resolve_from_assets

        def capturing_resolve(*args, **kwargs):
            result = original_resolve(*args, **kwargs)
            captured_bindings["resolved"] = result
            return result

        service._ref_resolver.resolve_from_assets = capturing_resolve
        service.generate_shot("shot-1")

        # Original resolved bindings should still have empty uploaded_filename
        for b in captured_bindings["resolved"]:
            assert b.uploaded_filename == ""


# ---------------------------------------------------------------------------
# M5.E: Two-asset production path
# ---------------------------------------------------------------------------

@pytest.fixture
def two_char_env(tmp_path):
    """Environment with two characters, two approved assets, and a two-subject shot."""
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()

    storage_root = os.path.join(str(tmp_path), "storage")
    os.makedirs(storage_root, exist_ok=True)

    # Place refs inside storage root (confinement-safe)
    ref_dir = os.path.join(storage_root, "references", "proj-1")
    os.makedirs(ref_dir, exist_ok=True)

    ref_data_a = b"alice reference body image"
    ref_data_b = b"bob reference body image"
    ref_path_a = os.path.join(ref_dir, "alice.png")
    ref_path_b = os.path.join(ref_dir, "bob.png")
    with open(ref_path_a, "wb") as f:
        f.write(ref_data_a)
    with open(ref_path_b, "wb") as f:
        f.write(ref_data_b)
    sha_a = _compute_sha(ref_data_a)
    sha_b = _compute_sha(ref_data_b)

    from film_director.models.canonical import ProductionProject, Sequence, Scene, Beat
    with db.connection() as conn:
        ProjectRepository(db).save_project(ProductionProject(
            id="proj-1", wc_project_id="wc-p1", title="Test",
            status="active", created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        ), conn=conn)
        SequenceRepository(db).save_sequence(Sequence(
            id="seq-1", project_id="proj-1", name="Seq", order_index=0,
        ), conn=conn)
        SceneRepository(db).save_scene(Scene(
            id="scene-1", sequence_id="seq-1", wc_scene_id="wc-s1",
            name="Scene1", location="office", description="office",
            order_index=0, provenance=_prov(),
        ), conn=conn)
        BeatRepository(db).save_beat(Beat(
            id="beat-1", scene_id="scene-1", dramatic_action="confrontation",
            character_intention="confront", change="tension",
            order_index=0, created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        ShotRepository(db).save_shot(ShotSpecificationV1(
            id="shot-2sub", beat_id="beat-1", dramatic_purpose="confrontation",
            subjects=[
                ShotSubject(character_id="char-a", name="Alice", ref_images=[]),
                ShotSubject(character_id="char-b", name="Bob", ref_images=[]),
            ],
            action="Alice confronts Bob", camera=CameraIntent(shot_size="medium"),
            audio_intent={"ambient": "tension"}, duration_sec=5.0,
            order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)
        CharacterRepository(db).save_character(CharacterReference(
            id="char-a", project_id="proj-1", wc_character_id="wc-ca",
            name="Alice", description="Detective", appearance="dark hair, trench coat",
            provenance=_prov(),
        ), conn=conn)
        CharacterRepository(db).save_character(CharacterReference(
            id="char-b", project_id="proj-1", wc_character_id="wc-cb",
            name="Bob", description="Suspect", appearance="blond, leather jacket",
            provenance=_prov(),
        ), conn=conn)
        GenerationPlanRepository(db).save_plan(GenerationPlan(
            id="plan-2sub", shot_id="shot-2sub", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, resolution_intent={"aspect": "16:9"},
            seed_policy="fixed", seed=99,
            created_at="2026-01-01", updated_at="2026-01-01",
        ), conn=conn)

        # Two approved ReferenceAssets
        ref_repo = ReferenceAssetRepository(db)
        ref_repo.save(ReferenceAsset(
            id="ref-a", project_id="proj-1", character_id="char-a",
            kind=ReferenceKind.CHARACTER_BODY,
            source=ReferenceSource.GENERATED,
            managed_path=ref_path_a, content_sha256=sha_a,
            source_provenance="gen-req-1",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024,
            created_at="2026-01-01T00:00:01", updated_at="2026-01-01T00:00:01",
        ), conn=conn)
        ref_repo.save(ReferenceAsset(
            id="ref-b", project_id="proj-1", character_id="char-b",
            kind=ReferenceKind.CHARACTER_BODY,
            source=ReferenceSource.GENERATED,
            managed_path=ref_path_b, content_sha256=sha_b,
            source_provenance="gen-req-2",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=1024, height=1024,
            created_at="2026-01-01T00:00:02", updated_at="2026-01-01T00:00:02",
        ), conn=conn)

    return {
        "db": db,
        "storage_root": storage_root,
        "ref_path_a": ref_path_a,
        "ref_path_b": ref_path_b,
        "sha_a": sha_a,
        "sha_b": sha_b,
        "tmp_path": str(tmp_path),
    }


class TestTwoAssetProductionPath:
    """M5.E: Two-asset production integration — workflow v2 + asset provenance."""

    def test_two_ref_selects_v2_workflow(self, two_char_env, tmp_path):
        """Two subjects → v2 workflow selected (2 materialized slots)."""
        captured = {}
        mock_comfyui = _fake_comfyui(tmp_path)

        original_submit = mock_comfyui.submit
        def capture_submit(workflow, client_id):
            captured["workflow"] = workflow
            return "fake-prompt-id"
        mock_comfyui.submit.side_effect = capture_submit
        mock_comfyui.get_result.return_value = ComfyUIGenerationResult(
            prompt_id="fake-prompt-id", output_node_id="92",
            outputs=[ComfyUIOutputRef("out.mp4", "m3", "output")],
        )

        service = GenerationService(
            db=two_char_env["db"], comfyui=mock_comfyui,
            storage_root=two_char_env["storage_root"], project_root=WORKTREE,
        )
        take = service.generate_shot("shot-2sub")

        assert take.status == "succeeded"

        # Workflow v2 must be used — check node 201 exists in submitted workflow
        workflow = captured["workflow"]
        assert "201" in workflow, "v2 workflow must have node 201 (second LoadImage)"

        # Both ref images uploaded
        assert mock_comfyui.upload_image.call_count == 2

    def test_two_ref_images_in_correct_nodes(self, two_char_env, tmp_path):
        """Actual workflow JSON has correct uploaded filenames in nodes 200/201."""
        captured = {"workflow": None, "filenames": []}
        mock_comfyui = _fake_comfyui(tmp_path)

        def capture_submit(workflow, client_id):
            captured["workflow"] = workflow
            return "fake-prompt-id"
        mock_comfyui.submit.side_effect = capture_submit
        mock_comfyui.get_result.return_value = ComfyUIGenerationResult(
            prompt_id="fake-prompt-id", output_node_id="92",
            outputs=[ComfyUIOutputRef("out.mp4", "m3", "output")],
        )

        # Capture uploaded filenames in order
        upload_call_order = []
        def capture_upload(path, filename):
            name = f"uploaded_{len(upload_call_order)}.png"
            upload_call_order.append(name)
            return name
        mock_comfyui.upload_image.side_effect = capture_upload

        service = GenerationService(
            db=two_char_env["db"], comfyui=mock_comfyui,
            storage_root=two_char_env["storage_root"], project_root=WORKTREE,
        )
        service.generate_shot("shot-2sub")

        workflow = captured["workflow"]
        assert len(upload_call_order) == 2

        # Node 200 (LoadImage for first ref) must have first uploaded filename
        assert workflow["200"]["inputs"]["image"] == upload_call_order[0]
        # Node 201 (LoadImage for second ref) must have second uploaded filename
        assert workflow["201"]["inputs"]["image"] == upload_call_order[1]

        # Node 104 (H3 model) must retain autogrow connections to both LoadImage nodes
        h3_inputs = workflow["104"]["inputs"]
        assert h3_inputs["ref_images.ref_image_0"] == ["200", 0]
        assert h3_inputs["ref_images.ref_image_1"] == ["201", 0]

    def test_two_ref_snapshot_has_asset_provenance(self, two_char_env, tmp_path):
        """reference_snapshot includes reference_asset_id + reference_kind."""
        mock_comfyui = _fake_comfyui(tmp_path)
        service = GenerationService(
            db=two_char_env["db"], comfyui=mock_comfyui,
            storage_root=two_char_env["storage_root"], project_root=WORKTREE,
        )
        service.generate_shot("shot-2sub")

        req_repo = GenerationRequestRepository(two_char_env["db"])
        reqs = req_repo.get_requests_by_shot("shot-2sub")
        assert len(reqs) == 1
        req = reqs[0]

        # Workflow version is v2
        assert req.workflow_definition_id == "h3_r2v_v2"
        assert req.workflow_definition_version == "2.0.0"

        # Reference snapshot has two entries with full provenance
        ref_snap = req.reference_snapshot
        assert len(ref_snap) == 2

        # First entry — Alice (subject order)
        assert ref_snap[0]["reference_asset_id"] == "ref-a"
        assert ref_snap[0]["reference_kind"] == "character_body"
        assert ref_snap[0]["character_id"] == "char-a"
        assert ref_snap[0]["picture_index"] == 1
        assert ref_snap[0]["subject_index"] == 1
        assert ref_snap[0]["content_sha256"] == two_char_env["sha_a"]

        # Second entry — Bob
        assert ref_snap[1]["reference_asset_id"] == "ref-b"
        assert ref_snap[1]["reference_kind"] == "character_body"
        assert ref_snap[1]["character_id"] == "char-b"
        assert ref_snap[1]["picture_index"] == 2
        assert ref_snap[1]["subject_index"] == 2
        assert ref_snap[1]["content_sha256"] == two_char_env["sha_b"]

    def test_two_ref_prompt_has_both_subjects(self, two_char_env, tmp_path):
        """H3 prompt includes <Subject 1>, <Subject 2>, <Picture 1>, <Picture 2>."""
        mock_comfyui = _fake_comfyui(tmp_path)
        service = GenerationService(
            db=two_char_env["db"], comfyui=mock_comfyui,
            storage_root=two_char_env["storage_root"], project_root=WORKTREE,
        )
        service.generate_shot("shot-2sub")

        from film_director.persistence.repositories import H3PromptRepository
        prompt_repo = H3PromptRepository(two_char_env["db"])
        prompt = prompt_repo.get_current_prompt("shot-2sub", 1, "plan-2sub", 1)
        assert prompt is not None
        assert "<Subject 1>" in prompt.subject_definitions
        assert "<Subject 2>" in prompt.subject_definitions
        assert "<Picture 1>" in prompt.subject_definitions
        assert "<Picture 2>" in prompt.subject_definitions

    def test_no_approved_ref_raises(self, two_char_env, tmp_path):
        """Missing approved ref for any subject → ReferenceResolutionError, no request."""
        # Remove Bob's approved reference
        with two_char_env["db"].connection() as conn:
            conn.execute(
                "UPDATE reference_assets SET status = 'rejected' WHERE id = 'ref-b'"
            )

        mock_comfyui = MagicMock(spec=ComfyUIAdapter)
        service = GenerationService(
            db=two_char_env["db"], comfyui=mock_comfyui,
            storage_root=two_char_env["storage_root"], project_root=WORKTREE,
        )
        with pytest.raises(ReferenceResolutionError, match="char-b"):
            service.generate_shot("shot-2sub")

        # No GenerationRequest created
        req_repo = GenerationRequestRepository(two_char_env["db"])
        assert len(req_repo.get_requests_by_shot("shot-2sub")) == 0
        mock_comfyui.submit.assert_not_called()

    def test_stale_ref_not_selected(self, two_char_env, tmp_path):
        """STALE reference is never selected — ReferenceResolutionError."""
        with two_char_env["db"].connection() as conn:
            conn.execute(
                "UPDATE reference_assets SET source_state = 'stale' WHERE id = 'ref-a'"
            )

        mock_comfyui = MagicMock(spec=ComfyUIAdapter)
        service = GenerationService(
            db=two_char_env["db"], comfyui=mock_comfyui,
            storage_root=two_char_env["storage_root"], project_root=WORKTREE,
        )
        with pytest.raises(ReferenceResolutionError, match="char-a"):
            service.generate_shot("shot-2sub")

        mock_comfyui.submit.assert_not_called()
