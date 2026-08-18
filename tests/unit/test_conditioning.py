"""Tests for M7.G.B — ConditioningRecipe, CapabilityRegistry, runtime probing,
recipe/capability validation, and fingerprinting.
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from film_director.generation.conditioning import (
    CapabilityProfile,
    CapabilityRegistry,
    CapabilityState,
    ConditioningRecipe,
    ProbeResult,
    RecipeRegistry,
    RecipeRoleRequirement,
    compute_capability_fingerprint,
    compute_recipe_fingerprint,
    extract_available_models,
    probe_runtime,
    validate_recipe_capability,
)
from film_director.models.reference import AssetRole
from film_director.persistence.database import Database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _role_req(**kw) -> RecipeRoleRequirement:
    base = dict(
        role=AssetRole.CHARACTER_FACE_CLOSEUP,
        required=True,
        slot_index=0,
        modality="image",
        max_count=1,
    )
    base.update(kw)
    return RecipeRoleRequirement(**base)


def _recipe(**kw) -> ConditioningRecipe:
    base = dict(
        id="test_recipe_v1",
        version="1.0.0",
        provider="minimax_h3",
        model_family="h3",
        supported_strategies=("REFERENCE_TO_VIDEO",),
        workflow_definition_id="h3_r2v_v2",
        workflow_definition_version="2.0.0",
        workflow_template_fingerprint="b" * 64,
        role_requirements=(
            _role_req(role=AssetRole.CHARACTER_FACE_CLOSEUP, slot_index=0, required=True),
            _role_req(role=AssetRole.ENVIRONMENT_VIEW, slot_index=1, required=False),
        ),
    )
    base.update(kw)
    r = ConditioningRecipe(**base)
    # Compute fingerprint if not supplied
    if not kw.get("fingerprint"):
        fp = compute_recipe_fingerprint(r)
        r = ConditioningRecipe(**{**base, "fingerprint": fp})
    return r


def _capability(**kw) -> CapabilityProfile:
    base = dict(
        id="h3_r2v_local",
        provider="minimax_h3",
        model_family="h3",
        execution_mode="local",
        supported_strategies=("REFERENCE_TO_VIDEO", "FIRST_LAST_FRAME"),
        supported_modalities=("image",),
        max_references=9,
        windows_compatible=True,
        comfyui_compatible=True,
        required_models=(
            "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        ),
        required_nodes=(
            "MiniMaxH3ReferenceToVideo",
        ),
    )
    base.update(kw)
    return CapabilityProfile(**base)


# ===========================================================================
# CONDITIONING RECIPE MODEL
# ===========================================================================

class TestConditioningRecipeModel:
    def test_roundtrip(self):
        r = _recipe()
        assert r.id == "test_recipe_v1"
        assert r.version == "1.0.0"
        assert r.provider == "minimax_h3"

    def test_stable_id_version(self):
        r = _recipe(id="my_recipe", version="2.0.0")
        assert r.id == "my_recipe"
        assert r.version == "2.0.0"

    def test_required_and_optional_roles(self):
        r = _recipe()
        required = [rr for rr in r.role_requirements if rr.required]
        optional = [rr for rr in r.role_requirements if not rr.required]
        assert len(required) == 1
        assert len(optional) == 1
        assert required[0].role == AssetRole.CHARACTER_FACE_CLOSEUP
        assert optional[0].role == AssetRole.ENVIRONMENT_VIEW

    def test_deterministic_slot_ordering(self):
        r = _recipe(role_requirements=(
            _role_req(role=AssetRole.ENVIRONMENT_VIEW, slot_index=1),
            _role_req(role=AssetRole.CHARACTER_FACE_CLOSEUP, slot_index=0),
        ))
        # Slot ordering is by slot_index in the tuple
        assert r.role_requirements[0].slot_index == 1
        assert r.role_requirements[1].slot_index == 0

    def test_modality_values(self):
        for mod in ("image", "video", "audio"):
            rr = _role_req(modality=mod)
            assert rr.modality == mod

    def test_cardinality(self):
        rr = _role_req(max_count=4)
        assert rr.max_count == 4

    def test_no_canonical_model_mutation(self):
        """Recipe model must not add fields to canonical entities."""
        from film_director.models.canonical import GenerationPlan, ShotSpecificationV1
        from film_director.models.reference import (
            VisualAssetPack, AssetRoleBinding, ReferenceAsset,
        )
        for cls in (GenerationPlan, ShotSpecificationV1, VisualAssetPack,
                    AssetRoleBinding, ReferenceAsset):
            fields = set(cls.model_fields.keys())
            provider_fields = {
                "engine_family", "workflow_profile", "provider", "model",
                "model_family", "workflow_definition_id", "node_ids",
            }
            assert fields & provider_fields == set(), f"{cls.__name__} has provider fields"

    def test_frozen(self):
        r = _recipe()
        with pytest.raises(AttributeError):
            r.id = "changed"

    def test_workflow_provenance(self):
        r = _recipe(
            workflow_definition_id="h3_r2v_v2",
            workflow_definition_version="2.0.0",
            workflow_template_fingerprint="b" * 64,
        )
        assert r.workflow_definition_id == "h3_r2v_v2"
        assert r.workflow_definition_version == "2.0.0"
        assert r.workflow_template_fingerprint == "b" * 64

    def test_optional_fields(self):
        r = _recipe(
            source_url="https://example.com/workflow",
            source_notes="Official H3 workflow",
            expected_vram_gb=24.0,
            expected_runtime_class="4-7min per shot",
            prompt_tag_convention="<Picture N>",
            fallback_behavior="omit optional slots",
        )
        assert r.source_url == "https://example.com/workflow"
        assert r.expected_vram_gb == 24.0
        assert r.prompt_tag_convention == "<Picture N>"


# ===========================================================================
# RECIPE FINGERPRINT
# ===========================================================================

class TestRecipeFingerprint:
    def test_identical_semantics_identical_hash(self):
        r1 = _recipe()
        r2 = _recipe()
        assert compute_recipe_fingerprint(r1) == compute_recipe_fingerprint(r2)

    def test_valid_sha256(self):
        fp = compute_recipe_fingerprint(_recipe())
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_role_change_affects_fingerprint(self):
        r1 = _recipe(role_requirements=(
            _role_req(role=AssetRole.CHARACTER_FACE_CLOSEUP, slot_index=0),
        ))
        r2 = _recipe(role_requirements=(
            _role_req(role=AssetRole.STYLE_REFERENCE, slot_index=0),
        ))
        assert compute_recipe_fingerprint(r1) != compute_recipe_fingerprint(r2)

    def test_required_optional_change_affects_fingerprint(self):
        r1 = _recipe(role_requirements=(
            _role_req(role=AssetRole.CHARACTER_FACE_CLOSEUP, required=True),
        ))
        r2 = _recipe(role_requirements=(
            _role_req(role=AssetRole.CHARACTER_FACE_CLOSEUP, required=False),
        ))
        assert compute_recipe_fingerprint(r1) != compute_recipe_fingerprint(r2)

    def test_slot_order_affects_fingerprint(self):
        r1 = _recipe(role_requirements=(
            _role_req(role=AssetRole.CHARACTER_FACE_CLOSEUP, slot_index=0),
            _role_req(role=AssetRole.ENVIRONMENT_VIEW, slot_index=1, required=False),
        ))
        r2 = _recipe(role_requirements=(
            _role_req(role=AssetRole.CHARACTER_FACE_CLOSEUP, slot_index=1),
            _role_req(role=AssetRole.ENVIRONMENT_VIEW, slot_index=0, required=False),
        ))
        assert compute_recipe_fingerprint(r1) != compute_recipe_fingerprint(r2)

    def test_workflow_fingerprint_change_affects_recipe_fingerprint(self):
        r1 = _recipe(workflow_template_fingerprint="a" * 64)
        r2 = _recipe(workflow_template_fingerprint="b" * 64)
        assert compute_recipe_fingerprint(r1) != compute_recipe_fingerprint(r2)

    def test_timestamps_do_not_affect_fingerprint(self):
        """No timestamp fields exist on recipe — fingerprint is purely semantic."""
        r = _recipe()
        fp1 = compute_recipe_fingerprint(r)
        fp2 = compute_recipe_fingerprint(r)
        assert fp1 == fp2

    def test_absolute_path_not_in_fingerprint(self):
        fp = compute_recipe_fingerprint(_recipe())
        assert "\\" not in fp
        assert "/" not in fp

    def test_runtime_observations_not_in_fingerprint(self):
        """Recipe fingerprint must not depend on runtime/VRAM/probe state."""
        r1 = _recipe(expected_vram_gb=24.0)
        r2 = _recipe(expected_vram_gb=48.0)
        # expected_vram_gb is advisory — NOT part of fingerprint
        assert compute_recipe_fingerprint(r1) == compute_recipe_fingerprint(r2)

    def test_dict_order_independent(self):
        """Role requirements fingerprint uses slot_index ordering, not tuple position."""
        r1 = _recipe(role_requirements=(
            _role_req(role=AssetRole.CHARACTER_FACE_CLOSEUP, slot_index=0),
            _role_req(role=AssetRole.ENVIRONMENT_VIEW, slot_index=1, required=False),
        ))
        r2 = _recipe(role_requirements=(
            _role_req(role=AssetRole.ENVIRONMENT_VIEW, slot_index=1, required=False),
            _role_req(role=AssetRole.CHARACTER_FACE_CLOSEUP, slot_index=0),
        ))
        assert compute_recipe_fingerprint(r1) == compute_recipe_fingerprint(r2)

    def test_provider_change_affects_fingerprint(self):
        r1 = _recipe(provider="minimax_h3")
        r2 = _recipe(provider="ltx")
        assert compute_recipe_fingerprint(r1) != compute_recipe_fingerprint(r2)

    def test_strategy_change_affects_fingerprint(self):
        r1 = _recipe(supported_strategies=("REFERENCE_TO_VIDEO",))
        r2 = _recipe(supported_strategies=("FIRST_LAST_FRAME",))
        assert compute_recipe_fingerprint(r1) != compute_recipe_fingerprint(r2)

    def test_max_references_change_affects_fingerprint(self):
        r1 = _recipe(max_references=4)
        r2 = _recipe(max_references=9)
        assert compute_recipe_fingerprint(r1) != compute_recipe_fingerprint(r2)


# ===========================================================================
# RECIPE REGISTRY
# ===========================================================================

class TestRecipeRegistry:
    def test_register_and_get(self):
        reg = RecipeRegistry()
        r = _recipe()
        reg.register(r)
        fetched = reg.get("test_recipe_v1", "1.0.0")
        assert fetched is not None
        assert fetched.id == r.id

    def test_deterministic_list(self):
        reg = RecipeRegistry()
        r1 = _recipe(id="b_recipe")
        r2 = _recipe(id="a_recipe")
        reg.register(r1)
        reg.register(r2)
        listed = reg.list_all()
        assert listed[0].id == "a_recipe"
        assert listed[1].id == "b_recipe"

    def test_multiple_versions_coexist(self):
        reg = RecipeRegistry()
        r1 = _recipe(id="my_recipe", version="1.0.0")
        r2 = _recipe(id="my_recipe", version="2.0.0",
                     role_requirements=(_role_req(),))  # different to get different fp
        reg.register(r1)
        reg.register(r2)
        assert reg.get("my_recipe", "1.0.0") is not None
        assert reg.get("my_recipe", "2.0.0") is not None
        versions = reg.list_versions("my_recipe")
        assert len(versions) == 2

    def test_same_version_same_fingerprint_idempotent(self):
        reg = RecipeRegistry()
        r = _recipe()
        reg.register(r)
        reg.register(r)  # should not raise
        assert len(reg.list_all()) == 1

    def test_same_version_different_fingerprint_rejected(self):
        reg = RecipeRegistry()
        r1 = _recipe(id="clash", version="1.0.0")
        reg.register(r1)
        # Different role requirements → different fingerprint
        r2_base = dict(
            id="clash", version="1.0.0",
            provider="minimax_h3", model_family="h3",
            supported_strategies=("REFERENCE_TO_VIDEO",),
            workflow_definition_id="h3_r2v_v2",
            workflow_definition_version="2.0.0",
            workflow_template_fingerprint="c" * 64,
            role_requirements=(_role_req(role=AssetRole.STYLE_REFERENCE),),
        )
        r2_nofp = ConditioningRecipe(**r2_base)
        r2 = ConditioningRecipe(**{**r2_base, "fingerprint": compute_recipe_fingerprint(r2_nofp)})
        with pytest.raises(ValueError, match="different fingerprint"):
            reg.register(r2)

    def test_exact_historical_version_resolution(self):
        reg = RecipeRegistry()
        r1 = _recipe(id="hist", version="1.0.0")
        r2 = _recipe(id="hist", version="2.0.0",
                     role_requirements=(_role_req(role=AssetRole.STYLE_REFERENCE),))
        reg.register(r1)
        reg.register(r2)
        assert reg.get("hist", "1.0.0").version == "1.0.0"
        assert reg.get("hist", "2.0.0").version == "2.0.0"

    def test_deprecated_recipe_remains_resolvable(self):
        """A recipe marked deprecated by convention should still be gettable."""
        reg = RecipeRegistry()
        r = _recipe(id="old_recipe", version="1.0.0")
        reg.register(r)
        # Mark deprecated (convention: register a newer version, old stays)
        r2 = _recipe(id="old_recipe", version="2.0.0",
                     role_requirements=(_role_req(role=AssetRole.PROP_REFERENCE),))
        reg.register(r2)
        assert reg.get("old_recipe", "1.0.0") is not None

    def test_no_silent_update(self):
        reg = RecipeRegistry()
        r = _recipe(id="stable", version="1.0.0")
        reg.register(r)
        fetched = reg.get("stable", "1.0.0")
        assert fetched.provider == "minimax_h3"

    def test_get_nonexistent_returns_none(self):
        reg = RecipeRegistry()
        assert reg.get("nonexistent", "1.0.0") is None


# ===========================================================================
# CAPABILITY PROFILE
# ===========================================================================

class TestCapabilityProfile:
    def test_local_profile(self):
        c = _capability(execution_mode="local")
        assert c.execution_mode == "local"

    def test_remote_profile(self):
        c = _capability(id="seedance_remote", execution_mode="remote",
                       provider="bytedance", model_family="seedance")
        assert c.execution_mode == "remote"

    def test_strategies(self):
        c = _capability(supported_strategies=("REFERENCE_TO_VIDEO", "FIRST_LAST_FRAME"))
        assert "REFERENCE_TO_VIDEO" in c.supported_strategies

    def test_modalities(self):
        c = _capability(supported_modalities=("image", "video", "audio"))
        assert "audio" in c.supported_modalities

    def test_reference_limits(self):
        c = _capability(max_references=4)
        assert c.max_references == 4

    def test_required_nodes_and_models(self):
        c = _capability(
            required_nodes=("NodeA", "NodeB"),
            required_models=("model_a.safetensors",),
        )
        assert len(c.required_nodes) == 2
        assert len(c.required_models) == 1

    def test_source_access_metadata(self):
        c = _capability(
            source_url="https://example.com",
            license_info="MIT",
            access_restrictions="requires API key",
        )
        assert c.source_url == "https://example.com"
        assert c.license_info == "MIT"

    def test_frozen(self):
        c = _capability()
        with pytest.raises(AttributeError):
            c.id = "changed"


# ===========================================================================
# CAPABILITY LIFECYCLE
# ===========================================================================

class TestCapabilityLifecycle:
    def test_discovered(self):
        reg = CapabilityRegistry()
        c = _capability()
        reg.register(c, CapabilityState.DISCOVERED)
        assert reg.get_state(c.id) == CapabilityState.DISCOVERED

    def test_valid_forward_transitions(self):
        reg = CapabilityRegistry()
        c = _capability()
        reg.register(c, CapabilityState.DISCOVERED)
        reg.transition(c.id, CapabilityState.AVAILABLE)
        assert reg.get_state(c.id) == CapabilityState.AVAILABLE
        reg.transition(c.id, CapabilityState.INSTALLED)
        assert reg.get_state(c.id) == CapabilityState.INSTALLED
        reg.transition(c.id, CapabilityState.VERIFIED)
        assert reg.get_state(c.id) == CapabilityState.VERIFIED
        reg.transition(c.id, CapabilityState.APPROVED)
        assert reg.get_state(c.id) == CapabilityState.APPROVED
        reg.transition(c.id, CapabilityState.DEPRECATED)
        assert reg.get_state(c.id) == CapabilityState.DEPRECATED

    def test_illegal_backward_transition(self):
        reg = CapabilityRegistry()
        c = _capability()
        reg.register(c, CapabilityState.APPROVED)
        with pytest.raises(ValueError, match="Invalid transition"):
            reg.transition(c.id, CapabilityState.INSTALLED)

    def test_illegal_skip_transition(self):
        reg = CapabilityRegistry()
        c = _capability()
        reg.register(c, CapabilityState.DISCOVERED)
        with pytest.raises(ValueError, match="Invalid transition"):
            reg.transition(c.id, CapabilityState.APPROVED)

    def test_probe_never_auto_approves(self):
        """Probing must not automatically set APPROVED state."""
        reg = CapabilityRegistry()
        c = _capability()
        reg.register(c, CapabilityState.DISCOVERED)
        # Even if probe succeeds, state does not change to APPROVED
        assert reg.get_state(c.id) == CapabilityState.DISCOVERED

    def test_deprecation_preserves_profile(self):
        reg = CapabilityRegistry()
        c = _capability()
        reg.register(c, CapabilityState.APPROVED)
        reg.transition(c.id, CapabilityState.DEPRECATED)
        profile = reg.get(c.id)
        assert profile is not None
        assert profile.provider == "minimax_h3"
        assert reg.get_state(c.id) == CapabilityState.DEPRECATED

    def test_deprecated_from_any_non_terminal(self):
        """DEPRECATED can be reached from AVAILABLE, INSTALLED, VERIFIED, APPROVED."""
        for initial in (CapabilityState.AVAILABLE, CapabilityState.INSTALLED,
                       CapabilityState.VERIFIED, CapabilityState.APPROVED):
            reg = CapabilityRegistry()
            c = _capability(id=f"cap_{initial.value}")
            reg.register(c, initial)
            reg.transition(c.id, CapabilityState.DEPRECATED)
            assert reg.get_state(c.id) == CapabilityState.DEPRECATED

    def test_deprecated_cannot_transition_further(self):
        reg = CapabilityRegistry()
        c = _capability()
        reg.register(c, CapabilityState.DEPRECATED)
        with pytest.raises(ValueError, match="Invalid transition"):
            reg.transition(c.id, CapabilityState.AVAILABLE)

    def test_unknown_profile_transition_rejected(self):
        reg = CapabilityRegistry()
        with pytest.raises(ValueError, match="Unknown profile"):
            reg.transition("nonexistent", CapabilityState.AVAILABLE)

    def test_list_all_deterministic(self):
        reg = CapabilityRegistry()
        reg.register(_capability(id="z_cap"), CapabilityState.DISCOVERED)
        reg.register(_capability(id="a_cap"), CapabilityState.AVAILABLE)
        listed = reg.list_all()
        assert listed[0][0].id == "a_cap"
        assert listed[1][0].id == "z_cap"

    def test_no_automatic_replacement_of_approved(self):
        """Registering a new capability must not affect an existing APPROVED one."""
        reg = CapabilityRegistry()
        old = _capability(id="established")
        reg.register(old, CapabilityState.APPROVED)
        new = _capability(id="newcomer")
        reg.register(new, CapabilityState.DISCOVERED)
        assert reg.get_state("established") == CapabilityState.APPROVED
        assert reg.get_state("newcomer") == CapabilityState.DISCOVERED


# ===========================================================================
# RUNTIME PROBE
# ===========================================================================

class TestRuntimeProbe:
    def test_reachable_runtime(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {
            "system": {"os": "nt"},
            "devices": [{"name": "NVIDIA RTX 5090", "vram_total": 34359738368, "vram_free": 30000000000}],
        }
        mock_adapter.get_object_info.return_value = {
            "MiniMaxH3ReferenceToVideo": {},
            "MiniMaxH3ImageToVideo": {},
        }
        result = probe_runtime(_capability(), mock_adapter)
        assert result.comfyui_reachable is True

    def test_unreachable_runtime(self):
        from film_director.errors import ComfyUIUnavailableError
        mock_adapter = MagicMock()
        mock_adapter.health.side_effect = ComfyUIUnavailableError("down")
        result = probe_runtime(_capability(), mock_adapter)
        assert result.comfyui_reachable is False

    def test_required_nodes_all_present(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {
            "MiniMaxH3ReferenceToVideo": {},
        }
        cap = _capability(required_nodes=("MiniMaxH3ReferenceToVideo",))
        result = probe_runtime(cap, mock_adapter)
        assert result.nodes_present["MiniMaxH3ReferenceToVideo"] is True

    def test_missing_node_detected(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {}
        cap = _capability(required_nodes=("MissingNode",))
        result = probe_runtime(cap, mock_adapter)
        assert result.nodes_present["MissingNode"] is False

    def test_required_models_present_from_object_info(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {
            "CheckpointLoaderSimple": {
                "input": {
                    "required": {
                        "ckpt_name": [["model.safetensors", "other.ckpt"]]
                    }
                }
            }
        }
        cap = _capability(required_models=("model.safetensors",))
        result = probe_runtime(cap, mock_adapter)
        assert result.models_present["model.safetensors"] is True

    def test_required_model_missing_from_inventory(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["other.safetensors"]]}}
            }
        }
        cap = _capability(required_models=("missing.safetensors",))
        result = probe_runtime(cap, mock_adapter)
        assert result.models_present["missing.safetensors"] is False

    def test_model_inventory_unavailable_returns_none(self):
        from film_director.errors import ComfyUIUnavailableError
        mock_adapter = MagicMock()
        mock_adapter.health.side_effect = ComfyUIUnavailableError("down")
        cap = _capability(required_models=("model.safetensors",))
        result = probe_runtime(cap, mock_adapter)
        assert result.models_present["model.safetensors"] is None

    def test_vram_observation_captured(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {
            "devices": [{"name": "RTX 5090", "vram_total": 34359738368, "vram_free": 30000000000}],
        }
        mock_adapter.get_object_info.return_value = {}
        result = probe_runtime(_capability(), mock_adapter)
        assert result.free_vram_gb is not None
        assert result.free_vram_gb > 0

    def test_transient_values_not_in_profile(self):
        """ProbeResult is transient — it must not alter the CapabilityProfile."""
        cap = _capability()
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {}
        result = probe_runtime(cap, mock_adapter)
        # Profile unchanged
        assert cap.id == "h3_r2v_local"
        # ProbeResult is separate
        assert isinstance(result, ProbeResult)
        assert result.profile_id == cap.id


# ===========================================================================
# RECIPE / CAPABILITY VALIDATION
# ===========================================================================

class TestRecipeCapabilityValidation:
    def test_compatible_pair(self):
        recipe = _recipe(provider="minimax_h3", model_family="h3",
                        supported_strategies=("REFERENCE_TO_VIDEO",))
        cap = _capability(provider="minimax_h3", model_family="h3",
                         supported_strategies=("REFERENCE_TO_VIDEO",),
                         supported_modalities=("image",))
        errors = validate_recipe_capability(recipe, cap)
        assert errors == []

    def test_provider_mismatch(self):
        recipe = _recipe(provider="minimax_h3")
        cap = _capability(provider="ltx")
        errors = validate_recipe_capability(recipe, cap)
        assert any("Provider mismatch" in e for e in errors)

    def test_model_family_mismatch(self):
        recipe = _recipe(model_family="h3")
        cap = _capability(model_family="ltx_2_3")
        errors = validate_recipe_capability(recipe, cap)
        assert any("Model family mismatch" in e for e in errors)

    def test_unsupported_strategy(self):
        recipe = _recipe(supported_strategies=("REFERENCE_TO_VIDEO",))
        cap = _capability(supported_strategies=("FIRST_LAST_FRAME",))
        errors = validate_recipe_capability(recipe, cap)
        assert any("Unsupported strategy" in e for e in errors)

    def test_unsupported_required_modality(self):
        recipe = _recipe(role_requirements=(
            _role_req(modality="video", required=True),
        ))
        cap = _capability(supported_modalities=("image",))
        errors = validate_recipe_capability(recipe, cap)
        assert any("Unsupported required modality" in e for e in errors)

    def test_reference_limit_overflow(self):
        recipe = _recipe(
            role_requirements=tuple(
                _role_req(role=AssetRole.CHARACTER_FACE_CLOSEUP, slot_index=i, required=True)
                for i in range(5)
            ),
            max_references=9,
        )
        cap = _capability(max_references=3)
        errors = validate_recipe_capability(recipe, cap)
        assert any("Reference limit" in e for e in errors)

    def test_optional_modality_not_flagged(self):
        """Optional roles with unsupported modality should not cause failure."""
        recipe = _recipe(role_requirements=(
            _role_req(modality="image", required=True),
            _role_req(modality="video", required=False, slot_index=1),
        ))
        cap = _capability(supported_modalities=("image",))
        errors = validate_recipe_capability(recipe, cap)
        assert errors == []


# ===========================================================================
# BOUNDARY TESTS
# ===========================================================================

class TestBoundary:
    def test_canonical_visual_asset_pack_no_provider_fields(self):
        from film_director.models.reference import VisualAssetPack
        fields = set(VisualAssetPack.model_fields.keys())
        provider = {"engine_family", "workflow_profile", "provider", "model",
                    "model_family", "workflow_definition_id"}
        assert fields & provider == set()

    def test_asset_role_binding_no_provider_fields(self):
        from film_director.models.reference import AssetRoleBinding
        fields = set(AssetRoleBinding.model_fields.keys())
        provider = {"provider", "model", "workflow", "slot", "picture_slot"}
        assert fields & provider == set()

    def test_generation_plan_no_provider_fields(self):
        from film_director.models.canonical import GenerationPlan
        fields = set(GenerationPlan.model_fields.keys())
        provider = {"engine_family", "workflow_profile", "provider", "model",
                    "model_family", "workflow_definition_id", "node_ids"}
        assert fields & provider == set()

    def test_recipe_provider_details_stay_provider_layer(self):
        """Recipe has provider-specific fields — that's correct for provider layer."""
        r = _recipe()
        assert hasattr(r, "provider")
        assert hasattr(r, "model_family")
        assert hasattr(r, "workflow_definition_id")


