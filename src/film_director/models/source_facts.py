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


# ---------------------------------------------------------------------------
# Source facts builder — WC bundle → normalized ShotSourceFacts
# ---------------------------------------------------------------------------

def build_shot_source_facts(
    wc_project_id: str,
    script_shots: list,
    storyboard_shots: list,
) -> dict[int, ShotSourceFacts]:
    """Build ShotSourceFacts from WC script shots + storyboard shots.

    Correlates by shotNumber (project-local). Returns a dict keyed by
    shotNumber. Deterministic: does NOT silently choose among duplicates.

    Args:
        wc_project_id: WC project ID for provenance
        script_shots: list of WCScriptShot (or duck-typed with matching attrs)
        storyboard_shots: list of WCStoryboardShot (or duck-typed)

    Returns:
        dict mapping shotNumber → ShotSourceFacts
    """
    # Index storyboard by shot_number (detect duplicates)
    sb_by_num: dict[int, Any] = {}
    sb_dupes: set[int] = set()
    for sb in storyboard_shots:
        num = sb.shot_number
        if num in sb_by_num:
            sb_dupes.add(num)
        else:
            sb_by_num[num] = sb

    result: dict[int, ShotSourceFacts] = {}
    seen_script_nums: set[int] = set()

    for ss in script_shots:
        num = ss.shot_number
        if num in seen_script_nums:
            continue  # skip duplicate script shotNumber
        seen_script_nums.add(num)

        # Correlate with storyboard
        sb = sb_by_num.get(num) if num not in sb_dupes else None

        # Parse storyboard description if available
        sb_desc = ""
        sb_asset_id = None
        sb_image_path = None
        duration = None
        parsed_camera = StoryboardParseResult()

        if sb is not None:
            sb_desc = sb.data.get("description", "")
            sb_asset_id = sb.asset_id
            sb_dur = sb.data.get("duration")
            if isinstance(sb_dur, (int, float)) and sb_dur > 0:
                duration = float(sb_dur)
            # Media path
            if sb.media_urls:
                sb_image_path = sb.media_urls[0] if sb.media_urls[0] else None
            elif sb.persistent_url:
                sb_image_path = sb.persistent_url
            # Parse structured fields
            parsed_camera = parse_storyboard_description(sb_desc)

        result[num] = ShotSourceFacts(
            source_project_id=wc_project_id,
            source_script_shot_number=num,
            source_storyboard_asset_id=sb_asset_id,
            action=ss.action if ss.action else None,
            emotion=ss.emotion if ss.emotion else None,
            dialogue_text=ss.dialogue if ss.dialogue else None,
            characters=list(ss.characters),
            duration_sec=duration,
            camera_angle=parsed_camera.camera_angle,
            camera_movement=parsed_camera.camera_movement,
            lighting=parsed_camera.lighting,
            storyboard_description=sb_desc,
            storyboard_image_path=sb_image_path,
        )

    # Storyboard shots with no matching script shot — include as source facts
    for num, sb in sb_by_num.items():
        if num not in result and num not in sb_dupes:
            sb_desc = sb.data.get("description", "")
            sb_dur = sb.data.get("duration")
            duration = float(sb_dur) if isinstance(sb_dur, (int, float)) and sb_dur > 0 else None
            sb_image_path = None
            if sb.media_urls:
                sb_image_path = sb.media_urls[0] if sb.media_urls[0] else None
            parsed_camera = parse_storyboard_description(sb_desc)

            result[num] = ShotSourceFacts(
                source_project_id=wc_project_id,
                source_script_shot_number=num,
                source_storyboard_asset_id=sb.asset_id,
                action=None,
                emotion=None,
                dialogue_text=None,
                characters=[],
                duration_sec=duration,
                camera_angle=parsed_camera.camera_angle,
                camera_movement=parsed_camera.camera_movement,
                lighting=parsed_camera.lighting,
                storyboard_description=sb_desc,
                storyboard_image_path=sb_image_path,
            )

    return result
