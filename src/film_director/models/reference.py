"""Canonical reference management models (M5, M7.G.A).

ReferenceAsset — source-neutral managed image reference.
ReferenceGenerationRequest — immutable ComfyUI generation input snapshot.
ReferenceGenerationExecution — mutable generation lifecycle.
AssetRole — semantic conditioning role (M7.G.A).
VisualAssetPack — versioned asset collection for conditioning (M7.G.A).
AssetRoleBinding — pack↔asset↔role binding (M7.G.A).
compute_appearance_fingerprint — shared helper for appearance hashing.
compute_pack_fingerprint — deterministic pack membership fingerprint (M7.G.A).
"""
from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator, field_validator

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def compute_appearance_fingerprint(appearance: str) -> str:
    """SHA-256 fingerprint of a character appearance string.

    Used by both reference generation (source_appearance_hash on
    ReferenceGenerationRequest, source_fingerprint on ReferenceAsset)
    and stale propagation (comparing against stored fingerprints).

    Same canonical appearance value always produces the same fingerprint.
    """
    return hashlib.sha256(appearance.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ReferenceKind(str, Enum):
    """What production role the reference image serves."""
    CHARACTER_FACE = "character_face"
    CHARACTER_BODY = "character_body"
    STORYBOARD = "storyboard"
    ENVIRONMENT = "environment"


class ReferenceSource(str, Enum):
    """Where the reference asset originated."""
    USER_UPLOAD = "user_upload"
    WIND_COMIC = "wind_comic"
    GENERATED = "generated"


class ReferenceStatus(str, Enum):
    """Review lifecycle status."""
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class ReferenceSourceState(str, Enum):
    """Source freshness — independent from approval status."""
    CURRENT = "current"
    STALE = "stale"


# ---------------------------------------------------------------------------
# ReferenceAsset
# ---------------------------------------------------------------------------

class ReferenceAsset(BaseModel):
    """Source-neutral managed image reference asset.

    Ownership invariants by kind:
    CHARACTER_FACE/BODY → character_id required, shot_id None.
    STORYBOARD → shot_id required, character_id None.
    ENVIRONMENT → project-level, both character_id and shot_id None.
    """

    id: str
    project_id: str
    character_id: str | None = None
    shot_id: str | None = None
    location_id: str | None = None  # FK to Location (nullable — Slice 2 migration)
    kind: ReferenceKind
    source: ReferenceSource
    managed_path: str
    content_sha256: str
    source_provenance: str
    source_fingerprint: str | None = None
    status: ReferenceStatus = ReferenceStatus.CANDIDATE
    source_state: ReferenceSourceState = ReferenceSourceState.CURRENT
    pinned: bool = False
    width: int = 0
    height: int = 0
    created_at: str = ""
    updated_at: str = ""

    @field_validator("id", "project_id", "managed_path", "source_provenance")
    @classmethod
    def non_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("content_sha256")
    @classmethod
    def valid_sha256(cls, v):
        if not _SHA256_RE.match(v):
            raise ValueError("content_sha256 must be 64 lowercase hex chars")
        return v

    @field_validator("width", "height")
    @classmethod
    def positive_dimension(cls, v):
        if v < 1:
            raise ValueError("must be >= 1")
        return v

    @model_validator(mode="after")
    def _validate_ownership(self):
        kind = self.kind
        cid = self.character_id
        sid = self.shot_id

        if kind in (ReferenceKind.CHARACTER_FACE, ReferenceKind.CHARACTER_BODY):
            if not cid:
                raise ValueError(f"{kind.value} requires character_id")
            if sid is not None:
                raise ValueError(f"{kind.value} must not have shot_id")
        elif kind == ReferenceKind.STORYBOARD:
            if not sid:
                raise ValueError("storyboard requires shot_id")
            if cid is not None:
                raise ValueError("storyboard must not have character_id")
        elif kind == ReferenceKind.ENVIRONMENT:
            if cid is not None:
                raise ValueError("environment must not have character_id")
            if sid is not None:
                raise ValueError("environment must not have shot_id")

        return self


# ---------------------------------------------------------------------------
# ReferenceGenerationRequest — IMMUTABLE input snapshot
# ---------------------------------------------------------------------------

class ReferenceGenerationRequest(BaseModel):
    """Immutable snapshot of a reference generation request.

    Once inserted, content fields NEVER change.
    Supports CHARACTER_FACE, CHARACTER_BODY, and ENVIRONMENT kinds.
    For ENVIRONMENT, character_id is '__environment__'.
    """

    id: str
    project_id: str
    character_id: str
    requested_kind: ReferenceKind
    source_appearance_hash: str
    prompt: str
    negative_prompt: str = ""
    workflow_definition_id: str
    workflow_definition_version: str
    workflow_template_fingerprint: str
    parameters_snapshot: list[dict] = Field(default_factory=list)
    seed: int = 0
    created_at: str = ""

    @field_validator("id", "project_id", "character_id", "workflow_definition_id",
                     "workflow_definition_version")
    @classmethod
    def non_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("source_appearance_hash")
    @classmethod
    def valid_appearance_hash(cls, v):
        if not v or not v.strip():
            raise ValueError("source_appearance_hash must not be empty")
        return v

    @field_validator("prompt")
    @classmethod
    def non_empty_prompt(cls, v):
        if not v or not v.strip():
            raise ValueError("prompt must not be empty")
        return v

    @field_validator("workflow_template_fingerprint")
    @classmethod
    def valid_fingerprint(cls, v):
        if not _SHA256_RE.match(v):
            raise ValueError("workflow_template_fingerprint must be 64 hex chars")
        return v

    @model_validator(mode="after")
    def _validate_kind(self):
        if self.requested_kind == ReferenceKind.STORYBOARD:
            raise ValueError("STORYBOARD is not valid for generation requests")
        return self


# ---------------------------------------------------------------------------
# ReferenceGenerationExecution — mutable lifecycle
# ---------------------------------------------------------------------------

class ReferenceGenerationExecution(BaseModel):
    """Mutable execution lifecycle for a reference generation request."""

    id: str
    request_id: str
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    comfyui_prompt_id: str | None = None
    output_reference_asset_id: str | None = None
    submitted_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @field_validator("id", "request_id")
    @classmethod
    def non_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v

    @model_validator(mode="after")
    def _validate_state(self):
        if self.status == "succeeded":
            if not self.output_reference_asset_id:
                raise ValueError("succeeded execution requires output_reference_asset_id")
            if not self.completed_at:
                raise ValueError("succeeded execution requires completed_at")
            if self.error is not None:
                raise ValueError("succeeded execution must not have error")
        elif self.status == "failed":
            if not self.error:
                raise ValueError("failed execution requires error")
            if not self.completed_at:
                raise ValueError("failed execution requires completed_at")
            if self.output_reference_asset_id is not None:
                raise ValueError("failed execution must not have output_reference_asset_id")
        return self


# ---------------------------------------------------------------------------
# AssetRole — semantic conditioning role (M7.G.A)
# ---------------------------------------------------------------------------

class AssetRole(str, Enum):
    """Semantic production purpose for visual assets used in conditioning.

    These roles are provider-neutral — no H3, LTX, Wan, SCAIL, Kling,
    Seedance, ComfyUI, or workflow-specific concepts.
    """
    CHARACTER_FACE_CLOSEUP = "character_face_closeup"
    CHARACTER_BODY_FRONT = "character_body_front"
    CHARACTER_BODY_SIDE = "character_body_side"
    CHARACTER_BODY_BACK = "character_body_back"
    CHARACTER_TURNAROUND = "character_turnaround"
    CHARACTER_WARDROBE = "character_wardrobe"
    ENVIRONMENT_MASTER = "environment_master"
    ENVIRONMENT_VIEW = "environment_view"
    ENVIRONMENT_PANORAMA_360 = "environment_panorama_360"
    ENVIRONMENT_DEPTH = "environment_depth"
    ENVIRONMENT_LAYOUT = "environment_layout"
    PROP_REFERENCE = "prop_reference"
    PROP_TURNAROUND = "prop_turnaround"
    STYLE_REFERENCE = "style_reference"
    CONTINUITY_FRAME = "continuity_frame"
    MOTION_REFERENCE = "motion_reference"
    CONTROL_VIDEO = "control_video"
    AUDIO_REFERENCE = "audio_reference"


# ---------------------------------------------------------------------------
# VisualAssetPack — versioned asset collection (M7.G.A)
# ---------------------------------------------------------------------------

class VisualAssetPack(BaseModel):
    """Project-level versioned collection of visual assets for conditioning.

    Groups existing ReferenceAssets via AssetRoleBindings.
    Contains no provider/model/workflow-specific fields.
    """

    id: str
    project_id: str
    name: str
    description: str = ""
    version: int = 1
    fingerprint: str
    created_at: str = ""
    updated_at: str = ""

    @field_validator("id", "project_id", "name")
    @classmethod
    def non_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("fingerprint")
    @classmethod
    def valid_fingerprint(cls, v):
        if not _SHA256_RE.match(v):
            raise ValueError("fingerprint must be 64 lowercase hex chars")
        return v


# ---------------------------------------------------------------------------
# AssetRoleBinding — pack↔asset↔role binding (M7.G.A)
# ---------------------------------------------------------------------------

class AssetRoleBinding(BaseModel):
    """Normalized binding between a VisualAssetPack, a ReferenceAsset, and a role.

    Contains no provider/model/workflow-specific fields.
    """

    id: str
    pack_id: str
    reference_asset_id: str
    role: AssetRole
    order_index: int = 0
    created_at: str = ""

    @field_validator("id", "pack_id", "reference_asset_id")
    @classmethod
    def non_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("order_index")
    @classmethod
    def non_negative_order(cls, v):
        if v < 0:
            raise ValueError("order_index must be >= 0")
        return v


# ---------------------------------------------------------------------------
# Pack fingerprinting (M7.G.A)
# ---------------------------------------------------------------------------

def compute_pack_fingerprint(
    pack_id: str,
    bindings: list[tuple[str, str, str, int]],
) -> str:
    """Deterministic SHA-256 fingerprint of a pack's membership.

    Args:
        pack_id: Pack stable identity.
        bindings: List of (reference_asset_id, role_value, content_sha256, order_index).
                  Sorted internally for determinism — input order does not matter.

    The fingerprint depends on:
    - pack identity
    - each binding's (role, order_index, reference_asset_id, content_sha256)

    It does NOT depend on timestamps, absolute paths, or provider fields.
    """
    sorted_bindings = sorted(bindings, key=lambda b: (b[1], b[3], b[0]))
    parts = [pack_id]
    for asset_id, role, content_sha, order_idx in sorted_bindings:
        parts.append(f"{role}:{order_idx}:{asset_id}:{content_sha}")
    canonical = "\n".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
