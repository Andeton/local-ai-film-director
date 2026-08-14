"""CoveragePlanner — LLM-driven coverage decisions for a beat (M2.D)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from film_director.errors import EnrichmentError
from film_director.llm.provider import LLMProvider
from film_director.models.canonical import Beat, Scene
from film_director.enrichment.prompts import (
    build_coverage_messages,
    build_repair_messages,
)


# ---------------------------------------------------------------------------
# Transient validation model — NOT in canonical.py
# ---------------------------------------------------------------------------

class CoverageDecision(BaseModel):
    shot_type: str
    shot_size: Literal[
        "extreme_wide", "wide", "medium_wide", "medium",
        "medium_close", "close_up", "extreme_close",
    ]
    angle: str = ""
    movement: str = ""
    purpose: str
    duration_sec: float

    @field_validator("shot_type", "purpose")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("duration_sec")
    @classmethod
    def positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be positive")
        return v


# ---------------------------------------------------------------------------
# Domain validation
# ---------------------------------------------------------------------------

def _validate_domain(parsed: dict) -> tuple[list[CoverageDecision], str | None]:
    """Validate parsed dict against coverage domain contract.

    Returns (decisions, None) on success, or ([], error_message) on failure.
    """
    if "coverage" not in parsed:
        return [], "Response missing 'coverage' key"

    raw_coverage = parsed["coverage"]
    if not isinstance(raw_coverage, list):
        return [], f"'coverage' must be a list, got {type(raw_coverage).__name__}"

    if len(raw_coverage) == 0:
        return [], "'coverage' list must be non-empty"

    decisions: list[CoverageDecision] = []
    for i, item in enumerate(raw_coverage):
        if not isinstance(item, dict):
            return [], f"Coverage item at index {i} is not an object"
        try:
            decisions.append(CoverageDecision(**item))
        except Exception as exc:
            return [], f"Coverage item at index {i} failed validation: {exc}"

    return decisions, None


# ---------------------------------------------------------------------------
# CoveragePlanner
# ---------------------------------------------------------------------------

class CoveragePlanner:
    """Plans shot coverage for a beat using an LLM.

    Contract:
    - Always calls llm.chat(..., expect_json=True)
    - Reads response.parsed["coverage"]
    - If domain invalid -> one repair prompt -> EnrichmentError on second failure
    - LLMStructuredOutputError / LLMUnavailableError propagate unchanged
    - Max 2 LLM calls
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def plan_coverage(self, beat: Beat, scene: Scene) -> list[CoverageDecision]:
        """Plan coverage shots for *beat* in context of *scene*. Max 2 LLM calls."""
        messages = build_coverage_messages(
            beat_dramatic_action=beat.dramatic_action,
            beat_characters=beat.characters,
            beat_change=beat.change,
            scene_name=scene.name,
            scene_location=scene.location,
            scene_description=scene.description,
        )

        # --- Initial call (provider errors propagate) ---
        response = self._llm.chat(messages, expect_json=True)
        decisions, error = _validate_domain(response.parsed or {})

        if error is None:
            return decisions

        # --- Domain repair: one attempt ---
        repair_messages = build_repair_messages(
            original_messages=messages,
            bad_response_content=response.content,
            error_detail=error,
        )
        repair_response = self._llm.chat(repair_messages, expect_json=True)
        repair_decisions, repair_error = _validate_domain(repair_response.parsed or {})

        if repair_error is not None:
            raise EnrichmentError(
                "Coverage domain validation failed after repair attempt",
                detail=repair_error,
            )

        return repair_decisions
