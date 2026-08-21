"""ShotPlanner — single-call LLM shot planning replacing beat→coverage chain.

Produces a concise 5-7 shot sequence directly from scene context,
avoiding the multiplicative expansion of separate beat and coverage calls.

Also enriches deficient character definitions (empty appearance, generic
template names) using the project description and pre-production context.
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

    # ------------------------------------------------------------------
    # Location planning (Slice 5)
    # ------------------------------------------------------------------

    def plan_locations(
        self,
        scenes: list[Scene],
        project_description: str,
    ) -> list[dict]:
        """Identify distinct production Locations from scene structure.

        Returns a list of dicts, each with:
        - name: canonical Location name
        - description: physical/set description (2-4 sentences)
        - scene_ids: list of Scene.id values assigned to this Location

        Uses LLM for semantic canonicalization/deduplication. Falls back
        to deterministic string grouping on LLM failure.
        """
        if not scenes:
            return []

        scene_evidence = []
        for s in scenes:
            scene_evidence.append({
                "scene_id": s.id,
                "scene_name": s.name,
                "location_text": s.location or "",
                "description": s.description or "",
            })

        messages = [
            {"role": "system", "content": _LOCATION_PLANNING_SYSTEM},
            {"role": "user", "content": (
                f"Story/Premise:\n{project_description}\n\n"
                f"Scenes:\n{_format_scene_evidence(scene_evidence)}\n\n"
                "Identify the distinct physical production Locations. "
                "Group scenes that take place in the same location. "
                'Return JSON: {"locations": [...]}'
            )},
        ]

        try:
            response = self._llm.chat(messages, expect_json=True)
            parsed = response.parsed or {}
            result, error = _validate_location_plan(parsed, scenes)
            if error:
                repair = build_repair_messages(
                    original_messages=messages,
                    bad_response_content=response.content,
                    error_detail=error,
                )
                repair_resp = self._llm.chat(repair, expect_json=True)
                result, error2 = _validate_location_plan(
                    repair_resp.parsed or {}, scenes,
                )
                if error2:
                    return _fallback_location_plan(scenes)
            return result
        except Exception:
            return _fallback_location_plan(scenes)

    # ------------------------------------------------------------------
    # Environment derivation
    # ------------------------------------------------------------------

    def derive_environment(self, project_description: str) -> str:
        """Extract a stable production-design environment description from the story.

        Converts narrative context into a physical set description, removing
        characters, actions, plot events, and story-specific props.

        Returns a concise environment definition suitable for persistent
        storage and repeated reference generation.
        """
        if not project_description or not project_description.strip():
            return ""

        messages = [
            {"role": "system", "content": _ENVIRONMENT_DERIVATION_SYSTEM},
            {"role": "user", "content": (
                f"Story/Premise:\n{project_description}\n\n"
                "Extract the physical environment/location description. "
                'Return JSON: {"environment_description": "..."}'
            )},
        ]

        response = self._llm.chat(messages, expect_json=True)
        parsed = response.parsed or {}
        desc = parsed.get("environment_description", "")

        if not desc or not desc.strip() or len(desc.strip()) < 20:
            # Repair attempt
            from film_director.enrichment.prompts import build_repair_messages
            repair = build_repair_messages(
                original_messages=messages,
                bad_response_content=response.content,
                error_detail="environment_description missing or too short (need 20+ chars)",
            )
            repair_resp = self._llm.chat(repair, expect_json=True)
            desc = (repair_resp.parsed or {}).get("environment_description", "")
            if not desc or len(desc.strip()) < 20:
                raise EnrichmentError(
                    "Failed to derive environment description",
                    detail="LLM did not produce a usable environment description",
                )

        return desc.strip()

    # ------------------------------------------------------------------
    # Character enrichment
    # ------------------------------------------------------------------

    def enrich_characters(
        self,
        characters: list[CharacterReference],
        project_description: str,
    ) -> list[CharacterReference]:
        """Enrich deficient characters using the project description.

        Returns updated CharacterReference objects for characters that need
        enrichment (empty appearance or generic template names). Characters
        with meaningful existing data are returned unchanged. IDs are preserved.

        Raises EnrichmentError if LLM output is invalid after repair attempt.
        """
        deficient = [c for c in characters if _is_character_deficient(c)]
        if not deficient:
            return []

        if not project_description:
            return []

        messages = _build_character_enrichment_messages(
            deficient, project_description,
        )

        response = self._llm.chat(messages, expect_json=True)
        enriched_map, error = _validate_character_enrichment(
            response.parsed or {}, deficient,
        )

        if error is not None:
            from film_director.enrichment.prompts import build_repair_messages
            repair = build_repair_messages(
                original_messages=messages,
                bad_response_content=response.content,
                error_detail=error,
            )
            repair_resp = self._llm.chat(repair, expect_json=True)
            enriched_map, repair_error = _validate_character_enrichment(
                repair_resp.parsed or {}, deficient,
            )
            if repair_error is not None:
                raise EnrichmentError(
                    "Character enrichment failed after repair",
                    detail=repair_error,
                )

        updated = []
        for char in deficient:
            data = enriched_map.get(char.id)
            if data is None:
                continue
            updated.append(char.model_copy(update={
                "name": data["display_name"],
                "appearance": data["appearance"],
            }))
        return updated


# -----------------------------------------------------------------------
# Environment derivation prompt
# -----------------------------------------------------------------------

_ENVIRONMENT_DERIVATION_SYSTEM = """\
You are a production designer extracting a physical SET/LOCATION description \
from a story premise.

