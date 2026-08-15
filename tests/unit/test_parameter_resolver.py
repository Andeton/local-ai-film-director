"""Tests for ParameterResolver — seed, duration, aspect, injection building."""
import copy
import json
import os

import pytest

from film_director.errors import ParameterResolutionError, WorkflowTemplateError
from film_director.generation.h3_prompt import H3PromptV1
from film_director.generation.h3_types import H3ReferenceBinding, WorkflowInjection
from film_director.generation.parameter_resolver import ParameterResolver
from film_director.generation.workflow_registry import WorkflowResolver
from film_director.models.canonical import (
    CameraIntent,
    GenerationPlan,
    ReferenceRequirements,
    ShotSpecificationV1,
)

VALID_SHA = "a" * 64

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plan(**kw) -> GenerationPlan:
    defaults = dict(
        id="plan-1",
        shot_id="shot-1",
        shot_version=1,
        strategy="REFERENCE_TO_VIDEO",
        reference_requirements=ReferenceRequirements(character_refs=True),
        duration_sec=5.0,
        version=1,
    )
    defaults.update(kw)
    return GenerationPlan(**defaults)


def _shot(**kw) -> ShotSpecificationV1:
    defaults = dict(
        id="shot-1",
        beat_id="beat-1",
        dramatic_purpose="tension",
        action="walks forward",
        camera=CameraIntent(shot_size="medium"),
        order_index=0,
        version=1,
    )
    defaults.update(kw)
    return ShotSpecificationV1(**defaults)


def _prompt(**kw) -> H3PromptV1:
    defaults = dict(
        id="h3p-test",
        shot_id="shot-1",
        generation_plan_id="plan-1",
        source_shot_version=1,
        source_generation_plan_version=1,
        subject_definitions="<Subject 1> is Alice",
        summary="Alice walks",
        retention_analysis="<Subject 1> fully_preserved",
        detailed_description="Camera: medium. Action: walks.",
        rendered_prompt_text="test prompt text with <Subject 1> in <Picture 1>",
    )
    defaults.update(kw)
    return H3PromptV1(**defaults)


def _binding(**kw) -> H3ReferenceBinding:
    defaults = dict(
        subject_index=1,
        character_id="c1",
        character_name="Alice",
        appearance="dark hair",
        picture_index=1,
        local_path="/refs/alice.png",
        content_sha256=VALID_SHA,
        uploaded_filename="uploaded_alice.png",
    )
    defaults.update(kw)
    return H3ReferenceBinding(**defaults)


def _get_defn():
    resolver = WorkflowResolver(project_root=WORKTREE)
    return resolver.resolve("REFERENCE_TO_VIDEO")


def _get_template():
    resolver = WorkflowResolver(project_root=WORKTREE)
    defn = resolver.resolve("REFERENCE_TO_VIDEO")
    return resolver.load_template(defn)


# ---------------------------------------------------------------------------
# Seed resolution
# ---------------------------------------------------------------------------

class TestSeedResolution:
    def test_fixed_seed_returns_plan_seed(self):
        pr = ParameterResolver()
        plan = _plan(seed_policy="fixed", seed=12345)
        assert pr.resolve_seed(plan, take_number=1) == 12345

    def test_fixed_seed_missing_raises(self):
        pr = ParameterResolver()
        plan = _plan(seed_policy="fixed", seed=None)
        with pytest.raises(ParameterResolutionError, match="seed"):
            pr.resolve_seed(plan, take_number=1)

    def test_random_seed_returns_integer(self):
        pr = ParameterResolver()
        plan = _plan(seed_policy="random")
        seed = pr.resolve_seed(plan, take_number=1)
        assert isinstance(seed, int)
        assert seed >= 0

    def test_random_seed_is_nondeterministic(self):
        pr = ParameterResolver()
        plan = _plan(seed_policy="random")
        seeds = {pr.resolve_seed(plan, take_number=1) for _ in range(10)}
        assert len(seeds) > 1  # statistically near-certain

    def test_vary_per_take_returns_integer(self):
        pr = ParameterResolver()
        plan = _plan(seed_policy="vary_per_take")
        seed = pr.resolve_seed(plan, take_number=1)
        assert isinstance(seed, int)
        assert seed >= 0

    def test_seed_within_uint64_range(self):
        pr = ParameterResolver()
        plan = _plan(seed_policy="random")
        for _ in range(100):
            seed = pr.resolve_seed(plan, take_number=1)
            assert 0 <= seed <= 0xFFFFFFFFFFFFFFFF


