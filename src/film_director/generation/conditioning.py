"""M7.G.B — ConditioningRecipe and CapabilityRegistry.

Provider-layer infrastructure for mapping canonical VisualAssetPack
semantic roles to concrete generator/provider workflow capabilities.

Source of truth:
- Static ConditioningRecipe/CapabilityProfile: code-based registries
  (following WorkflowDefinition pattern). Frozen dataclasses.
- Mutable CapabilityState lifecycle: durable SQLite persistence via
  capability_registry_states table. Survives process restart.
- ProbeResult: transient runtime observation, never persisted.

Canonical entities (VisualAssetPack, AssetRole, GenerationPlan, etc.)
are NOT modified. All provider-specific fields remain in this layer only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from film_director.models.reference import AssetRole

if TYPE_CHECKING:
    from film_director.generation.comfyui_adapter import ComfyUIAdapter
    from film_director.persistence.database import Database

logger = logging.getLogger(__name__)

_MODEL_EXTENSIONS = frozenset({
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf",
})


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

    # Runtime expectations (advisory — NOT part of fingerprint)
    expected_vram_gb: float | None = None
    expected_runtime_class: str = ""


# ---------------------------------------------------------------------------
# Capability profile fingerprinting
# ---------------------------------------------------------------------------

def compute_capability_fingerprint(profile: CapabilityProfile) -> str:
    """Deterministic SHA-256 fingerprint of a capability profile's stable semantics.

    Includes all declarative fields that define the profile's identity.
    Excludes: lifecycle state, timestamps, probe observations, VRAM,
    runtime class (advisory only).
    """
    payload = {
        "id": profile.id,
        "provider": profile.provider,
        "model_family": profile.model_family,
        "execution_mode": profile.execution_mode,
        "supported_strategies": sorted(profile.supported_strategies),
        "supported_modalities": sorted(profile.supported_modalities),
        "max_references": profile.max_references,
        "generation_constraints": profile.generation_constraints,
        "windows_compatible": profile.windows_compatible,
        "comfyui_compatible": profile.comfyui_compatible,
        "required_models": sorted(profile.required_models),
        "required_nodes": sorted(profile.required_nodes),
        "source_url": profile.source_url,
        "license_info": profile.license_info,
        "access_restrictions": profile.access_restrictions,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# ProbeResult — transient runtime observation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbeResult:
    """Transient runtime observation — NOT part of static fingerprint.

    Captures current machine/runtime state at probe time.
    Does NOT alter the CapabilityProfile or its identity.

    models_present values:
    - True  = confirmed present in ComfyUI model inventory
    - False = confirmed missing from inventory
    - None  = inventory unavailable, cannot be determined
    """
    profile_id: str
    comfyui_reachable: bool = False
    nodes_present: dict[str, bool] = field(default_factory=dict)
    models_present: dict[str, bool | None] = field(default_factory=dict)
    free_vram_gb: float | None = None
    gpu_name: str = ""
    probed_at: str = ""


# ---------------------------------------------------------------------------
# CapabilityRegistry — lifecycle management with durable persistence
# ---------------------------------------------------------------------------

class CapabilityRegistry:
    """Registry for provider capability profiles and lifecycle state.

    Static CapabilityProfile declarations are immutable code-based constants.
    Lifecycle state (CapabilityState) is durably persisted in SQLite when
    a Database is provided. Without a Database, operates in-memory only.

    Invariants:
    - Probe never auto-approves
    - APPROVED requires explicit transition
    - DEPRECATED preserves profile (no deletion)
    - New capabilities never replace existing APPROVED ones
    - Historical profiles remain discoverable
    - APPROVED/DEPRECATED survive process restart (when DB provided)
    - Changed profile fingerprint does NOT inherit old APPROVED state
    """

    def __init__(self, db: Database | None = None) -> None:
        self._profiles: dict[str, CapabilityProfile] = {}
        self._states: dict[str, CapabilityState] = {}
        self._fingerprints: dict[str, str] = {}
        self._revisions: dict[str, int] = {}
        self._db = db

    def register(
        self,
        profile: CapabilityProfile,
        state: CapabilityState = CapabilityState.DISCOVERED,
    ) -> None:
        fp = compute_capability_fingerprint(profile)
        self._profiles[profile.id] = profile
        self._fingerprints[profile.id] = fp

        # Try to restore persisted state
        if self._db is not None and profile.id not in self._states:
            persisted = self._load_state(profile.id)
            if persisted is not None:
                if persisted["capability_fingerprint"] == fp:
                    # Same profile semantics — restore durable state
                    self._states[profile.id] = CapabilityState(persisted["state"])
                    self._revisions[profile.id] = persisted["revision"]
                    return
                else:
                    # Profile definition changed — do NOT inherit old state
                    logger.warning(
                        "Capability %r profile fingerprint changed "
                        "(stored=%s, current=%s) — resetting to %s",
                        profile.id, persisted["capability_fingerprint"][:16],
                        fp[:16], state.value,
                    )

        if profile.id not in self._states:
            self._states[profile.id] = state
            self._revisions[profile.id] = 1
            self._persist_state(profile.id)

    def get(self, profile_id: str) -> CapabilityProfile | None:
        return self._profiles.get(profile_id)

    def get_state(self, profile_id: str) -> CapabilityState | None:
        return self._states.get(profile_id)

    def get_fingerprint(self, profile_id: str) -> str | None:
        return self._fingerprints.get(profile_id)

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
        self._revisions[profile_id] = self._revisions.get(profile_id, 0) + 1
        self._persist_state(profile_id)

    def list_all(self) -> list[tuple[CapabilityProfile, CapabilityState]]:
        return sorted(
            [(self._profiles[pid], self._states[pid]) for pid in self._profiles],
            key=lambda t: t[0].id,
        )

    # -- Persistence helpers --

    def _persist_state(self, profile_id: str) -> None:
        if self._db is None:
            return
        state = self._states.get(profile_id)
        fp = self._fingerprints.get(profile_id)
        rev = self._revisions.get(profile_id, 1)
        if state is None or fp is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._db.connection() as conn:
                conn.execute(
                    """INSERT INTO capability_registry_states
                        (capability_id, capability_fingerprint, state, revision,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(capability_id) DO UPDATE SET
                        capability_fingerprint = excluded.capability_fingerprint,
                        state = excluded.state,
                        revision = excluded.revision,
                        updated_at = excluded.updated_at
                    """,
                    (profile_id, fp, state.value, rev, now, now),
                )
        except sqlite3.Error as exc:
            logger.warning("Failed to persist capability state for %r: %s",
                          profile_id, exc)

    def _load_state(self, profile_id: str) -> dict | None:
        if self._db is None:
            return None
        try:
            with self._db.connection() as conn:
                row = conn.execute(
                    "SELECT * FROM capability_registry_states WHERE capability_id = ?",
                    (profile_id,),
                ).fetchone()
            if row is None:
                return None
            return dict(row)
        except sqlite3.Error:
            return None


# ---------------------------------------------------------------------------
# Model inventory extraction from ComfyUI object_info
# ---------------------------------------------------------------------------

def extract_available_models(object_info: dict) -> set[str]:
    """Extract model filenames available in ComfyUI from /object_info response.

    Scans all node class definitions for combo-type inputs that contain
    model filenames (identified by common model file extensions).
    This is generic — not specific to any particular model family.
    """
    models: set[str] = set()
    for _node_class, node_def in object_info.items():
        if not isinstance(node_def, dict):
            continue
        inputs = node_def.get("input", {})
        if not isinstance(inputs, dict):
            continue
        for category in ("required", "optional"):
            cat_inputs = inputs.get(category, {})
            if not isinstance(cat_inputs, dict):
                continue
            for _param, param_def in cat_inputs.items():
                if not isinstance(param_def, (list, tuple)) or len(param_def) < 1:
                    continue
                choices = param_def[0]
                if not isinstance(choices, list):
                    continue
                for choice in choices:
                    if isinstance(choice, str):
                        lower = choice.lower()
                        if any(lower.endswith(ext) for ext in _MODEL_EXTENSIONS):
                            models.add(choice)
    return models


# ---------------------------------------------------------------------------
# Runtime probing
# ---------------------------------------------------------------------------

def probe_runtime(
    profile: CapabilityProfile,
    adapter: ComfyUIAdapter,
) -> ProbeResult:
    """Probe current runtime state for a capability profile.

    Uses ComfyUIAdapter for reachability, node discovery, and model discovery.
    Returns a transient ProbeResult — does NOT alter profile or registry state.

    models_present values:
    - True  = model filename found in ComfyUI's known model inventory
    - False = model filename NOT found in inventory (inventory was available)
    - None  = model inventory could not be determined

    Does NOT:
    - Run generation
    - Auto-approve capabilities
    - Mutate static profile
    - Alter fingerprints
    """
    from film_director.errors import ComfyUIUnavailableError

    reachable = False
    nodes_present: dict[str, bool] = {}
    models_present: dict[str, bool | None] = {m: None for m in profile.required_models}
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

    # Check node and model availability
    if reachable:
        try:
            object_info = adapter.get_object_info()
            # Node check
            for node_name in profile.required_nodes:
                nodes_present[node_name] = node_name in object_info
            # Model check — extract from combo inputs
            available_models = extract_available_models(object_info)
            for model_name in profile.required_models:
                models_present[model_name] = model_name in available_models
        except Exception:
            for node_name in profile.required_nodes:
                nodes_present[node_name] = False
            # Models remain None (unknown)

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


# ---------------------------------------------------------------------------
# Production recipe: H3 R2V Image Pack v1 (M7.G.C)
# ---------------------------------------------------------------------------

_H3_R2V_IMAGE_PACK_V1_FP = (
    "32caca08d5f4bd0b4578efc4f709024a7d222dd933f9224d9e718bc20f4a7351"
)


def build_h3_image_pack_recipe() -> ConditioningRecipe:
    """Build the production H3 image-only multi-reference recipe.

    Slot ordering:
    1. CHARACTER_FACE_CLOSEUP — character identity anchor (required)
    2. ENVIRONMENT_VIEW — shot-specific environment (required)
    3. CONTINUITY_FRAME — predecessor last frame (required)
    4. PROP_REFERENCE — important prop (optional)

    No ref_video. No ref_audio.
    """
    role_reqs = (
        RecipeRoleRequirement(
            role=AssetRole.CHARACTER_FACE_CLOSEUP,
            required=True, slot_index=0, modality="image", max_count=1,
        ),
        RecipeRoleRequirement(
            role=AssetRole.ENVIRONMENT_VIEW,
            required=True, slot_index=1, modality="image", max_count=1,
        ),
        RecipeRoleRequirement(
            role=AssetRole.CONTINUITY_FRAME,
            required=True, slot_index=2, modality="image", max_count=1,
        ),
        RecipeRoleRequirement(
            role=AssetRole.PROP_REFERENCE,
            required=False, slot_index=3, modality="image", max_count=1,
        ),
    )

    recipe = ConditioningRecipe(
        id="h3_r2v_image_pack_v1",
        version="1.0.0",
        provider="minimax_h3",
        model_family="h3_ref2va",
        supported_strategies=("REFERENCE_TO_VIDEO",),
        workflow_definition_id="h3_r2v_image_pack_v1",
        workflow_definition_version="1.0.0",
        workflow_template_fingerprint=_H3_R2V_IMAGE_PACK_V1_FP,
        role_requirements=role_reqs,
        supports_image_references=True,
        supports_video_references=False,
        supports_audio_references=False,
        max_references=4,
        resolution_constraints={"megapixels": 1.0, "aspect": "16:9"},
        frame_constraints={
            "fps": 24, "frame_grid": "17k+5",
            "min_frames": 124, "max_frames": 362,
        },
        prompt_tag_convention="<Picture N>",
        fallback_behavior="omit optional Picture 4 slot when no prop",
        expected_vram_gb=24.0,
        expected_runtime_class="4-7 min per shot on RTX 5090",
        source_url="",
        source_notes="Derived from proven h3_r2v_v2 with 4 materialized image slots",
    )
    fp = compute_recipe_fingerprint(recipe)
    # Rebuild with computed fingerprint
    fields = {k: getattr(recipe, k) for k in recipe.__dataclass_fields__}
    fields["fingerprint"] = fp
    return ConditioningRecipe(**fields)


# Pre-built singleton
H3_IMAGE_PACK_RECIPE = build_h3_image_pack_recipe()
