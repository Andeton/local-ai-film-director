"""Canonical reference management models (M5).

ReferenceAsset — source-neutral managed image reference.
ReferenceGenerationRequest — immutable ComfyUI generation input snapshot.
ReferenceGenerationExecution — mutable generation lifecycle.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator, field_validator

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ReferenceKind(str, Enum):
    """What production role the reference image serves."""
    CHARACTER_FACE = "character_face"
    CHARACTER_BODY = "character_body"
    STORYBOARD = "storyboard"


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

    Ownership invariant: exactly one of (character_id, shot_id) is non-None.
    CHARACTER_FACE/BODY → character_id required, shot_id None.
    STORYBOARD → shot_id required, character_id None.
    """

    id: str
    project_id: str
    character_id: str | None = None
    shot_id: str | None = None
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

        return self


# ---------------------------------------------------------------------------
# ReferenceGenerationRequest — IMMUTABLE input snapshot
# ---------------------------------------------------------------------------

class ReferenceGenerationRequest(BaseModel):
    """Immutable snapshot of a character reference generation request.

    Once inserted, content fields NEVER change.
    Only CHARACTER_FACE and CHARACTER_BODY are valid requested_kind values.
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