# ---------------------------------------------------------------------------
# Duration resolution
# ---------------------------------------------------------------------------

class TestDurationResolution:
    def test_valid_duration_passes(self):
        pr = ParameterResolver()
        defn = _get_defn()
        assert pr.resolve_duration(5.0, defn) == 5.0

    def test_max_duration_passes(self):
        pr = ParameterResolver()
        defn = _get_defn()
        assert pr.resolve_duration(15.0, defn) == 15.0

    def test_too_short_raises(self):
        pr = ParameterResolver()
        defn = _get_defn()
        with pytest.raises(ParameterResolutionError, match="(?i)duration"):
            pr.resolve_duration(0.1, defn)

    def test_too_long_raises(self):
        pr = ParameterResolver()
        defn = _get_defn()
        with pytest.raises(ParameterResolutionError, match="(?i)duration"):
            pr.resolve_duration(20.0, defn)

    def test_duration_injected_as_seconds(self):
        """Duration injected as seconds to node 111, NOT frames."""
        pr = ParameterResolver()
        defn = _get_defn()
        val = pr.resolve_duration(7.5, defn)
        assert val == 7.5  # seconds, not frames


# ---------------------------------------------------------------------------
# Aspect resolution
# ---------------------------------------------------------------------------

class TestAspectResolution:
    def test_16_9_maps_correctly(self):
        pr = ParameterResolver()
        defn = _get_defn()
        assert pr.resolve_aspect("16:9", defn) == "16:9 (Widescreen)"

    def test_9_16_maps_correctly(self):
        pr = ParameterResolver()
        defn = _get_defn()
        assert pr.resolve_aspect("9:16", defn) == "9:16 (Portrait Widescreen)"

    def test_1_1_maps_correctly(self):
        pr = ParameterResolver()
        defn = _get_defn()
        assert pr.resolve_aspect("1:1", defn) == "1:1 (Square)"

    def test_4_3_maps_correctly(self):
        pr = ParameterResolver()
        defn = _get_defn()
        assert pr.resolve_aspect("4:3", defn) == "4:3 (Standard)"

    def test_unsupported_aspect_raises(self):
        pr = ParameterResolver()
        defn = _get_defn()
        with pytest.raises(ParameterResolutionError, match="aspect"):
            pr.resolve_aspect("5:4", defn)

    def test_all_supported_aspects(self):
        pr = ParameterResolver()
        defn = _get_defn()
        supported = ["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"]
        for aspect in supported:
            result = pr.resolve_aspect(aspect, defn)
            assert isinstance(result, str)
            assert aspect in result


# ---------------------------------------------------------------------------
# Reference injection
# ---------------------------------------------------------------------------

class TestReferenceInjection:
    def test_uploaded_filename_required(self):
        pr = ParameterResolver()
        defn = _get_defn()
        binding = _binding(uploaded_filename="")
        with pytest.raises(ParameterResolutionError, match="uploaded_filename"):
            pr.build_injections(
                plan=_plan(), shot=_shot(), prompt=_prompt(),
                workflow_def=defn, uploaded_bindings=[binding],
                seed=42, output_prefix="test/out",
            )

    def test_too_many_bindings_raises(self):
        """Template has 1 materialized slot; 2 bindings should fail."""
        pr = ParameterResolver()
        defn = _get_defn()
        bindings = [
            _binding(subject_index=1, picture_index=1, uploaded_filename="a.png"),
            _binding(subject_index=2, picture_index=2, character_id="c2",
                     character_name="Bob", uploaded_filename="b.png"),
        ]
        with pytest.raises(ParameterResolutionError, match="reference"):
            pr.build_injections(
                plan=_plan(), shot=_shot(), prompt=_prompt(),
                workflow_def=defn, uploaded_bindings=bindings,
                seed=42, output_prefix="test/out",
            )


