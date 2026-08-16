"""Source-neutral transport DTOs for upstream pre-production facts.

ShotSourceFacts carries normalized facts from any upstream system (Wind
Comic, future alternatives) into the enrichment pipeline. It is a
TRANSPORT DTO — consumed during enrichment, never persisted as a separate
entity. The canonical fields it populates ARE persisted on ShotSpecificationV1.

DialogueIntent is a typed model for dialogue content that preserves
source uncertainty about speaker identity.

StoryboardParser extracts structured fields from known WC-style prompt
markers using conservative deterministic parsing.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


class DialogueIntent(BaseModel):
    """Source-neutral dialogue content for one shot.

    Speaker identity is populated ONLY when deterministically proven
    by the normalization layer (not by this model). If speaker is
    unresolved or ambiguous, both speaker fields must be None.
    """

    text: str
    speaker_character_id: str | None = None
    speaker_name: str | None = None
    emotion: str = ""


class ShotSourceFacts(BaseModel, frozen=True):
    """Frozen transport DTO for normalized upstream pre-production facts.

    Built by import/normalization code. Consumed by ShotSpecBuilder.
    NOT persisted separately — canonical fields populated from it are
    persisted on ShotSpecificationV1 and related models.
    """

    source_project_id: str
    source_script_shot_number: int | None = None
    source_storyboard_asset_id: str | None = None
    action: str | None = None
    emotion: str | None = None
    dialogue_text: str | None = None
    characters: list[str] = Field(default_factory=list)
    duration_sec: float | None = None
    camera_angle: str | None = None
    camera_movement: str | None = None
    lighting: str | None = None
    storyboard_description: str = ""
    storyboard_image_path: str | None = None


# ---------------------------------------------------------------------------
# StoryboardParser — conservative deterministic extraction
# ---------------------------------------------------------------------------

# Known WC storyboard description markers (case-insensitive)
def _extract_marker(description: str, marker: str, stop_markers: list[str]) -> str | None:
    """Extract value after 'marker:' up to next known stop marker or end."""
    pattern = re.compile(rf"{marker}\s*:\s*", re.IGNORECASE)
    m = pattern.search(description)
    if not m:
        return None
    start = m.end()
    rest = description[start:]
    # Find earliest stop marker
    earliest = len(rest)
    for sm in stop_markers:
        sm_pat = re.compile(rf",\s*{sm}\s*:", re.IGNORECASE)
        sm_match = sm_pat.search(rest)
        if sm_match and sm_match.start() < earliest:
            earliest = sm_match.start()
    value = rest[:earliest].strip().rstrip(",").strip()
    return value if value else None
_MOVEMENT_MARKERS = re.compile(
    r"\b(slow\s+(?:push|dolly|pan|zoom|track|crane)|"
    r"fast\s+(?:push|dolly|pan|zoom|track)|"
    r"tracking\s+shot|dolly\s+(?:in|out|forward|back)|"
    r"crane\s+(?:up|down)|"
    r"pan\s+(?:left|right)|"
    r"zoom\s+(?:in|out)|"
    r"static|handheld|steadicam)\b",
    re.IGNORECASE,
)


class StoryboardParseResult(BaseModel):
    """Result of conservative storyboard description parsing."""

    camera_angle: str | None = None
    camera_movement: str | None = None
    lighting: str | None = None


def parse_storyboard_description(description: str) -> StoryboardParseResult:
    """Extract structured fields from a WC storyboard description.

    Uses deterministic regex for known markers. Returns None for each
    field where parsing fails or the marker is absent. Never invents
    values. Never modifies the original description.
    """
    if not description:
        return StoryboardParseResult()

    camera_angle: str | None = None
    camera_movement: str | None = None
    lighting: str | None = None

    # Camera angle
    camera_angle = _extract_marker(
        description, r"camera\s+angle",
        ["lighting", r"color\s+tone", "composition", r"character\s+action"],
    )

    # Camera movement — look within camera angle text or full description
    search_text = camera_angle or description
    mv = _MOVEMENT_MARKERS.search(search_text)
    if mv:
        camera_movement = mv.group(0).strip()

    # Lighting
    lighting = _extract_marker(
        description, "lighting",
        [r"color\s+tone", "composition", r"character\s+action"],
    )

    return StoryboardParseResult(
        camera_angle=camera_angle,
        camera_movement=camera_movement,
        lighting=lighting,
    )
