"""ContinuityState model — per-shot continuity chain membership and upstream validity.

M7.A: model definition and canonical fingerprinting.
Continuity validity (unresolved/current/outdated) is separate from Take.status.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, field_validator

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContinuityState(BaseModel):
    """Per-shot continuity chain membership and upstream validity.

    state tracks whether the shot's upstream input is valid:
      - unresolved: predecessor has no approved Take yet
      - current: upstream Take approved, frame captured and valid
      - outdated: upstream Take changed or upstream became outdated

    This is orthogonal to Take.status (which tracks review lifecycle).
    """

    id: str
    shot_id: str
    scene_id: str
    predecessor_shot_id: str | None = None
    upstream_take_id: str | None = None
    upstream_take_number: int | None = None
    upstream_last_frame_path: str | None = None
    upstream_last_frame_sha256: str | None = None
    state: Literal["unresolved", "current", "outdated"] = "unresolved"
    continuity_revision: int = 0
    invalidation_reason: str | None = None
    invalidation_source_shot_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    invalidated_at: str | None = None

    @field_validator("id", "shot_id", "scene_id")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("upstream_last_frame_sha256")
    @classmethod
    def valid_sha256_or_none(cls, v: str | None) -> str | None:
        if v is not None and not _SHA256_RE.match(v):
            raise ValueError("must be 64 lowercase hex chars")
        return v

    @field_validator("continuity_revision")
    @classmethod
    def non_negative_revision(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be >= 0")
        return v


def compute_continuity_fingerprint(
    upstream_shot_id: str,
    upstream_take_id: str,
    upstream_take_number: int,
    upstream_last_frame_sha256: str,
    continuity_revision: int,
) -> str:
    """Compute a deterministic SHA-256 fingerprint of continuity provenance.

    Canonical JSON: sorted keys, compact separators, UTF-8.
    Same inputs always produce the same fingerprint.
    Any provenance-relevant input change produces a different fingerprint.
    """
    payload = {
        "continuity_revision": continuity_revision,
        "upstream_last_frame_sha256": upstream_last_frame_sha256,
        "upstream_shot_id": upstream_shot_id,
        "upstream_take_id": upstream_take_id,
        "upstream_take_number": upstream_take_number,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
