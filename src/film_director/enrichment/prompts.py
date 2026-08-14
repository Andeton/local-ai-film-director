"""Prompt templates for beat enrichment."""
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


def build_repair_messages(
    original_messages: list[dict],
    bad_response_content: str,
    error_detail: str,
) -> list[dict]:
    """Build repair prompt messages after a domain validation failure."""
    repair_instruction = (
        f"Your previous response had an issue: {error_detail}\n\n"
        "Please return a valid JSON object with a 'beats' key containing a non-empty "
        "list of beat objects. Each beat MUST have a non-empty 'dramatic_action' string. "
        "Return ONLY the JSON object, no other text."
    )
    return [
        *original_messages,
        {"role": "assistant", "content": bad_response_content},
        {"role": "user", "content": repair_instruction},
    ]
