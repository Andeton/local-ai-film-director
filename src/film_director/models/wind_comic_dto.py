"""Wind Comic raw data transfer objects."""
from dataclasses import dataclass


@dataclass(frozen=True)
class WCProject:
    id: str
    title: str
    status: str
    aspect: str
    style_id: str | None
    script_data: dict | None
    locked_characters: list[dict]


@dataclass(frozen=True)
class WCScene:
    asset_id: str
    project_id: str
    name: str
    data: dict
    media_urls: list[str]
    persistent_url: str | None
    version: int


@dataclass(frozen=True)
class WCCharacter:
    asset_id: str
    project_id: str
    name: str
    data: dict
    media_urls: list[str]
    persistent_url: str | None
    version: int


@dataclass(frozen=True)
class WCStoryboardShot:
    asset_id: str
    project_id: str
    shot_number: int
    data: dict
    media_urls: list[str]
    persistent_url: str | None
    version: int


@dataclass(frozen=True)
class WCHealth:
    available: bool
    db_path: str
    error: str | None = None
