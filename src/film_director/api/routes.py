"""API routes for Local AI Film Director (M1 + M2)."""
from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, model_validator

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.models.canonical import CameraIntent, ShotSubject
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)
from film_director.services.enrichment_service import EnrichmentService
from film_director.services.import_service import ImportService


# ---------------------------------------------------------------------------
# Request DTOs for human editing
# ---------------------------------------------------------------------------


class BeatEditRequest(BaseModel):
    dramatic_action: str | None = None
    character_intention: str | None = None
    change: str | None = None
    characters: list[str] | None = None
    status: Literal["draft", "approved"] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if all(
            getattr(self, f) is None
            for f in ("dramatic_action", "character_intention", "change", "characters", "status")
        ):
            raise ValueError("At least one field must be provided")
        return self


class ShotEditRequest(BaseModel):
    dramatic_purpose: str | None = None
    subjects: list[ShotSubject] | None = None
    action: str | None = None
    environment: dict | None = None
    camera: CameraIntent | None = None
    lighting: dict | None = None
    audio_intent: dict | None = None
    duration_sec: float | None = None
    status: Literal["draft", "ready"] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if all(
            getattr(self, f) is None
            for f in (
                "dramatic_purpose", "subjects", "action", "environment",
                "camera", "lighting", "audio_intent", "duration_sec", "status",
            )
        ):
            raise ValueError("At least one field must be provided")
        return self


def create_router(
    adapter: WindComicAdapter,
    import_service: ImportService,
    project_repo: ProjectRepository,
    seq_repo: SequenceRepository,
    scene_repo: SceneRepository,
    char_repo: CharacterRepository,
    llm_provider,  # LLMProvider protocol
    # M2 services
    enrichment_service: EnrichmentService | None = None,
    beat_repo: BeatRepository | None = None,
    shot_repo: ShotRepository | None = None,
    plan_repo: GenerationPlanRepository | None = None,
) -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # Integration health checks
    # ------------------------------------------------------------------

    @router.get("/integrations/wind-comic/health")
    def wc_health() -> dict:
        h = adapter.health()
        return asdict(h)

    @router.get("/integrations/llm/health")
    def llm_health() -> dict:
        try:
            available = llm_provider.health()
        except Exception:
            available = False
        return {"available": available, "provider": "ollama"}

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    @router.post("/imports/wind-comic/{wc_project_id}")
    def import_project(wc_project_id: str) -> dict:
        result = import_service.import_project(wc_project_id)
        return asdict(result)

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    @router.get("/projects")
    def list_projects() -> list[dict]:
        projects = project_repo.list_projects()
        return [p.model_dump() for p in projects]

    @router.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return p.model_dump()

    @router.get("/projects/{project_id}/scenes")
    def get_scenes(project_id: str) -> list[dict]:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        sequences = seq_repo.get_sequences_by_project(project_id)
        scenes = []
        for seq in sequences:
            scenes.extend(scene_repo.get_scenes_by_sequence(seq.id))
        return [s.model_dump() for s in scenes]

    @router.get("/projects/{project_id}/characters")
    def get_characters(project_id: str) -> list[dict]:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        chars = char_repo.get_characters_by_project(project_id)
        return [c.model_dump() for c in chars]

    @router.get("/projects/{project_id}/storyboard")
    def get_storyboard(project_id: str) -> list[dict]:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        wc_project_id = p.wc_project_id
        shots = adapter.get_storyboard(wc_project_id)
        return [asdict(s) for s in shots]

    @router.get("/projects/{project_id}/changes")
    def get_changes(project_id: str) -> list[dict]:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        changes = import_service.check_for_changes(project_id)
        return [asdict(c) for c in changes]

    @router.post("/projects/{project_id}/apply-changes")
    def apply_changes(project_id: str) -> dict:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        changes = import_service.check_for_changes(project_id)
        if enrichment_service is not None:
            result = enrichment_service.apply_source_changes(project_id, changes)
            return {"applied": len(changes), **result}
        else:
            import_service.apply_detected_changes(project_id, changes)
            return {"applied": len(changes)}

    # ------------------------------------------------------------------
    # M2 Routes
    # ------------------------------------------------------------------

    @router.post("/projects/{project_id}/enrich")
    def enrich_project(project_id: str) -> dict:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        result = enrichment_service.enrich_project(project_id)
        return asdict(result)

    @router.get("/projects/{project_id}/beats")
    def get_project_beats(project_id: str) -> list[dict]:
        if beat_repo is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        sequences = seq_repo.get_sequences_by_project(project_id)
        all_beats = []
        for seq in sequences:
            scenes = scene_repo.get_scenes_by_sequence(seq.id)
            for scene in scenes:
                all_beats.extend(beat_repo.get_current_beats_by_scene(scene.id))
        return [b.model_dump() for b in all_beats]

    @router.get("/scenes/{scene_id}/beats")
    def get_scene_beats(scene_id: str) -> list[dict]:
        if beat_repo is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        beats = beat_repo.get_current_beats_by_scene(scene_id)
        return [b.model_dump() for b in beats]

    @router.put("/beats/{beat_id}")
    def edit_beat(beat_id: str, body: BeatEditRequest) -> dict:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        patch = body.model_dump(exclude_none=True)
        result = enrichment_service.edit_beat(beat_id, patch)
        if result is None:
            raise HTTPException(status_code=404, detail="Beat not found or outdated")
        return result.model_dump()

    @router.post("/scenes/{scene_id}/enrich-beats")
    def enrich_scene_beats(scene_id: str, force: bool = Query(False)) -> list[dict]:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        new_beats = enrichment_service.enrich_scene_beats(scene_id, force=force)
        return [b.model_dump() for b in new_beats]

    @router.get("/projects/{project_id}/shots")
    def get_project_shots(project_id: str) -> list[dict]:
        if shot_repo is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        shots = shot_repo.get_current_shots_by_project(project_id)
        return [s.model_dump() for s in shots]

    @router.get("/beats/{beat_id}/shots")
    def get_beat_shots(beat_id: str) -> list[dict]:
        if shot_repo is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        shots = shot_repo.get_current_shots_by_beat(beat_id)
        return [s.model_dump() for s in shots]

    @router.put("/shots/{shot_id}")
    def edit_shot(shot_id: str, body: ShotEditRequest) -> dict:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        patch = body.model_dump(exclude_none=True)
        result = enrichment_service.edit_shot(shot_id, patch)
        if result is None:
            raise HTTPException(status_code=404, detail="Shot not found or outdated")
        return result.model_dump()

    @router.post("/beats/{beat_id}/plan-coverage")
    def plan_beat_coverage(beat_id: str, force: bool = Query(False)) -> list[dict]:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        shots = enrichment_service.plan_beat_coverage(beat_id, force=force)
        return [s.model_dump() for s in shots]

    @router.get("/shots/{shot_id}/generation-plan")
    def get_generation_plan(shot_id: str) -> dict:
        if plan_repo is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        plan = plan_repo.get_current_plan_by_shot(shot_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Generation plan not found")
        return plan.model_dump()

    @router.post("/projects/{project_id}/assign-strategies")
    def assign_strategies(project_id: str) -> dict:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        count = enrichment_service.assign_strategies(project_id)
        return {"plans_created": count}

    return router
