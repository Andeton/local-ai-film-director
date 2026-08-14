"""Prompt templates for beat enrichment and coverage planning."""
from __future__ import annotations

import json

BEAT_ENRICHMENT_SYSTEM = """\
You are a film director analyzing a scene to decompose it into dramatic beats.

A beat is a single unit of dramatic action — a moment where something changes \
(emotionally, physically, or relationally). Each beat must have:
- dramatic_action: what physically or verbally happens (non-empty, specific)
- character_intention: what the active character wants in this beat
- change: what shifts as a result of this beat
- characters: list of character names present

Return ONLY a JSON object with a "beats" key containing a list of beat objects.
No markdown, no explanation — raw JSON only.

Example:
{
  "beats": [
    {
      "dramatic_action": "Hero slams door and demands answers",
      "character_intention": "Force a confession",
      "change": "Tension escalates; villain retreats",
      "characters": ["Hero", "Villain"]
    }
  ]
}
"""


def build_beat_enrichment_messages(
    scene_name: str,
    scene_location: str,
    scene_description: str,
    script_context: dict | None = None,
) -> list[dict]:
    """Build the message list for an initial beat enrichment call."""
    context_section = ""
    if script_context:
        context_section = (
            f"\n\nAdditional script context:\n{json.dumps(script_context, indent=2)}"
        )

    user_content = (
        f"Scene: {scene_name}\n"
        f"Location: {scene_location}\n"
        f"Description: {scene_description}"
        f"{context_section}\n\n"
        "Decompose this scene into dramatic beats. Return a JSON object with a "
        '"beats" key containing a list of beat objects.'
    )

    return [
        {"role": "system", "content": BEAT_ENRICHMENT_SYSTEM},
        {"role": "user", "content": user_content},
    ]


COVERAGE_SYSTEM = """\
You are a film director planning shot coverage for a dramatic beat.

For each shot in your coverage plan, provide:
- shot_type: descriptive label (e.g. "establishing", "reaction", "insert")
- shot_size: one of "extreme_wide", "wide", "medium_wide", "medium", \
"medium_close", "close_up", "extreme_close"
- angle: camera angle (e.g. "high", "low", "eye_level", "dutch") or ""
- movement: camera movement (e.g. "pan_left", "dolly_in", "static") or ""
- purpose: dramatic purpose of this shot (non-empty)
- duration_sec: estimated duration in seconds (positive number)

Return ONLY a JSON object with a "coverage" key containing a list of shot objects.
No markdown, no explanation — raw JSON only.

Example:
{
  "coverage": [
    {
      "shot_type": "establishing",
      "shot_size": "wide",
      "angle": "high",
      "movement": "slow_pan",
      "purpose": "Set the scene and establish location",
      "duration_sec": 4.0
    }
  ]
}
"""


def build_coverage_messages(
    beat_dramatic_action: str,
    beat_characters: list[str],
    beat_change: str,
    scene_name: str,
    scene_location: str,
    scene_description: str,
) -> list[dict]:
    """Build message list for a coverage planning LLM call."""
    user_content = (
        f"Scene: {scene_name}\n"
        f"Location: {scene_location}\n"
        f"Description: {scene_description}\n\n"
        f"Beat dramatic action: {beat_dramatic_action}\n"
        f"Characters: {', '.join(beat_characters)}\n"
        f"Change: {beat_change}\n\n"
        "Plan the shot coverage for this beat. Return a JSON object with a "
        '"coverage" key containing a list of shot objects.'
    )
    return [
        {"role": "system", "content": COVERAGE_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def build_repair_messages(
    original_messages: list[dict],
    bad_response_content: str,
    error_detail: str,
) -> list[dict]:
    """Build repair prompt messages after a domain validation failure.

    Works for both beat enrichment and coverage planning — the error_detail
    provides the specific validation context.
    """
    repair_instruction = (
        f"Your previous response had an issue: {error_detail}\n\n"
        "Please fix the issue and return a valid JSON object matching the "
        "originally requested schema. "
        "Return ONLY the JSON object, no other text."
    )
    return [
        *original_messages,
        {"role": "assistant", "content": bad_response_content},
        {"role": "user", "content": repair_instruction},
    ]
