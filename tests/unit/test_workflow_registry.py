"""Tests for WorkflowDefinition and WorkflowResolver."""
import copy
import hashlib
import json
import os
import tempfile

import pytest

from film_director.errors import UnsupportedStrategyError, WorkflowTemplateError
from film_director.generation.workflow_registry import (
    WorkflowDefinition,
    WorkflowResolver,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_PATH = os.path.join(WORKTREE, "workflows", "h3", "r2v_v1.json")
EXPECTED_SHA = "3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4"


# ---------------------------------------------------------------------------
# WorkflowDefinition
# ---------------------------------------------------------------------------

class TestWorkflowDefinition:
    def test_frozen(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        with pytest.raises(AttributeError):
            defn.id = "new_id"  # type: ignore[misc]

    def test_r2v_identity(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        assert defn.id == "h3_r2v_v1"
        assert defn.version == "1.0.0"
        assert defn.strategy == "REFERENCE_TO_VIDEO"

    def test_r2v_template_path(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        assert defn.template_path.endswith(os.path.join("workflows", "h3", "r2v_v1.json"))

    def test_r2v_fingerprint(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        assert defn.template_fingerprint == EXPECTED_SHA

    def test_r2v_parameter_mappings(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        mappings = defn.parameter_mappings
        assert "prompt" in mappings
        assert "seed" in mappings
        assert "duration" in mappings
        assert "aspect" in mappings
        assert "output_prefix" in mappings
        assert "ref_image_0" in mappings
        # Exact node IDs from M3.A
        assert mappings["prompt"]["node_id"] == "104"
        assert mappings["seed"]["node_id"] == "15"
        assert mappings["duration"]["node_id"] == "111"
        assert mappings["aspect"]["node_id"] == "115"
        assert mappings["output_prefix"]["node_id"] == "92"
        assert mappings["ref_image_0"]["node_id"] == "200"

    def test_r2v_required_nodes(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        for nid in ["104", "200", "111", "15", "115", "92"]:
            assert nid in defn.required_nodes

    def test_r2v_required_models(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        assert "minimax_h3_ref2va_pruned_int8_convrot.safetensors" in defn.required_models

    def test_r2v_constraints_max_refs(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        assert defn.constraints["max_reference_images"] == 9
        assert defn.constraints["materialized_reference_slots"] == 1


# ---------------------------------------------------------------------------
# WorkflowResolver — strategy resolution
# ---------------------------------------------------------------------------

class TestWorkflowResolverStrategy:
    def test_reference_to_video_resolves(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        assert isinstance(defn, WorkflowDefinition)

    @pytest.mark.parametrize("strategy", [
        "TEXT_TO_VIDEO",
        "IMAGE_TO_VIDEO",
        "FIRST_LAST_FRAME",
        "MULTI_PANEL",
    ])
    def test_unsupported_strategies_rejected(self, strategy):
        resolver = WorkflowResolver(project_root=WORKTREE)
        with pytest.raises(UnsupportedStrategyError, match=strategy):
            resolver.resolve(strategy)


# ---------------------------------------------------------------------------
# WorkflowResolver — template loading
# ---------------------------------------------------------------------------

class TestWorkflowResolverTemplate:
    def test_load_template_returns_valid_dict(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        template = resolver.load_template(defn)
        assert isinstance(template, dict)
        assert "104" in template
        assert "200" in template

    def test_load_template_contains_class_types(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        template = resolver.load_template(defn)
        assert template["104"]["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert template["200"]["class_type"] == "LoadImage"

    def test_fingerprint_mismatch_rejected(self, tmp_path):
        # Write a modified template
        modified = {"fake": {"inputs": {}, "class_type": "Fake"}}
        template_path = os.path.join(str(tmp_path), "bad_template.json")
        with open(template_path, "w") as f:
            json.dump(modified, f)
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        # Create a definition pointing to the bad template
        bad_defn = WorkflowDefinition(
            id=defn.id,
            version=defn.version,
            strategy=defn.strategy,
            template_path=template_path,
            template_fingerprint=EXPECTED_SHA,  # won't match
            parameter_mappings=defn.parameter_mappings,
            required_models=defn.required_models,
            required_nodes=defn.required_nodes,
            capabilities=defn.capabilities,
            constraints=defn.constraints,
        )
        with pytest.raises(WorkflowTemplateError, match="fingerprint"):
            resolver.load_template(bad_defn)

    def test_missing_template_raises(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        bad_defn = WorkflowDefinition(
            id=defn.id,
            version=defn.version,
            strategy=defn.strategy,
            template_path="/nonexistent/path.json",
            template_fingerprint=defn.template_fingerprint,
            parameter_mappings=defn.parameter_mappings,
            required_models=defn.required_models,
            required_nodes=defn.required_nodes,
            capabilities=defn.capabilities,
            constraints=defn.constraints,
        )
        with pytest.raises(WorkflowTemplateError, match="not found"):
            resolver.load_template(bad_defn)

    def test_compute_fingerprint(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        fp = resolver.compute_fingerprint(TEMPLATE_PATH)
        assert fp == EXPECTED_SHA

    def test_real_template_fingerprint_matches_m3a(self):
        """BLOCKING: the checked-in template must match the M3.A verified SHA."""
        with open(TEMPLATE_PATH, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        assert actual == EXPECTED_SHA

    def test_template_validates_required_nodes(self):
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        template = resolver.load_template(defn)
        for nid in defn.required_nodes:
            assert nid in template, f"Required node {nid} missing from template"

    def test_invalid_json_raises(self, tmp_path):
        bad_path = os.path.join(str(tmp_path), "bad.json")
        with open(bad_path, "w") as f:
            f.write("not json at all {{{")
        resolver = WorkflowResolver(project_root=WORKTREE)
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        # Compute actual fingerprint of the bad file for this test
        with open(bad_path, "rb") as f:
            bad_sha = hashlib.sha256(f.read()).hexdigest()
        bad_defn = WorkflowDefinition(
            id=defn.id,
            version=defn.version,
            strategy=defn.strategy,
            template_path=bad_path,
            template_fingerprint=bad_sha,
            parameter_mappings=defn.parameter_mappings,
            required_models=defn.required_models,
            required_nodes=defn.required_nodes,
            capabilities=defn.capabilities,
            constraints=defn.constraints,
        )
        with pytest.raises(WorkflowTemplateError, match="JSON"):
            resolver.load_template(bad_defn)
