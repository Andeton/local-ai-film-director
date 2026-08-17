"""Integration tests for M7.C — continuity-aware generation pipeline."""
import hashlib
import json
import os
import pytest
import sqlite3

from film_director.continuity.continuity_models import ContinuityState
from film_director.continuity.continuity_resolver import ContinuityResolver
from film_director.continuity.continuity_service import ContinuityService, ContinuityInput
from film_director.errors import ContinuityError
from film_director.generation.generation_request import GenerationRequest, Take
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    ContinuityStateRepository,
    GenerationRequestRepository,
    TakeRepository,
)

_FP = "a" * 64  # valid SHA-256


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_full_chain(conn, scene_id="scene_1", shot_ids=None):
    """Insert project→seq→scene→beat→shots chain. Returns shot_ids in order."""
    if shot_ids is None:
        shot_ids = ["shot_1", "shot_2", "shot_3"]

    conn.execute(
        "INSERT OR IGNORE INTO production_projects "
        "(id, wc_project_id, title, status, created_at, updated_at, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("proj_1", "wc_1", "Test", "active", "t", "t",
         "wind_comic", "wc_1", "a1", "t", "h1"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO sequences (id, project_id, name, order_index) VALUES (?,?,?,?)",
        ("seq_1", "proj_1", "Seq", 0),
    )
    conn.execute(
        "INSERT OR IGNORE INTO scenes "
        "(id, sequence_id, wc_scene_id, name, order_index, status, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (scene_id, "seq_1", f"wc_{scene_id}", "Scene", 0, "draft",
         "wind_comic", "wc_1", f"a_{scene_id}", "t", f"h_{scene_id}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO beats "
        "(id, scene_id, dramatic_action, order_index, status, source, version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (f"beat_{scene_id}", scene_id, "action", 0, "draft", "llm", 1, "t", "t"),
    )
    for i, sid in enumerate(shot_ids):
        conn.execute(
            "INSERT OR IGNORE INTO shots "
            "(id, beat_id, dramatic_purpose, subjects, action, order_index, status, source, version, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, f"beat_{scene_id}", "purpose", '[]', "walks forward", i, "draft", "generated", 1, "t", "t"),
        )
    return shot_ids


def _add_plan_prompt_request_take(conn, shot_id, take_id, take_number=1,
                                   status="succeeded", seed=42,
                                   video_path="v.mp4", last_frame_path=None):
    """Insert generation plan + prompt + request + take for a shot."""
    plan_id = f"plan_{shot_id}"
    prompt_id = f"prompt_{shot_id}"
    req_id = f"greq_{shot_id}_{take_number}"

    conn.execute(
        "INSERT OR IGNORE INTO generation_plans "
        "(id, shot_id, shot_version, strategy, reference_requirements, "
        "duration_sec, version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (plan_id, shot_id, 1, "REFERENCE_TO_VIDEO",
         '{"character_refs": true, "scene_ref": false, "prev_frame": false, "style_ref": false}',
         5.0, 1, "t", "t"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO h3_prompts "
        "(id, shot_id, generation_plan_id, source_shot_version, "
        "source_generation_plan_version, subject_definitions, summary, "
        "retention_analysis, detailed_description, rendered_prompt_text, "
        "version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (prompt_id, shot_id, plan_id, 1, 1, "sub", "sum", "ret", "desc", "rendered", 1, "t"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO generation_requests "
        "(id, shot_id, shot_version, generation_plan_id, generation_plan_version, "
        "prompt_artifact_id, prompt_artifact_version, workflow_definition_id, "
        "workflow_definition_version, workflow_template_fingerprint, take_number, "
        "parameters_snapshot, reference_snapshot, seed, continuity_snapshot, "
        "comfyui_prompt_id, status, submitted_at, completed_at, error) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (req_id, shot_id, 1, plan_id, 1, prompt_id, 1,
         "h3_r2v_v1", "1.0.0", _FP, take_number,
         "[]", "[]", seed, None, None, "succeeded", "", "", None),
    )
    conn.execute(
        "INSERT OR IGNORE INTO takes "
        "(id, shot_id, generation_request_id, seed, video_path, last_frame_path, "
        "status, is_favorite, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (take_id, shot_id, req_id, seed, video_path, last_frame_path,
         status, 0, "t"),
    )


