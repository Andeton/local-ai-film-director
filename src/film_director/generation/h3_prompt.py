"""H3PromptV1 Pydantic model — immutable prompt artifact for H3 R2V generation."""
from typing import Literal

from pydantic import BaseModel, field_validator


class H3PromptV1(BaseModel):
    id: str
    shot_id: str
    generation_plan_id: str
    source_shot_version: int
    source_generation_plan_version: int
    subject_definitions: str
    summary: str
    retention_analysis: str
    detailed_description: str
    overall_soundscape: str = ""
    non_diegetic_music: str = ""
    rendered_prompt_text: str
    status: Literal["current", "stale"] = "current"
    version: int = 1
    created_at: str = ""

    @field_validator("source_shot_version", "source_generation_plan_version", "version")
    @classmethod
    def positive_version(cls, v):
        if v < 1:
            raise ValueError("must be >= 1")
        return v

    @field_validator("id", "shot_id", "generation_plan_id", "rendered_prompt_text", "summary", "detailed_description")
    @classmethod
    def non_empty(cls, v):
        if not v.strip():
            raise ValueError("must not be empty")
        return v