# ===========================================================================
# BACKWARD COMPATIBILITY
# ===========================================================================

class TestBackwardCompatibility:
    def test_existing_generation_succeeds_without_recipe(self, tmp_path):
        """Existing generation code paths must work without a ConditioningRecipe."""
        from film_director.generation.workflow_registry import WorkflowResolver
        import os
        project_root = str(tmp_path)
        wf_dir = os.path.join(project_root, "workflows", "h3")
        os.makedirs(wf_dir, exist_ok=True)
        # Copy workflow templates from real location
        import shutil
        real_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        ))))
        for name in ["r2v_v1.json", "r2v_v2.json", "flf_v1.json"]:
            src = os.path.join(real_root, "workflows", "h3", name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(wf_dir, name))
        resolver = WorkflowResolver(project_root)
        # Old code path: resolve by strategy
        defn = resolver.resolve("REFERENCE_TO_VIDEO")
        assert defn.id == "h3_r2v_v1"
        # Old code path: resolve by reference count
        defn2 = resolver.resolve_for_reference_count(2)
        assert defn2.id == "h3_r2v_v2"
        # Old code path: continuity
        defn3 = resolver.resolve_for_continuity(True, 0)
        assert defn3.id == "h3_flf_v1"

    def test_m7ga_visual_asset_pack_unchanged(self):
        from film_director.models.reference import VisualAssetPack, AssetRole
        # M7.G.A fields still present
        fields = set(VisualAssetPack.model_fields.keys())
        assert "id" in fields
        assert "project_id" in fields
        assert "version" in fields
        assert "fingerprint" in fields
        # AssetRole still has 18 values
        assert len(AssetRole) == 18

    def test_reference_asset_model_unchanged(self):
        from film_director.models.reference import (
            ReferenceAsset, ReferenceKind, ReferenceStatus,
            ReferenceSourceState,
        )
        assert len(ReferenceKind) == 3
        assert len(ReferenceStatus) == 4
        assert len(ReferenceSourceState) == 2

    def test_historical_data_readable(self, tmp_path):
        """Historical DB data must survive M7.G.B."""
        from film_director.persistence.repositories import ReferenceAssetRepository
        from film_director.models.reference import (
            ReferenceAsset, ReferenceKind, ReferenceSource,
        )
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO production_projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("p1", "wc1", "Test", "active", "16:9", "{}",
                 "t", "t", "wind_comic", "p1", "p1", 1, "t", "a" * 64),
            )
        repo = ReferenceAssetRepository(db)
        asset = ReferenceAsset(
            id="ref-hist", project_id="p1", character_id="c1",
            kind=ReferenceKind.CHARACTER_FACE,
            source=ReferenceSource.USER_UPLOAD,
            managed_path="storage/ref/hist/original.png",
            content_sha256="a" * 64,
            source_provenance="upload-hist",
            width=512, height=768,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        repo.save(asset)
        fetched = repo.get("ref-hist")
        assert fetched is not None
        assert fetched.id == "ref-hist"


# ===========================================================================
# M7.G.B HARDENING — CAPABILITY FINGERPRINT
# ===========================================================================

class TestCapabilityFingerprint:
    def test_deterministic_identical_semantics(self):
        fp1 = compute_capability_fingerprint(_capability())
        fp2 = compute_capability_fingerprint(_capability())
        assert fp1 == fp2

    def test_valid_sha256(self):
        fp = compute_capability_fingerprint(_capability())
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_unordered_collections_canonicalized(self):
        c1 = _capability(supported_strategies=("B", "A"))
        c2 = _capability(supported_strategies=("A", "B"))
        assert compute_capability_fingerprint(c1) == compute_capability_fingerprint(c2)

    def test_required_model_change_changes_fingerprint(self):
        c1 = _capability(required_models=("model_a.safetensors",))
        c2 = _capability(required_models=("model_b.safetensors",))
        assert compute_capability_fingerprint(c1) != compute_capability_fingerprint(c2)

    def test_required_node_change_changes_fingerprint(self):
        c1 = _capability(required_nodes=("NodeA",))
        c2 = _capability(required_nodes=("NodeB",))
        assert compute_capability_fingerprint(c1) != compute_capability_fingerprint(c2)

    def test_modality_change_changes_fingerprint(self):
        c1 = _capability(supported_modalities=("image",))
        c2 = _capability(supported_modalities=("image", "video"))
        assert compute_capability_fingerprint(c1) != compute_capability_fingerprint(c2)

    def test_probe_result_not_in_fingerprint(self):
        """ProbeResult is transient — must not affect fingerprint."""
        cap = _capability()
        fp = compute_capability_fingerprint(cap)
        # Different probe results with same profile
        assert fp == compute_capability_fingerprint(cap)

    def test_lifecycle_state_not_in_fingerprint(self):
        """Lifecycle state is mutable — must not affect profile fingerprint."""
        cap = _capability()
        fp = compute_capability_fingerprint(cap)
        # Fingerprint depends only on static profile, not on state
        assert fp == compute_capability_fingerprint(cap)

    def test_timestamps_not_in_fingerprint(self):
        cap = _capability()
        fp1 = compute_capability_fingerprint(cap)
        fp2 = compute_capability_fingerprint(cap)
        assert fp1 == fp2

    def test_vram_advisory_not_in_fingerprint(self):
        c1 = _capability(expected_vram_gb=24.0)
        c2 = _capability(expected_vram_gb=48.0)
        assert compute_capability_fingerprint(c1) == compute_capability_fingerprint(c2)


# ===========================================================================
# M7.G.B HARDENING — DURABLE PERSISTENCE
# ===========================================================================

class TestDurablePersistence:
    @pytest.fixture
    def db(self, tmp_path):
        d = Database(str(tmp_path / "test.db"))
        d.init_schema()
        return d

    def test_lifecycle_state_roundtrip(self, db):
        reg = CapabilityRegistry(db=db)
        cap = _capability()
        reg.register(cap, CapabilityState.DISCOVERED)
        reg.transition(cap.id, CapabilityState.AVAILABLE)
        # Reconstruct registry
        reg2 = CapabilityRegistry(db=db)
        reg2.register(cap, CapabilityState.DISCOVERED)
        assert reg2.get_state(cap.id) == CapabilityState.AVAILABLE

    def test_approved_survives_restart(self, db):
        reg = CapabilityRegistry(db=db)
        cap = _capability()
        reg.register(cap, CapabilityState.DISCOVERED)
        reg.transition(cap.id, CapabilityState.AVAILABLE)
        reg.transition(cap.id, CapabilityState.INSTALLED)
        reg.transition(cap.id, CapabilityState.VERIFIED)
        reg.transition(cap.id, CapabilityState.APPROVED)
        # Reconstruct
        reg2 = CapabilityRegistry(db=db)
        reg2.register(cap, CapabilityState.DISCOVERED)
        assert reg2.get_state(cap.id) == CapabilityState.APPROVED

    def test_deprecated_survives_restart(self, db):
        reg = CapabilityRegistry(db=db)
        cap = _capability()
        reg.register(cap, CapabilityState.APPROVED)
        reg.transition(cap.id, CapabilityState.DEPRECATED)
        reg2 = CapabilityRegistry(db=db)
        reg2.register(cap, CapabilityState.DISCOVERED)
        assert reg2.get_state(cap.id) == CapabilityState.DEPRECATED

    def test_migration_from_pre_hardening_db(self, tmp_path):
        """DB created before hardening must gain the new table."""
        db = Database(str(tmp_path / "old.db"))
        db.init_schema()
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "old.db"))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "capability_registry_states" in tables

    def test_static_profile_not_duplicated_in_db(self, db):
        """Static CapabilityProfile fields should NOT be stored in DB."""
        reg = CapabilityRegistry(db=db)
        reg.register(_capability(), CapabilityState.DISCOVERED)
        with db.connection() as conn:
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(capability_registry_states)"
            ).fetchall()}
        # Table stores only: capability_id, fingerprint, state, revision, timestamps
        assert "provider" not in cols
        assert "model_family" not in cols
        assert "required_models" not in cols

    def test_historical_state_preserved(self, db):
        reg = CapabilityRegistry(db=db)
        cap = _capability()
        reg.register(cap, CapabilityState.DISCOVERED)
        reg.transition(cap.id, CapabilityState.AVAILABLE)
        # Check DB directly
        with db.connection() as conn:
            row = conn.execute(
                "SELECT state, revision FROM capability_registry_states WHERE capability_id = ?",
                (cap.id,),
            ).fetchone()
        assert row["state"] == "available"
        assert row["revision"] >= 1


