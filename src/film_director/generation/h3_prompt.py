"""H3PromptV1 Pydantic model + H3PromptBuilder — deterministic prompt assembly."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, field_validator

from film_director.errors import ReferenceResolutionError

if TYPE_CHECKING:
    from film_director.generation.h3_types import H3ReferenceBinding
    from film_director.models.canonical import GenerationPlan, ShotSpecificationV1


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


class H3PromptBuilder:
    """Deterministic H3 prompt assembly from authoritative binding list.

    Receives resolved bindings — the SAME list later used for upload/injection.
    Does NOT re-resolve characters, read the filesystem, or call any LLM.
    """

    def build(
        self,
        shot: ShotSpecificationV1,
        plan: GenerationPlan,
        bindings: list[H3ReferenceBinding],
    ) -> H3PromptV1:
        # --- version preconditions ---
        if plan.shot_id != shot.id:
            raise ReferenceResolutionError(
                f"Plan/shot ID mismatch: plan.shot_id={plan.shot_id!r} != shot.id={shot.id!r}",
            )
        if plan.shot_version != shot.version:
            raise ReferenceResolutionError(
                f"Plan/shot version mismatch: plan.shot_version={plan.shot_version} != shot.version={shot.version}",
            )

        # --- binding validation ---
        if not bindings:
            raise ReferenceResolutionError("Empty binding list — R2V requires at least one binding")

        seen_subj: set[int] = set()
        seen_pic: set[int] = set()
        for i, b in enumerate(bindings):
            expected_subj = i + 1
            if b.subject_index != expected_subj:
                raise ReferenceResolutionError(
                    f"Non-sequential subject_index: expected {expected_subj}, got {b.subject_index}",
                )
            if b.subject_index in seen_subj:
                raise ReferenceResolutionError(
                    f"Duplicate subject_index: {b.subject_index}",
                )
            if b.picture_index in seen_pic:
                raise ReferenceResolutionError(
                    f"Duplicate picture_index: {b.picture_index}",
                )
            seen_subj.add(b.subject_index)
            seen_pic.add(b.picture_index)

        # --- build sections ---
        subject_definitions = self._build_subject_definitions(bindings)
        summary = self._build_summary(shot, bindings)
        retention_analysis = self._build_retention_analysis(bindings)
        detailed_description = self._build_detailed_description(shot)
        overall_soundscape = shot.audio_intent.get("ambient", "") if shot.audio_intent else ""
        non_diegetic_music = shot.audio_intent.get("music", "") if shot.audio_intent else ""

        # --- assemble rendered prompt text ---
        rendered_prompt_text = self._render(
            subject_definitions,
            summary,
            retention_analysis,
            detailed_description,
            overall_soundscape,
            non_diegetic_music,
        )

        return H3PromptV1(
            id=f"h3p{uuid.uuid4().hex[:12]}",
            shot_id=shot.id,
            generation_plan_id=plan.id,
            source_shot_version=shot.version,
            source_generation_plan_version=plan.version,
            subject_definitions=subject_definitions,
            summary=summary,
            retention_analysis=retention_analysis,
            detailed_description=detailed_description,
            overall_soundscape=overall_soundscape,
            non_diegetic_music=non_diegetic_music,
            rendered_prompt_text=rendered_prompt_text,
        )

    # --- section builders ---

    @staticmethod
    def _build_subject_definitions(bindings: list[H3ReferenceBinding]) -> str:
        lines: list[str] = []
        for b in bindings:
            lines.append(
                f"<Subject {b.subject_index}> is {b.character_name} — {b.appearance} in <Picture {b.picture_index}>"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_summary(shot: ShotSpecificationV1, bindings: list[H3ReferenceBinding]) -> str:
        names = ", ".join(b.character_name for b in bindings)
        return f"{names}: {shot.action}. {shot.dramatic_purpose}."

    @staticmethod
    def _build_retention_analysis(bindings: list[H3ReferenceBinding]) -> str:
        lines: list[str] = []
        for b in bindings:
            lines.append(
                f"<Subject {b.subject_index}> fully_preserved — {b.appearance}"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_detailed_description(shot: ShotSpecificationV1) -> str:
        parts: list[str] = []
        cam = shot.camera
        parts.append(f"Camera: {cam.shot_size}")
        if cam.angle:
            parts.append(f"angle {cam.angle}")
        if cam.movement:
            parts.append(f"movement {cam.movement}")
        parts.append(f"Action: {shot.action}")
        if shot.lighting:
            lighting_str = ", ".join(f"{k}: {v}" for k, v in shot.lighting.items())
            parts.append(f"Lighting: {lighting_str}")
        parts.append(f"Duration: {shot.duration_sec}s")
        return ". ".join(parts) + "."

    @staticmethod
    def _render(
        subject_definitions: str,
        summary: str,
        retention_analysis: str,
        detailed_description: str,
        overall_soundscape: str,
        non_diegetic_music: str,
    ) -> str:
        sections: list[str] = [
            f"[Subject Definitions]\n{subject_definitions}",
            f"[Summary]\n{summary}",
            f"[Retention Analysis]\n{retention_analysis}",
            f"[Detailed Description]\n{detailed_description}",
        ]
        if overall_soundscape:
            sections.append(f"[Overall Soundscape]\n{overall_soundscape}")
        if non_diegetic_music:
            sections.append(f"[Non-Diegetic Music]\n{non_diegetic_music}")
        return "\n\n".join(sections)
