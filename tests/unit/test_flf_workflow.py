"""Unit tests for M7.B — FLF workflow definition, binding, and injection."""
import os
import pytest

from film_director.continuity.continuity_binding import ContinuityBinding
from film_director.continuity.continuity_models import compute_continuity_fingerprint
from film_director.generation.parameter_resolver import ParameterResolver
from film_director.generation.workflow_registry import WorkflowResolver

_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)


# ---------------------------------------------------------------------------
# FLF Workflow Definition
# ---------------------------------------------------------------------------

class TestFLFWorkflowDefinition:
    def test_flf_v1_loads(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        defn = resolver._flf_v1
        assert defn.id == "h3_flf_v1"
        assert defn.version == "1.0.0"
        assert defn.strategy == "FIRST_LAST_FRAME"

    def test_flf_v1_fingerprint_matches(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        template = resolver.load_template(resolver._flf_v1)
        assert template is not None  # loaded successfully = fingerprint verified

    def test_flf_v1_required_models(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        defn = resolver._flf_v1
        assert "minimax_h3_fl2va_pruned_int8_convrot.safetensors" in defn.required_models
        assert "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" in defn.required_models
        assert "minimax_h3_video_vae_fp16.safetensors" in defn.required_models
        assert "minimax_h3_audio_vae_fp32.safetensors" in defn.required_models

    def test_flf_v1_first_frame_node(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        template = resolver.load_template(resolver._flf_v1)
        # Node 300 is LoadImage for first frame
        assert "300" in template
        assert template["300"]["class_type"] == "LoadImage"
        # Node 104 has first_frame input connected to node 300
        node_104 = template["104"]
        assert node_104["class_type"] == "MiniMaxH3ImageToVideo"
        assert node_104["inputs"]["first_frame"] == ["300", 0]

    def test_flf_v1_last_frame_unbound(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        template = resolver.load_template(resolver._flf_v1)
        # MiniMaxH3ImageToVideo should not have last_frame connected
        node_104 = template["104"]
        assert "last_frame" not in node_104["inputs"]

    def test_flf_v1_no_ref_images(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        template = resolver.load_template(resolver._flf_v1)
        node_104 = template["104"]
        # No ref_images keys in any form
        for key in node_104["inputs"]:
            assert not key.startswith("ref_image"), f"Unexpected ref_image key: {key}"
        # No node 200/201 (R2V reference image nodes)
        assert "200" not in template
        assert "201" not in template

    def test_flf_v1_parameter_mappings(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        defn = resolver._flf_v1
        assert "prompt" in defn.parameter_mappings
        assert "first_frame" in defn.parameter_mappings
        assert "duration" in defn.parameter_mappings
        assert "seed" in defn.parameter_mappings
        assert "aspect" in defn.parameter_mappings
        assert "output_prefix" in defn.parameter_mappings
        # No ref_image mappings
        assert "ref_image_0" not in defn.parameter_mappings
        assert "ref_image_1" not in defn.parameter_mappings

    def test_flf_v1_constraints(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        defn = resolver._flf_v1
        assert defn.constraints["materialized_reference_slots"] == 0
        assert defn.constraints["continuity_frame_slots"] == 1
        assert defn.constraints["fps"] == 24

    def test_no_experimental_paths_in_template(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        template = resolver.load_template(resolver._flf_v1)
        import json
        raw = json.dumps(template)
        assert "m7b_first_frame" not in raw
        assert "preflight" not in raw.lower()
        assert "PLACEHOLDER" not in raw


# ---------------------------------------------------------------------------
# R2V backward compatibility
# ---------------------------------------------------------------------------

class TestR2VBackwardCompatibility:
    def test_r2v_v1_fingerprint_unchanged(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        assert resolver._v1.template_fingerprint == (
            "3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4"
        )

    def test_r2v_v2_fingerprint_unchanged(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        assert resolver._v2.template_fingerprint == (
            "b4930400f0433fdd09f3bd4f8a20d55394050c7d4558eddd6f2e7046e110f3b9"
        )

    def test_one_ref_still_selects_v1(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        defn = resolver.resolve_for_reference_count(1)
        assert defn.id == "h3_r2v_v1"

    def test_two_refs_still_selects_v2(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        defn = resolver.resolve_for_reference_count(2)
        assert defn.id == "h3_r2v_v2"

    def test_r2v_v1_loads_with_fingerprint(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        template = resolver.load_template(resolver._v1)
        assert template is not None

    def test_r2v_v2_loads_with_fingerprint(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        template = resolver.load_template(resolver._v2)
        assert template is not None


# ---------------------------------------------------------------------------
# Continuity selection helper
# ---------------------------------------------------------------------------

class TestContinuitySelection:
    def test_continuity_frame_selects_flf(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        defn = resolver.resolve_for_continuity(has_continuity_frame=True, reference_count=0)
        assert defn.id == "h3_flf_v1"

    def test_no_continuity_one_ref_selects_r2v_v1(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        defn = resolver.resolve_for_continuity(has_continuity_frame=False, reference_count=1)
        assert defn.id == "h3_r2v_v1"

    def test_no_continuity_two_refs_selects_r2v_v2(self):
        resolver = WorkflowResolver(_PROJECT_ROOT)
        defn = resolver.resolve_for_continuity(has_continuity_frame=False, reference_count=2)
        assert defn.id == "h3_r2v_v2"


# ---------------------------------------------------------------------------
# ContinuityBinding model
# ---------------------------------------------------------------------------

_FP = "a" * 64
_SHA = "b" * 64


class TestContinuityBinding:
    def test_valid_construction(self):
        binding = ContinuityBinding(
            continuity_state_id="cs_1",
            upstream_shot_id="shot_1",
            upstream_take_id="take_1",
            upstream_take_number=2,
            local_path="/storage/takes/proj/shot/take_2/last_frame.png",
            content_sha256=_SHA,
            continuity_revision=3,
            continuity_fingerprint=_FP,
        )
        assert binding.upstream_take_number == 2
        assert binding.uploaded_filename == ""

    def test_with_uploaded_filename(self):
        binding = ContinuityBinding(
            continuity_state_id="cs_1",
            upstream_shot_id="shot_1",
            upstream_take_id="take_1",
            upstream_take_number=1,
            local_path="/p",
            content_sha256=_SHA,
            continuity_revision=0,
            continuity_fingerprint=_FP,
            uploaded_filename="frame_abc.png",
        )
        assert binding.uploaded_filename == "frame_abc.png"

    def test_frozen(self):
        binding = ContinuityBinding(
            continuity_state_id="cs_1",
            upstream_shot_id="s",
            upstream_take_id="t",
            upstream_take_number=1,
            local_path="/p",
            content_sha256=_SHA,
            continuity_revision=0,
            continuity_fingerprint=_FP,
        )
        with pytest.raises(AttributeError):
            binding.upstream_shot_id = "changed"  # type: ignore

    def test_empty_state_id_rejected(self):
        with pytest.raises(ValueError):
            ContinuityBinding(
                continuity_state_id="", upstream_shot_id="s",
                upstream_take_id="t", upstream_take_number=1,
                local_path="/p", content_sha256=_SHA,
                continuity_revision=0, continuity_fingerprint=_FP,
            )

    def test_empty_shot_id_rejected(self):
        with pytest.raises(ValueError):
            ContinuityBinding(
                continuity_state_id="cs", upstream_shot_id="",
                upstream_take_id="t", upstream_take_number=1,
                local_path="/p", content_sha256=_SHA,
                continuity_revision=0, continuity_fingerprint=_FP,
            )

    def test_zero_take_number_rejected(self):
        with pytest.raises(ValueError):
            ContinuityBinding(
                continuity_state_id="cs", upstream_shot_id="s",
                upstream_take_id="t", upstream_take_number=0,
                local_path="/p", content_sha256=_SHA,
                continuity_revision=0, continuity_fingerprint=_FP,
            )

    def test_invalid_sha256_rejected(self):
        with pytest.raises(ValueError):
            ContinuityBinding(
                continuity_state_id="cs", upstream_shot_id="s",
                upstream_take_id="t", upstream_take_number=1,
                local_path="/p", content_sha256="not_hex",
                continuity_revision=0, continuity_fingerprint=_FP,
            )

    def test_invalid_fingerprint_rejected(self):
        with pytest.raises(ValueError):
            ContinuityBinding(
                continuity_state_id="cs", upstream_shot_id="s",
                upstream_take_id="t", upstream_take_number=1,
                local_path="/p", content_sha256=_SHA,
                continuity_revision=0, continuity_fingerprint="short",
            )

    def test_empty_local_path_rejected(self):
        with pytest.raises(ValueError):
            ContinuityBinding(
                continuity_state_id="cs", upstream_shot_id="s",
                upstream_take_id="t", upstream_take_number=1,
                local_path="  ", content_sha256=_SHA,
                continuity_revision=0, continuity_fingerprint=_FP,
            )

    def test_negative_revision_rejected(self):
        with pytest.raises(ValueError):
            ContinuityBinding(
                continuity_state_id="cs", upstream_shot_id="s",
                upstream_take_id="t", upstream_take_number=1,
                local_path="/p", content_sha256=_SHA,
                continuity_revision=-1, continuity_fingerprint=_FP,
            )


# ---------------------------------------------------------------------------
# Parameter injection for FLF
# ---------------------------------------------------------------------------

class TestFLFParameterInjection:
    """build_continuity_injections produces correct injections."""

    @pytest.fixture
    def resolver(self):
        return ParameterResolver()

    @pytest.fixture
    def flf_def(self):
        return WorkflowResolver(_PROJECT_ROOT)._flf_v1

    def _make_plan(self):
        from film_director.models.canonical import GenerationPlan, ReferenceRequirements
        return GenerationPlan(
            id="plan_1", shot_id="shot_1", shot_version=1,
            strategy="FIRST_LAST_FRAME",
            reference_requirements=ReferenceRequirements(prev_frame=True),
            duration_sec=5.0, version=1,
            resolution_intent={"aspect": "16:9"},
            created_at="t", updated_at="t",
        )

    def _make_binding(self, uploaded="frame.png"):
        fp = compute_continuity_fingerprint("s", "t", 1, _SHA, 0)
        return ContinuityBinding(
            continuity_state_id="cs_1",
            upstream_shot_id="s",
            upstream_take_id="t",
            upstream_take_number=1,
            local_path="/p",
            content_sha256=_SHA,
            continuity_revision=0,
            continuity_fingerprint=fp,
            uploaded_filename=uploaded,
        )

    def test_correct_injections(self, resolver, flf_def):
        plan = self._make_plan()
        binding = self._make_binding()
        injections = resolver.build_continuity_injections(
            plan=plan, prompt_text="test prompt",
            workflow_def=flf_def, continuity_binding=binding,
            seed=42, output_prefix="test/out",
        )
        names = [i.name for i in injections]
        assert names == ["prompt", "first_frame", "duration", "seed", "aspect", "output_prefix"]

    def test_first_frame_reaches_correct_node(self, resolver, flf_def):
        plan = self._make_plan()
        binding = self._make_binding(uploaded="uploaded_frame.png")
        injections = resolver.build_continuity_injections(
            plan=plan, prompt_text="prompt",
            workflow_def=flf_def, continuity_binding=binding,
            seed=42, output_prefix="pfx",
        )
        ff = [i for i in injections if i.name == "first_frame"][0]
        assert ff.node_id == "300"
        assert ff.field == "image"
        assert ff.value == "uploaded_frame.png"

    def test_no_ref_images_in_injections(self, resolver, flf_def):
        plan = self._make_plan()
        binding = self._make_binding()
        injections = resolver.build_continuity_injections(
            plan=plan, prompt_text="prompt",
            workflow_def=flf_def, continuity_binding=binding,
            seed=42, output_prefix="pfx",
        )
        for inj in injections:
            assert not inj.name.startswith("ref_image")

    def test_missing_uploaded_filename_fails(self, resolver, flf_def):
        plan = self._make_plan()
        binding = self._make_binding(uploaded="")
        with pytest.raises(Exception):
            resolver.build_continuity_injections(
                plan=plan, prompt_text="prompt",
                workflow_def=flf_def, continuity_binding=binding,
                seed=42, output_prefix="pfx",
            )

    def test_template_injection_produces_valid_workflow(self, resolver, flf_def):
        wr = WorkflowResolver(_PROJECT_ROOT)
        template = wr.load_template(flf_def)
        plan = self._make_plan()
        binding = self._make_binding()
        injections = resolver.build_continuity_injections(
            plan=plan, prompt_text="injected prompt",
            workflow_def=flf_def, continuity_binding=binding,
            seed=999, output_prefix="test/prefix",
        )
        result = resolver.apply_injections(template, injections)
        assert result["300"]["inputs"]["image"] == "frame.png"
        assert result["104"]["inputs"]["prompt"] == "injected prompt"
        assert result["15"]["inputs"]["noise_seed"] == 999
        assert result["92"]["inputs"]["filename_prefix"] == "test/prefix"