Your job is to describe the PHYSICAL SPACE where the story takes place, \
as a production design reference — NOT the story itself.

INCLUDE:
- Type of space (apartment, office, forest, etc.)
- Size and spatial layout (rooms, connections, sightlines)
- Key architectural features and furniture
- Lighting conditions (practical lights, windows, time of day)
- Color palette and visual atmosphere
- Production design style (modern, period, worn, etc.)
- Materials and textures

EXCLUDE (these belong to shots, NOT environment):
- Character names or descriptions
- Actions, events, or plot points
- Story-specific props (letters, weapons, evidence)
- Emotions or dramatic intent
- Dialogue or sound
- Camera angles or shot descriptions

Return ONLY a JSON object: {"environment_description": "..."}
The description should be 2-4 sentences, concise, visual, in English.
No markdown, no explanation — raw JSON only.
"""

# -----------------------------------------------------------------------
# Character enrichment helpers
# -----------------------------------------------------------------------

# Generic WC template names that provide no production identity
_WC_GENERIC_NAMES = frozenset({
    "主角", "伙伴", "配角", "角色", "对手", "反派", "助手",
    "protagonist", "companion", "antagonist", "sidekick",
})


def _is_character_deficient(char: CharacterReference) -> bool:
    """A character is deficient if it lacks usable production data."""
    has_appearance = bool(char.appearance and char.appearance.strip())
    has_meaningful_name = char.name.strip().casefold() not in _WC_GENERIC_NAMES
    # Deficient if no appearance, regardless of name quality
    if not has_appearance:
        return True
    # Also deficient if name is a generic template AND appearance is very short
    if not has_meaningful_name and len(char.appearance.strip()) < 20:
        return True
    return False


_LOCATION_PLANNING_SYSTEM = """\
You are a production designer identifying distinct physical locations for a film.

Given a story premise and a list of scenes with location evidence, identify \
the distinct production Locations. Each Location is a reusable physical place.

Rules:
- Merge scenes that clearly take place in the SAME physical location.
- Keep scenes in DIFFERENT physical places as separate Locations.
- "the apartment" and "Grandpa's apartment" are the same place.
- "apartment" and "subway" are different places.
- Do NOT merge unrelated places that merely share a word ("office" vs "office lobby").
- For each Location, provide:
  - name: concise production label (e.g., "Grandpa's Apartment", "Subway Platform")
  - description: 2-4 sentences describing the physical set: architecture, spatial \
layout, materials, furniture, persistent visual anchors. Focus on permanent \
physical features, NOT temporary scene state (weather, time, damage).
  - scene_ids: list of scene ID strings that take place at this Location

