"""Tests for image-pack slot pruning — unused ref slots removed before submit.

Regression test for HTTP 400 caused by placeholder LoadImage nodes
(continuity.png, prop.png) in unused image-pack slots.
"""
from __future__ import annotations

import copy
import json
import os

import pytest

from film_director.generation.h3_types import H3ReferenceBinding, WorkflowInjection
from film_director.generation.parameter_resolver import ParameterResolver
from film_director.generation.workflow_registry import WorkflowDefinition, WorkflowResolver


WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def resolver():
    return ParameterResolver()


@pytest.fixture
def image_pack_def():
    wr = WorkflowResolver(project_root=WORKTREE)
    return wr.resolve_image_pack()


@pytest.fixture
def image_pack_template(image_pack_def):
    wr = WorkflowResolver(project_root=WORKTREE)
    return wr.load_template(image_pack_def)


class TestPruneUnusedSlots:
    """Unused ref_image slots must be removed from submitted workflow."""

    def test_2_refs_prunes_slots_2_and_3(self, resolver, image_pack_def, image_pack_template):
        """Shot 1: 2 refs (char + env) → nodes 202, 203 removed."""
        injections = [
            WorkflowInjection(name="prompt", node_id="104", field="prompt", value="test"),
            WorkflowInjection(name="ref_image_0", node_id="200", field="image", value="char.png"),
            WorkflowInjection(name="ref_image_1", node_id="201", field="image", value="env.png"),
            WorkflowInjection(name="duration", node_id="111", field="value", value=5.0),
            WorkflowInjection(name="seed", node_id="15", field="noise_seed", value=42),
            WorkflowInjection(name="aspect", node_id="115", field="aspect_ratio", value="16:9 (Widescreen)"),
            WorkflowInjection(name="output_prefix", node_id="92", field="filename_prefix", value="test"),
        ]
        result = resolver.apply_injections(image_pack_template, injections, image_pack_def)

        # Nodes 200, 201 kept
        assert "200" in result
        assert "201" in result
        assert result["200"]["inputs"]["image"] == "char.png"
        assert result["201"]["inputs"]["image"] == "env.png"

        # Nodes 202, 203 REMOVED
        assert "202" not in result
        assert "203" not in result

        # H3 node connections pruned
        h3_inputs = result["104"]["inputs"]
        assert "ref_images.ref_image_0" in h3_inputs
        assert "ref_images.ref_image_1" in h3_inputs
        assert "ref_images.ref_image_2" not in h3_inputs
        assert "ref_images.ref_image_3" not in h3_inputs

    def test_3_refs_prunes_slot_3(self, resolver, image_pack_def, image_pack_template):
        """Downstream: 3 refs (char + env + continuity) → node 203 removed."""
        injections = [
            WorkflowInjection(name="prompt", node_id="104", field="prompt", value="test"),
            WorkflowInjection(name="ref_image_0", node_id="200", field="image", value="char.png"),
            WorkflowInjection(name="ref_image_1", node_id="201", field="image", value="env.png"),
            WorkflowInjection(name="ref_image_2", node_id="202", field="image", value="cont.png"),
            WorkflowInjection(name="duration", node_id="111", field="value", value=5.0),
            WorkflowInjection(name="seed", node_id="15", field="noise_seed", value=42),
            WorkflowInjection(name="aspect", node_id="115", field="aspect_ratio", value="16:9 (Widescreen)"),
            WorkflowInjection(name="output_prefix", node_id="92", field="filename_prefix", value="test"),
        ]
        result = resolver.apply_injections(image_pack_template, injections, image_pack_def)

        assert "200" in result
        assert "201" in result
        assert "202" in result
        assert "203" not in result  # Unused slot 3 removed

        h3_inputs = result["104"]["inputs"]
        assert "ref_images.ref_image_2" in h3_inputs
        assert "ref_images.ref_image_3" not in h3_inputs

    def test_4_refs_keeps_all(self, resolver, image_pack_def, image_pack_template):
        """All 4 slots used → nothing pruned."""
        injections = [
            WorkflowInjection(name="prompt", node_id="104", field="prompt", value="test"),
            WorkflowInjection(name="ref_image_0", node_id="200", field="image", value="c1.png"),
            WorkflowInjection(name="ref_image_1", node_id="201", field="image", value="env.png"),
            WorkflowInjection(name="ref_image_2", node_id="202", field="image", value="cont.png"),
            WorkflowInjection(name="ref_image_3", node_id="203", field="image", value="c2.png"),
            WorkflowInjection(name="duration", node_id="111", field="value", value=5.0),
            WorkflowInjection(name="seed", node_id="15", field="noise_seed", value=42),
            WorkflowInjection(name="aspect", node_id="115", field="aspect_ratio", value="16:9 (Widescreen)"),
            WorkflowInjection(name="output_prefix", node_id="92", field="filename_prefix", value="test"),
        ]
        result = resolver.apply_injections(image_pack_template, injections, image_pack_def)

        assert "200" in result
        assert "201" in result
        assert "202" in result
        assert "203" in result

    def test_1_ref_prunes_slots_1_2_3(self, resolver, image_pack_def, image_pack_template):
        """Extreme case: 1 ref only."""
        injections = [
            WorkflowInjection(name="prompt", node_id="104", field="prompt", value="test"),
            WorkflowInjection(name="ref_image_0", node_id="200", field="image", value="char.png"),
            WorkflowInjection(name="duration", node_id="111", field="value", value=5.0),
            WorkflowInjection(name="seed", node_id="15", field="noise_seed", value=42),
            WorkflowInjection(name="aspect", node_id="115", field="aspect_ratio", value="16:9 (Widescreen)"),
            WorkflowInjection(name="output_prefix", node_id="92", field="filename_prefix", value="test"),
        ]
        result = resolver.apply_injections(image_pack_template, injections, image_pack_def)

        assert "200" in result
        assert "201" not in result
        assert "202" not in result
        assert "203" not in result

        h3_inputs = result["104"]["inputs"]
        assert "ref_images.ref_image_0" in h3_inputs
        assert "ref_images.ref_image_1" not in h3_inputs

    def test_no_placeholder_filenames(self, resolver, image_pack_def, image_pack_template):
        """Pruned workflow must not contain continuity.png or prop.png."""
        injections = [
            WorkflowInjection(name="prompt", node_id="104", field="prompt", value="test"),
            WorkflowInjection(name="ref_image_0", node_id="200", field="image", value="real_char.png"),
            WorkflowInjection(name="ref_image_1", node_id="201", field="image", value="real_env.png"),
            WorkflowInjection(name="duration", node_id="111", field="value", value=5.0),
            WorkflowInjection(name="seed", node_id="15", field="noise_seed", value=42),
            WorkflowInjection(name="aspect", node_id="115", field="aspect_ratio", value="16:9 (Widescreen)"),
            WorkflowInjection(name="output_prefix", node_id="92", field="filename_prefix", value="test"),
        ]
        result = resolver.apply_injections(image_pack_template, injections, image_pack_def)

        serialized = json.dumps(result)
        assert "continuity.png" not in serialized
        assert "prop.png" not in serialized
        assert "character.png" not in serialized
        assert "environment.png" not in serialized


