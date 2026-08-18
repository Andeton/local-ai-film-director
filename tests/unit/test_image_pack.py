"""Tests for M7.G.C — H3 image-pack workflow, recipe, and integration."""
from __future__ import annotations

import hashlib
import json
import os
import shutil

import pytest

from film_director.generation.conditioning import (
    H3_IMAGE_PACK_RECIPE,
    build_h3_image_pack_recipe,
    compute_recipe_fingerprint,
)
from film_director.generation.workflow_registry import WorkflowResolver
from film_director.models.reference import AssetRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_project_root():
    """Find the repo root containing workflows/h3/."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return root


@pytest.fixture
def project_with_workflows(tmp_path):
    """Create a temp project root with all workflow templates."""
    real_root = _get_project_root()
    wf_dir = tmp_path / "workflows" / "h3"
    wf_dir.mkdir(parents=True)
    for name in ["r2v_v1.json", "r2v_v2.json", "flf_v1.json", "r2v_image_pack_v1.json"]:
        src = os.path.join(real_root, "workflows", "h3", name)
        if os.path.exists(src):
            shutil.copy2(src, wf_dir / name)
    return str(tmp_path)


# ===========================================================================
# WORKFLOW
# ===========================================================================

class TestImagePackWorkflow:
    def test_loads_and_validates(self, project_with_workflows):
        resolver = WorkflowResolver(project_with_workflows)
        defn = resolver.resolve_image_pack()
        template = resolver.load_template(defn)
        assert isinstance(template, dict)

    def test_fingerprint_stable(self):
        root = _get_project_root()
        data = open(os.path.join(root, "workflows", "h3", "r2v_image_pack_v1.json"), "rb").read()
        fp = hashlib.sha256(data).hexdigest()
        assert fp == "32caca08d5f4bd0b4578efc4f709024a7d222dd933f9224d9e718bc20f4a7351"

    def test_four_image_slots_materialized(self, project_with_workflows):
        resolver = WorkflowResolver(project_with_workflows)
        defn = resolver.resolve_image_pack()
        template = resolver.load_template(defn)
        node_104 = template["104"]
        inputs = node_104["inputs"]
        assert "ref_images.ref_image_0" in inputs
        assert "ref_images.ref_image_1" in inputs
        assert "ref_images.ref_image_2" in inputs
        assert "ref_images.ref_image_3" in inputs

    def test_no_ref_video_conditioning(self, project_with_workflows):
        resolver = WorkflowResolver(project_with_workflows)
        defn = resolver.resolve_image_pack()
        template = resolver.load_template(defn)
        node_104 = template["104"]["inputs"]
        for key in node_104:
            assert "ref_video" not in key.lower(), f"Unexpected ref_video key: {key}"
            assert "ref_audio" not in key.lower() or key == "audio_vae", \
                f"Unexpected ref_audio conditioning: {key}"

    def test_frozen_workflows_unchanged(self):
        root = _get_project_root()
        expected = {
            "r2v_v1": "3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4",
            "r2v_v2": "b4930400f0433fdd09f3bd4f8a20d55394050c7d4558eddd6f2e7046e110f3b9",
            "flf_v1": "47d6706c93865d43213a8c1bdf46b4d07a1665155cfae6a7721239b5d42c43d6",
        }
        for name, exp_fp in expected.items():
            data = open(os.path.join(root, "workflows", "h3", f"{name}.json"), "rb").read()
            assert hashlib.sha256(data).hexdigest() == exp_fp, f"{name} fingerprint changed!"

    def test_workflow_def_id_and_version(self, project_with_workflows):
        resolver = WorkflowResolver(project_with_workflows)
        defn = resolver.resolve_image_pack()
        assert defn.id == "h3_r2v_image_pack_v1"
        assert defn.version == "1.0.0"
        assert defn.strategy == "REFERENCE_TO_VIDEO"

    def test_four_load_image_nodes(self, project_with_workflows):
        resolver = WorkflowResolver(project_with_workflows)
        template = resolver.load_template(resolver.resolve_image_pack())
        for nid in ["200", "201", "202", "203"]:
            assert nid in template, f"Missing LoadImage node {nid}"
            assert template[nid]["class_type"] == "LoadImage"


# ===========================================================================
# RECIPE
# ===========================================================================

class TestImagePackRecipe:
    def test_recipe_registered(self):
        assert H3_IMAGE_PACK_RECIPE.id == "h3_r2v_image_pack_v1"
        assert H3_IMAGE_PACK_RECIPE.version == "1.0.0"

    def test_character_role_required(self):
        roles = {rr.role: rr for rr in H3_IMAGE_PACK_RECIPE.role_requirements}
        assert AssetRole.CHARACTER_FACE_CLOSEUP in roles
        assert roles[AssetRole.CHARACTER_FACE_CLOSEUP].required is True
        assert roles[AssetRole.CHARACTER_FACE_CLOSEUP].slot_index == 0

    def test_environment_role_required(self):
        roles = {rr.role: rr for rr in H3_IMAGE_PACK_RECIPE.role_requirements}
        assert AssetRole.ENVIRONMENT_VIEW in roles
        assert roles[AssetRole.ENVIRONMENT_VIEW].required is True
        assert roles[AssetRole.ENVIRONMENT_VIEW].slot_index == 1

    def test_continuity_role_required(self):
        roles = {rr.role: rr for rr in H3_IMAGE_PACK_RECIPE.role_requirements}
        assert AssetRole.CONTINUITY_FRAME in roles
        assert roles[AssetRole.CONTINUITY_FRAME].required is True
        assert roles[AssetRole.CONTINUITY_FRAME].slot_index == 2

    def test_prop_role_optional(self):
        roles = {rr.role: rr for rr in H3_IMAGE_PACK_RECIPE.role_requirements}
        assert AssetRole.PROP_REFERENCE in roles
        assert roles[AssetRole.PROP_REFERENCE].required is False
        assert roles[AssetRole.PROP_REFERENCE].slot_index == 3

    def test_deterministic_slot_order(self):
        slots = [(rr.slot_index, rr.role) for rr in H3_IMAGE_PACK_RECIPE.role_requirements]
        assert slots == [
            (0, AssetRole.CHARACTER_FACE_CLOSEUP),
            (1, AssetRole.ENVIRONMENT_VIEW),
            (2, AssetRole.CONTINUITY_FRAME),
            (3, AssetRole.PROP_REFERENCE),
        ]

    def test_workflow_provenance(self):
        assert H3_IMAGE_PACK_RECIPE.workflow_definition_id == "h3_r2v_image_pack_v1"
        assert H3_IMAGE_PACK_RECIPE.workflow_definition_version == "1.0.0"
        assert H3_IMAGE_PACK_RECIPE.workflow_template_fingerprint == \
            "32caca08d5f4bd0b4578efc4f709024a7d222dd933f9224d9e718bc20f4a7351"

    def test_no_ref_video_audio(self):
        assert H3_IMAGE_PACK_RECIPE.supports_video_references is False
        assert H3_IMAGE_PACK_RECIPE.supports_audio_references is False
        assert H3_IMAGE_PACK_RECIPE.supports_image_references is True

    def test_fingerprint_deterministic(self):
        r1 = build_h3_image_pack_recipe()
        r2 = build_h3_image_pack_recipe()
        assert r1.fingerprint == r2.fingerprint
        assert len(r1.fingerprint) == 64

    def test_prompt_tag_convention(self):
        assert H3_IMAGE_PACK_RECIPE.prompt_tag_convention == "<Picture N>"


# ===========================================================================
# ORDER INVARIANTS
# ===========================================================================

class TestOrderInvariants:
    def test_character_always_picture_1(self):
        reqs = H3_IMAGE_PACK_RECIPE.role_requirements
        assert reqs[0].role == AssetRole.CHARACTER_FACE_CLOSEUP
        assert reqs[0].slot_index == 0  # → ref_image_0 → Picture 1

    def test_environment_always_picture_2(self):
        reqs = H3_IMAGE_PACK_RECIPE.role_requirements
        assert reqs[1].role == AssetRole.ENVIRONMENT_VIEW
        assert reqs[1].slot_index == 1  # → ref_image_1 → Picture 2

    def test_continuity_always_picture_3(self):
        reqs = H3_IMAGE_PACK_RECIPE.role_requirements
        assert reqs[2].role == AssetRole.CONTINUITY_FRAME
        assert reqs[2].slot_index == 2  # → ref_image_2 → Picture 3

    def test_prop_always_picture_4(self):
        reqs = H3_IMAGE_PACK_RECIPE.role_requirements
        assert reqs[3].role == AssetRole.PROP_REFERENCE
        assert reqs[3].slot_index == 3  # → ref_image_3 → Picture 4


# ===========================================================================
# BACKWARD COMPATIBILITY
# ===========================================================================

class TestBackwardCompatibility:
    def test_existing_r2v_resolve_unchanged(self, project_with_workflows):
        resolver = WorkflowResolver(project_with_workflows)
        v1 = resolver.resolve("REFERENCE_TO_VIDEO")
        assert v1.id == "h3_r2v_v1"
        v2 = resolver.resolve_for_reference_count(2)
        assert v2.id == "h3_r2v_v2"

    def test_existing_flf_resolve_unchanged(self, project_with_workflows):
        resolver = WorkflowResolver(project_with_workflows)
        flf = resolver.resolve_for_continuity(True, 0)
        assert flf.id == "h3_flf_v1"

    def test_existing_paths_dont_require_image_pack(self, project_with_workflows):
        """Old generation paths must work without image pack."""
        resolver = WorkflowResolver(project_with_workflows)
        # 1-ref chain head still works
        v1 = resolver.resolve_for_reference_count(1)
        assert v1.id == "h3_r2v_v1"
        # Continuity still works
        flf = resolver.resolve_for_continuity(True, 0)
        assert flf.id == "h3_flf_v1"
