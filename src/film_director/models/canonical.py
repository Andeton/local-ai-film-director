"""Canonical production specification models (M1 + M2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from film_director.models.provenance import Provenance


class ProductionProject(BaseModel):
    id: str
    wc_project_id: str
    title: str
    status: Literal["draft", "active", "outdated"] = "draft"
    aspect: str = "16:9"
    director_context: dict = Field(default_factory=dict)
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


# ---------------------------------------------------------------------------
# M2 models — model-agnostic production specification
# ---------------------------------------------------------------------------


class Beat(BaseModel):
    id: str
    scene_id: str
    dramatic_action: str
    character_intention: str
    change: str
    characters: list[str] = Field(default_factory=list)
    order_index: int
    status: Literal["draft", "approved", "outdated"] = "draft"
    source: Literal["llm", "human"] = "llm"
    version: int = 1
    created_at: str = ""
    updated_at: str = ""


class ShotSubject(BaseModel):
    character_id: str
    name: str
    ref_images: list[str] = Field(default_factory=list)  # ALL available refs, non-lossy


class CameraIntent(BaseModel):
    shot_size: Literal[
        "extreme_wide",
        "wide",
        "medium_wide",
        "medium",
        "medium_close",
        "close_up",
        "extreme_close",
    ]
    angle: str = ""
    movement: str = ""


class ShotSpecificationV1(BaseModel):
    id: str
    beat_id: str
    wc_storyboard_id: str | None = None
    wc_shot_number: int | None = None
    dramatic_purpose: str
    subjects: list[ShotSubject] = Field(default_factory=list)
    action: str
    environment: dict = Field(default_factory=dict)
    camera: CameraIntent
    lighting: dict = Field(default_factory=dict)
    audio_intent: dict = Field(default_factory=dict)
    duration_sec: float = 5.0
    continuity_inputs: dict = Field(default_factory=dict)
    storyboard_image_path: str | None = None
    order_index: int
    status: Literal["draft", "ready", "outdated"] = "draft"
    source: Literal["generated", "human"] = "generated"
    version: int = 1
    created_at: str = ""
    updated_at: str = ""


class ReferenceRequirements(BaseModel):
    character_refs: bool = False
    scene_ref: bool = False
    prev_frame: bool = False
    style_ref: bool = False


class GenerationPlan(BaseModel):
    id: str
    shot_id: str
    shot_version: int
    strategy: Literal[
        "TEXT_TO_VIDEO",
        "IMAGE_TO_VIDEO",
        "REFERENCE_TO_VIDEO",
        "FIRST_LAST_FRAME",
        "MULTI_PANEL",
    ]
    reference_requirements: ReferenceRequirements
    duration_sec: float
    resolution_intent: dict = Field(default_factory=dict)
    seed_policy: Literal["random", "fixed", "vary_per_take"] = "random"
    seed: int | None = None
    continuity_mode: Literal["none", "last_frame", "first_last"] = "none"
    selection_reason: str = ""
    status: Literal["draft", "ready", "outdated"] = "draft"
    version: int = 1
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class StrategySelectionContext:
    has_character_refs: bool
    has_recurring_cast: bool
    has_storyboard_image: bool
    has_prev_shot: bool
    shot_purpose: str
    subject_count: int
