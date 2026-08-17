"""GenerationRequest and Take Pydantic models for generation pipeline.

M3: initial single-take pipeline.
M6: extended Take status (approved/rejected) + is_favorite preference flag.
M7: continuity_snapshot on GenerationRequest (nullable, backward-compatible).
"""
from typing import Literal
import re

from pydantic import BaseModel, Field, field_validator

_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')


class GenerationRequest(BaseModel):
    id: str
    shot_id: str
    shot_version: int
    generation_plan_id: str
    generation_plan_version: int
    prompt_artifact_id: str
    prompt_artifact_version: int
    workflow_definition_id: str
    workflow_definition_version: str
    workflow_template_fingerprint: str
    take_number: int
    parameters_snapshot: list[dict] = Field(default_factory=list)
    reference_snapshot: list[dict] = Field(default_factory=list)
    seed: int
    continuity_snapshot: dict | None = None  # M7: immutable upstream provenance, None for non-continuity shots
    comfyui_prompt_id: str | None = None
    status: Literal["pending", "queued", "running", "succeeded", "failed", "cancelled"] = "pending"
    submitted_at: str = ""
    completed_at: str = ""
    error: str | None = None

    @field_validator("shot_version", "generation_plan_version", "prompt_artifact_version", "take_number")
    @classmethod
    def positive(cls, v):
        if v < 1:
            raise ValueError("must be >= 1")
        return v

    @field_validator("workflow_template_fingerprint")
    @classmethod
    def valid_sha256(cls, v):
        if not _SHA256_RE.match(v):
            raise ValueError("must be 64 hex chars")
        return v

    @field_validator("id", "shot_id", "generation_plan_id", "prompt_artifact_id", "workflow_definition_id", "workflow_definition_version")
    @classmethod
    def non_empty(cls, v):
        if not v.strip():
            raise ValueError("must not be empty")
        return v


class Take(BaseModel):
    """Generated video take.

    M6 status evolution:
      Execution: pending → generating → succeeded | failed
      Review:    succeeded → approved | rejected
    is_favorite is orthogonal to status (an approved take can also be favorite).
    """

    id: str
    shot_id: str
    generation_request_id: str
    seed: int
    video_path: str
    audio_path: str | None = None
    last_frame_path: str | None = None
    status: Literal[
        "pending", "generating", "succeeded", "failed",
        "approved", "rejected",
    ] = "pending"
    is_favorite: bool = False
    created_at: str = ""

    @field_validator("id", "shot_id", "generation_request_id", "video_path")
    @classmethod
    def non_empty(cls, v):
        if not v.strip():
            raise ValueError("must not be empty")
        return v
