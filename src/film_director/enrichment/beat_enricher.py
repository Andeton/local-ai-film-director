"""BeatEnricher — LLM-driven scene decomposition into Beats (M2.C)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, field_validator

from film_director.errors import EnrichmentError
from film_director.llm.provider import LLMProvider
from film_director.models.canonical import Beat, Scene
from film_director.enrichment.prompts import (
    build_beat_enrichment_messages,
    build_repair_messages,
)


# ---------------------------------------------------------------------------
# Internal validation model — NOT exported to canonical.py
# ---------------------------------------------------------------------------

class BeatCandidate(BaseModel):
    dramatic_action: str
    character_intention: str = ""
    change: str = ""
    characters: list[str] = []

    @field_validator("dramatic_action")
    @classmethod
    def dramatic_action_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("dramatic_action must not be empty")
        return v


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_domain(parsed: dict) -> tuple[list[BeatCandidate], str | None]:
    """Validate parsed dict against domain contract.

    Returns (candidates, None) on success, or ([], error_message) on failure.
    Contract:
      - 'beats' key must be present
      - value must be a non-empty list
      - each item must pass BeatCandidate validation
    """
    if "beats" not in parsed:
        return [], "Response missing 'beats' key"

    raw_beats = parsed["beats"]
    if not isinstance(raw_beats, list):
        return [], f"'beats' must be a list, got {type(raw_beats).__name__}"

    if len(raw_beats) == 0:
        return [], "'beats' list must be non-empty"

    candidates: list[BeatCandidate] = []
    for i, item in enumerate(raw_beats):
        if not isinstance(item, dict):
            return [], f"Beat at index {i} is not an object"
        try:
            candidates.append(BeatCandidate(**item))
        except Exception as exc:
            return [], f"Beat at index {i} failed validation: {exc}"

    return candidates, None


def _build_beats(candidates: list[BeatCandidate], scene_id: str) -> list[Beat]:
    """Convert validated BeatCandidates into Beat model instances."""
    now = datetime.now(timezone.utc).isoformat()
    beats: list[Beat] = []
    for i, candidate in enumerate(candidates):
        beat_id = f"beat{uuid.uuid4().hex[:12]}"
        beats.append(
            Beat(
                id=beat_id,
                scene_id=scene_id,
                dramatic_action=candidate.dramatic_action,
                character_intention=candidate.character_intention,
                change=candidate.change,
                characters=list(candidate.characters),
                order_index=i,
                status="draft",
                source="llm",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    return beats


# ---------------------------------------------------------------------------
# BeatEnricher
# ---------------------------------------------------------------------------

class BeatEnricher:
    """Decomposes a Scene into Beats using an LLM.

    Contract:
    - Always calls llm.chat(..., expect_json=True)
    - Reads response.parsed["beats"]
    - If domain invalid → one repair prompt → EnrichmentError on second failure
    - LLMStructuredOutputError / LLMUnavailableError propagate unchanged
    - No persistence — returns list[Beat] only
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def enrich_scene(
        self,
        scene: Scene,
        script_context: dict | None = None,
    ) -> list[Beat]:
        """Decompose *scene* into beats using LLM. Max 2 LLM calls."""
        messages = build_beat_enrichment_messages(
            scene_name=scene.name,
            scene_location=scene.location,
            scene_description=scene.description,
            script_context=script_context,
        )

        # --- Initial call (provider errors propagate) ---
        response = self._llm.chat(messages, expect_json=True)
        candidates, error = _validate_domain(response.parsed or {})

        if error is None:
            return _build_beats(candidates, scene.id)

        # --- Domain repair: one attempt ---
        repair_messages = build_repair_messages(
            original_messages=messages,
            bad_response_content=response.content,
            error_detail=error,
        )
        repair_response = self._llm.chat(repair_messages, expect_json=True)
        repair_candidates, repair_error = _validate_domain(repair_response.parsed or {})

        if repair_error is not None:
            raise EnrichmentError(
                "Beat domain validation failed after repair attempt",
                detail=repair_error,
            )

        return _build_beats(repair_candidates, scene.id)
