"""Provenance tracking for imported artifacts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from film_director.models.wind_comic_dto import WCCharacter, WCProject, WCScene


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


def build_project_source_payload(wc: WCProject) -> dict:
    """Return the stable fields from a WCProject used for hashing.

    Excludes volatile WC fields: status, user_id.
    Includes script_data for M4 change detection.
    """
    return {
        "id": wc.id,
        "title": wc.title,
        "aspect": wc.aspect,
        "style_id": wc.style_id,
        "script_data": wc.script_data,
    }


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
