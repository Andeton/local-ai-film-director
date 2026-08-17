"""QueueJob model for persistent generation queue (M6).

Tracks the lifecycle of a single take-generation job from enqueue
through execution to completion. The QueueJob owns the pre-execution
state; a Take is only created after successful generation.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

# Safe seed domain: non-negative signed 63-bit (SQLite INTEGER compatible)
_SAFE_SEED_MAX = (1 << 63) - 1


class QueueJob(BaseModel):
    """Persistent generation queue entry.

    Status state machine:
        pending → claimed → succeeded
                          → failed
               → cancelled (user cancels before claim)

    generation_request_id and take_id are NULL until execution creates them.
    """

    id: str
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

    @field_validator("id", "shot_id", "project_id")
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
