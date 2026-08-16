"""Provenance tracking for imported artifacts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from film_director.models.wind_comic_dto import (
        WCCharacter,
        WCDirectorPlan,
        WCProject,
        WCScene,
        WCStoryboardShot,
    )


@dataclass(frozen=True)
class Provenance:
    source_system: str
    source_project_id: str
    source_asset_id: str
    source_asset_version: int | None
    imported_at: str
    source_hash: str


def compute_source_hash(data: dict) -> str:
    """Compute a deterministic SHA-256 hash of a dict payload."""
    normalized = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_project_source_payload(
    wc: WCProject,
    director_plan: WCDirectorPlan | None = None,
    storyboard_shots: list[WCStoryboardShot] | None = None,
) -> dict:
    """Return the stable fields from a WCProject used for hashing.

    Excludes volatile WC fields: status, user_id.
    Includes script_data (M4.C), director_plan and storyboard data (M4.F)
    for comprehensive change detection.
    """
    payload: dict = {
        "id": wc.id,
        "title": wc.title,
        "aspect": wc.aspect,
        "style_id": wc.style_id,
        "script_data": wc.script_data,
    }
    if director_plan is not None:
        payload["director_plan"] = {
            "genre": director_plan.genre,
            "style": director_plan.style,
            "story_structure": director_plan.story_structure,
        }
    if storyboard_shots:
        payload["storyboard_data"] = [
            {
                "asset_id": sb.asset_id,
                "shot_number": sb.shot_number,
                "data": sb.data,
                "media_urls": sb.media_urls,
                "version": sb.version,
            }
            for sb in sorted(storyboard_shots, key=lambda s: s.shot_number)
        ]
    return payload


def build_scene_source_payload(wc: WCScene) -> dict:
    """Return the fields from a WCScene used for hashing."""
    return {
        "asset_id": wc.asset_id,
        "name": wc.name,
        "data": wc.data,
        "media_urls": wc.media_urls,
        "persistent_url": wc.persistent_url,
        "version": wc.version,
    }


def build_character_source_payload(wc: WCCharacter) -> dict:
    """Return the fields from a WCCharacter used for hashing."""
    return {
        "asset_id": wc.asset_id,
        "name": wc.name,
        "data": wc.data,
        "media_urls": wc.media_urls,
        "persistent_url": wc.persistent_url,
        "version": wc.version,
    }