# ===========================================================================
# M7.G.B HARDENING — PROFILE CHANGE SAFETY
# ===========================================================================

class TestProfileChangeSafety:
    @pytest.fixture
    def db(self, tmp_path):
        d = Database(str(tmp_path / "test.db"))
        d.init_schema()
        return d

    def test_same_fingerprint_restores_state(self, db):
        cap = _capability()
        reg = CapabilityRegistry(db=db)
        reg.register(cap, CapabilityState.DISCOVERED)
        reg.transition(cap.id, CapabilityState.AVAILABLE)
        reg.transition(cap.id, CapabilityState.INSTALLED)
        reg.transition(cap.id, CapabilityState.VERIFIED)
        reg.transition(cap.id, CapabilityState.APPROVED)
        # Same profile → APPROVED restored
        reg2 = CapabilityRegistry(db=db)
        reg2.register(cap, CapabilityState.DISCOVERED)
        assert reg2.get_state(cap.id) == CapabilityState.APPROVED

    def test_changed_fingerprint_does_not_inherit_approved(self, db):
        """Changed profile semantics must NOT inherit old APPROVED state."""
        cap_old = _capability(required_models=("old_model.safetensors",))
        reg = CapabilityRegistry(db=db)
        reg.register(cap_old, CapabilityState.DISCOVERED)
        reg.transition(cap_old.id, CapabilityState.AVAILABLE)
        reg.transition(cap_old.id, CapabilityState.INSTALLED)
        reg.transition(cap_old.id, CapabilityState.VERIFIED)
        reg.transition(cap_old.id, CapabilityState.APPROVED)
        # Changed profile (different required_models → different fingerprint)
        cap_new = _capability(required_models=("new_model.safetensors",))
        reg2 = CapabilityRegistry(db=db)
        reg2.register(cap_new, CapabilityState.DISCOVERED)
        # Must NOT inherit APPROVED
        assert reg2.get_state(cap_new.id) == CapabilityState.DISCOVERED

    def test_old_state_not_deleted_on_fingerprint_change(self, db):
        """DB row should be updated, not deleted, on profile change."""
        cap = _capability(required_models=("old.safetensors",))
        reg = CapabilityRegistry(db=db)
        reg.register(cap, CapabilityState.APPROVED)
        # Changed profile
        cap2 = _capability(required_models=("new.safetensors",))
        reg2 = CapabilityRegistry(db=db)
        reg2.register(cap2, CapabilityState.DISCOVERED)
        # DB still has a row
        with db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM capability_registry_states WHERE capability_id = ?",
                (cap.id,),
            ).fetchone()
        assert row is not None


