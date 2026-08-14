"""Canonical production specification models (M1 subset)."""
from typing import Literal

from pydantic import BaseModel, Field

from film_director.models.provenance import Provenance


class ProductionProject(BaseModel):
    id: str
    wc_project_id: str
    title: str
    status: Literal["draft", "active", "outdated"] = "draft"
    aspect: str = "16:9"
    created_at: str = ""
    updated_at: str = ""
    provenance: Provenance  # REQUIRED — all M1 projects originate from WC


class Sequence(BaseModel):
    id: str
    project_id: str
    name: str
    order_index: int
    # No provenance — Sequence is a synthetic grouping entity


class Scene(BaseModel):
    id: str
    sequence_id: str
    wc_scene_id: str
    name: str
    location: str
    description: str
    order_index: int
    status: Literal["draft", "ready", "outdated"] = "draft"
    provenance: Provenance  # REQUIRED


class CharacterReference(BaseModel):
    id: str
    project_id: str
    wc_character_id: str
    name: str
    description: str
    appearance: str
    face_ref_path: str | None = None
    turnaround_paths: list[str] = Field(default_factory=list)
    visual_anchors: list[str] = Field(default_factory=list)
    status: Literal["active", "outdated"] = "active"
    provenance: Provenance  # REQUIRED