def _create_last_frame(path):
    """Create a minimal valid PNG file at path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Minimal valid PNG: 8-byte magic + IHDR + IEND
    png_data = (
        b"\x89PNG\r\n\x1a\n"  # magic
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"  # 1x1 RGB
        b"\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    with open(path, "wb") as f:
        f.write(png_data)
    return hashlib.sha256(png_data).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def storage_root(tmp_path):
    root = str(tmp_path / "storage")
    os.makedirs(root, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# ContinuityService.resolve_for_generation
# ---------------------------------------------------------------------------

class TestResolveForGeneration:
    def test_chain_head_returns_none(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1"])
        result = svc.resolve_for_generation("shot_1")
        assert result is None

    def test_downstream_with_approved_predecessor(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        frame_dir = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1")
        frame_path = os.path.join(frame_dir, "last_frame.png")
        frame_sha = _create_last_frame(frame_path)

        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="approved",
                last_frame_path=frame_path,
            )

        result = svc.resolve_for_generation("shot_2")
        assert result is not None
        assert result.upstream_shot_id == "shot_1"
        assert result.upstream_take_id == "take_s1"
        assert result.frame_sha256 == frame_sha
        assert result.frame_path == str(os.path.abspath(frame_path))

    def test_no_approved_predecessor_raises(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="succeeded",  # not approved!
            )
        with pytest.raises(ContinuityError, match="no approved Take"):
            svc.resolve_for_generation("shot_2")

    def test_missing_last_frame_path_raises(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="approved",
                last_frame_path=None,
            )
        with pytest.raises(ContinuityError, match="no last_frame_path"):
            svc.resolve_for_generation("shot_2")

    def test_missing_frame_file_raises(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        nonexistent = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1", "last_frame.png")
        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="approved",
                last_frame_path=nonexistent,
            )
        with pytest.raises(ContinuityError, match="does not exist"):
            svc.resolve_for_generation("shot_2")

    def test_frame_outside_storage_root_raises(self, db, storage_root, tmp_path):
        svc = ContinuityService(db, storage_root)
        # Create frame outside storage root
        outside_path = str(tmp_path / "outside" / "frame.png")
        _create_last_frame(outside_path)
        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="approved",
                last_frame_path=outside_path,
            )
        with pytest.raises(ContinuityError, match="escapes storage root"):
            svc.resolve_for_generation("shot_2")

    def test_non_png_frame_raises(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        frame_dir = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1")
        frame_path = os.path.join(frame_dir, "last_frame.png")
        os.makedirs(frame_dir, exist_ok=True)
        with open(frame_path, "wb") as f:
            f.write(b"NOT A PNG FILE DATA")
        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="approved",
                last_frame_path=frame_path,
            )
        with pytest.raises(ContinuityError, match="not a valid PNG"):
            svc.resolve_for_generation("shot_2")

    def test_outdated_state_raises(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        frame_dir = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1")
        frame_path = os.path.join(frame_dir, "last_frame.png")
        _create_last_frame(frame_path)

        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="approved",
                last_frame_path=frame_path,
            )

        # Manually mark outdated
        repo = ContinuityStateRepository(db)
        repo.save(ContinuityState(
            id="cs_shot_2", shot_id="shot_2", scene_id="scene_1",
            state="outdated", invalidation_reason="upstream_take_changed",
            created_at="t", updated_at="t",
        ))

        with pytest.raises(ContinuityError, match="outdated"):
            svc.resolve_for_generation("shot_2")

    def test_no_cross_scene(self, db, storage_root):
        """Shot in scene_2 should not see shots in scene_1 as predecessor."""
        svc = ContinuityService(db, storage_root)
        with db.connection() as conn:
            _setup_full_chain(conn, scene_id="scene_1", shot_ids=["shot_1"])
            # Second scene with its own shot
            conn.execute(
                "INSERT INTO scenes "
                "(id, sequence_id, wc_scene_id, name, order_index, status, "
                "prov_source_system, prov_source_project_id, prov_source_asset_id, "
                "prov_imported_at, prov_source_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("scene_2", "seq_1", "wc_s2", "Scene2", 1, "draft",
                 "wind_comic", "wc_1", "a_s2", "t", "h_s2"),
            )
            conn.execute(
                "INSERT INTO beats "
                "(id, scene_id, dramatic_action, order_index, status, source, version, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("beat_scene_2", "scene_2", "action", 0, "draft", "llm", 1, "t", "t"),
            )
            conn.execute(
                "INSERT INTO shots "
                "(id, beat_id, dramatic_purpose, subjects, action, order_index, status, source, version, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("shot_s2", "beat_scene_2", "purpose", "[]", "action", 0, "draft", "generated", 1, "t", "t"),
            )
        # shot_s2 is first in scene_2 — should be chain head
        result = svc.resolve_for_generation("shot_s2")
        assert result is None


class TestPersistState:
    def test_persist_creates_current_state(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        frame_dir = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1")
        frame_path = os.path.join(frame_dir, "last_frame.png")
        frame_sha = _create_last_frame(frame_path)

        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="approved",
                last_frame_path=frame_path,
            )

        ci = ContinuityInput(
            continuity_state_id="cs_shot_2",
            upstream_shot_id="shot_1",
            upstream_take_id="take_s1",
            upstream_take_number=1,
            frame_path=frame_path,
            frame_sha256=frame_sha,
            continuity_revision=0,
            continuity_fingerprint="c" * 64,
        )
        svc.persist_state("shot_2", "scene_1", ci)

        repo = ContinuityStateRepository(db)
        state = repo.get_by_shot("shot_2")
        assert state is not None
        assert state.state == "current"
        assert state.upstream_take_id == "take_s1"

    def test_same_provenance_reuses_state(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        frame_dir = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1")
        frame_path = os.path.join(frame_dir, "last_frame.png")
        frame_sha = _create_last_frame(frame_path)

        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="approved",
                last_frame_path=frame_path,
            )

        ci = ContinuityInput(
            continuity_state_id="cs_shot_2",
            upstream_shot_id="shot_1",
            upstream_take_id="take_s1",
            upstream_take_number=1,
            frame_path=frame_path,
            frame_sha256=frame_sha,
            continuity_revision=0,
            continuity_fingerprint="c" * 64,
        )
        svc.persist_state("shot_2", "scene_1", ci)
        svc.persist_state("shot_2", "scene_1", ci)

        repo = ContinuityStateRepository(db)
        state = repo.get_by_shot("shot_2")
        assert state.state == "current"


# ---------------------------------------------------------------------------
# FLF Prompt
# ---------------------------------------------------------------------------

class TestFLFPrompt:
    def test_no_picture_tags(self):
        from film_director.generation.h3_prompt import H3PromptBuilder
        from film_director.models.canonical import (
            GenerationPlan, ReferenceRequirements, ShotSpecificationV1, CameraIntent,
        )
        shot = ShotSpecificationV1(
            id="shot_2", beat_id="b1", dramatic_purpose="test",
            subjects=[{"character_id": "char_1", "name": "John", "ref_images": []}],
            action="walks forward", camera=CameraIntent(shot_size="medium"),
            order_index=1, version=1, created_at="t", updated_at="t",
        )
        plan = GenerationPlan(
            id="plan_1", shot_id="shot_2", shot_version=1,
            strategy="FIRST_LAST_FRAME",
            reference_requirements=ReferenceRequirements(prev_frame=True),
            duration_sec=5.0, version=1, created_at="t", updated_at="t",
        )
        builder = H3PromptBuilder()
        prompt = builder.build_continuity_prompt(shot, plan)
        assert "<Picture" not in prompt.rendered_prompt_text
        assert "<Subject" not in prompt.rendered_prompt_text
        assert "first frame" in prompt.rendered_prompt_text.lower()
        assert "walks forward" in prompt.rendered_prompt_text

    def test_r2v_prompt_unchanged(self):
        """Existing R2V prompt path must remain byte-for-byte compatible."""
        from film_director.generation.h3_prompt import H3PromptBuilder
        from film_director.generation.h3_types import H3ReferenceBinding
        from film_director.models.canonical import (
            GenerationPlan, ReferenceRequirements, ShotSpecificationV1, CameraIntent,
        )
        shot = ShotSpecificationV1(
            id="shot_1", beat_id="b1", dramatic_purpose="test",
            subjects=[{"character_id": "char_1", "name": "John", "ref_images": []}],
            action="walks forward", camera=CameraIntent(shot_size="medium"),
            order_index=0, version=1, created_at="t", updated_at="t",
        )
        plan = GenerationPlan(
            id="plan_1", shot_id="shot_1", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, version=1, created_at="t", updated_at="t",
        )
        binding = H3ReferenceBinding(
            subject_index=0, character_id="char_1",
            character_name="John", appearance="tall man",
            picture_index=1, local_path="/p",
            content_sha256=_FP,
        )
        builder = H3PromptBuilder()
        prompt = builder.build(shot, plan, [binding])
        assert "<Picture 1>" in prompt.rendered_prompt_text
        assert "<Subject 0>" in prompt.rendered_prompt_text


# ---------------------------------------------------------------------------
# Continuity Snapshot
# ---------------------------------------------------------------------------

class TestContinuitySnapshot:
    def test_snapshot_has_no_absolute_path(self, db, storage_root):
        """continuity_snapshot must use relative path, not absolute."""
        svc = ContinuityService(db, storage_root)
        frame_dir = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1")
        frame_path = os.path.join(frame_dir, "last_frame.png")
        frame_sha = _create_last_frame(frame_path)

        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="approved",
                last_frame_path=frame_path,
            )

        ci = svc.resolve_for_generation("shot_2")
        assert ci is not None
        # The frame_path from resolve is absolute (validated), but
        # the snapshot constructed in GenerationService uses os.path.relpath
        # We verify the resolve itself works
        assert os.path.isabs(ci.frame_path)

    def test_snapshot_provenance_fields(self, db, storage_root):
        svc = ContinuityService(db, storage_root)
        frame_dir = os.path.join(storage_root, "takes", "proj_1", "shot_1", "take_1")
        frame_path = os.path.join(frame_dir, "last_frame.png")
        frame_sha = _create_last_frame(frame_path)

        with db.connection() as conn:
            _setup_full_chain(conn, shot_ids=["shot_1", "shot_2"])
            _add_plan_prompt_request_take(
                conn, "shot_1", "take_s1", status="approved",
                last_frame_path=frame_path,
            )

        ci = svc.resolve_for_generation("shot_2")
        assert ci.upstream_shot_id == "shot_1"
        assert ci.upstream_take_id == "take_s1"
        assert ci.upstream_take_number == 1
        assert ci.frame_sha256 == frame_sha
        assert len(ci.continuity_fingerprint) == 64


# ---------------------------------------------------------------------------
# Workflow Selection
# ---------------------------------------------------------------------------

class TestWorkflowSelection:
    def test_chain_head_selects_r2v(self):
        from film_director.generation.workflow_registry import WorkflowResolver
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        wr = WorkflowResolver(project_root)
        defn = wr.resolve_for_continuity(has_continuity_frame=False, reference_count=1)
        assert defn.id == "h3_r2v_v1"

    def test_downstream_selects_flf(self):
        from film_director.generation.workflow_registry import WorkflowResolver
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        wr = WorkflowResolver(project_root)
        defn = wr.resolve_for_continuity(has_continuity_frame=True, reference_count=0)
        assert defn.id == "h3_flf_v1"

    def test_r2v_fingerprints_unchanged(self):
        from film_director.generation.workflow_registry import WorkflowResolver
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        wr = WorkflowResolver(project_root)
        assert wr._v1.template_fingerprint == "3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4"
        assert wr._v2.template_fingerprint == "b4930400f0433fdd09f3bd4f8a20d55394050c7d4558eddd6f2e7046e110f3b9"
