"""ShotPlanner — single-call LLM shot planning replacing beat→coverage chain.

Produces a concise 5-7 shot sequence directly from scene context,
avoiding the multiplicative expansion of separate beat and coverage calls.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, field_validator

from film_director.errors import EnrichmentError
from film_director.llm.provider import LLMProvider
from film_director.models.canonical import (
    Beat,
    CameraIntent,
    CharacterReference,
    Scene,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.enrichment.prompts import (
    build_shot_plan_messages,
    build_repair_messages,
)


class ShotCandidate(BaseModel):
    """Validated shot from LLM output."""
    action: str
    dramatic_purpose: str
    shot_size: Literal[
        "extreme_wide", "wide", "medium_wide", "medium",
        "medium_close", "close_up", "extreme_close",
    ]
    angle: str = ""
    movement: str = ""
    characters: list[str] = []
    duration_sec: float = 5.0

    @field_validator("action", "dramatic_purpose")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("duration_sec")
    @classmethod
    def positive_duration(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be positive")
        return min(v, 15.0)  # cap at 15s


def _validate_shot_plan(parsed: dict) -> tuple[list[ShotCandidate], str | None]:
    """Validate parsed dict against shot plan contract."""
    if "shots" not in parsed:
        return [], "Response missing 'shots' key"
    raw = parsed["shots"]
    if not isinstance(raw, list):
        return [], f"'shots' must be a list, got {type(raw).__name__}"
    if len(raw) == 0:
        return [], "'shots' list must be non-empty"
    candidates = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return [], f"Shot at index {i} is not an object"
        try:
            candidates.append(ShotCandidate(**item))
        except Exception as exc:
            return [], f"Shot at index {i} failed validation: {exc}"
    return candidates, None


class ShotPlanner:
    """Plans a concise shot sequence for a scene in a single LLM call.

    Contract:
    - One LLM call + one repair attempt (max 2 calls)
    - Returns (list[Beat], list[ShotSpecificationV1]) ready for persistence
    - LLMStructuredOutputError / LLMUnavailableError propagate unchanged
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def plan_scene(
        self,
        scene: Scene,
        characters: list[CharacterReference],
        storyboard_notes: list[str],
        script_context: dict | None = None,
        project_description: str = "",
    ) -> tuple[list[Beat], list[ShotSpecificationV1]]:
        """Plan shots for a scene. Returns (beats, shots).

        Creates one beat per shot (1:1 mapping) so the existing
        beat→shot→plan pipeline remains consistent.
        """
        char_dicts = [
            {"name": c.name, "appearance": c.appearance}
            for c in characters
        ]

        messages = build_shot_plan_messages(
            scene_name=scene.name,
            scene_location=scene.location,
            scene_description=scene.description,
            project_description=project_description,
            characters=char_dicts,
            storyboard_notes=storyboard_notes,
            script_context=script_context,
        )

        response = self._llm.chat(messages, expect_json=True)
        candidates, error = _validate_shot_plan(response.parsed or {})

        if error is not None:
            repair_messages = build_repair_messages(
                original_messages=messages,
                bad_response_content=response.content,
                error_detail=error,
            )
            repair_response = self._llm.chat(repair_messages, expect_json=True)
            candidates, repair_error = _validate_shot_plan(repair_response.parsed or {})
            if repair_error is not None:
                raise EnrichmentError(
                    "Shot plan validation failed after repair attempt",
                    detail=repair_error,
                )

        return self._build_from_candidates(candidates, scene, characters)

    def _build_from_candidates(
        self,
        candidates: list[ShotCandidate],
        scene: Scene,
        characters: list[CharacterReference],
    ) -> tuple[list[Beat], list[ShotSpecificationV1]]:
        now = datetime.now(timezone.utc).isoformat()
        beats = []
        shots = []

        for i, cand in enumerate(candidates):
            beat_id = f"beat{uuid.uuid4().hex[:12]}"
            beat = Beat(
                id=beat_id,
                scene_id=scene.id,
                dramatic_action=cand.action,
                character_intention=cand.dramatic_purpose,
                change="",
                characters=list(cand.characters),
                order_index=i,
                status="draft",
                source="llm",
                version=1,
                created_at=now,
                updated_at=now,
            )
            beats.append(beat)

            subjects = self._resolve_characters(cand.characters, characters)

            shot_id = f"shot{uuid.uuid4().hex[:12]}"
            shot = ShotSpecificationV1(
                id=shot_id,
                beat_id=beat_id,
                dramatic_purpose=cand.dramatic_purpose,
                subjects=subjects,
                action=cand.action,
                environment={
                    "location": scene.location,
                    "description": scene.description,
                },
                camera=CameraIntent(
                    shot_size=cand.shot_size,
                    angle=cand.angle,
                    movement=cand.movement,
                ),
                duration_sec=cand.duration_sec,
                order_index=i,
                status="draft",
                source="generated",
                version=1,
                created_at=now,
                updated_at=now,
            )
            shots.append(shot)

        return beats, shots

    @staticmethod
    def _resolve_characters(
        names: list[str],
        char_refs: list[CharacterReference],
    ) -> list[ShotSubject]:
        ref_lookup: dict[str, CharacterReference] = {}
        for cr in char_refs:
            key = cr.name.strip().casefold()
            if key not in ref_lookup:
                ref_lookup[key] = cr

        seen: set[str] = set()
        subjects = []
        for name in names:
            key = name.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            cr = ref_lookup.get(key)
            if cr:
                subjects.append(ShotSubject(
                    character_id=cr.id,
                    name=cr.name,
                    ref_images=list(cr.turnaround_paths),
                ))
        return subjects