# ---------------------------------------------------------------------------
# Build injections
# ---------------------------------------------------------------------------

class TestBuildInjections:
    def test_returns_workflow_injection_list(self):
        pr = ParameterResolver()
        defn = _get_defn()
        injections = pr.build_injections(
            plan=_plan(), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        assert isinstance(injections, list)
        assert all(isinstance(inj, WorkflowInjection) for inj in injections)

    def test_contains_prompt_injection(self):
        pr = ParameterResolver()
        defn = _get_defn()
        injections = pr.build_injections(
            plan=_plan(), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        prompt_injs = [i for i in injections if i.name == "prompt"]
        assert len(prompt_injs) == 1
        assert prompt_injs[0].node_id == "104"
        assert prompt_injs[0].field == "prompt"
        assert prompt_injs[0].value == _prompt().rendered_prompt_text

    def test_contains_seed_injection(self):
        pr = ParameterResolver()
        defn = _get_defn()
        injections = pr.build_injections(
            plan=_plan(), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        seed_injs = [i for i in injections if i.name == "seed"]
        assert len(seed_injs) == 1
        assert seed_injs[0].node_id == "15"
        assert seed_injs[0].field == "noise_seed"
        assert seed_injs[0].value == 42

    def test_contains_duration_injection_in_seconds(self):
        pr = ParameterResolver()
        defn = _get_defn()
        injections = pr.build_injections(
            plan=_plan(duration_sec=7.0), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        dur_injs = [i for i in injections if i.name == "duration"]
        assert len(dur_injs) == 1
        assert dur_injs[0].node_id == "111"
        assert dur_injs[0].field == "value"
        assert dur_injs[0].value == 7.0  # seconds, NOT frames

    def test_contains_aspect_injection(self):
        pr = ParameterResolver()
        defn = _get_defn()
        injections = pr.build_injections(
            plan=_plan(resolution_intent={"aspect": "16:9"}),
            shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        asp_injs = [i for i in injections if i.name == "aspect"]
        assert len(asp_injs) == 1
        assert asp_injs[0].node_id == "115"
        assert asp_injs[0].field == "aspect_ratio"
        assert asp_injs[0].value == "16:9 (Widescreen)"

    def test_contains_output_prefix_injection(self):
        pr = ParameterResolver()
        defn = _get_defn()
        injections = pr.build_injections(
            plan=_plan(), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="video/shot_001",
        )
        prefix_injs = [i for i in injections if i.name == "output_prefix"]
        assert len(prefix_injs) == 1
        assert prefix_injs[0].node_id == "92"
        assert prefix_injs[0].field == "filename_prefix"
        assert prefix_injs[0].value == "video/shot_001"

    def test_contains_reference_injection(self):
        pr = ParameterResolver()
        defn = _get_defn()
        injections = pr.build_injections(
            plan=_plan(), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding(uploaded_filename="my_ref.png")],
            seed=42, output_prefix="test/out",
        )
        ref_injs = [i for i in injections if i.name == "ref_image_0"]
        assert len(ref_injs) == 1
        assert ref_injs[0].node_id == "200"
        assert ref_injs[0].field == "image"
        assert ref_injs[0].value == "my_ref.png"

    def test_empty_output_prefix_raises(self):
        pr = ParameterResolver()
        defn = _get_defn()
        with pytest.raises(ParameterResolutionError, match="output_prefix"):
            pr.build_injections(
                plan=_plan(), shot=_shot(), prompt=_prompt(),
                workflow_def=defn, uploaded_bindings=[_binding()],
                seed=42, output_prefix="",
            )

    def test_deterministic_same_input_same_output(self):
        pr = ParameterResolver()
        defn = _get_defn()
        kwargs = dict(
            plan=_plan(), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        inj1 = pr.build_injections(**kwargs)
        inj2 = pr.build_injections(**kwargs)
        assert len(inj1) == len(inj2)
        for a, b in zip(inj1, inj2):
            assert a.name == b.name
            assert a.node_id == b.node_id
            assert a.field == b.field
            assert a.value == b.value


# ---------------------------------------------------------------------------
# Apply injections
# ---------------------------------------------------------------------------

class TestApplyInjections:
    def test_applies_values_to_template(self):
        pr = ParameterResolver()
        defn = _get_defn()
        template = _get_template()
        injections = pr.build_injections(
            plan=_plan(), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        result = pr.apply_injections(template, injections)
        assert result["15"]["inputs"]["noise_seed"] == 42
        assert result["111"]["inputs"]["value"] == 5.0
        assert result["104"]["inputs"]["prompt"] == _prompt().rendered_prompt_text
        assert result["200"]["inputs"]["image"] == "uploaded_alice.png"
        assert result["92"]["inputs"]["filename_prefix"] == "test/out"

    def test_deep_copy_no_mutation(self):
        pr = ParameterResolver()
        defn = _get_defn()
        template = _get_template()
        original_json = json.dumps(template, sort_keys=True)
        injections = pr.build_injections(
            plan=_plan(), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        pr.apply_injections(template, injections)
        after_json = json.dumps(template, sort_keys=True)
        assert original_json == after_json

    def test_missing_node_raises(self):
        pr = ParameterResolver()
        template = {"other": {"inputs": {}}}
        injections = [WorkflowInjection(name="seed", node_id="15", field="noise_seed", value=42)]
        with pytest.raises(WorkflowTemplateError, match="node"):
            pr.apply_injections(template, injections)

    def test_missing_inputs_raises(self):
        pr = ParameterResolver()
        template = {"15": {"no_inputs": {}}}
        injections = [WorkflowInjection(name="seed", node_id="15", field="noise_seed", value=42)]
        with pytest.raises(WorkflowTemplateError, match="inputs"):
            pr.apply_injections(template, injections)

    def test_class_types_preserved(self):
        pr = ParameterResolver()
        defn = _get_defn()
        template = _get_template()
        injections = pr.build_injections(
            plan=_plan(), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        result = pr.apply_injections(template, injections)
        assert result["104"]["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert result["200"]["class_type"] == "LoadImage"
        assert result["15"]["class_type"] == "RandomNoise"

    def test_graph_connections_preserved(self):
        pr = ParameterResolver()
        defn = _get_defn()
        template = _get_template()
        injections = pr.build_injections(
            plan=_plan(), shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        result = pr.apply_injections(template, injections)
        # R2V ref_images wire preserved
        assert result["104"]["inputs"]["ref_images.ref_image_0"] == ["200", 0]
        # Seed→sampler wire preserved
        assert result["14"]["inputs"]["noise"] == ["15", 0]


# ---------------------------------------------------------------------------
# BLOCKING: exact snapshot test
# ---------------------------------------------------------------------------

class TestExactSnapshotBlocking:
    """Every WorkflowInjection value must match the resulting workflow JSON."""

    def test_snapshot_matches_applied_workflow(self):
        pr = ParameterResolver()
        defn = _get_defn()
        template = _get_template()

        injections = pr.build_injections(
            plan=_plan(
                duration_sec=5.0,
                resolution_intent={"aspect": "16:9"},
                seed_policy="fixed",
                seed=42,
            ),
            shot=_shot(),
            prompt=_prompt(rendered_prompt_text="The detective walks in."),
            workflow_def=defn,
            uploaded_bindings=[_binding(uploaded_filename="ref_det.png")],
            seed=42,
            output_prefix="preflight/test_001",
        )
        result = pr.apply_injections(template, injections)

        for inj in injections:
            node = result[inj.node_id]
            assert node["inputs"][inj.field] == inj.value, (
                f"Injection {inj.name}: expected {inj.value!r}, "
                f"got {node['inputs'][inj.field]!r}"
            )


# ---------------------------------------------------------------------------
# Default aspect when resolution_intent missing
# ---------------------------------------------------------------------------

class TestDefaultAspect:
    def test_missing_aspect_uses_16_9_default(self):
        pr = ParameterResolver()
        defn = _get_defn()
        injections = pr.build_injections(
            plan=_plan(resolution_intent={}),
            shot=_shot(), prompt=_prompt(),
            workflow_def=defn, uploaded_bindings=[_binding()],
            seed=42, output_prefix="test/out",
        )
        asp = [i for i in injections if i.name == "aspect"][0]
        assert asp.value == "16:9 (Widescreen)"
