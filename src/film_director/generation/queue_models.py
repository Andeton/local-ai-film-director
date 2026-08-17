"""Queue models for persistent generation queue (M6).

QueueBatch — persistent idempotency record for an enqueue request.
QueueJob — individual take-generation job within a batch.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, field_validator

# Safe seed domain: non-negative signed 63-bit (SQLite INTEGER compatible)
_SAFE_SEED_MAX = (1 << 63) - 1


def compute_request_fingerprint(
    scope_type: str,
    scope_id: str,
    takes_count: int,
    base_seed: int,
    priority: int = 0,
) -> str:
    """SHA-256 fingerprint of canonical enqueue request inputs.

    Deterministic: same inputs always produce the same fingerprint.
    Used to detect payload conflicts on idempotency-key reuse.
    """
    payload = json.dumps(
        {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "takes_count": takes_count,
            "base_seed": base_seed,
            "priority": priority,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class QueueBatch(BaseModel):
    """Persistent idempotency record for an enqueue request.

    UNIQUE(project_id, idempotency_key) ensures one batch per key per project.
    Request fingerprint detects payload conflicts on key reuse.
    """

    id: str
    project_id: str
    scope_type: Literal["shot", "scene"]
    scope_id: str
    idempotency_key: str
    request_fingerprint: str
    takes_count: int
    base_seed: int
    priority: int = 0
    created_at: str = ""

    @field_validator("id", "project_id", "scope_id", "idempotency_key", "request_fingerprint")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("base_seed")
    @classmethod
    def valid_seed(cls, v: int) -> int:
        if v < 0 or v > _SAFE_SEED_MAX:
            raise ValueError(f"base_seed must be in [0, {_SAFE_SEED_MAX}]")
        return v


class QueueJob(BaseModel):
    """Persistent generation queue entry within a batch.

    Status state machine:
        pending → claimed → succeeded
                          → failed
               → cancelled (user cancels before claim)

    generation_request_id and take_id are NULL until execution creates them.
    """

    id: str
    batch_id: str
    shot_id: str
    take_number: int                    # 1-based
    project_id: str
    base_seed: int                      # enqueue-time base seed (immutable)
    seed: int                           # derived seed for this take (immutable)
    status: Literal[
        "pending", "claimed", "succeeded", "failed", "cancelled",
    ] = "pending"
    generation_request_id: str | None = None
    take_id: str | None = None
    priority: int = 0
    attempt_count: int = 0
    max_attempts: int = 1
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    claimed_at: str | None = None
    completed_at: str | None = None

    @field_validator("id", "batch_id", "shot_id", "project_id")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("take_number")
    @classmethod
    def positive_take_number(cls, v: int) -> int:
        if v < 1:
            raise ValueError("take_number must be >= 1 (1-based)")
        return v

    @field_validator("base_seed", "seed")
    @classmethod
    def valid_seed(cls, v: int) -> int:
        if v < 0 or v > _SAFE_SEED_MAX:
            raise ValueError(f"seed must be in [0, {_SAFE_SEED_MAX}]")
        return v

    @field_validator("attempt_count", "max_attempts")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be >= 0")
        return v