class TestLegacyWorkflowUnaffected:
    """Non-image-pack workflows must not be pruned."""

    def test_r2v_v1_no_pruning(self, resolver):
        wr = WorkflowResolver(project_root=WORKTREE)
        v1 = wr._v1
        tpl = wr.load_template(v1)

        injections = [
            WorkflowInjection(name="prompt", node_id="104", field="prompt", value="test"),
            WorkflowInjection(name="ref_image_0", node_id="200", field="image", value="char.png"),
            WorkflowInjection(name="duration", node_id="111", field="value", value=5.0),
            WorkflowInjection(name="seed", node_id="15", field="noise_seed", value=42),
            WorkflowInjection(name="aspect", node_id="115", field="aspect_ratio", value="16:9 (Widescreen)"),
            WorkflowInjection(name="output_prefix", node_id="92", field="filename_prefix", value="test"),
        ]
        result = resolver.apply_injections(tpl, injections, v1)

        # v1 only has 1 slot — node 200 stays
        assert "200" in result


class TestSourceReferenceHashes:
    """Source reference workflow copies must remain byte-identical."""

    def test_r2v_source_hash(self):
        import hashlib
        path = os.path.join(WORKTREE, "workflows", "source_reference", "minimax_h3", "video_minimax_h3_r2v.json")
        with open(path, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        assert h == "b6224a53c92f819c33cf1e96df95bb9946c0b792c4f01f2b5037ea18fb8f9d9e"

    def test_i2v_source_hash(self):
        import hashlib
        path = os.path.join(WORKTREE, "workflows", "source_reference", "minimax_h3", "video_minimax_h3_i2v.json")
        with open(path, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        assert h == "b9f11d8249edb2cee0b4e2e270ac8a58ccde282eaa41d465bda07ac8f7386305"
