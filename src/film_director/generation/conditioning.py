"""M7.G.B — ConditioningRecipe and CapabilityRegistry.

Provider-layer infrastructure for mapping canonical VisualAssetPack
semantic roles to concrete generator/provider workflow capabilities.

Source of truth: Code-based registries (following WorkflowDefinition pattern).
No new database tables. Static declarations are immutable frozen dataclasses.
Mutable lifecycle state lives in CapabilityRegistry (in-memory).

Canonical entities (VisualAssetPack, AssetRole, GenerationPlan, etc.)
are NOT modified. All provider-specific fields remain in this layer only.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from film_director.models.reference import AssetRole

if TYPE_CHECKING:
    from film_director.generation.comfyui_adapter import ComfyUIAdapter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RecipeRoleRequirement — provider-layer slot mapping
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RecipeRoleRequirement:
    """Provider-layer mapping from canonical AssetRole to workflow slot.

    Lives in the provider layer — may contain provider-specific slot semantics.
    Does NOT alter AssetRole itself.
    """
    role: AssetRole
    required: bool = True
    slot_index: int = 0
    modality: Literal["image", "video", "audio"] = "image"
    max_count: int = 1


# ---------------------------------------------------------------------------
# ConditioningRecipe — versioned provider-layer recipe
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConditioningRecipe:
    """Versioned provider-layer recipe mapping canonical roles to workflow inputs.

    Immutable after creation. (id, version) identifies one immutable recipe.
    Contains provider-specific fields (provider, model_family, workflow refs)
    that are NOT allowed in canonical entities.
    """
    # Identity / version
    id: str
    version: str
    fingerprint: str = ""

    # Target
    provider: str = ""
    model_family: str = ""
    supported_strategies: tuple[str, ...] = ()
    workflow_definition_id: str = ""
    workflow_definition_version: str = ""
    workflow_template_fingerprint: str = ""

    # Role requirements
    role_requirements: tuple[RecipeRoleRequirement, ...] = ()

    # Capabilities / constraints
    supports_image_references: bool = True
    supports_video_references: bool = False
    supports_audio_references: bool = False
    max_references: int = 9
    resolution_constraints: dict = field(default_factory=dict)
    frame_constraints: dict = field(default_factory=dict)
    prompt_tag_convention: str = ""
    fallback_behavior: str = ""

    # Runtime expectations (advisory — NOT part of fingerprint)
    expected_vram_gb: float | None = None
    expected_runtime_class: str = ""

    # Provenance
    source_url: str = ""
    source_notes: str = ""


# ---------------------------------------------------------------------------
# Recipe fingerprinting
# ---------------------------------------------------------------------------

def compute_recipe_fingerprint(recipe: ConditioningRecipe) -> str:
    """Deterministic SHA-256 fingerprint of a recipe's semantic identity.

    Includes all fields that define the recipe's behavior.
    Excludes advisory/transient fields (expected_vram_gb, expected_runtime_class,
    source_notes).

    Canonicalizes role requirements by slot_index for determinism.
    """
    sorted_roles = sorted(
        [
            {
                "role": rr.role.value,
                "required": rr.required,
                "slot_index": rr.slot_index,
                "modality": rr.modality,
                "max_count": rr.max_count,
            }
            for rr in recipe.role_requirements
        ],
        key=lambda r: (r["slot_index"], r["role"]),
    )

    payload = {
        "id": recipe.id,
        "version": recipe.version,
        "provider": recipe.provider,
        "model_family": recipe.model_family,
        "supported_strategies": sorted(recipe.supported_strategies),
        "workflow_definition_id": recipe.workflow_definition_id,
        "workflow_definition_version": recipe.workflow_definition_version,
        "workflow_template_fingerprint": recipe.workflow_template_fingerprint,
        "role_requirements": sorted_roles,
        "supports_image_references": recipe.supports_image_references,
        "supports_video_references": recipe.supports_video_references,
        "supports_audio_references": recipe.supports_audio_references,
        "max_references": recipe.max_references,
        "resolution_constraints": recipe.resolution_constraints,
        "frame_constraints": recipe.frame_constraints,
        "prompt_tag_convention": recipe.prompt_tag_convention,
        "fallback_behavior": recipe.fallback_behavior,
        "source_url": recipe.source_url,
    }

    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# RecipeRegistry — append-only version management
# ---------------------------------------------------------------------------

class RecipeRegistry:
    """In-memory registry for versioned ConditioningRecipe definitions.

    Follows WorkflowDefinition code-registry pattern.
    No database persistence — static declarations in code.

    Invariants:
    - Same (id, version) + same fingerprint = idempotent re-register
    - Same (id, version) + different fingerprint = rejected
    - Historical versions remain resolvable
    - No silent mutation
    """

    def __init__(self) -> None:
        self._recipes: dict[tuple[str, str], ConditioningRecipe] = {}

    def register(self, recipe: ConditioningRecipe) -> None:
        key = (recipe.id, recipe.version)
        if key in self._recipes:
            existing = self._recipes[key]
            if existing.fingerprint == recipe.fingerprint:
                return  # idempotent
            raise ValueError(
                f"Recipe {recipe.id!r} v{recipe.version} already registered "
                f"with different fingerprint "
                f"(existing={existing.fingerprint[:16]}..., "
                f"new={recipe.fingerprint[:16]}...)"
            )
        self._recipes[key] = recipe

    def get(self, recipe_id: str, version: str) -> ConditioningRecipe | None:
        return self._recipes.get((recipe_id, version))

    def list_all(self) -> list[ConditioningRecipe]:
        return sorted(self._recipes.values(), key=lambda r: (r.id, r.version))

    def list_versions(self, recipe_id: str) -> list[ConditioningRecipe]:
        return sorted(
            [r for r in self._recipes.values() if r.id == recipe_id],
            key=lambda r: r.version,
        )


# ---------------------------------------------------------------------------
# CapabilityState — lifecycle enum
# ---------------------------------------------------------------------------

class CapabilityState(str, Enum):
    """Provider capability lifecycle states.

    DISCOVERED → AVAILABLE → INSTALLED → VERIFIED → APPROVED → DEPRECATED

    Probe does not imply APPROVED. APPROVED requires explicit action.
    DEPRECATED is not deleted — historical profile remains discoverable.
    """
    DISCOVERED = "discovered"
    AVAILABLE = "available"
    INSTALLED = "installed"
    VERIFIED = "verified"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


_VALID_TRANSITIONS: dict[str, set[str]] = {
    "discovered": {"available"},
    "available": {"installed", "deprecated"},
    "installed": {"verified", "deprecated"},
    "verified": {"approved", "deprecated"},
    "approved": {"deprecated"},
    "deprecated": set(),
}


# ---------------------------------------------------------------------------
# CapabilityProfile — static declaration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilityProfile:
    """Static declaration of a provider/generator capability.

    Frozen — mutable lifecycle state lives in CapabilityRegistry.
    Contains provider-specific fields that are NOT allowed in canonical models.
    """
    # Identity
    id: str
    provider: str
    model_family: str
    execution_mode: Literal["local", "remote"] = "local"

    # Supported generation features
    supported_strategies: tuple[str, ...] = ()
    supported_modalities: tuple[str, ...] = ("image",)
    max_references: int = 0
    generation_constraints: dict = field(default_factory=dict)

    # Compatibility
    windows_compatible: bool = True
    comfyui_compatible: bool = True
    required_models: tuple[str, ...] = ()
    required_nodes: tuple[str, ...] = ()

    # Provenance / access
    source_url: str = ""
    license_info: str = ""
    access_restrictions: str = ""

    # Runtime expectations (advisory)
    expected_vram_gb: float | None = None
    expected_runtime_class: str = ""


# ---------------------------------------------------------------------------
# ProbeResult — transient runtime observation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbeResult:
    """Transient runtime observation — NOT part of static fingerprint.

    Captures current machine/runtime state at probe time.
    Does NOT alter the CapabilityProfile or its identity.
    """
    profile_id: str
    comfyui_reachable: bool = False
    nodes_present: dict[str, bool] = field(default_factory=dict)
    models_present: dict[str, bool] = field(default_factory=dict)
    free_vram_gb: float | None = None
    gpu_name: str = ""
    probed_at: str = ""


# ---------------------------------------------------------------------------
# CapabilityRegistry — lifecycle management
# ---------------------------------------------------------------------------

class CapabilityRegistry:
    """In-memory registry for provider capability profiles and lifecycle state.

    Static CapabilityProfile declarations are immutable.
    Lifecycle state (CapabilityState) is mutable via explicit transitions.

    Invariants:
    - Probe never auto-approves
    - APPROVED requires explicit transition
    - DEPRECATED preserves profile (no deletion)
    - New capabilities never replace existing APPROVED ones
    - Historical profiles remain discoverable
    """

    def __init__(self) -> None:
        self._profiles: dict[str, CapabilityProfile] = {}
        self._states: dict[str, CapabilityState] = {}

    def register(
        self,
        profile: CapabilityProfile,
        state: CapabilityState = CapabilityState.DISCOVERED,
    ) -> None:
        self._profiles[profile.id] = profile
        if profile.id not in self._states:
            self._states[profile.id] = state

    def get(self, profile_id: str) -> CapabilityProfile | None:
        return self._profiles.get(profile_id)

    def get_state(self, profile_id: str) -> CapabilityState | None:
        return self._states.get(profile_id)

    def transition(self, profile_id: str, target_state: CapabilityState) -> None:
        current = self._states.get(profile_id)
        if current is None:
            raise ValueError(f"Unknown profile: {profile_id!r}")
        allowed = _VALID_TRANSITIONS.get(current.value, set())
        if target_state.value not in allowed:
            raise ValueError(
                f"Invalid transition: {current.value} → {target_state.value} "
                f"for profile {profile_id!r}"
            )
        self._states[profile_id] = target_state

    def list_all(self) -> list[tuple[CapabilityProfile, CapabilityState]]:
        return sorted(
            [(self._profiles[pid], self._states[pid]) for pid in self._profiles],
            key=lambda t: t[0].id,
        )


# ---------------------------------------------------------------------------
# Runtime probing
# ---------------------------------------------------------------------------

def probe_runtime(
    profile: CapabilityProfile,
    adapter: ComfyUIAdapter,
) -> ProbeResult:
    """Probe current runtime state for a capability profile.

    Uses ComfyUIAdapter for reachability and node discovery.
    Returns a transient ProbeResult — does NOT alter profile or registry state.

    Does NOT:
    - Run generation
    - Auto-approve capabilities
    - Mutate static profile
    - Alter fingerprints
    """
    from film_director.errors import ComfyUIUnavailableError

    reachable = False
    nodes_present: dict[str, bool] = {}
    models_present: dict[str, bool] = {m: False for m in profile.required_models}
    free_vram: float | None = None
    gpu_name = ""

    # Check reachability
    try:
        health = adapter.health()
        reachable = True

        # Extract GPU info
        devices = health.get("devices", [])
        if devices:
            dev = devices[0]
            gpu_name = dev.get("name", "")
            vram_free = dev.get("vram_free")
            if vram_free is not None:
                free_vram = round(vram_free / (1024 ** 3), 2)
    except ComfyUIUnavailableError:
        pass

    # Check node availability
    if reachable:
        try:
            object_info = adapter.get_object_info()
            for node_name in profile.required_nodes:
                nodes_present[node_name] = node_name in object_info
        except Exception:
            for node_name in profile.required_nodes:
                nodes_present[node_name] = False

    return ProbeResult(
        profile_id=profile.id,
        comfyui_reachable=reachable,
        nodes_present=nodes_present,
        models_present=models_present,
        free_vram_gb=free_vram,
        gpu_name=gpu_name,
        probed_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Recipe ↔ Capability validation
# ---------------------------------------------------------------------------

def validate_recipe_capability(
    recipe: ConditioningRecipe,
    capability: CapabilityProfile,
) -> list[str]:
    """Validate that a recipe is compatible with a capability profile.

    Returns list of incompatibility reasons. Empty list = compatible.

    Does NOT:
    - Implement automatic model routing
    - Automatically choose a "best" model
    - Run generation
    """
    errors: list[str] = []

    if recipe.provider != capability.provider:
        errors.append(
            f"Provider mismatch: recipe={recipe.provider}, "
            f"capability={capability.provider}"
        )

    if recipe.model_family != capability.model_family:
        errors.append(
            f"Model family mismatch: recipe={recipe.model_family}, "
            f"capability={capability.model_family}"
        )

    for strategy in recipe.supported_strategies:
        if strategy not in capability.supported_strategies:
            errors.append(f"Unsupported strategy: {strategy}")

    for rr in recipe.role_requirements:
        if rr.required and rr.modality not in capability.supported_modalities:
            errors.append(
                f"Unsupported required modality: {rr.modality} "
                f"for role {rr.role.value}"
            )

    if capability.max_references > 0:
        required_count = sum(1 for r in recipe.role_requirements if r.required)
        if required_count > capability.max_references:
            errors.append(
                f"Reference limit exceeded: recipe needs {required_count}, "
                f"capability allows {capability.max_references}"
            )

    return errors
