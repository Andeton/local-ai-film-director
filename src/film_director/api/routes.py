"""API routes for Local AI Film Director (M1)."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.persistence.repositories import (
    CharacterRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
)
from film_director.services.import_service import ImportService


def create_router(
    adapter: WindComicAdapter,
    import_service: ImportService,
    project_repo: ProjectRepository,
    seq_repo: SequenceRepository,
    scene_repo: SceneRepository,
    char_repo: CharacterRepository,
    llm_provider,  # LLMProvider protocol
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
        import_service.apply_detected_changes(project_id, changes)
        return {"applied": len(changes)}

    return router