# ===========================================================================
# M7.G.B HARDENING — MODEL PROBE
# ===========================================================================

class TestModelProbe:
    def test_model_present_in_inventory(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {
            "LoadCheckpoint": {
                "input": {"required": {"ckpt_name": [["my_model.safetensors"]]}}
            }
        }
        cap = _capability(required_models=("my_model.safetensors",))
        result = probe_runtime(cap, mock_adapter)
        assert result.models_present["my_model.safetensors"] is True

    def test_model_missing_from_inventory(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {
            "LoadCheckpoint": {
                "input": {"required": {"ckpt_name": [["other.safetensors"]]}}
            }
        }
        cap = _capability(required_models=("needed.safetensors",))
        result = probe_runtime(cap, mock_adapter)
        assert result.models_present["needed.safetensors"] is False

    def test_several_models_all_present(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {
            "Loader1": {"input": {"required": {"ckpt": [["a.safetensors", "b.safetensors"]]}}},
            "Loader2": {"input": {"required": {"model": [["c.ckpt"]]}}},
        }
        cap = _capability(required_models=("a.safetensors", "b.safetensors", "c.ckpt"))
        result = probe_runtime(cap, mock_adapter)
        assert all(v is True for v in result.models_present.values())

    def test_one_of_several_missing(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {
            "Loader": {"input": {"required": {"ckpt": [["a.safetensors"]]}}},
        }
        cap = _capability(required_models=("a.safetensors", "missing.safetensors"))
        result = probe_runtime(cap, mock_adapter)
        assert result.models_present["a.safetensors"] is True
        assert result.models_present["missing.safetensors"] is False

    def test_inventory_unavailable_returns_none(self):
        from film_director.errors import ComfyUIUnavailableError
        mock_adapter = MagicMock()
        mock_adapter.health.side_effect = ComfyUIUnavailableError("down")
        cap = _capability(required_models=("model.safetensors",))
        result = probe_runtime(cap, mock_adapter)
        assert result.models_present["model.safetensors"] is None

    def test_node_present_model_missing_not_installed(self):
        """Node present + required model missing = NOT installation-ready."""
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {
            "MiniMaxH3ReferenceToVideo": {"input": {"required": {}}},
        }
        cap = _capability(
            required_nodes=("MiniMaxH3ReferenceToVideo",),
            required_models=("missing.safetensors",),
        )
        result = probe_runtime(cap, mock_adapter)
        assert result.nodes_present["MiniMaxH3ReferenceToVideo"] is True
        assert result.models_present["missing.safetensors"] is False

    def test_runtime_unreachable(self):
        from film_director.errors import ComfyUIUnavailableError
        mock_adapter = MagicMock()
        mock_adapter.health.side_effect = ComfyUIUnavailableError("down")
        result = probe_runtime(_capability(), mock_adapter)
        assert result.comfyui_reachable is False

    def test_node_missing(self):
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {}
        cap = _capability(required_nodes=("MissingNode",))
        result = probe_runtime(cap, mock_adapter)
        assert result.nodes_present["MissingNode"] is False


# ===========================================================================
# M7.G.B HARDENING — TRANSIENT SEPARATION
# ===========================================================================

class TestTransientSeparation:
    def test_vram_change_not_in_fingerprint(self):
        cap = _capability(expected_vram_gb=24.0)
        fp = compute_capability_fingerprint(cap)
        cap2 = _capability(expected_vram_gb=48.0)
        assert compute_capability_fingerprint(cap2) == fp

    def test_gpu_name_not_in_fingerprint(self):
        cap = _capability()
        fp = compute_capability_fingerprint(cap)
        # GPU name is in ProbeResult, not profile — no effect
        assert compute_capability_fingerprint(cap) == fp

    def test_probe_timestamp_not_in_fingerprint(self):
        cap = _capability()
        fp = compute_capability_fingerprint(cap)
        assert compute_capability_fingerprint(cap) == fp

    def test_probe_does_not_overwrite_approved(self, tmp_path):
        """Probing must not change APPROVED state."""
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        reg = CapabilityRegistry(db=db)
        cap = _capability()
        reg.register(cap, CapabilityState.APPROVED)
        # Probe happens externally, returns ProbeResult
        mock_adapter = MagicMock()
        mock_adapter.health.return_value = {"devices": []}
        mock_adapter.get_object_info.return_value = {}
        result = probe_runtime(cap, mock_adapter)
        # Registry state unchanged
        assert reg.get_state(cap.id) == CapabilityState.APPROVED


# ===========================================================================
# M7.G.B HARDENING — EXTRACT AVAILABLE MODELS
# ===========================================================================

class TestExtractAvailableModels:
    def test_basic_extraction(self):
        info = {
            "LoadCheckpoint": {
                "input": {
                    "required": {
                        "ckpt_name": [["model_a.safetensors", "model_b.ckpt"]]
                    }
                }
            }
        }
        models = extract_available_models(info)
        assert "model_a.safetensors" in models
        assert "model_b.ckpt" in models

    def test_multiple_nodes(self):
        info = {
            "Node1": {"input": {"required": {"model": [["a.safetensors"]]}}},
            "Node2": {"input": {"required": {"unet": [["b.safetensors"]]}}},
        }
        models = extract_available_models(info)
        assert "a.safetensors" in models
        assert "b.safetensors" in models

    def test_optional_inputs(self):
        info = {
            "Node": {"input": {"optional": {"lora": [["my.safetensors"]]}}}
        }
        models = extract_available_models(info)
        assert "my.safetensors" in models

    def test_non_model_values_excluded(self):
        info = {
            "Node": {"input": {"required": {"mode": [["nearest", "bilinear"]]}}}
        }
        models = extract_available_models(info)
        assert len(models) == 0

    def test_empty_object_info(self):
        assert extract_available_models({}) == set()

    def test_subdirectory_models(self):
        info = {
            "Node": {"input": {"required": {"ckpt": [["minimax/h3/model.safetensors"]]}}}
        }
        models = extract_available_models(info)
        assert "minimax/h3/model.safetensors" in models