Return ONLY a JSON object: {"locations": [{"name": "...", "description": "...", "scene_ids": ["..."]}]}
No markdown, no explanation — raw JSON only.
"""


def _format_scene_evidence(evidence: list[dict]) -> str:
    parts = []
    for e in evidence:
        line = f"  - scene_id: {e['scene_id']}"
        if e["scene_name"]:
            line += f", name: {e['scene_name']}"
        if e["location_text"]:
            line += f", location: {e['location_text']}"
        if e["description"]:
            line += f", description: {e['description'][:150]}"
        parts.append(line)
    return "\n".join(parts)


def _validate_location_plan(
    parsed: dict,
    scenes: list[Scene],
) -> tuple[list[dict], str | None]:
    """Validate LLM location plan output.

    Returns (list of validated location dicts, error_or_None).
    Each validated dict has: name, description, scene_ids.
    """
    if "locations" not in parsed:
        return [], "Response missing 'locations' key"
    raw = parsed["locations"]
    if not isinstance(raw, list) or not raw:
        return [], "'locations' must be a non-empty list"

    valid_scene_ids = {s.id for s in scenes}
    assigned_scene_ids: set[str] = set()
    result = []

    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return [], f"Location at index {i} is not an object"
        name = (item.get("name") or "").strip()
        desc = (item.get("description") or "").strip()
        scene_ids = item.get("scene_ids", [])
        if not name:
            return [], f"Location at index {i} has empty name"
        if not isinstance(scene_ids, list):
            return [], f"Location at index {i}: scene_ids must be a list"

        # Validate scene_ids
        validated_ids = []
        for sid in scene_ids:
            if not isinstance(sid, str):
                continue
            if sid not in valid_scene_ids:
                continue  # skip invalid IDs silently (may be LLM hallucination)
            if sid in assigned_scene_ids:
                continue  # skip duplicate assignment
            validated_ids.append(sid)
            assigned_scene_ids.add(sid)

        if validated_ids:
            result.append({
                "name": name,
                "description": desc,
                "scene_ids": validated_ids,
            })

    if not result:
        return [], "No valid locations with assigned scenes"

    # Assign any unassigned scenes to nearest match or create catch-all
    unassigned = [s for s in scenes if s.id not in assigned_scene_ids]
    if unassigned and result:
        # Append unassigned to the first location as fallback
        result[0]["scene_ids"].extend(s.id for s in unassigned)

    return result, None


def _fallback_location_plan(scenes: list[Scene]) -> list[dict]:
    """Deterministic fallback when LLM fails.

    Groups scenes by normalized location string. Scenes with identical
    normalized strings share one Location. Empty/missing location strings
    are grouped together as "Main Location".
    """
    groups: dict[str, list[str]] = {}
    for s in scenes:
        key = _normalize_location_string(s.location)
        groups.setdefault(key, []).append(s.id)

    result = []
    for key, scene_ids in groups.items():
        name = key if key else "Main Location"
        result.append({
            "name": name,
            "description": "",  # no LLM available for description
            "scene_ids": scene_ids,
        })
    return result


def _normalize_location_string(loc: str) -> str:
    """Normalize a location string for grouping."""
    import re
    s = loc.strip().lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_CHARACTER_ENRICHMENT_SYSTEM = """\
You are a production designer creating character visual descriptions for a film.

Given a story premise and a list of characters that need visual descriptions, \
provide a concise production-ready visual identity for each character.

For each character, provide:
- display_name: A concise production identity in English. \
Use a descriptive role label like "The Man" or "The Woman" unless the story \
provides an actual name. Do NOT invent personal names.
- appearance: A detailed visual description for reference image generation. \
Include: approximate age range, build, hair, skin tone, facial features, \
clothing, and any distinguishing visual traits. Keep it concise (2-3 sentences). \
Write in English.

Return ONLY a JSON object: {"characters": [{"id": "...", "display_name": "...", "appearance": "..."}]}
No markdown, no explanation — raw JSON only.
"""


def _build_character_enrichment_messages(
    characters: list[CharacterReference],
    project_description: str,
) -> list[dict]:
    parts = [f"Story/Premise:\n{project_description}\n"]
    parts.append("Characters needing visual descriptions:")
    for c in characters:
        parts.append(f"  - id: {c.id}")
        parts.append(f"    current_name: {c.name}")
        if c.description:
            parts.append(f"    wc_description: {c.description[:200]}")
    parts.append(
        "\nProvide a display_name and appearance for each character. "
        'Return JSON: {"characters": [...]}'
    )
    return [
        {"role": "system", "content": _CHARACTER_ENRICHMENT_SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
    ]


def _validate_character_enrichment(
    parsed: dict,
    expected_chars: list[CharacterReference],
) -> tuple[dict[str, dict], str | None]:
    """Validate character enrichment response.

    Returns (id→{display_name, appearance} map, error_or_None).
    """
    if "characters" not in parsed:
        return {}, "Response missing 'characters' key"
    raw = parsed["characters"]
    if not isinstance(raw, list):
        return {}, "'characters' must be a list"
    if len(raw) == 0:
        return {}, "'characters' list must be non-empty"

    expected_ids = {c.id for c in expected_chars}
    result = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return {}, f"Character at index {i} is not an object"
        cid = item.get("id", "")
        name = item.get("display_name", "")
        appearance = item.get("appearance", "")
        if not name or not name.strip():
            return {}, f"Character at index {i} has empty display_name"
        if not appearance or not appearance.strip():
            return {}, f"Character at index {i} has empty appearance"
        if len(appearance.strip()) < 15:
            return {}, f"Character at index {i} appearance too short"
        # Map by id if provided, otherwise by index
        if cid and cid in expected_ids:
            result[cid] = {"display_name": name.strip(), "appearance": appearance.strip()}
        elif i < len(expected_chars):
            result[expected_chars[i].id] = {"display_name": name.strip(), "appearance": appearance.strip()}

    return result, None
