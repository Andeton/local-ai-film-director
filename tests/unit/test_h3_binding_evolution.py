"""Tests for M5.E — H3ReferenceBinding evolution, prompt, workflow v2, parameter injection."""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from film_director.errors import (
    ParameterResolutionError,
    ReferenceResolutionError,
)
from film_director.generation.h3_prompt import H3PromptBuilder
from film_director.generation.h3_types import H3ReferenceBinding
from film_director.generation.workflow_registry import WorkflowResolver
from film_director.models.canonical import (
    CameraIntent,
    GenerationPlan,
    ReferenceRequirements,
    ShotSpecificationV1,
    ShotSubject,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHA = "a" * 64


def _binding(picture_index=1, subject_index=1, character_id="char-1",
             character_name="Hero", appearance="tall", **kw):
    base = dict(
        reference_asset_id="ref-1", reference_kind="character_body",
        subject_index=subject_index, character_id=character_id,
        character_name=character_name, appearance=appearance,
        picture_index=picture_index, local_path="/refs/hero.png",
        content_sha256=_SHA,
    )
    base.update(kw)
    return H3ReferenceBinding(**base)


def _shot(subjects=None):
    if subjects is None:
        subjects = [ShotSubject(character_id="char-1", name="Hero", ref_images=[])]
    return ShotSpecificationV1(
        id="shot-1", beat_id="beat-1", dramatic_purpose="test",
        subjects=subjects, action="test action", environment={},
        camera=CameraIntent(shot_size="wide"), lighting={},
        audio_intent={}, duration_sec=5.0, continuity_inputs={},
        order_index=0, status="draft", source="generated", version=1,
        created_at="t", updated_at="t",
    )


def _plan():
    return GenerationPlan(
        id="plan-1", shot_id="shot-1", shot_version=1,
        strategy="REFERENCE_TO_VIDEO",
        reference_requirements=ReferenceRequirements(),
        duration_sec=5.0, status="draft", version=1,
        created_at="t", updated_at="t",
    )


# ===========================================================================
# BINDING EVOLUTION
# ===========================================================================

class TestBindingEvolution:
    def test_new_fields_round_trip(self):
        b = _binding(reference_asset_id="ref-42", reference_kind="character_body")
        assert b.reference_asset_id == "ref-42"
        assert b.reference_kind == "character_body"

    def test_subject_index_differs_from_picture_index(self):
        b = _binding(picture_index=2, subject_index=1)
        assert b.picture_index == 2
        assert b.subject_index == 1

    def test_subject_index_may_repeat(self):
        b1 = _binding(picture_index=1, subject_index=1)
        b2 = _binding(picture_index=2, subject_index=1)
        assert b1.subject_index == b2.subject_index

    def test_subject_index_may_be_none(self):
        b = _binding(picture_index=1, subject_index=None,
                     character_id=None, character_name=None, appearance=None)
        assert b.subject_index is None
        assert b.character_id is None

    def test_picture_index_contiguous_validation(self):
        """PromptBuilder enforces contiguous picture indexes."""
        builder = H3PromptBuilder()
        bindings = [_binding(picture_index=1), _binding(picture_index=3)]
        with pytest.raises(ReferenceResolutionError, match="picture_index"):
            builder.build(shot=_shot(), plan=_plan(), bindings=bindings)

    def test_picture_index_unique(self):
        builder = H3PromptBuilder()
        bindings = [_binding(picture_index=1), _binding(picture_index=1)]
        with pytest.raises(ReferenceResolutionError, match="picture_index"):
            builder.build(shot=_shot(), plan=_plan(), bindings=bindings)

    def test_non_subject_binding_no_fake_subject(self):
        """Non-subject binding (subject_index=None) creates no Subject definition."""
        builder = H3PromptBuilder()
        b = _binding(picture_index=1, subject_index=None,
                     character_id=None, character_name=None, appearance=None)
        prompt = builder.build(shot=_shot(), plan=_plan(), bindings=[b])
        assert "<Subject" not in prompt.subject_definitions


# ===========================================================================
# PROMPT COMPATIBILITY
# ===========================================================================

class TestPromptCompat:
    def test_one_reference_compat(self):
        builder = H3PromptBuilder()
        bindings = [_binding(picture_index=1, subject_index=1)]
        prompt = builder.build(shot=_shot(), plan=_plan(), bindings=bindings)
        assert "<Subject 1>" in prompt.subject_definitions
        assert "<Picture 1>" in prompt.subject_definitions

    def test_two_references_both_pictures(self):
        builder = H3PromptBuilder()
        subjects = [
            ShotSubject(character_id="char-A", name="A", ref_images=[]),
            ShotSubject(character_id="char-B", name="B", ref_images=[]),
        ]
        bindings = [
            _binding(picture_index=1, subject_index=1, character_name="Hero A"),
            _binding(picture_index=2, subject_index=2, character_name="Hero B"),
        ]
        prompt = builder.build(shot=_shot(subjects=subjects), plan=_plan(),
                               bindings=bindings)
        assert "<Picture 1>" in prompt.subject_definitions
        assert "<Picture 2>" in prompt.subject_definitions
        assert "<Subject 1>" in prompt.subject_definitions
        assert "<Subject 2>" in prompt.subject_definitions


# ===========================================================================
# WORKFLOW REGISTRY
# ===========================================================================

class TestWorkflowRegistry:
    @pytest.fixture
    def project_root(self):
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def test_r2v_v1_hash_unchanged(self, project_root):
        v1_path = os.path.join(project_root, "workflows", "h3", "r2v_v1.json")
        with open(v1_path, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        assert actual == "3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4"

    def test_r2v_v2_fingerprint_verified(self, project_root):
        resolver = WorkflowResolver(project_root)
        v2 = resolver.resolve_for_reference_count(2)
        # load_template verifies fingerprint
        template = resolver.load_template(v2)
        assert "201" in template  # second LoadImage node

    def test_v2_has_two_ref_mappings(self, project_root):
        resolver = WorkflowResolver(project_root)
        v2 = resolver.resolve_for_reference_count(2)
        assert "ref_image_0" in v2.parameter_mappings
        assert "ref_image_1" in v2.parameter_mappings

    def test_v2_autogrow_connections(self, project_root):
        resolver = WorkflowResolver(project_root)
        v2 = resolver.resolve_for_reference_count(2)
        template = resolver.load_template(v2)
        h3_node = template["104"]
        assert h3_node["inputs"]["ref_images.ref_image_0"] == ["200", 0]
        assert h3_node["inputs"]["ref_images.ref_image_1"] == ["201", 0]

    def test_one_reference_selects_v1(self, project_root):
        resolver = WorkflowResolver(project_root)
        v1 = resolver.resolve_for_reference_count(1)
        assert v1.id == "h3_r2v_v1"

    def test_two_references_selects_v2(self, project_root):
        resolver = WorkflowResolver(project_root)
        v2 = resolver.resolve_for_reference_count(2)
        assert v2.id == "h3_r2v_v2"

    def test_zero_references_fails(self, project_root):
        resolver = WorkflowResolver(project_root)
        with pytest.raises(ReferenceResolutionError):
            resolver.resolve_for_reference_count(0)

    def test_more_than_two_fails(self, project_root):
        resolver = WorkflowResolver(project_root)
        with pytest.raises(ParameterResolutionError):
            resolver.resolve_for_reference_count(3)

    def test_v1_still_resolves_by_strategy(self, project_root):
        resolver = WorkflowResolver(project_root)
        v1 = resolver.resolve("REFERENCE_TO_VIDEO")
        assert v1.id == "h3_r2v_v1"
